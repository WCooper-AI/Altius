"""Comparing different strategies on the same footing.

Each strategy normally brings its own market, balance, risk and costs, so two backtests differ
in more than their rules. :func:`same_footing` copies the chosen groups of settings from one
strategy (the reference) to the others, and lists every setting it changed, so the comparison
says exactly what was made to match:

- ``market``: the reference's market and its contract terms (money per point, sizes, margin).
  Each strategy keeps its own candle size, because its rules were written for it.
- ``account``: starting balance, account currency, risk per trade, compounding and loss limits.
- ``costs``: spread, slippage, commission, funding and borrow. A spread or slippage in price
  points only means the same on the same market, so on a different market those two stay.

Values are copied as the reference actually uses them (blank settings filled in with its
market's defaults), so "the same" means the same numbers, not the same blanks.
"""

from __future__ import annotations

import copy

from .strategy import StrategyError, strategy_from_dict

MATCH_GROUPS = ("market", "account", "costs")
MARKET_KEYS = ("symbol", "label", "provider", "tz", "candle_alignment", "lookback_days")  # not timeframe
CONTRACT_KEYS = ("point_value", "size_step", "min_size", "max_size", "size_currency", "market_currency",
                 "fx_rate", "margin_pct")
ACCOUNT_KEYS = ("account_size", "currency", "risk_per_trade_pct", "compound", "max_daily_loss_pct",
                "max_weekly_loss_pct", "max_drawdown_pct", "max_margin_pct", "close_out_pct")
COST_KEYS = ("spread", "use_data_spread", "funding_pct", "commission", "commission_pct", "borrow_pct",
             "slippage", "slippage_pct", "slippage_atr_pct")
POINT_COSTS = ("spread", "slippage")  # in the market's own price points

LABELS = {
    "point_value": "Money per point", "size_step": "Size step", "min_size": "Smallest size",
    "max_size": "Largest size", "size_currency": "Sizes in", "market_currency": "Market currency",
    "fx_rate": "Fixed exchange rate", "margin_pct": "Margin (% of value)",
    "account_size": "Starting balance", "currency": "Account currency", "risk_per_trade_pct": "Risk per trade (%)",
    "compound": "Compounding", "max_daily_loss_pct": "Daily loss limit (%)",
    "max_weekly_loss_pct": "Weekly loss limit (%)", "max_drawdown_pct": "Drawdown limit (%)",
    "max_margin_pct": "Margin limit (% of balance)", "close_out_pct": "Close-out level (% of margin)",
    "spread": "Spread (price points)", "use_data_spread": "Use the data's own spread",
    "funding_pct": "Overnight funding (% a year)", "commission": "Commission per fill",
    "commission_pct": "Commission (% of value)", "borrow_pct": "Borrow fee (% a year)",
    "slippage": "Slippage (price points)", "slippage_pct": "Slippage (% of price)",
    "slippage_atr_pct": "Slippage (% of the average candle range)",
}


def _value(s, key: str):
    """The value a parsed strategy actually uses for a risk or cost setting."""
    return getattr(s.risk, key) if key in CONTRACT_KEYS or key in ACCOUNT_KEYS else getattr(s, key)


def _fmt(v) -> str:
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, (int, float)):
        return format(float(v), ",g")
    return str(v)


def _market_text(s) -> str:
    m = s.market
    return f"{m.label} ({m.symbol})" if m.label and m.label != m.symbol else m.symbol


def _same_market(a, b) -> bool:
    return (a.market.symbol, a.market.provider) == (b.market.symbol, b.market.provider)


def _parse(data: dict, name: str):
    try:
        return strategy_from_dict(copy.deepcopy(data), sid="compare")
    except StrategyError as exc:
        raise ValueError(f"{name}: {exc}") from None


def _copy(new: dict, section: str, ref, keys) -> None:
    sec = new.get(section)
    sec = dict(sec) if isinstance(sec, dict) else {}
    for k in keys:
        v = _value(ref, k)
        if v is None:
            sec.pop(k, None)
        else:
            sec[k] = v
    new[section] = sec


def _changes(before, after) -> list[str]:
    out = []
    if not _same_market(before, after):
        out.append(f"Market: {_market_text(after)} (was {_market_text(before)})")
    for k in CONTRACT_KEYS + ACCOUNT_KEYS + COST_KEYS:
        a, b = _value(before, k), _value(after, k)
        if a != b:
            out.append(f"{LABELS[k]}: {_fmt(b)} (was {_fmt(a)})")
    return out


def same_footing(items: list[tuple[str, str, dict]], reference: int, match) -> dict:
    """``items``: (id, name, strategy dict). Returns each strategy's dict with the ``match``
    groups copied from ``items[reference]``, what changed in it, and plain notes on what is the
    same for all. Raises ValueError (with the strategy's name) when a result isn't a valid strategy."""
    match = {g for g in match if g in MATCH_GROUPS}
    ref_id, ref_name, ref_data = items[reference]
    ref = _parse(ref_data, ref_name)
    parsed = [_parse(d, name) for _, name, d in items]
    out = []
    for i, (sid, name, data) in enumerate(items):
        new, notes = copy.deepcopy(data), []
        if i != reference:
            if "market" in match:
                m = {k: v for k, v in (ref_data.get("market") or {}).items() if k in MARKET_KEYS}
                m["timeframe"] = parsed[i].market.timeframe
                new["market"] = m
                _copy(new, "risk", ref, CONTRACT_KEYS)
            if "account" in match:
                _copy(new, "risk", ref, ACCOUNT_KEYS)
            if "costs" in match:
                here = _parse(new, name)
                same = _same_market(here, ref)
                _copy(new, "costs", ref, COST_KEYS if same else tuple(k for k in COST_KEYS if k not in POINT_COSTS))
                if not same:
                    notes.append(f"Spread and slippage in price points stay as they were: {name} trades a different "
                                 f"market from {ref_name}, where the same number of points is a different amount.")
        after = _parse(new, name)
        out.append({"id": sid, "name": name, "data": new, "changes": _changes(parsed[i], after), "notes": notes,
                    "market": _market_text(after), "timeframe": after.market.timeframe,
                    "currency": after.risk.currency, "account_size": after.risk.account_size})
    return {"strategies": out, "same": _same_notes(out, ref_name, match), "reference": ref_id}


def _same_notes(out: list[dict], ref_name: str, match: set) -> list[str]:
    """What the strategies have in common after matching, in plain sentences."""
    notes = []
    markets = {x["market"] for x in out}
    frames = {x["timeframe"] for x in out}
    if "market" in match:
        notes.append(f"Market: {next(iter(markets))} for all, from {ref_name}, with its money per point, sizes "
                     "and margin. Each strategy keeps its own candle size.")
    elif len(markets) == 1:
        notes.append(f"All trade {next(iter(markets))}, so they read the same prices.")
    else:
        notes.append("They trade different markets, so the prices differ; the period is the same.")
    if len(frames) > 1:
        notes.append("Candle sizes differ (" + ", ".join(sorted(frames)) + "), so the candles each one reads differ too.")
    if "account" in match:
        notes.append(f"Starting balance, account currency, risk per trade, compounding and loss limits: from {ref_name}.")
    elif len({(x["currency"], x["account_size"]) for x in out}) > 1:
        notes.append("Each uses its own starting balance and risk settings, so returns are compared in %.")
    if "costs" in match:
        notes.append(f"Costs: from {ref_name}.")
    else:
        notes.append("Each uses its own costs.")
    return notes
