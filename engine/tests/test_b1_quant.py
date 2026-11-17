"""Session B1: quantitative audit. Regression tests for the defects it fixed, more hand-worked
reference scenarios (on top of test_a1_reference.py), and property tests that nothing from
the future leaks into the past.

Unless a scenario says otherwise: 1h candles from Mon 6 Jan 2025 00:00 UTC, £1,000 account,
1% risk (£10), point value 1, sizes in steps of 0.01, stop 2 points and target 4 points from
the fill, no costs. Prices are mid prices; longs buy at the ask and sell at the bid.
"""

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tradealerts import analysis, fxrates, indicators, invariants, timeframes
from tradealerts import data as data_mod
from tradealerts.backtest import run_backtest
from tradealerts.risk import position_size
from tradealerts.strategy import strategy_from_dict

from .conftest import make_bars, make_strategy
from .test_a1_properties import RUNS, cases, prices

FLAT = (100, 100, 100, 100)
SIGNAL = (100, 100, 100, 101)          # closes above 100.5: a long enters at the next open
ENTRY = (101, 101.5, 100.5, 101.2)     # fills at 101, reaches neither stop nor target
DOWN = (100, 100, 99, 99)              # closes under 99.5: a short enters at the next open
SHORT_ENTRY = (99, 99.5, 98.5, 99)
NO_COSTS = {"spread": 0, "use_data_spread": False}
LONG = {"entry": {"left": "close", "op": ">", "right": 100.5}}
SHORT = {"direction": "short", "entry": {"left": "close", "op": "<", "right": 99.5}}
DAILY = {"market": {"symbol": "TEST", "label": "Test", "timeframe": "1d"}}
NY = "America/New_York"


def run(rows, fine=None, freq="1h", spreads=None, fx=None, **overrides):
    overrides.setdefault("entry", LONG["entry"])
    overrides.setdefault("costs", NO_COSTS)
    df = make_bars(rows, freq=freq)
    if spreads is not None:
        df["spread"] = spreads
    if fx is not None:
        df["fx"] = fx
    res = run_backtest(make_strategy(**overrides), df, fine=fine)
    assert invariants.check(res) == []
    return res


def only_trade(res):
    assert len(res.trades) == 1
    return res.trades.iloc[0]


# =========================================================================== regressions

# --- New York candles keep their hour through the clock changes (data.resample_ohlcv) ---

def _hourly(start, end, weekdays_only):
    idx = pd.date_range(start, end, freq="1h", tz="UTC")
    if weekdays_only:  # FX hours: Sunday 17:00 to Friday 17:00 New York
        ny = idx.tz_convert(NY)
        wd, hr = ny.day_of_week, ny.hour
        idx = idx[~((wd == 5) | ((wd == 4) & (hr >= 17)) | ((wd == 6) & (hr < 17)))]
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)


@pytest.mark.parametrize("start, end", [("2025-01-05", "2025-07-20"), ("2025-07-06", "2025-12-20")])
def test_new_york_candles_keep_their_hour_across_clock_changes(start, end):
    # Before the fix, candles were cut every 24 h of real time from the first one, so after a
    # clock change they started at 18:00 (or 16:00) New York, and every weekend left a daily
    # "candle" holding just the hour after Sunday's 17:00 open.
    raw = _hourly(start, end, weekdays_only=True)
    daily = data_mod.resample_ohlcv(raw, "1d", "new_york")
    local = daily.index.tz_convert(NY)
    assert set(local.hour) == {17} and set(local.minute) == {0}
    assert set(daily["volume"]) == {24.0}                       # every day is a full 24 hours
    assert set(local.day_of_week) == {6, 0, 1, 2, 3}            # Sunday-Thursday 17:00 starts
    four = data_mod.resample_ohlcv(raw, "4h", "new_york")
    assert set(four.index.tz_convert(NY).hour) == {1, 5, 9, 13, 17, 21} and set(four["volume"]) == {4.0}
    assert daily["volume"].sum() == four["volume"].sum() == len(raw)


def test_candle_across_the_autumn_change_is_25_hours_and_only_closes_at_its_end():
    # Markets open through the weekend (crypto): the day from Sat 1 Nov 17:00 EDT (21:00 UTC)
    # to Sun 2 Nov 17:00 EST (22:00 UTC) is 25 hours long, as on brokers' charts.
    raw = _hourly("2025-10-28", "2025-11-06", weekdays_only=False)
    daily = data_mod.resample_ohlcv(raw, "1d", "new_york")
    day = pd.Timestamp("2025-11-01 21:00", tz="UTC")
    assert daily.loc[day, "volume"] == 25.0
    assert daily.index[daily.index.get_loc(day) + 1] == pd.Timestamp("2025-11-02 22:00", tz="UTC")
    # It hasn't finished 24 hours after it started (that was an unfinished candle read as closed).
    assert data_mod.closed_bars(daily, "1d", day + pd.Timedelta(hours=24, minutes=30)).index[-1] < day
    assert data_mod.closed_bars(daily, "1d", day + pd.Timedelta(hours=25)).index[-1] == day
    # spring: Sat 8 Mar 17:00 EST to Sun 9 Mar 17:00 EDT is 23 hours
    spring = data_mod.resample_ohlcv(_hourly("2025-03-05", "2025-03-12", False), "1d", "new_york")
    assert spring.loc[pd.Timestamp("2025-03-08 22:00", tz="UTC"), "volume"] == 23.0


def test_utc_alignment_and_short_candles_are_unchanged():
    raw = _hourly("2025-03-05", "2025-03-12", weekdays_only=False)
    utc = data_mod.resample_ohlcv(raw, "1d", "utc")
    assert set(utc.index.hour) == {0}
    two = data_mod.resample_ohlcv(raw, "1h", "new_york")
    assert two is raw
    assert len(data_mod.closed_bars(raw, "1h", raw.index[-1] + pd.Timedelta(hours=1))) == len(raw)


# --- a stop closer than the spread is passed at the fill (simulator._levels) ---

@pytest.mark.parametrize("side, rows, fill, exit_px", [
    # Long: spread 1 -> buy at 101.5, stop 0.3 below = 101.2. The bid at that open is
    # 100.5, already under the stop, so the trade is out at 100.5 straight away.
    (LONG, [FLAT, SIGNAL, (101, 101.2, 100.9, 101.1)], 101.5, 100.5),
    # Short: sell at 99 - 0.5 = 98.5, stop 98.8; the ask at that open is 99.5, already above it.
    (SHORT, [FLAT, DOWN, (99, 99.1, 98.8, 98.9)], 98.5, 99.5),
])
def test_stop_inside_the_spread_exits_at_the_open_not_at_the_stop(side, rows, fill, exit_px):
    # size £10 / 0.3 = 33.33. Loss 1 point x 33.33 = £33.33 = -3.33R, not the -1R (£10)
    # reported before the fix, which filled the stop at its own price inside the spread.
    t = only_trade(run(rows, exit={"stop": {"points": 0.3}, "target": {"points": 4}},
                       costs={"spread": 1.0, "use_data_spread": False}, **side))
    assert t["entry_price"] == pytest.approx(fill) and t["size"] == pytest.approx(33.33)
    assert t["exit_reason"] == "stop" and t["exit_price"] == pytest.approx(exit_px)
    assert t["exit_time"] == t["entry_time"]
    assert t["pnl"] == pytest.approx(-33.33) and t["r_multiple"] == pytest.approx(-1 / 0.3, rel=1e-3)


def test_stop_just_outside_the_spread_is_not_hit_at_the_fill():
    # stop 1.2 below the 101.5 fill = 100.3; the bid at the open (100.5) is above it, the candle's
    # bid low 100.4 too, so the trade stays open to the end of the data.
    t = only_trade(run([FLAT, SIGNAL, (101, 101.2, 100.9, 101.1)], exit={"stop": {"points": 1.2}},
                       costs={"spread": 1.0, "use_data_spread": False}))
    assert t["exit_reason"] == "end_of_data"


# --- shared account: a flat commission doesn't grow with the size (analysis.shared_account) ---

def _overlapping(sid, threshold):
    """Two strategies on the same candles whose trades overlap (worked out in the test below)."""
    rows = [FLAT, SIGNAL, (101, 101.8, 100.5, 101.6), (101.6, 102, 101, 101.8),
            (101.8, 105.2, 101.5, 105.1), (105.1, 106, 105, 105.8), (105.8, 105.9, 103, 103.2)]
    s = make_strategy(name=sid, entry={"left": "close", "op": ">", "right": threshold},
                      costs={**NO_COSTS, "commission": 1},
                      risk={"account_size": 1000, "risk_per_trade_pct": 1, "point_value": 1,
                            "size_step": 0.01, "min_size": 0.01, "compound": False})
    res = run_backtest(s, make_bars(rows))
    assert invariants.check(res) == []
    return sid, sid, res


def test_shared_account_replays_overlapping_trades_by_hand():
    # A (close > 100.5): in 02:00 at 101 -> target 105 at 04:00, +20 - £2 commission = +18;
    #                    in 05:00 at 105.1 -> stop 103.1 at 06:00, -10 - 2 = -12.
    # B (close > 101.5): in 03:00 at 101.6 -> target 105.6 at 05:00, +18;
    #                    in 06:00 at 105.8 -> stop 103.8 at 06:00, -12. Each sized 5 (£10 / 2).
    a, b = _overlapping("a", 100.5), _overlapping("b", 101.5)
    assert list(a[2].trades["pnl"]) == pytest.approx([18, -12]) and list(b[2].trades["pnl"]) == pytest.approx([18, -12])
    assert list(a[2].trades["exit_time"]) == ["2025-01-06 04:00:00+00:00", "2025-01-06 06:00:00+00:00"]
    # One account, in entry order: A1 and B1 open on £1,000 (size 5 each, overlapping).
    # 05:00: A1 and B1 have closed -> £1,036. A2 sized 1% x 1,036 / 2 = 5.18: the price move
    #   scales (-10 x 5.18 / 5 = -10.36) but the £2 commission doesn't -> -12.36 (was -12.432).
    # 06:00: A2 closed -> £1,023.64. B2 sized 1% x 1,023.64 / 2 = 5.1182 -> 5.11 (rounded down):
    #   -10 x 5.11 / 5 - 2 = -12.22 -> £1,011.42.
    out = analysis.shared_account([a, b], 1000)
    assert out["metrics"]["trades"] == 4 and out["metrics"]["max_open_trades"] == 2
    assert out["metrics"]["end_equity"] == pytest.approx(1011.42)
    # At most one open: B1 is skipped (A1 open). A2 on 1,018 = 5.09 -> -12.18; B2 on 1,005.82
    # = 5.0291 -> 5.02 -> -12.04; ends at £993.78.
    one = analysis.shared_account([a, b], 1000, max_open_trades=1)
    assert one["skipped"]["open_trades"] == 1 and one["metrics"]["trades"] == 3
    assert one["metrics"]["end_equity"] == pytest.approx(993.78)


def test_portfolio_open_risk_is_in_account_money():
    # USD market, £0.50 per $: size £10 / (2 x 0.5) = 10. At risk 2 x 10 = $20 = £10 = 1% of
    # £1,000 (it was reported as 2%, the dollars taken for pounds).
    s = make_strategy(entry=LONG["entry"], costs=NO_COSTS,
                      risk={"account_size": 1000, "risk_per_trade_pct": 1, "point_value": 1, "size_step": 0.01,
                            "min_size": 0.01, "compound": False, "size_currency": "market",
                            "market_currency": "USD", "currency": "GBP", "fx_rate": 0.5})
    res = run_backtest(s, make_bars([FLAT, SIGNAL, ENTRY, (101.2, 106, 101, 105)]))
    assert res.trades.iloc[0]["size"] == pytest.approx(10)
    out = analysis.portfolio([("a", "a", res)], 1000)
    assert out["metrics"]["max_open_risk_pct"] == pytest.approx(1.0)


# =========================================================================== reference scenarios

def test_long_bid_reaches_the_stop_when_the_mid_does_not():
    # Spread from the data: 0.4 on the entry candle (buy at 101 + 0.2 = 101.2, stop 99.2,
    # target 105.2, size 5). Next candle's mid low 99.3 stays above the stop, but its spread
    # is 0.6, so the bid low is 99.0: stopped at 99.2. P&L (99.2 - 101.2) x 5 = -£10.
    # Spread paid: half of 0.4 in and half of 0.6 out = (0.2 + 0.3) x 5 = £2.50.
    res = run([FLAT, SIGNAL, (101, 101.5, 100.5, 101.2), (101.2, 101.4, 99.3, 99.5)],
              spreads=[0.2, 0.2, 0.4, 0.6], costs={"spread": 0, "use_data_spread": True})
    t = only_trade(res)
    assert (t["entry_price"], t["stop"], t["target"], t["size"]) == pytest.approx((101.2, 99.2, 105.2, 5.0))
    assert t["exit_reason"] == "stop" and t["exit_price"] == pytest.approx(99.2)
    assert t["pnl"] == pytest.approx(-10.0) and t["spread_cost"] == pytest.approx(2.5)
    # entry candle marked at its bid close: (101.2 - 0.2 - 101.2) x 5 = -1
    assert list(res.equity) == pytest.approx([1000, 1000, 999, 990])


def test_short_target_needs_the_ask_to_reach_it():
    # Sell at the bid 99 - 0.2 = 98.8: stop 100.8, target 94.8, size 5. Candle 3's mid low 94.7
    # is past the target but its ask low is 94.9: no fill. Candle 4 (spread 0.2): ask low
    # 94.5 + 0.1 = 94.6 reaches it -> out at 94.8. P&L (98.8 - 94.8) x 5 = £20; spread (0.2 + 0.1) x 5.
    res = run([FLAT, DOWN, SHORT_ENTRY, (99, 99.2, 94.7, 95), (95, 95.2, 94.5, 94.6)],
              spreads=[0.2, 0.2, 0.4, 0.4, 0.2], costs={"spread": 0, "use_data_spread": True}, **SHORT)
    t = only_trade(res)
    assert t["entry_price"] == pytest.approx(98.8) and t["exit_time"] == "2025-01-06 04:00:00+00:00"
    assert t["exit_reason"] == "target" and t["exit_price"] == pytest.approx(94.8)
    assert t["pnl"] == pytest.approx(20.0) and t["spread_cost"] == pytest.approx(1.5)
    # marked at the ask: candle 2 (99.2 - 98.8) x -5 = -2; candle 3 (95.2 - 98.8) x -5 = +18
    assert list(res.equity) == pytest.approx([1000, 1000, 998, 1018, 1020])


@pytest.mark.parametrize("side, rows, fill, exit_px", [
    # Long, 0.05 points slippage: buy at 101.05, target 105.05. Next candle opens at 106,
    # beyond the target: a target is a limit order, so it fills at that better open, no slippage.
    (LONG, [FLAT, SIGNAL, ENTRY, (106, 106.5, 105.5, 106)], 101.05, 106),
    # Short: sell at 98.95, target 94.95; next candle opens at 94 -> out at 94.
    (SHORT, [FLAT, DOWN, SHORT_ENTRY, (94, 94.5, 93.5, 94)], 98.95, 94),
])
def test_gap_through_the_target_fills_at_the_better_open(side, rows, fill, exit_px):
    # P&L 4.95 points x 5 = £24.75 = 2.475R; slippage paid only on the entry, 0.05 x 5 = £0.25.
    t = only_trade(run(rows, costs={**NO_COSTS, "slippage": 0.05}, **side))
    assert t["entry_price"] == pytest.approx(fill) and t["size"] == pytest.approx(5)
    assert t["exit_reason"] == "target" and t["exit_price"] == pytest.approx(exit_px)
    assert t["pnl"] == pytest.approx(24.75) and t["r_multiple"] == pytest.approx(2.475)
    assert t["slippage_cost"] == pytest.approx(0.25)


BOTH = (101.2, 105.5, 98.5, 101)  # reaches the stop (99) and the target (105) in one candle


def test_finer_candle_opening_past_the_stop_fills_at_that_open():
    # Rule: when one candle reaches both levels, the finer candles inside it are walked in order
    # and the first level reached wins, at that finer candle's open if it opened beyond it.
    # Here the second 15-minute candle opens at 98.6, under the 99 stop: out at 98.6, not 99.
    # P&L (98.6 - 101) x 5 = -£12 = -1.2R.
    fine = make_bars([(101.2, 101.5, 100.8, 101), (98.6, 99.5, 98.5, 99.4),
                      (99.4, 105.5, 99.3, 105.2), (105.2, 105.3, 100.9, 101)], start="2025-01-06 03:00", freq="15min")
    t = only_trade(run([FLAT, SIGNAL, ENTRY, BOTH], fine=fine))
    assert t["exit_reason"] == "stop" and t["exit_price"] == pytest.approx(98.6)
    assert t["pnl"] == pytest.approx(-12.0) and t["r_multiple"] == pytest.approx(-1.2)


def test_entry_candle_reaching_both_levels_uses_finer_candles_too():
    # The entry candle itself reaches 105.5 and 98.5. Its first 15 minutes reach 105.5 before
    # anything goes near 99: target, +£20. With no finer candles the stop is assumed: -£10.
    entry_both = (101, 105.5, 98.5, 100)
    fine = make_bars([(101, 105.5, 100.8, 105), (105, 105.2, 98.5, 99), (99, 100, 98.6, 99.5),
                      (99.5, 100.2, 99.4, 100)], start="2025-01-06 02:00", freq="15min")
    with_fine = only_trade(run([FLAT, SIGNAL, entry_both], fine=fine))
    assert with_fine["exit_reason"] == "target" and with_fine["pnl"] == pytest.approx(20.0)
    without = only_trade(run([FLAT, SIGNAL, entry_both]))
    assert without["exit_reason"] == "stop" and without["pnl"] == pytest.approx(-10.0)


def test_long_costs_over_two_nights_with_a_target_exit():
    # Daily candles. Spread 0.2, slippage 0.05 points, commission £1 + 0.1% per fill, funding
    # 3.65% a year (0.01% a night), borrow 7.3% (shorts only, so not charged here).
    # Entry 8 Jan: buy at 101 + 0.1 + 0.05 = 101.15; stop 99.15, target 105.15, size 5.
    #   commission 1 + 101.15 x 5 x 0.1% = 1.50575.
    # Nights of 8 and 9 Jan: 101 x 5 x 0.01% = 0.0505, then 102 x 5 x 0.01% = 0.051.
    # 10 Jan: bid high 105.4 reaches the target: out at 105.15 (a limit: no slippage).
    #   commission 1 + 105.15 x 5 x 0.1% = 1.52575.
    # P&L (105.15 - 101.15) x 5 = 20 - 0.1015 funding - 3.0315 commission = £16.867 (1.6867R).
    res = run([FLAT, SIGNAL, (101, 101.5, 100.5, 101), (101, 103, 100.8, 102), (102, 105.5, 101.8, 105.3)],
              freq="1D", **DAILY,
              costs={"spread": 0.2, "use_data_spread": False, "slippage": 0.05, "commission": 1,
                     "commission_pct": 0.1, "funding_pct": 3.65, "borrow_pct": 7.3})
    t = only_trade(res)
    assert (t["entry_price"], t["stop"], t["target"]) == pytest.approx((101.15, 99.15, 105.15))
    assert t["exit_reason"] == "target" and t["exit_price"] == pytest.approx(105.15)
    assert (t["funding"], t["borrow"], t["financing"]) == pytest.approx((0.1015, 0.0, 0.1015))
    assert t["commission"] == pytest.approx(3.0315)
    assert t["slippage_cost"] == pytest.approx(0.25) and t["spread_cost"] == pytest.approx(1.0)
    assert t["pnl"] == pytest.approx(16.867) and t["r_multiple"] == pytest.approx(1.6867)
    # marked at the bid: 8 Jan (100.9 - 101.15) x 5 = -1.25; 9 Jan (101.9 - 101.15) x 5 = +3.75
    assert list(res.equity) == pytest.approx([1000, 1000, 1000 - 1.50575 - 0.0505 - 1.25,
                                              1000 - 1.50575 - 0.1015 + 3.75, 1016.867])


EXIT_RULE = {"stop": {"points": 2}, "target": {"points": 4}, "when": {"left": "close", "op": "<", "right": 100}}


@pytest.mark.parametrize("rows, exit_, reason, exit_px", [
    # exit rule: candle 3 closes under 100 -> out at candle 4's open 99.9, less 0.05 = 99.85
    ([FLAT, SIGNAL, ENTRY, (101.2, 101.4, 99.6, 99.8), (99.9, 100, 99.5, 99.7)], EXIT_RULE, "exit_rule", 99.85),
    # time limit of 2 candles: out at candle 3's close 101.6, less 0.05 = 101.55
    ([FLAT, SIGNAL, ENTRY, (101.2, 101.8, 100.9, 101.6)],
     {"stop": {"points": 2}, "target": {"points": 4}, "max_bars": 2}, "max_bars", 101.55),
    # end of the data: out at the last close 102, less 0.05 = 101.95
    ([FLAT, SIGNAL, ENTRY, (101.2, 102.2, 100.9, 102)], {"stop": {"points": 2}, "target": {"points": 4}},
     "end_of_data", 101.95),
])
def test_every_market_exit_pays_slippage(rows, exit_, reason, exit_px):
    # buy at 101.05 (size 5); P&L (exit - 101.05) x 5; slippage 0.05 in and 0.05 out = £0.50
    t = only_trade(run(rows, exit=exit_, costs={**NO_COSTS, "slippage": 0.05}))
    assert t["exit_reason"] == reason and t["exit_price"] == pytest.approx(exit_px)
    assert t["pnl"] == pytest.approx((exit_px - 101.05) * 5) and t["slippage_cost"] == pytest.approx(0.5)


def test_rate_changing_during_a_trade_converts_each_amount_when_it_happens():
    # USD market, GBP account, daily candles. Rates (£ per $): 0.8, 0.8, 0.8, 0.75, 0.5.
    # Entry 8 Jan at 101 with the rate 0.8: size £10 / (2 x 0.8) = 6.25.
    #   commission £1 + 101 x 6.25 x 0.8 x 0.1% = 1.505 (the £1 is account money).
    # Funding 0.01% a night on the value in £ at that night's rate:
    #   8 Jan 101 x 6.25 x 0.8 = 505 -> 0.0505; 9 Jan 101.5 x 6.25 x 0.75 = 475.78125 -> 0.047578125.
    # 10 Jan (rate 0.5): target 105: 4 x 6.25 = $25 = £12.50; commission 1 + 105 x 6.25 x 0.5 x 0.1%
    #   = 1.328125. P&L 12.5 - 0.098078125 - 2.833125 = £9.568796875; risked £10 at entry -> 0.9569R.
    usd = {"account_size": 1000, "risk_per_trade_pct": 1, "point_value": 1, "size_step": 0.01, "min_size": 0.01,
           "size_currency": "market", "market_currency": "USD", "currency": "GBP"}
    res = run([FLAT, SIGNAL, (101, 101.5, 100.5, 101), (101, 102, 100.5, 101.5), (101.5, 106, 101.5, 105)],
              freq="1D", fx=[0.8, 0.8, 0.8, 0.75, 0.5], risk=usd, **DAILY,
              costs={**NO_COSTS, "commission": 1, "commission_pct": 0.1, "funding_pct": 3.65})
    t = only_trade(res)
    assert t["size"] == pytest.approx(6.25) and (t["fx_entry"], t["fx"]) == pytest.approx((0.8, 0.5))
    assert t["funding"] == pytest.approx(0.098078125) and t["commission"] == pytest.approx(2.833125)
    assert t["pnl"] == pytest.approx(9.568796875) and t["r_multiple"] == pytest.approx(0.9568796875)
    # 9 Jan marked at that day's rate: (101.5 - 101) x 6.25 x 0.75 = +2.34375
    assert list(res.equity) == pytest.approx([1000, 1000, 1000 - 1.505 - 0.0505,
                                              1000 - 1.505 - 0.098078125 + 2.34375, 1009.568796875])


# =========================================================================== properties

PROPS = settings(RUNS, max_examples=100)


@st.composite
def rich_cases(draw):
    """Settings the A1 generator leaves out: rule-level stops, volatility slippage, loss limits,
    margin and close-out, and a market priced in dollars with a moving exchange rate."""
    tf, df = draw(prices())
    ref = float(df["close"].iloc[0])
    direction = draw(st.sampled_from(["long", "short"]))
    stop = draw(st.one_of(
        st.builds(lambda v: {"percent": v}, st.floats(0.05, 8)),
        st.builds(lambda v: {"atr": v, "atr_period": 5}, st.floats(0.2, 4)),
        st.just({"level": {"indicator": "lowest" if direction == "long" else "highest", "period": 5, "shift": 1}}),
    ))
    exit_ = {"stop": stop, "target": {"r_multiple": draw(st.floats(0.3, 5))}}
    if draw(st.booleans()):
        exit_["max_bars"] = draw(st.integers(1, 20))
    risk = {"account_size": draw(st.floats(500, 1_000_000)), "risk_per_trade_pct": draw(st.floats(0.1, 10)),
            "point_value": draw(st.floats(0.05, 20)), "size_step": 0.01, "min_size": 0.01,
            "compound": draw(st.booleans())}
    if draw(st.booleans()):
        risk["max_daily_loss_pct"] = draw(st.floats(0.5, 10))
    if draw(st.booleans()):
        risk["max_drawdown_pct"] = draw(st.floats(1, 40))
    if draw(st.booleans()):
        risk.update(margin_pct=draw(st.floats(1, 50)), max_margin_pct=draw(st.floats(5, 100)),
                    close_out_pct=draw(st.floats(10, 90)))
    if draw(st.booleans()):
        risk.update(size_currency="market", market_currency="USD", currency="GBP")
        rng = np.random.default_rng(draw(st.integers(0, 2**32 - 1)))
        df = df.assign(fx=0.8 * np.exp(np.cumsum(rng.normal(0, 0.01, len(df)))))
    sma = {"indicator": "sma", "period": draw(st.integers(2, 10))}
    s = strategy_from_dict({
        "name": "Rich", "market": {"symbol": "TEST", "label": "Test", "timeframe": tf}, "direction": direction,
        "entry": {"left": "close", "op": draw(st.sampled_from([">", "<", "crosses_above", "crosses_below"])), "right": sma},
        "exit": exit_, "risk": risk,
        "costs": {"spread": ref * draw(st.floats(0, 0.004)), "use_data_spread": draw(st.booleans()),
                  "slippage_atr_pct": draw(st.floats(0, 30)), "funding_pct": draw(st.floats(0, 10)),
                  "borrow_pct": draw(st.floats(0, 10)), "commission": draw(st.floats(0, 5))},
    })
    return s, df


def _trades_equal(a: pd.DataFrame, b: pd.DataFrame):
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_exact=False, rtol=1e-9)


@PROPS
@given(st.one_of(cases().map(lambda c: c[:2]), rich_cases()), st.floats(0.2, 0.95))
def test_future_candles_never_change_the_past(case, share):
    """Run on the first k candles, then on all of them: every signal, every trade that closed
    inside the first k candles and the balance up to candle k-1 must be identical. (Candle
    k-1 itself differs only because the shorter test closes what's open at its last close.)"""
    s, df = case
    k = min(len(df) - 1, max(6, int(len(df) * share)))
    head = df.iloc[:k]
    pf, ph = s.prepare(df), s.prepare(head)
    assert np.array_equal(pf.entry[:k], ph.entry) and np.array_equal(pf.exit[:k], ph.exit)
    for key, arr in ph.refs.items():
        np.testing.assert_allclose(pf.refs[key][:k], arr, rtol=1e-12, equal_nan=True)
    full, part = run_backtest(s, df), run_backtest(s, head)
    pt, ft = part.trades, full.trades
    done = pt[pt["exit_reason"] != "end_of_data"] if len(pt) else pt
    assert len(ft) >= len(done)
    if len(done):
        _trades_equal(done, ft.iloc[:len(done)])
    if len(pt) > len(done):  # still open at the cut: entered exactly as in the full run
        a, b = pt.iloc[-1], ft.iloc[len(done)]
        assert (a["entry_time"], a["signal_time"]) == (b["entry_time"], b["signal_time"])
        assert (a["entry_price"], a["size"], a["stop"]) == pytest.approx((b["entry_price"], b["size"], b["stop"]), rel=1e-12)
    np.testing.assert_allclose(part.equity.to_numpy()[:-1], full.equity.to_numpy()[:k - 1], rtol=1e-12)


CAUSAL = [n for n in indicators.REGISTRY if not n.startswith("opening_range")]


@settings(RUNS, max_examples=60)
@given(prices(), st.integers(2, 12), st.floats(0.1, 0.9))
def test_indicators_only_use_the_current_and_earlier_candles(data, period, share):
    _, df = data
    k = max(3, int(len(df) * share))
    for name in CAUSAL:
        params = {p: (period if p != "slow" else period + 3) for p in indicators.params_for(name)
                  if p in ("period", "fast", "slow", "signal", "atr_period")}
        full = indicators.compute(df, name, **params).to_numpy(dtype=float)
        head = indicators.compute(df.iloc[:k], name, **params).to_numpy(dtype=float)
        np.testing.assert_allclose(full[:k], head, rtol=1e-9, atol=1e-12, equal_nan=True, err_msg=name)


def test_opening_range_is_only_known_after_it_ends():
    # regular 30-minute candles, New York session: the 09:30-10:00 range is known from the
    # 10:00 candle on, and later candles never change it (the range's own bar length is taken
    # from the data's usual spacing, so this holds for evenly spaced data)
    idx = pd.date_range("2025-01-06 14:00", periods=40, freq="30min", tz="UTC")  # 09:00 New York
    df = pd.DataFrame({"open": 100.0, "high": np.arange(40) + 101.0, "low": 99.0, "close": 100.0,
                       "volume": 1.0}, index=idx)
    high = indicators.compute(df, "opening_range_high")
    assert high.iloc[:2].isna().all() and high.iloc[2] == 102.0      # 09:30 candle's high, from 10:00
    np.testing.assert_array_equal(indicators.compute(df.iloc[:10], "opening_range_high").to_numpy(),
                                  high.iloc[:10].to_numpy())


@settings(RUNS, max_examples=80)
@given(st.integers(0, 2**32 - 1), st.integers(15, 60), st.integers(5, 40))
def test_exchange_rate_is_the_latest_close_known_at_each_open(seed, days, cut):
    rng = np.random.default_rng(seed)
    pair_idx = pd.date_range("2025-01-01", periods=days, freq="1D", tz="UTC")
    pair_idx = pair_idx[rng.random(days) > 0.25].union(pair_idx[:1])          # some days missing
    pair = pd.DataFrame({"close": 1.2 + rng.normal(0, 0.01, len(pair_idx)).cumsum()}, index=pair_idx)
    candles = pd.date_range(pair_idx[0], pair_idx[-1] + pd.Timedelta(days=2), freq="3h")
    got = fxrates.rates(candles, pair, invert=False)
    ends = pair.index + pd.Timedelta(days=1)                                  # a daily close is known at its end
    for t, rate in zip(candles, got):
        known = pair["close"][ends <= t]
        assert rate == (known.iloc[-1] if len(known) else pair["close"].iloc[0])
    # later rates never change earlier ones
    m = min(len(pair) - 1, max(1, cut * len(pair) // 40))
    early = candles < pair.index[m] + pd.Timedelta(days=1)
    np.testing.assert_array_equal(fxrates.rates(candles[early], pair.iloc[:m], invert=False), got[early])
    np.testing.assert_allclose(fxrates.rates(candles, pair, invert=True), 1 / got)


@settings(RUNS, max_examples=60)
@given(st.sampled_from(["2024-03-10", "2024-11-03", "2025-03-09", "2025-11-02"]), st.integers(-30 * 24, 5 * 24),
       st.integers(3, 30), st.booleans(), st.floats(0, 0.3), st.sampled_from(["4h", "1d"]), st.integers(0, 2**32 - 1))
def test_new_york_candles_hold_exactly_their_own_hours(change, offset, days, weekdays_only, missing, tf, seed):
    # start up to 30 days before or 5 days after a New York clock change
    start = pd.Timestamp(change, tz="UTC") + pd.Timedelta(hours=offset)
    raw = _hourly(start, start + pd.Timedelta(days=days), weekdays_only)
    rng = np.random.default_rng(seed)
    raw = raw[rng.random(len(raw)) >= missing]
    if len(raw) < 3:
        return
    raw = raw.assign(open=rng.normal(100, 1, len(raw)), close=rng.normal(100, 1, len(raw)),
                     volume=rng.integers(1, 9, len(raw)).astype(float))
    raw = raw.assign(high=raw[["open", "close"]].max(axis=1) + 0.5, low=raw[["open", "close"]].min(axis=1) - 0.5)
    out = data_mod.resample_ohlcv(raw, tf, "new_york")
    if out is raw:
        return
    local = out.index.tz_convert(NY)
    hours = {17} if tf == "1d" else {1, 5, 9, 13, 17, 21}
    assert set(local.hour) <= hours and set(local.minute) == {0}
    # each hourly candle goes to the latest candle starting at or before it, and lies inside
    # that candle's hours on the New York clock
    pos = np.searchsorted(out.index.asi8, raw.index.asi8, "right") - 1
    assert (pos >= 0).all()
    length = timeframes.to_timedelta(tf)
    wall = raw.index.tz_convert(NY).tz_localize(None)
    start_wall = local.tz_localize(None)[pos]
    assert ((wall >= start_wall) & (wall < start_wall + length)).all()
    grouped = raw.groupby(pos).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    np.testing.assert_allclose(out[["open", "high", "low", "close", "volume"]].to_numpy(), grouped.to_numpy())


@settings(max_examples=300, deadline=None)
@given(st.floats(1, 1e7), st.floats(0.01, 10), st.floats(1e-4, 1e4), st.floats(1e-3, 100),
       st.sampled_from([0, 0.001, 0.01, 0.1, 1.0]), st.floats(0, 5), st.one_of(st.none(), st.floats(0.01, 1e6)))
def test_position_size_never_risks_more_than_asked(equity, risk_pct, dist, pv, step, min_size, max_size):
    size = position_size(equity, risk_pct, dist, pv, step, min_size, max_size)
    assert size >= 0
    if size:
        assert size * dist * pv <= equity * risk_pct / 100 * (1 + 1e-8)       # rounded down, never up
        assert size >= min_size and (max_size is None or size <= max_size * (1 + 1e-12))
        if step:
            assert abs(size / step - round(size / step)) < 1e-6 * max(1.0, size / step)
