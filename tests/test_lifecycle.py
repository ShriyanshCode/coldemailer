from datetime import date

from coldemailer import actions, clock, timeutil
from coldemailer.provider_mock import SendResult

from conftest import contact, inject, make_contact, sends, tick


def local_date(ts, tz="Asia/Kolkata"):
    return timeutil.local(ts, tz).date()


def test_new_contact_gets_first_email(conn, settings):
    cid = make_contact(conn)
    tick(conn, settings)
    rows = sends(conn, cid)
    assert [(r["step"], r["state"]) for r in rows] == [(1, "sent")]
    c = contact(conn, cid)
    assert c["status"] == "first_sent" and c["next_action"] == "send_2"
    assert local_date(c["next_action_at"]) == date(2026, 10, 4)
    assert timeutil.in_window(c["next_action_at"], "Asia/Kolkata")


def test_full_sequence_then_deactivated(conn, settings):
    cid = make_contact(conn)
    tick(conn, settings, 24 * 25)
    rows = sends(conn, cid)
    assert [r["step"] for r in rows if r["state"] == "sent"] == [1, 2, 3, 4]
    gaps = [(local_date(b["sent_at"]) - local_date(a["sent_at"])).days for a, b in zip(rows, rows[1:])]
    assert gaps[0] >= 3 and gaps[1] >= 5 and gaps[2] >= 7
    c = contact(conn, cid)
    assert c["status"] == "deactivated" and c["next_action"] is None
    deact = conn.execute("SELECT occurred_at FROM events WHERE contact_id=? AND type='deactivated'", (cid,)).fetchone()
    assert (local_date(deact[0]) - local_date(rows[-1]["sent_at"])).days >= 7


def test_reply_stops_sequence(conn, settings):
    cid = make_contact(conn)
    tick(conn, settings)
    inject(conn, cid, "reply", timeutil.add(clock.now(conn), hours=1),
           "Sounds interesting, can you send more details?", "interested")
    tick(conn, settings, 24 * 10)
    assert contact(conn, cid)["status"] == "replied"
    assert len(sends(conn, cid)) == 1
    reply = conn.execute("SELECT classification FROM replies WHERE contact_id=?", (cid,)).fetchone()
    assert reply[0] == "interested"


def test_ooo_pushes_not_stops(conn, settings):
    cid = make_contact(conn)
    tick(conn, settings)
    inject(conn, cid, "reply", "2026-10-01T12:00:00Z",
           "Automatic reply: I am out of office until October 12 with limited access to email.", "out_of_office")
    tick(conn, settings, 24 * 9)  # to Oct 10
    c = contact(conn, cid)
    assert c["status"] == "first_sent"
    assert local_date(c["ooo_until"]) == date(2026, 10, 12)
    assert local_date(c["next_action_at"]) == date(2026, 10, 12)
    tick(conn, settings, 24 * 3)
    step2 = sends(conn, cid)[1]
    assert step2["state"] == "sent" and local_date(step2["sent_at"]) >= date(2026, 10, 12)


def test_hard_bounce_suppresses_forever(conn, settings, force_results):
    force_results([SendResult("hard_bounce", "550 5.1.1", "User unknown")])
    cid = make_contact(conn)
    tick(conn, settings)
    c = contact(conn, cid)
    assert c["status"] == "bounced" and c["next_action"] is None
    assert conn.execute("SELECT reason FROM suppression WHERE email=?", (c["email"],)).fetchone()[0] == "hard_bounce"
    tick(conn, settings, 24 * 30)
    assert len(sends(conn, cid)) == 1
    # Even if someone corrupts the row back to "due", the claim-time suppression check holds.
    conn.execute("UPDATE contacts SET status='new', next_action='send_1', next_action_at=? WHERE id=?",
                 (clock.now(conn), cid))
    tick(conn, settings)
    assert [r["state"] for r in sends(conn, cid)] == ["bounced"]
    assert contact(conn, cid)["status"] == "bounced"


def test_unsubscribe_click_and_complaint_suppress(conn, settings):
    a, b = make_contact(conn), make_contact(conn)
    tick(conn, settings)
    at = timeutil.add(clock.now(conn), minutes=30)
    inject(conn, a, "unsubscribe_click", at)
    inject(conn, b, "complaint", at)
    tick(conn, settings)
    for cid, reason in ((a, "unsubscribe_link"), (b, "complaint")):
        c = contact(conn, cid)
        assert c["status"] == "unsubscribed"
        assert conn.execute("SELECT reason FROM suppression WHERE email=?", (c["email"],)).fetchone()[0] == reason


def test_soft_bounce_retries_then_review(conn, settings, force_results):
    force_results([SendResult("soft_bounce", "452 4.2.2", "Mailbox full")] * 3)
    cid = make_contact(conn)
    tick(conn, settings, 48)
    attempts = conn.execute("SELECT at, result FROM send_attempts ORDER BY attempt").fetchall()
    assert [a["result"] for a in attempts] == ["soft_bounce"] * 3
    hours = [timeutil.seconds_between(x["at"], y["at"]) / 3600 for x, y in zip(attempts, attempts[1:])]
    assert hours[0] >= 1 and hours[1] >= 4
    c = contact(conn, cid)
    assert c["hold_reason"] == "repeated_soft_bounce"
    item = conn.execute("SELECT * FROM review_items WHERE contact_id=? AND resolved_at IS NULL", (cid,)).fetchone()
    assert item["kind"] == "soft_bounce"
    tick(conn, settings, 24)
    assert conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 3  # held: nothing sent
    actions.resolve_review(conn, item["id"], "retry_now", settings=settings)
    tick(conn, settings, 24)
    results = [r[0] for r in conn.execute("SELECT result FROM send_attempts ORDER BY attempt")]
    assert results == ["soft_bounce"] * 3 + ["accepted"]
    assert contact(conn, cid)["status"] == "first_sent"
    assert len(sends(conn, cid)) == 1


def test_policy_rejection_goes_straight_to_review(conn, settings, force_results):
    force_results([SendResult("soft_bounce", "550 5.7.1", "Message rejected due to content policy")])
    cid = make_contact(conn)
    tick(conn, settings, 24)
    assert conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 1
    assert contact(conn, cid)["hold_reason"] == "policy_rejection"


def test_ambiguous_reply_without_model_uses_fallback(conn, settings):
    cid = make_contact(conn)
    tick(conn, settings)
    rid = inject(conn, cid, "reply", timeutil.add(clock.now(conn), minutes=20),
                 "Maybe check back after the holidays?", "not_interested")
    tick(conn, settings)
    assert contact(conn, cid)["status"] == "replied"
    reply = conn.execute("SELECT * FROM replies WHERE sim_event_id=?", (rid,)).fetchone()
    assert reply["classified_by"] == "fallback"  # no model configured -> keyword fallback, never blocks
