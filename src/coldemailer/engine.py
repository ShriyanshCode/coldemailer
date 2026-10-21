"""The cycle: one pass of the worker over the simulated interval (sim_from, sim_to].

Order matters and is the core of "never loses track of a contact":
  1. recover   — finish sends a crashed run left half-done (ask the provider what happened)
  2. ingest    — apply replies / unsubscribes / complaints that have arrived by sim_to
  3. deactivate— contacts whose last wait expired
  4. send      — scan due contacts by index, gate them, then claim -> provider -> finalize
  5. autopause — mailboxes whose bounce/complaint rate crossed a threshold
  6. classify  — label replies the rules couldn't settle (never blocks sending)

Exactly-once per (contact, step): UNIQUE(contact_id, step) on sends, a claim lease on the
contact row re-checked inside the claim transaction, and an idempotency key per attempt that
the provider dedupes on.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import content, db, lifecycle, policies, provider_mock, timeutil
from .ai import classify, templates

REVIEW_REASONS = {
    "repeated_soft_bounce": "Repeated soft bounce",
    "policy_rejection": "Policy rejection",
    "content_blocked": "Content blocked",
    "classification": "Unsure classification",
}


@dataclass
class CycleStats:
    sent: int = 0
    deferred: dict = field(default_factory=dict)
    replies: int = 0
    ooo: int = 0
    unsubscribed: int = 0
    bounces: int = 0
    soft_bounces: int = 0
    deactivated: int = 0
    held: int = 0
    blocked: int = 0
    classified: int = 0
    duration_ms: int = 0

    def add(self, other: "CycleStats") -> None:
        for k, v in asdict(other).items():
            if k == "deferred":
                for r, n in v.items():
                    self.deferred[r] = self.deferred.get(r, 0) + n
            else:
                setattr(self, k, getattr(self, k) + v)

    @property
    def deferred_total(self) -> int:
        return sum(self.deferred.values())


# ---------------------------------------------------------------- small helpers

def _txn(conn):
    return nullcontext() if conn.in_transaction else db.tx(conn)


def event(conn, type_: str, at: str, contact_id=None, send_id=None, mailbox_id=None, **detail) -> int:
    cur = conn.execute(
        "INSERT INTO events(contact_id, send_id, mailbox_id, type, detail, occurred_at) VALUES (?,?,?,?,?,?)",
        (contact_id, send_id, mailbox_id, type_, json.dumps(detail) if detail else None, at))
    return cur.lastrowid


def suppress(conn, email: str, reason: str, at: str, source_event_id: int | None = None) -> None:
    conn.execute("INSERT OR IGNORE INTO suppression(email, reason, source_event_id, added_at) VALUES (?,?,?,?)",
                 (email, reason, source_event_id, at))


def is_suppressed(conn, email: str) -> bool:
    return conn.execute("SELECT 1 FROM suppression WHERE email=?", (email,)).fetchone() is not None


def stop(conn, contact_id: int, status: str, at: str) -> None:
    """Move a contact to a terminal status and close anything pending for it."""
    conn.execute(
        "UPDATE contacts SET status=?, next_action=NULL, next_action_at=NULL, hold_reason=NULL, ooo_until=NULL, "
        "claimed_by=NULL, claim_expires_at=NULL, updated_at=? WHERE id=?", (status, at, contact_id))
    conn.execute(
        "UPDATE review_items SET resolved_at=?, resolution=? WHERE contact_id=? AND resolved_at IS NULL "
        "AND kind != 'classification'", (at, f"auto: contact {status}", contact_id))


def open_review(conn, contact_id: int, kind: str, reason: str, at: str, send_id=None, reply_id=None,
                **detail) -> int:
    cur = conn.execute(
        "INSERT INTO review_items(contact_id, send_id, reply_id, kind, reason, detail, created_at) "
        "VALUES (?,?,?,?,?,?,?)", (contact_id, send_id, reply_id, kind, reason, json.dumps(detail), at))
    event(conn, "needs_review", at, contact_id, send_id, reason=REVIEW_REASONS.get(reason, reason), **detail)
    return cur.lastrowid


def _bump_daily(conn, mailbox_id: int, domain: str | None, at: str, col: str = "sent") -> None:
    day = timeutil.ist_day(at)
    conn.execute(f"INSERT INTO mailbox_daily(mailbox_id, day, {col}) VALUES (?,?,1) "
                 f"ON CONFLICT(mailbox_id, day) DO UPDATE SET {col}={col}+1", (mailbox_id, day))
    if domain:
        conn.execute("INSERT INTO domain_daily(domain, day, sent) VALUES (?,?,1) "
                     "ON CONFLICT(domain, day) DO UPDATE SET sent=sent+1", (domain, day))


def attempt_key(contact_id: int, step: int, campaign_id: int, attempt: int) -> str:
    return hashlib.sha256(f"{contact_id}:{step}:{campaign_id}:{attempt}".encode()).hexdigest()


def apply_classification(conn, reply_id: int, label: str, by: str, at: str) -> None:
    """Set a reply's label and apply what it implies. Only ever adds suppression or upgrades
    replied -> unsubscribed; never restarts a stopped sequence."""
    with _txn(conn):
        r = conn.execute("SELECT r.*, c.email, c.status FROM replies r JOIN contacts c ON c.id=r.contact_id "
                         "WHERE r.id=?", (reply_id,)).fetchone()
        conn.execute("UPDATE replies SET classification=?, classified_by=?, classified_at=? WHERE id=?",
                     (label, by, at, reply_id))
        event(conn, "classified", at, r["contact_id"], r["send_id"], label=label, by=by)
        if label == "unsubscribe":
            suppress(conn, r["email"], "unsubscribe_reply", at)
            if r["status"] == "replied":
                stop(conn, r["contact_id"], "unsubscribed", at)
                event(conn, "unsubscribed", at, r["contact_id"], r["send_id"], via="reply")
        elif label == "not_interested":
            suppress(conn, r["email"], "not_interested", at)


# ---------------------------------------------------------------- 1. recover

def _recover(conn, settings, provider, cap, sim_to: str, stats: CycleStats) -> None:
    rows = conn.execute(
        "SELECT c.*, s.id send_id, s.step send_step, s.mailbox_id send_mailbox_id, a.id attempt_id, "
        "a.idempotency_key, a.at attempt_at "
        "FROM sends s JOIN send_attempts a ON a.send_id=s.id AND a.result='pending' "
        "JOIN contacts c ON c.id=s.contact_id "
        "WHERE s.state='claimed' AND (c.claim_expires_at IS NULL OR c.claim_expires_at < ?)", (sim_to,)).fetchall()
    for r in rows:
        result = provider.lookup(r["idempotency_key"])
        if result:
            _finalize(conn, settings, cap, r, r["send_id"], r["attempt_id"], r["send_step"], r["send_mailbox_id"],
                      r["attempt_at"], result, stats)
            continue
        with db.tx(conn):  # the provider never saw it: undo the claim, the contact stays due
            conn.execute("DELETE FROM send_attempts WHERE id=?", (r["attempt_id"],))
            left = conn.execute("SELECT result FROM send_attempts WHERE send_id=? ORDER BY attempt DESC LIMIT 1",
                                (r["send_id"],)).fetchone()
            if left is None:
                conn.execute("DELETE FROM sends WHERE id=?", (r["send_id"],))
            else:
                conn.execute("UPDATE sends SET state='soft_bounced' WHERE id=?", (r["send_id"],))
            conn.execute("UPDATE contacts SET claimed_by=NULL, claim_expires_at=NULL WHERE id=?", (r["id"],))


# ---------------------------------------------------------------- 2. ingest

def _ingest(conn, settings, sim_to: str, stats: CycleStats) -> None:
    rows = conn.execute(
        "SELECT * FROM sim_events WHERE processed_at IS NULL AND deliver_at <= ? AND kind != 'accepted' "
        "ORDER BY deliver_at, id", (sim_to,)).fetchall()
    if not rows:
        return
    with db.tx(conn):
        for ev in rows:
            c = conn.execute("SELECT * FROM contacts WHERE id=?", (ev["contact_id"],)).fetchone()
            at = ev["deliver_at"]
            if c is not None:
                _apply_inbound(conn, settings, c, ev, at, stats)
            conn.execute("UPDATE sim_events SET processed_at=? WHERE id=?", (at, ev["id"]))


def _apply_inbound(conn, settings, c, ev, at: str, stats: CycleStats) -> None:
    kind, terminal = ev["kind"], c["status"] in lifecycle.TERMINAL
    mailbox_id = conn.execute("SELECT mailbox_id FROM sends WHERE id=?", (ev["send_id"],)).fetchone()
    mailbox_id = mailbox_id["mailbox_id"] if mailbox_id else None

    if kind in ("hard_bounce", "soft_bounce"):
        return  # bounces are applied synchronously when the provider answers (see _finalize)

    if kind == "reply":
        cur = conn.execute(
            "INSERT INTO replies(contact_id, send_id, sim_event_id, body, received_at) VALUES (?,?,?,?,?)",
            (c["id"], ev["send_id"], ev["id"], ev["body"], at))
        reply_id = cur.lastrowid
        label, confidence = classify.rules(ev["body"])
        if terminal:
            if label:
                apply_classification(conn, reply_id, label, "rule", at)
            return
        if label == "out_of_office":
            ret = classify.parse_return_date(ev["body"], at)
            push = lifecycle.ooo_push(at, ret, c["timezone"], f"{c['id']}:{reply_id}", settings.ooo_default_days,
                                      settings.ooo_cap_days, settings.window_start, settings.window_end)
            nxt = max(push, c["next_action_at"] or push)
            conn.execute("UPDATE contacts SET ooo_until=?, next_action_at=?, updated_at=? WHERE id=?",
                         (push, nxt, at, c["id"]))
            conn.execute("UPDATE replies SET classification='out_of_office', classified_by='rule', classified_at=? "
                         "WHERE id=?", (at, reply_id))
            event(conn, "ooo", at, c["id"], ev["send_id"], mailbox_id, return_date=ret, resumes_at=nxt)
            stats.ooo += 1
            return
        if label == "unsubscribe":
            stop(conn, c["id"], "unsubscribed", at)
            event(conn, "replied", at, c["id"], ev["send_id"], mailbox_id, label="unsubscribe")
            apply_classification(conn, reply_id, "unsubscribe", "rule", at)
            stats.unsubscribed += 1
            return
        stop(conn, c["id"], "replied", at)
        event(conn, "replied", at, c["id"], ev["send_id"], mailbox_id, label=label)
        if label and confidence >= 0.8:
            apply_classification(conn, reply_id, label, "rule", at)
        stats.replies += 1
        return

    if kind == "unsubscribe_click":
        suppress(conn, c["email"], "unsubscribe_link", at)
        if c["status"] not in ("unsubscribed", "bounced"):
            stop(conn, c["id"], "unsubscribed", at)
            event(conn, "unsubscribed", at, c["id"], ev["send_id"], mailbox_id, via="link")
            stats.unsubscribed += 1
        return

    if kind == "complaint":
        suppress(conn, c["email"], "complaint", at)
        event(conn, "complaint", at, c["id"], ev["send_id"], mailbox_id)
        if mailbox_id:
            _bump_daily(conn, mailbox_id, None, at, "complaints")
        if c["status"] not in ("unsubscribed", "bounced"):
            stop(conn, c["id"], "unsubscribed", at)
            stats.unsubscribed += 1


# ---------------------------------------------------------------- 3. deactivate

def _deactivate(conn, sim_to: str, stats: CycleStats) -> None:
    with db.tx(conn):
        rows = conn.execute(
            "SELECT id, next_action_at FROM contacts WHERE next_action='deactivate' AND hold_reason IS NULL "
            "AND next_action_at <= ?", (sim_to,)).fetchall()
        for r in rows:
            stop(conn, r["id"], "deactivated", r["next_action_at"])
            event(conn, "deactivated", r["next_action_at"], r["id"], after_step=4)
        stats.deactivated += len(rows)


# ---------------------------------------------------------------- 4. send

def _defer(pending: list, stats: CycleStats, c, reason: str, day: str, at: str) -> None:
    stats.deferred[reason] = stats.deferred.get(reason, 0) + 1
    if c["last_deferred_day"] != day:
        pending.append((c["id"], reason, day, at, lifecycle.action_step(c["next_action"])))


def _flush_deferred(conn, pending: list) -> None:
    if not pending:
        return
    with db.tx(conn):
        conn.executemany("UPDATE contacts SET last_deferred_day=? WHERE id=?", [(p[2], p[0]) for p in pending])
        conn.executemany(
            "INSERT INTO events(contact_id, type, detail, occurred_at) VALUES (?, 'deferred', ?, ?)",
            [(p[0], json.dumps({"reason": p[1], "step": p[4]}), p[3]) for p in pending])
    pending.clear()


def _send_due(conn, settings, provider, cap, sim_from: str, sim_to: str, stats: CycleStats, owner: str) -> set:
    touched: set[int] = set()
    exhausted: set[int] = set()
    day = timeutil.ist_day(sim_to)
    pending: list = []
    mailbox_addr = {m["id"]: m["address"] for m in cap.mailboxes.values()}

    def all_exhausted() -> bool:
        return all(m["paused"] or m["id"] in exhausted for m in cap.mailboxes.values())

    for action in reversed(lifecycle.SEND_ACTIONS):  # follow-ups before first emails
        step = lifecycle.action_step(action)
        last = ("", 0)
        while True:
            if all_exhausted():
                _bulk_defer(conn, cap, action, last, sim_to, day, stats)
                break
            rows = conn.execute(
                "SELECT * FROM contacts WHERE next_action=? AND hold_reason IS NULL AND next_action_at <= ? "
                "AND (next_action_at, id) > (?, ?) ORDER BY next_action_at, id LIMIT ?",
                (action, sim_to, last[0], last[1], settings.batch_size)).fetchall()
            if not rows:
                break
            for c in rows:
                earliest = max(c["next_action_at"], sim_from)
                mb_id, reason = cap.pick_mailbox(c, timeutil.ist_day(earliest))
                if mb_id is None:
                    _defer(pending, stats, c, reason, day, sim_to)
                    continue
                slot, reason = cap.next_slot(mb_id, earliest)
                if slot is None:
                    exhausted.add(mb_id)
                    _defer(pending, stats, c, reason, day, sim_to)
                    continue
                slot, reason = policies.gate(c, slot, cap, settings)
                if slot is None:
                    _defer(pending, stats, c, reason, day, sim_to)
                    continue
                if _send_one(conn, settings, provider, cap, c, step, mb_id, mailbox_addr[mb_id], slot,
                             sim_to, stats, owner):
                    touched.add(mb_id)
            last = (rows[-1]["next_action_at"], rows[-1]["id"])
            _flush_deferred(conn, pending)
        _flush_deferred(conn, pending)
    return touched


def _bulk_defer(conn, cap, action: str, last: tuple, sim_to: str, day: str, stats: CycleStats) -> None:
    """Every mailbox is out of room: defer the rest of this action's due contacts in one statement."""
    reason = "mailbox_paused" if all(m["paused"] for m in cap.mailboxes.values()) else "mailbox_capacity"
    where = ("next_action=? AND hold_reason IS NULL AND next_action_at <= ? AND (next_action_at, id) > (?, ?)")
    args = (action, sim_to, last[0], last[1])
    n = conn.execute(f"SELECT COUNT(*) n FROM contacts WHERE {where}", args).fetchone()["n"]
    if not n:
        return
    stats.deferred[reason] = stats.deferred.get(reason, 0) + n
    with db.tx(conn):
        ids = conn.execute(
            f"UPDATE contacts SET last_deferred_day=? WHERE {where} AND "
            f"(last_deferred_day IS NULL OR last_deferred_day != ?) RETURNING id", (day, *args, day)).fetchall()
        detail = json.dumps({"reason": reason, "step": lifecycle.action_step(action)})
        conn.executemany("INSERT INTO events(contact_id, type, detail, occurred_at) VALUES (?, 'deferred', ?, ?)",
                         [(r["id"], detail, sim_to) for r in ids])


def _send_one(conn, settings, provider, cap, c, step: int, mb_id: int, mb_addr: str, slot: str,
              sim_to: str, stats: CycleStats, owner: str) -> bool:
    lease = timeutil.add(sim_to, seconds=settings.claim_lease_s)
    with db.tx(conn):
        row = conn.execute(
            "UPDATE contacts SET claimed_by=?, claim_expires_at=? WHERE id=? AND step=? AND next_action=? "
            "AND hold_reason IS NULL AND status IN ('new','first_sent','follow_up_1_sent','follow_up_2_sent',"
            "'follow_up_3_sent') AND (claim_expires_at IS NULL OR claim_expires_at < ?) RETURNING *",
            (owner, lease, c["id"], step - 1, f"send_{step}", sim_to)).fetchone()
        if row is None:
            return False  # someone else changed it since the scan (reply, hold, another run)
        if is_suppressed(conn, row["email"]):
            reason = conn.execute("SELECT reason FROM suppression WHERE email=?", (row["email"],)).fetchone()[0]
            stop(conn, row["id"], "bounced" if reason == "hard_bounce" else "unsubscribed", slot)
            event(conn, "deferred", slot, row["id"], reason="suppressed", step=step)
            return False
        existing = conn.execute("SELECT * FROM sends WHERE contact_id=? AND step=?", (row["id"], step)).fetchone()
        if existing is not None and existing["state"] == "sent":
            conn.execute("UPDATE contacts SET claimed_by=NULL, claim_expires_at=NULL WHERE id=?", (row["id"],))
            return False
        tpl = templates.pick(conn, row["segment"], step, row["variant"])
        subject, body, headers = templates.render(tpl, row, mb_addr, mb_id, settings.campaign_id)
        problems = content.check(subject, body)
        if existing is None:
            send_id = conn.execute(
                "INSERT INTO sends(contact_id, step, mailbox_id, template_id, subject, body, headers, state, claimed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (row["id"], step, mb_id, tpl["id"], subject, body, json.dumps(headers),
                 "blocked" if problems else "claimed", slot)).lastrowid
        else:
            send_id = existing["id"]
            conn.execute("UPDATE sends SET mailbox_id=?, template_id=?, subject=?, body=?, headers=?, state=?, "
                         "claimed_at=? WHERE id=?", (mb_id, tpl["id"], subject, body, json.dumps(headers),
                                                     "blocked" if problems else "claimed", slot, send_id))
        if problems:
            conn.execute("UPDATE contacts SET hold_reason='content_blocked', claimed_by=NULL, claim_expires_at=NULL "
                         "WHERE id=?", (row["id"],))
            event(conn, "content_blocked", slot, row["id"], send_id, mb_id, reasons=problems)
            open_review(conn, row["id"], "content", "content_blocked", slot, send_id, reasons=problems)
            stats.blocked += 1
            stats.held += 1
            return False
        attempt = conn.execute("SELECT COALESCE(MAX(attempt), 0) + 1 FROM send_attempts WHERE send_id=?",
                               (send_id,)).fetchone()[0]
        key = attempt_key(row["id"], step, settings.campaign_id, attempt)
        attempt_id = conn.execute(
            "INSERT INTO send_attempts(send_id, attempt, idempotency_key, result, at) VALUES (?,?,?,'pending',?)",
            (send_id, attempt, key, slot)).lastrowid
        conn.execute("UPDATE sends SET idempotency_key=? WHERE id=?", (key, send_id))

    with db.tx(conn):  # the "network call" — the provider records what it did in sim_events
        result = provider.send(key, send_id, row["id"], step, slot, row["domain"], row["first_name"],
                               row["timezone"])
    _finalize(conn, settings, cap, row, send_id, attempt_id, step, mb_id, slot, result, stats)
    return True


def _finalize(conn, settings, cap, c, send_id: int, attempt_id: int, step: int, mb_id: int, slot: str,
              result: provider_mock.SendResult, stats: CycleStats) -> None:
    """Record the provider's answer. Module-level so tests can make it fail to simulate a crash."""
    mb_addr = conn.execute("SELECT address FROM mailboxes WHERE id=?", (mb_id,)).fetchone()["address"]
    with db.tx(conn):
        conn.execute("UPDATE send_attempts SET result=?, smtp_code=? WHERE id=?",
                     (result.kind, result.smtp_code, attempt_id))
        conn.execute("UPDATE mailboxes SET last_sent_at=MAX(COALESCE(last_sent_at, ''), ?) WHERE id=?", (slot, mb_id))
        _bump_daily(conn, mb_id, c["domain"], slot)
        if cap is not None:
            cap.commit(mb_id, c["domain"], slot)
        key = conn.execute("SELECT idempotency_key FROM send_attempts WHERE id=?", (attempt_id,)).fetchone()[0]

        if result.kind == "accepted":
            nxt = lifecycle.schedule_next(slot, step, c["timezone"], settings.waits_days, f"{c['id']}:{step}",
                                          settings.window_start, settings.window_end)
            conn.execute("UPDATE sends SET state='sent', sent_at=?, mailbox_id=?, provider_message_id=? WHERE id=?",
                         (slot, mb_id, key[:24], send_id))
            conn.execute(
                "UPDATE contacts SET step=?, status=?, next_action=?, next_action_at=?, mailbox_id=?, "
                "claimed_by=NULL, claim_expires_at=NULL, ooo_until=NULL, updated_at=? WHERE id=?",
                (step, lifecycle.STATUS_BY_STEP[step], lifecycle.next_action_after(step), nxt, mb_id, slot, c["id"]))
            event(conn, "sent", slot, c["id"], send_id, mb_id, step=step, mailbox=mb_addr)
            stats.sent += 1
            return

        conn.execute("UPDATE sim_events SET processed_at=? WHERE idempotency_key=? AND kind=? "
                     "AND processed_at IS NULL", (slot, key, result.kind))
        conn.execute("UPDATE contacts SET mailbox_id=?, claimed_by=NULL, claim_expires_at=NULL WHERE id=?",
                     (mb_id, c["id"]))
        if result.kind == "hard_bounce":
            conn.execute("UPDATE sends SET state='bounced', mailbox_id=? WHERE id=?", (mb_id, send_id))
            _bump_daily(conn, mb_id, None, slot, "bounced")
            ev = event(conn, "hard_bounced", slot, c["id"], send_id, mb_id, step=step,
                       smtp_code=result.smtp_code, message=result.message)
            suppress(conn, c["email"], "hard_bounce", slot, ev)
            stop(conn, c["id"], "bounced", slot)
            stats.bounces += 1
            return

        # soft bounce: retry with backoff, or hand to a human
        conn.execute("UPDATE sends SET state='soft_bounced', mailbox_id=? WHERE id=?", (mb_id, send_id))
        n = conn.execute("SELECT COUNT(*) FROM send_attempts WHERE send_id=? AND result='soft_bounce'",
                         (send_id,)).fetchone()[0]
        policy = provider_mock.is_policy_code(result.smtp_code)
        event(conn, "soft_bounced", slot, c["id"], send_id, mb_id, step=step, attempt=n,
              smtp_code=result.smtp_code, message=result.message)
        stats.soft_bounces += 1
        if policy or n >= settings.max_soft_attempts:
            reason = "policy_rejection" if policy else "repeated_soft_bounce"
            conn.execute("UPDATE contacts SET hold_reason=? WHERE id=?", (reason, c["id"]))
            conn.execute("UPDATE sends SET state='held' WHERE id=?", (send_id,))
            attempts = [dict(a) for a in conn.execute(
                "SELECT attempt, smtp_code, at FROM send_attempts WHERE send_id=? ORDER BY attempt", (send_id,))]
            open_review(conn, c["id"], "policy" if policy else "soft_bounce", reason, slot, send_id,
                        smtp_code=result.smtp_code, message=result.message, attempts=attempts)
            stats.held += 1
        else:
            hours = settings.soft_retry_hours[min(n, len(settings.soft_retry_hours)) - 1]
            retry = timeutil.next_window_open(timeutil.add(slot, hours=hours), c["timezone"],
                                              settings.window_start, settings.window_end)
            conn.execute("UPDATE contacts SET next_action_at=? WHERE id=?", (retry, c["id"]))


# ---------------------------------------------------------------- 5. auto-pause

def _autopause(conn, settings, mailbox_ids, at: str) -> None:
    for mb_id in sorted(mailbox_ids):
        mb = conn.execute("SELECT paused, address FROM mailboxes WHERE id=?", (mb_id,)).fetchone()
        if mb["paused"]:
            continue
        reason = policies.evaluate_autopause(conn, mb_id, settings)
        if reason:
            with db.tx(conn):
                conn.execute("UPDATE mailboxes SET paused=1, paused_reason=?, paused_at=? WHERE id=?",
                             (reason, at, mb_id))
                event(conn, "mailbox_paused", at, mailbox_id=mb_id, mailbox=mb["address"], reason=reason)


# ---------------------------------------------------------------- the cycle

def run_cycle(conn, settings, sim_from: str, sim_to: str, llm=None, owner: str = "cycle") -> CycleStats:
    db.require_initialised(conn)
    t0 = time.perf_counter()
    stats = CycleStats()
    provider = provider_mock.MockProvider(conn, settings)
    cap = policies.Capacity.load(conn, settings, sim_from, sim_to)

    _recover(conn, settings, provider, cap, sim_to, stats)
    _ingest(conn, settings, sim_to, stats)
    _deactivate(conn, sim_to, stats)
    cap = policies.Capacity.load(conn, settings, sim_from, sim_to)  # recovery may have used capacity
    touched = _send_due(conn, settings, provider, cap, sim_from, sim_to, stats, owner)
    _autopause(conn, settings, touched, sim_to)
    stats.classified = classify.backlog(conn, settings, llm, now=sim_to)

    stats.duration_ms = int((time.perf_counter() - t0) * 1000)
    conn.execute(
        "INSERT INTO cycles(started_at_wall, sim_from, sim_to, sent, deferred_json, replies, bounces, deactivated, "
        "held, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), sim_from, sim_to, stats.sent, json.dumps(stats.deferred),
         stats.replies + stats.ooo + stats.unsubscribed, stats.bounces, stats.deactivated, stats.held,
         stats.duration_ms))
    return stats


def last_cycle_end(conn) -> str | None:
    row = conn.execute("SELECT sim_to FROM cycles ORDER BY id DESC LIMIT 1").fetchone()
    return row["sim_to"] if row else None
