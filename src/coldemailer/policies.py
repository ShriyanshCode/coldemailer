"""Sending policies: warm-up limits, pacing, per-domain caps, prospect windows, auto-pause.

`Capacity` is loaded once per cycle and tracks usage in memory while the cycle assigns send
slots; the engine writes the durable counters (mailbox_daily, domain_daily) when a send is final.
"""
from __future__ import annotations

from datetime import date

from . import provider_mock, timeutil

DEFER_REASONS = ("outside_window", "domain_cap", "mailbox_capacity", "pacing", "mailbox_paused")


def warmup_limit(mailbox, ist_day: str) -> int:
    days = (date.fromisoformat(ist_day) - date.fromisoformat(mailbox["warmup_started_on"])).days
    if days < 0:
        return 0
    return min(mailbox["max_limit"], mailbox["start_limit"] + mailbox["daily_increment"] * days)


def pacing_gap_s(limit: int, window_minutes: int = 540) -> int:
    return window_minutes * 60 // max(limit, 1)


class Capacity:
    """Per-cycle view of mailbox and domain capacity over the simulated interval (sim_from, sim_to]."""

    def __init__(self, settings, mailboxes: dict, mb_used: dict, dom_used: dict, sim_from: str, sim_to: str):
        self.s = settings
        self.mailboxes = mailboxes      # id -> dict(row)
        self.mb_used = mb_used          # (id, day) -> sent
        self.dom_used = dom_used        # (domain, day) -> sent
        self.sim_from, self.sim_to = sim_from, sim_to
        self.window = timeutil.window_minutes(settings.window_start, settings.window_end)

    @classmethod
    def load(cls, conn, settings, sim_from: str, sim_to: str) -> "Capacity":
        days = {timeutil.ist_day(sim_from), timeutil.ist_day(sim_to)}
        mbs = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM mailboxes")}
        ph = ",".join("?" * len(days))
        mb_used = {(r["mailbox_id"], r["day"]): r["sent"] for r in conn.execute(
            f"SELECT mailbox_id, day, sent FROM mailbox_daily WHERE day IN ({ph})", tuple(days))}
        dom_used = {(r["domain"], r["day"]): r["sent"] for r in conn.execute(
            f"SELECT domain, day, sent FROM domain_daily WHERE day IN ({ph})", tuple(days))}
        return cls(settings, mbs, mb_used, dom_used, sim_from, sim_to)

    def limit(self, mailbox_id: int, day: str) -> int:
        mb = self.mailboxes[mailbox_id]
        return 0 if mb["paused"] else warmup_limit(mb, day)

    def remaining(self, mailbox_id: int, day: str) -> int:
        return self.limit(mailbox_id, day) - self.mb_used.get((mailbox_id, day), 0)

    def domain_remaining(self, domain: str, day: str) -> int:
        return self.s.domain_daily_cap - self.dom_used.get((domain, day), 0)

    def pick_mailbox(self, contact, earliest: str, skip=()) -> tuple[int | None, str | None, str | None]:
        """(mailbox, slot, reason). Follow-ups keep their sticky mailbox. First sends (or a follow-up
        whose mailbox is paused) go to whichever active mailbox can send soonest in this cycle, most
        room left breaking ties, so every mailbox's paced slots get used instead of the roomiest
        one's queue. On failure the mailbox is returned only for a sticky follow-up."""
        sticky = contact["mailbox_id"]
        if sticky and not self.mailboxes[sticky]["paused"]:
            slot, reason = self.next_slot(sticky, earliest)
            return sticky, slot, reason
        active = [m["id"] for m in self.mailboxes.values() if not m["paused"]]
        if not active:
            return None, None, "mailbox_paused"
        day = timeutil.ist_day(max(earliest, self.sim_from))
        best, reasons = None, set()
        for mb_id in active:
            if mb_id in skip:
                continue
            slot, reason = self.next_slot(mb_id, earliest)
            if slot is None:
                reasons.add(reason)
            elif best is None or (slot, -self.remaining(mb_id, day), mb_id) < best:
                best = (slot, -self.remaining(mb_id, day), mb_id)
        if best is None:
            return None, None, "pacing" if "pacing" in reasons else "mailbox_capacity"
        return best[2], best[0], None

    def next_slot(self, mailbox_id: int, earliest: str) -> tuple[str | None, str | None]:
        """Earliest paced send time for this mailbox inside the cycle interval, or (None, reason)."""
        mb = self.mailboxes[mailbox_id]
        if mb["paused"]:
            return None, "mailbox_paused"
        base = max(earliest, self.sim_from)
        day = timeutil.ist_day(base)
        if self.remaining(mailbox_id, day) <= 0:
            return None, "mailbox_capacity"
        if mb["last_sent_at"]:
            gap = pacing_gap_s(self.limit(mailbox_id, day), self.window)
            base = max(base, timeutil.add(mb["last_sent_at"], seconds=gap))
        if base > self.sim_to:
            return None, "pacing"
        if timeutil.ist_day(base) != day and self.remaining(mailbox_id, timeutil.ist_day(base)) <= 0:
            return None, "mailbox_capacity"
        return base, None

    def commit(self, mailbox_id: int, domain: str, at: str) -> None:
        day = timeutil.ist_day(at)
        self.mb_used[(mailbox_id, day)] = self.mb_used.get((mailbox_id, day), 0) + 1
        self.dom_used[(domain, day)] = self.dom_used.get((domain, day), 0) + 1
        mb = self.mailboxes[mailbox_id]
        mb["last_sent_at"] = max(mb["last_sent_at"] or at, at)


def gate(contact, slot: str, cap: Capacity, settings) -> tuple[str | None, str | None]:
    """Prospect-side checks for a proposed slot. Returns (slot, None) — possibly moved to the next
    window opening inside the interval — or (None, reason)."""
    tz = contact["timezone"]
    if not timeutil.in_window(slot, tz, settings.window_start, settings.window_end):
        opened = timeutil.next_window_open(slot, tz, settings.window_start, settings.window_end)
        if opened > cap.sim_to:
            return None, "outside_window"
        slot = opened
    if cap.domain_remaining(contact["domain"], timeutil.ist_day(slot)) <= 0:
        return None, "domain_cap"
    return slot, None


def evaluate_autopause(conn, mailbox_id: int, settings) -> str | None:
    """Reason to pause this mailbox, or None. Bounces = hard bounces + policy rejections.
    Only attempts after the last manual resume count, so a resumed mailbox gets a fresh sample."""
    since = conn.execute("SELECT COALESCE(resumed_at, '') FROM mailboxes WHERE id=?", (mailbox_id,)).fetchone()[0]
    rows = conn.execute(
        "SELECT a.result, a.smtp_code FROM send_attempts a JOIN sends s ON s.id=a.send_id "
        "WHERE s.mailbox_id=? AND a.at > ? AND a.result IN ('accepted','hard_bounce','soft_bounce') "
        "ORDER BY a.at DESC, a.id DESC LIMIT ?", (mailbox_id, since, settings.autopause_window)).fetchall()
    if len(rows) >= settings.autopause_min_sample:
        bad = sum(1 for r in rows if r["result"] == "hard_bounce"
                  or (r["result"] == "soft_bounce" and provider_mock.is_policy_code(r["smtp_code"])))
        rate = bad / len(rows)
        if rate > settings.autopause_bounce:
            return f"bounce rate {rate:.1%} over {settings.autopause_bounce:.0%} threshold"
    sent = conn.execute(
        "SELECT COUNT(*) n FROM (SELECT a.id FROM send_attempts a JOIN sends s ON s.id=a.send_id "
        "WHERE s.mailbox_id=? AND a.at > ? AND a.result='accepted' ORDER BY a.at DESC LIMIT ?)",
        (mailbox_id, since, settings.autopause_complaint_window)).fetchone()["n"]
    if sent >= settings.autopause_complaint_min_sample:
        complaints = conn.execute(
            "SELECT COUNT(*) n FROM events WHERE mailbox_id=? AND type='complaint' AND occurred_at > ?",
            (mailbox_id, since)).fetchone()["n"]
        rate = complaints / sent
        if rate > settings.autopause_complaint:
            return f"complaint rate {rate:.2%} over {settings.autopause_complaint:.1%} threshold"
    return None
