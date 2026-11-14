"""Loss limits: stop opening new trades after a bad day, a bad week or a deep fall.
(The margin limit, ``margin``, is checked where trades are sized; it shares the reasons and note.)

Shared by one strategy's simulator and the shared-account portfolio. Losses are measured on
the cash balance (closed trades and costs paid, not open trades' running profit), so a
trade's loss counts on the day it closes. Days and weeks follow the given time zone and a
week starts on Monday. The daily and weekly limits lift when the next day or week starts.
The drawdown limit is measured from the highest balance so far and, once reached, stops new
trades for the rest of the test (or forward test).

The tracker is a plain dict so live runs can save it with the rest of the simulator state.
"""

from __future__ import annotations

import pandas as pd

REASONS = {
    "daily_loss": "the daily loss limit was reached; new trades resume the next day",
    "weekly_loss": "the weekly loss limit was reached; new trades resume next week",
    "drawdown": "the drawdown limit was reached; no new trades for the rest of this test",
    "margin": "the margin this trade needs is more than your margin limit allows",
}


def _utc(time) -> pd.Timestamp:
    t = pd.Timestamp(time)
    return t.tz_localize("UTC") if t.tzinfo is None else t


def roll(track: dict, time, balance: float, tz: str = "UTC") -> None:
    """Bring ``track`` up to ``time``. Call it before every change to the balance, so each
    new day or week starts from the balance it really opened with."""
    t = _utc(time)
    if t.value >= track.get("day_end", -1):
        day = t.tz_convert(tz).tz_localize(None).normalize()  # local midnight, as wall-clock time
        track["day_end"] = _local(day + pd.Timedelta(days=1), tz)
        track["day_start"] = balance
        if t.value >= track.get("week_end", -1):
            track["week_end"] = _local(day + pd.Timedelta(days=7 - day.weekday()), tz)
            track["week_start"] = balance
    track["peak"] = max(track.get("peak", balance), balance)


def _local(wall: pd.Timestamp, tz: str) -> int:
    """A wall-clock time in ``tz`` as UTC nanoseconds (a clock change at midnight moves it on)."""
    return wall.tz_localize(tz, nonexistent="shift_forward", ambiguous=True).value


def blocked(track: dict, time, balance: float, tz: str, daily_pct: float | None,
            weekly_pct: float | None, drawdown_pct: float | None) -> str | None:
    """Which limit, if any, stops a new trade opening at ``time``: a key of :data:`REASONS`."""
    roll(track, time, balance, tz)

    def reached(start: float, pct: float | None) -> bool:
        return pct is not None and balance <= start * (1 - pct / 100) + 1e-9 * max(1.0, abs(start))

    if drawdown_pct is not None and (track.get("halted") or reached(track["peak"], drawdown_pct)):
        track.setdefault("halted", str(_utc(time)))
        return "drawdown"
    if reached(track["day_start"], daily_pct):
        return "daily_loss"
    if reached(track["week_start"], weekly_pct):
        return "weekly_loss"
    return None


def note(skips: list[tuple]) -> str | None:
    """One plain sentence on how many trades the limits stopped, from (time, limit) pairs."""
    if not skips:
        return None
    words = {"daily_loss": "the daily loss limit", "weekly_loss": "the weekly loss limit",
             "drawdown": "the drawdown limit"}
    counts = {k: sum(1 for _, x in skips if x == k) for k in words}
    n = sum(counts.values())
    parts = [f"{c} by {words[k]}" for k, c in counts.items() if c]
    text = f"Your loss limits stopped {n} trade{'s' if n != 1 else ''} from opening: {', '.join(parts)}." if n else ""
    halted = [t for t, x in skips if x == "drawdown"]
    if halted:
        text += f" The drawdown limit first stopped a trade on {pd.Timestamp(halted[0]):%d %b %Y}."
    m = sum(1 for _, x in skips if x == "margin")
    if m:
        text += (" " if text else "") + (f"Your margin limit stopped {m} trade{'s' if m != 1 else ''} from "
                                         "opening, because the margin needed was more than it allows.")
    return text or None
