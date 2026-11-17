"""Session B0 (full code audit): regression tests for the defects fixed in this session.

- web/common.py: ``raw_from_settings`` crashed with an unhandled ``TypeError`` (-> 500) instead
  of a plain 422 when a PUT to /api/settings left out the report's drawdown/near-level
  percentages (``number(..., required=False)`` can return ``None``, which was compared with
  ``>= 100`` before a default was applied).
- state.py: ``StateStore.add_equity`` never called ``self.db.commit()``, unlike every other
  write method on the class. A row inserted by ``add_equity`` was silently lost if the
  connection was closed (e.g. ``Workspace.store()``'s context manager) before some later,
  unrelated call happened to commit on its behalf.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradealerts.state import StateStore
from tradealerts.web import create_app

H = {"X-Requested-With": "tradealerts"}


@pytest.fixture
def client(tmp_path):
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\nnotifiers:\n  - type: console\n")
    return TestClient(create_app(tmp_path / "config.yaml"))


# --------------------------------------------------------------------------- PUT /api/settings


def test_settings_report_without_drawdown_or_near_pct_does_not_crash(client):
    """Before the fix this raised a bare TypeError (None >= 100) instead of returning a response."""
    body = {"poll_minutes": 15, "notifiers": {}, "report": {"day": "sun", "hour": 18}}
    r = client.put("/api/settings", json=body, headers=H)
    assert r.status_code == 200, r.text
    rep = r.json()["report"]
    assert rep["drawdown_pct"] == 0 and rep["near_pct"] == 0


def test_settings_report_with_null_drawdown_or_near_pct_does_not_crash(client):
    body = {"poll_minutes": 15, "notifiers": {},
            "report": {"day": "sun", "hour": 18, "drawdown_pct": None, "near_pct": None}}
    r = client.put("/api/settings", json=body, headers=H)
    assert r.status_code == 200, r.text
    rep = r.json()["report"]
    assert rep["drawdown_pct"] == 0 and rep["near_pct"] == 0


def test_settings_report_still_rejects_a_limit_at_or_over_100(client):
    body = {"poll_minutes": 15, "notifiers": {}, "report": {"day": "sun", "hour": 18, "drawdown_pct": 100}}
    r = client.put("/api/settings", json=body, headers=H)
    assert r.status_code == 422
    assert "100" in r.json()["detail"]


# --------------------------------------------------------------------------- StateStore.add_equity


def test_add_equity_persists_even_without_a_later_commit(tmp_path):
    """add_equity must commit its own row, like every sibling write method, so a row isn't
    silently dropped when the connection closes before anything else happens to commit."""
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    run = store.start_run("strat-1", name="Strat", market="Gold", timeframe="1h", currency="GBP",
                          start_balance=1000.0)
    store.add_equity(run["id"], "2025-01-01T00:00:00+00:00", 1010.0)
    store.close()  # no explicit store.commit() call in between

    reopened = StateStore(db_path)
    try:
        assert reopened.equity(run["id"]) == [("2025-01-01T00:00:00+00:00", 1010.0)]
    finally:
        reopened.close()


def test_add_equity_keeps_earlier_points_for_the_same_run(tmp_path):
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    run = store.start_run("strat-1", name="Strat", market="Gold", timeframe="1h", currency="GBP",
                          start_balance=1000.0)
    store.add_equity(run["id"], "2025-01-01T00:00:00+00:00", 1010.0)
    store.add_equity(run["id"], "2025-01-01T01:00:00+00:00", 1020.0)
    store.close()

    reopened = StateStore(db_path)
    try:
        assert reopened.equity(run["id"]) == [
            ("2025-01-01T00:00:00+00:00", 1010.0),
            ("2025-01-01T01:00:00+00:00", 1020.0),
        ]
    finally:
        reopened.close()
