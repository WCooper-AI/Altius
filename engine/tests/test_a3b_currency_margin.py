"""Session A3b: markets priced in another currency, margin, the margin limit and broker close-out.

Hand-worked cases use 1h candles, a £1,000 account at 1% risk (£10), no costs, a 2-point stop
and a 4-point target: a signal candle closes at 101, the next candle fills at its open of 101.
"""

import pandas as pd
import pytest

from tradealerts import analysis, fxrates, invariants, report
from tradealerts.backtest import run_backtest
from tradealerts.data import DataError
from tradealerts.strategy import StrategyError

from .conftest import make_bars, make_strategy
from .test_web import CSV_STRATEGY, H
from .test_v03_s8e import client, ws  # noqa: F401 (fixtures)

NO_COSTS = {"spread": 0, "use_data_spread": False}
ABOVE = {"left": "close", "op": ">", "right": 100.5}
FLAT = (100, 100, 100, 100)
SIGNAL = (100, 100, 100, 101)
WIN = (101, 106, 101, 105)             # target 105 reached inside the candle
LOSS = (101, 101, 98, 98)              # stop 99 reached inside the candle
HOLD = (101, 102, 100.5, 101.5)        # neither level reached
USD = {"size_currency": "market", "market_currency": "USD", "currency": "GBP"}


def strat(risk=None, costs=NO_COSTS, **kw):
    return make_strategy(entry=ABOVE, costs=costs, risk={"account_size": 1000, "risk_per_trade_pct": 1, "point_value": 1,
                                                          "size_step": 0.01, "min_size": 0.01, **(risk or {})}, **kw)


def run(rows, risk=None, fx=None, **kw):
    df = make_bars(rows)
    if fx is not None:
        df = df.assign(fx=fx)
    res = run_backtest(strat(risk, **kw), df)
    assert invariants.check(res) == []
    return res


# --------------------------------------------------------------------------- currency

def test_account_currency_sizes_do_not_convert():
    res = run([FLAT, SIGNAL, WIN])
    t = res.trades.iloc[0]
    assert (t["fx_entry"], t["fx"], t["margin"]) == (1.0, 1.0, 0.0)
    assert res.metrics["fx"] is None and res.metrics["max_margin_used_pct"] is None


def test_fixed_rate_hand_worked():
    # $ per point, £0.80 per $: size = £10 / (2 points x $1 x 0.8) = 6.25.
    # Target: 4 points x 6.25 = $25 = £20, which is 2R.
    res = run([FLAT, SIGNAL, WIN], risk={**USD, "fx_rate": 0.8})
    t = res.trades.iloc[0]
    assert t["size"] == pytest.approx(6.25) and t["pnl"] == pytest.approx(20.0)
    assert t["r_multiple"] == pytest.approx(2.0) and t["fx"] == pytest.approx(0.8)
    assert res.metrics["fx"] == {"from": "USD", "to": "GBP", "fixed": 0.8}


def test_downloaded_rates_size_at_entry_and_convert_at_exit():
    # Sized at the entry candle's rate 0.8 (6.25), closed when the rate is 0.5:
    # $25 = £12.50, against £10 risked at entry = 1.25R.
    rows = [FLAT, SIGNAL, HOLD, WIN]
    res = run(rows, risk=USD, fx=[0.8, 0.8, 0.8, 0.5])
    t = res.trades.iloc[0]
    assert (t["fx_entry"], t["fx"]) == (pytest.approx(0.8), pytest.approx(0.5))
    assert t["size"] == pytest.approx(6.25) and t["pnl"] == pytest.approx(12.5)
    assert t["r_multiple"] == pytest.approx(1.25)
    assert res.metrics["fx"]["low"] == pytest.approx(0.5) and res.metrics["fx"]["high"] == pytest.approx(0.8)


def test_commission_converts_but_the_flat_amount_is_account_money():
    # fx 0.5: size = £10 / (2 x 0.5) = 10. 0.1% of each fill's value in £: 101 x 10 x 0.5 x 0.001 = 0.505
    # in, 105 x 10 x 0.5 x 0.001 = 0.525 out; plus £1 flat each way = £3.03. Gross 4 x 10 x 0.5 = £20.
    res = run([FLAT, SIGNAL, WIN], risk={**USD, "fx_rate": 0.5},
              costs={**NO_COSTS, "commission": 1, "commission_pct": 0.1})
    t = res.trades.iloc[0]
    assert t["commission"] == pytest.approx(3.03) and t["pnl"] == pytest.approx(20 - 3.03)


def test_converting_without_rates_is_a_plain_error():
    with pytest.raises(ValueError, match="exchange rate"):
        run_backtest(strat(USD), make_bars([FLAT, SIGNAL, WIN]))


def test_same_currency_needs_no_rate():
    res = run([FLAT, SIGNAL, WIN], risk={**USD, "market_currency": "GBP"})
    assert res.trades.iloc[0]["pnl"] == pytest.approx(20.0) and res.metrics["fx"] is None


def test_shuffled_market_keeps_the_rates():
    import numpy as np
    df = make_bars([FLAT, SIGNAL, WIN] * 5).assign(fx=np.linspace(0.7, 0.8, 15))
    out = analysis.shuffled_market(df, np.random.default_rng(1))
    assert list(out["fx"]) == list(df["fx"])


def test_pair_for():
    assert fxrates.pair_for("USD", "GBP") == ("gbpusd", True)
    assert fxrates.pair_for("eur", "usd") == ("eurusd", False)
    assert fxrates.pair_for("GBP", "EUR") == ("eurgbp", True)
    assert fxrates.pair_for("JPY", "GBP") is None


def test_rates_are_the_close_known_at_each_open():
    pair = make_bars([(1.2, 1.3, 1.2, 1.25), (1.25, 1.3, 1.2, 1.30), (1.3, 1.4, 1.3, 1.35)], freq="1D")
    when = pd.DatetimeIndex(["2025-01-06 06:00", "2025-01-07 12:00", "2025-01-08 00:00"], tz="UTC")
    got = fxrates.rates(when, pair, invert=True)
    # before the first daily candle has ended: its close; then the close of the last day that ended
    assert list(got) == [pytest.approx(1 / 1.25), pytest.approx(1 / 1.25), pytest.approx(1 / 1.30)]


def test_attach_loads_the_pair_and_errors_plainly():
    df = make_bars([FLAT] * 30)
    pair = make_bars([(1.25, 1.25, 1.25, 1.25)] * 5, start="2025-01-01", freq="1D")
    asked = []
    out = fxrates.attach(df, strat(USD), lambda key, days: asked.append(key) or pair)
    assert asked == ["gbpusd"] and out["fx"].iloc[-1] == pytest.approx(0.8)
    assert "fx" not in df.columns  # the cached prices aren't changed
    assert fxrates.attach(df, strat({**USD, "fx_rate": 0.8}), None) is df
    with pytest.raises(DataError, match="fixed exchange rate"):
        fxrates.attach(df, strat({**USD, "market_currency": "JPY"}), lambda k, d: pair)

    def broken(key, days):
        raise RuntimeError("offline")
    with pytest.raises(DataError, match="USD/GBP exchange rate"):
        fxrates.attach(df, strat(USD), broken)


@pytest.mark.parametrize("risk,msg", [
    ({"size_currency": "pounds"}, "'account' or 'market'"),
    ({"size_currency": "market"}, "market_currency"),
    ({"market_currency": "US"}, "3-letter"),
    ({**USD, "fx_rate": 0}, "above 0"),
    ({"margin_pct": 0}, "above 0"),
    ({"max_margin_pct": 50}, "margin rate"),
    ({"margin_pct": 5, "close_out_pct": 150}, "up to 100"),
])
def test_settings_are_checked(risk, msg):
    with pytest.raises(StrategyError, match=msg):
        strat(risk)


def test_built_in_markets_know_currency_and_margin():
    s = make_strategy(market={"symbol": "us500", "label": "US 500", "timeframe": "1h", "provider": "auto"})
    assert (s.risk.market_currency, s.risk.margin_pct, s.risk.size_currency) == ("USD", 5, "account")
    assert not s.risk.converts  # spread-bet sizes stay in the account currency until asked
    s = make_strategy(market={"symbol": "us500", "label": "US 500", "timeframe": "1h", "provider": "auto"},
                      risk={"account_size": 1000, "risk_per_trade_pct": 1, "size_currency": "market",
                            "margin_pct": 10, "currency": "GBP"})
    assert s.risk.converts and s.risk.margin_pct == 10


# --------------------------------------------------------------------------- margin

def test_margin_is_recorded_with_its_peak():
    # size 5 at 101 with 5% margin: 101 x 5 x 0.05 = £25.25, 2.525% of the £1,000 balance
    res = run([FLAT, SIGNAL, WIN], risk={"margin_pct": 5})
    assert res.trades.iloc[0]["margin"] == pytest.approx(25.25)
    assert res.metrics["max_margin_used_pct"] == pytest.approx(2.525)


def test_margin_limit_skips_the_trade():
    # 50% margin: 101 x 5 x 0.5 = £252.50, more than 20% of £1,000
    res = run([FLAT, SIGNAL, WIN], risk={"margin_pct": 50, "max_margin_pct": 20})
    assert res.trades.empty and res.metrics["blocked_by_limits"] == 1
    assert res.skipped[0].data["limit"] == "margin"
    assert any("margin limit stopped 1 trade" in n for n in res.notes)
    assert run([FLAT, SIGNAL, WIN], risk={"margin_pct": 50, "max_margin_pct": 30}).metrics["trades"] == 1


def test_close_out_hand_worked():
    # 10% risk = £100 over 2 points: size 50. Margin 38% of 101 x 50 = £1,919; close-out at 50% = £959.50.
    # Balance plus open P&L falls to £959.50 at 101 - 40.50 / 50 = 100.19, above the 99 stop.
    risk = {"risk_per_trade_pct": 10, "margin_pct": 38, "close_out_pct": 50}
    res = run([FLAT, SIGNAL, LOSS, FLAT], risk=risk)
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "close_out" and t["exit_price"] == pytest.approx(100.19)
    assert t["pnl"] == pytest.approx(-40.5) and res.metrics["closed_out"] == 1
    # with the stop closer than the close-out level, the stop goes first
    assert run([FLAT, SIGNAL, LOSS, FLAT], risk={**risk, "margin_pct": 10}).trades.iloc[0]["exit_reason"] == "stop"


def test_close_out_straight_away_when_already_past_it():
    # Margin 40% of 5,050 = £2,020; half of it (£1,010) is more than the £1,000 balance, so the
    # trade closes at the entry candle's open bid, 101.
    res = run([FLAT, SIGNAL, WIN], risk={"risk_per_trade_pct": 10, "margin_pct": 40, "close_out_pct": 50})
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "close_out" and t["exit_price"] == pytest.approx(101) and t["pnl"] == pytest.approx(0)


def test_report_mentions_margin_and_currency():
    res = run([FLAT, SIGNAL, WIN], risk={**USD, "fx_rate": 0.8, "margin_pct": 5})
    rows = dict(report.summary_rows(res))
    assert rows["Currency"] == "USD converted to GBP at a fixed 0.8" and "Most margin held" in rows


# --------------------------------------------------------------------------- strategies together

def _part(sid, risk):
    s = strat({"compound": False, **risk}, name=sid)
    return (sid, sid, run_backtest(s, make_bars([FLAT, SIGNAL, HOLD, WIN])))


def test_shared_account_sizes_with_the_entry_rate():
    # fx 0.5: £10 / (2 x 0.5) = 10 again, so the shared account matches the backtest
    part = _part("a", {**USD, "fx_rate": 0.5})
    out = analysis.shared_account([part], 1000)
    assert out["metrics"]["end_equity"] == pytest.approx(1000 + part[2].trades.iloc[0]["pnl"])


def test_shared_account_margin_limit():
    # two trades open together, each holding £25.25 (2.525%): a 4% limit leaves room for one
    parts = [_part("a", {"margin_pct": 5}), _part("b", {"margin_pct": 5})]
    out = analysis.shared_account(parts, 1000, max_margin_pct=4)
    assert out["skipped"]["margin"] == 1 and out["metrics"]["trades"] == 1
    assert out["metrics"]["max_margin_used_pct"] == pytest.approx(2.525)
    both = analysis.shared_account(parts, 1000)
    assert both["metrics"]["trades"] == 2 and both["metrics"]["max_margin_used_pct"] == pytest.approx(5.05)


# --------------------------------------------------------------------------- web

def test_backtest_route_records_currency_and_margin(client):  # noqa: F811
    data = {**CSV_STRATEGY, "risk": {**CSV_STRATEGY["risk"], **USD, "fx_rate": 0.8, "margin_pct": 5, "max_margin_pct": 100}}
    r = client.post("/api/backtest", json={"data": data}, headers=H)
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["fx"] == {"from": "USD", "to": "GBP", "fixed": 0.8}
    assert meta["risk"]["size_currency"] == "market" and meta["risk"]["max_margin_pct"] == 100
    assert meta["max_margin_used_pct"] is not None


def test_backtest_route_downloads_rates(client, monkeypatch):  # noqa: F811
    pair = make_bars([(1.25, 1.25, 1.25, 1.25)] * 400, start="2024-01-01", freq="1D")
    asked = []
    monkeypatch.setattr(fxrates, "loader", lambda cfg, cache=None: lambda key, days: asked.append(key) or pair)
    data = {**CSV_STRATEGY, "risk": {**CSV_STRATEGY["risk"], **USD}}
    r = client.post("/api/backtest", json={"data": data}, headers=H)
    assert r.status_code == 200, r.text
    assert asked == ["gbpusd"] and r.json()["meta"]["fx"]["last"] == pytest.approx(0.8)


def test_backtest_route_explains_a_missing_pair(client):  # noqa: F811
    data = {**CSV_STRATEGY, "risk": {**CSV_STRATEGY["risk"], **USD, "market_currency": "JPY"}}
    r = client.post("/api/backtest", json={"data": data}, headers=H)
    assert r.status_code == 422 and "fixed exchange rate" in r.json()["detail"]


def test_portfolio_route_takes_the_margin_limit(client):  # noqa: F811
    from .test_v03_s8e import _two_ids
    a, b = _two_ids(client)
    r = client.post("/api/portfolio", json={"ids": [a, b], "balance": 5000, "shared": True, "max_margin_pct": 50}, headers=H)
    assert r.status_code == 200, r.text
    assert "margin" in r.json()["skipped"]
