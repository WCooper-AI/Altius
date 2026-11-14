"""Session A3: slippage that grows with volatility, loss limits, and where fill prices came from.

Loss-limit scenarios use 1h candles from Mon 6 Jan 2025 00:00 UTC, a £1,000 account at 1% risk
and a 2-point stop: each "cycle" is a signal candle, a candle that fills at 101 and stops out
at 99 (about -£10), then a flat candle.
"""

import json

import pandas as pd
import pytest

from tradealerts import analysis, invariants, limits, report
from tradealerts.backtest import run_backtest
from tradealerts.simulator import Simulator
from tradealerts.strategy import StrategyError
from tradealerts.web.routes.testing import price_basis

from .conftest import make_bars, make_strategy
from .test_v03_s8e import _two_ids
from .test_web import H
from .test_v03_s8e import client, ws  # noqa: F401 (fixtures)

NO_COSTS = {"spread": 0, "use_data_spread": False}
ABOVE = {"left": "close", "op": ">", "right": 100.5}
RANGE = (100, 101, 99, 100)            # true range 2 every candle, so the 14-candle ATR is 2
FLAT = (100, 100, 100, 100)
SIGNAL = (100, 100, 100, 101)
LOSS = (101, 101, 98, 98)              # fills at 101, stop at 99 is hit inside the candle
CYCLE = [SIGNAL, LOSS, FLAT]


def run(rows, risk=None, costs=NO_COSTS, **kw):
    s = make_strategy(entry=ABOVE, costs=costs, risk={"account_size": 1000, "risk_per_trade_pct": 1, "point_value": 1,
                                                       "size_step": 0.01, "min_size": 0.01, **(risk or {})}, **kw)
    res = run_backtest(s, make_bars(rows))
    assert invariants.check(res) == []
    return res


def hours(total, cycles_at):
    """``total`` flat 1h candles with a losing cycle starting at each hour in ``cycles_at``."""
    rows = [FLAT] * total
    for at in cycles_at:
        rows[at:at + 3] = CYCLE
    return rows


# --------------------------------------------------------------------------- volatility slippage

def test_atr_slippage_hand_worked():
    # 16 candles of range 2 -> ATR 2; 10% of it = 0.2 per market fill.
    # Buy at 101 + 0.2 = 101.2, stop 99.2, target 105.2, size £10 / 2 = 5.
    # The entry candle runs to 120, but the ATR used is the one known at its open (2), not after it.
    # The target is a limit order: out at 105.2 with no slippage. P&L 4 x 5 = £20; slippage 0.2 x 5 = £1.
    rows = [RANGE] * 16 + [(100, 101, 99, 101), (101, 120, 100, 101), (101, 102, 100, 101)]
    t = run(rows, costs={**NO_COSTS, "slippage_atr_pct": 10}).trades.iloc[0]
    assert t["entry_price"] == pytest.approx(101.2) and t["stop"] == pytest.approx(99.2)
    assert t["exit_reason"] == "target" and t["exit_price"] == pytest.approx(105.2)
    assert t["pnl"] == pytest.approx(20.0) and t["slippage_cost"] == pytest.approx(1.0)


def test_atr_slippage_on_market_exit_adds_to_other_slippage():
    # 0.2 (10% of ATR 2) + 0.05 points = 0.25 a fill: in at 101.25, out at the end-of-data close 101 - 0.25.
    rows = [RANGE] * 16 + [(100, 101, 99, 101), (101, 102, 100, 101), (101, 102, 100, 101)]
    t = run(rows, costs={**NO_COSTS, "slippage_atr_pct": 10, "slippage": 0.05}).trades.iloc[0]
    assert t["entry_price"] == pytest.approx(101.25)
    assert t["exit_reason"] == "end_of_data" and t["exit_price"] == pytest.approx(100.75)
    assert t["slippage_cost"] == pytest.approx(0.5 * t["size"])


def test_atr_slippage_needs_14_candles_of_history():
    # The signal comes on candle 2, before any ATR exists: no volatility slippage yet.
    t = run([RANGE, (100, 101, 99, 101), (101, 102, 100, 101), (101, 102, 100, 101)],
            costs={**NO_COSTS, "slippage_atr_pct": 10}).trades.iloc[0]
    assert t["entry_price"] == pytest.approx(101.0) and t["slippage_cost"] == 0


def test_atr_slippage_off_adds_no_reference():
    s = make_strategy(entry=ABOVE, costs=NO_COSTS)
    assert "slip_atr" not in s.prepare(make_bars([RANGE] * 20)).refs


# --------------------------------------------------------------------------- loss limits, one strategy

def test_daily_loss_limit_stops_entries_until_the_next_day():
    # Mon: -£10 (990, -1%), -£9.90 (980.10, -1.99%) -> the next two are stopped at 1.5%.
    # Tue starts from 980.10: -£9.80, -£9.70 both allowed (the second starts at -1%).
    res = run(hours(36, [0, 3, 6, 9, 24, 27]), risk={"max_daily_loss_pct": 1.5})
    assert len(res.trades) == 4 and res.metrics["blocked_by_limits"] == 2
    assert [e.time.hour for e in res.skipped] == [7, 10]
    assert all(e.data["limit"] == "daily_loss" for e in res.skipped)
    assert res.trades["entry_time"].str[:10].tolist() == ["2025-01-06"] * 2 + ["2025-01-07"] * 2
    assert any("stopped 2 trades from opening: 2 by the daily loss limit" in n for n in res.notes)


def test_weekly_loss_limit_lasts_until_monday():
    # 2.5% a week: Mon -1%, -1.99%, -2.97% -> stopped from the 4th; still stopped on Tuesday; free next Monday.
    rows = hours(7 * 24 + 6, [0, 3, 6, 9, 24, 7 * 24])
    res = run(rows, risk={"max_weekly_loss_pct": 2.5})
    assert len(res.trades) == 4 and res.metrics["blocked_by_limits"] == 2
    assert {e.data["limit"] for e in res.skipped} == {"weekly_loss"}
    assert res.trades["entry_time"].iloc[-1].startswith("2025-01-13")


def test_drawdown_limit_is_measured_from_the_high_and_never_lifts():
    # A win first (+£20 -> 1,020), then losses. From the high, 989.74 is -2.97% (past 2.5%),
    # though only -1.03% from the start. Every later signal, on any day, is stopped.
    win = [SIGNAL, (101, 106, 101, 105), FLAT]
    rows = win + hours(48, [0, 3, 6, 9, 30])
    res = run(rows, risk={"max_drawdown_pct": 2.5})
    assert res.trades["exit_reason"].tolist() == ["target", "stop", "stop", "stop"]
    assert res.trades["equity_after"].iloc[-1] > 980  # well short of -2.5% from the start
    assert res.metrics["blocked_by_limits"] == 2 and {e.data["limit"] for e in res.skipped} == {"drawdown"}
    assert any("drawdown limit first stopped a trade on 06 Jan 2025" in n for n in res.notes)


def test_limit_days_follow_the_market_time_zone():
    # New York: Mon 00:00-04:59 UTC is still Sunday there, so the 05:00 UTC trade starts a new day.
    rows = hours(12, [0, 3, 6])
    utc = run(rows, risk={"max_daily_loss_pct": 1.5})
    ny = run(rows, risk={"max_daily_loss_pct": 1.5},
             market={"symbol": "TEST", "label": "Test", "timeframe": "1h", "tz": "America/New_York"})
    assert len(utc.trades) == 2 and len(ny.trades) == 3


def test_no_limits_change_nothing():
    rows = hours(36, [0, 3, 6, 9, 24, 27])
    assert len(run(rows).trades) == 6 and run(rows).metrics["blocked_by_limits"] == 0


def test_limit_skips_are_not_reported_as_unplaced_signals():
    res = run(hours(36, [0, 3, 6, 9]), risk={"max_daily_loss_pct": 1.5})
    lines = report.warnings_for(res)
    assert not any("signal(s) were skipped" in x for x in lines)
    assert any("Your loss limits stopped 2 trades" in x for x in lines)
    assert res.skipped[0].data["reason"] == limits.REASONS["daily_loss"]


def test_limits_survive_a_saved_and_reloaded_forward_test_state():
    # A live run saves its state between checks; the result must match one unbroken pass.
    s = make_strategy(entry=ABOVE, costs=NO_COSTS, risk={"account_size": 1000, "risk_per_trade_pct": 1,
                                                         "point_value": 1, "max_daily_loss_pct": 1.5})
    df = make_bars(hours(36, [0, 3, 6, 9, 24, 27]))
    prep = s.prepare(df)

    def steps(sim, a, b):
        out = []
        for i in range(a, b):
            r = df.iloc[i]
            out += sim.step(df.index[i], r.open, r.high, r.low, r.close, bool(prep.entry[i]), bool(prep.exit[i]),
                            prep.refs_at(i), values=prep.values_at(i))
        return out

    whole = steps(Simulator(s), 0, len(df))
    first = Simulator(s)
    part = steps(first, 0, 8)
    saved = json.loads(json.dumps(first.state))
    part += steps(Simulator(s, saved), 8, len(df))
    assert [(e.kind, str(e.time)) for e in part] == [(e.kind, str(e.time)) for e in whole]
    assert sum(1 for e in whole if e.kind == "skipped") == 2


def test_tracker_handles_a_clock_change():
    # London clocks go forward at 01:00 on 30 Mar 2025; the day still ends at local midnight.
    track = {}
    limits.roll(track, pd.Timestamp("2025-03-30 12:00", tz="UTC"), 1000, "Europe/London")
    assert pd.Timestamp(track["day_end"], tz="UTC") == pd.Timestamp("2025-03-30 23:00", tz="UTC")
    assert pd.Timestamp(track["week_end"], tz="UTC") == pd.Timestamp("2025-03-30 23:00", tz="UTC")


# --------------------------------------------------------------------------- settings

@pytest.mark.parametrize("risk,costs,msg", [
    ({"max_daily_loss_pct": 0}, {}, "above 0"),
    ({"max_weekly_loss_pct": 150}, {}, "up to 100"),
    ({"max_drawdown_pct": "lots"}, {}, "must be a number"),
    ({}, {"slippage_atr_pct": 101}, "typo"),
    ({}, {"slippage_atr_pct": -1}, "at least 0"),
])
def test_bad_settings_are_refused(risk, costs, msg):
    with pytest.raises(StrategyError, match=msg):
        make_strategy(risk={"account_size": 1000, "risk_per_trade_pct": 1, **risk}, costs=costs)


def test_blank_settings_mean_none():
    s = make_strategy(risk={"account_size": 1000, "risk_per_trade_pct": 1, "max_daily_loss_pct": "",
                            "max_drawdown_pct": None}, costs={"slippage_atr_pct": ""})
    assert s.risk.max_daily_loss_pct is None and s.risk.max_drawdown_pct is None and s.slippage_atr_pct == 0


# --------------------------------------------------------------------------- shared account

def _losing_pair():
    rows = hours(36, [0, 3, 6, 9, 24, 27])
    risk = {"account_size": 1000, "risk_per_trade_pct": 1, "point_value": 1, "compound": False}
    a = make_strategy(name="A", entry=ABOVE, costs=NO_COSTS, risk=risk)
    b = make_strategy(name="B", entry=ABOVE, costs=NO_COSTS, risk=risk,
                      market={"symbol": "OTHER", "label": "Other", "timeframe": "1h"})
    bars = make_bars(rows)
    return [("a", "A", run_backtest(a, bars)), ("b", "B", run_backtest(b, bars))]


def test_shared_account_daily_loss_limit():
    # Both strategies lose together: Mon cycle 1 takes the balance to 980.10 (-1.99%), so Monday's
    # other 3 cycles (6 trades) are stopped; Tuesday's first cycle runs, its second (2 trades) is stopped.
    out = analysis.shared_account(_losing_pair(), 1000.0, max_daily_loss_pct=1.5)
    assert out["skipped"]["daily_loss"] == 8 and out["metrics"]["trades_skipped"] == 8
    assert [(p["taken"], p["skipped"]) for p in out["per_strategy"]] == [(2, 4), (2, 4)]
    free = analysis.shared_account(_losing_pair(), 1000.0)
    assert free["metrics"]["trades_skipped"] == 0


def test_shared_account_drawdown_limit():
    out = analysis.shared_account(_losing_pair(), 1000.0, max_drawdown_pct=2.5)
    # A closes before B checks (both stop out on their entry candle): 1,000 -> 990 -> 980.10.
    # Cycle 2: A is let in at -1.99% and loses -> 970.30 (-2.97%), so B and every later trade are stopped.
    assert out["skipped"]["drawdown"] == 9 and sum(p["taken"] for p in out["per_strategy"]) == 3


def test_portfolio_route_takes_loss_limits(client):  # noqa: F811
    a, b = _two_ids(client)
    body = {"ids": [a, b], "balance": 5000, "shared": True, "max_daily_loss_pct": 1, "max_drawdown_pct": 5}
    r = client.post("/api/portfolio", json=body, headers=H)
    assert r.status_code == 200, r.text
    assert {"daily_loss", "weekly_loss", "drawdown"} <= set(r.json()["skipped"])


@pytest.mark.parametrize("extra,msg", [
    ({"shared": True, "max_daily_loss_pct": 0}, "above 0"),
    ({"shared": True, "max_weekly_loss_pct": 101}, "up to 100"),
    ({"shared": True, "max_drawdown_pct": True}, "Drawdown limit"),
    ({"max_daily_loss_pct": 2}, "share one account"),
])
def test_portfolio_route_rejects_bad_limits(client, extra, msg):  # noqa: F811
    a, b = _two_ids(client)
    r = client.post("/api/portfolio", json={"ids": [a, b], "balance": 5000, **extra}, headers=H)
    assert r.status_code == 422, r.text
    assert msg in r.json()["detail"]


# --------------------------------------------------------------------------- where fills came from

def test_price_basis_says_where_the_spread_came_from():
    assert "bid and ask candles" in price_basis("dukascopy", True, True)
    assert "Yahoo Finance prices have no bid or ask" in price_basis("yahoo", False, True)
    assert "switched off" in price_basis("dukascopy", False, False)
    assert "no spread column" in price_basis("csv", False, True)
