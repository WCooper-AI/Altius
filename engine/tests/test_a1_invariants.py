"""Session A1: the invariant checker, the simulator's own guards, and the funding / borrow /
financing split (including old saved data)."""

import dataclasses
import math
import sqlite3

import numpy as np
import pandas as pd
import pytest

from tradealerts import analysis, invariants
from tradealerts.backtest import run_backtest
from tradealerts.sample import synthetic_bars
from tradealerts.simulator import InvariantError, Simulator
from tradealerts.state import StateStore
from tradealerts.strategy import SkipTrade

from .conftest import make_strategy

COSTS = {"spread": 1.0, "use_data_spread": False, "funding_pct": 5, "borrow_pct": 3, "commission": 1,
         "commission_pct": 0.05, "slippage": 0.2, "slippage_pct": 0.01}
EXIT = {"stop": {"atr": 1.5}, "target": {"r_multiple": 2}, "max_bars": 30,
        "when": {"left": "close", "op": "<", "right": {"indicator": "sma", "period": 5}}}


@pytest.fixture(scope="module")
def good():
    s = make_strategy(direction="short", costs=COSTS, exit=EXIT,
                      risk={"account_size": 10_000, "risk_per_trade_pct": 1, "point_value": 1,
                            "size_step": 0.01, "min_size": 0.01})
    res = run_backtest(s, synthetic_bars(days=40, freq="1h", seed=5))
    assert len(res.trades) >= 5 and res.metrics["total_borrow"] > 0
    return res


def broken(res, trade=None, metric=None, equity=None):
    """A copy of ``res`` with one thing changed: trade=(row, col, value), metric=(key, value), equity=(i, value)."""
    trades, eq, m = res.trades.copy(), res.equity.copy(), dict(res.metrics)
    if trade:
        row, col, value = trade
        trades[col] = trades[col].astype(object)
        trades.at[row, col] = value
    if metric:
        m[metric[0]] = metric[1]
    if equity:
        eq.iloc[equity[0]] = equity[1]
    return dataclasses.replace(res, trades=trades, equity=eq, metrics=m)


def problems_mention(res, text):
    found = invariants.check(res)
    assert any(text in p for p in found), found


# ------------------------------------------------------------------ the checker


def test_good_result_passes_and_repeats_exactly(good):
    assert invariants.check(good) == []
    again = run_backtest(good.strategy, synthetic_bars(days=40, freq="1h", seed=5))
    assert invariants.same_result(good, again) == []


def test_checker_catches_each_broken_rule(good):
    t0, t1 = good.trades.iloc[0], good.trades.iloc[1]
    problems_mention(broken(good, trade=(0, "exit_time", "2000-01-01 00:00:00+00:00")), "before it opened")
    problems_mention(broken(good, trade=(1, "entry_time", t0["entry_time"])), "before the previous trade closed")
    problems_mention(broken(good, trade=(0, "signal_time", t0["entry_time"])), "no later than its signal")
    problems_mention(broken(good, trade=(0, "size", -1.0)), "isn't a positive number")
    problems_mention(broken(good, trade=(0, "exit_price", float("nan"))), "isn't a real number")
    problems_mention(broken(good, trade=(0, "pnl", t0["pnl"] + 1)), "isn't the price move")
    problems_mention(broken(good, trade=(0, "financing", t0["financing"] + 1)), "isn't funding")
    problems_mention(broken(good, trade=(0, "commission", -t0["commission"])), "commission is negative")
    problems_mention(broken(good, trade=(1, "equity_after", t1["equity_after"] + 5)), "balance after it")
    problems_mention(broken(good, metric=("total_borrow", 0.0)), "total_borrow")
    problems_mention(broken(good, metric=("trades", len(good.trades) + 1)), "trades reported")
    problems_mention(broken(good, metric=("end_equity", good.metrics["end_equity"] + 1)), "final balance")
    problems_mention(broken(good, equity=(-1, good.equity.iloc[-1] + 3)), "equity curve isn't the realised balance")
    problems_mention(broken(good, equity=(0, float("nan"))), "missing or infinite")
    late = str(good.equity.index[-1] + pd.Timedelta(days=3))
    problems_mention(broken(good, trade=(len(good.trades) - 1, "exit_time", late)), "outside the data")
    # a duplicated trade is a trade closed twice
    dup = dataclasses.replace(good, trades=pd.concat([good.trades.iloc[:1], good.trades]).reset_index(drop=True))
    assert invariants.check(dup)


def test_same_result_reports_differences(good):
    other = broken(good, trade=(0, "pnl", good.trades.iloc[0]["pnl"] + 1), equity=(3, 0.0))
    diffs = invariants.same_result(good, other)
    assert any("trades differ" in d for d in diffs) and "equity curves differ" in diffs


def test_split_parts_are_both_checked(good):
    res = run_backtest(good.strategy, synthetic_bars(days=40, freq="1h", seed=5), split_pct=60)
    assert invariants.check(res) == []
    bad = dataclasses.replace(res, oos_metrics={**res.oos_metrics, "total_funding": -1.0})
    assert any(p.startswith("out-of-sample: total_funding") for p in invariants.check(bad))


# ------------------------------------------------------------------ the simulator's own guards


def test_closing_with_nothing_open_is_refused():
    sim = Simulator(make_strategy())
    with pytest.raises(InvariantError, match="closed twice"):
        sim._close(pd.Timestamp("2025-01-06", tz="UTC"), 100.0, "stop", 0.0)


def test_closing_before_opening_is_refused():
    sim = Simulator(make_strategy())
    sim.state["position"] = {"entry_time": "2025-01-07 00:00:00+00:00", "entry_price": 100.0, "stop": 98.0,
                             "target": None, "size": 1.0, "bars_held": 1, "signal_time": "2025-01-06 23:00:00+00:00"}
    with pytest.raises(InvariantError, match="before it opens"):
        sim._close(pd.Timestamp("2025-01-06 12:00", tz="UTC"), 101.0, "stop", 0.0)


def test_candle_with_missing_price_is_refused():
    sim = Simulator(make_strategy())
    with pytest.raises(ValueError, match="missing or invalid price"):
        sim.step(pd.Timestamp("2025-01-06", tz="UTC"), 100.0, float("nan"), 99.0, 100.0, False, False, {})


def test_stop_that_isnt_a_number_skips_the_trade():
    with pytest.raises(SkipTrade):
        make_strategy().plan(float("nan"), {})
    with pytest.raises(SkipTrade):
        make_strategy(exit={"stop": {"points": 2}, "target": {"level": {"indicator": "sma", "period": 3}}}).plan(
            100.0, {"target_level": float("inf")})


def test_check_state_matches_a_live_run(good):
    df = synthetic_bars(days=10, freq="1h", seed=8)
    s = good.strategy
    prepared, sim = s.prepare(df), Simulator(s)
    start, closed = s.risk.account_size, 0.0
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    for i, t in enumerate(df.index):
        for ev in sim.step(t, o[i], h[i], l[i], c[i], bool(prepared.entry[i]), bool(prepared.exit[i]),
                           prepared.refs_at(i)):
            if ev.kind == "closed":
                closed += ev.data["pnl"]
        assert invariants.check_state(sim, start, closed) == []
    sim.state["equity"] += 1
    assert invariants.check_state(sim, start, closed)


# ------------------------------------------------------------------ funding / borrow / financing


def test_short_trade_reports_each_overnight_charge_once(good):
    t = good.trades
    assert (t["financing"] - t["funding"] - t["borrow"]).abs().max() < 1e-9
    assert (t["borrow"] > 0).any() and (t["funding"] > 0).any()
    m = good.metrics
    assert m["total_financing"] == pytest.approx(m["total_funding"] + m["total_borrow"])
    # every penny of P&L is the price move less financing and commission
    move = (t["exit_price"] - t["entry_price"]) * -1 * t["size"]
    assert np.allclose(t["pnl"], move - t["financing"] - t["commission"])


def test_open_position_saved_before_the_split_is_upgraded():
    s = make_strategy(direction="short", costs={"spread": 0, "use_data_spread": False, "funding_pct": 3.65,
                                                 "borrow_pct": 3.65})
    old = {"equity": 999.4, "pending_entry": None, "pending_exit": False, "trades": 0, "half_spread": 0.0,
           "position": {"signal_time": "2025-01-06 00:00:00+00:00", "entry_time": "2025-01-06 01:00:00+00:00",
                        "entry_price": 100.0, "stop": 102.0, "target": None, "size": 5.0, "bars_held": 3,
                        "entry_half_spread": 0.0, "entry_slippage": 0.0, "commission": 0.0,
                        "funding": 0.6, "borrow": 0.3,  # version 1: funding included the borrow fee
                        "funded_until": "2025-01-06 04:00:00+00:00"}}
    sim = Simulator(s, old)
    assert sim.state["version"] == 2 and sim.position["funding"] == pytest.approx(0.3)
    ev = sim.finish(pd.Timestamp("2025-01-06 04:00", tz="UTC"), 100.0)[0]
    assert ev.data["financing"] == pytest.approx(0.6) and ev.data["pnl"] == pytest.approx(-0.6)
    # upgrading twice changes nothing
    again = Simulator(s, sim.state)
    assert again.state["version"] == 2


def test_saved_paper_trades_are_split_once(tmp_path):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE paper_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT NOT NULL, "
                "run_id INTEGER, pnl REAL, funding REAL, borrow REAL)")
    con.execute("INSERT INTO paper_trades (strategy_id, pnl, funding, borrow) VALUES "
                "('s', -5, 0.5, 0.2), ('s', 4, 0.1, NULL), ('s', 1, NULL, NULL)")
    con.commit()
    con.close()
    for _ in range(2):  # opening the database again doesn't take the borrow fee off twice
        store = StateStore(db)
        rows = [tuple(r) for r in store.db.execute("SELECT funding, borrow, financing FROM paper_trades ORDER BY id")]
        assert rows[0] == pytest.approx((0.3, 0.2, 0.5))
        assert rows[1] == (0.1, None, 0.1) and rows[2] == (None, None, None)
        store.close()


def test_new_paper_trades_keep_all_three(tmp_path, good):
    store = StateStore(tmp_path / "state.db")
    trade = good.trades.iloc[0].to_dict()
    store.add_trade("s", trade, None)
    saved = store.trades()[0]
    assert (saved["funding"], saved["borrow"], saved["financing"]) == pytest.approx(
        (trade["funding"], trade["borrow"], trade["financing"]))
    store.close()


def test_window_costs_add_up_to_net(good):
    m = analysis.window_metrics(analysis.trades_frame(good.trades), 10_000)
    costs = m["spread_cost"] + m["slippage_cost"] + m["commission"] + m["financing"]
    assert m["gross_pnl"] - costs == pytest.approx(m["net_pnl"])
    assert m["financing"] == pytest.approx(good.metrics["total_financing"])
    assert m["commission"] > 0 and m["commission"] == pytest.approx(good.metrics["total_commission"])


def test_window_costs_read_trades_saved_without_financing():
    rows = [{"pnl": 10, "r_multiple": 1, "funding": 0.5, "borrow": 0.25, "spread_cost": 1},
            {"pnl": -5, "r_multiple": -0.5, "funding": 0.5, "borrow": None, "spread_cost": 1, "financing": None}]
    f = analysis.trades_frame(rows)
    assert list(f["financing"]) == [0.75, 0.5]
    m = analysis.window_metrics(f, 1000)
    assert m["gross_pnl"] == pytest.approx(5 + 2 + 1.25) and not math.isnan(m["commission"])
