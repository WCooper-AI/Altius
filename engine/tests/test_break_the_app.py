"""Fixes from the break-the-app report (Building Docs/security/break-the-app-2026-11-22.md):
YAML alias bombs, server paths in upload errors, and a cap on request bodies."""

import time

import pytest
import yaml
from fastapi.testclient import TestClient

from tradealerts import safeyaml
from tradealerts.sample import synthetic_bars
from tradealerts.strategy import StrategyError, dump_strategy, load_strategy
from tradealerts.web import create_app
from tradealerts.web.app import MAX_BODY

H = {"X-Requested-With": "tradealerts"}


def bomb(levels: int) -> str:
    """Each level is a list of nine aliases to the one above: 9**levels items once expanded."""
    lines = ['l0: &l0 ["x","x","x","x","x","x","x","x","x"]']
    for i in range(1, levels):
        lines.append(f"l{i}: &l{i} [" + ",".join([f"*l{i - 1}"] * 9) + "]")
    return "\n".join(lines) + "\n"


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "data").mkdir()
    synthetic_bars(days=30, freq="1h", seed=4).to_csv(tmp_path / "data" / "bars.csv")
    (tmp_path / "config.yaml").write_text("notifiers: [{type: console}]\n")
    return tmp_path


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws / "config.yaml"))


# ---------------------------------------------------------------- YAML aliases

def test_full_bomb_refused_at_parse():
    started = time.perf_counter()
    with pytest.raises(yaml.YAMLError, match="aliases"):
        safeyaml.load(bomb(9))
    assert time.perf_counter() - started < 1


def test_alias_error_points_at_the_line():
    with pytest.raises(yaml.YAMLError) as info:
        safeyaml.load("a: &x 1\nb: *x\n")
    assert info.value.problem_mark.line == 1


def test_plain_yaml_and_unused_anchor_still_load():
    assert safeyaml.load("a: 1\nb: [x, y]\n") == {"a": 1, "b": ["x", "y"]}
    assert safeyaml.load("a: &x 1\n") == {"a": 1}


def test_dump_never_writes_aliases():
    shared = {"indicator": "rsi", "period": 14}
    text = safeyaml.dump({"a": shared, "b": shared})
    assert "&" not in text and "*" not in text
    assert safeyaml.load(text) == {"a": shared, "b": shared}
    assert "&" not in dump_strategy({"entry": {"all": [shared, shared]}})


def test_yaml_preview_rejects_bomb(client):
    started = time.perf_counter()
    r = client.post("/api/yaml/parse", json={"yaml": bomb(5)}, headers=H)
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is False and out["data"] is None
    assert "aliases" in out["error"] and "line 2" in out["error"]
    assert time.perf_counter() - started < 1


def test_import_rejects_bomb(client):
    r = client.post("/api/strategies/import", json={"yaml": "market: x\nentry: x\n" + bomb(5)}, headers=H)
    assert r.status_code == 422


def test_strategy_file_with_alias_is_an_error(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("name: S\n" + bomb(3))
    with pytest.raises(StrategyError, match="aliases"):
        load_strategy(p)


def test_bundled_examples_still_load(client):
    r = client.get("/api/examples")
    assert r.status_code == 200 and r.json()


# ---------------------------------------------------------------- upload errors

def test_upload_error_names_the_file_not_the_server_path(client, ws):
    r = client.post("/api/uploads/evil.csv", content=b"alert(1)//,a,b\n1,2,3\n", headers=H)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "evil.csv needs a time column" in detail
    assert ".part" not in detail and str(ws) not in detail and "/" not in detail.split(":", 1)[1]


# ---------------------------------------------------------------- request size

def test_oversized_body_refused(client):
    r = client.post("/api/yaml/parse", content=b"{" + b" " * MAX_BODY + b"}",
                    headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_chunked_body_without_length_refused(client):
    r = client.post("/api/yaml/parse", content=iter([b'{"yaml": "a: 1"}']),
                    headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 411


def test_normal_body_still_accepted(client):
    r = client.post("/api/yaml/parse", json={"yaml": "a: 1"}, headers=H)
    assert r.status_code == 200


def test_uploads_keep_their_own_limit(client):
    r = client.post("/api/uploads/big.csv", content=b"x" * (MAX_BODY + 1), headers=H)
    assert r.status_code == 422  # reached the route (not a bad CSV's 413 from the body cap)


# ---------------------------------------------------------------- deep nesting

DEEP = "a: " + "[" * 5000 + "]" * 5000 + "\n"


def test_deep_nesting_is_a_yaml_error():
    with pytest.raises(yaml.YAMLError, match="nested too deeply"):
        safeyaml.load(DEEP)


def test_yaml_preview_rejects_deep_nesting(client):
    r = client.post("/api/yaml/parse", json={"yaml": DEEP}, headers=H)
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is False and out["data"] is None and "nested too deeply" in out["error"]


def test_import_rejects_deep_nesting(client):
    r = client.post("/api/strategies/import", json={"yaml": "market: x\nentry: x\n" + DEEP}, headers=H)
    assert r.status_code == 422
