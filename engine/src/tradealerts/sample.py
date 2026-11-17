"""Synthetic price data for trying the tool offline. Randomly generated, not real prices."""

from __future__ import annotations

import functools

import numpy as np
import pandas as pd


def synthetic_bars(
    start: str = "2024-01-01",
    days: int = 730,
    freq: str = "1h",
    start_price: float = 5000.0,
    seed: int = 7,
    weekdays_only: bool = True,
) -> pd.DataFrame:
    """Random-walk OHLCV bars with drifting trends and changing volatility."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=int(pd.Timedelta(days=days) / pd.Timedelta(freq)), freq=freq, tz="UTC")
    if weekdays_only:
        idx = idx[idx.day_of_week < 5]
    n = len(idx)
    bar_hours = pd.Timedelta(freq).total_seconds() / 3600
    # Slowly changing trend and volatility regimes
    drift = np.repeat(rng.normal(0.00002, 0.00012, n // 300 + 1), 300)[:n] * bar_hours
    vol = np.exp(np.cumsum(rng.normal(0, 0.03, n)).clip(-1.5, 1.5)) * 0.0025 * np.sqrt(bar_hours)
    rets = drift + vol * rng.standard_t(5, n) / np.sqrt(5 / 3)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]]) * np.exp(rng.normal(0, 0.0003, n))
    wick = np.abs(rng.normal(0, 1, (2, n))) * vol * close * 0.6
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    volume = rng.integers(1_000, 10_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    ).rename_axis("time").round(2)


# The built-in "Sample data" market (``sample`` in the catalogue): one fixed series, generated
# once per process from a fixed seed and start, then cut off at the current time. Because the
# seed, start and length never change, the bars before any moment are always the same, so a
# backtest run today and one run next month agree on the past, and a forward test sees new
# bars arrive as time passes. 15-minute bars (so every candle size works), shut at the weekend.
SAMPLE_START = "2024-01-01"
SAMPLE_END = "2031-01-01"
SAMPLE_FREQ = "15min"
SAMPLE_PRICE = 5000.0
SAMPLE_SEED = 7


@functools.lru_cache(maxsize=1)
def _sample_series() -> pd.DataFrame:
    days = (pd.Timestamp(SAMPLE_END) - pd.Timestamp(SAMPLE_START)).days
    df = synthetic_bars(start=SAMPLE_START, days=days, freq=SAMPLE_FREQ, start_price=SAMPLE_PRICE, seed=SAMPLE_SEED,
                        weekdays_only=False)
    # Shut for the weekend like the built-in index and currency markets (markethours.weekend_reopen):
    # from Friday 17:00 to Sunday 18:00 New York time, so a forward test on it waits at the same times.
    ny = df.index.tz_convert("America/New_York")
    dow, hour = ny.day_of_week, ny.hour
    shut = ((dow == 4) & (hour >= 17)) | (dow == 5) | ((dow == 6) & (hour < 18))
    return df[~shut]


def sample_bars(now: pd.Timestamp | str | None = None) -> pd.DataFrame:
    """The sample market's bars whose candle had closed by ``now`` (default: the current time)."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    df = _sample_series()
    return df[df.index + pd.Timedelta(SAMPLE_FREQ) <= now].copy()
