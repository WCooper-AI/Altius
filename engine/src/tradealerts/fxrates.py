"""Exchange rates for markets priced in another currency than the account.

When a strategy's sizes are in the market's own currency (``risk.size_currency: market``),
a trade's profit and loss, costs and margin come out in that currency and are converted
into the account currency at the rate known at the time: the close of the latest daily
candle of the currency pair that had finished before the candle opens. ``risk.fx_rate``
fixes the rate instead, and nothing is downloaded.

The rates go in an ``fx`` column next to the prices (account money per 1 unit of market
money), so every test that reads those prices (optimiser, luck check, portfolio) uses them.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from .data import DataError, provider_for

# currency pairs with a built-in market: (base, quote) -> market key. The price is quote money per 1 base.
PAIRS: dict[tuple[str, str], str] = {("EUR", "USD"): "eurusd", ("GBP", "USD"): "gbpusd", ("EUR", "GBP"): "eurgbp"}
TIMEFRAME = "1d"
EXTRA_DAYS = 10  # rate history loaded before the prices start, to cover weekends and holidays


def pair_for(market: str, account: str) -> tuple[str, bool] | None:
    """(market key, invert) of the pair that turns ``market`` money into ``account`` money, or None."""
    m, a = market.upper(), account.upper()
    if (m, a) in PAIRS:
        return PAIRS[(m, a)], False  # e.g. EUR into USD: EUR/USD is already dollars per euro
    if (a, m) in PAIRS:
        return PAIRS[(a, m)], True  # e.g. USD into GBP: GBP/USD is dollars per pound, so 1 / it
    return None


def needs_download(strategy) -> bool:
    r = strategy.risk
    return r.converts and r.fx_rate is None


def _ns(idx: pd.DatetimeIndex) -> np.ndarray:
    idx = pd.DatetimeIndex(idx)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return idx.as_unit("ns").asi8


def rates(index: pd.DatetimeIndex, pair: pd.DataFrame, invert: bool,
          bar: pd.Timedelta = pd.Timedelta(days=1)) -> np.ndarray:
    """The rate known when each candle in ``index`` opens: the close of the latest ``pair``
    candle that had ended by then. Candles before the first rate use the first rate."""
    close = pair["close"].astype(float)
    close = close[np.isfinite(close.to_numpy()) & (close.to_numpy() > 0)]
    if close.empty:
        raise DataError("The exchange-rate prices have no usable closes")
    known_at = _ns(close.index + bar)  # a candle's close is known once it has ended
    order = np.argsort(known_at, kind="stable")
    known_at, vals = known_at[order], close.to_numpy()[order]
    pos = np.searchsorted(known_at, _ns(index), "right") - 1
    out = vals[np.clip(pos, 0, None)]
    return 1.0 / out if invert else out


def attach(df: pd.DataFrame, strategy, load: Callable[[str, int], pd.DataFrame]) -> pd.DataFrame:
    """``df`` with an ``fx`` column when ``strategy`` needs downloaded rates, otherwise ``df`` itself.

    ``load(market_key, days)`` returns that market's daily candles over the last ``days`` days."""
    if not needs_download(strategy) or df.empty:
        return df
    r = strategy.risk
    q, a = r.market_currency.upper(), r.currency.upper()
    found = pair_for(q, a)
    if found is None:
        raise DataError(f"This market is priced in {q} and your account is in {a}, and there is no "
                        f"built-in {q}/{a} price to convert with. Type a fixed exchange rate in the "
                        "risk settings instead.")
    key, invert = found
    first = df.index[0]
    first = first.tz_localize("UTC") if first.tzinfo is None else first
    days = max(0, (pd.Timestamp.now(tz="UTC") - first).days) + EXTRA_DAYS
    try:
        pair = load(key, days)
    except DataError:
        raise
    except Exception as exc:
        raise DataError(f"Couldn't load the {q}/{a} exchange rate to convert this market's money: {exc}") from exc
    if pair is None or pair.empty:
        raise DataError(f"Couldn't load the {q}/{a} exchange rate to convert this market's money. "
                        "Type a fixed exchange rate in the risk settings instead.")
    return df.assign(fx=rates(df.index, pair, invert))


def loader(cfg, cache=None) -> Callable[[str, int], pd.DataFrame]:
    """``load`` for :func:`attach`: a pair's daily candles from the workspace's chosen data source
    (built-in markets, whatever the strategy's own provider), shared through ``cache`` when given."""

    def load(key: str, days: int) -> pd.DataFrame:
        def fetch():
            return provider_for(cfg, "auto").fetch(key, TIMEFRAME, days)
        if cache is None:
            return fetch()
        tag = (cfg.data.get("source"), str(cfg.cache_dir), cfg.data.get("oanda_token") or "")
        return cache.get(("fx", *tag, key, days), fetch)

    return load


def fx_for(strategy, df: pd.DataFrame) -> np.ndarray | None:
    """The ``fx`` column for the simulator, when this strategy converts with downloaded rates."""
    if needs_download(strategy) and "fx" in df.columns:
        return df["fx"].to_numpy(dtype=float)
    return None
