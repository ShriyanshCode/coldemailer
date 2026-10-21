import pytest

from coldemailer import cli, clock, db, engine, runner, timeutil

from conftest import contact, inject, make_contact, sends, tick


def test_run_twice_same_clock_sends_nothing_new(conn, settings):
    ids = [make_contact(conn) for _ in range(5)]
    tick(conn, settings, 2)
    before = conn.execute("SELECT COUNT(*) FROM sends").fetchone()[0]
    assert before == 5
    first = runner.advance(conn, settings, 0, llm=None)[0]
    second = runner.advance(conn, settings, 0, llm=None)[0]
    assert first.sent == 0 and second.sent == 0
    assert conn.execute("SELECT COUNT(*) FROM sends").fetchone()[0] == before
    assert all(len(sends(conn, i)) == 1 for i in ids)


def test_same_interval_twice_is_harmless(conn, settings):
    make_contact(conn)
    a, b = clock.now(conn), timeutil.add(clock.now(conn), hours=1)
    clock.set_now(conn, b)
    engine.run_cycle(conn, settings, a, b)
    engine.run_cycle(conn, settings, a, b)
    assert conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sim_events WHERE kind='accepted'").fetchone()[0] == 1


def test_crash_after_provider_accept_recovers_without_resend(conn, settings, monkeypatch):
    cid = make_contact(conn)
    real = engine._finalize
    calls = []

    def crash_once(*args, **kwargs):
        if not calls:
            calls.append(1)
            raise RuntimeError("process died after the provider accepted")
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "_finalize", crash_once)
    with pytest.raises(RuntimeError):
        tick(conn, settings)
    assert contact(conn, cid)["claimed_by"] is not None
    assert sends(conn, cid)[0]["state"] == "claimed"
    tick(conn, settings)  # lease (5 min) has expired an hour later: recovery asks the provider
    assert conn.execute("SELECT COUNT(*) FROM sim_events WHERE kind='accepted'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 1
    assert [r["state"] for r in sends(conn, cid)] == ["sent"]
    c = contact(conn, cid)
    assert c["status"] == "first_sent" and c["claimed_by"] is None


def test_crash_before_provider_releases_claim(conn, settings, monkeypatch):
    from coldemailer import provider_mock
    cid = make_contact(conn)
    real = provider_mock.MockProvider.send

    def die(*a, **k):
        raise ConnectionError("network down before the provider saw it")

    monkeypatch.setattr(provider_mock.MockProvider, "send", die)
    with pytest.raises(ConnectionError):
        tick(conn, settings)
    monkeypatch.setattr(provider_mock.MockProvider, "send", real)
    tick(conn, settings)
    assert [r["state"] for r in sends(conn, cid)] == ["sent"]
    assert conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 1


def test_reply_in_same_cycle_as_due_followup_blocks_send(conn, settings):
    cid = make_contact(conn)
    tick(conn, settings)
    conn.execute("UPDATE contacts SET next_action_at=? WHERE id=?", (timeutil.add(clock.now(conn), minutes=10), cid))
    inject(conn, cid, "reply", timeutil.add(clock.now(conn), minutes=5), "Not interested, thanks.", "not_interested")
    tick(conn, settings)
    assert len(sends(conn, cid)) == 1
    assert contact(conn, cid)["status"] == "replied"


def test_concurrent_advance_refused(conn, settings):
    other = db.connect(settings.db_path)
    assert clock.acquire_run_lock(other, "someone-else")
    with pytest.raises(clock.RunLocked, match="another run is in progress"):
        runner.advance(conn, settings, 1, llm=None)
    clock.release_run_lock(other, "someone-else")
    runner.advance(conn, settings, 1, llm=None)


def test_clock_rejects_backwards(conn):
    with pytest.raises(clock.ClockError):
        clock.set_now(conn, timeutil.add(clock.now(conn), minutes=-1))


def test_cli_before_seed_exits_1(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--db", str(tmp_path / "missing.db"), "run"])
    assert e.value.code == 1
    assert "python -m coldemailer seed" in capsys.readouterr().err
