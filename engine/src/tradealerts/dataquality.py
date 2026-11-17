"""Data quality: is the price history good enough to trust what's drawn on it?

`check(df, timeframe)` looks at a bar frame and returns plain-English warnings about
gaps (missing candles), timestamps out of order, a stale last bar (the feed stopped),
and zero-volume or flat candles (the feed filled in a price without any trading behind
it). It never raises: bad input just means no warnings.

`report(df, timeframe)` is the fuller record kept with every backtest: the checks above
(except staleness, which only matters live), plus unusual jumps between candles,
candles closer together than the candle size, the time zone, how many candles had a
spread in the data, a fingerprint of the prices so the same data can be recognised, and
two things that look like jumps but have a specific, named cause: an overnight move close
to a common stock-split ratio, or (on a futures symbol) a step change at what is probably
a contract roll. Neither of these changes a single price: they are notes about data that
is left exactly as the provider sent it.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from . import instruments, timeframes
from .markethours import trades_24_7

# a gap must be at least this many bars wide before it counts (one missing candle is normal noise)
GAP_BARS = 3
# how many candles the last bar may lag "now" before it is called stale
STALE_BARS = 3
# a run of zero-volume (or flat) candles at least this long is reported
ZERO_RUN = 5
# above this many candles in one gap, estimate its weekday share instead of listing every candle
EXACT_LIMIT = 50_000

# Overnight price ratios (this candle's open over the one before's close) close enough to one of
# these to be a stock split rather than an ordinary move, each with its plain-English phrase and
# the ratio as people write it. An unadjusted feed shows a real split as almost exactly this jump,
# because every later candle is on the new price scale.
SPLIT_PHRASES: dict[float, tuple[str, str]] = {
    0.5: ("fell to about half", "2:1"),
    1 / 3: ("fell to about a third", "3:1"),
    2 / 3: ("fell to about two-thirds", "3:2"),
    0.25: ("fell to about a quarter", "4:1"),
    0.2: ("fell to about a fifth", "5:1"),
    2.0: ("roughly doubled", "1:2 reverse"),
    3.0: ("roughly tripled", "1:3 reverse"),
    1.5: ("rose to about one and a half times its level", "2:3 reverse"),
    4.0: ("rose to about four times its level", "1:4 reverse"),
    5.0: ("rose to about five times its level", "1:5 reverse"),
}
SPLIT_TOL = 0.04  # relative tolerance: a real split lands almost exactly on the ratio

DIVIDEND_GAP = (0.001, 0.05)   # a 0.1%-5% overnight drop is the size of a typical cash dividend
DIVIDEND_Z = 3.5                # ...and clearly bigger than this market's own ordinary overnight moves
DIVIDEND_MIN_HITS = 3           # at least this many similarly-spaced drops before it's called a pattern
DIVIDEND_PERIOD_TOL = 0.35      # the gap between them may vary by this fraction and still count as regular
# plausible days between one dividend-sized drop and the next: quarterly, half-yearly, yearly
DIVIDEND_PERIODS_DAYS = ((75, 100), (160, 200), (340, 390))


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _spans_weekend(a: pd.Timestamp, b: pd.Timestamp) -> bool:
    """True if a→b is a weekend shutdown rather than missing data: it includes part of a Saturday and is
    no longer than a long weekend. Anything wider than that is a real gap even if a weekend sits inside it."""
    if b - a > pd.Timedelta(days=3, hours=12):
        return False
    return any(d.weekday() == 5 for d in pd.date_range(a.normalize(), b.normalize(), freq="D"))


def _weekday_candles(a: pd.Timestamp, b: pd.Timestamp, bar: pd.Timedelta) -> int:
    """Candles that should sit strictly between a and b on weekdays (Saturdays and Sundays skipped)."""
    n = int((b - a) / bar) - 1
    if n <= 0:
        return 0
    if n > EXACT_LIMIT:
        return int(n * 5 / 7)
    stamps = pd.date_range(a + bar, periods=n, freq=bar)
    return int((stamps.day_of_week < 5).sum())


def _fmt(t: pd.Timestamp) -> str:
    return f"{t:%a %d %b %H:%M}"


def check(df: pd.DataFrame, timeframe: str, now=None, symbol: str = "") -> list[dict]:
    """Warnings about the frame, each {"kind", "text", "count"}; empty when it looks fine.

    kinds: "gap" (missing candles, weekends ignored), "stale" (last bar well behind now),
    "zero_volume" (a run of candles with no volume, or no price movement when there's no volume column),
    "repaired" (candles with impossible high/low values that were widened when loaded),
    "missing_bars" (total weekday candles missing across several gaps, with ``gaps`` and ``largest_at``),
    "disordered" (timestamps that go backwards or repeat; checked after sorting them).
    """
    out: list[dict] = []
    if df is None or len(df) < 2 or not isinstance(df.index, pd.DatetimeIndex):
        return out
    try:
        bar = timeframes.to_timedelta(timeframe)
    except Exception:
        return out
    idx = df.index
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    steps = idx[1:] - idx[:-1]
    back = int(df.attrs.get("disordered_bars", 0) or 0) or int((steps <= pd.Timedelta(0)).sum())  # earlier than, or the same as, the candle before
    if back:
        out.append({"kind": "disordered", "count": back,
                    "text": (f"{back} candle{'s' if back != 1 else ''} came out of time order or repeated an "
                             "earlier time. They were put in order, but the source data may be mixed up there.")})
    if not idx.is_monotonic_increasing:
        order = idx.argsort()
        idx, df = idx[order], df.iloc[order]

    # gaps
    is_24_7 = bool(symbol and trades_24_7(symbol))
    diffs = pd.Series(idx[1:] - idx[:-1], index=idx[1:])
    gaps = [(a, b) for b, d in diffs[diffs > GAP_BARS * bar].items() for a in [b - d] if is_24_7 or not _spans_weekend(a, b)]
    if gaps:
        a, b = max(gaps, key=lambda g: g[1] - g[0])
        missing = int((b - a) / bar) - 1
        out.append({"kind": "gap", "count": len(gaps),
                    "text": (f"{len(gaps)} gap{'s' if len(gaps) != 1 else ''} in the prices, the widest "
                             f"about {missing} candles ({_fmt(a)} to {_fmt(b)} UTC). "
                             "Rules that look back over those candles may have missed or invented signals.")})
    # Weekday candles missing. Holes next to a weekend count too when more than GAP_BARS weekday
    # candles are gone (a market shut from Friday evening loses a few). One gap already reported
    # above is described in full there, so this adds the total when there are several.
    holes = []
    for b, d in diffs[diffs > GAP_BARS * bar].items():
        a = b - d
        n = max(0, int((b - a) / bar) - 1) if is_24_7 else _weekday_candles(a, b, bar)
        if n > (0 if (a, b) in gaps else GAP_BARS):
            holes.append((a, b, n))
    if len(holes) > 1 or (holes and not gaps):
        total = sum(g[2] for g in holes)
        a, b, worst = max(holes, key=lambda g: g[2])
        out.append({"kind": "missing_bars", "count": total, "gaps": len(holes), "largest_at": a.isoformat(),
                    "text": (f"About {total} weekday candles are missing across {len(holes)} gaps; the largest "
                             f"({worst} candles) starts {_fmt(a)} UTC. Results over those dates rest on less data than they seem.")})

    # stale last bar
    now = pd.Timestamp.now(tz="UTC") if now is None else _utc(now)
    age = now - (idx[-1] + bar)
    if age > STALE_BARS * bar and (is_24_7 or not _spans_weekend(idx[-1], now)):
        hours = age.total_seconds() / 3600
        ago = f"{int(age.total_seconds() // 60)} min" if hours < 1 else f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} days"
        out.append({"kind": "stale", "count": 1,
                    "text": f"The last candle finished {ago} ago. The feed may have stopped, so anything drawn since is guesswork."})

    # zero-volume / flat runs
    if "volume" in df.columns:
        dead = (pd.to_numeric(df["volume"], errors="coerce").fillna(0) <= 0).to_numpy()
    elif {"open", "high", "low", "close"} <= set(df.columns):
        dead = ((df["high"] == df["low"]) & (df["open"] == df["close"])).to_numpy()
    else:
        dead = None
    if dead is not None and dead.any():
        runs, start = [], None
        for i, d in enumerate(list(dead) + [False]):
            if d and start is None:
                start = i
            elif not d and start is not None:
                if i - start >= ZERO_RUN:
                    runs.append((start, i - 1))
                start = None
        if runs:
            s, e = max(runs, key=lambda r: r[1] - r[0])
            what = "no volume" if "volume" in df.columns else "no price movement"
            out.append({"kind": "zero_volume", "count": len(runs),
                        "text": (f"{len(runs)} stretch{'es' if len(runs) != 1 else ''} of candles with {what}, the longest "
                                 f"{e - s + 1} candles from {_fmt(idx[s])} UTC. Those are filler, not real trading.")})

    # candles whose high/low didn't contain the open/close, widened when loaded (data.normalise)
    repaired = int(df.attrs.get("repaired_bars", 0) or 0)
    if repaired:
        out.append({"kind": "repaired", "count": repaired,
                    "text": (f"{repaired} candle{'s' if repaired != 1 else ''} had a high below the open, close or low "
                             "(or a low above them) and were widened to fit. The source data has bad ticks there.")})
    return out


def _is_stock(symbol: str) -> bool:
    """Corporate actions (splits, dividends) only happen on shares: CFDs, FX and crypto don't
    split or pay a dividend. A built-in market says so directly; a custom symbol is guessed from
    the usual Yahoo-style naming (``=F`` futures, ``=X`` FX pairs, ``BTC-USD`` style crypto
    pairs) and otherwise treated as a possible stock, since shares have no distinguishing mark."""
    inst = instruments.get(symbol)
    if inst is not None:
        return inst.group == "Stocks"
    sym = (symbol or "").upper()
    return not (sym.endswith("=F") or sym.endswith("=X") or "-" in sym)


def _is_futures(symbol: str) -> bool:
    """True for a Yahoo-style futures ticker (ends ``=F``): checked on the built-in instrument's
    own symbol field, or the raw symbol for a custom market."""
    inst = instruments.get(symbol)
    sym = inst.symbol if inst is not None else (symbol or "")
    return sym.upper().endswith("=F")


def _overnight_ratio(df: pd.DataFrame, bar: pd.Timedelta) -> pd.Series:
    """This candle's open divided by the close of the candle before it, but only where the
    candle before could plausibly be the previous session's close: every row for daily-or-longer
    candles, or just the first candle after a break for anything finer. Without that limit,
    ordinary candle-to-candle noise on intraday data would look like a split or a dividend purely
    by chance, since many small moves land in the same range over hundreds of candles."""
    prev_close = df["close"].shift(1)
    ratio = df["open"] / prev_close
    if bar < pd.Timedelta(days=1):
        step = df.index.to_series().diff()
        ratio = ratio[step > bar]
    return ratio.replace([np.inf, -np.inf], np.nan).dropna()


def find_splits(df: pd.DataFrame, bar: pd.Timedelta) -> list[tuple[pd.Timestamp, float, float]]:
    """Overnight ratios close to a common split ratio: a list of (when, actual ratio, matched
    ratio). An ordinary move practically never lands within a few percent of exactly 2, 3, 0.5
    and so on, so this rarely fires by chance."""
    out = []
    for t, r in _overnight_ratio(df, bar).items():
        if not np.isfinite(r) or r <= 0 or abs(r - 1) < 0.2:
            continue  # ordinary moves never get close to halving, doubling, and so on
        nearest = min(SPLIT_PHRASES, key=lambda s: abs(r - s) / s)
        if abs(r - nearest) / nearest <= SPLIT_TOL:
            out.append((t, float(r), nearest))
    return out


def find_dividend_gaps(df: pd.DataFrame, bar: pd.Timedelta, symbol: str) -> list[tuple[pd.Timestamp, float]]:
    """Small, regularly-spaced overnight drops on a stock: the signature an unadjusted feed
    leaves on an ex-dividend date. A single small drop could be anything (ordinary noise, news),
    and on a volatile stock an ordinary down day often falls in the same size range purely by
    chance, so this only counts a drop that is both dividend-sized *and* clearly bigger than this
    market's own typical overnight move, and only reports when at least three of those are spaced
    about as regularly as quarterly, half-yearly or yearly dividends usually are. That makes it
    cautious: a real but small or irregular dividend may go unreported rather than risk a false
    "possible dividend" on ordinary noise."""
    if not _is_stock(symbol):
        return []
    ratio = _overnight_ratio(df, bar)
    if len(ratio) < 30:
        return []
    # a robust (outlier-resistant) measure of this market's typical overnight move, so the test
    # is relative to its own volatility rather than one fixed percentage for every stock
    typical_move = float((ratio - 1).abs().median()) * 1.4826 + 1e-12
    lo, hi = 1 - DIVIDEND_GAP[1], 1 - DIVIDEND_GAP[0]
    sized = (ratio >= lo) & (ratio <= hi)
    unusual = (1 - ratio) > DIVIDEND_Z * typical_move
    hits = ratio[sized & unusual]
    if len(hits) < DIVIDEND_MIN_HITS:
        return []
    spacing_days = hits.index.to_series().diff().dropna().dt.total_seconds() / 86400
    if spacing_days.empty:
        return []
    typical = spacing_days.median()
    if not any(lo_d <= typical <= hi_d for lo_d, hi_d in DIVIDEND_PERIODS_DAYS):
        return []  # not spaced like quarterly, half-yearly or yearly dividends: too likely a coincidence
    regular = spacing_days[(spacing_days >= typical * (1 - DIVIDEND_PERIOD_TOL)) & (spacing_days <= typical * (1 + DIVIDEND_PERIOD_TOL))]
    if len(regular) < DIVIDEND_MIN_HITS - 1:
        return []
    return [(t, float(r)) for t, r in hits.items()]


def fingerprint(df: pd.DataFrame) -> str:
    """A short code for the candles themselves (times, prices and any spread): the same
    data gives the same code on any machine, and any changed candle gives a new one."""
    h = hashlib.sha256()
    if df is None or not len(df):
        return h.hexdigest()[:16]
    idx = df.index.tz_convert("UTC") if getattr(df.index, "tz", None) is not None else df.index
    h.update(np.ascontiguousarray(idx.as_unit("ns").asi8).tobytes())
    cols = [c for c in ("open", "high", "low", "close", "spread") if c in df.columns]
    h.update(",".join(cols).encode())
    values = np.ascontiguousarray(df[cols].to_numpy(dtype=float))
    h.update(np.where(np.isnan(values), 0.0, values).tobytes())  # one form of "missing"
    return h.hexdigest()[:16]


def report(df: pd.DataFrame, timeframe: str, symbol: str = "") -> dict:
    """The data-quality record for one backtest period (see the module notes)."""
    out: dict = {"candles": 0 if df is None else int(len(df)), "issues": [], "counts": {},
                 "fingerprint": fingerprint(df), "timezone": None, "spread_coverage_pct": None}
    if df is None or len(df) < 2 or not isinstance(df.index, pd.DatetimeIndex):
        return out
    bar = timeframes.to_timedelta(timeframe)
    out["start"], out["end"] = df.index[0].isoformat(), df.index[-1].isoformat()
    tz = df.index.tz
    out["timezone"] = str(tz) if tz is not None else None
    issues = check(df, timeframe, now=df.index[-1] + bar, symbol=symbol)  # past data: never "stale"
    if tz is None:
        issues.append({"kind": "timezone", "count": len(df),
                       "text": "The candle times carry no time zone, so they were read as UTC."})
    steps = pd.Series(df.index[1:] - df.index[:-1])
    close = int(((steps > pd.Timedelta(0)) & (steps < bar)).sum())
    if close:
        issues.append({"kind": "frequency", "count": close,
                       "text": (f"{close} candle{'s' if close != 1 else ''} start less than one candle size "
                                f"({timeframe}) after the one before. The data may mix candle sizes there.")})
    from .data import find_jumps
    jumps = find_jumps(df, timeframe)
    if len(jumps):
        worst = jumps["gap"].idxmax()
        if _is_futures(symbol):
            issues.append({"kind": "roll", "count": int(len(jumps)),
                           "text": (f"{len(jumps)} step change{'s' if len(jumps) != 1 else ''} between candles, the "
                                    f"largest on {_fmt(worst)} UTC. This symbol is a futures contract, so this is "
                                    "likely a contract roll (the contract behind the price changed), not a bad tick.")})
        else:
            issues.append({"kind": "jump", "count": int(len(jumps)),
                           "text": (f"{len(jumps)} unusual jump{'s' if len(jumps) != 1 else ''} between candles, the "
                                    f"largest on {_fmt(worst)} UTC. Often a futures roll or a bad tick.")})
    splits = find_splits(df, bar)
    if splits:
        t, r, matched = max(splits, key=lambda s: abs(np.log(s[1])))  # the most extreme ratio
        phrase, label = SPLIT_PHRASES[matched]
        issues.append({"kind": "split", "count": len(splits),
                       "text": (f"{len(splits)} overnight move{'s' if len(splits) != 1 else ''} close to a common "
                                f"split ratio, the largest on {_fmt(t)} UTC (prices {phrase} overnight) — possibly "
                                f"a {label} split. This data is not adjusted for it; a split changes the price scale "
                                "but not what actually happened at the time.")})
    dividends = find_dividend_gaps(df, bar, symbol)
    if dividends:
        worst = min(dividends, key=lambda d: d[1])
        issues.append({"kind": "dividend", "count": len(dividends),
                       "text": (f"{len(dividends)} small, regularly-spaced overnight drops, the kind an unadjusted "
                                "feed leaves on a stock's ex-dividend date; the largest on "
                                f"{_fmt(worst[0])} UTC (down {(1 - worst[1]) * 100:.1f}% overnight). "
                                "This data is not adjusted for it.")})
    if "spread" in df.columns:
        pct = float(df["spread"].notna().mean() * 100)
        out["spread_coverage_pct"] = pct
        if 0 < pct < 100:
            issues.append({"kind": "spread", "count": int(df["spread"].isna().sum()),
                           "text": (f"The data has a spread for {pct:.0f}% of candles; the others use the "
                                    "strategy's spread setting.")})
    out["issues"] = issues
    out["counts"] = {i["kind"]: i["count"] for i in issues}
    return out
