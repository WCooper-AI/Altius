"""A6b: the interface as ES modules, and secrets kept in the computer's password store."""
import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tradealerts import keychain
from tradealerts.cli import build_parser
from tradealerts.config import load_config
from tradealerts.web import create_app
from tradealerts.web.routes import backup as backup_routes
from tradealerts.workspace import Workspace

STATIC = Path(__file__).resolve().parents[1] / "src" / "tradealerts" / "static"
STUB = Path(__file__).parent / "js" / "browser_stub.mjs"
H = {"X-Requested-With": "tradealerts"}
IMPORT = re.compile(r'^import \{([^}]*)\} from "([^"]+)";', re.M)
EXPORT = re.compile(r'^export (?:async )?(?:function\*? |class |const |let |var )([\w$]+)', re.M)
MODULES = sorted(p.name for p in STATIC.glob("*.js") if p.name not in ("theme.js", "report.js"))


# --------------------------------------------------------------------- ES modules

def test_page_loads_boot_as_a_module(tmp_path):
    client = TestClient(create_app(tmp_path / "config.yaml"))
    html = client.get("/").text
    assert re.search(r'<script type="module" src="/static/boot\.js\?v=[0-9a-f]+"></script>', html)
    assert " defer>" not in html  # no classic scripts sharing one scope


def test_module_imports_follow_the_page_version(tmp_path):
    client = TestClient(create_app(tmp_path / "config.yaml"))
    r = client.get("/static/strategies.js?v=abc123")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert 'from "./util.js?v=abc123";' in r.text and 'from "./util.js";' not in r.text
    plain = client.get("/static/strategies.js").text
    assert 'from "./util.js";' in plain
    odd = client.get("/static/strategies.js?v=%22%3E%3Cx").text  # only a plain version string is copied
    assert 'from "./util.js";' in odd
    assert client.get("/static/nope.js?v=abc").status_code == 404


def test_every_import_names_an_export():
    exports = {name: set(EXPORT.findall((STATIC / name).read_text(encoding="utf-8"))) for name in MODULES}
    for name in MODULES:
        for names, src in IMPORT.findall((STATIC / name).read_text(encoding="utf-8")):
            target = src.removeprefix("./")
            assert target in exports, f"{name} imports missing {src}"
            missing = {n.strip() for n in names.split(",") if n.strip()} - exports[target]
            assert not missing, f"{name} imports {missing} that {target} doesn't export"


def test_util_imports_nothing_so_it_loads_first():
    assert not IMPORT.search((STATIC / "util.js").read_text(encoding="utf-8"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("entry", ["boot.js", "editor.js", "settings.js"])
def test_modules_load_in_any_order(entry):
    # Linking fails on a missing export, and code that runs at load time fails on a module
    # that hasn't run yet, whichever module the browser (or a test) starts from.
    script = ("import { pathToFileURL } from 'node:url';\n"
              "const [dir, stub, entry] = process.argv.slice(1);\n"
              "await import(pathToFileURL(stub).href);\n"
              "await import(pathToFileURL(dir + '/' + entry).href);\n"
              "console.log('ok'); process.exit(0);\n")
    out = subprocess.run(["node", "--input-type=module", "-e", script, "--", str(STATIC), str(STUB), entry],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr


# --------------------------------------------------------------------- password store

class WinVaultKeyring:  # named like keyring's Windows backend, for store_name()
    priority = 5


class FakeStore:
    def __init__(self):
        self.items = {}

    def get_keyring(self):
        return WinVaultKeyring()

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[(service, name)] = value

    def delete_password(self, service, name):
        del self.items[(service, name)]


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(keychain, "_backend", fake)
    monkeypatch.setattr(keychain, "_checked", True)
    return fake


@pytest.fixture
def no_store(monkeypatch):
    monkeypatch.setattr(keychain, "_backend", None)
    monkeypatch.setattr(keychain, "_checked", True)


RAW = {
    "poll_minutes": 15,
    "notifiers": [
        {"type": "telegram", "token": "123:abc", "chat_id": "42"},
        {"type": "ntfy", "server": "https://ntfy.sh", "topic": "my-private-topic", "token": ""},
        {"type": "webhook", "url": "https://hooks.example/x", "headers": {"Authorization": "Bearer zz"}},
        {"type": "email", "host": "smtp.example", "password": "${SMTP_PASSWORD}"},
    ],
    "data": {"source": "oanda", "oanda_token": "oa-1", "oanda_environment": "practice"},
}


def test_stash_moves_each_secret_and_reveal_brings_it_back(store):
    out = keychain.stash(RAW)
    tg, ntfy, hook, email = out["notifiers"]
    assert keychain.is_placeholder(tg["token"]) and tg["chat_id"] == "42"
    assert keychain.is_placeholder(ntfy["topic"]) and ntfy["token"] == "" and ntfy["server"] == "https://ntfy.sh"
    assert keychain.is_placeholder(hook["url"]) and keychain.is_placeholder(hook["headers"]["Authorization"])
    assert email["password"] == "${SMTP_PASSWORD}"  # names a variable; not itself the secret
    assert keychain.is_placeholder(out["data"]["oanda_token"]) and out["data"]["source"] == "oanda"
    assert tg["token"].startswith("keychain:notifiers.telegram.token.")
    assert "123:abc" not in yaml.safe_dump(out) and len(store.items) == 5
    assert keychain.reveal(out) == RAW


def test_stash_keeps_the_same_entry_and_deletes_unused_ones(store):
    first = keychain.stash(RAW)
    changed = json.loads(json.dumps(first))
    changed["notifiers"][0]["token"] = "999:new"
    changed["notifiers"] = changed["notifiers"][:2]  # webhook removed
    second = keychain.stash(changed, first)
    assert second["notifiers"][0]["token"] == first["notifiers"][0]["token"]
    assert keychain.reveal(second)["notifiers"][0]["token"] == "999:new"
    assert len(store.items) == 3  # telegram token, ntfy topic, OANDA token


def test_without_a_store_secrets_stay_in_the_file(no_store):
    assert keychain.stash(RAW) == RAW
    assert keychain.store_name() is None
    lost = {"data": {"oanda_token": "keychain:data.oanda_token.00000000"}}
    assert keychain.reveal(lost) == {"data": {"oanda_token": ""}}


def test_an_unreachable_store_falls_back_to_the_file(store, monkeypatch):
    def locked(*a):
        raise RuntimeError("collection is locked")
    monkeypatch.setattr(store, "set_password", locked)
    assert keychain.stash(RAW) == RAW


def test_config_reads_placeholders_only_when_trusted(store, tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "from-env")
    ws = Workspace(tmp_path / "config.yaml", keychain=True)
    ws.write_config(RAW)
    text = (tmp_path / "config.yaml").read_text()
    assert "123:abc" not in text and "oa-1" not in text and "keychain:" in text
    c = load_config(tmp_path / "config.yaml")
    assert c.notifiers[0]["token"] == "123:abc" and c.data["oanda_token"] == "oa-1"
    shared = load_config(tmp_path / "config.yaml", trusted=False)  # hosted: never reads a password store
    assert keychain.is_placeholder(shared.notifiers[0]["token"])
    hosted = Workspace(tmp_path / "other.yaml", trusted=False, keychain=True)
    hosted.write_config(RAW)
    assert "123:abc" in (tmp_path / "other.yaml").read_text()


def test_settings_page_saves_to_the_store(store, tmp_path):
    client = TestClient(create_app(tmp_path / "config.yaml", keychain=True))
    s = client.get("/api/settings").json()
    assert s["secrets_store"] == "Windows Credential Manager"
    s["notifiers"]["telegram"] = {"enabled": True, "token": "123:abc", "chat_id": "42"}
    s["notifiers"]["ntfy"] = {"enabled": True, "server": "https://ntfy.sh", "topic": "my-private-topic", "token": ""}
    r = client.put("/api/settings", json=s, headers=H)
    assert r.status_code == 200, r.text
    text = (tmp_path / "config.yaml").read_text()
    assert "123:abc" not in text and "my-private-topic" not in text
    shown = client.get("/api/settings").json()
    assert shown["notifiers"]["ntfy"]["topic"] == "my-private-topic"  # shown, as before
    assert shown["notifiers"]["telegram"]["token"] == "" and shown["notifiers"]["telegram"]["token_set"] is True
    before = dict(store.items)
    assert client.put("/api/settings", json=shown, headers=H).status_code == 200  # blank token keeps it
    assert store.items == before and (tmp_path / "config.yaml").read_text() == text
    assert client.get("/api/settings").json()["secrets_store"] == "Windows Credential Manager"


def test_settings_say_when_secrets_stay_in_the_file(no_store, tmp_path):
    client = TestClient(create_app(tmp_path / "config.yaml", keychain=True))
    assert client.get("/api/settings").json()["secrets_store"] is None


def test_full_backup_carries_the_secrets_and_restore_stores_them_again(store, tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "from-env")
    (tmp_path / "data").mkdir()
    ws = Workspace(tmp_path / "config.yaml", keychain=True)
    ws.write_config(RAW)
    with zipfile.ZipFile(io.BytesIO(backup_routes.make_backup(ws))) as z:
        saved = yaml.safe_load(z.read("config.yaml"))
    assert saved["notifiers"][0]["token"] == "123:abc" and "keychain:" not in json.dumps(saved)
    with zipfile.ZipFile(io.BytesIO(backup_routes.make_backup(ws, redact=True))) as z:
        shared = z.read("config.yaml").decode()
    assert "123:abc" not in shared and "keychain:" not in shared

    other = tmp_path / "other"
    other.mkdir()
    client = TestClient(create_app(other / "config.yaml", keychain=True))
    r = client.post("/api/restore", content=backup_routes.make_backup(ws),
                    headers={**H, "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    text = (other / "config.yaml").read_text()
    assert "123:abc" not in text and "keychain:" in text
    assert load_config(other / "config.yaml").notifiers[0]["token"] == "123:abc"


def test_restore_ignores_password_store_references(store, tmp_path):
    ws = Workspace(tmp_path / "config.yaml", keychain=True)
    raw = {"notifiers": [{"type": "webhook", "url": "https://hooks.example/x",
                          "headers": {"Authorization": "keychain:notifiers.telegram.token.1234abcd"}}]}
    out = backup_routes._settings_from_backup(ws, raw)
    assert out["notifiers"][0]["headers"]["Authorization"] == ""


def test_serve_has_a_switch_to_keep_secrets_in_the_file():
    assert build_parser().parse_args(["serve", "--no-keychain"]).no_keychain is True
    assert build_parser().parse_args(["serve"]).no_keychain is False
