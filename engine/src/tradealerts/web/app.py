"""The web interface: a JSON API plus a single-page app served from /static.

``build_app`` puts the shared routes together for an edition's context.
``create_app`` and ``serve`` are the self-hosted edition (``tradealerts serve``); the
hosted edition builds its own context in the tradealerts_cloud package.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import socket
import sys
import threading
import time
from importlib import resources
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..workspace import Workspace
from .auth import Auth
from .auth import router as auth_router
from .common import appearance_from_raw, err
from .models import plain_errors
from .context import AppContext, SelfHostContext
from .routes import backup, journal, markets, news, runs, settings, status, strategies, testing

log = logging.getLogger(__name__)

STATIC = resources.files("tradealerts") / "static"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
# The interface is ES modules with no build step: the page loads boot.js, which imports the rest.
ENTRY = "boot.js"
# Windows can map .js to text/plain in its registry; browsers refuse modules served that way.
mimetypes.add_type("text/javascript", ".js")
IMPORT_RE = re.compile(rb'(\bfrom\s*")(\.{1,2}/[\w./-]+\.js)(")')


class VersionedStatic(StaticFiles):
    """Static files. A module asked for as ``name.js?v=X`` imports its neighbours with ``?v=X`` too,
    so the whole interface follows the version in the page (the desktop window caches hard)."""

    async def get_response(self, path: str, scope) -> Response:
        resp = await super().get_response(path, scope)
        q = re.search(rb"(?:^|&)v=([\w-]{1,40})(?:&|$)", scope.get("query_string") or b"")
        if not (q and path.endswith(".js") and resp.status_code == 200 and hasattr(resp, "path")):
            return resp
        body = IMPORT_RE.sub(rb"\1\2?v=" + q.group(1) + rb"\3", Path(resp.path).read_bytes())
        keep = {k: v for k, v in resp.headers.items() if k not in ("content-length", "etag", "last-modified")}
        return Response(body, headers=keep, media_type="text/javascript")


def static_version(extra_dirs=()) -> str:
    """Changes whenever an installed file changes, so the desktop window (which caches hard)
    and browsers always pick up a new version after an update."""
    h = hashlib.sha1(__version__.encode())
    for d in (Path(str(STATIC)), *extra_dirs):
        for f in sorted(Path(d).iterdir()):
            if f.is_file():
                st = f.stat()
                h.update(f"{f.name}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:10]


# The API's version. Requests may name it (/api/v1/...) or not (/api/...); a change that
# breaks existing callers gets a new number, and the old prefix keeps working.
API_VERSION = "1"
API_V1 = "/api/v1/"


def build_app(ctx: AppContext, *, authorize: Callable[[Request], Response | None],
              extra_routers=(), allowed_hosts: set[str] | None = None, csrf_exempt: tuple[str, ...] = (),
              extra_static: dict[str, Path] | None = None) -> FastAPI:
    app = FastAPI(title="Trading Alerts", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.state.runner = ctx.runner  # older name
    app.state.shutdown = None      # older name for ctx.shutdown

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": plain_errors(exc.errors())}, 422)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.scope["path"]
        if path.startswith(API_V1):  # /api/v1/... is the same API as /api/...
            path = "/api/" + path[len(API_V1):]
            request.scope["path"], request.scope["raw_path"] = path, path.encode()
        if allowed_hosts is not None and request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "Unknown host"}, 421)
        if path.startswith("/api/"):
            if (request.method not in ("GET", "HEAD") and path not in csrf_exempt
                    and request.headers.get("x-requested-with") != "tradealerts"):
                return JSONResponse({"detail": "Request blocked: missing X-Requested-With header"}, 403)
            denied = authorize(request)
            if denied is not None:
                return denied
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = CSP
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["API-Version"] = API_VERSION
        elif path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # always check for a newer copy
        return response

    @app.get("/api/session")
    def session(request: Request):
        out = ctx.session(request)
        if out.get("signed_in"):
            ws = ctx.workspace(request)
            raw = ws.raw_config()
            out["appearance"] = appearance_from_raw(raw)
            out["appearance_saved"] = isinstance(raw.get("appearance"), dict)
        return out

    @app.post("/api/shutdown")
    def shutdown():
        """Used by the desktop app when you quit it, so the engine can finish cleanly."""
        stop = app.state.shutdown or ctx.shutdown
        if not ctx.desktop or not stop:
            err(404, "Not found")
        stop()
        return {"stopping": True}

    for r in (*extra_routers, strategies.router, runs.router, testing.router, markets.router, journal.router,
              news.router, settings.router, backup.router, status.router):
        app.include_router(r)

    extra_static = extra_static or {}
    scripts = [*ctx.extra_scripts, ENTRY]   # hosted extras (modules) register before boot.js starts the app

    @app.get("/", include_in_schema=False)
    def index():
        html = (Path(str(STATIC)) / "index.html").read_text(encoding="utf-8")
        v = static_version(extra_static.values())
        tags = "\n".join(f'<script type="module" src="/static/{s}?v={v}"></script>' for s in scripts)
        html = html.replace("<!-- scripts -->", tags)
        html = re.sub(r'(/static/[\w.-]+\.(?:js|css))(?=")', rf"\1?v={v}", html)
        return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    for prefix, folder in extra_static.items():
        app.mount(f"/static/{prefix}", VersionedStatic(directory=str(folder)), name=f"static-{prefix}")
    app.mount("/static", VersionedStatic(directory=str(STATIC)), name="static")
    return app


# --------------------------------------------------------------------------- self-hosted edition


def create_app(config_path: str | Path | None = None, password: str | None = None, runner=None,
               allowed_hosts: set[str] | None = None, desktop: bool = False, keychain: bool = False) -> FastAPI:
    from .. import news

    ws = Workspace(Path(config_path) if config_path else Path("config.yaml"), keychain=keychain)
    c = ws.config()
    for sid in ws.migrate():
        log.info("%s: updated to follow Settings, Price data", sid)
    news.set_data_dir(c.data_dir)
    auth = Auth(password, c.data_dir / ".session_secret", db_path=c.state_db)
    ctx = SelfHostContext(ws, auth, runner=runner, desktop=desktop)

    def authorize(request: Request):
        if request.url.path in ("/api/session", "/api/login") or ctx.signed_in(request):
            return None
        return JSONResponse({"detail": "Sign in to continue"}, 401)

    return build_app(ctx, authorize=authorize, extra_routers=(auth_router(auth),), allowed_hosts=allowed_hosts)


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        k = ctypes.windll.kernel32
        handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(handle, ctypes.byref(code))
        k.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def bind_socket(host: str, port: int, fallback: bool = False) -> socket.socket:
    """Bind the listening socket before starting uvicorn, so the real port is known up front.

    Port 0 lets the OS pick a free port. With ``fallback``, a port that's already taken falls
    back to one the OS picks instead of failing (the desktop app reads the real port from stdout).
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform != "win32":  # on Windows this would let two servers share a port
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        try:
            sock.bind((host, port))
        except OSError:
            if not (fallback and port):
                raise
            log.warning("Port %s is in use; letting the system pick a free one", port)
            sock.bind((host, 0))
    except OSError as e:
        sock.close()
        raise SystemExit(f"Couldn't listen on {host}:{port} ({e}). Try another --port, or --port 0.")
    sock.set_inheritable(True)
    return sock


def serve(config_path: str | None, host: str, port: int, run_live: bool = True, no_auth: bool = False,
          desktop: bool = False, parent_pid: int | None = None, keychain: bool = True) -> None:
    import uvicorn

    from ..runner import LiveRunner

    password = os.environ.get("TRADEALERTS_PASSWORD") or None
    if desktop:
        host, password = "127.0.0.1", None  # the desktop app only ever talks to this computer
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if no_auth:
        password = None
    elif not loopback and not password:
        raise SystemExit(
            f"Refusing to listen on {host} without a password. Set TRADEALERTS_PASSWORD, or pass "
            "--no-auth if a reverse proxy (e.g. Caddy basic auth) already protects it."
        )
    sock = bind_socket(host, port, fallback=desktop)
    port = sock.getsockname()[1]
    path = config_path or "config.yaml"
    runner = LiveRunner(Workspace(Path(path))).start() if run_live else None
    # On this computer only, refuse requests addressed to any other host name (DNS rebinding).
    hosts = {f"{n}:{port}" for n in ("127.0.0.1", "localhost", "[::1]")} if loopback else None
    app = create_app(path, password=password, runner=runner, allowed_hosts=hosts, desktop=desktop, keychain=keychain)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning",
                                           proxy_headers=not loopback))
    app.state.ctx.shutdown = lambda: setattr(server, "should_exit", True)
    if parent_pid:
        def watch():
            while not server.should_exit:
                if not _parent_alive(parent_pid):
                    log.warning("Desktop app closed; stopping")
                    server.should_exit = True
                    return
                time.sleep(2)
        threading.Thread(target=watch, name="parent-watch", daemon=True).start()
    shown = "localhost" if loopback else host
    print(f"TRADEALERTS_PORT={port}", flush=True)  # handshake read by the desktop app
    print(f"Trading Alerts is running at http://{shown}:{port}  (Ctrl+C to stop)", flush=True)
    if not run_live:
        print("Live checks are off (--no-runner).")
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
        if runner:
            runner.stop()
