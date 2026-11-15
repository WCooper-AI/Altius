"""A6: background-job lifecycle, typed price-alert bodies, the /api/v1 prefix."""
import time

import pytest
from fastapi.testclient import TestClient

from tradealerts import jobs
from tradealerts.web import create_app

H = {"X-Requested-With": "tradealerts"}
ALERT_FIELDS = {"id", "market", "label", "direction", "price", "note", "created_at", "checked_to", "triggered_at",
                "triggered_price", "active", "repeat", "cooldown_minutes", "cross", "fired_count", "last_fired_at"}


def slow_steps(progress, n, delay):
    for i in range(n):
        progress.check()
        time.sleep(delay)
        progress.update(i + 1, n, f"step {i + 1}")
    return {"steps": n}


def boom(progress):
    raise ValueError("no good")


def wait(runner, owner, job, timeout=30):
    end = time.time() + timeout
    while runner.status(owner, job)["status"] in ("queued", "running") and time.time() < end:
        time.sleep(0.02)
    return runner.status(owner, job)


@pytest.fixture
def client(tmp_path):
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\nnotifiers:\n  - type: console\n")
    return TestClient(create_app(tmp_path / "config.yaml"))


# ------------------------------------------------------------------ jobs


def test_completed_job_reports_times_and_cpu():
    runner = jobs.InlineJobs()
    job = runner.start("me", slow_steps, 3, 0.02, kind="optimise")
    st = wait(runner, "me", job)
    assert st["status"] == "completed" and st["kind"] == "optimise" and st["result"] == {"steps": 3}
    assert st["created"].endswith("Z") and st["started"].endswith("Z") and st["finished"].endswith("Z")
    assert st["created"] <= st["started"] <= st["finished"]
    assert st["waited"] >= 0 and st["seconds"] >= 0.05 and isinstance(st["cpu_seconds"], float)
    assert "error" not in st


def test_failed_job_reports_the_error_and_its_type():
    runner = jobs.InlineJobs()
    st = wait(runner, "me", runner.start("me", boom))
    assert st["status"] == "failed" and st["error"] == "no good" and st["error_type"] == "ValueError"
    assert "result" not in st


def test_job_list_is_newest_first_without_results():
    runner = jobs.InlineJobs()
    first = runner.start("me", slow_steps, 1, 0)
    wait(runner, "me", first)
    second = runner.start("me", slow_steps, 1, 0)
    wait(runner, "me", second)
    runner.start("someone-else", slow_steps, 1, 0)
    listed = runner.list("me")
    assert [j["id"] for j in listed] == [second, first]
    assert all("result" not in j and j["status"] == "completed" for j in listed)


def test_queued_job_can_be_cancelled_before_it_starts():
    runner = jobs.PoolJobs(workers=1, max_queue=4)
    try:
        busy = runner.start("a", slow_steps, 400, 0.05)
        end = time.time() + 30
        while runner.status("a", busy)["status"] != "running" and time.time() < end:
            time.sleep(0.05)
        waiting = runner.start("b", slow_steps, 3, 0.01)
        assert runner.status("b", waiting)["status"] == "queued"
        assert runner.status("b", waiting)["started"] is None
        assert runner.cancel("b", waiting)
        runner.cancel("a", busy)
        st = wait(runner, "b", waiting)
        assert st["status"] == "cancelled" and st["started"] is None and st["done"] == 0
    finally:
        runner.close()


def test_runaway_job_fails_with_a_time_limit_message():
    runner = jobs.PoolJobs(workers=1, timeout=1.0)
    try:
        st = wait(runner, "me", runner.start("me", slow_steps, 2000, 0.02))
        assert st["status"] == "failed" and st["error_type"] == "TimedOut" and "limit" in st["error"]
    finally:
        runner.close()


def test_jobs_endpoint_lists_this_workspaces_runs(client):
    ctx = client.app.state.ctx
    ws = ctx.workspace(None)
    job = ctx.jobs.start(ws.owner, slow_steps, 1, 0, kind="optimise")
    wait(ctx.jobs, ws.owner, job)
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert [j["id"] for j in r.json()["jobs"]] == [job] and r.json()["jobs"][0]["kind"] == "optimise"
    assert client.get(f"/api/jobs/{job}").json()["status"] == "completed"


# ------------------------------------------------------------------ price alerts + API version


def test_price_alert_bodies_are_checked_with_plain_messages(client):
    r = client.post("/api/price-alerts", json={"market": "gold"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Choose above or below; Fill in the price"
    r = client.post("/api/price-alerts", json={"market": "nope", "direction": "above", "price": 1}, headers=H)
    assert r.json()["detail"] == "Choose a market"
    for price in (True, "2700", -1, 0):
        r = client.post("/api/price-alerts", json={"market": "gold", "direction": "above", "price": price}, headers=H)
        assert r.status_code == 422 and r.json()["detail"] == "Fill in the price", price
    r = client.post("/api/price-alerts", json=[1, 2], headers=H)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_price_alert_responses_follow_the_model(client):
    r = client.post("/api/price-alerts", json={"market": "gold", "direction": "above", "price": 2700,
                                               "note": "x" * 300, "unknown": 1}, headers=H)
    assert r.status_code == 201, r.text
    a = r.json()
    assert set(a) == ALERT_FIELDS and len(a["note"]) == 200 and a["cross"] == 0 and a["active"] == 1
    r = client.put(f"/api/price-alerts/{a['id']}", json={"direction": "below", "price": 2600, "active": False},
                   headers=H)
    assert r.status_code == 200 and r.json()["active"] == 0 and r.json()["direction"] == "below"
    assert client.put("/api/price-alerts/999", json={"direction": "below", "price": 1}, headers=H).status_code == 404
    listed = client.get("/api/price-alerts").json()
    assert set(listed) == {"alerts", "poll_minutes"} and listed["poll_minutes"] == 15
    assert client.delete(f"/api/price-alerts/{a['id']}", headers=H).json() == {"deleted": a["id"]}


def test_v1_prefix_is_the_same_api(client):
    r = client.get("/api/v1/price-alerts")
    assert r.status_code == 200 and r.headers["API-Version"] == "1"
    assert client.get("/api/price-alerts").headers["API-Version"] == "1"
    r = client.post("/api/v1/price-alerts", json={"market": "gold", "direction": "above", "price": 2700}, headers=H)
    assert r.status_code == 201
    assert client.post("/api/v1/price-alerts", json={"market": "gold", "direction": "above", "price": 1}).status_code == 403
    assert "API-Version" not in client.get("/").headers
