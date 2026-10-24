"""Invariant checks over the whole database. `python -m coldemailer verify` and POST /api/verify.

Each check returns up to 5 offending ids so a failure is easy to chase.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from . import clock, db, lifecycle, policies, timeutil

TERMINAL_EVENTS = {"hard_bounced", "replied", "unsubscribed", "complaint", "deactivated"}


def _derived_status(evts: list[tuple[str, dict]]) -> str:
    sent, status = 0, None
    for type_, d in evts:
        if type_ == "sent" and status is None:
            sent += 1
        elif status is None and type_ in TERMINAL_EVENTS:
            status = {"hard_bounced": "bounced", "unsubscribed": "unsubscribed", "complaint": "unsubscribed",
                      "deactivated": "deactivated"}.get(type_)
            if type_ == "replied":
                status = "unsubscribed" if d.get("label") == "unsubscribe" else "replied"
        elif status in ("replied", "deactivated") and type_ in ("unsubscribed", "complaint"):
            status = "unsubscribed"
    return status or lifecycle.STATUS_BY_STEP[min(sent, 4)]


def _check(name: str, bad: list, detail: str) -> dict:
    return {"name": name, "ok": not bad, "detail": "ok" if not bad else f"{len(bad)} failing: {detail} "
            f"(e.g. {', '.join(str(b) for b in bad[:5])})"}


def run(conn, settings) -> dict:
    db.require_initialised(conn)
    now = clock.now(conn)
    contacts = {r["id"]: r for r in conn.execute("SELECT * FROM contacts")}
    checks = []

    evts = defaultdict(list)
    for e in conn.execute("SELECT contact_id, type, detail FROM events WHERE contact_id IS NOT NULL AND type IN "
                          "('sent','hard_bounced','replied','unsubscribed','complaint','deactivated') "
                          "ORDER BY contact_id, occurred_at, id"):
        evts[e["contact_id"]].append((e["type"], json.loads(e["detail"]) if e["detail"] else {}))
    bad = [cid for cid, c in contacts.items() if _derived_status(evts.get(cid, [])) != c["status"]]
    checks.append(_check("Status matches the event history", bad, "contact status differs from its events"))

    sent_counts = dict(conn.execute("SELECT contact_id, COUNT(*) FROM sends WHERE state='sent' GROUP BY 1").fetchall())
    expected = {s: i for i, s in enumerate(lifecycle.STATUS_BY_STEP)} | {"deactivated": 4}
    bad = [cid for cid, c in contacts.items() if c["step"] != sent_counts.get(cid, 0)
           or (c["status"] in expected and c["step"] != expected[c["status"]])]
    checks.append(_check("Step equals emails actually sent, and fits the status", bad,
                         "contacts.step != sent sends, or a status like follow_up_1_sent with the wrong step"))

    bad = [r[0] for r in conn.execute(
        "SELECT send_id FROM send_attempts WHERE result='accepted' GROUP BY send_id HAVING COUNT(*) > 1")]
    bad += [f"{r[0]}:{r[1]}" for r in conn.execute(
        "SELECT contact_id, step FROM sends GROUP BY 1, 2 HAVING COUNT(*) > 1")]
    checks.append(_check("No step sent twice", bad, "duplicate step or >1 accepted attempt"))

    bad = [r[0] for r in conn.execute(
        "SELECT s.id FROM sends s JOIN contacts c ON c.id=s.contact_id JOIN suppression x ON x.email=c.email "
        "WHERE s.state='sent' AND s.sent_at > x.added_at")]
    checks.append(_check("Nothing sent after suppression", bad, "send after the email was suppressed"))

    bad = [cid for cid, c in contacts.items() if c["status"] in lifecycle.TERMINAL and c["next_action"]]
    checks.append(_check("Stopped contacts have no next action", bad, "terminal contact still scheduled"))

    waits, bad, prev = list(settings.waits_days), [], {}
    for s in conn.execute("SELECT s.contact_id, s.step, s.sent_at, c.timezone FROM sends s "
                          "JOIN contacts c ON c.id=s.contact_id WHERE s.state='sent' ORDER BY s.contact_id, s.step"):
        p = prev.get(s["contact_id"])
        if p and s["step"] == p[0] + 1:
            gap = (timeutil.local(s["sent_at"], s["timezone"]).date() - timeutil.local(p[1], s["timezone"]).date()).days
            if gap < waits[p[0] - 1]:
                bad.append(s["contact_id"])
        prev[s["contact_id"]] = (s["step"], s["sent_at"])
    checks.append(_check("Follow-ups respect the waits", bad, "follow-up sent before its wait (local days)"))

    bad = [r["id"] for r in conn.execute(
        "SELECT s.id, s.sent_at, c.timezone FROM sends s JOIN contacts c ON c.id=s.contact_id "
        "WHERE s.source='engine' AND s.state='sent'")
           if not timeutil.in_window(r["sent_at"], r["timezone"], settings.window_start, settings.window_end)]
    checks.append(_check("Sends inside the prospect's window", bad, "engine send outside 09:00-18:00 local"))

    mbs = {m["id"]: m for m in conn.execute("SELECT * FROM mailboxes")}
    bad = [f"{r['mailbox_id']}@{r['day']}" for r in conn.execute("SELECT * FROM mailbox_daily")
           if r["sent"] > policies.warmup_limit(mbs[r["mailbox_id"]], r["day"])]
    checks.append(_check("Mailboxes within warm-up limits", bad, "daily sends above the warm-up limit"))

    bad = [f"{r['domain']}@{r['day']}" for r in conn.execute(
        "SELECT * FROM domain_daily WHERE sent > ?", (settings.domain_daily_cap,))]
    checks.append(_check("Recipient domains within daily cap", bad, "domain over its daily cap"))

    bad = [r["id"] for r in conn.execute(
        "SELECT c.id FROM contacts c WHERE c.hold_reason IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM send_attempts a JOIN sends s ON s.id=a.send_id WHERE s.contact_id=c.id AND a.at > ("
        "SELECT MAX(occurred_at) FROM events e WHERE e.contact_id=c.id AND e.type IN ('needs_review','content_blocked')))")]
    checks.append(_check("Nothing sent while on hold", bad, "attempt after the contact was put on hold"))

    bad = [r[0] for r in conn.execute(
        "SELECT id FROM sim_events WHERE processed_at IS NULL AND kind != 'accepted' AND deliver_at <= ?", (now,))]
    checks.append(_check("Every arrived reply/bounce was processed", bad, "sim event delivered but not ingested"))

    bad = [cid for cid, c in contacts.items()
           if c["status"] not in lifecycle.TERMINAL and not c["hold_reason"] and not c["next_action"]]
    bad += [r[0] for r in conn.execute(
        "SELECT s.id FROM sends s JOIN contacts c ON c.id=s.contact_id WHERE s.state='claimed' "
        "AND (c.claim_expires_at IS NULL OR c.claim_expires_at < ?)", (now,))]
    checks.append(_check("No contact lost or stuck", bad, "active contact with no next action, or stale claim"))

    ok = all(c["ok"] for c in checks)
    conn.execute("INSERT INTO verify_runs(sim_at, ok, checks_json) VALUES (?,?,?)", (now, int(ok), json.dumps(checks)))
    return {"ok": ok, "at": now, "checks": checks}
