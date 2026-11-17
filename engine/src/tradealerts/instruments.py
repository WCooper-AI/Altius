"""What each built-in market is: one typed record per market, read from the catalogue in
``recipes.MARKETS``.

Everything the rest of the app needs to know about a market lives here: its currencies
(base and quote), what one point of price is worth per unit of size (contract size), the
smallest price step and what that step is worth, the smallest size and size step, its
trading hours, margin rate and financing costs, and which currency pair converts its
money into another currency. A strategy's own settings still win; these are the
defaults a blank setting falls back to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .recipes import MARKETS

HOURS = {
    "Indices": "Almost 24 hours, Monday to Friday",
    "Commodities": "Almost 24 hours, Monday to Friday",
    "Currencies": "24 hours, Monday to Friday",
    "Crypto": "Every day, around the clock",
    "Try it out": "24 hours, Monday to Friday (made-up prices)",
}
EXCHANGE_HOURS = {"XNYS": "New York exchange hours: 9:30 to 16:00 New York time, Monday to Friday"}


@dataclass(frozen=True)
class Instrument:
    key: str
    label: str
    group: str
    symbol: str
    quote: str                  # the currency prices are in
    base: str | None            # what is being priced, for currencies, metals and crypto
    point_value: float          # quote money per 1.0 of price, per 1 unit of size
    tick_size: float            # the smallest price step
    spread: float               # usual gap between buying and selling price, in price
    margin_pct: float | None    # % of a position's value a broker usually holds
    exchange: str | None        # exchange calendar code when it only trades in exchange hours
    funding_pct: float = 0.0    # yearly overnight financing, % of position value
    borrow_pct: float = 0.0     # yearly cost of borrowing shares to sell short, %
    commission: float = 0.0     # per unit of size, each side
    commission_pct: float = 0.0  # % of trade value, each side
    min_size: float = 0.01
    size_step: float = 0.01
    note: str = ""

    @property
    def tick_value(self) -> float:
        """Quote money one price step is worth for 1 unit of size."""
        return self.tick_size * self.point_value

    @property
    def hours(self) -> str:
        if self.exchange:
            return EXCHANGE_HOURS.get(self.exchange, f"{self.exchange} exchange hours")
        return HOURS.get(self.group, "")

    def conversion(self, account: str) -> tuple[str, bool] | None:
        """(market key, invert) of the pair that turns this market's money into ``account``
        money, or None when it is the same currency or there is no built-in pair."""
        if not account or account.upper() == self.quote:
            return None
        from .fxrates import pair_for  # pulls in the price downloaders

        return pair_for(self.quote, account)

    def round_price(self, price: float) -> float:
        """``price`` on the nearest price step."""
        steps = round(price / self.tick_size)
        return round(steps * self.tick_size, max(0, -math.floor(math.log10(self.tick_size))) + 2)

    def round_size(self, size: float) -> float:
        """``size`` rounded down to the size step; 0 when below the smallest size."""
        steps = math.floor(size / self.size_step + 1e-9)
        out = round(steps * self.size_step, 10)
        return out if out >= self.min_size else 0.0

    def defaults(self) -> dict:
        """The values a strategy's blank risk and cost settings fall back to."""
        return {"point_value": self.point_value, "spread": self.spread, "funding": self.funding_pct,
                "borrow": self.borrow_pct, "commission": self.commission, "commission_pct": self.commission_pct,
                "quote": self.quote, "margin": self.margin_pct, "min_size": self.min_size,
                "size_step": self.size_step}

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "group": self.group, "quote": self.quote, "base": self.base,
                "point_value": self.point_value, "tick_size": self.tick_size, "tick_value": round(self.tick_value, 10),
                "min_size": self.min_size, "size_step": self.size_step, "spread": self.spread,
                "margin_pct": self.margin_pct, "funding_pct": self.funding_pct, "borrow_pct": self.borrow_pct,
                "commission": self.commission, "commission_pct": self.commission_pct, "exchange": self.exchange,
                "hours": self.hours}


def _from(m: dict) -> Instrument:
    return Instrument(
        key=m["key"], label=m["label"], group=m["group"], symbol=m["symbol"], quote=m["quote"], base=m.get("base"),
        point_value=float(m["point_value"]), tick_size=float(m["tick"]), spread=float(m["spread"]),
        margin_pct=m.get("margin"), exchange=m.get("exchange"), funding_pct=float(m.get("funding", 0.0)),
        borrow_pct=float(m.get("borrow", 0.0)), commission=float(m.get("commission", 0.0)),
        commission_pct=float(m.get("commission_pct", 0.0)), min_size=float(m.get("min_size", 0.01)),
        size_step=float(m.get("size_step", 0.01)), note=m.get("note", ""))


INSTRUMENTS: dict[str, Instrument] = {m["key"]: _from(m) for m in MARKETS}


def get(key: str | None) -> Instrument | None:
    """The built-in market ``key`` (e.g. "gold"), or None for a custom symbol."""
    return INSTRUMENTS.get(key) if key else None


def defaults(key: str | None) -> dict:
    """A built-in market's defaults (see ``Instrument.defaults``); {} for anything else."""
    inst = get(key)
    return inst.defaults() if inst else {}
