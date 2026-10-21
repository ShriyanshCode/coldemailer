"""Sequence rules shared by the seed and the engine, so seeded history and live runs agree.

Step numbering: step 1 = first email, steps 2-4 = follow-ups 1-3.
contacts.step = number of steps sent (0-4).
"""
from __future__ import annotations

import hashlib
import random
from datetime import timedelta

from . import timeutil

STATUS_BY_STEP = ["new", "first_sent", "follow_up_1_sent", "follow_up_2_sent", "follow_up_3_sent"]
TERMINAL = {"replied", "bounced", "unsubscribed", "deactivated"}
ACTIVE = set(STATUS_BY_STEP)
ALL_STATUSES = STATUS_BY_STEP + ["replied", "bounced", "unsubscribed", "deactivated"]
STEP_NAMES = ["First email", "Follow-up 1", "Follow-up 2", "Follow-up 3"]
SEND_ACTIONS = ["send_1", "send_2", "send_3", "send_4"]


def rng(*parts) -> random.Random:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def next_action_after(step_sent: int) -> str:
    return f"send_{step_sent + 1}" if step_sent < 4 else "deactivate"


def action_step(action: str) -> int:
    """'send_3' -> 3."""
    return int(action.split("_")[1])


def schedule_next(sent_at: str, step_sent: int, tz: str, waits, key: str,
                  start: str = "09:00", end: str = "18:00") -> str:
    """When the next action is due after `step_sent` went out at `sent_at`.

    Next send: the prospect-local date `waits[step_sent-1]` days later, at a
    deterministic random minute inside their window (spreads sends across the day).
    After step 4: deactivation at the end of that local day's window.
    """
    d = timeutil.local(sent_at, tz).date() + timedelta(days=waits[step_sent - 1])
    if step_sent >= 4:
        return timeutil.window_end_on(d, tz, end)
    minute = rng(key).randrange(timeutil.window_minutes(start, end))
    return timeutil.window_time_on(d, tz, minute, start, end)


def ooo_push(received_at: str, return_date: str | None, tz: str, key: str,
             default_days: int = 3, cap_days: int = 14,
             start: str = "09:00", end: str = "18:00") -> str:
    """Next send time after an out-of-office reply: the return date if known, else +3 days, capped at +14."""
    from datetime import date

    today = timeutil.local(received_at, tz).date()
    target = date.fromisoformat(return_date) if return_date else today + timedelta(days=default_days)
    target = max(today + timedelta(days=1), min(target, today + timedelta(days=cap_days)))
    minute = rng(key, "ooo").randrange(timeutil.window_minutes(start, end))
    return timeutil.window_time_on(target, tz, minute, start, end)
