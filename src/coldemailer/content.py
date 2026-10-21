"""Pre-send content checks. Any reason returned blocks the send."""
from __future__ import annotations

import re

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
URL = re.compile(r"https?://\S+")
SPAM_WORDS = (
    "free money", "guarantee", "guaranteed", "act now", "100%", "risk-free", "risk free",
    "winner", "no cost", "cash bonus", "click here", "limited time", "urgent", "double your",
    "earn $", "miracle", "buy now", "order now", "lowest price", "you have been selected",
)
MAX_BODY_CHARS = 1200
MAX_LINKS = 2


def check(subject: str, body: str) -> list[str]:
    reasons: list[str] = []
    text = f"{subject}\n{body}"
    if PLACEHOLDER.search(text):
        reasons.append("unrendered_placeholder")
    low = text.lower()
    if any(w in low for w in SPAM_WORDS):
        reasons.append("spam_words")
    links = [u for u in URL.findall(body) if "unsub" not in u]
    if len(links) > MAX_LINKS:
        reasons.append("too_many_links")
    if len(body) > MAX_BODY_CHARS:
        reasons.append("too_long")
    if "unsub" not in body.lower():
        reasons.append("missing_unsubscribe")
    letters = [c for c in subject if c.isalpha()]
    if len(letters) >= 6 and all(c.isupper() for c in letters):
        reasons.append("subject_all_caps")
    return reasons
