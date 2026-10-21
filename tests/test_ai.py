import json
from dataclasses import replace

import httpx
import pytest

from coldemailer.ai import classify, templates
from coldemailer.ai.openrouter import LLMClient, TokenBucket
from coldemailer.sim_corpus import INTERESTED, NOT_INTERESTED, OOO, UNSUBSCRIBE, reply_body
from coldemailer.lifecycle import rng

from conftest import make_contact


class FakeTime:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def now(self):
        return self.t


def ok(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def client(conn, settings, handler, models=("m1:free", "m2:free")):
    s = replace(settings, openrouter_api_key="test", openrouter_models=models)
    ft = FakeTime()
    return LLMClient(conn, s, transport=httpx.MockTransport(handler), sleep=ft.sleep, now=ft.now), ft


def calls(conn):
    return [tuple(r) for r in conn.execute("SELECT model, status FROM llm_calls ORDER BY id")]


def test_429_retries_then_succeeds(conn, settings):
    responses = [httpx.Response(429, headers={"Retry-After": "2"}), ok("hello")]
    c, ft = client(conn, settings, lambda req: responses.pop(0))
    assert c.chat([{"role": "user", "content": "hi"}], "template") == ("hello", "m1:free")
    assert calls(conn) == [("m1:free", "rate_limited"), ("m1:free", "ok")]
    assert 2.0 in ft.sleeps


def test_model_fallback_chain(conn, settings):
    def handler(req):
        model = json.loads(req.content)["model"]
        return httpx.Response(429) if model == "m1:free" else ok("from m2")
    c, _ = client(conn, settings, handler)
    assert c.chat([{"role": "user", "content": "hi"}], "classify") == ("from m2", "m2:free")
    assert calls(conn) == [("m1:free", "rate_limited")] * 3 + [("m2:free", "ok")]


def test_cooldown_skips_failed_model_on_next_call(conn, settings):
    seen = []

    def handler(req):
        model = json.loads(req.content)["model"]
        seen.append(model)
        return httpx.Response(429) if model == "m1:free" else ok("x")
    c, _ = client(conn, settings, handler)
    c.chat([{"role": "user", "content": "1"}], "classify")
    seen.clear()
    c.chat([{"role": "user", "content": "2"}], "classify")
    assert seen == ["m2:free"]


def test_all_models_fail_returns_none_and_logs_fallback(conn, settings):
    c, _ = client(conn, settings, lambda req: httpx.Response(500))
    assert c.chat([{"role": "user", "content": "hi"}], "template") is None
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='ai_fallback'").fetchone()[0] == 1
    assert len(calls(conn)) == 6


def test_unknown_model_is_not_retried(conn, settings):
    c, _ = client(conn, settings, lambda req: httpx.Response(404), models=("gone:free",))
    assert c.chat([{"role": "user", "content": "hi"}], "template") is None
    assert calls(conn) == [("gone:free", "error")]


def test_token_bucket_limits_rate():
    ft = FakeTime()
    bucket = TokenBucket(15, ft.sleep, ft.now)
    for _ in range(20):
        bucket.acquire()
    assert sum(ft.sleeps) == pytest.approx(20.0)  # 5 extra requests at 15/min = 4s each


@pytest.mark.parametrize("pool,label", [(OOO, "out_of_office"), (UNSUBSCRIBE, "unsubscribe"),
                                        (INTERESTED, "interested"), (NOT_INTERESTED, "not_interested")])
def test_rules_classifier_on_corpus(pool, label):
    for text in pool:
        body = text.replace("{until}", " until November 3").replace("{first_name}", "Priya")
        assert classify.rules(body)[0] == label, body


def test_rules_leave_ambiguous_replies_alone():
    for body in ("Maybe check back after the holidays?", "I'm not the right person for this but our COO might be."):
        assert classify.rules(body) == (None, 0.0)


@pytest.mark.parametrize("body,expected", [
    ("I'm out of office until November 3.", "2026-11-03"),
    ("Automatic reply - on leave and back on November 3.", "2026-11-03"),
    ("OOO: on leave until 3 Nov", "2026-11-03"),
    ("Out of office until 2026-11-03.", "2026-11-03"),
    ("Out of office, limited email.", None),
])
def test_parse_return_date(body, expected):
    assert classify.parse_return_date(body, "2026-10-20T10:00:00Z") == expected


def test_corpus_ooo_dates_round_trip():
    r = rng("t")
    for _ in range(50):
        body = reply_body("out_of_office", r, "Priya", "2026-11-03")
        assert classify.parse_return_date(body, "2026-10-20T10:00:00Z") in ("2026-11-03", None)
        assert classify.rules(body)[0] == "out_of_office"


class FakeLLM:
    def __init__(self, labels):
        self.labels, self.calls = labels, 0

    def chat(self, messages, purpose, **kw):
        self.calls += 1
        items = json.loads(messages[-1]["content"])
        return json.dumps([{"id": i["id"], "label": self.labels(i)} for i in items]), "m2:free"


def add_replies(conn, n, body="Possibly. What would you need from our side?"):
    ids = []
    for _ in range(n):
        cid = make_contact(conn, status="replied", next_action=None, next_action_at=None)
        ids.append(conn.execute("INSERT INTO replies(contact_id, body, received_at) VALUES (?,?,?)",
                                (cid, body, "2026-10-01T05:00:00Z")).lastrowid)
    return ids


def test_backlog_batches_and_unsure_goes_to_review(conn, settings):
    ids = add_replies(conn, 10)
    llm = FakeLLM(lambda item: "unsure" if item["id"] == ids[0] else "interested")
    assert classify.backlog(conn, settings, llm) == 9
    assert llm.calls == 1
    by = dict(conn.execute("SELECT classified_by, COUNT(*) FROM replies GROUP BY 1").fetchall())
    assert by == {"llm:m2:free": 9, None: 1}
    classify.backlog(conn, settings, llm)  # second time unsure -> a human decides
    item = conn.execute("SELECT * FROM review_items").fetchone()
    assert item["kind"] == "classification" and item["reply_id"] == ids[0]


def test_llm_unsubscribe_label_upgrades_and_suppresses(conn, settings):
    rid = add_replies(conn, 1, "Please take this thread elsewhere.")[0]
    classify.backlog(conn, settings, FakeLLM(lambda item: "unsubscribe"))
    c = conn.execute("SELECT c.* FROM contacts c JOIN replies r ON r.contact_id=c.id WHERE r.id=?", (rid,)).fetchone()
    assert c["status"] == "unsubscribed"
    assert conn.execute("SELECT reason FROM suppression WHERE email=?", (c["email"],)).fetchone()[0] == "unsubscribe_reply"


def test_no_key_uses_fallback(conn, settings):
    add_replies(conn, 3)
    classify.backlog(conn, settings, None)
    assert {r[0] for r in conn.execute("SELECT classified_by FROM replies")} == {"fallback"}


class TemplateLLM:
    def __init__(self, body):
        self.body = body

    def chat(self, messages, purpose, **kw):
        return json.dumps({"subject": "Quick idea for {company}", "body": self.body}), "m1:free"


def test_warm_stores_valid_templates(conn):
    body = "Hi {first_name},\n\nShort note for {role}s at {company} about {industry_topic}.\n\n{sender_name}\nNorthbeam" \
           "\n\nNot relevant? Unsubscribe here: {unsubscribe_url}"
    out = templates.warm(conn, TemplateLLM(body), "2026-10-01T04:30:00Z")
    assert out["generated"] == 40
    t = templates.pick(conn, "Fintech", 2, "B")
    assert t["source"] == "llm:m1:free"


def test_warm_rejects_invalid_template(conn):
    out = templates.warm(conn, TemplateLLM("Hi {first_name}, ask {ceo_name}. Unsubscribe: {unsubscribe_url}"),
                         "2026-10-01T04:30:00Z")
    assert out["generated"] == 0 and out["failed"] == 40
    assert templates.pick(conn, "SaaS", 1, "A")["source"] == "static"
