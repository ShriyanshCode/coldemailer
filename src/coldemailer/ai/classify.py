"""Reply classification: rules at ingest (decide whether the sequence stops), LLM later for the rest."""
from __future__ import annotations

import re
from datetime import date

from .. import timeutil

LABELS = ("interested", "not_interested", "out_of_office", "unsubscribe")

UNSUB = re.compile(r"\b(unsubscribe|remove me|take me off|stop emailing|opt me out)\b", re.I)
OOO = re.compile(r"\b(out of (the )?office|ooo|automatic reply|auto-?reply|on leave)\b", re.I)
NOT_INTERESTED = re.compile(
    r"\b(not interested|no thanks|not a fit|don'?t follow up|no budget|don'?t have budget|"
    r"already have a solution|not looking)\b", re.I)
INTERESTED = re.compile(
    r"\b(interested|interesting|happy to chat|set up a call|learn more|keen to|send over|"
    r"relevant for us|pricing|let'?s (talk|chat|set up))\b", re.I)

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
MONTH_DAY = re.compile(r"\b(?:until|back on|returning|back)\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})\b", re.I)
DAY_MONTH = re.compile(r"\b(?:until|back on|returning|back)\s+(\d{1,2})\s+([A-Za-z]{3,9})\b", re.I)


def rules(body: str) -> tuple[str | None, float]:
    if UNSUB.search(body):
        return "unsubscribe", 1.0
    if OOO.search(body):
        return "out_of_office", 1.0
    if NOT_INTERESTED.search(body):
        return "not_interested", 0.8
    if INTERESTED.search(body):
        return "interested", 0.8
    return None, 0.0


def fallback(body: str) -> str:
    """Keyword-free last resort when no model is available: a question back usually means interest."""
    label, _ = rules(body)
    if label:
        return label
    return "interested" if "?" in body else "not_interested"


def parse_return_date(body: str, received_at: str) -> str | None:
    ref = timeutil.from_ts(received_at).date()
    m = ISO_DATE.search(body)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None
    month = day = None
    m = MONTH_DAY.search(body)
    if m and m[1][:3].lower() in MONTHS:
        month, day = MONTHS[m[1][:3].lower()], int(m[2])
    else:
        m = DAY_MONTH.search(body)
        if m and m[2][:3].lower() in MONTHS:
            month, day = MONTHS[m[2][:3].lower()], int(m[1])
    if not month:
        return None
    for year in (ref.year, ref.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= ref:
            return d.isoformat()
    return None


def backlog(conn, settings, llm=None, now: str | None = None) -> int:
    """Label replies the rules couldn't settle. Without a model: keyword fallback.
    (The LLM path is added by the OpenRouter task.)"""
    from .. import engine  # avoid import cycle

    rows = conn.execute(
        "SELECT r.*, c.status FROM replies r JOIN contacts c ON c.id=r.contact_id "
        "WHERE r.classification IS NULL ORDER BY r.id LIMIT ?",
        (settings.classify_batch * settings.classify_calls_per_cycle,)).fetchall()
    if not rows:
        return 0
    at = now or rows[-1]["received_at"]
    for r in rows:
        engine.apply_classification(conn, r["id"], fallback(r["body"]), "fallback", at)
    return len(rows)
