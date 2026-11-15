"""Session A4: ranges on the luck check, block bootstrap, deflated Sharpe, probability of
backtest overfitting, and results by market conditions."""
import copy
import math
import time
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradealerts import analysis, stats
from tradealerts.backtest import run_backtest
from tradealerts.sample import synthetic_bars
from tradealerts.strategy import strategy_from_dict
from tradealerts.web import create_app

from .test_web import CSV_STRATEGY, H


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    synthetic_bars(days=200, freq="1h", seed=4).to_csv(tmp_path / "data" / "bars.csv")
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\nnotifiers:\n  - type: console\n")
    return tmp_path


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws / "config.yaml"))


def variant(rsi_level, atr):
    d = copy.deepcopy(CSV_STRATEGY)
    d["entry"]["all"][0]["right"] = rsi_level
    d["exit"]["stop"]["atr"] = atr
    return d


def small_grid():
    return [((lv, a), variant(lv, a)) for lv in (30, 35, 40) for a in (1.5, 2.0)]


def wait_done(client, job, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/jobs/{job}", headers=H).json()
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------------ ranges and bootstrap


def test_wilson_range_matches_known_values():
    lo, hi = stats.wilson(0, 10)
    assert lo == 0 and hi == pytest.approx(0.2775, abs=1e-4)
    lo, hi = stats.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)
    lo, hi = stats.wilson(180, 200)
    assert lo < 0.9 < hi and hi - lo < 0.1
    assert stats.wilson(0, 0) == (0.0, 1.0)


def test_block_bootstrap_keeps_streaks_and_widens_the_range():
    x = np.tile(np.repeat([1.0, -1.0], 25), 4)  # long winning and losing streaks
    rng = np.random.default_rng(0)
    iid, b1 = stats.block_means(x, 4000, rng, block=1)
    blk, b = stats.block_means(x, 4000, rng)
    assert b1 == 1 and b == stats.block_size(len(x)) == 6
    assert iid.mean() == pytest.approx(x.mean(), abs=0.01) and blk.mean() == pytest.approx(x.mean(), abs=0.02)
    assert blk.std() > 1.5 * iid.std()


def test_block_bootstrap_samples_are_runs_of_consecutive_trades():
    x = np.arange(30, dtype=float)  # each value is its own position
    rng = np.random.default_rng(3)
    n, b = len(x), 3
    starts = np.random.default_rng(3).integers(0, n, (5, 10))
    means, _ = stats.block_means(x, 5, rng, block=b)
    expect = (((starts[:, :, None] + np.arange(b)) % n).reshape(5, -1)[:, :n]).mean(axis=1)
    assert np.allclose(means, expect)


# ------------------------------------------------------------------ deflated Sharpe


def test_expected_max_sharpe_matches_the_paper():
    assert stats.expected_max(1, 1.0) == 0.0
    assert stats.expected_max(100, 1.0) == pytest.approx(2.531, abs=0.01)
    assert stats.expected_max(1000, 0.25) > stats.expected_max(100, 0.25) > stats.expected_max(10, 0.25) > 0


def test_one_try_gives_the_probabilistic_sharpe_ratio():
    r = np.random.default_rng(1).normal(0.004, 0.02, 120)
    out = stats.deflated_sharpe(r, 1)
    sr = r.mean() / r.std(ddof=1)
    sd = r.std()
    skew = ((r - r.mean()) ** 3).mean() / sd ** 3
    kurt = ((r - r.mean()) ** 4).mean() / sd ** 4
    z = sr * math.sqrt(len(r) - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    assert out["bar"] == 0 and out["trials"] == 1 and out["trades"] == 120
    assert out["sharpe"] == pytest.approx(sr) and out["probability"] == pytest.approx(NormalDist().cdf(z))


def test_more_tries_lower_the_deflated_probability():
    r = np.random.default_rng(2).normal(0.003, 0.02, 200)
    probs = [stats.deflated_sharpe(r, n)["probability"] for n in (1, 10, 100, 1000)]
    assert probs == sorted(probs, reverse=True) and probs[0] > probs[-1]
    # a wider spread between the tries raises the bar further
    assert stats.deflated_sharpe(r, 50, 0.04)["bar"] > stats.deflated_sharpe(r, 50, 0.01)["bar"]


def test_deflated_sharpe_needs_enough_varied_trades():
    assert stats.deflated_sharpe(np.full(50, 0.01), 1) is None
    assert stats.deflated_sharpe(np.array([0.01, -0.02] * 4), 1) is None  # 8 trades
    assert stats.deflated_sharpe(np.array([0.01, -0.02] * 5), 1) is not None


def test_trade_returns_use_the_balance_before_each_trade():
    t = pd.DataFrame({"pnl": [10.0, -20.0, 5.0], "equity_after": [1010.0, 990.0, 995.0]})
    assert np.allclose(stats.trade_returns(t), [10 / 1000, -20 / 1010, 5 / 990])
    assert np.allclose(stats.closed_returns(1000.0, t["pnl"].to_numpy()), [10 / 1000, -20 / 1010, 5 / 990])


# ------------------------------------------------------------------ probability of backtest overfitting


def test_a_combination_that_wins_everywhere_is_never_overfit():
    profit = np.tile(np.arange(5, dtype=float), (8, 1))  # combination 4 makes the most in every stretch
    out = stats.overfitting(profit)
    assert out["splits"] == math.comb(8, 4) == 70
    assert out["probability"] == 0 and out["below"] == 0 and out["lost"] == 0


def test_a_winner_that_only_won_in_its_own_stretch_is_always_below_the_middle():
    profit = np.diag(np.arange(1, 9, dtype=float))  # each combination made money in one stretch only
    out = stats.overfitting(profit)
    assert out["probability"] == 1 and out["below"] == 70 and out["lost"] == 0
    assert out["median_logit"] < 0


def test_overfitting_needs_combinations_and_trades():
    assert stats.overfitting(np.ones((8, 1))) is None
    assert stats.overfitting(np.zeros((8, 4))) is None
    assert stats.overfitting(np.ones((7, 4))) is None


def test_optimiser_reports_deflated_sharpe_and_overfitting():
    df = synthetic_bars(days=200, freq="1h", seed=4)
    out = analysis.optimise(None, small_grid(), df, None, None, score="return", min_trades=5)
    pbo, dsr = out["overfitting"], out["deflated"]
    assert pbo["splits"] == 70 and pbo["combinations"] == out["tested"] == 6
    assert 0 <= pbo["probability"] <= 1 and pbo["below"] <= pbo["splits"]
    assert dsr["trials"] == 6 and 0 <= dsr["probability"] <= 1
    best = run_backtest(strategy_from_dict(copy.deepcopy(variant(*out["best"]))), df, None)
    assert dsr["trades"] == len(best.trades)
    assert all("_trades" not in r for r in out["rows"])


def test_overfitting_profit_per_stretch_adds_up_to_each_combination(monkeypatch):
    a, b = 0, 800
    rows = [{"error": None, "_trades": (1000.0, np.array([50, 150, 790, 805]), np.array([1.0, 2.0, 3.0, 4.0]))},
            {"error": None, "_trades": (1000.0, np.array([350, 450]), np.array([-1.0, 5.0]))},
            {"error": "bad", "values": [9]}]
    seen = []
    real = stats.overfitting
    monkeypatch.setattr(stats, "overfitting", lambda profit, blocks=stats.PBO_BLOCKS: seen.append(profit.copy()) or real(profit, blocks))
    analysis.selection_checks(rows, rows[0], a, b, open_end=True)
    analysis.selection_checks(rows, rows[0], a, b, open_end=False)
    open_profit = seen[0]
    assert open_profit[:, 0].tolist() == [1, 2, 0, 0, 0, 0, 0, 7]  # 805 is after the end: last stretch
    assert open_profit[:, 1].tolist() == [0, 0, 0, -1, 5, 0, 0, 0]
    assert seen[1][:, 0].sum() == 6  # nothing after the end when the end is closed


def test_optimiser_overfitting_uses_only_the_ranked_part_with_a_holdout():
    df = synthetic_bars(days=200, freq="1h", seed=4)
    out = analysis.optimise(None, small_grid(), df, None, None, score="return", min_trades=5, holdout_pct=30)
    assert pd.Timestamp(out["overfitting"]["to"]) < pd.Timestamp(out["holdout"]["from"])
    best = next(r for r in out["rows"] if r["values"] == out["best"])
    assert out["deflated"] is None or out["deflated"]["trades"] == best["train"]["trades"]


# ------------------------------------------------------------------ market conditions


def test_market_states_only_use_earlier_days():
    idx = pd.date_range("2024-01-01", periods=120, freq="D", tz="UTC")
    close = np.r_[np.linspace(100, 160, 80), np.linspace(160, 100, 40)]
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=idx)
    st = stats.market_states(df)
    avg = pd.Series(close, index=idx).rolling(stats.TREND_DAYS).mean()
    for day in (idx[60], idx[90], idx[110]):
        prev = day - pd.Timedelta(days=1)
        want = "rising" if close[idx.get_loc(prev)] > avg[prev] else "falling"
        assert st.loc[day, "direction"] == want
    assert pd.isna(st["direction"].iloc[stats.TREND_DAYS - 1])  # no full average yet
    # changing today's price can't change today's state
    df2 = df.copy()
    df2.iloc[90, df2.columns.get_loc("close")] = 1.0
    assert stats.market_states(df2).loc[idx[90], "direction"] == st.loc[idx[90], "direction"]


def test_regimes_split_trades_and_days():
    df = synthetic_bars(days=200, freq="1h", seed=4)
    res = run_backtest(strategy_from_dict(copy.deepcopy(CSV_STRATEGY)), df, None)
    out = stats.regimes(df, res.trades)
    rows = {r["key"]: r for r in out["rows"]}
    assert set(rows) == {"rising", "falling", "calm", "busy"}
    for a, b in (("rising", "falling"), ("calm", "busy")):
        assert rows[a]["days_pct"] + rows[b]["days_pct"] == pytest.approx(100)
        assert rows[a]["trades"] + rows[b]["trades"] + out["unknown"] >= len(res.trades)
    known = len(res.trades) - out["unknown"]
    assert rows["rising"]["trades"] + rows["falling"]["trades"] == known
    total = sum(rows[k]["pnl"] for k in ("rising", "falling"))
    assert total == pytest.approx(float(res.trades["pnl"].sum()) - _unknown_pnl(df, res.trades))


def _unknown_pnl(df, trades):
    st = stats.market_states(df)
    entry = pd.to_datetime(trades["entry_time"], utc=True).dt.floor("D")
    tagged = st.reindex(pd.DatetimeIndex(entry), method="ffill")["direction"].to_numpy()
    return float(trades["pnl"].to_numpy()[pd.isna(tagged)].sum())


def test_regimes_need_enough_history():
    df = synthetic_bars(days=40, freq="1h", seed=1)
    assert stats.regimes(df, pd.DataFrame(columns=["entry_time", "pnl", "r_multiple"])) is None


# ------------------------------------------------------------------ luck check


def test_luck_check_reports_ranges_blocks_deflated_and_regimes():
    df = synthetic_bars(days=200, freq="1h", seed=4)
    s = strategy_from_dict(copy.deepcopy(CSV_STRATEGY))
    out = analysis.luck_check(s, df, None, None, time_budget=8, max_random=25)
    rnd = out["random"]
    assert rnd["runs"] <= 25 and rnd["share_low"] <= rnd["share"] <= rnd["share_high"]
    assert rnd["share"] == rnd["beaten"] / rnd["runs"]
    assert 0 < rnd["p_value"] <= 1 and rnd["p5"] <= rnd["median"] <= rnd["p95"]
    check = next(c for c in out["checks"] if c["key"] == "random")
    assert "95% range" in check["title"]
    if out["trades"] >= 5:
        assert out["resample"]["block"] == stats.block_size(out["trades"])
        assert "consecutive" in next(c for c in out["checks"] if c["key"] == "resample")["detail"]
    assert out["regimes"] is not None
    if out["trades"] >= stats.MIN_TRADES_DSR:
        assert out["deflated"]["trials"] == 1 and out["deflated"]["bar"] == 0


def test_luck_check_trials_raise_the_bar():
    df = synthetic_bars(days=200, freq="1h", seed=4)
    s = strategy_from_dict(copy.deepcopy(CSV_STRATEGY))
    one = analysis.luck_check(s, df, None, None, time_budget=2, max_random=5)
    many = analysis.luck_check(s, df, None, None, time_budget=2, max_random=5, trials=200)
    assert many["deflated"]["trials"] == 200 and many["deflated"]["bar"] > 0
    assert many["deflated"]["probability"] < one["deflated"]["probability"]


def test_api_luck_takes_and_validates_settings_tried(client):
    for bad in (0, -1, 1.5, True, "3", 2_000_000):
        r = client.post("/api/luck", json={"data": CSV_STRATEGY, "trials": bad}, headers=H)
        assert r.status_code == 422 and "Settings tried" in r.json()["detail"], bad
    r = client.post("/api/luck", json={"data": CSV_STRATEGY, "trials": 12}, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["deflated"] is None or out["deflated"]["trials"] == 12
    assert "regimes" in out and "share_low" in out["random"]


def test_api_optimise_result_carries_selection_checks(client):
    body = {"data": CSV_STRATEGY, "score": "return", "min_trades": 5,
            "settings": [{"setting": {"kind": "path", "path": ["entry", "all", 0, "right"]}, "values": [30, 35, 40]}]}
    r = client.post("/api/optimise", json=body, headers=H)
    assert r.status_code == 200, r.text
    st = wait_done(client, r.json()["job"])
    assert st["status"] == "done", st
    res = st["result"]
    assert res["overfitting"]["splits"] == 70 and res["deflated"]["trials"] == 3
