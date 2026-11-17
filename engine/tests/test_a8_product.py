"""A8: the built-in sample market, the sample strategy, and the five-stage status
(Research → Backtest → Validate → Paper trade → Monitor) with the test records behind it."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradealerts import dataquality, instruments, stages
from tradealerts import data as data_mod
from tradealerts.recipes import MARKETS_BY_KEY, build_strategy, example_settings
from tradealerts.sample import sample_bars
from tradealerts.web import create_app

H = {"X-Requested-With": "tradealerts"}


# ------------------------------------------------------------------ sample market


def test_sample_series_is_fixed_and_only_grows():
    early, later = sample_bars("2025-03-01"), sample_bars("2025-06-01")
    assert len(later) > len(early) > 0
    assert later.loc[:early.index[-1]].equals(early)  # the past never changes
    bar = pd.Timedelta("15min")
    assert early.index[-1] + bar <= pd.Timestamp("2025-03-01", tz="UTC")  # only candles that had closed
    assert sample_bars().index[-1] + bar <= pd.Timestamp.now(tz="UTC")


def test_sample_series_is_shut_at_the_weekend_like_the_index_markets():
    ny = sample_bars("2025-02-01").index.tz_convert("America/New_York")
    dow, hour = ny.day_of_week, ny.hour
    assert not (((dow == 4) & (hour >= 17)) | (dow == 5) | ((dow == 6) & (hour < 18))).any()
    assert ((dow == 6) & (hour >= 18)).any()  # Sunday evening New York time is open


@pytest.mark.parametrize("source", ["dukascopy", "yahoo", "oanda"])
def test_sample_market_never_downloads(source):
    prov, sym = data_mod.MarketProvider({"source": source}).resolve("sample")
    assert isinstance(prov, data_mod.SampleProvider) and sym == "sample"
    bars, fine = data_mod.MarketProvider({"source": source}).fetch_bars("sample", "1h", 30)
    assert len(bars) > 100 and fine is not None
    assert data_mod.data_notes("sample", "auto", {"source": source}) == [data_mod.SAMPLE_NOTE]


def test_sample_market_is_in_the_catalogue_with_a_full_record():
    m = MARKETS_BY_KEY["sample"]
    assert m["sample"] and m["dukascopy"] is None and m["oanda"] is None and m["news"] == []
    i = instruments.get("sample")
    assert i.hours and i.quote == "USD" and i.point_value == 1
    data = build_strategy("trend", "sample", example_settings("trend"))
    assert data["market"]["symbol"] == "sample" and data["market"]["provider"] == "auto"


def test_sample_data_has_no_gaps_on_weekdays():
    df = data_mod.resample_ohlcv(sample_bars("2025-06-01"), "1h")
    kinds = {x["kind"] for x in dataquality.check(df, "1h", now=df.index[-1] + pd.Timedelta("1h"))}
    assert not kinds & {"gap", "missing_bars", "disordered", "stale"}


# ------------------------------------------------------------------ stages


def rec(kind, at, rules_hash="abc"):
    return {"kind": kind, "at": at, "rules_hash": rules_hash, "count": 1}


def step(block, key):
    return next(s for s in block["steps"] if s["key"] == key)


def test_new_strategy_is_at_backtest():
    b = stages.status({"template": True}, [], rules_hash="abc")
    assert [s["key"] for s in b["steps"]] == list(stages.ORDER)
    assert b["current"] == "backtest" and b["done"] == 1 and not b["complete"]
    assert "backtest" in b["next"]["text"].lower()


def test_each_stage_moves_the_next_step_on():
    tests = [rec("backtest", "2026-11-01T10:00:00")]
    assert stages.status({"template": True}, tests, rules_hash="abc")["current"] == "validate"
    tests.append(rec("luck", "2026-11-01T11:00:00"))
    b = stages.status({"template": True}, tests, rules_hash="abc")
    assert b["current"] == "paper" and step(b, "validate")["detail"] == "Luck check"
    run = {"started_at": "2026-11-02T09:00:00"}
    b = stages.status({"template": True}, tests, run, rules_hash="abc")
    assert b["current"] == "monitor" and step(b, "paper")["at"] == run["started_at"]
    b = stages.status({"template": False, "enabled": True}, tests, run, rules_hash="abc")
    assert b["complete"] and b["done"] == 5 and b["next"]["key"] is None


def test_split_counts_as_backtest_and_validation():
    b = stages.status({}, [rec("split", "2026-11-01T10:00:00")], rules_hash="abc")
    assert step(b, "backtest")["state"] == "done" and step(b, "validate")["state"] == "done"
    assert stages.test_kinds({"split_pct": 70}) == ["backtest", "split"]
    assert stages.test_kinds({}) == ["backtest"]


def test_a_settings_comparison_is_not_a_robustness_check():
    b = stages.status({}, [rec("backtest", "2026-11-01"), rec("sweep", "2026-11-02")], rules_hash="abc")
    assert b["current"] == "validate"


def test_tests_on_other_rules_are_marked_changed():
    b = stages.status({}, [rec("backtest", "2026-11-01", "old")], rules_hash="new")
    s = step(b, "backtest")
    assert s["state"] == "done" and s["changed"] and "changed" in s["detail"].lower()


def test_broken_file_stays_at_research():
    b = stages.status({}, [], error="market.symbol is missing")
    assert b["current"] == "research" and "market.symbol is missing" in step(b, "research")["detail"]


def test_monitor_without_a_channel_points_to_settings():
    tests = [rec("backtest", "2026-11-01"), rec("optimise", "2026-11-02")]
    run = {"started_at": "2026-11-03"}
    assert "Settings" in stages.status({}, tests, run, channels=False)["next"]["text"]
    assert "Settings" not in stages.status({}, tests, run, channels=True)["next"]["text"]


def test_earlier_forward_tests_count_as_paper_trading():
    b = stages.status({}, [], earlier_runs=2)
    assert step(b, "paper")["state"] == "done" and "2 earlier" in step(b, "paper")["detail"]


def test_paused_alerts_are_not_monitoring():
    assert not stages.alerts_on({"template": False, "enabled": False})
    assert not stages.alerts_on({"template": True})
    assert stages.alerts_on({"template": False})


def test_stage_wording_never_judges_results():
    words = " ".join(stages.NEXT.values()).lower() + " ".join(stages.LABELS.values()).lower()
    for bad in ("good", "bad", "broken", "degraded", "profitable", "recommend", "score"):
        assert bad not in words


# ------------------------------------------------------------------ API


@pytest.fixture
def client(tmp_path):
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\nnotifiers:\n  - type: console\n")
    return TestClient(create_app(tmp_path / "config.yaml"))


def make_sample(client):
    r = client.post("/api/strategies/sample", headers=H)
    assert r.status_code == 201
    return r.json()["id"]


def test_sample_strategy_is_a_ready_to_test_draft(client):
    sid = make_sample(client)
    rec_ = client.get(f"/api/strategies/{sid}").json()
    assert rec_["error"] is None and rec_["data"]["template"] is True
    assert rec_["data"]["market"]["symbol"] == "sample" and rec_["data"]["recipe"]["id"] == "trend"
    assert rec_["stages"]["current"] == "backtest"
    assert client.post("/api/strategies/sample", headers=H).json()["id"] != sid  # a second one gets its own file


def test_tests_on_a_saved_strategy_move_its_stage(client):
    sid = make_sample(client)
    data = client.get(f"/api/strategies/{sid}").json()["data"]
    r = client.post("/api/backtest", headers=H, json={"data": data, "strategy_id": sid, "years": 1})
    assert r.status_code == 200, r.text
    assert data_mod.SAMPLE_NOTE in r.json()["warnings"]  # every result on it says it's made up
    assert client.get(f"/api/strategies/{sid}").json()["stages"]["current"] == "validate"
    r = client.post("/api/backtest", headers=H, json={"data": data, "strategy_id": sid, "years": 1, "split_pct": 70})
    assert r.status_code == 200, r.text
    block = client.get(f"/api/strategies/{sid}").json()["stages"]
    assert block["current"] == "paper" and step(block, "validate")["detail"] == "Train/test split"
    listed = next(x for x in client.get("/api/strategies").json()["strategies"] if x["id"] == sid)
    assert listed["stages"]["current"] == "paper"


def test_forward_test_and_alerts_reach_monitor(client):
    sid = make_sample(client)
    data = client.get(f"/api/strategies/{sid}").json()["data"]
    client.post("/api/backtest", headers=H, json={"data": data, "strategy_id": sid, "years": 1, "split_pct": 70})
    assert client.post(f"/api/strategies/{sid}/forward-test/start", headers=H).status_code == 200
    block = client.get(f"/api/strategies/{sid}").json()["stages"]
    assert block["current"] == "monitor" and "Settings" in block["next"]["text"]  # console only: no channel yet
    r = client.put(f"/api/strategies/{sid}", headers=H, json={"data": {**data, "template": False, "enabled": True},
                                                               "on_running": "keep"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/strategies/{sid}").json()["stages"]["complete"]


def test_unsaved_or_unknown_strategies_leave_no_record(client, tmp_path):
    sid = make_sample(client)
    data = client.get(f"/api/strategies/{sid}").json()["data"]
    client.post("/api/backtest", headers=H, json={"data": data, "years": 1})
    client.post("/api/backtest", headers=H, json={"data": data, "strategy_id": "no-such-strategy", "years": 1})
    client.post("/api/backtest", headers=H, json={"data": data, "strategy_id": "../evil", "years": 1})
    assert client.get(f"/api/strategies/{sid}").json()["stages"]["current"] == "backtest"
    from tradealerts.state import StateStore
    store = StateStore(tmp_path / "data" / "state.db")
    try:
        assert store.all_tests() == {}
    finally:
        store.close()


def test_deleting_a_strategy_forgets_its_tests(client, tmp_path):
    sid = make_sample(client)
    data = client.get(f"/api/strategies/{sid}").json()["data"]
    client.post("/api/backtest", headers=H, json={"data": data, "strategy_id": sid, "years": 1})
    assert client.delete(f"/api/strategies/{sid}", headers=H).status_code == 200
    from tradealerts.state import StateStore
    store = StateStore(tmp_path / "data" / "state.db")
    try:
        assert store.tests(sid) == []
    finally:
        store.close()


def test_the_store_counts_repeat_tests():
    from tradealerts.state import StateStore
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        store = StateStore(Path(d) / "s.db")
        try:
            store.add_test("x", "luck", "a")
            store.add_test("x", "luck", "b")
            (row,) = store.tests("x")
            assert row["count"] == 2 and row["rules_hash"] == "b"
        finally:
            store.close()
