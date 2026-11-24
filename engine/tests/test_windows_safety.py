"""Guards for problems that only showed on the Windows / Python 3.11 CI runners, never on a Linux dev machine."""
import ast
import json
from pathlib import Path

from fastapi.testclient import TestClient

from tradealerts.sample import synthetic_bars
from tradealerts.web import create_app

ENGINE = Path(__file__).resolve().parents[1]
H = {"X-Requested-With": "tradealerts", "Content-Type": "application/json"}


def _text_calls_without_encoding(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        texty = any(isinstance(kw.get(k), ast.Constant) and kw[k].value is True for k in ("text", "universal_newlines"))
        if texty and "encoding" not in kw:
            yield f"{path.relative_to(ENGINE)}:{node.lineno}"


def test_subprocess_text_output_names_its_encoding():
    """Windows decodes child output as cp1252 unless told otherwise, so "–" from Node came back garbled."""
    files = [*(ENGINE / "src").rglob("*.py"), *(ENGINE / "tests").rglob("*.py")]
    bad = [hit for f in files for hit in _text_calls_without_encoding(f)]
    assert not bad, "add encoding=\"utf-8\" next to text=True: " + ", ".join(bad)


def test_body_too_deep_to_parse_gets_the_plain_message(tmp_path, monkeypatch):
    """Python 3.11 and Windows can't parse very deep JSON; that must still be a plain 422, not a bare 400."""
    (tmp_path / "data").mkdir()
    synthetic_bars(days=5, freq="1h", seed=1).to_csv(tmp_path / "data" / "bars.csv")
    (tmp_path / "config.yaml").write_text("notifiers: [{type: console}]\n", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "config.yaml"))

    def too_deep(*_a, **_k):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    monkeypatch.setattr(json, "loads", too_deep)  # what the body parser hits on those platforms
    r = client.post("/api/backtest", content=b'{"data": {}}', headers=H)
    monkeypatch.undo()
    assert r.status_code == 422
    assert r.json()["detail"] == "Rules are nested too deeply."


def test_other_bad_bodies_keep_their_usual_answer(tmp_path):
    (tmp_path / "config.yaml").write_text("notifiers: [{type: console}]\n", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "config.yaml"))
    r = client.post("/api/backtest", content=b"{not json", headers=H)
    assert r.status_code == 422
    assert "nested" not in json.dumps(r.json())
