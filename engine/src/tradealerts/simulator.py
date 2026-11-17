"""Candle-by-candle trade simulation, shared by the backtester and live forward tests.

How trades are filled, modelled on a CFD/spread-bet broker:

* Prices in the data are mid prices. You buy at the ask (mid + half the spread)
  and sell at the bid (mid - half the spread). The spread is the candle's own
  spread when the data has one (e.g. OANDA), otherwise the strategy's setting.
* Signals are read on a candle's *close*; entries fill at the **next candle's open**.
* Stop and target levels are the price you'd actually get: a long's stop or
  target is hit when the *bid* reaches it, a short's when the *ask* does. A
  candle that opens beyond the stop fills at that open (gap risk). That includes
  the entry candle: a stop closer than the spread (plus entry slippage) is already
  passed at the fill, so the trade is out straight away at the bid (long) or ask (short).
* If a stop and target both fall inside one candle, finer candles (e.g. 1h inside
  4h) decide which came first. With no finer data the stop is assumed first.
* An exit rule (``exit.when``) closes at the next candle's open; ``max_bars``
  closes at the close of the Nth candle held (the entry candle counts as 1).
* Overnight funding is charged for each 22:00 UTC a position is held over, at
  ``costs.funding_pct`` a year on the position's value. Short positions also pay
  ``costs.borrow_pct`` a year (stock borrow fee) over the same nights. Each trade
  reports them apart as ``funding`` and ``borrow``; ``financing`` is the two together.
* ``costs.commission`` (money) and ``costs.commission_pct`` (% of the fill's value)
  are charged on the entry fill and again on the exit fill.
* ``costs.slippage`` (price points), ``costs.slippage_pct`` (% of the fill price) and
  ``costs.slippage_atr_pct`` (% of the 14-candle ATR known at the candle's open, so it
  grows when the market is busy) move every market fill against you on top of the
  spread: entries, stops, exit rules, time limits and the end-of-data close. Targets are
  limit orders, filled at their price or better, so they get none. Reported per trade as
  ``slippage_cost``.
* ``risk.max_daily_loss_pct``, ``risk.max_weekly_loss_pct`` and ``risk.max_drawdown_pct``
  stop new entries (a ``skipped`` event with ``limit`` set) as described in :mod:`limits`.
* With ``risk.size_currency: market`` the size is money per point in the market's own
  currency (``risk.market_currency``). Every amount (profit, costs, margin, sizing) is
  converted into the account currency at the rate known at the time (see :mod:`fxrates`):
  the profit at the exit's rate, funding night by night, each commission at its fill.
  ``fx_entry`` and ``fx`` on a trade are the rates at entry and exit (1 when nothing converts).
* Margin is ``risk.margin_pct`` of the position's value when it opens (``margin`` on a trade).
  ``risk.max_margin_pct`` skips an entry whose margin would be more than that % of the balance
  (limit ``margin``). ``risk.close_out_pct`` closes the trade at market (``close_out``) once the
  balance plus the open profit or loss falls below that % of the margin, checked like a stop.

State is a plain dict so live runs can save it to disk between checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from . import limits, timeframes
from .risk import position_size
from .strategy import SkipTrade, Strategy

_CUT = 22 * 3600 * 10**9  # funding cut-off, 22:00 UTC, in ns
_DAY = 86400 * 10**9
STATE_VERSION = 2  # 2: an open position's ``funding`` no longer includes its ``borrow``


class InvariantError(RuntimeError):
    """The simulator reached a state that should be impossible (a bug, not bad settings)."""


def nights_between(a, b) -> int:
    """How many 22:00 UTC funding cut-offs fall in (a, b]."""
    av, bv = pd.Timestamp(a).value, pd.Timestamp(b).value
    return max(0, (bv - _CUT) // _DAY - (av - _CUT) // _DAY)


@dataclass
class Event:
    kind: str  # signal | opened | closed | skipped
    time: pd.Timestamp
    data: dict[str, Any] = field(default_factory=dict)


def new_state(strategy: Strategy) -> dict:
    return {
        "version": STATE_VERSION,
        "equity": strategy.risk.account_size,
        "position": None,
        "pending_entry": None,
        "pending_exit": False,
        "trades": 0,
        "half_spread": strategy.spread / 2,
    }


def upgrade_state(state: dict) -> dict:
    """Bring a saved state up to date. Version 1 kept an open short's borrow fee inside ``funding``."""
    if state.get("version", 1) < 2:
        p = state.get("position")
        if p and p.get("borrow"):
            p["funding"] = p.get("funding", 0.0) - p["borrow"]
        state["version"] = 2
    return state


class Simulator:
    def __init__(self, strategy: Strategy, state: dict | None = None):
        self.s = strategy
        self.state = upgrade_state(state) if state is not None else new_state(strategy)
        self.bar = timeframes.to_timedelta(strategy.market.timeframe)
        r = strategy.risk
        self._limits = (r.max_daily_loss_pct, r.max_weekly_loss_pct, r.max_drawdown_pct)
        self._limited = any(x is not None for x in self._limits)
        self._convert = r.converts

    # ------------------------------------------------------------------ helpers

    @property
    def position(self) -> dict | None:
        return self.state["position"]

    def _half_spread(self, spread: float | None) -> float:
        if spread is None or (isinstance(spread, float) and math.isnan(spread)) or not self.s.use_data_spread:
            return self.s.spread / 2
        spread = float(spread)
        if not math.isfinite(spread) or spread < 0:
            raise ValueError(f"Candle spread must be a finite number of 0 or more, got {spread}")
        return spread / 2

    def _rate(self) -> float:
        """Account money per 1 unit of the market's money now (1 when nothing converts)."""
        r = self.s.risk
        if not self._convert:
            return 1.0
        if r.fx_rate:
            return r.fx_rate
        fx = self.state.get("fx")
        if fx is None:
            raise ValueError(f"No {r.market_currency}/{r.currency} exchange rate to convert this market's money")
        return fx

    def _sizing_equity(self) -> float:
        return self.state["equity"] if self.s.risk.compound else self.s.risk.account_size

    def size_for(self, entry: float, stop: float) -> float:
        r = self.s.risk
        return position_size(
            self._sizing_equity(), r.risk_per_trade_pct, abs(entry - stop),
            r.point_value * self._rate(), r.size_step, r.min_size, r.max_size,
        )

    def unrealised(self, price: float, half_spread: float | None = None) -> float:
        """Open profit/loss if closed now at the bid (long) or ask (short)."""
        p = self.position
        if not p:
            return 0.0
        hs = self.state.get("half_spread", 0.0) if half_spread is None else half_spread
        exit_px = price - self.s.sign * hs
        return (exit_px - p["entry_price"]) * self.s.sign * p["size"] * self.s.risk.point_value * self._rate()

    def _commission(self, price: float, size: float) -> float:
        """Cost of one fill at ``price``: a flat amount plus a % of the fill's value."""
        s = self.s
        return s.commission + price * size * s.risk.point_value * self._rate() * s.commission_pct / 100

    def _slip(self, price: float) -> float:
        """Price points lost to slippage on one market fill at ``price``."""
        atr = self.state.get("slip_atr") if self.s.slippage_atr_pct else None
        vol = atr * self.s.slippage_atr_pct / 100 if atr and math.isfinite(atr) else 0.0
        return self.s.slippage + abs(price) * self.s.slippage_pct / 100 + vol

    def _blocked(self, time) -> str | None:
        """Which loss limit, if any, stops a trade opening at ``time``."""
        if not self._limited:
            return None
        return limits.blocked(self.state.setdefault("limits", {}), time, self.state["equity"],
                              self.s.market.tz, *self._limits)

    def _accrue_funding(self, until, price: float) -> None:
        """Take overnight funding, and the borrow fee when short, for each 22:00 UTC passed."""
        p = self.state["position"]
        if not p:
            return
        borrow_pct = self.s.borrow_pct if self.s.sign < 0 else 0.0
        nights = nights_between(p["funded_until"], until) if self.s.funding_pct or borrow_pct else 0
        if nights:
            value = price * p["size"] * self.s.risk.point_value * self._rate()
            funding = nights * value * self.s.funding_pct / 100 / 365
            borrow = nights * value * borrow_pct / 100 / 365
            p["funding"] = p.get("funding", 0.0) + funding
            p["borrow"] = p.get("borrow", 0.0) + borrow
            self.state["equity"] -= funding + borrow
        p["funded_until"] = str(until)

    def _margin(self, price: float, size: float) -> float:
        """Margin a broker holds for a position of ``size`` opened at ``price``, in account money."""
        m = self.s.risk.margin_pct
        return price * size * self.s.risk.point_value * self._rate() * m / 100 if m else 0.0

    def _close_out_level(self, p: dict) -> float | None:
        """The exit-side price at which balance plus open P&L falls to the close-out level."""
        k = self.s.risk.close_out_pct
        if k is None or not p.get("margin"):
            return None
        per_point = p["size"] * self.s.risk.point_value * self._rate()
        return p["entry_price"] + self.s.sign * (k / 100 * p["margin"] - self.state["equity"]) / per_point

    def _excursion(self, p: dict, h: float, l: float, hs: float) -> None:
        """Track the worst and best the trade got to, at the side of the book it would exit on."""
        risk = abs(p["entry_price"] - p["stop"])
        if not risk:
            return
        sign = self.s.sign
        best = ((h - hs) if sign > 0 else (l + hs)) - p["entry_price"]
        worst = ((l - hs) if sign > 0 else (h + hs)) - p["entry_price"]
        p["mfe_r"] = max(p.get("mfe_r", 0.0), best * sign / risk)
        p["mae_r"] = min(p.get("mae_r", 0.0), worst * sign / risk)

    def _close(self, time, exit_price: float, reason: str, exit_half_spread: float) -> Event:
        """Close at ``exit_price``, the actual bid (long) or ask (short) received.

        ``time`` is when the exit happened: a candle's start for exits at its open or
        inside it, its end for exits at its close (time limit, end of data)."""
        p = self.state["position"]
        s = self.s
        if not p:
            raise InvariantError(f"A trade can't be closed twice: nothing is open at {time}")
        if pd.Timestamp(time) < pd.Timestamp(p["entry_time"]):
            raise InvariantError(f"A trade can't close ({time}) before it opens ({p['entry_time']})")
        slip = 0.0 if reason == "target" else self._slip(exit_price)
        exit_price -= s.sign * slip
        if not math.isfinite(exit_price):
            raise InvariantError(f"Exit price at {time} isn't a number")
        fx, fx_in = self._rate(), p.get("fx_entry", 1.0)
        gross = (exit_price - p["entry_price"]) * s.sign * p["size"] * s.risk.point_value * fx
        funding, borrow = p.get("funding", 0.0), p.get("borrow", 0.0)
        financing = funding + borrow
        commission = p.get("commission", 0.0) + self._commission(exit_price, p["size"])
        pnl = gross - financing - commission
        risk_amount = abs(p["entry_price"] - p["stop"]) * p["size"] * s.risk.point_value * fx_in
        exit_r = (exit_price - p["entry_price"]) * s.sign / abs(p["entry_price"] - p["stop"]) if p["stop"] != p["entry_price"] else 0.0
        spread_cost = (p.get("entry_half_spread", 0.0) * fx_in + exit_half_spread * fx) * p["size"] * s.risk.point_value
        slippage_cost = (p.get("entry_slippage", 0.0) * fx_in + slip * fx) * p["size"] * s.risk.point_value
        # funding was already taken night by night and the entry commission on the fill
        self.state["equity"] += gross - (commission - p.get("commission", 0.0))
        self.state["position"] = None
        self.state["pending_exit"] = False
        self.state["trades"] += 1
        trade = {
            "direction": s.direction,
            "signal_time": p["signal_time"],
            "entry_time": p["entry_time"],
            "entry_price": p["entry_price"],
            "exit_time": str(time),
            "exit_price": exit_price,
            "size": p["size"],
            "stop": p["stop"],
            "target": p["target"],
            "exit_reason": reason,
            "pnl": pnl,
            "r_multiple": pnl / risk_amount if risk_amount else 0.0,
            "bars_held": p["bars_held"],
            "equity_after": self.state["equity"],
            "funding": funding,
            "spread_cost": spread_cost,
            "borrow": borrow,
            "commission": commission,
            "slippage_cost": slippage_cost,
            "entry_reason": p.get("entry_reason") or {},
            "exit_values": getattr(self, "_values", {}) or {},
            # the closing candle may run on past the stop or target after the trade is out,
            # so the worst/best point while open can't go beyond the exit price itself
            "mae_r": max(p.get("mae_r", 0.0), exit_r) if exit_r < 0 else p.get("mae_r", 0.0),
            "mfe_r": min(p.get("mfe_r", 0.0), exit_r) if exit_r > 0 else p.get("mfe_r", 0.0),
            "financing": financing,  # funding + borrow; same order as the saved paper-trade columns
            "fx_entry": fx_in,
            "fx": fx,
            "margin": p.get("margin", 0.0),
        }
        return Event("closed", time, trade)

    def _levels(self, p: dict, o: float, h: float, l: float, hs: float):
        """Which of stop/target this candle reaches, at the side of the book we'd exit on.

        Returns (reason, price) for a gap through a level at the open, otherwise
        (stop_hit, target_hit) booleans as ("hits", stop_hit, target_hit).
        """
        sign, stop, target = self.s.sign, p["stop"], p["target"]
        if sign > 0:  # long exits at the bid
            bo, bh, bl = o - hs, h - hs, l - hs
            if bo <= stop:
                return ("stop", bo)
            if target is not None and bo >= target:
                return ("target", bo)
            return ("hits", bl <= stop, target is not None and bh >= target)
        ao, ah, al = o + hs, h + hs, l + hs  # short exits at the ask
        if ao >= stop:
            return ("stop", ao)
        if target is not None and ao <= target:
            return ("target", ao)
        return ("hits", ah >= stop, target is not None and al <= target)

    def _resolve_exit(self, p, o, h, l, hs, fine: Callable[[], Any] | None):
        # On the entry candle the open is the fill, so a target can't be past it there, but a
        # stop inside the spread can: that's an exit at the open, like any gap through the stop.
        res = self._levels(p, o, h, l, hs)
        if res[0] != "hits":
            return res
        _, stop_hit, target_hit = res
        if stop_hit and not target_hit:
            return ("stop", p["stop"])
        if target_hit and not stop_hit:
            return ("target", p["target"])
        if not stop_hit and not target_hit:
            return None
        # Both inside one candle: look at the finer candles to see which came first.
        rows = fine() if fine else None
        if rows is not None and len(rows):
            for r in rows.itertuples(index=False):
                sub = self._levels(p, r.open, r.high, r.low, hs)
                if sub[0] != "hits":
                    return sub
                if sub[1]:
                    return ("stop", p["stop"])  # stop first, or both in the same finer candle
                if sub[2]:
                    return ("target", p["target"])
        return ("stop", p["stop"])

    def _exit_hit(self, p, o, h, l, hs, fine):
        """Stop, target or close-out inside this candle, as (reason, price), or None."""
        sign, level = self.s.sign, self._close_out_level(p)
        if level is None or (level - p["stop"]) * sign <= 0:  # the stop comes first, or no close-out
            return self._resolve_exit(p, o, h, l, hs, fine)
        if (level - (o - sign * hs)) * sign >= 0:  # already past it when the candle opens
            return ("close_out", o - sign * hs)
        hit = self._resolve_exit({**p, "stop": level}, o, h, l, hs, fine)
        return ("close_out", hit[1]) if hit and hit[0] == "stop" else hit

    def _open(self, time, pend, fill, stop, target, size, hs, slip, margin, events) -> None:
        st = self.state
        st["position"] = {
            "signal_time": pend["signal_time"],
            "entry_time": str(time),
            "entry_price": fill,
            "stop": stop,
            "target": target,
            "size": size,
            "bars_held": 0,
            "entry_half_spread": hs,
            "entry_slippage": slip,
            "funding": 0.0,
            "borrow": 0.0,
            "commission": self._commission(fill, size),
            "funded_until": str(time),
            "entry_reason": pend.get("reason") or {},
            "mae_r": 0.0,  # worst point while open, in R (0 or below)
            "mfe_r": 0.0,  # best point while open, in R (0 or above)
            "fx_entry": self._rate(),
            "margin": margin,
        }
        st["equity"] -= st["position"]["commission"]
        events.append(Event("opened", time, {k: v for k, v in st["position"].items()
                                             if k not in ("funded_until",)}))

    # ------------------------------------------------------------------ main step

    def step(
        self,
        time: pd.Timestamp,
        o: float, h: float, l: float, c: float,
        entry_signal: bool,
        exit_signal: bool,
        refs: dict[str, float | None],
        spread: float | None = None,
        fine: Callable[[], Any] | None = None,
        values: dict[str, float | None] | None = None,
        fx: float | None = None,
    ) -> list[Event]:
        """``values``: what the entry rule read on this candle (indicator values), kept with the trade.
        ``fx``: account money per 1 unit of the market's money known at this candle's open."""
        s, st, sign = self.s, self.state, self.s.sign
        if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l) and math.isfinite(c)):
            raise ValueError(f"Candle at {time} has a missing or invalid price")
        hs = self._half_spread(spread)
        st["half_spread"] = hs
        self._values = values or {}
        events: list[Event] = []
        if s.slippage_atr_pct:
            st["slip_atr"] = refs.get("slip_atr")
        if self._convert and fx is not None and math.isfinite(fx) and fx > 0:
            st["fx"] = float(fx)
        if self._limited:  # start a new day or week from the balance it opened with
            limits.roll(st.setdefault("limits", {}), time, st["equity"], s.market.tz)

        # 0. funding for nights since the last candle, up to this open
        if st["position"]:
            self._accrue_funding(time, o)

        # 1. exit rule from the previous candle -> out at this open
        if st["position"] and st["pending_exit"]:
            events.append(self._close(time, o - sign * hs, "exit_rule", hs))

        # 2. entry signal from the previous candle -> in at this open
        pend = st["pending_entry"]
        st["pending_entry"] = None
        why = self._blocked(time) if pend and not st["position"] else None
        if why:
            events.append(Event("skipped", time, {"reason": limits.REASONS[why], "limit": why}))
            pend = None
        if pend and not st["position"]:
            slip = self._slip(o + sign * hs)
            fill = o + sign * (hs + slip)
            try:
                stop, target = s.plan(fill, pend["refs"])
                size = self.size_for(fill, stop)
                if not math.isfinite(size) or size <= 0:
                    raise SkipTrade("position size rounds to zero at your risk settings")
                margin = self._margin(fill, size)
            except SkipTrade as exc:
                events.append(Event("skipped", time, {"reason": str(exc)}))
            else:
                cap = s.risk.max_margin_pct
                if cap is not None and margin > st["equity"] * cap / 100 + 1e-9 * max(1.0, abs(st["equity"])):
                    events.append(Event("skipped", time, {"reason": limits.REASONS["margin"], "limit": "margin"}))
                else:
                    self._open(time, pend, fill, stop, target, size, hs, slip, margin, events)

        # 3. stop / target inside this candle
        p = st["position"]
        if p:
            self._excursion(p, h, l, hs)
            hit = self._exit_hit(p, o, h, l, hs, fine)
            if hit:
                events.append(self._close(time, hit[1], hit[0], hs))

        # 4. time limit and exit rule
        p = st["position"]
        if p:
            p["bars_held"] += 1
            if s.max_bars and p["bars_held"] >= s.max_bars:
                self._accrue_funding(time + self.bar, c)
                events.append(self._close(time + self.bar, c - sign * hs, "max_bars", hs))
            elif exit_signal:
                st["pending_exit"] = True

        # 5. funding up to this candle's close for anything still open
        if st["position"]:
            self._accrue_funding(time + self.bar, c)

        # 6. new entry signal on this candle's close
        if entry_signal and not st["position"] and not st["pending_entry"]:
            info: dict[str, Any] = {"ref_price": c}
            try:
                stop, target = s.plan(c, refs)
                info.update(stop=stop, target=target, size=self.size_for(c, stop))
            except SkipTrade as exc:
                info.update(stop=None, target=None, size=0.0, warning=str(exc))
            st["pending_entry"] = {"signal_time": str(time), "refs": refs, "reason": self._values}
            events.append(Event("signal", time, info))

        return events

    def finish(self, time, price: float) -> list[Event]:
        """Close any open position at the last price (end of a backtest)."""
        if self.state["position"]:
            hs = self.state.get("half_spread", self.s.spread / 2)
            self._values = {}
            return [self._close(pd.Timestamp(time) + self.bar, price - self.s.sign * hs, "end_of_data", hs)]
        return []
