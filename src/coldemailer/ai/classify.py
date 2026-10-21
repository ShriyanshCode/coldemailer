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


def _llm_labels(llm, rows) -> tuple[dict[int, str], str | None]:
    import json
    items = [{"id": r["id"], "text": r["body"][:500]} for r in rows]
    messages = [
        {"role": "system", "content":
            "You label replies to B2B cold emails. Labels: interested, not_interested, out_of_office, unsubscribe. "
            "If you can't tell, use unsure. Answer with a JSON array only: [{\"id\": 1, \"label\": \"interested\"}]."},
        {"role": "user", "content": json.dumps(items)},
    ]
    answer = llm.chat(messages, "classify", max_tokens=1500)
    if not answer:
        return {}, None
    text = answer[0]
    start, end = text.find("["), text.rfind("]")
    try:
        parsed = json.loads(text[start:end + 1]) if start >= 0 else []
    except json.JSONDecodeError:
        return {}, answer[1]
    out = {}
    for item in parsed if isinstance(parsed, list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            out[item["id"]] = str(item.get("label", "")).strip().lower()
    return out, answer[1]


def backlog(conn, settings, llm=None, now: str | None = None) -> int:
    """Label replies the rules couldn't settle: batched LLM calls when a model is available,
    keyword fallback otherwise. Replies the model can't settle twice go to the review queue.
    The contact has already stopped, so none of this can cause or block a send."""
    from .. import engine  # avoid import cycle

    rows = conn.execute(
        "SELECT r.*, c.status FROM replies r JOIN contacts c ON c.id=r.contact_id "
        "WHERE r.classification IS NULL AND NOT EXISTS (SELECT 1 FROM review_items i WHERE i.reply_id=r.id "
        "AND i.resolved_at IS NULL) ORDER BY r.id LIMIT ?",
        (settings.classify_batch * settings.classify_calls_per_cycle,)).fetchall()
    if not rows:
        return 0
    at = now or rows[-1]["received_at"]
    done = 0
    for i in range(0, len(rows), settings.classify_batch):
        chunk = rows[i:i + settings.classify_batch]
        labels, model = _llm_labels(llm, chunk) if llm else ({}, None)
        for r in chunk:
            label = labels.get(r["id"])
            if model and label in LABELS:
                engine.apply_classification(conn, r["id"], label, f"llm:{model}", at)
            elif model:  # the model answered but couldn't settle this one
                conn.execute("UPDATE replies SET llm_attempts=llm_attempts+1 WHERE id=?", (r["id"],))
                if r["llm_attempts"] + 1 >= 2:
                    engine.open_review(conn, r["contact_id"], "classification", "classification", at,
                                       r["send_id"], r["id"], body=r["body"], model_label=label)
                continue
            else:
                engine.apply_classification(conn, r["id"], fallback(r["body"]), "fallback", at)
            done += 1
    return done
