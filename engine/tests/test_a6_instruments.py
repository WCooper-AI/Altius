"""A6: one typed record per built-in market (instruments.py)."""
import pytest
from fastapi.testclient import TestClient

from tradealerts import instruments, markethours
from tradealerts.recipes import MARKETS
from tradealerts.strategy import strategy_from_dict
from tradealerts.web import create_app


def test_every_built_in_market_has_a_full_record():
    assert set(instruments.INSTRUMENTS) == {m["key"] for m in MARKETS}
    for i in instruments.INSTRUMENTS.values():
        assert len(i.quote) == 3 and i.tick_size > 0 and i.point_value > 0 and i.min_size > 0 and i.size_step > 0
        assert i.hours, i.key
    assert instruments.get("nope") is None and instruments.get(None) is None and instruments.defaults("nope") == {}


def test_currencies_steps_and_their_value():
    eurgbp, gold, eurusd = instruments.get("eurgbp"), instruments.get("gold"), instruments.get("eurusd")
    assert (eurgbp.base, eurgbp.quote) == ("EUR", "GBP") and gold.base == "XAU" and instruments.get("us500").base is None
    assert gold.tick_value == pytest.approx(0.01) and eurusd.tick_value == pytest.approx(0.1)
    assert eurusd.public()["tick_value"] == 0.1


def test_conversion_pairs():
    gold = instruments.get("gold")
    assert gold.conversion("USD") is None and gold.conversion("usd") is None
    assert gold.conversion("GBP") == ("gbpusd", True) and gold.conversion("EUR") == ("eurusd", True)
    assert instruments.get("de40").conversion("USD") == ("eurusd", False)
    assert gold.conversion("JPY") is None


def test_rounding_prices_and_sizes():
    eurusd, us30 = instruments.get("eurusd"), instruments.get("us30")
    assert eurusd.round_price(1.123456) == 1.12346 and us30.round_price(42010.6) == 42011
    assert eurusd.round_size(0.0149) == 0.01 and eurusd.round_size(0.005) == 0.0 and eurusd.round_size(1.0) == 1.0


def test_hours_come_from_the_record():
    assert "New York" in instruments.get("aapl").hours and instruments.get("aapl").exchange == "XNYS"
    assert markethours.exchange_for("aapl") == "XNYS" and markethours.exchange_for("gold") is None
    assert markethours.trades_24_7("btc") and not markethours.trades_24_7("gold")
    assert instruments.get("btc").hours == "Every day, around the clock"


def test_blank_strategy_settings_fall_back_to_the_record():
    base = {"name": "x", "market": {"symbol": "eurusd", "provider": "auto", "timeframe": "1h"}, "direction": "long",
            "entry": {"all": [{"left": "close", "op": ">", "right": 1}]}, "exit": {"stop": {"atr": 2}},
            "risk": {"account_size": 1000, "risk_per_trade_pct": 1, "currency": "£"}}
    s = strategy_from_dict(base)
    eurusd = instruments.get("eurusd")
    assert s.risk.point_value == eurusd.point_value and s.risk.margin_pct == eurusd.margin_pct
    assert s.risk.min_size == eurusd.min_size and s.risk.size_step == eurusd.size_step
    assert s.risk.market_currency == "USD" and s.spread == eurusd.spread


def test_markets_and_recipes_endpoints_list_instruments(tmp_path):
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\n")
    client = TestClient(create_app(tmp_path / "config.yaml"))
    for path in ("/api/markets", "/api/recipes"):
        got = {i["key"]: i for i in client.get(path).json()["instruments"]}
        assert got["gold"]["tick_size"] == 0.01 and got["gold"]["quote"] == "USD" and got["aapl"]["exchange"] == "XNYS"
