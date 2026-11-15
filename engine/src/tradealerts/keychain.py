"""Passwords, tokens and private addresses kept in the computer's password store instead of
config.yaml (self-hosted and desktop editions).

config.yaml then holds a placeholder such as ``keychain:notifiers.telegram.token.3f9a2c1b``; the
value itself lives in Windows Credential Manager, the macOS Keychain or the Linux Secret Service.
Needs the optional ``keyring`` package (``pip install "tradealerts[keychain]"``, bundled with the
desktop app). Without it, or when the store can't be reached, secrets stay in config.yaml as before.

Only trusted workspaces use this: the hosted edition never reads or writes a password store.
"""

from __future__ import annotations

import copy
import logging
import secrets

log = logging.getLogger(__name__)

PREFIX = "keychain:"
SERVICE = "Altius"

# Settings that hold a password, API token or private address (any level of config.yaml).
SECRET_KEYS = {"token", "password", "secret", "api_key", "webhook_url", "oanda_token", "headers"}
# Notifier settings that are themselves the secret: a webhook's address, an ntfy topic.
SECRET_NOTIFIER_KEYS = {"webhook": {"url"}, "ntfy": {"topic"}}

_backend = None        # the keyring module, or a stand-in set by tests
_checked = False
STORE_NAMES = {"WinVaultKeyring": "Windows Credential Manager", "macOS": "the macOS Keychain",
               "SecretService": "the system keyring (Secret Service)", "kwallet": "KWallet"}


def is_secret(key, kind: str = "") -> bool:
    """Whether a setting called ``key`` (inside a notifier of type ``kind``) holds a secret."""
    key = str(key).lower()
    return (key in SECRET_KEYS or key in SECRET_NOTIFIER_KEYS.get(str(kind).lower(), set())
            or key.endswith(("_token", "_password", "_secret")))


def is_placeholder(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def backend():
    """The password store, or None when there isn't a usable one."""
    global _backend, _checked
    if _backend is None and not _checked:
        _checked = True
        try:
            import keyring
            from keyring.backends import fail
            kr = keyring.get_keyring()
            if not isinstance(kr, fail.Keyring) and kr.priority > 0:
                _backend = keyring
        except Exception as exc:  # missing package, or the store can't be reached
            log.debug("No password store: %s", exc)
    return _backend


def store_name() -> str | None:
    """The password store in plain words, for Settings; None when secrets stay in config.yaml."""
    b = backend()
    if b is None:
        return None
    try:
        kr = b.get_keyring()
        key = type(kr).__name__ if type(kr).__name__ in STORE_NAMES else type(kr).__module__.rsplit(".", 1)[-1]
        return STORE_NAMES.get(key, getattr(kr, "name", None) or "the system password store")
    except Exception:
        return "the system password store"


# --------------------------------------------------------------------- walking config.yaml

def _replace(value, fn):
    """A copy of the settings with each secret string passed through ``fn(path, text)``."""
    def go(v, path, kind):
        if isinstance(v, list):
            return [go(x, (*path, str(x.get("type", i)).lower() if isinstance(x, dict) else i), kind)
                    for i, x in enumerate(v)]
        if not isinstance(v, dict):
            return v
        kind = str(v.get("type", kind))
        out = {}
        for k, x in v.items():
            if is_secret(k, kind) and isinstance(x, str):
                out[k] = fn((*path, str(k)), x)
            elif is_secret(k, kind) and isinstance(x, dict):
                out[k] = {hk: fn((*path, str(k), str(hk)), hv) if isinstance(hv, str) else hv for hk, hv in x.items()}
            else:
                out[k] = go(x, (*path, str(k)), kind)
        return out
    return go(copy.deepcopy(value), (), "")


def placeholders(value) -> dict:
    """{path: placeholder} for every secret already moved to the password store."""
    found = {}

    def note(path, text):
        if is_placeholder(text):
            found[path] = text
        return text
    _replace(value, note)
    return found


# --------------------------------------------------------------------- reading and writing

def reveal(value):
    """The settings with each placeholder swapped for the stored secret ("" if it can't be read)."""
    if not placeholders(value):
        return value
    b = backend()

    def get(path, text):
        if not is_placeholder(text):
            return text
        try:
            got = b.get_password(SERVICE, text[len(PREFIX):]) if b else None
        except Exception as exc:
            log.warning("Couldn't read %s from the password store: %s", ".".join(map(str, path)), exc)
            got = None
        if got is None:
            log.warning("%s isn't in the password store; enter it again in Settings", ".".join(map(str, path)))
        return got or ""
    return _replace(value, get)


def stash(new: dict, old: dict | None = None) -> dict:
    """``new`` with each secret moved to the password store, leaving a placeholder.

    A secret that was already stored at the same place keeps its placeholder. Stored secrets the
    new settings no longer use are deleted. With no usable store, ``new`` comes back unchanged."""
    b = backend()
    if b is None:
        return new
    before = placeholders(old or {})
    from .config import uses_env

    def put(path, text):
        if not text or is_placeholder(text) or uses_env(text):
            return text  # blank, already stored, or the name of an environment variable
        ref = before.get(path) or PREFIX + ".".join(map(str, path)) + "." + secrets.token_hex(4)
        b.set_password(SERVICE, ref[len(PREFIX):], text)
        return ref
    try:
        out = _replace(new, put)
    except Exception as exc:
        log.warning("Couldn't use the password store, so secrets stay in config.yaml: %s", exc)
        return new
    still = set(placeholders(out).values())
    for ref in set(before.values()) - still:
        try:
            b.delete_password(SERVICE, ref[len(PREFIX):])
        except Exception:
            pass  # already gone
    return out


def drop_placeholders(value):
    """Blank any placeholder (a backup carries the secrets themselves, never references)."""
    return _replace(value, lambda path, text: "" if is_placeholder(text) else text)
