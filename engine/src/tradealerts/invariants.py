"""Rules every simulated result must obey, checked from the outside after a run.

``check(res)`` lists what's broken in a backtest result (an empty list means every
rule holds). The tests run it after hand-worked scenarios and random property tests,
so any engine change that breaks the books shows up; ``check_state`` does the same for
a simulator between candles. The simulator also refuses the impossible cases as they
happen (``InvariantError``).

1. A trade opens after its signal and closes no earlier than it opens.
2. Trades never overlap or repeat: each opens no earlier than the last one closed.
3. Every position size is a positive number.
4. The balance reconciles: each trade's ``equity_after`` is the start plus all P&L so
   far, the equity curve equals it whenever no trade is open (open trades add their
   unrealised P&L), and the final balance is the start plus every trade's P&L.
5. Every fill has a real price and a time inside the data.
6. Costs reconcile: P&L is the price move minus financing and commission, financing
   is funding plus borrow, no cost is negative, and the reported totals add up.
7. Running the same inputs twice gives the same result (``same_result``).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from . import timeframes

TOL = 1e-6  # relative tolerance for money and prices
# exits that happen at a candle's close, so the trade's time is that candle's end
CLOSE_EXITS = ("max_bars", "end_of_data")
TOTALS = (("total_funding", "funding"), ("total_borrow", "borrow"), ("total_financing", "financing"),
          ("total_commission", "commission"), ("total_spread_cost", "spread_cost"),
          ("total_slippage", "slippage_cost"))


def _num(v) -> float:
    """A trade field as a float, with missing values as 0."""
    if v is None:
        return 0.0
    v = float(v)
    return 0.0 if math.isnan(v) else v


def _finite(v) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _same(a: float, b: float, scale: float = 1.0) -> bool:
    return abs(a - b) <= TOL * max(1.0, abs(a), abs(b), abs(scale))


def check(res) -> list[str]:
    """Everything wrong with a backtest result, both parts when it was split in two."""
    out = check_part(res.strategy, res.trades, res.equity, res.metrics)
    if res.oos_metrics is not None:
        out += [f"out-of-sample: {p}" for p in
                check_part(res.strategy, res.oos_trades, res.oos_equity, res.oos_metrics)]
    return out


def check_part(strategy, trades: pd.DataFrame, equity: pd.Series, metrics: dict) -> list[str]:
    """Everything wrong with one account's trades, equity curve and totals."""
    problems: list[str] = []
    sign, pv = strategy.sign, strategy.risk.point_value
    bar = timeframes.to_timedelta(strategy.market.timeframe)
    start = float(metrics["start_equity"])
    idx = equity.index
    eq = equity.to_numpy(dtype=float)
    if not np.isfinite(eq).all():
        problems.append("the equity curve has a missing or infinite value")
    rows = trades.to_dict("records") if len(trades) else []
    if int(metrics.get("trades", len(rows))) != len(rows):
        problems.append(f"{metrics.get('trades')} trades reported but {len(rows)} listed")

    realised = start
    open_mask = np.zeros(len(idx), dtype=bool)
    realised_at = np.full(len(idx), start)
    prev_exit = None
    for k, t in enumerate(rows, 1):
        tag = f"trade {k}"
        try:
            sig, ent, ext = (pd.Timestamp(t[c]) for c in ("signal_time", "entry_time", "exit_time"))
        except (KeyError, TypeError, ValueError):
            problems.append(f"{tag}: a signal, entry or exit time is missing or unreadable")
            continue
        # 1. order of events
        if not sig < ent:
            problems.append(f"{tag}: opened ({ent}) no later than its signal ({sig})")
        if ext < ent:
            problems.append(f"{tag}: closed ({ext}) before it opened ({ent})")
        # 2. one trade at a time
        if prev_exit is not None and ent < prev_exit:
            problems.append(f"{tag}: opened ({ent}) before the previous trade closed ({prev_exit})")
        prev_exit = ext
        # 5. real prices, times inside the data
        if not (idx[0] <= ent and ext <= idx[-1] + bar):
            problems.append(f"{tag}: a fill time is outside the data ({ent} to {ext})")
        if not all(_finite(t.get(c)) for c in ("entry_price", "exit_price", "stop")):
            problems.append(f"{tag}: an entry, exit or stop price isn't a real number")
            continue
        if t.get("target") is not None and not pd.isna(t["target"]) and not _finite(t["target"]):
            problems.append(f"{tag}: the target isn't a real number")
        # 3. size
        size = t.get("size")
        if not (_finite(size) and size > 0):
            problems.append(f"{tag}: size {size} isn't a positive number")
            continue
        # 6. costs
        funding, borrow, financing = _num(t.get("funding")), _num(t.get("borrow")), _num(t.get("financing"))
        commission = _num(t.get("commission"))
        if not _same(financing, funding + borrow):
            problems.append(f"{tag}: financing {financing} isn't funding {funding} plus borrow {borrow}")
        if t["entry_price"] > 0 and t["exit_price"] > 0:
            for c in ("funding", "borrow", "commission", "spread_cost", "slippage_cost"):
                if _num(t.get(c)) < 0:
                    problems.append(f"{tag}: {c} is negative ({t.get(c)})")
        move = (t["exit_price"] - t["entry_price"]) * sign * size * pv
        pnl = _num(t.get("pnl"))
        if not _same(pnl, move - financing - commission, move):
            problems.append(f"{tag}: P&L {pnl} isn't the price move {move} less financing {financing} "
                            f"and commission {commission}")
        # 4. the realised balance
        realised += pnl
        if not _same(_num(t.get("equity_after")), realised, start):
            problems.append(f"{tag}: balance after it is {t.get('equity_after')}, but start plus P&L is {realised}")
        exit_candle = ext - bar if t.get("exit_reason") in CLOSE_EXITS else ext
        a = int(idx.searchsorted(ent, "left"))
        b = int(idx.searchsorted(exit_candle, "left"))
        open_mask[a:b] = True
        realised_at[b:] += pnl

    flat = ~open_mask & np.isfinite(eq)
    gap = np.abs(eq[flat] - realised_at[flat])
    bad = gap > TOL * np.maximum(1.0, np.abs(realised_at[flat]))
    if bad.any():
        when = idx[flat][int(np.argmax(bad))]
        problems.append(f"with no trade open the equity curve isn't the realised balance (first at {when})")
    if not _same(float(metrics.get("end_equity", eq[-1])), realised, start):
        problems.append(f"final balance {metrics.get('end_equity')} isn't start plus all P&L ({realised})")
    for key, col in TOTALS:
        want = float(trades[col].sum()) if len(trades) and col in trades else 0.0
        if not _same(float(metrics.get(key, 0.0)), want):
            problems.append(f"{key} is {metrics.get(key)} but the trades add up to {want}")
    return problems


def check_state(sim, start: float, closed_pnl: float) -> list[str]:
    """Rules 3 and 4 between candles: the cash balance is the start plus closed P&L, less
    what the open trade has paid so far (entry commission, funding, borrow)."""
    problems = []
    p = sim.position
    paid = 0.0 if not p else _num(p.get("commission")) + _num(p.get("funding")) + _num(p.get("borrow"))
    want = start + closed_pnl - paid
    if not _same(sim.state["equity"], want, start):
        problems.append(f"cash balance {sim.state['equity']} isn't start plus closed P&L less costs paid ({want})")
    if p and not (_finite(p.get("size")) and p["size"] > 0):
        problems.append(f"open position size {p.get('size')} isn't a positive number")
    return problems


def same_result(a, b) -> list[str]:
    """How two results that should be identical (same inputs, run twice) differ."""
    diffs = []
    try:
        pd.testing.assert_frame_equal(a.trades, b.trades)
    except AssertionError as exc:
        diffs.append(f"trades differ: {str(exc).splitlines()[0]}")
    if not a.equity.equals(b.equity):
        diffs.append("equity curves differ")
    ma, mb = (json.dumps(r.metrics, sort_keys=True, default=str) for r in (a, b))
    if ma != mb:
        diffs.append("metrics differ")
    return diffs
