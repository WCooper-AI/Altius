"""Session B3: operational reliability.

1. Crash & restart recovery for the live forward-test runner.
2. Notification delivery tracking: retries with backoff, bounded attempts, ``alert_failed``
   only once they're exhausted.
3. System health & persistence: background jobs survive a restart, ``GET /api/health``,
   ``PRAGMA quick_check`` at startup, backup verification.
4. Keychain and migration resilience.
"""

from __future__ import annotations

import json
import time

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from tradealerts import cli, keychain, notify
from tradealerts.config import load_config
from tradealerts.jobs import InlineJobs
from tradealerts.notify import Message, Notifier, build_notifiers, record_deliveries, retry_pending, send_each
from tradealerts.runner import process_strategy, run_cycle
from tradealerts.sample import synthetic_bars
from tradealerts.state import StateStore
from tradealerts.strategy import load_strategy
from tradealerts.web import create_app
from tradealerts.web import app as app_module
from tradealerts.web.routes import backup as backup_routes
from tradealerts.workspace import Workspace

H = {"X-Requested-With": "tradealerts"}

# A tight stop (relative to the usual 2x ATR) makes some trades close on the same bar they
# open; a looser target and max_bars lets others stay open for several bars. Entries always
# pass through one bar of "pending" first. So this one fixture, run bar by bar, naturally
# covers a pending entry, an open position and a same-bar close.
STRATEGY = {
    "name": "Ops test",
    "market": {"symbol": "TEST", "label": "Test", "timeframe": "1h"},
    "direction": "long",
    "entry": {"left": {"indicator": "rsi", "period": 14}, "op": "crosses_above", "right": 35},
    "exit": {"stop": {"atr": 0.4}, "target": {"r_multiple": 1.5}, "max_bars": 24},
    "risk": {"account_size": 1000, "risk_per_trade_pct": 1},
}


class Recorder(Notifier):
    name = "recorder"

    def __init__(self):
        self.messages = []

    def send(self, msg):
        self.messages.append(msg)


class AlwaysFails(Notifier):
    def __init__(self, name="ntfy"):
        self.name = name

    def send(self, msg):
        raise RuntimeError("timed out")


def _make_cfg(path):
    (path / "strategies").mkdir()
    (path / "strategies" / "live.yaml").write_text(yaml.safe_dump(STRATEGY))
    (path / "config.yaml").write_text("poll_minutes: 60\n")
    return load_config(path / "config.yaml")


def _trades(store):
    return pd.DataFrame(store.trades()).drop(columns=["id", "strategy_id", "run_id"])


# =========================================================================== 1. crash & restart


def test_a_bars_writes_commit_or_discard_together(tmp_path):
    """The building block the recovery tests below rely on: a trade, its event and the
    cursor move, passed ``commit=False``, share one database transaction. Closing the
    connection without an explicit commit (as a crash would) leaves none of them behind --
    not just the cursor out of step with an already-saved trade."""
    store = StateStore(tmp_path / "state.db")
    store.save("s1", "2024-01-01T00:00:00", {"equity": 1000})
    store.add_trade("s1", {"direction": "long", "entry_price": 1, "size": 1}, commit=False)
    store.add_event("s1", "2024-01-01T01:00:00", "opened", "opened", True, commit=False)
    store.save("s1", "2024-01-01T01:00:00", {"equity": 999}, commit=False)
    store.db.close()  # no commit -- like a crash partway through the bar

    reopened = StateStore(tmp_path / "state.db")
    assert reopened.trades() == []  # the half-finished bar left nothing behind
    assert reopened.load("s1")["cursor"] == "2024-01-01T00:00:00"  # still at the last full commit
    reopened.close()

    # Finishing the sequence (reaching the commit) saves everything together.
    store = StateStore(tmp_path / "state.db")
    store.add_trade("s1", {"direction": "long", "entry_price": 1, "size": 1}, commit=False)
    store.add_event("s1", "2024-01-01T01:00:00", "opened", "opened", True, commit=False)
    store.save("s1", "2024-01-01T01:00:00", {"equity": 999}, commit=False)
    store.commit()
    store.close()
    reopened = StateStore(tmp_path / "state.db")
    assert len(reopened.trades()) == 1 and reopened.load("s1")["cursor"] == "2024-01-01T01:00:00"
    reopened.close()


def test_restart_every_bar_matches_an_uninterrupted_run(tmp_path_factory):
    """Persisting state and rebuilding the runner from what's on disk after every bar (as if
    the process restarted each time) must give exactly the same trades, equity and alerts as
    a run that's never interrupted."""
    bars = synthetic_bars(days=40, freq="1h", seed=5)
    step = pd.Timedelta("1h")

    cfg_ref = _make_cfg(tmp_path_factory.mktemp("ref"))
    strat_ref = load_strategy(cfg_ref.strategies_dir / "live.yaml")
    store_ref = StateStore(cfg_ref.state_db)
    ref_sent = []
    for i in range(1, len(bars)):
        rec = Recorder()
        process_strategy(cfg_ref, strat_ref, store_ref, [rec], now=bars.index[i] + step, loader=lambda c, s: bars)
        ref_sent += [(m.title, m.body) for m in rec.messages]
    ref_trades = _trades(store_ref)
    run_id = store_ref.active_run("live")["id"]
    ref_equity = store_ref.equity(run_id)
    store_ref.close()
    assert len(ref_trades) > 5
    assert (ref_trades["bars_held"] == 0).any(), "fixture should cover a trade closed the same bar it opened"
    assert (ref_trades["bars_held"] > 1).any(), "fixture should cover a position held open across bars"

    cfg = _make_cfg(tmp_path_factory.mktemp("restart"))
    strat = load_strategy(cfg.strategies_dir / "live.yaml")
    sent = []
    for i in range(1, len(bars)):
        store = StateStore(cfg.state_db)  # rebuilt from what's on disk, as after a restart
        rec = Recorder()
        process_strategy(cfg, strat, store, [rec], now=bars.index[i] + step, loader=lambda c, s: bars)
        sent += [(m.title, m.body) for m in rec.messages]
        store.close()
    store = StateStore(cfg.state_db)
    trades = _trades(store)
    run_id = store.active_run("live")["id"]
    equity = store.equity(run_id)
    store.close()

    pd.testing.assert_frame_equal(trades.reset_index(drop=True), ref_trades.reset_index(drop=True), check_dtype=False)
    assert equity == ref_equity
    assert sent == ref_sent


def test_crash_partway_through_catchup_recovers_without_duplicating_trades(tmp_path_factory):
    """If the process is killed partway through catching up several missed bars in one go,
    each bar's trade, event, equity point and advanced cursor land as one transaction: a
    crash can only lose a bar that hadn't finished, never replay one that had."""
    bars = synthetic_bars(days=40, freq="1h", seed=5)
    step = pd.Timedelta("1h")

    cfg_ref = _make_cfg(tmp_path_factory.mktemp("ref2"))
    strat_ref = load_strategy(cfg_ref.strategies_dir / "live.yaml")
    store_ref = StateStore(cfg_ref.state_db)
    for i in range(1, len(bars)):
        process_strategy(cfg_ref, strat_ref, store_ref, [Recorder()], now=bars.index[i] + step, loader=lambda c, s: bars)
    ref_trades = _trades(store_ref)
    store_ref.close()

    cfg = _make_cfg(tmp_path_factory.mktemp("crashy"))
    strat = load_strategy(cfg.strategies_dir / "live.yaml")
    store = StateStore(cfg.state_db)
    for i in range(1, 400):  # normal running for a while
        process_strategy(cfg, strat, store, [Recorder()], now=bars.index[i] + step, loader=lambda c, s: bars)
    store.close()

    # Fall behind, then catch up to the end in one call that gets killed partway through.
    store = StateStore(cfg.state_db)
    calls = {"n": 0}
    real_commit = store.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated crash")
        real_commit()

    store.commit = flaky_commit
    with pytest.raises(RuntimeError):
        process_strategy(cfg, strat, store, [Recorder()], now=bars.index[-1] + step, loader=lambda c, s: bars)
    store.db.close()  # nothing the interrupted bar wrote is left half-saved, as on a real crash

    # Resume: a fresh connection picks up right after the last bar that fully committed.
    store = StateStore(cfg.state_db)
    process_strategy(cfg, strat, store, [Recorder()], now=bars.index[-1] + step, loader=lambda c, s: bars)
    trades = _trades(store)
    store.close()

    pd.testing.assert_frame_equal(trades.reset_index(drop=True), ref_trades.reset_index(drop=True), check_dtype=False)


# =========================================================================== 2. notification delivery


def test_failed_delivery_retries_with_backoff_then_gives_up(tmp_path):
    store = StateStore(tmp_path / "state.db")
    n = AlwaysFails("ntfy")
    msg = Message("Rules met", "body", "default")
    now = pd.Timestamp("2026-01-01T00:00:00Z")

    outcomes = send_each([n], msg)
    assert outcomes == [("ntfy", "timed out")]
    record_deliveries(store, outcomes, strategy_id="s1", run_id=1, kind="signal",
                      title=msg.title, body=msg.body, priority=msg.priority, now=now)
    assert store.due_deliveries(now.isoformat()) == []  # the backoff hasn't elapsed yet

    exhausted = []
    t = now
    for _ in range(10):
        t = t + pd.Timedelta(hours=1)  # well past any backoff, like widely-spaced polls
        exhausted = retry_pending(store, [n], t)
        if exhausted:
            break
    assert exhausted and exhausted[0]["channel"] == "ntfy" and exhausted[0]["attempts"] == notify.MAX_DELIVERY_ATTEMPTS
    status = store.channel_delivery_status()
    assert status["ntfy"]["last_failed"] is not None and status["ntfy"]["last_sent"] is None
    store.close()


def test_delivery_recovers_once_the_channel_works_again(tmp_path):
    class FlakyOnce(Notifier):
        name = "ntfy"

        def __init__(self):
            self.calls = 0

        def send(self, msg):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("timed out")

    store = StateStore(tmp_path / "state.db")
    n = FlakyOnce()
    msg = Message("Rules met", "body", "default")
    now = pd.Timestamp("2026-01-01T00:00:00Z")
    record_deliveries(store, send_each([n], msg), kind="signal", title=msg.title, body=msg.body,
                      priority=msg.priority, now=now)
    assert store.channel_delivery_status()["ntfy"]["last_sent"] is None

    exhausted = retry_pending(store, [n], now + pd.Timedelta(minutes=1))
    assert exhausted == []
    assert store.channel_delivery_status()["ntfy"]["last_sent"] is not None
    assert store.due_deliveries((now + pd.Timedelta(hours=1)).isoformat()) == []  # nothing left pending
    store.close()


@pytest.mark.parametrize("channel", ["ntfy", "telegram", "discord", "slack", "email", "webhook"])
def test_delivery_tracking_covers_every_notifier_channel(tmp_path, channel):
    """Delivery tracking goes through the common notifier path (every channel just has a
    ``.name`` and a ``.send()``), so it works the same for all of them -- no real network."""
    store = StateStore(tmp_path / "state.db")
    n = AlwaysFails(channel)
    msg = Message("Test", "body", "default")
    now = pd.Timestamp("2026-01-01T00:00:00Z")
    record_deliveries(store, send_each([n], msg), kind="signal", title=msg.title, body=msg.body,
                      priority=msg.priority, now=now)
    due = store.due_deliveries((now + pd.Timedelta(minutes=1)).isoformat())
    assert due and due[0]["channel"] == channel and due[0]["status"] == "retrying"
    store.close()


def test_alert_failed_only_raised_after_retries_exhaust(tmp_path_factory):
    """A single bad poll must not raise ``alert_failed`` straight away: the channel is
    retried a few times first, and only an exhausted retry raises the problem."""
    bars = synthetic_bars(days=40, freq="1h", seed=5)
    step = pd.Timedelta("1h")
    cfg = _make_cfg(tmp_path_factory.mktemp("delivery"))
    strat = load_strategy(cfg.strategies_dir / "live.yaml")
    store = StateStore(cfg.state_db)
    notifiers = [AlwaysFails("ntfy")]

    i = 1
    while not store.recent_events(5):
        process_strategy(cfg, strat, store, notifiers, now=bars.index[i] + step, loader=lambda c, s: bars)
        i += 1
        if i > 200:
            pytest.fail("fixture didn't produce an early signal to test with")
    assert store.events_page(["alert_failed"]) == []  # the first failure alone isn't reported

    # Keep polling, hours apart: every retry is long overdue by the next poll, so it fails
    # again each time, until it's tried MAX_DELIVERY_ATTEMPTS times in total.
    for _ in range(notify.MAX_DELIVERY_ATTEMPTS + 2):
        i += 1
        run_cycle(cfg, store, notifiers, now=bars.index[i] + step)
    failed = store.events_page(["alert_failed"])
    assert failed and "ntfy: timed out" in failed[0]["message"]
    store.close()


def test_settings_shows_last_sent_and_last_failed_per_channel(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    client = TestClient(create_app(tmp_path / "config.yaml"))
    with Workspace(tmp_path / "config.yaml").store() as store:
        record_deliveries(store, [("ntfy", None)], kind="signal", title="t", body="b", priority="default")
        # a channel only counts as "last failed" once its retries are exhausted
        store.add_delivery(strategy_id=None, run_id=None, channel="telegram", kind="signal", title="t", body="b",
                           priority="default", status="failed", attempts=notify.MAX_DELIVERY_ATTEMPTS,
                           error="timed out", next_retry_at=None)
    s = client.get("/api/settings").json()
    assert s["notifiers"]["ntfy"]["last_sent"] is not None and s["notifiers"]["ntfy"].get("last_failed") is None
    assert s["notifiers"]["telegram"]["last_failed"] is not None


# =========================================================================== 3. system health & persistence


def test_jobs_persist_across_a_restart_and_unfinished_ones_become_failed(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    ws = Workspace(tmp_path / "config.yaml")
    with ws.store():  # the database already exists, as it normally would by the time a job runs
        pass
    jobs = InlineJobs(store_factory=lambda: ws.store(create=False))
    job_id = jobs.start(None, lambda progress: time.sleep(0.3) or "ok", kind="test")
    time.sleep(0.05)  # it's running, but nowhere near done
    with ws.store(create=False) as store:
        rows = store.jobs_for_owner("-")
    assert rows and rows[0]["id"] == job_id and rows[0]["status"] == "queued"

    # As a new process would after a restart: nothing in memory, but the database still
    # knows the job existed -- and a leftover "queued" or "running" one can only mean the
    # old process stopped before finishing it.
    with ws.store() as store:
        changed = store.fail_stale_jobs("The server restarted while this was running.")
    assert changed == 1
    fresh = InlineJobs(store_factory=lambda: ws.store(create=False))
    with ws.store(create=False) as store:
        listed = fresh.list("-", store)
    row = next(j for j in listed if j["id"] == job_id)
    assert row["status"] == "failed" and "restarted" in row["error"]
    time.sleep(0.4)  # let the real background thread actually finish before the test ends


def test_jobs_api_survives_a_restart(tmp_path):
    """GET /api/jobs still reports a job from before a restart (as failed), even though a
    freshly-started process has no memory of it."""
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    ws = Workspace(tmp_path / "config.yaml")
    with ws.store() as store:
        store.save_job("abc123", "-", "optimise", "running", "2026-01-01T00:00:00.000Z",
                      started_at="2026-01-01T00:00:01.000Z")
        store.fail_stale_jobs("The server restarted while this was running.")
    client = TestClient(create_app(tmp_path / "config.yaml"))
    jobs = client.get("/api/jobs").json()["jobs"]
    row = next(j for j in jobs if j["id"] == "abc123")
    assert row["status"] == "failed" and "restarted" in row["error"]


def test_health_endpoint_reports_db_runner_and_jobs(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    client = TestClient(create_app(tmp_path / "config.yaml"))
    body = client.get("/api/health").json()
    assert body["db_ok"] is True and body["db_problem"] is None
    assert body["runner_alive"] is False  # no live runner started in this test app
    assert isinstance(body["jobs"], dict)
    assert client.get("/api/v1/health").json() == body  # same API, versioned prefix


def test_quick_check_reports_a_problem_without_crashing(tmp_path):
    store = StateStore(tmp_path / "state.db")
    assert store.quick_check() is None  # a normal database passes

    class FakeCursor:
        def fetchall(self):
            return [("row 12 missing from index idx_runs_strategy",)]

    class FakeConnection:
        """``sqlite3.Connection`` can't be monkeypatched (it's an immutable C type), so this
        stands in for it just long enough to make ``PRAGMA quick_check`` report a problem."""

        def __init__(self, real):
            self._real = real

        def execute(self, sql, *a):
            return FakeCursor() if "quick_check" in sql else self._real.execute(sql, *a)

    store.db = FakeConnection(store.db)
    problem = store.quick_check()
    assert problem and "missing" in problem


def test_startup_check_records_a_db_problem_event(tmp_path, monkeypatch):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    monkeypatch.setattr(StateStore, "quick_check", lambda self: "row 9 missing from an index")
    ws = Workspace(tmp_path / "config.yaml")
    app_module._startup_checks(ws)
    with ws.store() as store:
        events = store.events_page(["db_problem"])
    assert events and "missing from an index" in events[0]["message"]


def test_create_app_runs_startup_checks_and_shows_them_in_activity(tmp_path, monkeypatch):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    monkeypatch.setattr(StateStore, "quick_check", lambda self: "simulated corruption")
    client = TestClient(create_app(tmp_path / "config.yaml"))
    items = client.get("/api/activity?category=problems").json()["items"]
    assert any(i["kind"] == "db_problem" for i in items)


def test_cli_check_db_reports_ok_then_a_problem(tmp_path, monkeypatch, capsys):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    cfg = load_config(tmp_path / "config.yaml")
    StateStore(cfg.state_db).close()

    assert cli.main(["-c", str(tmp_path / "config.yaml"), "check-db"]) == 0
    assert "integrity check" in capsys.readouterr().out

    monkeypatch.setattr(StateStore, "quick_check", lambda self: "simulated corruption")
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "check-db"]) == 1
    assert "simulated corruption" in capsys.readouterr().out


def test_cli_verify_backup_round_trip(tmp_path, capsys):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    ws = Workspace(tmp_path / "config.yaml")
    good = tmp_path / "good.zip"
    good.write_bytes(backup_routes.make_backup(ws))
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "verify-backup", str(good)]) == 0
    assert "reads back fine" in capsys.readouterr().out

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "verify-backup", str(bad)]) == 1
    assert "backup" in capsys.readouterr().out.lower()


def test_backup_download_is_verified_and_recorded(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    client = TestClient(create_app(tmp_path / "config.yaml"))
    assert client.get("/api/health").json()["last_backup_at"] is None
    assert client.get("/api/backup").status_code == 200
    assert client.get("/api/health").json()["last_backup_at"] is not None


# =========================================================================== 4. keychain & migration


class FakeKeyringStore:
    """Stands in for the OS password store, same shape as the ``keyring`` package."""

    def __init__(self):
        self.items: dict[tuple[str, str], str] = {}

    def get_keyring(self):
        return type("K", (), {})()

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[(service, name)] = value

    def delete_password(self, service, name):
        del self.items[(service, name)]


@pytest.fixture
def keyring_store(monkeypatch):
    fake = FakeKeyringStore()
    monkeypatch.setattr(keychain, "_backend", fake)
    monkeypatch.setattr(keychain, "_checked", True)
    return fake


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.setattr(keychain, "_backend", None)
    monkeypatch.setattr(keychain, "_checked", True)


def test_keychain_unavailable_resolves_placeholders_to_blank_without_crashing(no_keyring):
    raw = {"notifiers": [{"type": "ntfy", "topic": "keychain:notifiers.ntfy.topic.deadbeef", "enabled": True}]}
    revealed = keychain.reveal(raw)
    assert revealed["notifiers"][0]["topic"] == ""  # a clear blank, not a crash or the raw reference
    # Building the notifier from that still fails, but with the normal, plain complaint --
    # not a surprise about the password store.
    with pytest.raises(ValueError, match="topic"):
        build_notifiers(revealed["notifiers"])


def test_restoring_on_a_machine_without_that_keychain_entry_leaves_a_clear_blank(tmp_path, no_keyring):
    """A placeholder that means nothing on this machine (a new install, or one where that
    entry was never carried over) must come through as a plain blank, ready to re-enter --
    never the raw ``keychain:...`` reference, and never a crash."""
    ws = Workspace(tmp_path / "config.yaml", keychain=True)
    raw = {"notifiers": [{"type": "telegram", "token": "keychain:notifiers.telegram.token.deadbeef",
                          "chat_id": "42", "enabled": True}]}
    out = backup_routes._settings_from_backup(ws, raw)
    assert out["notifiers"][0]["token"] == ""
    ws.write_config(out)
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.notifiers[0]["token"] == ""  # no crash; just needs to be re-entered in Settings


def test_rotating_a_token_replaces_the_keychain_entry(keyring_store):
    """Saving a new value for a secret that's already in the password store overwrites that
    same entry: the old value isn't kept anywhere, under any key."""
    raw = {"notifiers": [{"type": "telegram", "token": "111:old", "chat_id": "1", "enabled": True}]}
    first = keychain.stash(raw)
    ref = first["notifiers"][0]["token"]
    assert keychain.is_placeholder(ref)
    assert keyring_store.items[("Altius", ref[len(keychain.PREFIX):])] == "111:old"

    rotated = json.loads(json.dumps(first))
    rotated["notifiers"][0]["token"] = "222:new"
    second = keychain.stash(rotated, first)

    assert second["notifiers"][0]["token"] == ref  # the same entry, not a new one
    assert keyring_store.items[("Altius", ref[len(keychain.PREFIX):])] == "222:new"
    assert "111:old" not in keyring_store.items.values()  # the old value is gone
    assert keychain.reveal(second)["notifiers"][0]["token"] == "222:new"
