"""Reproducible benchmarks: a few example strategies backtested on the built-in Sample data
market, with their known-good metrics committed next to them, so a change to the simulator or
its maths shows up as a difference next to a result that's already been checked.

Layout, under a benchmarks folder (``benchmarks/`` at the repository root):

* ``strategies/<name>.yaml`` - a strategy file, market: symbol ``sample``, provider ``auto``.
* ``periods.json`` - ``{"<name>": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}}``, one fixed
  period per benchmark.
* ``expected/<name>.json`` - the metrics committed as "known good", keyed as in :data:`METRICS`.

The Sample data market (:mod:`tradealerts.sample`) is generated once from a fixed seed and
start date, so the candles between any two calendar dates in its past never change no matter
when or where a benchmark runs: the same strategy over the same period gives the same trades,
the same metrics, every time, on every machine. That's what makes the comparison meaningful -
a mismatch means the code changed, not that the market data did.

``tradealerts benchmark`` reruns every benchmark and reports any metric that no longer matches
its expected file; ``--write`` regenerates the expected files from the current code, for use
after a deliberate, reviewed change to the simulator or its statistics.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from .backtest import lookback_for_start, run_backtest
from .data import MarketProvider, closed_bars
from .strategy import load_strategy

#: metrics compared between a fresh run and the committed expected file (see
#: ``backtest.compute_metrics``); a small, stable subset rather than the whole dict, so a
#: harmless addition to the metrics dict doesn't make every benchmark "fail".
METRICS = (
    "trades", "end_equity", "total_return_pct", "max_drawdown_pct",
    "win_rate_pct", "profit_factor", "expectancy", "avg_r",
)


def _period(root: Path, name: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    periods = json.loads((root / "periods.json").read_text(encoding="utf-8"))
    if name not in periods:
        raise KeyError(f"No period for benchmark {name!r} in {root / 'periods.json'}")
    p = periods[name]
    return pd.Timestamp(p["from"], tz="UTC"), pd.Timestamp(p["to"], tz="UTC")


def run_one(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Backtest one benchmark strategy file over [``start``, ``end``) and return the metrics
    listed in :data:`METRICS`. Loads candles directly from the Sample data market, so it needs
    no workspace config."""
    s = load_strategy(path)
    lookback = lookback_for_start(s.market.timeframe, start, s.warmup_candles)
    bars, fine = MarketProvider({}, None).fetch_bars(s.market.symbol, s.market.timeframe, lookback,
                                                       s.market.candle_alignment)
    bars = closed_bars(bars[bars.index < end], s.market.timeframe)
    if bars.empty or bars.index[-1] < start:
        raise ValueError(f"No Sample data candles for {path.name} between {start.date()} and {end.date()}")
    res = run_backtest(s, bars, fine, start=start)
    return {k: res.metrics.get(k) for k in METRICS}


def run_all(root: Path) -> dict[str, dict]:
    """Every benchmark strategy under ``root``: ``{name: metrics}``."""
    out = {}
    for path in sorted((root / "strategies").glob("*.yaml")):
        start, end = _period(root, path.stem)
        out[path.stem] = run_one(path, start, end)
    return out


def _close(a, e) -> bool:
    """Whether a fresh value ``a`` still matches the committed expected value ``e``: exact for
    ints, bools and strings, a small tolerance for floats (library versions can differ in the
    last few digits), and ``None`` only matches ``None``."""
    if a is None or e is None:
        return a is None and e is None
    if isinstance(a, bool) or isinstance(e, bool):
        return a == e
    if isinstance(a, int) and isinstance(e, int):
        return a == e
    try:
        return math.isclose(float(a), float(e), rel_tol=1e-6, abs_tol=1e-6)
    except (TypeError, ValueError):
        return a == e


def compare(root: Path) -> list[str]:
    """Reruns every benchmark under ``root`` and lists what no longer matches its expected
    file, one line per metric; an empty list means every benchmark still matches."""
    problems = []
    for name, actual in sorted(run_all(root).items()):
        expected_path = root / "expected" / f"{name}.json"
        if not expected_path.exists():
            problems.append(f"{name}: no expected file at {expected_path}")
            continue
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        for key in METRICS:
            a, e = actual.get(key), expected.get(key)
            if not _close(a, e):
                problems.append(f"{name}.{key}: expected {e!r}, got {a!r}")
    return problems


def write_expected(root: Path) -> dict[str, dict]:
    """Reruns every benchmark under ``root`` and overwrites its expected file with the current
    metrics. Only for a deliberate, reviewed change to the simulator - not part of normal use."""
    results = run_all(root)
    for name, metrics in results.items():
        path = root / "expected" / f"{name}.json"
        path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return results
