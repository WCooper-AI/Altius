"""A6: restore checks every part first and puts everything back if it stops part-way."""
import io
import sqlite3
import struct
import warnings
import zipfile

import pytest
from fastapi.testclient import TestClient

from tradealerts.state import StateStore
from tradealerts.web import create_app
from tradealerts.web.routes import backup as backup_routes
from tradealerts.workspace import Workspace

H = {"X-Requested-With": "tradealerts", "Content-Type": "application/zip"}
README = "trading-alerts-backup.txt"
STRATEGY = "name: Old\nmarket: {symbol: gold, provider: auto, timeframe: 1h}\n"


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "strategies").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "config.yaml").write_text("poll_minutes: 15\nnotifiers:\n  - type: console\n")
    (tmp_path / "strategies" / "old.yaml").write_text(STRATEGY)
    return tmp_path


@pytest.fixture
def client(ws):
    c = TestClient(create_app(ws / "config.yaml"), raise_server_exceptions=False)
    c.post("/api/price-alerts", json={"market": "gold", "direction": "above", "price": 2700},
           headers={"X-Requested-With": "tradealerts"})
    return c


def zipped(files: dict, readme=True) -> bytes:
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # duplicate names on purpose
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            if readme:
                z.writestr(README, "backup")
            for name, data in (files.items() if isinstance(files, dict) else files):
                z.writestr(name, data)
    return out.getvalue()


def state(ws):
    """Everything a restore could change."""
    s = StateStore(ws / "data" / "state.db")
    try:
        alerts = [a["price"] for a in s.price_alerts()]
    finally:
        s.close()
    return {"config": (ws / "config.yaml").read_text(), "alerts": alerts,
            "strategies": {p.name: p.read_text() for p in sorted((ws / "strategies").glob("*.y*ml"))}}


def refused(client, ws, blob, status=422, says=""):
    before = state(ws)
    r = client.post("/api/restore", content=blob, headers=H)
    assert r.status_code == status, r.text
    assert says in r.json()["detail"]
    assert state(ws) == before
    return r


def test_corrupt_archives_are_refused(client, ws):
    good = zipped({"strategies/new.yaml": STRATEGY})
    refused(client, ws, good[: len(good) // 2], says="not a zip")
    refused(client, ws, b"PK\x03\x04" + b"\0" * 40, says="not a zip")
    refused(client, ws, zipped({"config.yaml": "poll_minutes: 15\n"}, readme=False), says="isn't a Trading Alerts backup")
    # a flipped byte inside a file fails its checksum
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.writestr(README, "backup")
        z.writestr("strategies/new.yaml", STRATEGY)
    blob = bytearray(out.getvalue())
    at = blob.index(b"name: Old")
    blob[at] ^= 0xFF
    refused(client, ws, bytes(blob), says="damaged")


def test_size_claims_are_not_trusted(client, ws, monkeypatch):
    # the header says 100 bytes; the file is 5 MB of the same letter
    blob = bytearray(zipped({"strategies/bomb.yaml": "a" * 5_000_000}))
    central = blob.rindex(b"strategies/bomb.yaml") - 46  # its entry in the zip's directory
    assert blob[central:central + 4] == b"PK\x01\x02"
    struct.pack_into("<I", blob, central + 24, 100)
    refused(client, ws, bytes(blob), says="damaged")
    refused(client, ws, zipped({"strategies/big.yaml": "a: " + "b" * 300_000}), says="too big to be a strategy")
    monkeypatch.setattr(backup_routes, "MAX_UNPACKED", 1000)
    refused(client, ws, zipped({"strategies/new.yaml": "name: x\n" * 500}), status=413, says="unpacks to more")


def test_malformed_yaml_and_unexpected_layouts(client, ws):
    refused(client, ws, zipped({"strategies/new.yaml": "name: [unclosed"}), says="damaged")
    refused(client, ws, zipped({"strategies/new.yaml": "- just\n- a list\n"}), says="isn't laid out")
    refused(client, ws, zipped({"config.yaml": "- not\n- settings\n"}), says="isn't laid out")
    refused(client, ws, zipped({"config.yaml": "poll_minutes: 15\nnot_a_setting: 1\n"}), says="can't be used")
    refused(client, ws, zipped({"config.yaml": "poll_minutes: -3\n"}), says="can't be used")
    refused(client, ws, zipped({"strategies/new.yaml": STRATEGY, "scripts/run.sh": "rm -rf /"}), says="Unexpected file")


def test_duplicate_entries_are_refused(client, ws):
    blob = zipped([("strategies/new.yaml", STRATEGY), ("strategies/new.yaml", "name: Sneaky\n")])
    refused(client, ws, blob, says="more than once")


def _db(path, build) -> bytes:
    p = path
    con = sqlite3.connect(p)
    build(con)
    con.commit()
    con.close()
    return p.read_bytes()


def test_databases_with_an_unexpected_layout_change_nothing(client, ws, tmp_path):
    blob = zipped({"strategies/new.yaml": STRATEGY,
                   "data/state.db": _db(tmp_path / "a.db", lambda c: c.execute("CREATE TABLE notes (x)"))})
    refused(client, ws, blob, says="isn't from Trading Alerts")

    def null_market(c):
        c.execute("CREATE TABLE runs (id INTEGER)")
        c.execute("CREATE TABLE paper_trades (id INTEGER)")
        c.execute("CREATE TABLE price_alerts (id INTEGER, market TEXT, direction TEXT, price REAL, created_at TEXT)")
        c.execute("INSERT INTO price_alerts VALUES (1, NULL, 'above', 1.0, '2026-01-01')")
    blob = zipped({"strategies/new.yaml": STRATEGY, "data/state.db": _db(tmp_path / "b.db", null_market)})
    refused(client, ws, blob, says="isn't laid out")
    refused(client, ws, zipped({"data/state.db": b"SQLite format 3\0" + b"\x01" * 200}), says="damaged")


def test_a_restore_that_stops_part_way_puts_everything_back(client, ws, monkeypatch):
    blob = client.get("/api/backup").content  # gold alert at 2700, strategy old.yaml
    client.post("/api/price-alerts", json={"market": "gold", "direction": "below", "price": 2500},
                headers={"X-Requested-With": "tradealerts"})
    (ws / "strategies" / "old.yaml").unlink()
    (ws / "strategies" / "newer.yaml").write_text(STRATEGY.replace("Old", "Newer"))
    (ws / "config.yaml").write_text("poll_minutes: 30\nnotifiers:\n  - type: console\n")

    def disk_full(self):
        raise OSError("No space left on device")
    with monkeypatch.context() as m:
        m.setattr(Workspace, "migrate", disk_full)
        r = refused(client, ws, blob, status=500, says="put back")
        assert "No space left" in r.json()["detail"]

    real = StateStore.replace_from
    calls = []

    def fails_second_time(self, src):
        calls.append(src)
        if len(calls) == 2:  # the first is the check on a scratch copy; the second is the real one
            raise sqlite3.OperationalError("database is locked")
        return real(self, src)
    with monkeypatch.context() as m:
        m.setattr(StateStore, "replace_from", fails_second_time)
        refused(client, ws, blob, status=500, says="put back")
    assert len(calls) == 2

    # and with nothing in the way, it goes through
    r = client.post("/api/restore", content=blob, headers=H)
    assert r.status_code == 200, r.text
    after = state(ws)
    assert after["alerts"] == [2700] and set(after["strategies"]) == {"old.yaml"} and "poll_minutes: 15" in after["config"]
