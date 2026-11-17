"""Testing tools: backtest, compare settings (sweep), optimiser, luck check, strategies together."""

from __future__ import annotations

import copy
import os

import pandas as pd
from fastapi import APIRouter, Body, Depends, Request

from ... import __version__, analysis, compute, manifest, sidebyside, stages, versions
from ...backtest import MAX_SPLIT_PCT, MIN_SPLIT_PCT
from ...jobs import Busy
from ...recipes import RecipeError, build_strategy
from ...strategy import ID_RE, read_raw, strategy_from_dict
from ...workspace import Workspace
from ..common import CURRENCY_SYMBOLS, clean, ctx_of, err, workspace
from ..prices import load_period
from ..strategy_io import load, now_iso, strategy_path, validate

router = APIRouter()

MAX_SWEEP = 25


def _run(request: Request, ws: Workspace, fn, *args):
    try:
        return ctx_of(request).jobs.run(ws.owner, fn, *args)
    except Busy as exc:
        err(429, str(exc))
    except ValueError as exc:
        err(422, str(exc))


def record_test(ws: Workspace, body: dict, *kinds: str) -> None:
    """Remember that these tests ran on a saved strategy (``strategy_id`` in the request), so its
    stage strip can say what has been done. Unsaved drafts leave no record."""
    sid = body.get("strategy_id")
    if not (isinstance(sid, str) and ID_RE.match(sid)):
        return
    if not (ws.config().strategies_dir / f"{sid}.yaml").exists():
        return
    fp = versions.rules_fingerprint(body.get("data"))
    with ws.store() as store:
        for kind in kinds:
            store.add_test(sid, kind, fp)


@router.post("/api/backtest")
def backtest(request: Request, body: dict = Body(...), ws: Workspace = Depends(workspace)):
    s = validate(ws, body.get("data"))
    split = body.get("split_pct")
    if split is not None and (isinstance(split, bool) or not isinstance(split, (int, float))
                              or not MIN_SPLIT_PCT <= split <= MAX_SPLIT_PCT):
        err(422, f"split_pct must be a number from {MIN_SPLIT_PCT} to {MAX_SPLIT_PCT}")
    df, fine, test_start, notes = load_period(ctx_of(request), ws, s, body)
    out = _run(request, ws, compute.backtest, body["data"], df, fine, test_start, notes,
               None if split is None else float(split))
    out["meta"] = stamp(ws, s, body, out["metrics"], out)
    record_test(ws, body, *stages.test_kinds(body))
    return out


@router.post("/api/signals/count")
def count_signals(request: Request, body: dict = Body(...), ws: Workspace = Depends(workspace)):
    """How often the entry rules fired on past data, without running the trade simulator."""
    s = validate(ws, body.get("data"))
    df, _fine, test_start, notes = load_period(ctx_of(request), ws, s, body)
    p = s.prepare(df)
    mask = (df.index >= test_start) if test_start is not None else df.index == df.index
    entry = p.entry[mask]
    return {
        "signals": int(entry.sum()),
        "candles": int(mask.sum()),
        "start": df.index[mask][0].isoformat() if mask.any() else None,
        "end": df.index[mask][-1].isoformat() if mask.any() else None,
        "notes": notes,
    }


SOURCE_NAMES = {"dukascopy": "Dukascopy", "yahoo": "Yahoo Finance", "oanda": "OANDA", "csv": "CSV file"}


def price_basis(source: str, used_data_spread: bool | None, use_data_spread: bool) -> str:
    """Where the fill prices and the spread came from, in one plain sentence."""
    if used_data_spread:
        return "Mid prices from bid and ask candles; each fill pays that candle's own spread"
    if not use_data_spread:
        return "Mid prices with the fixed spread you set (the data's own spread is switched off)"
    if source == "yahoo":
        return "Yahoo Finance prices have no bid or ask, so they are treated as mid prices with the fixed spread added"
    if source == "csv":
        return "Your file has no spread column, so its prices are treated as mid prices with the fixed spread added"
    return "This price data came without bid and ask prices, so the fixed spread is added"


def stamp(ws: Workspace, s, body: dict, metrics: dict, out: dict | None = None) -> dict:
    """Everything needed to run the same backtest again and get the same answer.
    ``out`` (the backtest's own result) adds the data fingerprint and data checks."""
    out = out or {}
    r = s.risk
    version = None
    sid = body.get("strategy_id")
    if isinstance(sid, str) and ID_RE.match(sid):
        with ws.store(create=False) as store:
            v = versions.version_with_rules(store, sid, body["data"]) if store else None
            version = v["version_no"] if v else None
    source = ws.config().data.get("source", "dukascopy") if s.market.provider == "auto" else s.market.provider
    return {
        "engine_version": __version__,
        "rules_hash": versions.rules_fingerprint(body["data"]),
        "version": version,                # saved version these exact rules match, if any
        **manifest.reproducibility(s, body),  # name, rules, indicator code versions, period asked, time zones
        "market": s.market.label,
        "symbol": s.market.symbol,
        "source": SOURCE_NAMES.get(source, source),
        "timeframe": s.market.timeframe,
        "start": metrics.get("start"),
        "end": metrics.get("end"),
        "warmup_candles": metrics.get("warmup_candles"),
        "spread": s.spread,
        "used_data_spread": metrics.get("used_data_spread"),
        "funding_pct": s.funding_pct,
        "slippage": s.slippage,
        "slippage_pct": s.slippage_pct,
        "slippage_atr_pct": s.slippage_atr_pct,
        "prices": price_basis(source, metrics.get("used_data_spread"), s.use_data_spread),
        "commission": s.commission,
        "commission_pct": s.commission_pct,
        "borrow_pct": s.borrow_pct,
        "direction": s.direction,
        "risk": {"account_size": r.account_size, "risk_per_trade_pct": r.risk_per_trade_pct,
                 "point_value": r.point_value, "size_step": r.size_step, "min_size": r.min_size,
                 "max_size": r.max_size, "compound": r.compound, "currency": r.currency,
                 "max_daily_loss_pct": r.max_daily_loss_pct, "max_weekly_loss_pct": r.max_weekly_loss_pct,
                 "max_drawdown_pct": r.max_drawdown_pct, "size_currency": r.size_currency,
                 "market_currency": r.market_currency, "fx_rate": r.fx_rate, "margin_pct": r.margin_pct,
                 "max_margin_pct": r.max_margin_pct, "close_out_pct": r.close_out_pct},
        "fx": metrics.get("fx"),           # how the market's money was converted, when it was
        "blocked_by_limits": metrics.get("blocked_by_limits"),
        "max_margin_used_pct": metrics.get("max_margin_used_pct"),
        "closed_out": metrics.get("closed_out"),
        "split_pct": metrics.get("split_pct"),
        "used_finer_candles": metrics.get("used_finer_candles"),
        "random_seed": analysis.SEED,
        "data_fingerprint": out.get("data_fingerprint"),
        "data_candles": out.get("data_candles"),
        "data_checks": (out.get("data_quality") or {}).get("counts"),
        "tested_at": now_iso(),
    }


def set_path(data: dict, path: list, value) -> None:
    node = data
    for key in path[:-1]:
        if isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
            node = node[key]
        elif isinstance(node, dict) and isinstance(key, str) and key in node:
            node = node[key]
        else:
            err(422, "That setting isn't in this strategy any more")
    last = path[-1]
    if isinstance(node, dict) and isinstance(last, str):
        node[last] = value
    elif isinstance(node, list) and isinstance(last, int) and 0 <= last < len(node):
        node[last] = value
    else:
        err(422, "That setting isn't in this strategy any more")


def check_setting(setting: dict) -> None:
    kind = setting.get("kind")
    if kind not in ("knob", "path"):
        err(422, "Choose a setting to test")
    path = setting.get("path")
    if kind == "path" and (not isinstance(path, list) or not path or len(path) > 12):
        err(422, "Choose a setting to test")


def apply_setting(d: dict, setting: dict, v):
    """Set one setting to ``v`` in strategy dict ``d`` (in place). Returns an error message for
    a value the recipe rejects, else None."""
    if setting.get("kind") == "knob":
        r = d.get("recipe") or {}
        settings = dict(r.get("settings") or {})
        settings[str(setting.get("key"))] = v
        try:
            built = build_strategy(str(r.get("id", "")), str(r.get("market", "")), settings)
        except RecipeError as exc:
            return str(exc)
        for k in ("direction", "entry", "exit", "recipe"):
            d[k] = built[k]
        d["market"] = {**(d.get("market") or {}), "timeframe": built["market"]["timeframe"]}
    else:
        set_path(d, setting.get("path"), v)
    return None


def sweep_variants(data: dict, setting: dict, values: list) -> list:
    """(value, strategy dict or error text) for each value of one setting."""
    check_setting(setting)
    out = []
    for v in values:
        d = copy.deepcopy(data)
        problem = apply_setting(d, setting, v)
        out.append((v, problem if problem else d))
    return out


def check_values(values) -> None:
    if not isinstance(values, list) or not values:
        err(422, "Give at least one value for each setting")
    if len(values) > MAX_SWEEP:
        err(422, f"Compare at most {MAX_SWEEP} values of one setting. Use a bigger step.")
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            err(422, "Values must be numbers or options")


@router.post("/api/sweep")
def sweep_settings(request: Request, body: dict = Body(...), ws: Workspace = Depends(workspace)):
    s = validate(ws, body.get("data"))
    values = body.get("values")
    if not isinstance(values, list) or len(values) < 2:
        err(422, "Give at least two values to compare")
    check_values(values)
    variants = sweep_variants(body["data"], body.get("setting") or {}, values)
    warmup = s.warmup_candles  # enough history for the variant with the longest indicators
    for _, d in variants:
        try:
            warmup = max(warmup, strategy_from_dict(copy.deepcopy(d)).warmup_candles)
        except Exception:  # an invalid variant is reported by the sweep itself
            pass
    df, fine, test_start, notes = load_period(ctx_of(request), ws, s, body, warmup=warmup)
    out = _run(request, ws, compute.sweep, variants, df, fine, test_start, s.risk.symbol, notes)
    record_test(ws, body, "sweep")
    return out


@router.post("/api/luck")
def luck(request: Request, body: dict = Body(...), ws: Workspace = Depends(workspace)):
    s = validate(ws, body.get("data"))
    trials = _trials(body.get("trials"))
    df, fine, test_start, notes = load_period(ctx_of(request), ws, s, body)
    out = _run(request, ws, compute.luck, body["data"], df, fine, test_start, notes, trials)
    record_test(ws, body, "luck")
    return out


MAX_TRIALS = 1_000_000


def _trials(v) -> int:
    """Settings or versions tried before this one: blank means 1."""
    if v is None:
        return 1
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not float(v).is_integer() or not 1 <= v <= MAX_TRIALS:
        err(422, f"Settings tried must be a whole number from 1 to {MAX_TRIALS:,}")
    return int(v)


@router.post("/api/portfolio")
def portfolio(request: Request, body: dict = Body(...), ws: Workspace = Depends(workspace)):
    ids = body.get("ids")
    if not isinstance(ids, list) or len(ids) < 1:
        err(422, "Choose at least one strategy")
    if len(ids) > 12:
        err(422, "Choose 12 strategies at most")
    if len(set(map(str, ids))) != len(ids):
        err(422, "Each strategy can only be chosen once")
    balance = body.get("balance")
    if isinstance(balance, bool) or not isinstance(balance, (int, float)) or balance <= 0:
        err(422, "Fill in the starting balance")
    parts, notes, currencies = [], [], set()
    for sid in ids:
        if not isinstance(sid, str):
            err(422, "Unknown strategy")
        s = load(ws, sid)
        currencies.add(s.risk.currency)
        df, fine, test_start, n = load_period(ctx_of(request), ws, s, body)
        notes += [f"{s.name}: {x}" for x in n[:1]]
        parts.append((sid, s.name, read_raw(strategy_path(ws, sid)), df, fine, test_start))
    if len(currencies) > 1:
        err(422, "These strategies use different currencies. Choose ones in the same currency.")
    sym = CURRENCY_SYMBOLS.get(currencies.pop(), "")
    return _run(request, ws, compute.portfolio, parts, float(balance), sym, notes, _portfolio_options(body, ids))


def _portfolio_options(body: dict, ids: list[str]) -> dict:
    """Weighting and shared-account settings for /api/portfolio, checked with plain messages."""
    weighting = body.get("weighting") or "equal"
    if weighting not in analysis.WEIGHTING_MODES:
        err(422, "Choose a weighting: equal, inverse volatility or custom")
    weights = None
    if weighting == "custom":
        weights = body.get("weights")
        if not isinstance(weights, dict):
            err(422, "Fill in a share (%) for every chosen strategy")
        for sid in ids:
            v = weights.get(sid)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                err(422, "Fill in a share (%) of 0 or more for every chosen strategy")
        if sum(weights.get(sid, 0) for sid in ids) <= 0:
            err(422, "The shares must add up to more than 0")
        weights = {sid: float(weights[sid]) for sid in ids}
    shared = bool(body.get("shared"))
    max_open_trades = body.get("max_open_trades")
    if max_open_trades not in (None, ""):
        if isinstance(max_open_trades, bool) or not isinstance(max_open_trades, (int, float)) \
                or max_open_trades != int(max_open_trades) or max_open_trades < 1:
            err(422, "Most trades open at once must be a whole number of 1 or more")
        max_open_trades = int(max_open_trades)
    else:
        max_open_trades = None
    max_open_risk_pct = body.get("max_open_risk_pct")
    if max_open_risk_pct not in (None, ""):
        if isinstance(max_open_risk_pct, bool) or not isinstance(max_open_risk_pct, (int, float)) or max_open_risk_pct <= 0:
            err(422, "Most at risk at once must be a % above 0")
        max_open_risk_pct = float(max_open_risk_pct)
    else:
        max_open_risk_pct = None
    loss = {}
    for key, label in (("max_daily_loss_pct", "Daily loss limit"), ("max_weekly_loss_pct", "Weekly loss limit"),
                       ("max_drawdown_pct", "Drawdown limit"), ("max_margin_pct", "Margin limit")):
        v = body.get(key)
        if v in (None, ""):
            loss[key] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 100:
            err(422, f"{label} must be a % above 0 and up to 100")
        loss[key] = float(v)
    if not shared and (max_open_trades is not None or max_open_risk_pct is not None
                       or any(v is not None for v in loss.values())):
        err(422, "The caps only apply when the strategies share one account")
    return {"weighting": weighting, "weights": weights, "shared": shared,
            "max_open_trades": max_open_trades, "max_open_risk_pct": max_open_risk_pct, **loss}


# --------------------------------------------------------------------------- optimiser


def combos_cap(ctx) -> int:
    return analysis.MAX_COMBOS.get(getattr(ctx, "edition", "cloud"), analysis.MAX_COMBOS["cloud"])


def optimiser_variants(data: dict, settings: list[dict], combos: list[tuple]) -> list:
    """(values, strategy dict or error text) for each combination; settings are applied in order."""
    out = []
    for values in combos:
        d = copy.deepcopy(data)
        problem = None
        for setting, v in zip(settings, values):
            problem = apply_setting(d, setting["setting"], v)
            if problem:
                break
        out.append((values, problem if problem else d))
    return out


@router.post("/api/optimise")
def optimise(request: Request, body: dict = Body(...), ws: Workspace = Depends(workspace)):
    """Start an optimiser run in the background. Returns ``{"job": id}``; poll ``GET /api/jobs/{id}``."""
    ctx = ctx_of(request)
    s = validate(ws, body.get("data"))
    settings = body.get("settings")
    if not isinstance(settings, list) or not settings:
        err(422, "Choose at least one setting to optimise")
    if len(settings) > analysis.MAX_OPT_SETTINGS:
        err(422, f"Optimise at most {analysis.MAX_OPT_SETTINGS} settings at once")
    axes = []
    for item in settings:
        if not isinstance(item, dict):
            err(422, "Choose a setting to test")
        check_setting(item.get("setting") or {})
        check_values(item.get("values"))
        axes.append(list(item["values"]))
    score = body.get("score") or "return"
    if score not in analysis.SCORES:
        err(422, "Choose how to rank the results")
    min_trades = body.get("min_trades", analysis.MIN_TRADES)
    if isinstance(min_trades, bool) or not isinstance(min_trades, int) or min_trades < 0:
        err(422, "The minimum number of trades must be a whole number")
    holdout = body.get("holdout_pct", 0) or 0
    if isinstance(holdout, bool) or not isinstance(holdout, (int, float)) or not 0 <= holdout <= analysis.MAX_HOLDOUT_PCT:
        err(422, f"Keep back between 0 and {analysis.MAX_HOLDOUT_PCT}% of the period")
    folds = body.get("walk_forward", 0) or 0
    if isinstance(folds, bool) or not isinstance(folds, int) or folds not in (0, *analysis.WALK_FORWARD_FOLDS):
        err(422, f"The walk-forward check uses {analysis.WALK_FORWARD_FOLDS[0]} to {analysis.WALK_FORWARD_FOLDS[-1]} stretches")
    try:
        combos = analysis.grid(axes, combos_cap(ctx))
    except ValueError as exc:
        err(422, str(exc))
    variants = optimiser_variants(body["data"], settings, combos)
    warmup = s.warmup_candles  # enough history for the variant with the longest indicators, per setting
    for item in settings:
        for v, d in sweep_variants(body["data"], item["setting"], item["values"]):
            try:
                warmup = max(warmup, strategy_from_dict(copy.deepcopy(d)).warmup_candles) if isinstance(d, dict) else warmup
            except Exception:  # an invalid variant is reported by the optimiser itself
                pass
    df, fine, test_start, notes = load_period(ctx, ws, s, body, warmup=warmup)
    workers = max(1, os.cpu_count() or 1) if ctx.edition == "selfhost" else 1
    extra = {"notes": notes, "currency": s.risk.symbol, "combinations": len(combos)}
    try:
        job = ctx.jobs.start(ws.owner, analysis.optimise, variants, df, fine, test_start, score, int(min_trades), workers, extra,
                             float(holdout), int(folds), kind="optimise")
    except Busy as exc:
        err(429, str(exc))
    record_test(ws, body, "optimise")
    return {"job": job, "combinations": len(combos)}


@router.get("/api/jobs")
def job_list(request: Request, ws: Workspace = Depends(workspace)):
    """This workspace's background runs, newest first, without their results."""
    return {"jobs": clean(ctx_of(request).jobs.list(ws.owner))}


@router.get("/api/jobs/{job_id}")
def job_status(request: Request, job_id: str, ws: Workspace = Depends(workspace)):
    st = ctx_of(request).jobs.status(ws.owner, job_id)
    if st is None:
        err(404, "That run has finished and been cleared, or never existed")
    return clean(st)


@router.post("/api/jobs/{job_id}/cancel")
def job_cancel(request: Request, job_id: str, ws: Workspace = Depends(workspace)):
    if not ctx_of(request).jobs.cancel(ws.owner, job_id):
        err(404, "That run has finished and been cleared, or never existed")
    return {"ok": True}


# --------------------------------------------------------------------------- strategies side by side


def _compare_period(body: dict) -> dict:
    """The same fixed dates for every strategy: "the last N years" becomes a start and end date
    once, so backtests run one after another can't drift apart."""
    years = body.get("years")
    if years not in (None, ""):
        if isinstance(years, bool) or not isinstance(years, (int, float)) or not 0 < years <= 20:
            err(422, "The test period must be between 1 and 20 years")
        now = pd.Timestamp.now(tz="UTC")
        return {"start": (now - pd.Timedelta(days=round(years * 365.25))).date().isoformat(),
                "end": now.date().isoformat()}
    out = {}
    for k in ("start", "end"):
        if body.get(k):
            try:
                out[k] = pd.Timestamp(str(body[k])).date().isoformat()
            except ValueError:
                err(422, "Dates must look like 2025-01-31")
    if "start" in out and "end" in out and out["start"] > out["end"]:
        err(422, "The start date must be before the end date")
    return out


@router.post("/api/compare/setup")
def compare_setup(body: dict = Body(...), ws: Workspace = Depends(workspace)):
    """Each chosen strategy's rules, with the settings ticked in ``match`` (market, account,
    costs) copied from the ``reference`` strategy, what changed in each, and one fixed period.
    The page then backtests each one as returned."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not 2 <= len(ids) <= 4 or not all(isinstance(x, str) for x in ids):
        err(422, "Choose 2 to 4 strategies")
    if len(set(ids)) != len(ids):
        err(422, "Each strategy can only be chosen once")
    ref = body.get("reference") or ids[0]
    if ref not in ids:
        err(422, "Take the shared settings from one of the chosen strategies")
    match = body.get("match") or {}
    if not isinstance(match, dict):
        err(422, "match must be an object")
    items = []
    for sid in ids:
        data = read_raw(strategy_path(ws, sid))
        if not isinstance(data, dict):
            err(422, "That strategy file can't be read")
        items.append((sid, str(data.get("name") or sid), data))
    period = _compare_period(body)
    try:
        out = sidebyside.same_footing(items, ids.index(ref), [k for k, v in match.items() if v is True])
    except ValueError as exc:
        err(422, str(exc))
    out["period"] = period
    if not period.get("start"):
        out["same"].append("No start date was given, so each one starts where its own price data starts.")
    return clean(out)
