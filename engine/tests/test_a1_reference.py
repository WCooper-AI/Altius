"""Hand-worked reference scenarios: tiny price series where every fill, cost and balance
was calculated on paper. Any engine change that moves one of these numbers is a change
in what a backtest means, so it has to be deliberate.

Unless a scenario says otherwise: 1h candles from Mon 6 Jan 2025 00:00 UTC, £1,000
account, 1% risk (£10), point value 1, sizes in steps of 0.01, stop 2 points and
target 4 points from the fill, no costs.
"""

import pandas as pd
import pytest

from tradealerts import invariants
from tradealerts.backtest import run_backtest

from .conftest import make_bars, make_strategy

FLAT = (100, 100, 100, 100)
SIGNAL = (100, 100, 100, 101)        # closes above 100.5: enter at the next open
ENTRY = (101, 101.5, 100.5, 101.2)   # fills at 101, reaches neither stop nor target
NO_COSTS = {"spread": 0, "use_data_spread": False}


def run(rows, fine=None, freq="1h", **overrides):
    overrides.setdefault("entry", {"left": "close", "op": ">", "right": 100.5})
    overrides.setdefault("costs", NO_COSTS)
    s = make_strategy(**overrides)
    res = run_backtest(s, make_bars(rows, freq=freq), fine=fine)
    assert invariants.check(res) == []
    return res


def only_trade(res):
    assert len(res.trades) == 1
    return res.trades.iloc[0]


def test_long_target_with_spread():
    # spread 0.2 -> buy at 101 + 0.1 = 101.1; stop 99.1, target 105.1; size £10 / 2 = 5.
    # Candle 3: bid high 105.5 - 0.1 = 105.4 reaches 105.1, bid low 100.1 stays above 99.1.
    # P&L (105.1 - 101.1) x 5 = £20; spread paid (0.1 + 0.1) x 5 = £1.
    res = run([FLAT, SIGNAL, ENTRY, (101.2, 105.5, 100.2, 100.4), (100.4,) * 4, (100.4,) * 4],
              costs={"spread": 0.2, "use_data_spread": False})
    t = only_trade(res)
    assert (t["entry_price"], t["stop"], t["target"], t["size"]) == pytest.approx((101.1, 99.1, 105.1, 5.0))
    assert t["exit_reason"] == "target" and t["exit_price"] == pytest.approx(105.1)
    assert t["exit_time"] == "2025-01-06 03:00:00+00:00"
    assert t["pnl"] == pytest.approx(20.0) and t["r_multiple"] == pytest.approx(2.0)
    assert t["spread_cost"] == pytest.approx(1.0) and t["bars_held"] == 1
    # on the entry candle the open trade is worth (101.2 - 0.1 - 101.1) x 5 = 0
    assert list(res.equity) == pytest.approx([1000, 1000, 1000, 1020, 1020, 1020])


def test_long_gap_through_stop_with_percent_slippage():
    # slippage 0.1% of the fill price: buy at 101 + 0.101 = 101.101, stop 99.101, size 5.
    # Candle 3 opens at 98, below the stop: out at that open, less 98 x 0.1% = 0.098 -> 97.902.
    # P&L (97.902 - 101.101) x 5 = -£15.995; slippage (0.101 + 0.098) x 5 = £0.995.
    res = run([FLAT, SIGNAL, (101, 101.5, 100.5, 101), (98, 98.5, 97.5, 98), (98,) * 4],
              costs={**NO_COSTS, "slippage_pct": 0.1})
    t = only_trade(res)
    assert t["entry_price"] == pytest.approx(101.101) and t["size"] == pytest.approx(5.0)
    assert t["exit_reason"] == "stop" and t["exit_price"] == pytest.approx(97.902)
    assert t["pnl"] == pytest.approx(-15.995) and t["r_multiple"] == pytest.approx(-1.5995)
    assert t["slippage_cost"] == pytest.approx(0.995)
    assert res.metrics["end_equity"] == pytest.approx(984.005)


def test_short_with_funding_borrow_commission_and_slippage():
    # Daily candles. Short entry when the close is under 99.5; spread 0.2, slippage 0.05
    # points, commission £1 + 0.1% of each fill, funding 3.65% and borrow 7.3% a year.
    # Entry: sell at 99 - 0.1 - 0.05 = 98.85; stop 100.85, target 94.85; size £10 / 2 = 5.
    #   commission 1 + 98.85 x 5 x 0.1% = £1.49425.
    # Two nights held (8 and 9 Jan, 22:00 UTC), each on 99 x 5 = £495:
    #   funding 495 x 3.65% / 365 = £0.0495 a night, borrow 495 x 7.3% / 365 = £0.099 a night.
    # 10 Jan opens at 101.5: ask 101.6 is past the stop, so out there plus 0.05 -> 101.65.
    #   commission 1 + 101.65 x 5 x 0.1% = £1.50825.
    # P&L (98.85 - 101.65) x 5 = -14, less financing 0.297 and commission 3.0025 = -£17.2995.
    res = run([FLAT, (100, 100, 99, 99), (99, 99.5, 98.5, 99), (99, 99.5, 98.5, 99),
               (101.5, 102, 101, 101.5), (101.5,) * 4], freq="1D", direction="short",
              market={"symbol": "TEST", "label": "Test", "timeframe": "1d"},
              entry={"left": "close", "op": "<", "right": 99.5},
              costs={"spread": 0.2, "use_data_spread": False, "slippage": 0.05, "commission": 1,
                     "commission_pct": 0.1, "funding_pct": 3.65, "borrow_pct": 7.3})
    t = only_trade(res)
    assert (t["entry_price"], t["stop"], t["target"], t["size"]) == pytest.approx((98.85, 100.85, 94.85, 5.0))
    assert t["exit_reason"] == "stop" and t["exit_price"] == pytest.approx(101.65)
    assert t["funding"] == pytest.approx(0.099) and t["borrow"] == pytest.approx(0.198)
    assert t["financing"] == pytest.approx(0.297)
    assert t["commission"] == pytest.approx(3.0025)
    assert t["spread_cost"] == pytest.approx(1.0) and t["slippage_cost"] == pytest.approx(0.5)
    assert t["pnl"] == pytest.approx(-17.2995) and t["r_multiple"] == pytest.approx(-1.72995)
    # open trade marked at the ask: (99.1 - 98.85) x -5 = -1.25 on top of costs taken so far
    assert list(res.equity) == pytest.approx([1000, 1000, 1000 - 1.49425 - 0.1485 - 1.25,
                                              1000 - 1.49425 - 0.297 - 1.25, 982.7005, 982.7005])
    m = res.metrics
    assert (m["total_funding"], m["total_borrow"], m["total_financing"]) == pytest.approx((0.099, 0.198, 0.297))
    assert m["total_commission"] == pytest.approx(3.0025)


def test_exit_rule_closes_at_next_open():
    # Candle 3 closes under 100: out at candle 4's open, 99.9. P&L (99.9 - 101) x 5 = -£5.50.
    res = run([FLAT, SIGNAL, ENTRY, (101.2, 101.4, 99.6, 99.8), (99.9, 100, 99.5, 99.7)],
              exit={"stop": {"points": 2}, "target": {"points": 4},
                    "when": {"left": "close", "op": "<", "right": 100}})
    t = only_trade(res)
    assert t["exit_reason"] == "exit_rule" and t["exit_time"] == "2025-01-06 04:00:00+00:00"
    assert t["exit_price"] == pytest.approx(99.9) and t["pnl"] == pytest.approx(-5.5)


def test_time_limit_closes_at_the_candle_close():
    # max_bars 2: held on candles 2 and 3, out at candle 3's close 101.6 (its end, 04:00).
    # P&L (101.6 - 101) x 5 = £3. Candle 3's close is a new signal, but there's no next candle.
    res = run([FLAT, SIGNAL, ENTRY, (101.2, 101.8, 100.9, 101.6)],
              exit={"stop": {"points": 2}, "target": {"points": 4}, "max_bars": 2})
    t = only_trade(res)
    assert t["exit_reason"] == "max_bars" and t["exit_time"] == "2025-01-06 04:00:00+00:00"
    assert t["exit_price"] == pytest.approx(101.6) and t["pnl"] == pytest.approx(3.0) and t["bars_held"] == 2


def test_end_of_data_closes_at_the_last_close():
    # still open when the data ends: closed at the last close, 102. P&L (102 - 101) x 5 = £5.
    t = only_trade(run([FLAT, SIGNAL, ENTRY, (101.2, 102.2, 100.9, 102)]))
    assert t["exit_reason"] == "end_of_data" and t["exit_time"] == "2025-01-06 04:00:00+00:00"
    assert t["exit_price"] == pytest.approx(102) and t["pnl"] == pytest.approx(5.0)


BOTH = (101.2, 105.5, 98.5, 101)  # reaches the stop (99) and the target (105) in one candle


def test_stop_and_target_in_one_candle_assumes_the_stop():
    # no finer candles: the stop is assumed first. P&L (99 - 101) x 5 = -£10, -1R.
    t = only_trade(run([FLAT, SIGNAL, ENTRY, BOTH]))
    assert t["exit_reason"] == "stop" and t["pnl"] == pytest.approx(-10.0) and t["r_multiple"] == pytest.approx(-1.0)


def test_stop_and_target_in_one_candle_uses_finer_candles():
    # the first 15-minute candle reaches 105.5 before anything goes near 99: target first.
    # P&L (105 - 101) x 5 = £20.
    fine = make_bars([(101.2, 105.5, 101.0, 105.2), (105.2, 105.3, 100, 100.2),
                      (100.2, 100.3, 98.5, 99), (99, 101, 98.9, 101)], start="2025-01-06 03:00", freq="15min")
    t = only_trade(run([FLAT, SIGNAL, ENTRY, BOTH], fine=fine))
    assert t["exit_reason"] == "target" and t["exit_price"] == pytest.approx(105) and t["pnl"] == pytest.approx(20.0)


TWO_WINS = [FLAT, SIGNAL, ENTRY, (101.2, 105.5, 100.2, 100.4), (100.4, 100.4, 100.4, 101), ENTRY,
            (101.2, 105.5, 100.2, 100.4)]


@pytest.mark.parametrize("compound, second_size, end", [(True, 5.1, 1040.4), (False, 5.0, 1040.0)])
def test_compounding_sizes_the_next_trade_from_the_new_balance(compound, second_size, end):
    # first trade wins £20 -> £1,020. Compounding risks 1% of 1,020 over 2 points = 5.1;
    # without it, 1% of the starting 1,000 = 5. Second win: 4 points x size.
    res = run(TWO_WINS, risk={"account_size": 1000, "risk_per_trade_pct": 1, "point_value": 1,
                              "size_step": 0.01, "min_size": 0.01, "compound": compound})
    assert list(res.trades["size"]) == pytest.approx([5.0, second_size])
    assert res.metrics["end_equity"] == pytest.approx(end)
    assert pd.Timestamp(res.trades["entry_time"].iloc[1]) >= pd.Timestamp(res.trades["exit_time"].iloc[0])
