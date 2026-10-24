from dataclasses import replace

from coldemailer import actions, clock, policies, timeutil
from coldemailer.provider_mock import SendResult

from conftest import contact, make_contact, make_db, sends, tick


def one_mailbox(settings, start_limit=20, increment=10, max_limit=200, warm_days=0):
    conn = make_db(settings, mailboxes=1)
    conn.execute("UPDATE mailboxes SET warmup_started_on=?, start_limit=?, daily_increment=?, max_limit=?",
                 (timeutil.shift_day(timeutil.ist_day(clock.now(conn)), -warm_days), start_limit, increment, max_limit))
    return conn


def mixed_zone_contacts(conn, n):
    """Prospects in Kolkata and London: together their windows cover 09:00-22:30 IST, as in real use."""
    return [make_contact(conn, timezone="Asia/Kolkata" if i % 2 else "Europe/London") for i in range(n)]


def sent_rows(conn):
    return conn.execute("SELECT * FROM sends WHERE state='sent' ORDER BY sent_at").fetchall()


def deferred(conn, reason):
    return conn.execute("SELECT * FROM events WHERE type='deferred' AND json_extract(detail,'$.reason')=?",
                        (reason,)).fetchall()


def test_window_respects_timezone(conn, settings):
    cid = make_contact(conn, timezone="America/Los_Angeles")  # due at 10:00 IST = 21:30 PDT the day before
    tick(conn, settings)
    assert sends(conn, cid) == []
    assert deferred(conn, "outside_window")
    tick(conn, settings, 12)
    s = sends(conn, cid)[0]
    local = timeutil.local(s["sent_at"], "America/Los_Angeles")
    assert s["state"] == "sent" and 9 <= local.hour < 18


def test_window_across_dst(conn, settings):
    a = make_contact(conn, timezone="America/New_York", next_action_at="2026-10-31T14:00:00Z")  # 10:00 EDT
    b = make_contact(conn, timezone="America/New_York", next_action_at="2026-11-02T15:00:00Z")  # 10:00 EST
    tick(conn, settings, 24 * 33)
    for cid in (a, b):
        s = sends(conn, cid)[0]
        assert s["state"] == "sent"
        assert timeutil.in_window(s["sent_at"], "America/New_York")
        assert 9 <= timeutil.local(s["sent_at"], "America/New_York").hour < 18


def test_warmup_limit_caps_daily_sends(settings):
    conn = one_mailbox(settings)  # warm-up day 0: limit 20
    mixed_zone_contacts(conn, 100)
    tick(conn, settings, 13)  # to 23:00 IST, same IST day
    assert len(sent_rows(conn)) == 20
    assert conn.execute("SELECT sent FROM mailbox_daily").fetchone()[0] == 20
    assert deferred(conn, "mailbox_capacity") or deferred(conn, "pacing")


def test_warmup_ramps_next_day(settings):
    conn = one_mailbox(settings)
    mixed_zone_contacts(conn, 100)
    tick(conn, settings, 24 + 13)
    per_day = dict(conn.execute("SELECT day, sent FROM mailbox_daily").fetchall())
    assert per_day == {"2026-10-01": 20, "2026-10-02": 30}


def test_pacing_spreads_sends(settings):
    conn = one_mailbox(settings)
    mixed_zone_contacts(conn, 100)
    tick(conn, settings, 13)
    times = [r["sent_at"] for r in sent_rows(conn)]
    gaps = [timeutil.seconds_between(a, b) for a, b in zip(times, times[1:])]
    assert min(gaps) >= policies.pacing_gap_s(20)


def test_domain_cap(conn, settings):
    for i in range(60):
        make_contact(conn, email=f"p{i}@bigcorp.com", domain="bigcorp.com")
    tick(conn, settings, 13)
    assert len(sent_rows(conn)) == 50
    assert len({e["contact_id"] for e in deferred(conn, "domain_cap")}) == 10


def test_all_mailboxes_paused_defers_once_per_day(conn, settings):
    conn.execute("UPDATE mailboxes SET paused=1, paused_reason='test'")
    ids = [make_contact(conn) for _ in range(10)]
    stats = tick(conn, settings, 12)  # stays inside IST Oct 1
    assert sum(s.sent for s in stats) == 0
    events = deferred(conn, "mailbox_paused")
    assert sorted(e["contact_id"] for e in events) == ids  # exactly one per contact for the day


def test_autopause_on_bounce_rate(settings, force_results):
    s = replace(settings, autopause_window=50, autopause_min_sample=20)
    conn = one_mailbox(s, start_limit=200, increment=0, max_limit=200)
    bounce = SendResult("hard_bounce", "550 5.1.1", "User unknown")
    ok = SendResult("accepted")
    force_results(([ok] * 5 + [bounce]) * 5)
    for _ in range(30):
        make_contact(conn)
    tick(conn, s, 8)
    mb = conn.execute("SELECT * FROM mailboxes").fetchone()
    assert mb["paused"] == 1 and "bounce rate" in mb["paused_reason"]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='mailbox_paused'").fetchone()[0] == 1
    actions.resume_mailbox(conn, mb["id"])
    assert conn.execute("SELECT paused FROM mailboxes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='mailbox_resumed'").fetchone()[0] == 1


def test_followups_before_first_emails(settings):
    conn = one_mailbox(settings, start_limit=5, increment=0, max_limit=5)
    followups = [make_contact(conn, status="first_sent", step=1, next_action="send_2", mailbox_id=1)
                 for _ in range(5)]
    for _ in range(5):
        make_contact(conn)
    tick(conn, settings, 13)
    rows = sent_rows(conn)
    assert len(rows) == 5
    assert sorted(r["contact_id"] for r in rows) == followups and {r["step"] for r in rows} == {2}


def test_content_block_holds_contact(conn, settings):
    cid = make_contact(conn, company=None)
    tick(conn, settings)
    c = contact(conn, cid)
    assert c["hold_reason"] == "content_blocked"
    assert conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='content_blocked'").fetchone()[0] == 1
    assert sends(conn, cid)[0]["state"] == "blocked"


def test_first_emails_use_every_mailbox_with_a_free_slot(settings):
    # Two warm mailboxes, 100/day and 90/day: each can pace roughly 10 sends an hour.
    conn = make_db(settings, mailboxes=2, limit=100)
    conn.execute("UPDATE mailboxes SET start_limit=90, max_limit=90 WHERE id=2")
    for _ in range(40):
        make_contact(conn)
    tick(conn, settings, 1)
    by_mailbox = dict(conn.execute("SELECT mailbox_id, COUNT(*) FROM sends WHERE state='sent' GROUP BY 1").fetchall())
    assert by_mailbox.get(1, 0) >= 10 and by_mailbox.get(2, 0) >= 10  # not all piled on the roomiest mailbox
    times = {mb: [r["sent_at"] for r in sent_rows(conn) if r["mailbox_id"] == mb] for mb in (1, 2)}
    for mb, ts in times.items():  # each mailbox still respects its own pacing gap
        assert all(timeutil.seconds_between(a, b) >= policies.pacing_gap_s(100 if mb == 1 else 90)
                   for a, b in zip(ts, ts[1:]))


def test_followups_stay_on_their_mailbox(settings):
    conn = make_db(settings, mailboxes=2, limit=100)
    conn.execute("UPDATE mailboxes SET start_limit=5, max_limit=5 WHERE id=2")
    ids = [make_contact(conn, status="first_sent", step=1, next_action="send_2", mailbox_id=2) for _ in range(8)]
    tick(conn, settings, 13)
    rows = sent_rows(conn)
    assert len(rows) == 5 and {r["mailbox_id"] for r in rows} == {2}
    assert len({e["contact_id"] for e in deferred(conn, "mailbox_capacity")} & set(ids)) == 3
