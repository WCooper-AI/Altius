"""Typed request and response bodies for the API (Pydantic).

Each check raises a ``PydanticCustomError`` whose message is the plain sentence shown to
the user, and ``plain_errors`` turns a failed request into one string, the same shape as
every other error the API sends.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from ..recipes import MARKETS_BY_KEY

MAX_COOLDOWN = 7 * 24 * 60  # minutes


def _problem(message: str) -> PydanticCustomError:
    return PydanticCustomError("plain", message)


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def plain_errors(errors: list[dict]) -> str:
    """One readable sentence per problem; our own checks already read as sentences."""
    out = []
    for e in errors:
        if e.get("type") == "plain":
            msg = e["msg"]
        else:
            field = ".".join(str(x) for x in e.get("loc", ())[1:]) or "request"
            msg = f"{field}: {e.get('msg', 'not valid')}"
        if msg not in out:
            out.append(msg)
    return "; ".join(out) or "The request was not valid"


class PriceAlertEdit(BaseModel):
    """What you can change on a price alert. Editing re-arms it from now."""

    model_config = ConfigDict(extra="ignore")

    direction: Optional[str] = Field(None, validate_default=True, description="above or below")
    price: Optional[float] = Field(None, validate_default=True, description="The level to watch, above 0")
    note: str = Field("", description="Up to 200 characters")
    repeat: str = Field("once", description="once, or every time the price gets there")
    cooldown_minutes: int = Field(60, description=f"Minutes to wait before it can fire again (0 to {MAX_COOLDOWN})")
    cross: bool = Field(False, description="Only fire when the price crosses the level, not when it starts past it")
    active: bool = True

    @field_validator("direction", mode="before")
    @classmethod
    def _direction(cls, v):
        if v not in ("above", "below"):
            raise _problem("Choose above or below")
        return v

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, v):
        if not _number(v) or not v > 0 or v == float("inf"):
            raise _problem("Fill in the price")
        return float(v)

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, v):
        return str(v or "")[:200]

    @field_validator("repeat", mode="before")
    @classmethod
    def _repeat(cls, v):
        v = v or "once"
        if v not in ("once", "every"):
            raise _problem("Choose once or every time")
        return v

    @field_validator("cooldown_minutes", mode="before")
    @classmethod
    def _cooldown(cls, v):
        if v is None:
            return 60
        if not _number(v) or not 0 <= v <= MAX_COOLDOWN:
            raise _problem("The cooldown must be between 0 minutes and 7 days")
        return int(v)

    @field_validator("cross", "active", mode="before")
    @classmethod
    def _flag(cls, v):
        return bool(v)

    def fields(self) -> dict:
        """The alert settings as the store keeps them."""
        return {"direction": self.direction, "price": self.price, "note": self.note, "repeat": self.repeat,
                "cooldown_minutes": self.cooldown_minutes, "cross": self.cross}


class PriceAlertIn(PriceAlertEdit):
    """A new price alert on one of the built-in markets."""

    market: Optional[str] = Field(None, validate_default=True, description="A market key, e.g. gold")

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, v):
        if str(v) not in MARKETS_BY_KEY:
            raise _problem("Choose a market")
        return str(v)


class PriceAlert(BaseModel):
    """A stored price alert as the API returns it. Flags are 1 or 0, as stored."""

    model_config = ConfigDict(extra="ignore")

    id: int
    market: str
    label: Optional[str] = None
    direction: str
    price: float
    note: Optional[str] = None
    created_at: str
    checked_to: Optional[str] = None
    triggered_at: Optional[str] = None
    triggered_price: Optional[float] = None
    active: int
    repeat: str
    cooldown_minutes: int
    cross: int
    fired_count: int = 0
    last_fired_at: Optional[str] = None


class PriceAlertList(BaseModel):
    alerts: list[PriceAlert]
    poll_minutes: Optional[int | float] = None


class Deleted(BaseModel):
    deleted: int
