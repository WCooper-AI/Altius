"""Session B8: reproducibility manifest, strategies compared on the same footing, portfolio risk,
and the journal's market / strategy breakdown."""

import copy
import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradealerts import analysis, indicators, manifest, sidebyside
from tradealerts.sample import synthetic_bars
from tradealerts.state import StateStore
from tradealerts.strategy import strategy_from_dict
from tradealerts.web import create_app

from .test_web import CSV_STRATEGY, H


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    synthetic_bars(days=120, freq="1h", seed=4).to_csv(tmp_path / "data" / "bars.csv")
    synthetic_bars(days=120, freq="1h", seed=9).to_csv(tmp_path / "data" / "other.csv")
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\nnotifiers:\n  - type: console\n")
    return tmp_path


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws / "config.yaml"))


def _short(name="CSV short", **over):
    d = copy.deepcopy(CSV_STRATEGY)
    d.update({"name": name, "direction": "short"})
    d["entry"]["all"][0]["op"] = "crosses_below"
    d["entry"]["all"][0]["right"] = 65
    d.update(over)
    return d


def _save(client, data) -> str:
    r = client.post("/api/strategies", json={"data": data}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ------------------------------------------------------------------ reproducibility manifest


def test_indicator_versions_follow_the_code_actually_used(monkeypatch):
    v = {n: manifest.indicator_version(n) for n in ("rsi", "atr", "sma", "macd")}
    assert all(len(x) == 12 for x in v.values()) and len(set(v.values())) == 4
    assert manifest.indicator_version("rsi") == v["rsi"]  # the same code gives the same version

    def wilder_smooth(series, period):  # a changed helper that rsi and atr call
        return series.ewm(alpha=1 / period, adjust=False).mean()

    wilder_smooth.__module__ = indicators.__name__
    monkeypatch.setattr(indicators, "wilder_smooth", wilder_smooth)
    assert manifest.indicator_version("rsi") != v["rsi"] and manifest.indicator_version("atr") != v["atr"]
    assert manifest.indicator_version("sma") == v["sma"]  # sma doesn't use it


def test_indicators_used_covers_rules_stops_and_costs():
    s = strategy_from_dict(copy.deepcopy(CSV_STRATEGY))
    assert manifest.indicators_used(s) == ["atr", "rsi"]  # rsi in the entry, atr for the stop
    d = copy.deepcopy(CSV_STRATEGY)
    d["exit"] = {"stop": {"points": 2}, "when": {"left": "close", "op": "<", "right": {"indicator": "ema", "period": 9}}}
    d["costs"] = {"slippage_atr_pct": 5}
    assert manifest.indicators_used(strategy_from_dict(d)) == ["atr", "ema", "rsi"]


def test_backtest_run_record_has_the_manifest(client):
    sid = _save(client, CSV_STRATEGY)
    body = {"data": CSV_STRATEGY, "strategy_id": sid, "start": "2024-02-01"}
    out = client.post("/api/backtest", json=body, headers=H).json()
    meta = out["meta"]
    assert meta["strategy_name"] == "CSV test" and meta["strategy_id"] == sid
    assert meta["strategy_rules"]["entry"] == CSV_STRATEGY["entry"] and "name" not in meta["strategy_rules"]
    assert [i["name"] for i in meta["indicators"]] == ["atr", "rsi"]
    assert meta["indicators"][1]["version"] == manifest.indicator_version("rsi")
    assert meta["requested_period"] == {"start": "2024-02-01"}
    assert meta["timezone"] == {"prices": "UTC", "market": "UTC", "candle_alignment": "new_york"}
    assert meta["use_data_spread"] is True and meta["libraries"]["pandas"] == pd.__version__
    assert meta["engine_version"] and meta["rules_hash"] and meta["risk"]["account_size"] == 1000
    again = client.post("/api/backtest", json=body, headers=H).json()["meta"]
    assert {k: v for k, v in again.items() if k != "tested_at"} == {k: v for k, v in meta.items() if k != "tested_at"}


# ------------------------------------------------------------------ same footing


def _strat(name, symbol="AAA", size=1000, risk_pct=1, costs=None, timeframe="1h", **risk):
    return {"name": name, "market": {"symbol": symbol, "label": symbol, "provider": "csv", "timeframe": timeframe},
            "direction": "long", "entry": {"left": "close", "op": ">", "right": {"indicator": "sma", "period": 3}},
            "exit": {"stop": {"points": 2}},
            "risk": {"account_size": size, "risk_per_trade_pct": risk_pct, "currency": "GBP", **risk},
            "costs": costs or {}}


def test_same_footing_with_nothing_ticked_changes_nothing():
    items = [("a", "A", _strat("A")), ("b", "B", _strat("B", size=5000))]
    out = sidebyside.same_footing(items, 0, [])
    assert [x["data"] for x in out["strategies"]] == [items[0][2], items[1][2]]
    assert all(not x["changes"] for x in out["strategies"])
    assert any("own starting balance" in s for s in out["same"]) and "Each uses its own costs." in out["same"]


def test_same_footing_copies_account_and_lists_each_change():
    a = _strat("A", size=10000, risk_pct=0.5, max_daily_loss_pct=3)
    b = _strat("B", size=5000, risk_pct=2, compound=False)
    out = sidebyside.same_footing([("a", "A", a), ("b", "B", b)], 0, ["account"])
    first, second = out["strategies"]
    assert first["data"] == a and not first["changes"]  # the reference itself is untouched
    r = second["data"]["risk"]
    assert (r["account_size"], r["risk_per_trade_pct"], r["compound"], r["max_daily_loss_pct"]) == (10000, 0.5, True, 3)
    assert "Starting balance: 10,000 (was 5,000)" in second["changes"]
    assert "Risk per trade (%): 0.5 (was 2)" in second["changes"]
    assert "Compounding: on (was off)" in second["changes"]
    assert "Daily loss limit (%): 3 (was none)" in second["changes"]
    assert second["data"]["entry"] == b["entry"] and second["data"]["market"] == b["market"]  # rules stay
    assert b["risk"]["account_size"] == 5000  # the input isn't changed
    assert any("from A" in s for s in out["same"])


def test_same_footing_costs_keep_price_points_on_another_market():
    a = _strat("A", costs={"spread": 0.5, "slippage": 0.1, "commission_pct": 0.02, "funding_pct": 3})
    b = _strat("B", symbol="BBB", costs={"spread": 2, "slippage": 1})
    c = _strat("C", costs={"spread": 9})
    out = sidebyside.same_footing([("a", "A", a), ("b", "B", b), ("c", "C", c)], 0, ["costs"])
    _, ob, oc = out["strategies"]
    assert (ob["data"]["costs"]["spread"], ob["data"]["costs"]["slippage"]) == (2, 1)  # different market: kept
    assert ob["data"]["costs"]["commission_pct"] == 0.02 and ob["data"]["costs"]["funding_pct"] == 3
    assert ob["notes"] and "different market" in ob["notes"][0]
    assert oc["data"]["costs"]["spread"] == 0.5 and oc["data"]["costs"]["slippage"] == 0.1 and not oc["notes"]
    assert "Spread (price points): 0.5 (was 9)" in oc["changes"]


def test_same_footing_market_keeps_each_candle_size():
    a = _strat("A", symbol="AAA", point_value=10)
    b = _strat("B", symbol="BBB", timeframe="4h", point_value=1)
    out = sidebyside.same_footing([("a", "A", a), ("b", "B", b)], 0, ["market"])
    ob = out["strategies"][1]
    assert ob["data"]["market"] == {"symbol": "AAA", "label": "AAA", "provider": "csv", "timeframe": "4h"}
    assert ob["data"]["risk"]["point_value"] == 10
    assert "Market: AAA (was BBB)" in ob["changes"] and "Money per point: 10 (was 1)" in ob["changes"]
    assert any("Candle sizes differ" in s for s in out["same"])


def test_same_footing_reference_can_be_any_and_names_bad_results():
    a, b = _strat("A", size=1000), _strat("B", size=7000)
    out = sidebyside.same_footing([("a", "A", a), ("b", "B", b)], 1, ["account"])
    assert out["reference"] == "b" and out["strategies"][0]["data"]["risk"]["account_size"] == 7000
    bad = _strat("B", risk_pct=50)
    with pytest.raises(ValueError, match="^B:"):
        sidebyside.same_footing([("a", "A", a), ("b", "B", bad)], 0, [])


def test_compare_setup_route_and_identical_backtests(client):
    a = _save(client, CSV_STRATEGY)
    other = _short(market={**CSV_STRATEGY["market"], "symbol": "data/other.csv", "label": "Other"},
                   risk={"account_size": 2500, "risk_per_trade_pct": 2, "currency": "GBP", "compound": False},
                   costs={"commission": 3})
    b = _save(client, other)
    body = {"ids": [a, b], "reference": a, "match": {"account": True, "costs": True}, "start": "2024-02-01", "end": "2024-04-01"}
    r = client.post("/api/compare/setup", json=body, headers=H)
    assert r.status_code == 200, r.text
    setup = r.json()
    assert setup["period"] == {"start": "2024-02-01", "end": "2024-04-01"}
    sa, sb = setup["strategies"]
    assert sb["data"]["risk"]["account_size"] == 1000 and sb["data"]["risk"]["risk_per_trade_pct"] == 1
    assert any(c.startswith("Starting balance") for c in sb["changes"]) and "Commission per fill: 0 (was 3)" in sb["changes"]
    res = [client.post("/api/backtest", json={"data": x["data"], **setup["period"]}, headers=H).json() for x in (sa, sb)]
    assert res[0]["metrics"]["start"] == res[1]["metrics"]["start"] and res[0]["metrics"]["end"] == res[1]["metrics"]["end"]
    assert res[0]["metrics"]["start_equity"] == res[1]["metrics"]["start_equity"] == 1000
    assert res[0]["meta"]["commission"] == res[1]["meta"]["commission"] == 0


def test_compare_setup_years_become_fixed_dates(client):
    a, b = _save(client, CSV_STRATEGY), _save(client, _short())
    setup = client.post("/api/compare/setup", json={"ids": [a, b], "years": 1}, headers=H).json()
    start, end = pd.Timestamp(setup["period"]["start"]), pd.Timestamp(setup["period"]["end"])
    assert 364 <= (end - start).days <= 366
    assert any("same prices" in s for s in setup["same"])  # both read the same CSV file
    nostart = client.post("/api/compare/setup", json={"ids": [a, b]}, headers=H).json()
    assert nostart["period"] == {} and any("No start date" in s for s in nostart["same"])


@pytest.mark.parametrize("body,code,msg", [
    ({"ids": ["x"]}, 422, "2 to 4"),
    ({"ids": "abc"}, 422, "2 to 4"),
    ({"ids": ["a", "b", "c", "d", "e"]}, 422, "2 to 4"),
    ({"ids": ["SAME", "SAME"]}, 422, "only be chosen once"),
    ({"ids": ["A", "B"], "reference": "zzz"}, 422, "one of the chosen"),
    ({"ids": ["A", "B"], "years": 0}, 422, "between 1 and 20"),
    ({"ids": ["A", "B"], "start": "not a date"}, 422, "2025-01-31"),
    ({"ids": ["A", "B"], "start": "2025-03-01", "end": "2025-01-01"}, 422, "before the end"),
    ({"ids": ["A", "nope"]}, 404, "No strategy"),
])
def test_compare_setup_rejects_bad_requests(client, body, code, msg):
    a, b = _save(client, CSV_STRATEGY), _save(client, _short())
    ids = body["ids"]
    if isinstance(ids, list):
        body = {**body, "ids": [{"A": a, "B": b, "SAME": a}.get(x, x) for x in ids]}
    r = client.post("/api/compare/setup", json=body, headers=H)
    assert r.status_code == code, r.text
    assert msg in r.json()["detail"]


# ------------------------------------------------------------------ portfolio risk


def _risk_inputs(a_pnl, b_pnl, balance=1000.0):
    idx = pd.date_range("2025-01-01", periods=len(a_pnl), freq="D", tz="UTC")
    aligned = pd.DataFrame({"a": np.asarray(a_pnl, float), "b": np.asarray(b_pnl, float)}, index=idx)
    equity = balance + aligned.sum(axis=1)
    daily = aligned.resample("1D").last().diff().dropna(how="all")
    per = [{"id": k, "name": k.upper(), "pnl": float(aligned[k].iloc[-1])} for k in ("a", "b")]
    total = sum(p["pnl"] for p in per)
    for p in per:
        p["contribution_pct"] = p["pnl"] / total * 100 if total else 0.0
    return per, aligned, equity, daily, balance


def test_portfolio_risk_splits_the_worst_fall_and_the_variance():
    # A climbs to +100 by day 10 then falls back to 0 by day 20; B is flat, then makes +20
    # while A falls. The combined high is day 10 (1,100) and the low day 20 (1,020).
    a = list(range(0, 110, 10)) + list(range(90, -10, -10)) + [0] * 19
    b = [0] * 11 + list(range(2, 22, 2)) + [20] * 19
    per, aligned, equity, daily, bal = _risk_inputs(a, b)
    r = analysis.portfolio_risk(per, aligned, equity, daily, bal)
    w = r["worst_drawdown"]
    assert w["depth_pct"] == pytest.approx((1020 / 1100 - 1) * 100)
    assert pd.Timestamp(w["peak"]).day == 11 and pd.Timestamp(w["trough"]).day == 21 and w["recovered"] is None
    pa, pb = per
    assert pa["drawdown_pnl"] == pytest.approx(-100) and pb["drawdown_pnl"] == pytest.approx(20)
    assert pa["drawdown_share_pct"] == pytest.approx(125) and pb["drawdown_share_pct"] == pytest.approx(-25)
    assert pa["risk_share_pct"] + pb["risk_share_pct"] == pytest.approx(100)
    assert r["longest_below_high"] == {"days": 29.0, "from": str(aligned.index[10]), "to": str(aligned.index[-1]),
                                       "recovered": False}
    assert r["yearly_volatility_pct"] == pytest.approx(r["daily_volatility_pct"] * math.sqrt(365.25))
    assert r["largest_risk_share"]["id"] == "a" and r["largest_profit_share"]["id"] == "b"  # A ends at 0, B at +20
    assert 1 <= r["effective_strategies"] <= 2 and r["strategies"] == 2


def test_portfolio_risk_equal_swings_count_as_two_strategies():
    a = np.cumsum([0] + [5, -5] * 20)
    b = np.cumsum([0] + [-5, 5] * 20)  # the mirror image: B's days offset A's exactly
    per, aligned, equity, daily, bal = _risk_inputs(a, b)
    r = analysis.portfolio_risk(per, aligned, equity, daily, bal)
    assert r["effective_strategies"] == pytest.approx(2.0)
    assert per[0]["daily_swing_pct"] == pytest.approx(per[1]["daily_swing_pct"])
    assert r["worst_drawdown"] is None and r["longest_below_high"] is None  # combined balance never moves
    assert per[0]["risk_share_pct"] is None and r["largest_risk_share"] is None  # no variance to share
    assert r["largest_profit_share"] is None  # no profit to share


def test_portfolio_route_reports_combined_risk(client):
    a, b = _save(client, CSV_STRATEGY), _save(client, _short())
    for extra in ({}, {"shared": True}):
        r = client.post("/api/portfolio", json={"ids": [a, b], "balance": 5000, **extra}, headers=H)
        assert r.status_code == 200, r.text
        out = r.json()
        risk = out["risk"]
        assert {"worst_drawdown", "longest_below_high", "daily_volatility_pct", "effective_strategies",
                "largest_risk_share"} <= set(risk)
        for p in out["per_strategy"]:
            assert {"daily_swing_pct", "risk_share_pct", "drawdown_pnl", "drawdown_share_pct"} <= set(p)
        shares = [p["risk_share_pct"] for p in out["per_strategy"] if p["risk_share_pct"] is not None]
        if len(shares) == 2:
            assert sum(shares) == pytest.approx(100)
        if risk["worst_drawdown"]:
            assert risk["worst_drawdown"]["depth_pct"] == pytest.approx(out["metrics"]["max_drawdown_pct"])


# ------------------------------------------------------------------ journal by market and strategy


def test_journal_breaks_down_by_market_and_strategy(client, ws):
    store = StateStore(ws / "data" / "state.db")
    store.start_run("s1", name="Old name", market="US 500", timeframe="1h", currency="GBP", start_balance=1000)
    store.start_run("s1", name="Breakout", market="US 500", timeframe="1h", currency="GBP", start_balance=1000)
    store.add_trade("s1", {"direction": "long", "entry_time": "2025-01-02T10:00:00+00:00", "entry_price": 100.0,
                           "exit_time": "2025-01-02T14:00:00+00:00", "exit_price": 110.0, "size": 1, "pnl": 10.0}, None)
    pid = store.trades()[0]["id"]
    store.close()
    closed = {"direction": "long", "entry_time": "2025-01-02T10:01", "entry_price": 100, "exit_time": "2025-01-02T14:02",
              "exit_price": 104, "size": 1, "point_value": 1}
    client.post("/api/journal", json={**closed, "market": "us500", "paper_trade_id": pid}, headers=H)
    client.post("/api/journal", json={**closed, "market": "us500"}, headers=H)
    client.post("/api/journal", json={**closed, "market": "eurusd", "exit_price": 99}, headers=H)
    client.post("/api/journal", json={"market": "eurusd", "direction": "short", "entry_time": "2025-02-01T09:00",
                                      "entry_price": 1.1, "size": 1, "point_value": 1}, headers=H)
    b = client.get("/api/journal").json()["breakdowns"]
    assert {x["value"]: (x["trades"], x["pnl"]) for x in b["market"]} == {"us500": (2, 8.0), "eurusd": (1, -1.0)}
    assert b["strategy"] == [{"value": "Breakout", "trades": 1, "pnl": 4.0, "wins": 1}]
    assert b["open_by_market"] == [{"value": "eurusd", "trades": 1}]
