"""Email templates: static fallbacks, the per-segment cache, rendering, and the committed seed file."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re

from .. import content
from ..config import ROOT, SEGMENTS

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


def unsubscribe_token(contact_id: int, campaign_id: int = 1, secret: str = "dev-only-unsubscribe-secret") -> str:
    """HMAC, not a plain hash: without the secret nobody can forge the link for someone else."""
    return hmac.new(secret.encode(), f"{contact_id}:{campaign_id}".encode(), hashlib.sha256).hexdigest()[:32]


def _fill(text: str, values: dict) -> str:
    def sub(m):
        v = values.get(m.group(1))
        return m.group(0) if v in (None, "") else str(v)
    return FIELD.sub(sub, text)


def render(tpl, contact, mailbox_address: str, mailbox_id: int = 1, campaign_id: int = 1,
           secret: str = "dev-only-unsubscribe-secret"):
    """-> (subject, body, headers). Unknown or empty fields stay as {field} so content.check catches them."""
    token = unsubscribe_token(contact["id"], campaign_id, secret)
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


SEED_FILE = ROOT / "data" / "templates.seed.json"


def load_seed_file(conn, path=None) -> int:
    path = path or SEED_FILE
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    conn.executemany(
        "INSERT OR REPLACE INTO templates(segment, step, variant, subject, body, source, created_at) "
        "VALUES (:segment,:step,:variant,:subject,:body,:source,:created_at)", items)
    return len(items)


def export_seed_file(conn, path=None) -> int:
    path = path or SEED_FILE
    rows = conn.execute(
        "SELECT segment, step, variant, subject, body, source, created_at FROM templates "
        "WHERE source != 'static' ORDER BY segment, step, variant").fetchall()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([dict(r) for r in rows], f, indent=2, ensure_ascii=False)
    return len(rows)


STEP_BRIEF = {
    1: "the first email: introduce one concrete problem and ask for a short call",
    2: "follow-up 1 (3 days later): short bump that offers a one-page example instead of a call",
    3: "follow-up 2: share a different angle or small proof point, still low pressure",
    4: "follow-up 3, the last one: politely close the loop and invite a reply if timing changes",
}
VARIANT_STYLE = {"A": "direct and plain", "B": "opens with a short question"}
SAMPLE = {"id": 1, "first_name": "Priya", "company": "Acme", "role": "VP Sales", "industry": "SaaS",
          "email": "priya@acme.com"}


def _prompt(segment: str, step: int, variant: str) -> list[dict]:
    fields = ", ".join("{" + f + "}" for f in sorted(MERGE_FIELDS))
    return [
        {"role": "system", "content":
            "You write short, plain-text B2B cold emails for Northbeam, a workflow tool. "
            "Answer with one JSON object only: {\"subject\": \"...\", \"body\": \"...\"}. No markdown."},
        {"role": "user", "content":
            f"Audience: {segment} companies; the reader's role is the merge field {{role}}. "
            f"Topic: {INDUSTRY_TOPIC[segment]}. This is {STEP_BRIEF[step]}. Style: {VARIANT_STYLE[variant]}. "
            f"Use only these merge fields, written exactly like this: {fields}. Start with 'Hi {{first_name}},'. "
            "Under 110 words. No links except the unsubscribe line. No hype words (free, guarantee, urgent, act now). "
            "End the body with: '{sender_name}\nNorthbeam\n\nNot relevant? Unsubscribe here: {unsubscribe_url}'. "
            + ("Subject under 8 words." if step == 1 else "Subject: reuse 'Re: ' plus a short first subject.")},
    ]


def _parse(text: str) -> tuple[str, str] | None:
    text = text.strip().strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    subject, body = str(obj.get("subject", "")).strip(), str(obj.get("body", "")).strip()
    return (subject, body) if subject and body else None


def validate(subject: str, body: str) -> list[str]:
    unknown = {f for f in FIELD.findall(subject + body)} - MERGE_FIELDS
    problems = [f"unknown_field:{f}" for f in sorted(unknown)]
    if "{unsubscribe_url}" not in body:
        problems.append("missing_unsubscribe")
    s, b, _ = render({"subject": subject, "body": body}, SAMPLE, "sales1@mail-a.test")
    return problems + content.check(s, b)


def warm(conn, client, now: str) -> dict:
    """Generate any missing segment x step x variant templates. Resumable; failures keep the static one."""
    out = {"generated": 0, "skipped": 0, "failed": 0}
    for segment in SEGMENTS:
        for step in (1, 2, 3, 4):
            for variant in ("A", "B"):
                if conn.execute("SELECT 1 FROM templates WHERE segment=? AND step=? AND variant=?",
                                (segment, step, variant)).fetchone():
                    out["skipped"] += 1
                    continue
                answer = client.chat(_prompt(segment, step, variant), "template", patient=True) if client else None
                parsed = _parse(answer[0]) if answer else None
                if parsed and "{unsubscribe_url}" not in parsed[1]:
                    parsed = (parsed[0], parsed[1].rstrip() + FOOTER)
                if not parsed or validate(*parsed):
                    out["failed"] += 1
                    continue
                conn.execute(
                    "INSERT INTO templates(segment, step, variant, subject, body, source, created_at) "
                    "VALUES (?,?,?,?,?,?,?)", (segment, step, variant, parsed[0], parsed[1], f"llm:{answer[1]}", now))
                conn.execute("INSERT INTO events(type, detail, occurred_at) VALUES ('template_generated', ?, ?)",
                             (json.dumps({"segment": segment, "step": step, "variant": variant, "model": answer[1]}), now))
                out["generated"] += 1
    return out
