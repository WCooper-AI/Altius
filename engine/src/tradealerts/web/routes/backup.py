"""Backup and restore: everything in one zip, to move between computers, servers and the hosted service."""

from __future__ import annotations

import io
import logging
import os
import re
import sqlite3
import tempfile
import time
import zipfile
import zlib
from pathlib import Path

import pandas as pd
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ... import __version__, keychain
from ...config import PATH_KEYS, ConfigError, load_config, uses_env
from ...state import StateStore
from ...strategy import strategy_files
from ...workspace import CONFIG_HEADER, Workspace, write_private
from ..common import err, workspace
from ..strategy_io import enforce_running_limit

router = APIRouter()
log = logging.getLogger(__name__)

MAX_BACKUP = 200 * 1024 * 1024
MAX_UNPACKED = 600 * 1024 * 1024
MAX_STRATEGY = 200_000
STRATEGY_NAME = re.compile(r"^strategies/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.ya?ml$")
README = "trading-alerts-backup.txt"

ENV_ONLY = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
REDACTED_NOTE = ("# Shareable copy: passwords, API tokens, webhook addresses and ntfy topics were removed.\n"
                 "# Fill them in again in Settings after restoring.\n")


def _blank(value):
    if isinstance(value, str) and ENV_ONLY.match(value):
        return value  # names a variable on the server, not the secret itself
    return {} if isinstance(value, dict) else ""


def redact_config(value):
    """A copy of config.yaml's settings with passwords, tokens and private addresses blanked."""
    if isinstance(value, list):
        return [redact_config(v) for v in value]
    if not isinstance(value, dict):
        return value
    out, blanked = {}, False
    for k, v in value.items():
        if keychain.is_secret(k, value.get("type", "")):
            out[k] = _blank(v) if v not in (None, "") else v
            blanked = blanked or out[k] != v
        else:
            out[k] = redact_config(v)
    if blanked and "type" in value:
        out["enabled"] = False  # a notifier without its token would only fail; switched on again in Settings
    return out


def make_backup(ws: Workspace, redact: bool = False) -> bytes:
    c = ws.config()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(README,
                   f"Trading Alerts {__version__} backup, made {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC.\n"
                   + ("Restore it from Settings, Backup. This is a shareable copy: passwords, API tokens and "
                      "webhook addresses were removed. It still contains your strategies, forward tests, "
                      "journal, alerts and notes.\n" if redact else
                      "Restore it from Settings, Backup. It contains your settings (including alert and API tokens), "
                      "strategies, forward tests, journal, alerts and notes. Keep it private.\n"))
        if ws.config_path.exists():
            if redact:
                try:
                    raw = yaml.safe_load(ws.config_path.read_text(encoding="utf-8")) or {}
                except yaml.YAMLError:
                    raw = {}  # never fall back to copying a file we couldn't clean
                z.writestr("config.yaml", REDACTED_NOTE + yaml.safe_dump(redact_config(raw), sort_keys=False,
                                                                         allow_unicode=True))
            elif ws.trusted and keychain.placeholders(raw := ws.raw_config()):
                # the secrets themselves, so the backup restores on another computer
                z.writestr("config.yaml", CONFIG_HEADER + yaml.safe_dump(keychain.reveal(raw), sort_keys=False,
                                                                         allow_unicode=True))
            else:
                z.write(ws.config_path, "config.yaml")
        for p in strategy_files(c.strategies_dir).values():
            z.write(p, f"strategies/{p.name}")
        if c.state_db.exists():
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "state.db"
                src, dst = sqlite3.connect(c.state_db), sqlite3.connect(copy)
                try:
                    src.backup(dst)  # consistent copy even while the checker is running
                    if redact:
                        dst.execute("DROP TABLE IF EXISTS login_attempts")  # sign-in addresses
                        dst.commit()
                        dst.execute("VACUUM")
                finally:
                    dst.close()
                    src.close()
                z.write(copy, "data/state.db")
        if ws.trusted:  # the hosted service's news calendar is shared, not the customer's
            ev = c.data_dir / "news_events.csv"
            if ev.exists():
                z.write(ev, "data/news_events.csv")
    return buf.getvalue()


@router.get("/api/backup")
def backup(redact: bool = False, ws: Workspace = Depends(workspace)):
    kind = "shareable-backup" if redact else "backup"
    name = f"trading-alerts-{kind}-{pd.Timestamp.now():%Y-%m-%d}.zip"
    data = make_backup(ws, redact=redact)
    ok, problem = verify_backup(ws, data)
    if ok:
        with ws.store() as store:
            store.set_value("last_backup_at", pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"))
    else:
        log.warning("A backup was made but didn't read back cleanly: %s", problem)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _settings_from_backup(ws: Workspace, raw: dict) -> dict:
    """A backup's settings, made safe for this workspace."""
    if not isinstance(raw, dict):
        err(422, "config.yaml in the backup is damaged")
    raw = keychain.drop_placeholders(raw)  # a backup carries secrets, never password-store references
    if ws.trusted:
        return raw
    out = {k: v for k, v in raw.items() if k not in PATH_KEYS and k != "log_level"}
    # Values read from the server's environment (${...}) only made sense on the old server.
    out["notifiers"] = [n for n in (out.get("notifiers") or []) if isinstance(n, dict) and not uses_env(n)
                        and (ws.limits.console_notifier or str(n.get("type")) != "console")]
    data = out.get("data") if isinstance(out.get("data"), dict) else {}
    if uses_env(data) or data.get("source") not in ws.limits.sources:
        data = {"source": ws.limits.sources[0]}
    out["data"] = data
    poll = out.get("poll_minutes", 15)
    if isinstance(poll, bool) or not isinstance(poll, (int, float)) or poll < ws.limits.min_poll_minutes:
        out["poll_minutes"] = max(15, ws.limits.min_poll_minutes)
    return out


def _member(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """One file from the backup. A file whose header lies about its size fails its
    checksum (zipfile stops at the stated size), so it is reported as damaged."""
    try:
        return z.read(info)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, OSError):
        err(422, f"{info.filename} in the backup is damaged")


def _yaml_dict(name: str, b: bytes) -> dict:
    try:
        data = yaml.safe_load(b.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        err(422, f"{name} in the backup is damaged")
    if data is None and name == "config.yaml":
        return {}
    if not isinstance(data, dict):
        err(422, f"{name} in the backup isn't laid out the way Trading Alerts writes it")
    return data


def _check_settings(ws: Workspace, settings: dict) -> None:
    """Load the backup's settings the way the app will, before anything is written."""
    folder = ws.config_path.parent
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".restore-check-", suffix=".yaml", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(settings, f, sort_keys=False, allow_unicode=True)
        load_config(name, must_exist=True, trusted=ws.trusted, cache_dir=ws.cache_dir,
                    min_poll_minutes=ws.limits.min_poll_minutes)
    except ConfigError as exc:
        err(422, f"The settings in the backup can't be used: {exc}")
    finally:
        Path(name).unlink(missing_ok=True)


def _check_db(path: Path, staging: Path) -> None:
    """Copy the backup's rows into a fresh database of this version first, so a damaged
    file or an unexpected layout is found before the real one is touched."""
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.DatabaseError:
        ok = False
    if not ok:
        err(422, "The database in the backup is damaged")
    store = StateStore(staging)
    try:
        store.replace_from(path)
    except ValueError as exc:
        err(422, str(exc))
    except sqlite3.Error as exc:
        err(422, f"The database in the backup isn't laid out the way Trading Alerts writes it ({exc})")
    finally:
        store.close()


def read_backup(ws: Workspace, raw: bytes, tmp: Path) -> dict:
    """Check every part of a backup without changing anything. Returns what to restore."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        infos = z.infolist()
    except (zipfile.BadZipFile, zlib.error, EOFError, ValueError, OSError):
        err(422, "That isn't a Trading Alerts backup (not a zip file)")
    names = [i.filename for i in infos]
    if README not in names:
        err(422, "That zip isn't a Trading Alerts backup")
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        err(422, f"The backup has {twice[0]} in it more than once, so it can't be trusted")
    if sum(i.file_size for i in infos) > MAX_UNPACKED:
        err(413, "That backup unpacks to more than this server accepts")
    allowed = {README, "config.yaml", "data/state.db", "data/news_events.csv"}
    for n in names:
        if not n.endswith("/") and n not in allowed and not STRATEGY_NAME.match(n):
            err(422, f"Unexpected file in the backup: {n}")
    out = {"strategies": {}, "settings": None, "db": None, "news": None}
    for info in infos:
        n = info.filename
        if STRATEGY_NAME.match(n):
            if info.file_size > MAX_STRATEGY:
                err(422, f"{n} in the backup is too big to be a strategy")
            b = _member(z, info)
            _yaml_dict(n, b)
            out["strategies"][Path(n).name] = b
        elif n == "config.yaml":
            out["settings"] = _settings_from_backup(ws, _yaml_dict(n, _member(z, info)))
            _check_settings(ws, out["settings"])
        elif n == "data/state.db":
            out["db"] = tmp / "state.db"
            out["db"].write_bytes(_member(z, info))
            _check_db(out["db"], tmp / "staged.db")
        elif n == "data/news_events.csv" and ws.trusted:
            out["news"] = _member(z, info)
        elif n == README:
            _member(z, info)
    limit = ws.limits.max_strategies
    if limit is not None and len(out["strategies"]) > limit:
        err(422, f"The backup has {len(out['strategies'])} strategies; this plan allows {limit}.")
    return out


def verify_backup(ws: Workspace, raw: bytes) -> tuple[bool, str | None]:
    """Read a backup back the way a restore would, without changing anything: (ok, problem).
    Used right after a backup is made (so Settings and ``GET /api/health`` can show when one
    last worked) and by ``tradealerts verify-backup``."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            read_backup(ws, raw, Path(tmp))
        except HTTPException as exc:
            return False, str(exc.detail)
    return True, None


def _snapshot(ws: Workspace) -> dict:
    c = ws.config()
    news = c.data_dir / "news_events.csv"
    return {"config": ws.config_path.read_bytes() if ws.config_path.exists() else None,
            "strategies": {p: p.read_bytes() for p in strategy_files(c.strategies_dir).values()},
            "news": news.read_bytes() if news.exists() else None, "news_path": news}


def _put_back(ws: Workspace, before: dict, written: list[Path]) -> None:
    """Undo a restore that failed part-way: the old settings, strategies and news file."""
    if before["config"] is None:
        ws.config_path.unlink(missing_ok=True)
    else:
        tmp = ws.config_path.with_name(ws.config_path.name + ".tmp")
        tmp.write_bytes(before["config"])
        tmp.replace(ws.config_path)
    for p in written:
        p.unlink(missing_ok=True)
    for p in strategy_files(ws.config().strategies_dir).values():
        if p not in before["strategies"]:
            p.unlink(missing_ok=True)
    for p, b in before["strategies"].items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
    news = before["news_path"]
    if before["news"] is None:
        news.unlink(missing_ok=True)
    else:
        news.write_bytes(before["news"])


def apply_backup(ws: Workspace, b: dict) -> None:
    """Swap in a checked backup. If any step fails, everything goes back as it was: the
    files are put back from a copy taken first, and the database is replaced last, in one
    transaction, so it is either all the backup's rows or all the old ones."""
    before = _snapshot(ws)
    written: list[Path] = []
    try:
        if b["settings"] is not None:
            ws.write_config(b["settings"])
        c = ws.config()
        c.strategies_dir.mkdir(parents=True, exist_ok=True)
        for p in strategy_files(c.strategies_dir).values():
            p.unlink()
        for name, data in b["strategies"].items():
            p = c.strategies_dir / name
            written.append(p)
            p.write_bytes(data)
        ws.migrate()  # backups made before 0.2
        if b["news"] is not None:
            c.data_dir.mkdir(parents=True, exist_ok=True)
            written.append(c.data_dir / "news_events.csv")
            (c.data_dir / "news_events.csv").write_bytes(b["news"])
        if b["db"] is not None:
            with ws.store() as store:  # rows go into this version's own tables
                store.replace_from(b["db"])
    except BaseException as exc:
        try:
            _put_back(ws, before, written)
        except Exception:  # noqa: BLE001 - the safety copy is still there
            log.exception("restore: could not put the old files back")
            raise
        if isinstance(exc, HTTPException):
            raise
        log.exception("restore failed part-way; the old files were put back")
        err(500, f"The restore stopped part-way ({exc}), so everything was put back as it was.")


@router.post("/api/restore")
async def restore(request: Request, ws: Workspace = Depends(workspace)):
    """Replace everything with a backup. Every part is checked first, the current state is
    saved, and a failure part-way puts everything back."""
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BACKUP:
            err(413, "That backup is too big")
    with tempfile.TemporaryDirectory() as tmp:
        b = read_backup(ws, bytes(raw), Path(tmp))
        # safety copy of what's there now
        keep = ws.config().data_dir / "backups"
        keep.mkdir(parents=True, exist_ok=True)
        for old in sorted(keep.glob("before-restore-*.zip"))[:-4]:  # keep the last five
            old.unlink(missing_ok=True)
        safety = keep / f"before-restore-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        write_private(safety, make_backup(ws))  # holds the secrets too
        apply_backup(ws, b)
    if not ws.trusted:
        # Strategies this service can't run (e.g. a CSV path or price source from the self-hosted
        # app) come in as drafts, so they're never checked live until fixed.
        from ...strategy import read_raw
        from ..strategy_io import validate, write
        for p in strategy_files(ws.config().strategies_dir).values():
            data = read_raw(p)
            try:
                validate(ws, data)
            except HTTPException:
                if data.get("template") is not True:
                    data["template"] = True
                    write(p, data)
    stopped = enforce_running_limit(ws)
    return {"restored": True, "strategies": len(b["strategies"]), "stopped_over_limit": stopped,
            "safety_copy": str(safety) if ws.trusted else safety.name}
