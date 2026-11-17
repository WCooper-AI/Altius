"""The parts of a backtest's run record that say which code and which request produced it.

``web.routes.testing.stamp`` records the settings (market, costs, sizing, data fingerprint).
This adds what makes the run repeatable on another computer or a later version of the app:
the strategy's own rules, a version code for each indicator the rules read (taken from the
indicator's code, so a change to how an indicator is worked out changes its code), the
period that was asked for, the time zones in use and the library versions.
"""

from __future__ import annotations

import hashlib
import inspect
import platform
import types

import numpy as np
import pandas as pd

from . import indicators, versions


def _source(fn) -> str:
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):  # no source files (a frozen build): fall back to the compiled code
        code = fn.__code__
        return code.co_code.hex() + repr(code.co_consts)


def _names(code: types.CodeType) -> set[str]:
    """Every global name a function (and any function or lambda inside it) reads."""
    out = set(code.co_names)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            out |= _names(c)
    return out


def _parts(fn, seen: dict[str, str]) -> None:
    """The source of ``fn`` and of every helper and constant it uses from the indicators module."""
    if fn.__name__ in seen:
        return
    seen[fn.__name__] = _source(fn)
    for name in sorted(_names(fn.__code__)):
        g = getattr(indicators, name, None)
        if inspect.isfunction(g) and g.__module__ == indicators.__name__:
            _parts(g, seen)
        elif isinstance(g, (int, float, str, tuple)) and not name.startswith("__"):
            seen.setdefault(name, f"{name} = {g!r}")


def indicator_version(name: str) -> str:
    """A 12-character code for how ``name`` is worked out: its function and the helpers it calls."""
    fn = indicators.REGISTRY[name]
    seen: dict[str, str] = {}
    _parts(fn, seen)
    text = "\n".join(seen[k] for k in sorted(seen))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _walk(node, out: set[str]) -> None:
    if isinstance(node, dict):
        name = node.get("indicator")
        if isinstance(name, str) and name in indicators.REGISTRY:
            out.add(name)
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def indicators_used(s) -> list[str]:
    """Every indicator the strategy's entry, exit, stop, target and costs read, sorted."""
    out: set[str] = set()
    for spec in (s.entry_spec, s.exit_spec, s.stop_spec, s.target_spec):
        _walk(spec, out)
    for spec in (s.stop_spec, s.target_spec):
        if isinstance(spec, dict) and "atr" in spec:
            out.add("atr")
    if s.slippage_atr_pct:
        out.add("atr")
    return sorted(out)


def _period(body: dict) -> dict:
    """The period as it was asked for (the stamp's start and end are what the data gave)."""
    return {k: body.get(k) for k in ("years", "start", "end") if body.get(k) not in (None, "")}


def reproducibility(s, body: dict) -> dict:
    """Extra run-record fields: who, which code and which request. JSON-ready."""
    sid = body.get("strategy_id")
    data = body.get("data") or {}
    return {
        "strategy_name": s.name,
        "strategy_id": sid if isinstance(sid, str) else None,
        "strategy_rules": {k: data[k] for k in versions.RULE_KEYS if k in data},
        "indicators": [{"name": n, "version": indicator_version(n)} for n in indicators_used(s)],
        "requested_period": _period(body),
        "timezone": {"prices": "UTC", "market": s.market.tz, "candle_alignment": s.market.candle_alignment},
        "use_data_spread": s.use_data_spread,
        "libraries": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__},
    }
