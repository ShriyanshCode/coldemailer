"""Human-in-the-loop actions: resolve review items, resume mailboxes, suppress contacts.

Each runs in one transaction and writes a human_action event, so the timeline shows who did what.
"""
from __future__ import annotations

from . import clock, db, engine, timeutil
from .ai.classify import LABELS

SEND_ACTIONS = ("retry_now", "retry_later", "mark_bounced", "suppress")


def _item(conn, item_id: int):
    item = conn.execute("SELECT * FROM review_items WHERE id=?", (item_id,)).fetchone()
    if item is None:
        raise ValueError(f"review item {item_id} not found")
    if item["resolved_at"]:
        raise ValueError(f"review item {item_id} is already resolved")
    return item


def resolve_review(conn, item_id: int, action: str, days: int = 3, note: str | None = None,
                   settings=None) -> None:
    now = clock.now(conn)
    with db.tx(conn):
        item = _item(conn, item_id)
        c = conn.execute("SELECT * FROM contacts WHERE id=?", (item["contact_id"],)).fetchone()
        if item["kind"] == "classification":
            if not action.startswith("label:") or action[6:] not in LABELS:
                raise ValueError(f"'{action}' is not valid for a classification item; use label:<{'|'.join(LABELS)}>")
            label = action[6:]
            engine.apply_classification(conn, item["reply_id"], label, "human", now)
            outcome = f"Classified as {label.replace('_', ' ')}"
        else:
            if action not in SEND_ACTIONS:
                raise ValueError(f"'{action}' is not valid here; use one of {', '.join(SEND_ACTIONS)}")
            if action in ("retry_now", "retry_later"):
                at = now if action == "retry_now" else timeutil.add(now, days=days)
                if settings is not None:
                    at = timeutil.next_window_open(at, c["timezone"], settings.window_start, settings.window_end)
                conn.execute("UPDATE contacts SET hold_reason=NULL, next_action_at=?, updated_at=? WHERE id=?",
                             (at, now, c["id"]))
                outcome = "Retry now" if action == "retry_now" else f"Retry in {days} days"
            elif action == "mark_bounced":
                engine.suppress(conn, c["email"], "hard_bounce", now)
                engine.stop(conn, c["id"], "bounced", now)
                engine.event(conn, "hard_bounced", now, c["id"], item["send_id"], via="manual")
                if item["send_id"]:
                    conn.execute("UPDATE sends SET state='bounced' WHERE id=?", (item["send_id"],))
                outcome = "Marked bounced"
            else:
                engine.suppress(conn, c["email"], "manual", now)
                engine.stop(conn, c["id"], "unsubscribed", now)
                engine.event(conn, "unsubscribed", now, c["id"], via="manual")
                outcome = "Suppressed"
        conn.execute("UPDATE review_items SET resolved_at=?, resolution=?, note=? WHERE id=?",
                     (now, outcome, note, item_id))
        engine.event(conn, "human_action", now, c["id"], item["send_id"], actor="operator", action=outcome,
                     review_item=item_id, note=note)


def resume_mailbox(conn, mailbox_id: int) -> None:
    now = clock.now(conn)
    with db.tx(conn):
        mb = conn.execute("SELECT * FROM mailboxes WHERE id=?", (mailbox_id,)).fetchone()
        if mb is None:
            raise ValueError(f"mailbox {mailbox_id} not found")
        if not mb["paused"]:
            raise ValueError(f"mailbox {mb['address']} is not paused")
        conn.execute("UPDATE mailboxes SET paused=0, paused_reason=NULL, paused_at=NULL WHERE id=?", (mailbox_id,))
        engine.event(conn, "mailbox_resumed", now, mailbox_id=mailbox_id, mailbox=mb["address"], actor="operator")
        engine.event(conn, "human_action", now, mailbox_id=mailbox_id, actor="operator",
                     action=f"Mailbox resumed: {mb['address']}")


def suppress_contact(conn, contact_id: int, reason: str = "manual") -> None:
    now = clock.now(conn)
    with db.tx(conn):
        c = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if c is None:
            raise ValueError(f"contact {contact_id} not found")
        if conn.execute("SELECT 1 FROM suppression WHERE email=?", (c["email"],)).fetchone():
            raise ValueError(f"{c['email']} is already suppressed")
        engine.suppress(conn, c["email"], reason, now)
        if c["status"] not in ("bounced", "unsubscribed", "deactivated", "replied"):
            engine.stop(conn, contact_id, "unsubscribed", now)
            engine.event(conn, "unsubscribed", now, contact_id, via="manual")
        engine.event(conn, "human_action", now, contact_id, actor="operator", action="Suppressed manually")
