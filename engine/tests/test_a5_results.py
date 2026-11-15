"""Session A5: yearly growth, Calmar, expected shortfall, drawdown length, best/worst periods,
and trades by weekday and session. Balance curves here are hand-made so every number can be
worked out on paper."""

import json

import numpy as np
import pandas as pd
import pytest

from tradealerts import invariants, measures, report
from tradealerts.backtest import monthly_returns, run_backtest

from .conftest import make_bars, make_strategy


def curve(values, start="2025-01-06", freq="D"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq, tz="UTC"), dtype=float)


def trades(rows):
    """rows: (entry_time, pnl, r)."""
    return pd.DataFrame([{"entry_time": t, "pnl": p, "r_multiple": r} for t, p, r in rows])


# --- yearly growth and Calmar -------------------------------------------------------------

def test_cagr_two_years_doubling_is_about_41_pct():
    g = measures.cagr(1000, 2000, "2023-01-01", pd.Timestamp("2023-01-01") + pd.Timedelta(days=2 * 365.25))
    assert g == pytest.approx((2 ** 0.5 - 1) * 100)


def test_cagr_blank_under_a_year():
    assert measures.cagr(1000, 1100, "2025-01-01", "2025-12-01") is None


def test_cagr_wiped_out_account():
    assert measures.cagr(1000, 0, "2020-01-01", "2023-01-01") == -100.0


def test_calmar_divides_by_drawdown_size():
    assert measures.calmar(12.0, -6.0) == pytest.approx(2.0)
    assert measures.calmar(None, -6.0) is None
    assert measures.calmar(12.0, 0.0) is None


# --- period changes, best/worst, expected shortfall -----------------------------------------

def test_period_changes_daily_from_opening_balance():
    ch = measures.period_changes(curve([1000, 1100, 990]), "D")
    assert ch.round(6).tolist() == [0.0, 10.0, -10.0]


def test_best_worst_day_and_week_with_dates():
    # Mon 6 Jan 2025 for 14 days: +10% on Tue 7th, -20% on Mon 13th (second week)
    vals = [1000, 1100] + [1100] * 5 + [880] + [880] * 6
    p = measures.best_worst(curve(vals))
    assert p["day"]["best"] == pytest.approx(10.0) and p["day"]["best_at"] == "2025-01-07"
    assert p["day"]["worst"] == pytest.approx(-20.0) and p["day"]["worst_at"] == "2025-01-13"
    assert p["week"]["best_at"] == "2025-01-06" and p["week"]["worst_at"] == "2025-01-13"
    assert p["week"]["count"] == 2
    assert p["month"] is None  # only one month in the test


def test_expected_shortfall_is_mean_of_worst_5_pct_of_days():
    # 40 days: 38 flat, one -4% and one -2% → worst ceil(40 * 5%) = 2 days average -3%
    vals = [1000.0] * 40
    vals[10] = 960.0
    for i in range(11, 40):
        vals[i] = 960.0
    vals[20] = 940.8
    for i in range(21, 40):
        vals[i] = 940.8
    assert measures.expected_shortfall(curve(vals)) == pytest.approx(-3.0)


def test_expected_shortfall_needs_20_days():
    assert measures.expected_shortfall(curve([1000] * 19)) is None


# --- drawdown length -------------------------------------------------------------------------

def test_worst_drawdown_dates_and_lengths():
    # high on day 2, bottom on day 5, back on day 9
    vals = [1000, 1050, 1100, 1000, 950, 900, 950, 1000, 1050, 1100, 1120]
    w = measures.worst_drawdown(curve(vals), 1000)
    assert w["depth_pct"] == pytest.approx((900 / 1100 - 1) * 100)
    assert (w["days_down"], w["days_to_recover"], w["days_total"]) == (3.0, 4.0, 7.0)
    assert w["recovered"].startswith("2025-01-15")


def test_worst_drawdown_not_recovered():
    w = measures.worst_drawdown(curve([1000, 900, 950]), 1000)
    assert w["recovered"] is None and w["days_to_recover"] is None
    assert w["days_down"] == 1.0 and w["days_total"] == 2.0


def test_no_drawdown():
    assert measures.worst_drawdown(curve([1000, 1010, 1020]), 1000) is None


# --- weekday and session ---------------------------------------------------------------------

def test_by_weekday_groups_in_week_order():
    t = trades([("2025-01-07 10:00+00:00", 10, 1.0), ("2025-01-06 10:00+00:00", -5, -0.5),
                ("2025-01-13 10:00+00:00", 20, 2.0)])
    rows = measures.by_weekday(t)
    assert [r["name"] for r in rows] == ["Monday", "Tuesday"]
    mon = rows[0]
    assert (mon["trades"], mon["won_pct"], mon["pnl"], mon["avg_r"]) == (2, 50.0, 15.0, 0.75)


@pytest.mark.parametrize("when, name", [
    ("2025-01-07 02:00+00:00", "Asia"),               # 11:00 Tokyo
    ("2025-01-07 09:00+00:00", "London"),             # 09:00 London, 04:00 New York
    ("2025-01-07 14:00+00:00", "London + New York"),  # 14:00 London, 09:00 New York
    ("2025-01-07 18:00+00:00", "New York"),           # 13:00 New York, London shut
    ("2025-01-07 23:00+00:00", "Other hours"),
    ("2025-01-11 14:00+00:00", "Other hours"),        # Saturday
    ("2025-07-08 07:30+00:00", "London"),             # 08:30 British Summer Time
    ("2025-01-07 07:30+00:00", "Asia"),               # 16:30 Tokyo; London opens 08:00 in winter
])
def test_session_names_use_local_clocks(when, name):
    assert measures.session_names(pd.to_datetime(pd.Series([when]), utc=True))[0] == name


def test_by_session_blank_for_daily_candles():
    t = trades([("2025-01-07 14:00+00:00", 10, 1.0)])
    assert measures.by_session(t, "1d") == []
    assert measures.by_session(t, "1h")[0]["name"] == "London + New York"


# --- through the backtest --------------------------------------------------------------------

def test_backtest_metrics_carry_new_keys_and_stay_json_safe():
    rows = [(100, 100, 100, 101), (101, 106, 101, 105), (105, 105, 105, 105)] * 6
    res = run_backtest(make_strategy(entry={"left": "close", "op": ">", "right": 100.5}), make_bars(rows))
    assert invariants.check(res) == []
    m = res.metrics
    for key in ("cagr_pct", "calmar", "expected_shortfall_pct", "worst_drawdown", "periods", "by_weekday", "by_session"):
        assert key in m
    assert m["cagr_pct"] is None  # 18 hours of candles
    assert sum(r["trades"] for r in m["by_weekday"]) == m["trades"]
    assert sum(r["trades"] for r in m["by_session"]) == m["trades"]
    json.dumps(m, allow_nan=False)
    labels = [k for k, _ in report.summary_rows(res)]
    assert "Yearly growth rate (CAGR)" in labels and "Worst drawdown, how long" in labels


def test_monthly_returns_unchanged_by_shared_helper():
    eq = curve(np.linspace(1000, 1200, 70))
    out = monthly_returns(eq)
    jan_end = float(eq[eq.index.month == 1].iloc[-1])
    assert out[0]["months"][0] == pytest.approx((jan_end / 1000 - 1) * 100)
    assert out[0]["ytd"] == pytest.approx((1200 / 1000 - 1) * 100)
