"""Mock email provider. Never sends anything; it writes what the outside world would do into sim_events.

Every outcome is drawn from a RNG keyed on the attempt's idempotency key, so results don't
depend on run order, and calling send() twice with the same key changes nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from . import lifecycle, timeutil
from .sim_corpus import reply_body

TRANSIENT = [("421 4.4.2", "Connection timed out, try again later"),
             ("450 4.2.1", "Greylisted, please retry"),
             ("452 4.2.2", "Mailbox full")]
POLICY = [("550 5.7.1", "Message rejected due to content policy"),
          ("554 5.7.0", "Message blocked as suspected spam")]


@dataclass
class SendResult:
    kind: Literal["accepted", "hard_bounce", "soft_bounce"]
    smtp_code: str | None = None
    message: str | None = None


def is_policy_code(code: str | None) -> bool:
    return bool(code) and code.split(" ")[1].startswith("5.7")


class MockProvider:
    def __init__(self, conn, settings):
        self.conn = conn
        self.s = settings

    def was_accepted(self, idempotency_key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sim_events WHERE idempotency_key=? AND kind='accepted'", (idempotency_key,)
        ).fetchone() is not None

    def lookup(self, key: str) -> SendResult | None:
        """What happened to an attempt, if the provider ever saw it (used by crash recovery)."""
        row = self.conn.execute(
            "SELECT kind, smtp_code, body FROM sim_events WHERE idempotency_key=? "
            "AND kind IN ('accepted','hard_bounce','soft_bounce') ORDER BY id LIMIT 1", (key,)).fetchone()
        return SendResult(row["kind"], row["smtp_code"], row["body"]) if row else None

    def _insert(self, key, send_id, contact_id, kind, deliver_at, smtp_code=None, body=None,
                true_label=None, processed_at=None):
        self.conn.execute(
            "INSERT INTO sim_events(idempotency_key, send_id, contact_id, kind, smtp_code, body, true_label, "
            "deliver_at, processed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (key, send_id, contact_id, kind, smtp_code, body, true_label, deliver_at, processed_at))

    def send(self, idempotency_key: str, send_id: int, contact_id: int, step: int, at: str,
             domain: str, first_name: str, tz: str = "Asia/Kolkata", mailbox: str = "") -> SendResult:
        prior = self.lookup(idempotency_key)
        if prior:
            return prior
        s, r = self.s, lifecycle.rng("provider", idempotency_key)
        hard = s.bad_domain_bounce_rate if domain in s.bad_domains else s.hard_bounce_rate
        u = r.random()
        if u < hard:
            msg = "User unknown"
            self._insert(idempotency_key, send_id, contact_id, "hard_bounce",
                         timeutil.add(at, minutes=r.randint(1, 10)), "550 5.1.1", msg)
            return SendResult("hard_bounce", "550 5.1.1", msg)
        extra_policy = s.poor_reputation_policy_rate if mailbox in s.poor_reputation_mailboxes else 0.0
        if u < hard + s.soft_bounce_rate + extra_policy:
            policy = u >= hard + s.soft_bounce_rate or r.random() < s.policy_share_of_soft
            code, msg = r.choice(POLICY if policy else TRANSIENT)
            self._insert(idempotency_key, send_id, contact_id, "soft_bounce",
                         timeutil.add(at, minutes=r.randint(1, 10)), code, msg)
            return SendResult("soft_bounce", code, msg)

        self._insert(idempotency_key, send_id, contact_id, "accepted", at, processed_at=at)
        v = r.random()
        if v < s.complaint_rate:
            self._insert(idempotency_key, send_id, contact_id, "complaint",
                         timeutil.add(at, minutes=r.randint(60, 1440)), body="Marked as spam")
        elif v < s.complaint_rate + s.unsub_click_rate:
            self._insert(idempotency_key, send_id, contact_id, "unsubscribe_click",
                         timeutil.add(at, minutes=r.randint(60, 2880)), body="List-Unsubscribe one-click")
        elif v < s.complaint_rate + s.unsub_click_rate + s.reply_rates[step - 1]:
            labels, weights = zip(*s.reply_mix)
            label = r.choices(labels, weights)[0]
            deliver = timeutil.add(at, minutes=r.randint(120, 4 * 1440))
            return_date = None
            if label == "out_of_office" and r.random() < 0.7:
                return_date = (timeutil.local(deliver, tz).date() + timedelta(days=r.randint(2, 12))).isoformat()
            body = reply_body(label, r, first_name, return_date)
            self._insert(idempotency_key, send_id, contact_id, "reply", deliver, body=body, true_label=label)
        return SendResult("accepted")
