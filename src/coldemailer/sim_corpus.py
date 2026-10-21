"""Reply bodies the simulated prospects send. No LLM: phrase lists + Faker-style names.

OOO bodies always carry an unambiguous auto-reply marker so the rule classifier can catch them
at ingest. About 15% of interested / not-interested replies are deliberately ambiguous (no rule
keyword) so the LLM path has real work.
"""
from __future__ import annotations

from datetime import date

INTERESTED = [
    "Hi, thanks for reaching out. This sounds interesting, can you send more details?",
    "Happy to chat. Does Thursday afternoon work for a quick call?",
    "Yes, this is relevant for us right now. Send over a few times next week.",
    "Interesting timing, we were just discussing this. Let's set up a call.",
    "Sure, I'd like to learn more. What does pricing look like?",
    "Thanks {first_name} here, keen to see the one-pager you mentioned.",
]
INTERESTED_AMBIGUOUS = [
    "I'm not the right person for this but our COO might be. Loop her in?",
    "Possibly. What would you need from our side to get started?",
    "We tried something similar last year. What makes yours different?",
]
NOT_INTERESTED = [
    "Thanks, but we're not interested at the moment.",
    "Not a fit for us right now, appreciate it.",
    "No thanks, we already have a solution for this.",
    "We're not looking at this area this year. Please don't follow up.",
    "Not interested, thanks.",
    "We don't have budget for this, sorry.",
]
NOT_INTERESTED_AMBIGUOUS = [
    "Maybe check back after the holidays?",
    "Hmm, we're in the middle of a reorg. Timing is bad.",
    "I'll pass this along, but don't expect much.",
]
OOO = [
    "Automatic reply: I am out of office{until} with limited access to email.",
    "Thanks for your email. I'm currently out of the office{until}. For urgent matters contact my team.",
    "OOO: I'm on leave{until} and will respond when I'm back.",
    "Auto-reply: I am travelling{until} and checking email infrequently. Out of office.",
    "I'm out of office{until}. Your message will not be forwarded.",
    "Automatic reply - on leave{until}.",
]
UNSUBSCRIBE = [
    "Please remove me from your mailing list.",
    "Unsubscribe.",
    "Stop emailing me.",
    "Take me off this list, thanks.",
    "Please unsubscribe me from these emails.",
    "Remove me. Not interested in any further emails.",
]

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def _until(return_date: str | None, rng) -> str:
    if not return_date:
        return ""
    d = date.fromisoformat(return_date)
    style = rng.randrange(3)
    if style == 0:
        return f" until {MONTHS[d.month - 1]} {d.day}"
    if style == 1:
        return f" and back on {MONTHS[d.month - 1]} {d.day}"
    return f" until {d.isoformat()}"


def reply_body(label: str, rng, first_name: str, return_date: str | None = None) -> str:
    if label == "out_of_office":
        return rng.choice(OOO).format(until=_until(return_date, rng))
    if label == "unsubscribe":
        return rng.choice(UNSUBSCRIBE)
    if label == "interested":
        pool = INTERESTED_AMBIGUOUS if rng.random() < 0.15 else INTERESTED
    elif label == "not_interested":
        pool = NOT_INTERESTED_AMBIGUOUS if rng.random() < 0.15 else NOT_INTERESTED
    else:
        raise ValueError(label)
    return rng.choice(pool).replace("{first_name}", first_name)
