"""Statistics behind the luck check and the optimiser.

* ``wilson``: 95% range for a share (k out of n).
* ``block_means``: block bootstrap, resampling runs of consecutive trades so winning and
  losing streaks stay together.
* ``deflated_sharpe``: chance the true per-trade Sharpe ratio is above the best one you'd expect
  from luck alone after trying that many settings, allowing for skewed and fat-tailed trade
  returns (Bailey & López de Prado, 2014). With one try it is the probabilistic Sharpe ratio.
* ``overfitting``: probability of backtest overfitting by combinatorially symmetric
  cross-validation (Bailey, Borwein, López de Prado & Zhu, 2015).
* ``regimes``: how the trades did when the market was rising or falling, calm or busy.
* ``multiple_testing``: how many of the optimiser's combinations clear a one-sided test that
  the average trade made money, before and after allowing for the number tried (Bonferroni;
  Benjamini & Hochberg, 1995).
* ``ratio_ranges``: Sharpe and Sortino ranges from a block bootstrap of candle-to-candle balance
  changes, so returns that are linked (overlapping trades, one trade over many candles) stay together.

All of these describe the past; none predicts future results.
"""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

Z95 = 1.959963984540054
EULER = 0.5772156649015329
MIN_TRADES_DSR = 10
ALPHA = 0.05  # the usual 5% level for the optimiser's per-combination tests
RATIO_RUNS = 1000  # bootstrap samples behind the linked-returns Sharpe and Sortino ranges
PBO_BLOCKS = 8
TREND_DAYS = 50  # rising = the daily close is above its average over this many days
VOL_DAYS = 20  # busy = daily moves over this many days were larger than the period's usual
_N = NormalDist()


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """95% Wilson score range for a share of ``k`` out of ``n``."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    mid = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, mid - half), min(1.0, mid + half)


def block_size(n: int) -> int:
    """Block length for ``n`` trades: the cube root, the usual rule of thumb."""
    return max(1, int(round(n ** (1 / 3))))


def block_means(x: np.ndarray, runs: int, rng: np.random.Generator, block: int | None = None) -> tuple[np.ndarray, int]:
    """Mean of ``runs`` circular block bootstrap samples of ``x`` (each as long as ``x``).
    Returns the means and the block length used."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    b = block or block_size(n)
    k = -(-n // b)
    starts = rng.integers(0, n, (runs, k))
    idx = ((starts[:, :, None] + np.arange(b)) % n).reshape(runs, k * b)[:, :n]
    return x[idx].mean(axis=1), b


def trade_returns(trades: pd.DataFrame) -> np.ndarray:
    """Each closed trade's profit as a fraction of the balance just before it."""
    if not len(trades):
        return np.empty(0)
    before = (trades["equity_after"] - trades["pnl"]).to_numpy(dtype=float)
    pnl = trades["pnl"].to_numpy(dtype=float)
    ok = before > 0
    return pnl[ok] / before[ok]


def closed_returns(start_eq: float, pnl: np.ndarray) -> np.ndarray:
    """Trade returns from a running balance: each P&L over the balance before it."""
    pnl = np.asarray(pnl, dtype=float)
    before = start_eq + np.concatenate(([0.0], np.cumsum(pnl)[:-1]))
    ok = before > 0
    return pnl[ok] / before[ok]


def per_trade_sharpe(r: np.ndarray) -> float | None:
    """Mean over standard deviation of trade returns (not scaled to a year)."""
    if len(r) < 2:
        return None
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r) / sd) if sd > 0 else None


def expected_max(trials: int, var: float) -> float:
    """The best Sharpe ratio you'd expect from ``trials`` tries with no real edge, when the
    tries' Sharpe ratios vary by ``var`` (the false strategy theorem)."""
    if trials <= 1 or var <= 0:
        return 0.0
    return math.sqrt(var) * ((1 - EULER) * _N.inv_cdf(1 - 1 / trials) + EULER * _N.inv_cdf(1 - 1 / (trials * math.e)))


def deflated_sharpe(r: np.ndarray, trials: int = 1, var: float | None = None) -> dict | None:
    """Deflated Sharpe ratio of trade returns ``r`` after ``trials`` tries.

    ``var`` is the variance of the tries' per-trade Sharpe ratios. Without it (the luck check,
    where the other tries aren't known) the tries are taken as independent with no edge, so their
    Sharpe ratios vary by about 1 / (trades - 1). None under ``MIN_TRADES_DSR`` trades or with no
    variation in the returns."""
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    t = len(r)
    sr = per_trade_sharpe(r)
    if t < MIN_TRADES_DSR or sr is None:
        return None
    trials = max(1, int(trials))
    sd = float(np.std(r))
    skew = float(np.mean((r - r.mean()) ** 3) / sd ** 3)
    kurt = float(np.mean((r - r.mean()) ** 4) / sd ** 4)
    if var is None or not np.isfinite(var):
        var = 1 / (t - 1)
    bar = expected_max(trials, var)
    spread = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if spread <= 0:
        return None
    z = (sr - bar) * math.sqrt(t - 1) / math.sqrt(spread)
    return {"sharpe": sr, "bar": bar, "probability": _N.cdf(z), "trials": trials, "trades": t,
            "skew": skew, "kurtosis": kurt}


def overfitting(profit: np.ndarray, blocks: int = PBO_BLOCKS) -> dict | None:
    """Probability of backtest overfitting from ``profit`` (rows: equal stretches of time in
    order, columns: combinations; values: profit in that stretch).

    Every way of choosing half the stretches is one split: the combination with the most profit
    in the chosen half is found, and its place among all combinations in the other half is
    recorded. The probability is the share of splits where it finished below the middle.
    None with fewer than 2 combinations, an odd or too small number of stretches, or no trades."""
    profit = np.asarray(profit, dtype=float)
    s, n = profit.shape
    if n < 2 or s < 4 or s % 2 or not np.any(profit):
        return None
    splits = below = lost = 0
    logits = []
    for chosen in itertools.combinations(range(s), s // 2):
        mask = np.zeros(s, dtype=bool)
        mask[list(chosen)] = True
        ins, outs = profit[mask].sum(axis=0), profit[~mask].sum(axis=0)
        best = int(np.argmax(ins))
        rank = float(pd.Series(outs).rank(method="average").iloc[best])
        w = rank / (n + 1)
        lam = math.log(w / (1 - w))
        logits.append(lam)
        splits += 1
        below += lam < 0
        lost += outs[best] < 0
    return {"probability": below / splits, "splits": splits, "below": below, "lost": lost,
            "blocks": s, "combinations": n, "median_logit": float(np.median(logits))}


def _utc(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def market_states(df: pd.DataFrame) -> pd.DataFrame | None:
    """Each day's market state as known at the start of the day: ``direction`` (rising when the
    previous daily close was above its ``TREND_DAYS``-day average) and ``activity`` (busy when the
    daily moves over the previous ``VOL_DAYS`` days were larger than usual for the period, i.e.
    above their median). None with too little history."""
    close = pd.Series(df["close"].to_numpy(dtype=float), index=_utc(df.index)).resample("1D").last().dropna()
    if len(close) < TREND_DAYS + VOL_DAYS:
        return None
    avg = close.rolling(TREND_DAYS, min_periods=TREND_DAYS).mean()
    vol = np.log(close).diff().rolling(VOL_DAYS, min_periods=VOL_DAYS).std()
    direction = np.where(avg.isna(), None, np.where(close > avg, "rising", "falling"))
    activity = np.where(vol.isna(), None, np.where(vol > vol.median(), "busy", "calm"))
    out = pd.DataFrame({"direction": direction, "activity": activity}, index=close.index).shift(1)
    return out


REGIME_LABELS = {"rising": "Rising market", "falling": "Falling market", "calm": "Calm market", "busy": "Busy market"}


def regimes(df: pd.DataFrame, trades: pd.DataFrame, start: pd.Timestamp | None = None) -> dict | None:
    """How the trades did by the market's state on the day each one opened. Purely descriptive:
    the share of days in each state, then trades, wins, profit and average R in each."""
    states = market_states(df)
    if states is None:
        return None
    first = _utc([start])[0] if start is not None else None
    days = states if first is None else states[states.index >= first.floor("D")]
    if len(trades):
        entry = _utc(pd.to_datetime(trades["entry_time"], utc=True)).floor("D")
        tagged = states.reindex(entry, method="ffill")
        tagged.index = trades.index
    else:
        tagged = pd.DataFrame(columns=["direction", "activity"])
    rows = []
    for col in ("direction", "activity"):
        known = days[col].dropna()
        for key in (("rising", "falling") if col == "direction" else ("calm", "busy")):
            t = trades[tagged[col] == key] if len(trades) else trades
            pnl = t["pnl"].to_numpy(dtype=float) if len(t) else np.empty(0)
            rows.append({
                "split": col, "key": key, "label": REGIME_LABELS[key],
                "days_pct": float((known == key).mean() * 100) if len(known) else 0.0,
                "trades": int(len(t)),
                "win_rate_pct": float((pnl > 0).mean() * 100) if len(pnl) else None,
                "pnl": float(pnl.sum()),
                "avg_r": float(t["r_multiple"].mean()) if len(t) and "r_multiple" in t else None,
            })
    unknown = int(tagged["direction"].isna().sum()) if len(trades) else 0
    return {"trend_days": TREND_DAYS, "vol_days": VOL_DAYS, "rows": rows, "unknown": unknown}


# ------------------------------------------------------------------ multiple testing


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularised incomplete beta function (modified Lentz)."""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def t_upper(t: float, df: float) -> float:
    """Chance a Student's t with ``df`` degrees of freedom is above ``t`` (one-sided p-value)."""
    if not math.isfinite(t):
        return 0.0 if t > 0 else 1.0
    tail = 0.5 * betainc(df / 2, 0.5, df / (df + t * t))
    return tail if t > 0 else 1.0 - tail


def mean_above_zero_p(r: np.ndarray) -> float | None:
    """One-sided p-value of a t-test that the average of ``r`` is above zero. None with fewer than
    2 values or no variation."""
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return None
    sd = float(np.std(r, ddof=1))
    if not sd > 0:
        return None
    return t_upper(float(np.mean(r)) / (sd / math.sqrt(len(r))), len(r) - 1)


def bonferroni(p: np.ndarray) -> np.ndarray:
    """Bonferroni-adjusted p-values: each multiplied by the number of tests, at most 1."""
    p = np.asarray(p, dtype=float)
    return np.minimum(1.0, p * len(p))


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (false discovery rate), in the order given."""
    p = np.asarray(p, dtype=float)
    m = len(p)
    if not m:
        return p
    order = np.argsort(p)
    scaled = p[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(scaled[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(1.0, adj)
    return out


def multiple_testing(returns: list[np.ndarray], chosen: int | None, alpha: float = ALPHA) -> dict | None:
    """One-sided t-test per combination that its average trade return is above zero, then how
    many clear ``alpha`` before and after allowing for the number of combinations tested.

    Combinations with too few trades (or identical results) to test count as tried with
    p-value 1. ``chosen`` is the index of the combination the optimiser picked. None with no
    combinations."""
    m = len(returns)
    if not m:
        return None
    raw = [mean_above_zero_p(r) for r in returns]
    p = np.array([1.0 if x is None else x for x in raw])
    bon, bh = bonferroni(p), benjamini_hochberg(p)
    out = {"tests": m, "alpha": alpha, "untestable": sum(x is None for x in raw),
           "raw": int((p < alpha).sum()), "bonferroni": int((bon < alpha).sum()), "bh": int((bh < alpha).sum()),
           "chosen": None}
    if chosen is not None and 0 <= chosen < m:
        out["chosen"] = {"p": raw[chosen], "bonferroni": float(bon[chosen]) if raw[chosen] is not None else None,
                         "bh": float(bh[chosen]) if raw[chosen] is not None else None,
                         "trades": int(np.isfinite(np.asarray(returns[chosen], dtype=float)).sum())}
    return out


# ------------------------------------------------------------------ linked returns


def ratio_ranges(r: np.ndarray, scale: float, block: int | None = None, runs: int = RATIO_RUNS,
                 rng: np.random.Generator | None = None) -> dict | None:
    """95% ranges for the Sharpe and Sortino ratios of per-candle returns ``r`` (scaled by
    ``scale``, the square root of candles a year) from a circular block bootstrap: each sample
    is built from runs of ``block`` consecutive candles, so linked returns stay together.

    Uses running sums, so each sample costs one lookup per block rather than per candle.
    None with fewer than 30 returns or no variation."""
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30 or not float(np.std(r)) > 0:
        return None
    b = int(min(max(1, block or block_size(n)), max(1, n // 2)))
    rng = rng or np.random.default_rng(0)
    ext = np.concatenate([r, r[:b]])
    down = np.minimum(ext, 0.0)
    sums = [np.concatenate(([0.0], np.cumsum(v))) for v in (ext, ext * ext, down * down)]
    full, rest = divmod(n, b)
    starts = rng.integers(0, n, (runs, full))
    tot = [c[starts + b].sum(axis=1) - c[starts].sum(axis=1) for c in sums]
    if rest:
        s = rng.integers(0, n, runs)
        tot = [t + c[s + rest] - c[s] for t, c in zip(tot, sums)]
    s1, s2, s3 = tot
    mean = s1 / n
    var = np.maximum(s2 - n * mean * mean, 0.0) / (n - 1)
    sd, dsd = np.sqrt(var), np.sqrt(s3 / n)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = mean / sd * scale
        sortino = mean / dsd * scale

    def band(x):
        x = x[np.isfinite(x)]
        return [float(v) for v in np.percentile(x, [2.5, 97.5])] if len(x) >= runs // 2 else None

    return {"sharpe": band(sharpe), "sortino": band(sortino), "block": b, "runs": runs, "candles": n}
