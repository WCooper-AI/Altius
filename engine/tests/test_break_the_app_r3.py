"""Fixes from the security-break-the-app follow-up (R3-1 and Nit):
Non-finite numbers in rules, deeply nested rule trees, and inf/nan in backtest responses."""

import json
import math

import pytest
from fastapi.testclient import TestClient

from tradealerts import rules
from tradealerts.sample import synthetic_bars
from tradealerts.web import create_app

H = {"X-Requested-With": "tradealerts"}


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "data").mkdir()
    synthetic_bars(days=30, freq="1h", seed=4).to_csv(tmp_path / "data" / "bars.csv")
    (tmp_path / "config.yaml").write_text("notifiers: [{type: console}]\n")
    return tmp_path


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws / "config.yaml"))


@pytest.fixture
def base_strategy():
    """A minimal valid strategy."""
    return {
        "name": "Test",
        "market": {"symbol": "data/bars.csv", "label": "Sample", "provider": "csv", "timeframe": "1h"},
        "direction": "long",
        "entry": {"all": [{"left": "close", "op": ">", "right": 100}]},
        "exit": {"stop": {"atr": 2, "atr_period": 14}, "target": {"r_multiple": 2}, "max_bars": 24},
        "risk": {"account_size": 1000, "risk_per_trade_pct": 0.5, "currency": "GBP"},
    }


# ---------------------------------------------------------------- Bare operands: non-finite numbers

def test_bare_operand_infinity_rejected():
    """Infinity in a bare operand is rejected with a plain message."""
    with pytest.raises(rules.RuleError, match="must be a normal number"):
        rules.compile_operand(float("inf"))


def test_bare_operand_neg_infinity_rejected():
    """Negative infinity in a bare operand is rejected."""
    with pytest.raises(rules.RuleError, match="must be a normal number"):
        rules.compile_operand(float("-inf"))


def test_bare_operand_nan_rejected():
    """NaN in a bare operand is rejected."""
    with pytest.raises(rules.RuleError, match="must be a normal number"):
        rules.compile_operand(float("nan"))


def test_bare_operand_very_large_number_rejected():
    """1e400 (too large to be finite) is rejected."""
    with pytest.raises(rules.RuleError, match="must be a normal number"):
        rules.compile_operand(1e400)


# ---------------------------------------------------------------- Scale/add: non-finite numbers

def test_scale_infinity_rejected():
    """Infinity in scale is rejected with a plain message."""
    with pytest.raises(rules.RuleError, match="scale must be a normal number"):
        rules.compile_operand({"field": "close", "scale": float("inf")})


def test_scale_nan_rejected():
    """NaN in scale is rejected."""
    with pytest.raises(rules.RuleError, match="scale must be a normal number"):
        rules.compile_operand({"field": "close", "scale": float("nan")})


def test_add_infinity_rejected():
    """Infinity in add is rejected."""
    with pytest.raises(rules.RuleError, match="add must be a normal number"):
        rules.compile_operand({"field": "close", "add": float("inf")})


def test_add_nan_rejected():
    """NaN in add is rejected."""
    with pytest.raises(rules.RuleError, match="add must be a normal number"):
        rules.compile_operand({"field": "close", "add": float("nan")})


# ---------------------------------------------------------------- Validation and backtest
# Note: inf/nan cannot be sent via JSON (client encoder rejects them with allow_nan=False),
# so these validation tests use the compile_operand directly instead of backtest endpoint.


# ---------------------------------------------------------------- Backtest response sanitisation

def test_backtest_response_has_no_inf_nan(client, base_strategy):
    """The backtest response is cleaned of any inf/nan (turned to None/null)."""
    r = client.post("/api/backtest", json={"data": base_strategy}, headers=H)
    assert r.status_code == 200
    result = r.json()
    # Verify the response is valid JSON (would have failed if it contained inf/nan)
    # and that there are no inf/nan values anywhere
    def has_inf_nan(obj):
        if isinstance(obj, dict):
            return any(has_inf_nan(v) for v in obj.values())
        elif isinstance(obj, list):
            return any(has_inf_nan(v) for v in obj)
        elif isinstance(obj, float):
            return math.isnan(obj) or math.isinf(obj)
        return False
    assert not has_inf_nan(result), "Backtest response contains inf or nan"


# ---------------------------------------------------------------- Deep rule nesting

def deep_nested_rule(depth: int):
    """Create a rule tree nested to a given depth: {all: [{all: [{all: ...}]}]}."""
    rule = {"left": "close", "op": ">", "right": 100}
    for _ in range(depth):
        rule = {"all": [rule]}
    return rule


def test_deeply_nested_rules_dont_recurse_error():
    """A deeply nested rule tree produces a RuleError, not RecursionError."""
    df = synthetic_bars(days=5, freq="1h", seed=1)
    with pytest.raises(rules.RuleError, match="nested too deeply"):
        rules.evaluate(deep_nested_rule(2000), df)


def test_deeply_nested_rule_error_has_no_recursion_text():
    """The error message for deep nesting doesn't expose 'recursion'."""
    df = synthetic_bars(days=5, freq="1h", seed=1)
    try:
        rules.evaluate(deep_nested_rule(2000), df)
    except rules.RuleError as e:
        assert "recursion" not in str(e).lower()
        assert "nested" in str(e).lower()


def test_deeply_nested_rules_rejected_on_backtest(client):
    """A strategy with a deeply nested rule is rejected on backtest (422, plain message)."""
    # Create a simple strategy and add deep nesting directly (avoid deepcopy which also hits recursion limit)
    # The JSON is built as text: json.dumps of a 2000-deep tree hits the recursion limit on
    # Python 3.11 and on Windows (smaller C stack), which broke this test there, not the app.
    strategy = {
        "name": "Test",
        "market": {"symbol": "data/bars.csv", "label": "Sample", "provider": "csv", "timeframe": "1h"},
        "direction": "long",
        "entry": "__ENTRY__",
        "exit": {"stop": {"atr": 2, "atr_period": 14}, "target": {"r_multiple": 2}, "max_bars": 24},
        "risk": {"account_size": 1000, "risk_per_trade_pct": 0.5, "currency": "GBP"},
    }
    depth = 2000
    entry = '{"all": [' * depth + '{"left": "close", "op": ">", "right": 100}' + ']}' * depth
    body = json.dumps({"data": strategy}).replace('"__ENTRY__"', entry)
    r = client.post("/api/backtest", content=body, headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "nested too deeply" in detail or "nested" in detail
    assert "recursion" not in detail.lower()
