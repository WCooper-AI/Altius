"""More result measures: yearly growth, drawdown length, best and worst periods, the
average of the worst days, trades split by weekday and trading session, results by calendar
year, the range for the share of trades won, and how many trades overlapped.

Plain numbers only: nothing here says whether a result is good or bad."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import stats, timeframes

MIN_YEARS_FOR_CAGR = 1.0  # shorter tests stretched to a year give wild, misleading rates
ES_SHARE = 0.05  # expected shortfall: the worst 5% of days
MIN_DAYS_FOR_ES = 20
PERIODS = (("day", "D"), ("week", "W"), ("month", "ME"))
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Local opening hours (minutes after midnight), so daylight saving moves with each centre.
SESSIONS = {
    "tokyo": ("Asia/Tokyo", 9 * 60, 18 * 60),
    "london": ("Europe/London", 8 * 60, 16 * 60 + 30),
    "new_york": ("America/New_York", 8 * 60, 17 * 60),
}
SESSION_ORDER = ["Asia", "London", "London + New York", "New York", "Other hours"]


def _utc(index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def _days(a, b) -> float:
    return float((pd.Timestamp(b) - pd.Timestamp(a)) / pd.Timedelta(days=1))


def cagr(start_equity: float, end_equity: float, start, end) -> float | None:
    """Yearly growth rate (%) that turns the opening balance into the closing one over the test.
    None for tests shorter than a year."""
    years = _days(start, end) / 365.25
    if years < MIN_YEARS_FOR_CAGR or start_equity <= 0:
        return None
    if end_equity <= 0:
        return -100.0
    return float(((end_equity / start_equity) ** (1 / years) - 1) * 100)


def calmar(cagr_pct: float | None, max_drawdown_pct: float) -> float | None:
    """Yearly growth divided by the size of the worst drawdown. None without both."""
    if cagr_pct is None or not max_drawdown_pct:
        return None
    return float(cagr_pct / abs(max_drawdown_pct))


def period_changes(equity: pd.Series, rule: str) -> pd.Series:
    """% change of the balance over each calendar period (``rule`` as in pandas resample, e.g.
    "D", "W", "ME"); periods without candles are left out. The first starts from the opening balance."""
    if len(equity) < 2:
        return pd.Series(dtype=float)
    eq = equity.copy()
    eq.index = _utc(eq.index)
    ends = eq.resample(rule).last().dropna()
    starts = ends.shift(1)
    starts.iloc[0] = float(eq.iloc[0])
    pct = (ends / starts - 1) * 100
    return pct[np.isfinite(pct)]


def _period_start(stamp: pd.Timestamp, name: str) -> str:
    if name == "week":
        stamp = stamp - pd.Timedelta(days=6)  # weeks end on Sunday; label by their Monday
    elif name == "month":
        stamp = stamp.replace(day=1)
    return stamp.date().isoformat()


def best_worst(equity: pd.Series) -> dict:
    """Largest fall and largest rise of the balance within one calendar day, week and month,
    each with the date that period started. None for a period length the test doesn't span twice."""
    out = {}
    for name, rule in PERIODS:
        ch = period_changes(equity, rule)
        if len(ch) < 2:
            out[name] = None
            continue
        lo, hi = ch.idxmin(), ch.idxmax()
        out[name] = {"worst": float(ch[lo]), "worst_at": _period_start(lo, name),
                     "best": float(ch[hi]), "best_at": _period_start(hi, name), "count": int(len(ch))}
    return out


def expected_shortfall(equity: pd.Series, share: float = ES_SHARE) -> float | None:
    """Average % change on the worst ``share`` of days (5% by default): -2.1 means those days
    lost 2.1% on average. Days with no trade open count as 0%. None with under 20 days."""
    ch = period_changes(equity, "D").to_numpy(dtype=float)
    if len(ch) < MIN_DAYS_FOR_ES:
        return None
    k = max(1, int(np.ceil(len(ch) * share)))
    return float(np.sort(ch)[:k].mean())


def worst_drawdown(equity: pd.Series, start_equity: float) -> dict | None:
    """The deepest drop from a high (the opening balance counts as one): when it started,
    bottomed and got back to that high, with lengths in days. ``recovered`` and
    ``days_to_recover`` are None when the balance never got back by the end of the test."""
    if len(equity) < 2:
        return None
    peak = equity.cummax().clip(lower=start_equity)
    dd = (equity / peak - 1).to_numpy(dtype=float)
    if not (dd < 0).any():
        return None
    i = int(np.argmin(dd))
    idx = equity.index
    level = float(peak.iloc[i])
    highs = np.flatnonzero(equity.iloc[: i + 1].to_numpy(dtype=float) >= level)
    top = idx[highs[-1]] if len(highs) else idx[0]
    back = np.flatnonzero(equity.iloc[i:].to_numpy(dtype=float) >= level)
    rec = idx[i + back[0]] if len(back) else None
    return {
        "depth_pct": float(dd[i] * 100),
        "peak": str(top),
        "trough": str(idx[i]),
        "recovered": str(rec) if rec is not None else None,
        "days_down": _days(top, idx[i]),
        "days_to_recover": _days(idx[i], rec) if rec is not None else None,
        "days_total": _days(top, rec if rec is not None else idx[-1]),
    }


def _groups(trades: pd.DataFrame, keys: np.ndarray, order: list[str]) -> list[dict]:
    rows = []
    for name in order:
        t = trades[keys == name]
        if t.empty:
            continue
        pnl = t["pnl"]
        rows.append({"name": name, "trades": int(len(t)), "won_pct": float((pnl > 0).mean() * 100),
                     "pnl": float(pnl.sum()), "avg_r": float(t["r_multiple"].mean())})
    return rows


def _opened(trades: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(trades["entry_time"], utc=True)


def by_weekday(trades: pd.DataFrame) -> list[dict]:
    """Trades grouped by the weekday they opened (UTC)."""
    if trades.empty or "entry_time" not in trades:
        return []
    names = np.array(WEEKDAYS)[_opened(trades).dt.day_of_week.to_numpy()]
    return _groups(trades, names, WEEKDAYS)


def _in_session(times: pd.Series, zone: str, start: int, end: int) -> np.ndarray:
    local = times.dt.tz_convert(zone)
    mins = local.dt.hour * 60 + local.dt.minute
    return ((local.dt.day_of_week < 5) & (mins >= start) & (mins < end)).to_numpy()


def session_names(times: pd.Series) -> np.ndarray:
    """Trading session for each UTC time: London + New York when both are open, then London,
    New York, Asia (Tokyo hours), else Other hours."""
    tk, ld, ny = (_in_session(times, *SESSIONS[k]) for k in ("tokyo", "london", "new_york"))
    return np.select([ld & ny, ld, ny, tk], ["London + New York", "London", "New York", "Asia"], "Other hours")


def by_session(trades: pd.DataFrame, timeframe: str) -> list[dict]:
    """Trades grouped by the trading session they opened in. Empty for daily or longer candles,
    where the opening time says nothing about the session."""
    if trades.empty or "entry_time" not in trades or timeframes.minutes(timeframe) >= 24 * 60:
        return []
    return _groups(trades, session_names(_opened(trades)), SESSION_ORDER)


def by_year(trades: pd.DataFrame, equity: pd.Series) -> list[dict]:
    """Results for each calendar year (UTC) of the test: change in the balance (the first year
    starts from the opening balance), worst drop within the year (from the balance's high so far
    that year), and the trades that closed in it."""
    ch = period_changes(equity, "YE")
    if not len(ch):
        return []
    eq = equity.copy()
    eq.index = _utc(eq.index)
    closed = pd.to_datetime(trades["exit_time"], utc=True).dt.year if len(trades) and "exit_time" in trades else None
    rows = []
    for stamp, pct in ch.items():
        y = int(stamp.year)
        part = eq[eq.index.year == y]
        prev = eq[eq.index.year < y]
        first = float(prev.iloc[-1]) if len(prev) else float(eq.iloc[0])
        path = np.concatenate(([first], part.to_numpy(dtype=float)))
        peak = np.maximum.accumulate(path)
        dd = float(((path / np.where(peak > 0, peak, np.nan)) - 1).min() * 100) if len(path) else 0.0
        t = trades[(closed == y).to_numpy()] if closed is not None else trades.iloc[0:0]
        pnl = t["pnl"].to_numpy(dtype=float) if len(t) else np.empty(0)
        rows.append({"year": y, "change_pct": float(pct), "max_drawdown_pct": dd if np.isfinite(dd) else None,
                     "trades": int(len(t)), "won_pct": float((pnl > 0).mean() * 100) if len(pnl) else None,
                     "pnl": float(pnl.sum()),
                     "avg_r": float(t["r_multiple"].mean()) if len(t) and "r_multiple" in t else None})
    return rows


def win_rate_range(trades: pd.DataFrame) -> list[float] | None:
    """95% range (Wilson) for the share of trades won, in %. None without trades."""
    if not len(trades):
        return None
    lo, hi = stats.wilson(int((trades["pnl"] > 0).sum()), len(trades))
    return [lo * 100, hi * 100]


def overlapping_trades(trades: pd.DataFrame) -> int:
    """How many trades opened while an earlier one was still open."""
    if len(trades) < 2 or "entry_time" not in trades or "exit_time" not in trades:
        return 0
    t = pd.DataFrame({"a": pd.to_datetime(trades["entry_time"], utc=True),
                      "b": pd.to_datetime(trades["exit_time"], utc=True)}).sort_values("a", kind="stable")
    still_open = t["b"].cummax().shift(1)
    return int((t["a"] < still_open).sum())


def extra(trades: pd.DataFrame, equity: pd.Series, start_equity: float, start, end,
          max_drawdown_pct: float, timeframe: str) -> dict:
    """All of the above, as metric keys for ``backtest.compute_metrics``."""
    growth = cagr(start_equity, float(equity.iloc[-1]), start, end)
    return {
        "cagr_pct": growth,
        "calmar": calmar(growth, max_drawdown_pct),
        "expected_shortfall_pct": expected_shortfall(equity),
        "worst_drawdown": worst_drawdown(equity, start_equity),
        "periods": best_worst(equity),
        "by_weekday": by_weekday(trades),
        "by_session": by_session(trades, timeframe),
        "by_year": by_year(trades, equity),
        "win_rate_range": win_rate_range(trades),
        "overlapping_trades": overlapping_trades(trades),
    }
