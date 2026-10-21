"""Regression tests for the findings of the whole-branch review."""
import json
from dataclasses import replace

import httpx
import pytest

from coldemailer import actions, clock, db, engine, report, runner, timeutil, verify
from coldemailer.ai import classify
from coldemailer.ai.openrouter import LLMClient
from coldemailer.provider_mock import SendResult

from conftest import contact, inject, make_contact, make_db, sends, tick


def crash_first_finalize(monkeypatch):
    real, calls = engine._finalize, []

    def crash_once(*a, **k):
        if not calls:
            calls.append(1)
            raise RuntimeError("crash after provider accepted")
        return real(*a, **k)
    monkeypatch.setattr(engine, "_finalize", crash_once)


def test_recovery_does_not_revive_a_contact_who_replied(conn, settings, monkeypatch):
    cid = make_contact(conn)
    tick(conn, settings)  # step 1 sent
    crash_first_finalize(monkeypatch)
    with pytest.raises(RuntimeError):
        for _ in range(24 * 5):
            tick(conn, settings)  # follow-up 1 comes due, is accepted, then finalize crashes
    inject(conn, cid, "reply", clock.now(conn), "Sounds interesting, let's set up a call.", "interested")
    runner.advance(conn, settings, 0.05, step_hours=0.05, llm=None)  # ingest before the lease expires
    tick(conn, settings, 24 * 12)
    c = contact(conn, cid)
    assert c["status"] == "replied" and c["next_action"] is None
    assert [r["step"] for r in sends(conn, cid) if r["state"] == "sent"] == [1, 2]  # 2 really went out; nothing after
    assert verify.run(conn, settings)["ok"]


def test_recovery_respects_operator_suppression(conn, settings, monkeypatch):
    cid = make_contact(conn)
    crash_first_finalize(monkeypatch)
    with pytest.raises(RuntimeError):
        tick(conn, settings)
    actions.suppress_contact(conn, cid)
    tick(conn, settings, 24 * 5)
    c = contact(conn, cid)
    assert c["status"] == "unsubscribed" and c["next_action"] is None
    assert verify.run(conn, settings)["ok"]


def test_deferred_once_per_contact_day_and_reason_even_when_reasons_alternate(settings):
    conn = make_db(settings, mailboxes=1)
    conn.execute("UPDATE mailboxes SET start_limit=40, max_limit=40")
    for i in range(80):
        make_contact(conn, timezone=["Asia/Kolkata", "America/New_York", "Europe/London"][i % 3])
    tick(conn, settings, 24 * 3)
    from collections import Counter
    seen = Counter((e["contact_id"], timeutil.ist_day(e["occurred_at"]), json.loads(e["detail"])["reason"])
                   for e in conn.execute("SELECT * FROM events WHERE type='deferred'"))
    assert seen and max(seen.values()) == 1
    assert len({r for _, _, r in seen}) > 1  # reasons really alternated


def test_resumed_mailbox_is_judged_on_new_sends_only(settings, force_results):
    s = replace(settings, autopause_window=50, autopause_min_sample=20)
    conn = make_db(s, mailboxes=1)
    bounce, ok = SendResult("hard_bounce", "550 5.1.1", "User unknown"), SendResult("accepted")
    force_results(([ok] * 3 + [bounce]) * 8)  # 25% bounces -> pause
    for _ in range(60):
        make_contact(conn)
    tick(conn, s, 8)
    assert conn.execute("SELECT paused FROM mailboxes").fetchone()[0] == 1
    force_results([])  # the operator fixed the cause: from now on the provider behaves
    actions.resume_mailbox(conn, 1)
    tick(conn, s, 24)  # clean sends now; the old bounces must not re-pause it
    assert conn.execute("SELECT paused FROM mailboxes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM send_attempts WHERE at > (SELECT occurred_at FROM events "
                        "WHERE type='mailbox_resumed')").fetchone()[0] > 0


def test_cycle_never_sleeps_on_429(conn, settings):
    s = replace(settings, openrouter_api_key="k", openrouter_models=("m1:free", "m2:free"))
    sleeps = []
    responses = {"m1:free": httpx.Response(429, headers={"Retry-After": "45"}),
                 "m2:free": httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})}
    c = LLMClient(conn, s, transport=httpx.MockTransport(lambda r: responses[json.loads(r.content)["model"]]),
                  sleep=sleeps.append, now=lambda: 0.0)
    assert c.chat([{"role": "user", "content": "x"}], "classify") == ("ok", "m2:free")
    assert sleeps == []  # the model went on cool-down for Retry-After; the cycle moved on
    assert c.cooldown["m1:free"] >= 45  # Retry-After honoured (the generic 60 s cool-down may be longer)


@pytest.mark.parametrize("body", [
    "I'm on leave this week but interested, let's chat Monday.",
    "Out of office until Friday. Not interested though, please don't follow up.",
])
def test_ooo_with_a_real_answer_stops_the_sequence(body):
    assert classify.rules(body)[0] != "out_of_office"


def test_reply_kpi_matches_replied_list(tmp_path):
    from coldemailer import seed
    from coldemailer.config import load_settings
    s = load_settings(db_path=str(tmp_path / "k.db"), openrouter_api_key=None, openrouter_models=())
    seed.run(s, n=600)
    conn = db.connect(s.db_path)
    runner.advance(conn, s, 24 * 5, llm=None)
    k = report.overview(conn, s)["kpis"]
    assert k["replied"] == report.contacts(conn, {"status": "replied"})["total"]


class DripStream(httpx.SyncByteStream):
    """OpenRouter-style keep-alive: whitespace forever, each chunk 'taking' 20 s of fake time."""
    def __init__(self, clock):
        self.clock = clock

    def __iter__(self):
        while True:
            self.clock["t"] += 20
            yield b" "


def test_hanging_call_is_cut_at_the_deadline_and_chain_moves_on(conn, settings):
    s = replace(settings, openrouter_api_key="k", openrouter_models=("slow:free", "m2:free"))
    fake = {"t": 0.0}

    def handler(req):
        if json.loads(req.content)["model"] == "slow:free":
            return httpx.Response(200, stream=DripStream(fake))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    c = LLMClient(conn, s, transport=httpx.MockTransport(handler), sleep=lambda x: None, now=lambda: fake["t"])
    assert c.chat([{"role": "user", "content": "x"}], "classify") == ("ok", "m2:free")
    statuses = [tuple(r) for r in conn.execute("SELECT model, status FROM llm_calls ORDER BY id")]
    assert statuses == [("slow:free", "error"), ("m2:free", "ok")]
