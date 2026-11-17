"""Formal contracts for the boundaries between Altius's main pieces.

This module does not change any behaviour and does not introduce a new architecture: it
names the shapes that already exist, so a change that quietly breaks one (a renamed key, a
provider that no longer has ``fetch_bars``, a notifier without ``send``) fails a test instead
of surfacing later as a bug report. See ``tests/test_b7_contracts.py`` for the conformance
checks and ``docs/architecture.md`` for the one-line summary of each boundary.

Four ``typing.Protocol`` classes describe the things with more than one implementation
(or that could reasonably get another one):

* :class:`PriceProvider` -- what every class in :mod:`data` derived from ``Provider`` offers
  (``CSVProvider``, ``YahooProvider``, ``OandaProvider``, ``DukascopyProvider``,
  ``SampleProvider``, ``MarketProvider``).
* :class:`Notifier` -- what every alert channel in :mod:`notify` offers (console, ntfy,
  Discord, Telegram, Slack, webhook, email).
* :class:`JobRunner` -- what :class:`~tradealerts.jobs.InlineJobs` (self-hosted) and
  :class:`~tradealerts.jobs.PoolJobs` (hosted) both offer to run and track a background job.
* :class:`StateStorage` -- the part of :class:`~tradealerts.state.StateStore` that
  :mod:`notify` and :mod:`jobs` rely on across the module boundary (simulator state, forward
  test runs, trades, deliveries and jobs). ``StateStore`` itself also stores price alerts,
  journal entries, news notes and strategy versions, which are feature-specific persistence
  outside this contract, not a separate implementation of it.

The rest are ``typing.TypedDict`` shapes for the plain ``dict``/``list`` values that cross
these boundaries, with the keys as they are actually produced today:

* :class:`TradeRecord` -- one closed trade, as the simulator reports it (and as
  ``StateStore.add_trade``/``trades`` persist and read it back, plus ``id``/``strategy_id``/
  ``run_id``).
* :class:`BacktestMetrics` -- the ``metrics`` dict ``backtest.run_backtest`` returns.
* :class:`RunRecord` -- one row of the ``runs`` table (a forward test).
* :class:`DeliveryRecord` -- one row of the ``deliveries`` table (one attempt to send one
  message on one channel).
* :class:`JobRecord` -- one row of the ``jobs`` table (a background job surviving a restart).
* :class:`JobStatus` -- the public shape ``GET /api/jobs`` and ``GET /api/jobs/{id}`` report.
* :class:`HealthStatus` -- the body of ``GET /api/health``.
* :class:`BacktestAPIResponse` -- the body of ``POST /api/backtest``.

Nothing here is new behaviour: every type is a description of code that already exists in
``data.py``, ``strategy.py``, ``simulator.py``, ``backtest.py``, ``notify.py``, ``jobs.py`` and
``state.py``. No new dependency is needed; everything used is in the standard library's
``typing`` module (Python >= 3.11) plus ``pandas``, which the engine already depends on.
"""

from typing import Any, Callable, NotRequired, Protocol, TypedDict, runtime_checkable

import pandas as pd

from .notify import Message

# --------------------------------------------------------------------------- market data


@runtime_checkable
class PriceProvider(Protocol):
    """What every price source in :mod:`data` offers (see ``data.Provider``).

    ``fetch_bars`` returns the candles resampled to ``timeframe``, plus the finer candles
    they were built from when there were any (used to order a stop and target that land in
    the same coarse candle); ``fetch`` is just the first of that pair.
    """

    def fetch_bars(
        self, symbol: str, timeframe: str, lookback_days: int, alignment: str = ...
    ) -> tuple[pd.DataFrame, pd.DataFrame | None]: ...

    def fetch(self, symbol: str, timeframe: str, lookback_days: int, alignment: str = ...) -> pd.DataFrame: ...


# --------------------------------------------------------------------------- notifications


@runtime_checkable
class Notifier(Protocol):
    """One alert channel, as every class in :mod:`notify` implements it."""

    name: str

    def send(self, msg: Message) -> None: ...


# --------------------------------------------------------------------------- background jobs


class JobStatus(TypedDict):
    """The public shape of one job, as ``_Job.public()`` and ``_JobTable.list`` build it for
    ``GET /api/jobs`` and ``GET /api/jobs/{id}``. ``result`` is only present once the job has
    completed and was asked for; ``error``/``error_type`` only once it has failed."""

    id: str
    kind: str
    status: str  # queued | running | completed | failed | cancelled
    done: int
    total: int
    note: str | None
    created: str | None
    started: str | None
    finished: str | None
    waited: float
    seconds: float
    cpu_seconds: float | None
    result: NotRequired[Any]
    error: NotRequired[str | None]
    error_type: NotRequired[str | None]


@runtime_checkable
class JobRunner(Protocol):
    """What :class:`~tradealerts.jobs.InlineJobs` and :class:`~tradealerts.jobs.PoolJobs` both
    offer: run a job now and wait for it, or start it in the background and poll/cancel it."""

    def run(self, owner: str | None, fn: Callable, *args: Any) -> Any: ...

    def start(self, owner: str | None, fn: Callable, *args: Any, kind: str = "job") -> str: ...

    def status(self, owner: str | None, job_id: str) -> JobStatus | None: ...

    def list(self, owner: str | None, store: Any = None) -> list[JobStatus]: ...

    def cancel(self, owner: str | None, job_id: str) -> bool: ...

    def running(self, owner: str | None) -> int: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- simulation events


class TradeRecord(TypedDict):
    """One closed trade: the dict the simulator's ``closed`` event carries, and the shape
    ``StateStore.add_trade``/``trades`` persist and read back (which also carry ``id``,
    ``strategy_id`` and ``run_id`` -- see :meth:`StateStorage.trades`)."""

    direction: str  # long | short
    signal_time: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    size: float
    stop: float
    target: float | None
    exit_reason: str  # stop | target | exit_rule | max_bars | close_out | end_of_data
    pnl: float
    r_multiple: float
    bars_held: int
    equity_after: float
    funding: float
    spread_cost: float
    borrow: float
    commission: float
    slippage_cost: float
    entry_reason: dict[str, Any]
    exit_values: dict[str, Any]
    mae_r: float
    mfe_r: float
    financing: float  # funding + borrow
    fx_entry: float
    fx: float
    margin: float


# --------------------------------------------------------------------------- backtest results


class BacktestMetrics(TypedDict):
    """The ``metrics`` dict ``backtest.run_backtest`` returns (and ``oos_metrics`` when a
    train/test split is used, which never gets ``data_jumps``: see ``backtest.run_backtest``).

    The keys below the trade-sample ones are only added when there is at least one trade
    (``backtest.compute_metrics``); ``data_jumps`` and ``split_pct`` only apply to the whole
    backtest, not a per-metrics-dict concept, so they are added once by ``run_backtest``
    itself and are optional here too.
    """

    market: str
    timeframe: str
    direction: str
    start: str
    end: str
    bars: int
    repaired_bars: int
    start_equity: float
    end_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    buy_and_hold_pct: float
    time_in_market_pct: float
    trades: int
    total_funding: float
    total_spread_cost: float
    total_commission: float
    total_borrow: float
    total_financing: float
    total_slippage: float
    sample_warning: str | None
    max_margin_used_pct: float | None
    closed_out: int
    sharpe: float | None
    sortino: float | None
    sharpe_ci: list[float] | None
    longest_underwater_days: float
    monthly_returns: list[dict]
    cagr_pct: float | None
    calmar: float | None
    expected_shortfall_pct: float | None
    worst_drawdown: dict | None
    periods: dict
    by_weekday: list[dict]
    by_session: list[dict]
    by_year: list[dict]
    win_rate_range: list[float] | None
    overlapping_trades: int
    warmup_candles: int
    blocked_by_limits: int
    used_data_spread: bool
    fx: dict | None
    used_finer_candles: bool
    # only once there is at least one trade
    win_rate_pct: NotRequired[float]
    avg_win: NotRequired[float]
    avg_loss: NotRequired[float]
    profit_factor: NotRequired[float | None]
    expectancy: NotRequired[float]
    avg_r: NotRequired[float]
    avg_bars_held: NotRequired[float]
    longest_losing_streak: NotRequired[int]
    longest_winning_streak: NotRequired[int]
    largest_win: NotRequired[float]
    largest_loss: NotRequired[float]
    avg_hold_hours: NotRequired[float]
    exit_reasons: NotRequired[dict[str, int]]
    r_histogram: NotRequired[list[dict]]
    hold_histogram: NotRequired[list[dict]]
    # added once, by run_backtest, not compute_metrics
    data_jumps: NotRequired[int]
    split_pct: NotRequired[float]


class BacktestAPIResponse(TypedDict):
    """The body of ``POST /api/backtest`` (``web.routes.testing.backtest``, built by
    ``compute.backtest`` and stamped with ``meta``). A superset of :class:`BacktestMetrics`
    under ``metrics``, plus the chart data and a few things the GUI needs that the engine's
    own result does not carry (benchmark, Monte Carlo, data quality)."""

    chart: dict
    metrics: BacktestMetrics
    warnings: list[str]
    equity: list
    benchmark: list
    drawdown: list
    trades: list[TradeRecord]
    trade_count: int
    currency: str
    monte_carlo: dict | None
    regimes: dict | None
    data_quality: dict
    data_fingerprint: str
    data_candles: int
    meta: dict
    why_no_trades: NotRequired[list[str]]
    oos_metrics: NotRequired[BacktestMetrics]
    oos_equity: NotRequired[list]
    oos_trade_count: NotRequired[int]
    split_date: NotRequired[str]


# --------------------------------------------------------------------------- persistence


class RunRecord(TypedDict):
    """One row of the ``runs`` table: a forward test, running or stopped. The exact dict
    ``StateStore.run``/``runs``/``active_run``/``start_run``/``stop_run`` return, and what
    ``POST /api/strategies/{id}/forward-test/start`` sends back as-is."""

    id: int
    strategy_id: str
    name: str | None
    market: str | None
    timeframe: str | None
    currency: str | None
    start_balance: float
    started_at: str
    stopped_at: str | None
    source_hash: str | None
    strategy_yaml: str | None


class DeliveryRecord(TypedDict):
    """One row of the ``deliveries`` table: one attempt to send one message on one channel
    (``status`` is ``sent``, ``failed`` or ``retrying``). The exact dict
    ``StateStore.due_deliveries`` returns."""

    id: int
    strategy_id: str | None
    run_id: int | None
    channel: str
    kind: str
    title: str | None
    body: str | None
    priority: str | None
    status: str
    attempts: int
    last_error: str | None
    created_at: str
    updated_at: str
    next_retry_at: str | None


class JobRecord(TypedDict):
    """One row of the ``jobs`` table, kept so ``GET /api/jobs`` can still report a job from
    before a restart. The exact dict ``StateStore.jobs_for_owner`` returns."""

    id: str
    owner: str
    kind: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    error_type: str | None


@runtime_checkable
class StateStorage(Protocol):
    """The part of :class:`~tradealerts.state.StateStore` that :mod:`notify` and :mod:`jobs`
    rely on across the module boundary: simulator state, forward test runs, trades,
    deliveries and background jobs. ``StateStore`` also stores price alerts, journal entries,
    news notes and strategy versions; those are feature-specific persistence, not part of
    this cross-cutting contract."""

    def load(self, strategy_id: str) -> dict | None: ...

    def save(self, strategy_id: str, cursor: str, state: dict, source_hash: str = "", commit: bool = True) -> None: ...

    def reset(self, strategy_id: str) -> None: ...

    def active_run(self, strategy_id: str) -> RunRecord | None: ...

    def start_run(self, strategy_id: str, **kwargs: Any) -> RunRecord: ...

    def stop_run(self, strategy_id: str, stopped_at: str | None = None) -> RunRecord | None: ...

    def run(self, run_id: int) -> RunRecord | None: ...

    def runs(self) -> list[RunRecord]: ...

    def add_trade(self, strategy_id: str, trade: TradeRecord, run_id: int | None = None, commit: bool = True) -> None: ...

    def trades(self, strategy_id: str | None = None, run_id: int | None = None) -> list[TradeRecord]: ...

    def add_event(
        self, strategy_id: str, bar_time: str, kind: str, message: str, notified: bool,
        run_id: int | None = None, commit: bool = True,
    ) -> None: ...

    def recent_events(self, limit: int = 20, run_id: int | None = None) -> list[dict]: ...

    def add_delivery(self, **kwargs: Any) -> int: ...

    def due_deliveries(self, now_iso: str, limit: int = 200) -> list[DeliveryRecord]: ...

    def update_delivery(self, delivery_id: int, **kwargs: Any) -> None: ...

    def channel_delivery_status(self) -> dict[str, dict]: ...

    def save_job(self, job_id: str, owner: str, kind: str, status: str, created_at: str, **kwargs: Any) -> None: ...

    def jobs_for_owner(self, owner: str) -> list[JobRecord]: ...

    def job_counts(self) -> dict[str, int]: ...

    def fail_stale_jobs(self, reason: str) -> int: ...

    def get_value(self, key: str, default: str | None = None) -> str | None: ...

    def set_value(self, key: str, value: str) -> None: ...

    def quick_check(self) -> str | None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- the API surface


class HealthStatus(TypedDict):
    """The body of ``GET /api/health`` (``web.routes.status.health``): a monitoring check,
    not the richer ``GET /api/status`` for people."""

    db_ok: bool
    db_problem: str | None
    runner_alive: bool
    last_check: str | None
    jobs: dict[str, int]
    last_backup_at: str | None


# --------------------------------------------------------------------------- conformance helpers


def required_keys_present(data: dict, typed_dict: type) -> bool:
    """True when every required key of ``typed_dict`` is in ``data`` (a subset check: ``data``
    may legitimately carry more, e.g. an API response wrapping an engine result)."""
    return typed_dict.__required_keys__ <= data.keys()


def matches_exactly(data: dict, typed_dict: type) -> bool:
    """True when ``data`` has exactly the keys ``typed_dict`` documents (required and
    optional) -- no fewer, no more. Use this on a boundary with one fixed shape (a database
    row, the simulator's own trade dict); a drift there, in either direction, is a bug."""
    documented = typed_dict.__required_keys__ | typed_dict.__optional_keys__
    return data.keys() == documented
