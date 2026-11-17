"""B4: security hardening (sign-in throttle races, password-less copies behind a proxy, owner-only
secret files, file-based keyrings) and the CI security jobs."""
import os
import stat
import threading
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tradealerts import keychain
from tradealerts.web import app as app_mod
from tradealerts.web.app import NO_PASSWORD_BEHIND_PROXY, create_app
from tradealerts.web.auth import LoginThrottle
from tradealerts.workspace import Workspace, write_private

H = {"X-Requested-With": "tradealerts"}
ROOT = Path(__file__).resolve().parents[2]
posix_only = pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "config.yaml").write_text("notifiers: []\n")
    return tmp_path / "config.yaml"


def _all_at_once(fn, n=30):
    """Call fn from n threads released together; returns the results."""
    gate, out = threading.Barrier(n), []
    lock = threading.Lock()

    def go():
        gate.wait()
        r = fn()
        with lock:
            out.append(r)
    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


# --------------------------------------------------------------------------- sign-in throttle


@pytest.mark.parametrize("on_disk", [False, True])
def test_parallel_guesses_cant_skip_the_wait(tmp_path, on_disk):
    t = LoginThrottle(free_tries=5, db_path=tmp_path / "s.db" if on_disk else None)
    results = _all_at_once(lambda: t.attempt("1.2.3.4"))
    assert sum(1 for w in results if w == 0) == 5  # only the free tries reach the password check
    assert t.locked_for("1.2.3.4") > 0


def test_attempt_matches_the_old_one_at_a_time_behaviour():
    t = LoginThrottle(free_tries=3)
    assert [t.attempt("ip") for _ in range(3)] == [0, 0, 0]
    assert 0 < t.attempt("ip") <= 30  # third wrong one locks for 30 s; nothing more is counted
    t.ok("ip")
    assert t.attempt("ip") == 0


def test_a_locked_key_blocks_without_counting_the_other(tmp_path):
    t = LoginThrottle(free_tries=1, db_path=tmp_path / "s.db")
    t.attempt("email:a")            # locks email:a
    assert t.attempt("ip", "email:a") > 0
    assert t.locked_for("ip") == 0
    other = LoginThrottle(free_tries=1, db_path=tmp_path / "s.db")  # a second process sees it too
    assert other.attempt("email:a") > 0


def test_login_counts_before_checking_and_a_right_password_clears(cfg):
    c = TestClient(create_app(cfg, password="right"))
    for _ in range(4):
        assert c.post("/api/login", json={"password": "wrong"}, headers=H).status_code == 401
    assert c.post("/api/login", json={"password": "right"}, headers=H).status_code == 200
    for _ in range(5):
        c.post("/api/login", json={"password": "wrong"}, headers=H)
    r = c.post("/api/login", json={"password": "right"}, headers=H)
    assert r.status_code == 429 and "Try again" in r.json()["detail"]


# --------------------------------------------------------------------------- no password behind a proxy


@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Real-IP", "Forwarded", "X-Forwarded-Host"])
def test_password_less_copy_refuses_proxied_requests(cfg, header):
    c = TestClient(create_app(cfg, refuse_proxied=True))
    assert c.get("/api/session").status_code == 200  # this computer, directly
    r = c.get("/api/strategies", headers={header: "203.0.113.9"})
    assert r.status_code == 403 and r.json()["detail"] == NO_PASSWORD_BEHIND_PROXY
    assert c.post("/api/login", json={}, headers={**H, header: "x"}).status_code == 403


def test_proxied_requests_still_work_with_a_password_or_no_auth(cfg):
    proxied = {"X-Forwarded-For": "203.0.113.9"}
    with_pw = TestClient(create_app(cfg, password="pw", refuse_proxied=True))
    assert with_pw.get("/api/strategies", headers=proxied).status_code == 401  # asks to sign in
    no_auth = TestClient(create_app(cfg, refuse_proxied=False))  # --no-auth: the proxy signs people in
    assert no_auth.get("/api/strategies", headers=proxied).status_code == 200


@pytest.mark.parametrize("no_auth", [False, True])
def test_serve_turns_the_proxy_check_on_unless_no_auth(cfg, monkeypatch, no_auth):
    seen = {}

    class Stop(Exception):
        pass

    def fake_create_app(*a, **kw):
        seen.update(kw)
        raise Stop

    class Sock:
        def getsockname(self):
            return ("127.0.0.1", 8321)

    monkeypatch.delenv("TRADEALERTS_PASSWORD", raising=False)
    monkeypatch.setattr(app_mod, "bind_socket", lambda *a, **k: Sock())
    monkeypatch.setattr(app_mod, "create_app", fake_create_app)
    with pytest.raises(Stop):
        app_mod.serve(str(cfg), "127.0.0.1", 8321, run_live=False, no_auth=no_auth)
    assert seen["refuse_proxied"] is (not no_auth)


# --------------------------------------------------------------------------- owner-only files


@posix_only
def test_settings_file_is_owner_only(cfg):
    os.chmod(cfg, 0o644)  # written by an older version
    ws = Workspace(cfg)
    ws.write_config({"notifiers": [{"type": "telegram", "token": "123:abc", "chat_id": "1"}]})
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert yaml.safe_load(cfg.read_text())["notifiers"][0]["token"] == "123:abc"


@posix_only
def test_session_secret_and_restore_safety_copy_are_owner_only(cfg):
    c = TestClient(create_app(cfg, password="pw"))
    assert stat.S_IMODE((cfg.parent / "data" / ".session_secret").stat().st_mode) == 0o600
    c.post("/api/login", json={"password": "pw"}, headers=H)
    with Workspace(cfg).store():
        pass  # a full database to back up
    r = c.post("/api/restore", content=c.get("/api/backup").content, headers={**H, "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    copies = list((cfg.parent / "data" / "backups").glob("before-restore-*.zip"))
    assert copies and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in copies)


def test_write_private_replaces_contents(tmp_path):
    p = tmp_path / "f"
    write_private(p, b"long first version")
    write_private(p, b"short")
    assert p.read_bytes() == b"short"


# --------------------------------------------------------------------------- keyrings that are really files


def _kr(module, name, priority=1.0):
    return type(name, (), {"priority": priority, "__module__": module})()


@pytest.mark.parametrize("store,usable", [
    (_kr("keyrings.alt.file", "PlaintextKeyring", 0.5), False),
    (_kr("keyrings.alt.file", "EncryptedKeyring", 0.6), False),
    (_kr("keyring.backends.SecretService", "Keyring", 5), True),
])
def test_file_based_keyrings_dont_count_as_a_password_store(monkeypatch, store, usable):
    keyring = pytest.importorskip("keyring")
    monkeypatch.setattr(keychain, "_backend", None)
    monkeypatch.setattr(keychain, "_checked", False)
    monkeypatch.setattr(keyring, "get_keyring", lambda: store)
    assert (keychain.backend() is not None) is usable


def test_a_chain_of_stores_is_judged_by_the_one_it_uses():
    alt, real = _kr("keyrings.alt.file", "PlaintextKeyring"), _kr("keyring.backends.macOS", "Keyring")
    assert keychain._file_store(type("Chainer", (), {"backends": [alt, real]})())
    assert not keychain._file_store(type("Chainer", (), {"backends": [real, alt]})())


# --------------------------------------------------------------------------- CI security jobs


@pytest.mark.skipif(not (ROOT / ".github").is_dir(), reason="needs the git checkout")
def test_security_workflow_runs_pip_audit_and_secret_scanning():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    audit = " ".join(s.get("run", "") for s in jobs["pip-audit"]["steps"])
    assert "pip-audit -r" in audit and "[yahoo,stocks,parquet,keychain]" in audit
    leaks = jobs["secrets"]["steps"]
    assert leaks[0]["uses"].startswith("actions/checkout") and leaks[0]["with"]["fetch-depth"] == 0
    run = " ".join(s.get("run", "") for s in leaks)
    assert "sha256sum -c" in run and "gitleaks git" in run and "--exit-code 1" in run
    assert True in wf and "schedule" in wf[True]  # "on:" reads as True in YAML 1.1
    # gitleaks-action needs a paid licence for organisations; the CLI doesn't
    for p in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert "gitleaks/gitleaks-action" not in p.read_text(encoding="utf-8"), p.name


def test_self_hosting_guide_is_linked_and_covers_the_basics():
    guide = (ROOT / "docs" / "self-hosting.md").read_text(encoding="utf-8")
    for needed in ("reverse_proxy", "proxy_pass", "TRADEALERTS_PASSWORD", "ufw", "FORWARDED_ALLOW_IPS"):
        assert needed in guide, needed
    assert "(self-hosting.md)" in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "self-hosting.md" in (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
