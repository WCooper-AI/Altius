"""Break-the-app round 3 (R3-2): far-off test dates get a plain 422, not a crash."""

import pytest
from fastapi.testclient import TestClient

from tradealerts.sample import synthetic_bars
from tradealerts.web import create_app

from .test_web import CSV_STRATEGY, H


@pytest.fixture
def client(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    synthetic_bars(days=60, freq="1h", seed=4).to_csv(tmp_path / "data" / "bars.csv")
    (tmp_path / "config.yaml").write_text("notifiers:\n  - type: console\n")
    return TestClient(create_app(tmp_path / "config.yaml"))


def backtest(client, **dates):
    return client.post("/api/backtest", json={"data": CSV_STRATEGY, **dates}, headers=H)


@pytest.mark.parametrize("dates", [
    {"start": "0001-01-01"},
    {"start": "1600-01-01"},
    {"start": "1899-12-31"},
    {"end": "9999-12-31"},
    {"start": "2020-01-01", "end": "2262-04-12"},
])
def test_far_off_dates_are_refused(client, dates):
    r = backtest(client, **dates)
    assert r.status_code == 422
    assert r.json()["detail"] == "Dates must be between 1900 and 2200"


def test_bad_date_text_keeps_its_message(client):
    r = backtest(client, start="not a date")
    assert r.status_code == 422 and "2025-01-31" in r.json()["detail"]


def test_edge_and_normal_dates_still_run(client):
    assert backtest(client, start="1900-01-01").status_code == 200
    assert backtest(client, start="2020-01-01", end="2200-12-31").status_code == 200
