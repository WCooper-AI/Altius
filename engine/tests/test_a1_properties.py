"""Session A1: property tests. Hypothesis makes random price series (gaps at the open,
missing candles, per-candle spreads) and random settings (direction, stops, targets,
time limits, exit rules, every cost, sizing), and every run must pass the invariant
checker, keep its cash balance right candle by candle, and repeat exactly."""

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradealerts import invariants
from tradealerts.backtest import run_backtest, spreads_for
from tradealerts.simulator import Simulator
from tradealerts.strategy import strategy_from_dict

FREQ = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}
RUNS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@st.composite
def prices(draw):
    """A random walk with jumps at the open, some candles missing, and maybe a spread column."""
    tf = draw(st.sampled_from(sorted(FREQ)))
    n = draw(st.integers(20, 160))
    rng = np.random.default_rng(draw(st.integers(0, 2**32 - 1)))
    price, vol = draw(st.floats(5, 20_000)), draw(st.floats(0.0005, 0.04))
    gap_chance, missing = draw(st.floats(0, 0.3)), draw(st.floats(0, 0.25))
    spreads = draw(st.sampled_from(["none", "some", "all"]))
    o, c = np.empty(n), np.empty(n)
    for i in range(n):
        if rng.random() < gap_chance:
            price *= np.exp(rng.normal(0, vol * 4))
        o[i] = price
        price *= np.exp(rng.normal(0, vol))
        c[i] = price
    wick = np.abs(rng.normal(0, vol / 2, (2, n)))
    start = pd.Timestamp("2025-01-06", tz="UTC") + pd.Timedelta(FREQ[tf]) * draw(st.integers(0, 40))
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * np.exp(wick[0]),
                       "low": np.minimum(o, c) * np.exp(-wick[1]), "close": c, "volume": 0.0},
                      index=pd.date_range(start, periods=n, freq=FREQ[tf]))
    keep = rng.random(n) >= missing
    keep[:5] = True
    df = df[keep].copy()
    if spreads != "none":
        sp = df["close"].to_numpy() * np.abs(rng.normal(0, vol / 5, len(df)))
        if spreads == "some":
            sp[rng.random(len(df)) < 0.5] = np.nan
        df["spread"] = sp
    return tf, df


def maybe(strategy):
    return st.one_of(st.none(), strategy)


@st.composite
def strategies(draw, tf, ref):
    """Random but valid settings; money and point amounts scaled to the price ``ref``."""
    sma = lambda k: {"indicator": "sma", "period": k}  # noqa: E731
    step = draw(st.sampled_from([0.001, 0.01, 0.1, 1.0]))
    min_size = step * draw(st.integers(1, 3))
    exit_ = {"stop": draw(st.one_of(st.builds(lambda v: {"percent": v}, st.floats(0.05, 8)),
                                    st.builds(lambda v: {"atr": v, "atr_period": 5}, st.floats(0.2, 4))))}
    target = draw(maybe(st.one_of(st.builds(lambda v: {"r_multiple": v}, st.floats(0.3, 6)),
                                  st.builds(lambda v: {"percent": v}, st.floats(0.05, 10)))))
    if target:
        exit_["target"] = target
    if (bars := draw(maybe(st.integers(1, 25)))) is not None:
        exit_["max_bars"] = bars
    if (k := draw(maybe(st.integers(2, 12)))) is not None:
        exit_["when"] = {"left": "close", "op": draw(st.sampled_from([">", "<"])), "right": sma(k)}
    max_size = draw(maybe(st.floats(1, 10_000)))
    return strategy_from_dict({
        "name": "Random",
        "market": {"symbol": "TEST", "label": "Test", "timeframe": tf},
        "direction": draw(st.sampled_from(["long", "short"])),
        "entry": {"left": "close", "op": draw(st.sampled_from([">", "<"])), "right": sma(draw(st.integers(2, 10)))},
        "exit": exit_,
        "risk": {"account_size": draw(st.floats(100, 1_000_000)), "risk_per_trade_pct": draw(st.floats(0.05, 10)),
                 "point_value": draw(st.floats(0.01, 50)), "size_step": step, "min_size": min_size,
                 "max_size": None if max_size is None else min_size * max_size,
                 "compound": draw(st.booleans())},
        "costs": {"spread": ref * draw(st.floats(0, 0.005)), "use_data_spread": draw(st.booleans()),
                  "funding_pct": draw(st.floats(0, 20)), "borrow_pct": draw(st.floats(0, 20)),
                  "commission": draw(st.floats(0, 10)), "commission_pct": draw(st.floats(0, 0.5)),
                  "slippage": ref * draw(st.floats(0, 0.003)), "slippage_pct": draw(st.floats(0, 0.3))},
    })


@st.composite
def cases(draw):
    tf, df = draw(prices())
    return draw(strategies(tf, float(df["close"].iloc[0]))), df, draw(maybe(st.integers(50, 90)))


@RUNS
@given(cases())
def test_random_backtests_keep_every_rule(case):
    s, df, split = case
    res = run_backtest(s, df, split_pct=split)
    assert invariants.check(res) == []
    assert invariants.same_result(res, run_backtest(s, df, split_pct=split)) == []


@RUNS
@given(cases())
def test_random_runs_keep_the_cash_balance_candle_by_candle(case):
    s, df, _ = case
    prepared, sim = s.prepare(df), Simulator(s)
    spreads = spreads_for(s, df)
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    start, closed, trades = s.risk.account_size, 0.0, []
    for i, t in enumerate(df.index):
        for ev in sim.step(t, o[i], h[i], l[i], c[i], bool(prepared.entry[i]), bool(prepared.exit[i]),
                           prepared.refs_at(i), spread=None if spreads is None else spreads[i],
                           values=prepared.values_at(i)):
            if ev.kind == "closed":
                closed += ev.data["pnl"]
                trades.append(ev.data)
        assert invariants.check_state(sim, start, closed) == []
    for ev in sim.finish(df.index[-1], c[-1]):
        closed += ev.data["pnl"]
        trades.append(ev.data)
    assert invariants.check_state(sim, start, closed) == []
    # the backtest gives the same trades as stepping through by hand
    pd.testing.assert_frame_equal(pd.DataFrame(trades), run_backtest(s, df).trades)
