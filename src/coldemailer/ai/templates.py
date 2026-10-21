"""Email templates: static fallbacks, the per-segment cache, rendering, and the committed seed file."""
from __future__ import annotations

import hashlib
import json
import os
import re

INDUSTRY_TOPIC = {
    "SaaS": "reducing churn during onboarding",
    "Fintech": "cutting month-end reconciliation time",
    "Retail": "forecasting seasonal demand",
    "Healthcare": "reducing appointment no-shows",
    "Logistics": "tracking late shipments before customers notice",
}
SENDER_NAMES = ["Maya Iyer", "Daniel Brooks", "Sara Lindqvist", "Arjun Rao", "Leah Chen"]
MERGE_FIELDS = {"first_name", "company", "role", "industry_topic", "sender_name", "unsubscribe_url"}
FIELD = re.compile(r"\{([a-z_]+)\}")

FOOTER = "\n\n{sender_name}\nNorthbeam\n\nNot relevant? Unsubscribe here: {unsubscribe_url}"

STATIC: dict[int, tuple[str, str]] = {
    1: ("Quick idea for {company}",
        "Hi {first_name},\n\nI work with {role}s on {industry_topic}. Teams like {company} usually "
        "lose a few hours a week to it, and we have a simple way to win most of that back.\n\n"
        "Would a 15-minute call next week be useful?" + FOOTER),
    2: ("Re: Quick idea for {company}",
        "Hi {first_name},\n\nFollowing up on my note about {industry_topic}. Happy to send a one-page "
        "example of how a similar team set it up, if that's easier than a call." + FOOTER),
    3: ("Re: Quick idea for {company}",
        "Hi {first_name},\n\nOne more thought on {industry_topic}: the first win usually shows up within "
        "a month, without changing your current tools. Open to a quick look?" + FOOTER),
    4: ("Closing the loop",
        "Hi {first_name},\n\nI'll stop here so I don't crowd your inbox. If {industry_topic} becomes a "
        "priority at {company}, just reply and I'll pick it up." + FOOTER),
}


def ensure_static(conn, now: str) -> None:
    rows = [("*", step, v, s, b, "static", now) for step, (s, b) in STATIC.items() for v in ("A", "B")]
    conn.executemany(
        "INSERT OR IGNORE INTO templates(segment, step, variant, subject, body, source, created_at) "
        "VALUES (?,?,?,?,?,?,?)", rows)


def pick(conn, segment: str, step: int, variant: str):
    row = conn.execute(
        "SELECT * FROM templates WHERE segment=? AND step=? AND variant=?", (segment, step, variant)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM templates WHERE segment='*' AND step=? AND variant=?", (step, variant)
        ).fetchone()
    return row


def unsubscribe_token(contact_id: int, campaign_id: int = 1) -> str:
    return hashlib.sha256(f"{contact_id}:{campaign_id}".encode()).hexdigest()[:16]


def _fill(text: str, values: dict) -> str:
    def sub(m):
        v = values.get(m.group(1))
        return m.group(0) if v in (None, "") else str(v)
    return FIELD.sub(sub, text)


def render(tpl, contact, mailbox_address: str, mailbox_id: int = 1, campaign_id: int = 1):
    """-> (subject, body, headers). Unknown or empty fields stay as {field} so content.check catches them."""
    token = unsubscribe_token(contact["id"], campaign_id)
    url = f"https://unsub.example.test/u/{token}"
    values = {
        "first_name": contact["first_name"],
        "company": contact["company"],
        "role": contact["role"],
        "industry_topic": INDUSTRY_TOPIC.get(contact["industry"]),
        "sender_name": SENDER_NAMES[(mailbox_id - 1) % len(SENDER_NAMES)],
        "unsubscribe_url": url,
    }
    domain = mailbox_address.split("@", 1)[1]
    headers = {
        "From": mailbox_address,
        "To": contact["email"],
        "List-Unsubscribe": f"<mailto:unsubscribe@{domain}?subject=unsub-{contact['id']}>, <{url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    return _fill(tpl["subject"], values), _fill(tpl["body"], values), headers


def load_seed_file(conn, path: str = "data/templates.seed.json") -> int:
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    conn.executemany(
        "INSERT OR REPLACE INTO templates(segment, step, variant, subject, body, source, created_at) "
        "VALUES (:segment,:step,:variant,:subject,:body,:source,:created_at)", items)
    return len(items)


def export_seed_file(conn, path: str = "data/templates.seed.json") -> int:
    rows = conn.execute(
        "SELECT segment, step, variant, subject, body, source, created_at FROM templates "
        "WHERE source != 'static' ORDER BY segment, step, variant").fetchall()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([dict(r) for r in rows], f, indent=2, ensure_ascii=False)
    return len(rows)
