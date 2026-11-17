"""A workspace is one person's Trading Alerts: settings, strategies, live state and data.

The self-hosted edition has exactly one, in the folder you run it from (or the desktop
app's data folder). The hosted edition has one per customer, under the server's data
folder, and loads them with ``trusted=False`` so a customer's settings can't read the
server's environment or point outside their own folder.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import AppConfig, ConfigError, load_config, read_raw
from .state import StateStore

def write_private(path: Path, data: bytes) -> None:
    """Write a file only its owner can read (on Windows the user's folders already are)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if os.name == "posix":
        os.chmod(path, 0o600)  # an older file keeps its mode through O_CREAT


CONFIG_HEADER = "# Trading Alerts settings. Edited in the web interface; you can also edit by hand.\n"


@dataclass
class Limits:
    """What one workspace may do. The self-hosted edition has no limits."""

    max_strategies: int | None = None
    max_running: int | None = None        # forward tests running at once (alerts start one too)
    max_price_alerts: int | None = None
    max_uploads: int | None = None
    max_upload_mb: float = 50
    max_versions: int | None = None       # saved versions kept per strategy (None: all)
    min_poll_minutes: float = 1
    sources: tuple[str, ...] = ("dukascopy", "yahoo", "oanda")
    console_notifier: bool = True           # "print alerts in the server log" makes no sense on a shared server
    csv_paths: bool = True                  # free-text CSV paths (hosted: uploaded files only)

    def public(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@dataclass
class Workspace:
    config_path: Path
    owner: str | None = None          # customer id in the hosted edition
    trusted: bool = True
    cache_dir: Path | None = None
    limits: Limits = field(default_factory=Limits)
    read_only: bool = False           # hosted: subscription lapsed
    keychain: bool = False            # keep secrets in the computer's password store (keychain.py)

    def __post_init__(self):
        self.config_path = Path(self.config_path)

    @property
    def key(self) -> str:
        return self.owner or str(self.config_path.resolve())

    # ------------------------------------------------------------------ settings

    def config(self) -> AppConfig:
        return load_config(self.config_path, must_exist=False, trusted=self.trusted, cache_dir=self.cache_dir,
                           min_poll_minutes=self.limits.min_poll_minutes)

    def raw_config(self) -> dict:
        return read_raw(self.config_path)

    def write_config(self, new: dict) -> None:
        if not self.trusted:
            from .config import PATH_KEYS
            new = {k: v for k, v in new.items() if k not in PATH_KEYS}
        elif self.keychain:
            from . import keychain
            new = keychain.stash(new, self.raw_config())
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_name(self.config_path.name + ".tmp")
        # Owner-only: without a password store, alert tokens and passwords are in this file.
        write_private(tmp, (CONFIG_HEADER + yaml.safe_dump(new, sort_keys=False, allow_unicode=True)).encode("utf-8"))
        tmp.replace(self.config_path)

    def ensure(self) -> None:
        """Create the folders a fresh workspace needs."""
        c = self.config()
        for d in (c.strategies_dir, c.data_dir):
            d.mkdir(parents=True, exist_ok=True)
        if not self.trusted:
            c.uploads_dir.mkdir(parents=True, exist_ok=True)

    def migrate(self) -> list[str]:
        """Bring strategy files saved by older versions up to date. Returns the ids changed."""
        from .strategy import migrate_strategy_files

        return migrate_strategy_files(self.config().strategies_dir)

    # ------------------------------------------------------------------ state

    @contextlib.contextmanager
    def store(self, create: bool = True):
        """The workspace's SQLite store, closed afterwards. With ``create=False`` yields
        None when nothing has been stored yet, instead of creating an empty database."""
        db = self.config().state_db
        if not create and not db.exists():
            yield None
            return
        s = StateStore(db)
        try:
            yield s
        finally:
            s.close()


def safe_config(cfg_path: Path) -> AppConfig | None:
    """The config, or None if it can't be read (for background loops that must keep going)."""
    try:
        return load_config(cfg_path, must_exist=False)
    except ConfigError:
        return None
