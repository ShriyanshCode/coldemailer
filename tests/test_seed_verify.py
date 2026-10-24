import hashlib
import sqlite3
from datetime import date

import pytest

from coldemailer import db, runner, seed, timeutil, verify
from coldemailer.config import load_settings


def real_settings(tmp_path, name="seed.db"):
    return load_settings(db_path=str(tmp_path / name), openrouter_api_key=None, openrouter_models=())


def table_hash(path: str) -> str:
    c = sqlite3.connect(path)
    h = hashlib.sha256()
    for t in ("contacts", "sends", "events", "replies", "suppression"):
        for row in c.execute(f"SELECT * FROM {t} ORDER BY rowid"):
            h.update(repr(row).encode())
    c.close()
    return h.hexdigest()


def test_seed_deterministic(tmp_path):
    a, b = real_settings(tmp_path, "a.db"), real_settings(tmp_path, "b.db")
    seed.run(a, n=500, seed=42)
    seed.run(b, n=500, seed=42)
    assert table_hash(a.db_path) == table_hash(b.db_path)


def test_seed_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COLDEMAILER_SEED", "7")
    assert real_settings(tmp_path).seed == 7
    monkeypatch.setenv("COLDEMAILER_SEED", "")
    assert real_settings(tmp_path).seed == 42


def test_seed_cli_uses_env_seed_unless_flag_given(tmp_path, monkeypatch):
    from coldemailer import cli
    calls = []
    monkeypatch.setattr(seed, "run", lambda s, n, seed: calls.append(seed) or
                        {"contacts": 0, "sends": 0, "events": 0, "seconds": 0, "counts": {}})
    monkeypatch.setenv("COLDEMAILER_SEED", "7")
    for extra in ([], ["--seed", "3"]):
        with pytest.raises(SystemExit):
            cli.main(["--db", str(tmp_path / "x.db"), "seed", *extra])
    assert calls == [7, 3]


def test_seed_counts_match_mix(tmp_path):
    s = real_settings(tmp_path)
    counts = seed.run(s, n=2000)["counts"]
    for status, pct in seed.STATUS_MIX:
        assert abs(counts[status] / 2000 * 100 - pct) <= 2, status


def test_seed_history_consistent(tmp_path):
    s = real_settings(tmp_path)
    seed.run(s, n=2000)
    conn = db.connect(s.db_path)
    for c in conn.execute("SELECT id, timezone FROM contacts WHERE status='follow_up_2_sent'"):
        rows = conn.execute("SELECT step, sent_at FROM sends WHERE contact_id=? ORDER BY step", (c["id"],)).fetchall()
        assert [r["step"] for r in rows] == [1, 2, 3]
        d = [timeutil.local(r["sent_at"], c["timezone"]).date() for r in rows]
        assert (d[1] - d[0]).days >= 3 and (d[2] - d[1]).days >= 5
        assert all(r["sent_at"] < "2026-10-01T03:30:00Z" for r in rows)


def test_verify_passes_after_seed_and_advance(tmp_path):
    s = real_settings(tmp_path)
    seed.run(s, n=300)
    conn = db.connect(s.db_path)
    assert verify.run(conn, s)["ok"]
    runner.advance(conn, s, 24 * 14, llm=None)
    result = verify.run(conn, s)
    assert result["ok"], [c for c in result["checks"] if not c["ok"]]


def test_verify_catches_drift(tmp_path):
    s = real_settings(tmp_path)
    seed.run(s, n=300)
    conn = db.connect(s.db_path)
    cid = conn.execute("SELECT id FROM contacts WHERE status='first_sent' LIMIT 1").fetchone()[0]
    conn.execute("UPDATE contacts SET step=3 WHERE id=?", (cid,))
    result = verify.run(conn, s)
    assert not result["ok"]
    failed = [c for c in result["checks"] if not c["ok"]]
    assert [c["name"] for c in failed] == ["Step equals emails actually sent, and fits the status"]
    assert str(cid) in failed[0]["detail"]
