"""Session A2: reproducible backtests (run record + data fingerprint), the per-backtest data
report, the check-data command, and the Parquet price cache."""

import json

import numpy as np
import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from tradealerts import analysis, cli, dataquality
from tradealerts import data as data_mod
from tradealerts.sample import synthetic_bars
from tradealerts.web import create_app

from .test_web import CSV_STRATEGY, H


def bars(n=200):
    return synthetic_bars(days=max(2, n // 24 + 1), freq="1h", seed=4).iloc[:n].copy()


# ------------------------------------------------------------------ fingerprint


def test_fingerprint_follows_the_candles_only():
    df = bars()
    fp = dataquality.fingerprint(df)
    assert fp == dataquality.fingerprint(df.copy()) and len(fp) == 16
    assert fp == dataquality.fingerprint(df.assign(volume=123.0))  # volume isn't part of it
    moved = df.copy()
    moved.iloc[50, moved.columns.get_loc("close")] += 0.01
    assert dataquality.fingerprint(moved) != fp
    assert dataquality.fingerprint(df.iloc[1:]) != fp
    with_spread = df.assign(spread=np.nan)
    assert dataquality.fingerprint(with_spread) == dataquality.fingerprint(with_spread.copy())
    assert dataquality.fingerprint(with_spread) != fp


# ------------------------------------------------------------------ data report


def test_report_on_clean_data_finds_nothing_but_records_the_basics():
    df = bars().assign(volume=1.0)
    rep = dataquality.report(df, "1h", "TEST")
    assert rep["issues"] == [] and rep["counts"] == {}
    assert rep["candles"] == len(df) and rep["timezone"] == "UTC" and rep["spread_coverage_pct"] is None
    assert rep["fingerprint"] == dataquality.fingerprint(df)


def test_report_lists_each_kind_of_problem():
    df = bars().assign(volume=1.0)
    extra = df.iloc[[10]].copy()
    extra.index = extra.index + pd.Timedelta(minutes=30)  # a candle half a candle-size later
    odd = pd.concat([df, extra]).sort_index()
    odd.iloc[150:, :4] = odd.iloc[150:, :4] * 1.3  # one big jump at an open
    odd["spread"] = np.where(np.arange(len(odd)) % 2, 0.5, np.nan)
    kinds = dataquality.report(odd, "1h", "TEST")["counts"]
    assert {"frequency", "jump", "spread"} <= set(kinds)
    naive = df.copy()
    naive.index = naive.index.tz_localize(None)
    assert "timezone" in dataquality.report(naive, "1h")["counts"]


def test_report_never_calls_past_data_stale():
    old = bars()
    old.index = old.index - pd.Timedelta(days=900)
    assert "stale" not in dataquality.report(old.assign(volume=1.0), "1h")["counts"]


# ------------------------------------------------------------------ the run record


@pytest.fixture
def client(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    synthetic_bars(days=120, freq="1h", seed=4).to_csv(tmp_path / "data" / "bars.csv")
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\nnotifiers:\n  - type: console\n")
    return TestClient(create_app(tmp_path / "config.yaml"))


def test_backtest_records_everything_needed_to_repeat_it(client):
    data = {**CSV_STRATEGY, "costs": {"spread": 0.5, "use_data_spread": False, "commission": 2,
                                       "commission_pct": 0.01, "borrow_pct": 1.5}}
    out = client.post("/api/backtest", json={"data": data, "split_pct": 70}, headers=H).json()
    meta = out["meta"]
    assert meta["data_fingerprint"] and meta["data_candles"] > 0
    assert (meta["commission"], meta["commission_pct"], meta["borrow_pct"]) == (2, 0.01, 1.5)
    assert meta["risk"]["account_size"] == 1000 and meta["risk"]["compound"] is True
    assert meta["split_pct"] == 70 and meta["random_seed"] == analysis.SEED and meta["direction"] == "long"
    assert isinstance(meta["data_checks"], dict)
    q = out["data_quality"]
    assert q["candles"] > 0 and q["fingerprint"] and "issues" in q
    again = client.post("/api/backtest", json={"data": data, "split_pct": 70}, headers=H).json()
    assert again["meta"]["data_fingerprint"] == meta["data_fingerprint"]
    assert again["metrics"] == out["metrics"] and again["monte_carlo"] == out["monte_carlo"]
    assert {k: v for k, v in again["meta"].items() if k != "tested_at"} == \
        {k: v for k, v in meta.items() if k != "tested_at"}


# ------------------------------------------------------------------ check-data command


def test_check_data_command(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\nnotifiers:\n  - type: console\n")
    strat = tmp_path / "s.yaml"
    strat.write_text(yaml.safe_dump({**CSV_STRATEGY, "market": {**CSV_STRATEGY["market"], "symbol": "TEST"}}))
    clean = tmp_path / "clean.csv"
    bars(300).assign(volume=1.0).to_csv(clean)
    args = ["-c", str(tmp_path / "config.yaml"), "check-data", str(strat), "--data", str(clean)]
    assert cli.main(args) == 0
    printed = capsys.readouterr().out
    assert "Fingerprint" in printed and "Nothing unusual found." in printed
    gappy = bars(300).assign(volume=1.0)
    gappy = gappy.drop(gappy.index[100:140])
    gappy.to_csv(tmp_path / "gappy.csv")
    assert cli.main(args[:-1] + [str(tmp_path / "gappy.csv")]) == 1
    assert "gap" in capsys.readouterr().out


# ------------------------------------------------------------------ Parquet cache


def _feed(p, monkeypatch, df):
    monkeypatch.setattr(p, "_fetch_range", lambda *a: df)


def _hours(n, start="2025-01-06"):
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    c = 100 + np.arange(n, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0}, index=idx)


@pytest.mark.skipif(not data_mod.PARQUET, reason="pyarrow not installed")
def test_cache_is_parquet_with_revisions(tmp_path, monkeypatch):
    p = data_mod.DukascopyProvider(tmp_path)
    df = _hours(48)
    _feed(p, monkeypatch, df)
    now = df.index[-1] + pd.Timedelta(hours=1)
    first = p._side("X", "INTERVAL_HOUR_1", "BID", df.index[0], now)
    path = p._cache_path("X", "INTERVAL_HOUR_1", "BID")
    assert path.suffix == ".parquet" and len(first) == 48
    meta = json.loads(path.with_suffix(".json").read_text())
    assert meta["revision"] == 1 and meta["rows"] == 48 and meta["fingerprint"] == dataquality.fingerprint(first)
    more = _hours(60)
    _feed(p, monkeypatch, more.iloc[40:])
    p._memo.clear()
    second = p._side("X", "INTERVAL_HOUR_1", "BID", df.index[0], more.index[-1] + pd.Timedelta(hours=1))
    assert len(second) == 60
    assert json.loads(path.with_suffix(".json").read_text())["revision"] == 2
    pd.testing.assert_frame_equal(pd.read_parquet(path), second, check_freq=False)


@pytest.mark.skipif(not data_mod.PARQUET, reason="pyarrow not installed")
def test_old_csv_cache_is_moved_to_parquet(tmp_path, monkeypatch):
    p = data_mod.DukascopyProvider(tmp_path)
    df = _hours(48)
    legacy = tmp_path / "dukascopy_X_INTERVAL_HOUR_1_BID.csv.gz"
    df.to_csv(legacy, compression="gzip")
    legacy.with_suffix(".json").write_text(json.dumps({"from": df.index[0].isoformat()}))
    _feed(p, monkeypatch, df.iloc[-3:])  # nothing new
    got = p._side("X", "INTERVAL_HOUR_1", "BID", df.index[0], df.index[-1] + pd.Timedelta(hours=1))
    assert len(got) == 48
    assert not legacy.exists() and not legacy.with_suffix(".json").exists()
    assert (tmp_path / "dukascopy_X_INTERVAL_HOUR_1_BID.parquet").exists()


def test_cache_falls_back_to_csv_without_pyarrow(tmp_path, monkeypatch):
    monkeypatch.setattr(data_mod, "PARQUET", False)
    p = data_mod.DukascopyProvider(tmp_path)
    df = _hours(24)
    _feed(p, monkeypatch, df)
    p._side("X", "INTERVAL_HOUR_1", "BID", df.index[0], df.index[-1] + pd.Timedelta(hours=1))
    path = p._cache_path("X", "INTERVAL_HOUR_1", "BID")
    assert path.name.endswith(".csv.gz") and path.exists()
    assert json.loads(path.with_suffix(".json").read_text())["revision"] == 1
