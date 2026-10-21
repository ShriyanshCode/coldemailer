import time

import pytest
from fastapi.testclient import TestClient

from coldemailer import clock, db, seed
from coldemailer.api import create_app
from coldemailer.config import load_settings


@pytest.fixture
def seeded(tmp_path):
    s = load_settings(db_path=str(tmp_path / "api.db"), openrouter_api_key=None, openrouter_models=())
    seed.run(s, n=300)
    return s


@pytest.fixture
def api(seeded):
    return TestClient(create_app(seeded))


def wait_for(api, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = api.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_overview_shape(api):
    d = api.get("/api/overview").json()
    for key in ("kpis", "statuses", "markers", "replies", "classifiers", "hours", "deferred", "due",
                "suppression", "health", "cycle", "campaign", "clock"):
        assert key in d
    assert sum(s["count"] for s in d["statuses"]) == 300 == d["kpis"]["total"]
    assert len(d["hours"]) == 24 and len(d["due"]) == 5


def test_contacts_filter_and_paging(api, seeded):
    d = api.get("/api/contacts", params={"status": "first_sent", "size": 25, "page": 0}).json()
    conn = db.connect(seeded.db_path)
    assert d["total"] == conn.execute("SELECT COUNT(*) FROM contacts WHERE status='first_sent'").fetchone()[0]
    assert d["rows"] and all(r["status"] == "first_sent" for r in d["rows"])
    page2 = api.get("/api/contacts", params={"size": 10, "page": 1}).json()
    assert [r["id"] for r in page2["rows"]] == list(range(11, 21))


def test_contact_detail_timeline(api, seeded):
    conn = db.connect(seeded.db_path)
    cid = conn.execute("SELECT id FROM contacts WHERE status='follow_up_1_sent' LIMIT 1").fetchone()[0]
    d = api.get(f"/api/contacts/{cid}").json()
    sent = [e for e in d["timeline"] if e["type"] == "sent"]
    assert len(sent) == 2 and all(e["body"] and e["fields"][0]["v"] for e in sent)
    assert api.get("/api/contacts/999999").status_code == 404


def test_search(api, seeded):
    conn = db.connect(seeded.db_path)
    email = conn.execute("SELECT email FROM contacts LIMIT 1").fetchone()[0]
    assert api.get("/api/search", params={"q": email[:6]}).json()[0]["email"].startswith(email[:6])


def test_advance_job_and_lock(api, seeded):
    job = api.post("/api/clock/advance", json={"hours": 2}).json()
    assert wait_for(api, job["job_id"])["status"] == "done"
    assert api.get("/api/clock").json()["advanced_hours"] == 2
    other = db.connect(seeded.db_path)
    assert clock.acquire_run_lock(other, "someone-else")
    r = api.post("/api/clock/advance", json={"hours": 1})
    assert r.status_code == 409 and "another run" in r.json()["detail"]
    clock.release_run_lock(other, "someone-else")


def test_events_paging(api):
    wait_for(api, api.post("/api/clock/advance", json={"hours": 24}).json()["job_id"])
    first = api.get("/api/events", params={"limit": 5}).json()
    assert len(first["rows"]) == 5 and first["next"]
    second = api.get("/api/events", params={"limit": 5, "before": first["next"]}).json()
    assert not {r["id"] for r in first["rows"]} & {r["id"] for r in second["rows"]}


def test_review_resolve_endpoint(api, seeded):
    conn = db.connect(seeded.db_path)
    cid = conn.execute("SELECT id FROM contacts WHERE status='first_sent' LIMIT 1").fetchone()[0]
    conn.execute("UPDATE contacts SET hold_reason='policy_rejection' WHERE id=?", (cid,))
    item = conn.execute("INSERT INTO review_items(contact_id, kind, reason, detail, created_at) "
                        "VALUES (?, 'policy', 'policy_rejection', '{}', '2026-10-01T03:30:00Z')", (cid,)).lastrowid
    assert api.post(f"/api/reviews/{item}/resolve", json={"action": "label:interested"}).status_code == 400
    assert api.post(f"/api/reviews/{item}/resolve", json={"action": "retry_now"}).status_code == 200
    assert api.post(f"/api/reviews/{item}/resolve", json={"action": "retry_now"}).status_code == 400
    assert conn.execute("SELECT hold_reason FROM contacts WHERE id=?", (cid,)).fetchone()[0] is None


def test_verify_endpoint(api):
    d = api.post("/api/verify").json()
    assert d["ok"] and len(d["checks"]) == 12
    assert api.get("/api/overview").json()["health"]["verify"]["ok"] is True


def test_other_reads(api):
    assert len(api.get("/api/mailboxes").json()) == 20
    assert api.get("/api/ai").json()["totals"]["calls"] == 0
    assert len(api.get("/api/templates").json()["cells"]) == 40
    assert api.get("/api/reviews", params={"state": "all"}).status_code == 200


def test_empty_db_returns_200(tmp_path):
    s = load_settings(db_path=str(tmp_path / "missing.db"), openrouter_api_key=None, openrouter_models=())
    api = TestClient(create_app(s))
    r = api.get("/api/overview")
    assert r.status_code == 200 and r.json()["initialised"] is False
    assert api.get("/api/contacts").json()["total"] == 0
    assert api.post("/api/clock/advance", json={"hours": 1}).status_code == 409


def test_contacts_due_and_ooo_filters(api, seeded):
    conn = db.connect(seeded.db_path)
    today_end = conn.execute("SELECT now FROM clock").fetchone()[0][:10] + "T18:30:00Z"  # IST midnight
    expected = conn.execute("SELECT COUNT(*) FROM contacts WHERE next_action IS NOT NULL AND hold_reason IS NULL "
                            "AND next_action_at < ?", (today_end,)).fetchone()[0]
    assert api.get("/api/contacts", params={"due": "today"}).json()["total"] == expected
    tomorrow = api.get("/api/contacts", params={"due": "tomorrow", "size": 200}).json()
    assert all(r["next_action_at"] >= today_end for r in tomorrow["rows"])
    assert api.get("/api/contacts", params={"ooo": "yes"}).json()["total"] == 0  # seed has no OOO pushes yet
