"""Session B2: market-data integrity. New dataquality checks (possible splits, possible
dividends, futures contract rolls), centralised provider facts and trading-hours assumptions in
the run record, and edge cases: daylight-saving transitions, exchange holidays, and malformed
candles (bad CSV rows included)."""

import numpy as np
import pandas as pd
import pytest

from tradealerts import data as data_mod
from tradealerts import dataquality, markethours
from tradealerts.data import DataError, normalise, resample_ohlcv

from .test_web import CSV_STRATEGY, H, client, ws  # noqa: F401  (fixtures)


def daily_bars(n=120, seed=1, start="2024-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    close = 100 + rng.normal(0, 0.5, n).cumsum()
    return pd.DataFrame({"open": close - 0.1, "high": close + 0.3, "low": close - 0.3, "close": close,
                         "volume": rng.integers(1000, 5000, n)}, index=idx)


def hourly_bars(n=200, seed=1, start="2024-03-04 00:00"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    close = 100 + rng.normal(0, 0.3, n).cumsum()
    return pd.DataFrame({"open": close - 0.05, "high": close + 0.2, "low": close - 0.2, "close": close,
                         "volume": rng.integers(50, 500, n)}, index=idx)


T = lambda s, **kw: pd.Timestamp(s, **kw)  # noqa: E731


# --------------------------------------------------------------------------- possible splits


def test_split_like_overnight_move_is_reported_on_a_stock():
    df = daily_bars()
    df.iloc[60:, :4] = df.iloc[60:, :4] * 0.5  # every candle from here on is on a new price scale
    hits = dataquality.find_splits(df, pd.Timedelta(days=1))
    assert len(hits) == 1 and hits[0][0] == df.index[60]
    rep = dataquality.report(df, "1d", "AAPL")
    assert rep["counts"]["split"] == 1
    issue = next(i for i in rep["issues"] if i["kind"] == "split")
    assert "half" in issue["text"] and "2:1" in issue["text"] and "not adjusted" in issue["text"]


def test_ordinary_moves_never_look_like_a_split():
    # an everyday 30% jump is nowhere near a split ratio (2x, 3x, 0.5x, ...)
    df = daily_bars()
    df.iloc[60:, :4] = df.iloc[60:, :4] * 1.3
    assert dataquality.find_splits(df, pd.Timedelta(days=1)) == []
    assert "split" not in dataquality.report(df, "1d", "AAPL")["counts"]


def test_split_check_ignores_ordinary_intraday_moves():
    """Without a real session gap, an hour-to-hour move hitting a split ratio by coincidence
    should not be reported: splits happen overnight, not mid-session."""
    df = hourly_bars()
    df.iloc[100:, :4] = df.iloc[100:, :4] * 0.5  # no gap in the index around row 100
    assert dataquality.find_splits(df, pd.Timedelta(hours=1)) == []


def test_split_is_still_found_across_a_real_intraday_gap():
    idx = pd.date_range("2024-03-04 09:00", periods=100, freq="1h", tz="UTC").delete(range(40, 48))
    rng = np.random.default_rng(9)
    close = 100 + rng.normal(0, 0.2, len(idx)).cumsum()
    df = pd.DataFrame({"open": close - 0.05, "high": close + 0.1, "low": close - 0.1, "close": close,
                       "volume": rng.integers(100, 900, len(idx))}, index=idx)
    df.iloc[40:, :4] = df.iloc[40:, :4] * 0.5  # the split lands exactly on the overnight break
    hits = dataquality.find_splits(df, pd.Timedelta(hours=1))
    assert len(hits) == 1 and hits[0][0] == idx[40]


def test_reverse_split_rises_are_also_matched():
    df = daily_bars(seed=2)
    df.iloc[60:, :4] = df.iloc[60:, :4] * 2.0
    hits = dataquality.find_splits(df, pd.Timedelta(days=1))
    assert len(hits) == 1
    issue = next(i for i in dataquality.report(df, "1d", "AAPL")["issues"] if i["kind"] == "split")
    assert "doubled" in issue["text"] and "1:2 reverse" in issue["text"]


# --------------------------------------------------------------------------- possible dividends


def _overnight_path(rng, n, overnight_std=0.003, intraday_std=0.01, start=100.0, start_date="2021-01-04"):
    """A daily open/close path with separate (smaller) overnight and intraday noise, closer to
    how real stocks behave than a single random walk on the close alone."""
    idx = pd.date_range(start_date, periods=n, freq="1D", tz="UTC")
    close_prev, opens, closes = start, [], []
    for _ in range(n):
        o = close_prev * np.exp(rng.normal(0, overnight_std))
        c = o * np.exp(rng.normal(0, intraday_std))
        opens.append(o)
        closes.append(c)
        close_prev = c
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes),
                         "close": closes, "volume": 1000.0}, index=idx)


def test_regular_dividend_sized_drops_are_reported_on_a_stock():
    rng = np.random.default_rng(42)
    df = _overnight_path(rng, 756)
    for i in range(60, 756, 91):  # a clean quarterly drop, clearly bigger than the daily noise
        df.iloc[i:, :4] = df.iloc[i:, :4] * (1 - 5 * 0.003)
    hits = dataquality.find_dividend_gaps(df, pd.Timedelta(days=1), "AAPL")
    assert len(hits) == 8
    rep = dataquality.report(df, "1d", "AAPL")
    assert rep["counts"]["dividend"] == 8
    issue = next(i for i in rep["issues"] if i["kind"] == "dividend")
    assert "ex-dividend" in issue["text"] and "not adjusted" in issue["text"]


def test_dividend_check_never_fires_on_ordinary_noise():
    """A single isolated small drop, or ordinary volatility with no real pattern, must not be
    called a dividend: that would be a false, unhelpful claim about data that's simply noisy."""
    for seed in range(40):
        df = _overnight_path(np.random.default_rng(seed), 756)
        assert dataquality.find_dividend_gaps(df, pd.Timedelta(days=1), "AAPL") == []


def test_dividend_check_is_skipped_for_non_stocks():
    rng = np.random.default_rng(42)
    df = _overnight_path(rng, 756, overnight_std=0.001, intraday_std=0.002)
    for i in range(60, 756, 91):
        df.iloc[i:, :4] = df.iloc[i:, :4] * (1 - 5 * 0.003)
    # the same pattern on a currency pair (not a stock) is never reported
    assert dataquality.find_dividend_gaps(df, pd.Timedelta(days=1), "EURUSD=X") == []
    assert dataquality._is_stock("EURUSD=X") is False
    assert dataquality._is_stock("BTC-USD") is False
    assert dataquality._is_stock("ES=F") is False
    assert dataquality._is_stock("aapl") is True   # built-in Stocks market
    assert dataquality._is_stock("us500") is False  # built-in Indices market
    assert dataquality._is_stock("RANDOMCO") is True  # unknown custom symbol: guessed as a stock


def test_only_two_occurrences_are_not_enough_to_call_it_a_pattern():
    rng = np.random.default_rng(42)
    df = _overnight_path(rng, 400, overnight_std=0.002)
    for i in range(60, 400, 340):  # only two clean drops in range
        df.iloc[i:, :4] = df.iloc[i:, :4] * (1 - 5 * 0.002)
    assert dataquality.find_dividend_gaps(df, pd.Timedelta(days=1), "AAPL") == []


# --------------------------------------------------------------------------- futures rolls


def test_big_step_on_a_futures_symbol_is_called_a_likely_roll():
    df = hourly_bars()
    df.iloc[150:, :4] = df.iloc[150:, :4] + 30  # a step, not close to any split ratio
    rep = dataquality.report(df, "1h", "ES=F")
    assert rep["counts"] == {"roll": 1}
    issue = rep["issues"][0]
    assert "contract roll" in issue["text"] and "roll" == issue["kind"]


def test_same_step_on_a_non_futures_symbol_stays_a_plain_jump():
    df = hourly_bars()
    df.iloc[150:, :4] = df.iloc[150:, :4] + 30
    rep = dataquality.report(df, "1h", "TEST")
    assert rep["counts"] == {"jump": 1}


def test_is_futures_checks_the_built_in_symbol_and_the_raw_one():
    assert dataquality._is_futures("us500")   # built-in Indices market: Instrument.symbol is "ES=F"
    assert not dataquality._is_futures("aapl")
    assert dataquality._is_futures("NQ=F")    # a custom Yahoo futures ticker typed directly
    assert not dataquality._is_futures("AAPL")


# --------------------------------------------------------------------------- provider facts


def test_provider_facts_cover_every_source():
    for source in ("dukascopy", "yahoo", "oanda", "csv"):
        facts = data_mod.provider_facts("whatever", source)
        assert facts["source"] == source
        assert facts["price"] and facts["adjusted"] and facts["history"]
        assert facts["spread_available"] in (True, False, None)
    assert data_mod.provider_facts("whatever", "dukascopy")["spread_available"] is True
    assert data_mod.provider_facts("whatever", "yahoo")["spread_available"] is False
    assert data_mod.provider_facts("whatever", "csv")["spread_available"] is None


def test_provider_facts_for_auto_markets_follow_the_configured_source_and_sample():
    assert data_mod.provider_facts("us500", "auto", {"source": "oanda"})["source"] == "oanda"
    assert data_mod.provider_facts("us500", "auto", {})["source"] == "dukascopy"  # default
    sample = data_mod.provider_facts("sample", "auto", {"source": "yahoo"})
    assert sample["source"] == "sample" and "made-up" in sample["price"]


# --------------------------------------------------------------------------- session assumptions


def test_session_notes_distinguish_exchange_crypto_and_cfd_markets():
    stock = markethours.session_notes("aapl")
    assert stock["calendar"] in ("exchange", "fallback") and "exchange holidays" in stock["weekend"].lower() or \
        "exchange holidays" in stock["weekend"]
    crypto = markethours.session_notes("btc")
    assert crypto["calendar"] is None and crypto["weekend"] == "Never closes"
    cfd = markethours.session_notes("us500")
    assert cfd["calendar"] is None and "17:00-18:00" in cfd["weekend"]


def test_session_notes_says_whether_the_real_calendar_was_used(monkeypatch):
    markethours._calendar.cache_clear()
    monkeypatch.setattr(markethours, "_calendar", lambda code: None)
    assert markethours.session_notes("aapl")["calendar"] == "fallback"


def test_session_notes_uses_the_real_calendar_when_available():
    pytest.importorskip("exchange_calendars")
    markethours._calendar.cache_clear()
    assert markethours.session_notes("aapl")["calendar"] == "exchange"


# --------------------------------------------------------------------------- run record wiring


def test_backtest_stamp_states_provider_facts_and_session(client, ws):
    sid = client.post("/api/strategies", json={"data": CSV_STRATEGY}, headers=H).json()["id"]
    meta = client.post("/api/backtest", json={"data": CSV_STRATEGY, "strategy_id": sid}, headers=H).json()["meta"]
    pf = meta["provider_facts"]
    assert pf["source"] == "csv" and pf["spread_available"] is None and pf["price"]
    session = meta["session"]
    assert session["hours"] and session["weekend"] and session["calendar_text"]


# --------------------------------------------------------------------------- daylight saving: resampling


def test_new_york_daily_candles_keep_the_same_local_hour_through_spring_forward():
    idx = pd.date_range("2026-03-01 00:00", "2026-03-12 00:00", freq="1h", tz="UTC")
    raw = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=idx)
    daily = resample_ohlcv(raw, "1d", "new_york")
    local_hours = {t.tz_convert("America/New_York").hour for t in daily.index}
    assert local_hours == {17}  # not 18: before this fix, the hour drifted forward after the transition
    assert daily["volume"].sum() == raw["volume"].sum()  # no candles lost or double-counted


def test_new_york_daily_candles_keep_the_same_local_hour_through_fall_back():
    idx = pd.date_range("2026-10-25 00:00", "2026-11-05 00:00", freq="1h", tz="UTC")
    raw = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=idx)
    daily = resample_ohlcv(raw, "1d", "new_york")
    local_hours = {t.tz_convert("America/New_York").hour for t in daily.index}
    assert local_hours == {17}  # not 16
    assert daily["volume"].sum() == raw["volume"].sum()


def test_four_hour_candles_keep_their_local_hours_through_a_dst_transition():
    idx = pd.date_range("2026-10-30 00:00", "2026-11-02 00:00", freq="15min", tz="UTC")
    raw = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=idx)
    four = resample_ohlcv(raw, "4h", "new_york")
    local_hours = sorted({t.tz_convert("America/New_York").hour for t in four.index})
    assert local_hours == [1, 5, 9, 13, 17, 21]
    assert abs(four["volume"].sum() - raw["volume"].sum()) < 1e-9


# --------------------------------------------------------------------------- daylight saving: market hours


def test_cfd_daily_break_stays_at_17_00_new_york_across_us_transitions():
    # US spring forward: Sun 2026-03-08. Tuesday before (EST, UTC-5): 17:00 NY = 22:00 UTC.
    assert markethours.closed_until("us500", T("2026-03-03 22:00", tz="UTC")) == \
        (T("2026-03-03 23:00", tz="UTC"), "daily break")
    assert markethours.closed_until("us500", T("2026-03-03 21:00", tz="UTC")) is None
    # Tuesday after (EDT, UTC-4): 17:00 NY = 21:00 UTC -- not 22:00, or the break has drifted an hour
    assert markethours.closed_until("us500", T("2026-03-10 21:00", tz="UTC")) == \
        (T("2026-03-10 22:00", tz="UTC"), "daily break")
    # US fall back: Sun 2026-11-01. Tuesday before (EDT): 17:00 NY = 21:00 UTC.
    assert markethours.closed_until("us500", T("2026-10-27 21:00", tz="UTC")) == \
        (T("2026-10-27 22:00", tz="UTC"), "daily break")
    # Tuesday after (EST): 17:00 NY = 22:00 UTC
    assert markethours.closed_until("us500", T("2026-11-03 22:00", tz="UTC")) == \
        (T("2026-11-03 23:00", tz="UTC"), "daily break")


def test_exchange_fallback_timetable_stays_at_local_wall_clock_across_uk_transitions(monkeypatch):
    markethours._calendar.cache_clear()
    monkeypatch.setattr(markethours, "_calendar", lambda code: None)  # as if exchange_calendars were missing
    # UK spring forward is 2026-03-29; London is UTC+0 before, UTC+1 after
    before = markethours.exchange_closed_until("XLON", T("2026-03-27 08:30", tz="UTC"))  # 08:30 local, open
    after = markethours.exchange_closed_until("XLON", T("2026-03-30 07:30", tz="UTC"))   # 08:30 BST, open
    assert before is None and after is None
    shut_before = markethours.exchange_closed_until("XLON", T("2026-03-27 07:30", tz="UTC"))  # 07:30 local
    shut_after = markethours.exchange_closed_until("XLON", T("2026-03-30 06:30", tz="UTC"))   # 07:30 BST
    assert shut_before == (T("2026-03-27 08:00", tz="UTC"), "outside exchange hours")
    assert shut_after == (T("2026-03-30 07:00", tz="UTC"), "outside exchange hours")  # 08:00 BST = 07:00 UTC


def test_exchange_real_calendar_handles_us_and_uk_holidays():
    pytest.importorskip("exchange_calendars")
    markethours._calendar.cache_clear()
    shut = markethours.closed_until("spy", T("2026-11-26 15:00", tz="UTC"))  # US Thanksgiving
    assert shut is not None and shut[1] == "holiday"
    shut_uk = markethours.exchange_closed_until("XLON", T("2026-12-25 10:00", tz="UTC"))  # Christmas Day
    assert shut_uk is not None and shut_uk[1] == "holiday"


# --------------------------------------------------------------------------- malformed candles


def test_zero_or_negative_price_is_rejected():
    idx = pd.date_range("2025-01-06", periods=3, freq="1h", tz="UTC")
    bad_zero = pd.DataFrame({"open": [10, 0, 10], "high": [11, 0, 11], "low": [9, 0, 9],
                             "close": [10.5, 0, 10.5], "volume": [5, 5, 5]}, index=idx)
    with pytest.raises(DataError, match="zero or negative"):
        normalise(bad_zero)
    bad_negative = bad_zero.copy()
    bad_negative.iloc[1] = [-10, -9, -11, -10, 5]
    with pytest.raises(DataError, match="zero or negative"):
        normalise(bad_negative)


def test_nan_rows_are_dropped_not_raised():
    idx = pd.date_range("2025-01-06", periods=4, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": [10, np.nan, 10, 10], "high": [11, 11, 11, 11], "low": [9, 9, 9, 9],
                       "close": [10.5, 10.5, 10.5, 11], "volume": [5, 5, 5, 5]}, index=idx)
    out = normalise(df)
    assert len(out) == 3 and not out.isna().any().any()


def test_high_below_low_and_open_close_outside_range_are_widened_not_dropped():
    idx = pd.date_range("2025-01-06", periods=2, freq="1h", tz="UTC")
    # row 0: high (9) is below low (11) -- inverted; row 1: close (12) sits above the given high (11)
    df = pd.DataFrame({"open": [10, 10], "high": [9, 11], "low": [11, 9], "close": [10, 12],
                       "volume": [5, 5]}, index=idx)
    out = normalise(df)
    assert len(out) == 2
    assert (out["high"] >= out[["open", "close", "low"]].max(axis=1)).all()
    assert (out["low"] <= out[["open", "close", "high"]].min(axis=1)).all()
    assert out.attrs["repaired_bars"] == 2


def test_duplicate_timestamps_keep_the_last_and_unsorted_rows_are_sorted():
    idx = pd.to_datetime(["2025-01-06 02:00", "2025-01-06 00:00", "2025-01-06 01:00", "2025-01-06 01:00"], utc=True)
    df = pd.DataFrame({"open": [1, 2, 3, 4], "high": [1, 2, 3, 4], "low": [1, 2, 3, 4], "close": [1, 2, 3, 4],
                       "volume": [1, 1, 1, 1]}, index=idx)
    out = normalise(df)
    assert list(out.index) == sorted(out.index)
    assert out.loc[pd.Timestamp("2025-01-06 01:00", tz="UTC"), "close"] == 4  # the later duplicate wins
    assert len(out) == 3
    assert out.attrs["disordered_bars"] >= 1


def test_malformed_csv_timestamp_raises_a_clear_data_error(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("timestamp,open,high,low,close,volume\n"
                 "2025-01-06 00:00,10,11,9,10.5,5\n"
                 "not-a-real-date,10,11,9,10.5,5\n"
                 "2025-01-06 02:00,10,11,9,10.5,5\n")
    prov = data_mod.CSVProvider(base_dir=tmp_path)
    with pytest.raises(DataError, match="time"):
        prov.load(p)


def test_csv_rows_with_a_non_numeric_price_are_dropped_not_crashed(tmp_path):
    p = tmp_path / "junk.csv"
    p.write_text("timestamp,open,high,low,close,volume\n"
                 "2025-01-06 00:00,10,11,9,10.5,5\n"
                 "2025-01-06 01:00,abc,11,9,10.5,5\n"
                 "2025-01-06 02:00,10,11,9,10.5,5\n")
    prov = data_mod.CSVProvider(base_dir=tmp_path)
    out = prov.load(p)
    assert len(out) == 2  # the junk row's price couldn't be read, so it was dropped like any other NaN


def test_ragged_csv_row_does_not_crash(tmp_path):
    p = tmp_path / "ragged.csv"
    p.write_text("timestamp,open,high,low,close,volume\n"
                 "2025-01-06 00:00,10,11,9,10.5,5\n"
                 "2025-01-06 01:00,10,11,9\n"
                 "2025-01-06 02:00,10,11,9,10.5,5\n")
    prov = data_mod.CSVProvider(base_dir=tmp_path)
    out = prov.load(p)
    assert len(out) == 2
