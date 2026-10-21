"""Shared fixtures. The mock provider's random outcomes are switched off (all rates 0) so tests
decide exactly what happens: `force_results` scripts the provider's answers and `inject` drops a
reply / unsubscribe / complaint into the simulated inbox."""
from __future__ import annotations

import itertools
import json

import pytest

from coldemailer import clock, db, provider_mock, runner, timeutil
from coldemailer.ai import templates
from coldemailer.config import load_settings

START = "2026-10-01T04:30:00Z"  # 10:00 IST


@pytest.fixture
def settings(tmp_path):
    return load_settings(
        db_path=str(tmp_path / "test.db"), openrouter_api_key=None, openrouter_models=(),
        hard_bounce_rate=0.0, soft_bounce_rate=0.0, complaint_rate=0.0, unsub_click_rate=0.0,
        reply_rates=(0.0, 0.0, 0.0, 0.0), poor_reputation_mailboxes=(), mailbox_count=2)


def make_db(settings, mailboxes: int = 2, warm_days: int = 30, limit: int = 200):
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    clock.init(conn, START)
    conn.execute("INSERT INTO campaign(id, name, waits_days, window_start, window_end, domain_daily_cap, created_at) "
                 "VALUES (1, 'test', ?, '09:00', '18:00', ?, ?)",
                 (json.dumps(list(settings.waits_days)), settings.domain_daily_cap, START))
    for i in range(1, mailboxes + 1):
        conn.execute("INSERT INTO mailboxes(id, address, warmup_started_on, start_limit, daily_increment, max_limit) "
                     "VALUES (?,?,?,?,?,?)", (i, f"sales{i}@mail-a.test",
                                              timeutil.shift_day(timeutil.ist_day(START), -warm_days), limit, 0, limit))
    templates.ensure_static(conn, START)
    return conn


@pytest.fixture
def conn(settings):
    c = make_db(settings)
    yield c
    c.close()


_ids = itertools.count(1)


def make_contact(conn, **fields) -> int:
    n = next(_ids)
    row = {"email": f"person{n}@example{n % 7}.com", "first_name": "Priya", "last_name": f"Rao{n}",
           "company": "Acme", "domain": f"example{n % 7}.com", "role": "VP Sales", "industry": "SaaS",
           "segment": "SaaS", "variant": "A", "timezone": "Asia/Kolkata", "status": "new", "step": 0,
           "next_action": "send_1", "next_action_at": clock.now(conn), "created_at": START, "updated_at": START}
    row.update(fields)
    cols = ", ".join(row)
    return conn.execute(f"INSERT INTO contacts({cols}) VALUES ({', '.join('?' * len(row))})",
                        tuple(row.values())).lastrowid


def tick(conn, settings, hours: float = 1):
    return runner.advance(conn, settings, hours, llm=None)


def inject(conn, contact_id: int, kind: str, at: str, body: str | None = None, label: str | None = None,
           code: str | None = None) -> int:
    send = conn.execute("SELECT id, idempotency_key FROM sends WHERE contact_id=? ORDER BY step DESC LIMIT 1",
                        (contact_id,)).fetchone()
    return conn.execute(
        "INSERT INTO sim_events(idempotency_key, send_id, contact_id, kind, smtp_code, body, true_label, deliver_at) "
        "VALUES (?,?,?,?,?,?,?,?)", (send["idempotency_key"] if send else "test", send["id"] if send else None,
                                     contact_id, kind, code, body, label, at)).lastrowid


@pytest.fixture
def force_results(monkeypatch):
    """force_results([SendResult(...), ...]) — the provider answers these in order, then accepts."""
    def install(results):
        queue = list(results)
        real = provider_mock.MockProvider.send

        def send(self, key, send_id, contact_id, step, at, *args, **kwargs):
            if not queue:
                return real(self, key, send_id, contact_id, step, at, *args, **kwargs)
            r = queue.pop(0)
            if r.kind == "accepted":
                self._insert(key, send_id, contact_id, "accepted", at, processed_at=at)
            else:
                self._insert(key, send_id, contact_id, r.kind, timeutil.add(at, minutes=1), r.smtp_code, r.message)
            return r
        monkeypatch.setattr(provider_mock.MockProvider, "send", send)
    return install


def sends(conn, contact_id: int):
    return conn.execute("SELECT * FROM sends WHERE contact_id=? ORDER BY step", (contact_id,)).fetchall()


def contact(conn, contact_id: int):
    return conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
