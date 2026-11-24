"""Session B7: formal engineering contracts.

Conformance checks for ``tradealerts.contracts``: every real provider/notifier/job runner/
store satisfies its Protocol; a real trade dict and backtest metrics dict contain exactly
the documented keys (catches silent drift); the public JSON shape of a few key API
responses matches the documented TypedDict keys; and the simulator/backtest/analysis/
stats/measures stack stays free of web or database imports.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from tradealerts import contracts as c
from tradealerts.backtest import run_backtest
from tradealerts.data import CSVProvider, DukascopyProvider, MarketProvider, OandaProvider, SampleProvider, YahooProvider
from tradealerts.jobs import InlineJobs, PoolJobs
from tradealerts.notify import ConsoleNotifier, DiscordNotifier, EmailNotifier, NtfyNotifier, SlackNotifier, TelegramNotifier, WebhookNotifier
from tradealerts.sample import synthetic_bars
from tradealerts.state import StateStore
from tradealerts.web import create_app

from .conftest import make_strategy

H = {"X-Requested-With": "tradealerts"}

CSV_STRATEGY = {
    "name": "B7 contracts test",
    "market": {"symbol": "data/bars.csv", "label": "Test", "provider": "csv", "timeframe": "1h"},
    "direction": "long",
    "entry": {"left": {"indicator": "rsi", "period": 14}, "op": "crosses_above", "right": 35},
    "exit": {"stop": {"atr": 2, "atr_period": 14}, "target": {"r_multiple": 2}, "max_bars": 24},
    "risk": {"account_size": 1000, "risk_per_trade_pct": 1, "currency": "GBP"},
}


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    synthetic_bars(days=120, freq="1h", seed=4).to_csv(tmp_path / "data" / "bars.csv")
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    return tmp_path


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws / "config.yaml"))


# --------------------------------------------------------------------------- import hygiene


def test_simulator_stack_has_no_web_or_database_imports():
    """The simulator/backtest/analysis/stats/measures stack must stay importable without
    ever pulling in FastAPI, Starlette, sqlite3, httpx or requests, so it can be used (or
    tested) with nothing but pandas/numpy -- a clean interpreter checks this, since any of
    those modules could already be in ``sys.modules`` from an earlier import in this process."""
    forbidden = ("fastapi", "starlette", "sqlite3", "httpx", "requests")
    script = textwrap.dedent(f"""
        import sys
        from tradealerts import simulator, backtest, analysis, stats, measures
        hit = [m for m in {forbidden!r} if m in sys.modules]
        print(",".join(hit))
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    hit = result.stdout.strip()
    assert hit == "", f"simulator/backtest/analysis/stats/measures pulled in: {hit}"


# --------------------------------------------------------------------------- protocols


def test_price_providers_conform_to_protocol():
    for cls in (CSVProvider, YahooProvider, OandaProvider, DukascopyProvider, SampleProvider, MarketProvider):
        assert isinstance(cls.__new__(cls), c.PriceProvider), cls.__name__


def test_notifiers_conform_to_protocol():
    for cls in (ConsoleNotifier, NtfyNotifier, DiscordNotifier, TelegramNotifier, SlackNotifier, WebhookNotifier, EmailNotifier):
        assert isinstance(cls.__new__(cls), c.Notifier), cls.__name__
    assert isinstance(ConsoleNotifier(), c.Notifier)


def test_job_runners_conform_to_protocol():
    inline = InlineJobs()
    assert isinstance(inline, c.JobRunner)
    inline.close()
    pool = PoolJobs(workers=1)
    try:
        assert isinstance(pool, c.JobRunner)
    finally:
        pool.close()


def test_state_store_conforms_to_protocol(tmp_path):
    store = StateStore(tmp_path / "state.db")
    try:
        assert isinstance(store, c.StateStorage)
    finally:
        store.close()


# --------------------------------------------------------------------------- engine-level shapes


def test_trade_record_matches_documented_keys(bars_1h):
    """Every closed trade from the simulator has exactly the keys ``TradeRecord`` documents --
    no more, no fewer. A silent rename or a dropped/added field fails here."""
    strategy = make_strategy()
    result = run_backtest(strategy, bars_1h)
    assert len(result.trades) >= 1, "fixture should produce at least one trade"
    for trade in result.trades.to_dict("records"):
        assert c.matches_exactly(trade, c.TradeRecord)


def test_backtest_metrics_matches_documented_keys(bars_1h):
    """``run_backtest``'s metrics dict only ever has the required keys plus (when there was
    at least one trade) the documented optional ones -- nothing undocumented leaks through."""
    strategy = make_strategy()
    result = run_backtest(strategy, bars_1h)
    documented = c.BacktestMetrics.__required_keys__ | c.BacktestMetrics.__optional_keys__
    assert c.required_keys_present(result.metrics, c.BacktestMetrics)
    assert result.metrics.keys() <= documented, result.metrics.keys() - documented
    if result.metrics["trades"] > 0:
        # with trades closed, the trade-sample keys must all be there too
        trade_sample_keys = {"win_rate_pct", "avg_win", "avg_loss", "expectancy", "avg_r",
                             "avg_bars_held", "largest_win", "largest_loss", "exit_reasons"}
        assert trade_sample_keys <= result.metrics.keys()


def test_backtest_metrics_without_trades_still_matches(bars_1h):
    """A strategy whose rules never fire still produces every required key -- the
    trade-sample keys are the only ones allowed to be missing."""
    strategy = make_strategy(entry={"left": "close", "op": ">", "right": 10_000_000})
    result = run_backtest(strategy, bars_1h)
    assert result.metrics["trades"] == 0
    assert c.required_keys_present(result.metrics, c.BacktestMetrics)


def test_run_record_matches_documented_keys(tmp_path):
    """``StateStore.start_run`` (and therefore ``run``/``runs``/``active_run``) returns
    exactly the columns ``RunRecord`` documents."""
    store = StateStore(tmp_path / "state.db")
    try:
        run = store.start_run("strat-1", name="Test", market="EURUSD", timeframe="1h",
                              currency="GBP", start_balance=1000.0)
        assert c.matches_exactly(run, c.RunRecord)
        assert c.matches_exactly(store.run(run["id"]), c.RunRecord)
        assert c.matches_exactly(store.active_run("strat-1"), c.RunRecord)
        for row in store.runs():
            assert c.matches_exactly(row, c.RunRecord)
    finally:
        store.close()


def test_delivery_record_matches_documented_keys(tmp_path):
    store = StateStore(tmp_path / "state.db")
    try:
        store.add_delivery(strategy_id="strat-1", run_id=None, channel="console", kind="signal",
                           title="t", body="b", priority="default", status="retrying", attempts=1,
                           error="boom", next_retry_at="2026-01-01T00:00:00.000Z")
        rows = store.due_deliveries("2026-06-01T00:00:00.000Z")
        assert len(rows) == 1
        assert c.matches_exactly(rows[0], c.DeliveryRecord)
    finally:
        store.close()


def test_job_record_matches_documented_keys(tmp_path):
    store = StateStore(tmp_path / "state.db")
    try:
        store.save_job("job-1", "owner-1", "backtest", "completed", "2026-01-01T00:00:00.000Z",
                       started_at="2026-01-01T00:00:01.000Z", finished_at="2026-01-01T00:00:02.000Z")
        rows = store.jobs_for_owner("owner-1")
        assert len(rows) == 1
        assert c.matches_exactly(rows[0], c.JobRecord)
    finally:
        store.close()


# --------------------------------------------------------------------------- public API shapes


def test_health_endpoint_matches_documented_keys(client):
    body = client.get("/api/health").json()
    documented = c.HealthStatus.__required_keys__ | c.HealthStatus.__optional_keys__
    assert body.keys() == documented


def test_forward_test_start_matches_run_record(client, ws):
    sid = client.post("/api/strategies", json={"data": CSV_STRATEGY}, headers=H).json()["id"]
    body = client.post(f"/api/strategies/{sid}/forward-test/start", headers=H).json()
    documented = c.RunRecord.__required_keys__ | c.RunRecord.__optional_keys__
    assert body.keys() == documented


def test_backtest_api_response_has_documented_keys(client):
    body = client.post("/api/backtest", json={"data": CSV_STRATEGY}, headers=H).json()
    assert c.required_keys_present(body, c.BacktestAPIResponse)
    assert c.required_keys_present(body["metrics"], c.BacktestMetrics)


# --------------------------------------------------------------------------- sanity on the helpers


def test_conformance_helpers_catch_drift():
    good = {"a": 1, "b": 2}
    bad_missing = {"a": 1}
    bad_extra = {"a": 1, "b": 2, "c": 3}

    class Pair(dict):
        __required_keys__ = frozenset({"a", "b"})
        __optional_keys__ = frozenset()

    assert c.matches_exactly(good, Pair)
    assert not c.matches_exactly(bad_missing, Pair)
    assert not c.matches_exactly(bad_extra, Pair)
    assert c.required_keys_present(good, Pair)
    assert not c.required_keys_present(bad_missing, Pair)
    assert c.required_keys_present(bad_extra, Pair)  # a superset is fine for this check
