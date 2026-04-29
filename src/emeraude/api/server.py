"""HTTP server bridging the Vue/Vuetify WebView to the Python core.

Architecture (cf. ADR-0004) :

* La WebView Android charge ``http://127.0.0.1:8765/`` au boot.
* Ce serveur sert :
    - ``GET /``                   index.html (Vue 3 + Vuetify SPA)
    - ``GET /static/<path>``      assets statiques (JS, CSS, fonts)
    - ``GET /api/dashboard``      :class:`DashboardSnapshot` -> JSON

* Iter #78 livre la route ``/api/dashboard`` ; ``/api/journal``,
  ``/api/config`` et les ``POST`` de mutation (toggle mode, save
  credentials) viennent en iter #79/#80.

Sécurité loopback
=================

Le serveur écoute sur ``127.0.0.1`` uniquement (jamais sur
``0.0.0.0``). Les requêtes API nécessitent un **token aléatoire**
généré au démarrage et passé à la WebView via un cookie ``HttpOnly``
(initialisé par ``GET /``). Une autre app malveillante sur le device
qui essaierait de fetch ``localhost:8765/api/dashboard`` ne peut pas
forger ce cookie et reçoit un 403.

Pas de TLS — on est sur loopback, le risque MITM est nul.

Pas de FastAPI / Flask — :mod:`http.server` stdlib suffit largement
pour notre besoin (3-5 endpoints, GET principalement, JSON en sortie).
Zéro nouvelle dépendance Python à pinner pour Buildozer.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Final, cast
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from pathlib import Path

    from emeraude.api.context import AppContext

_LOGGER = logging.getLogger(__name__)

#: Default loopback bind address — never expose to network.
DEFAULT_HOST: Final[str] = "127.0.0.1"

#: Default port — chosen high to avoid clashes with system services.
DEFAULT_PORT: Final[int] = 8765

#: Auth cookie name (HttpOnly).
AUTH_COOKIE: Final[str] = "emeraude_auth"

#: HTTP request timeout (seconds) — keeps the server responsive even
#: if a malformed client hangs.
REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0

#: MIME types per file extension. Limited intentionally — only file
#: types we actually ship in ``web/``.
_MIME_TYPES: Final[dict[str, str]] = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}


def _serialise(value: Any) -> Any:
    """JSON-friendly recursive transform for our snapshot dataclasses.

    Rules :
    * :class:`Decimal` -> ``str`` (preserves precision ; JSON ``number``
      would lose decimals on the JS side).
    * Dataclasses -> dict (via ``asdict``) recursively serialised.
    * Tuples / lists -> list (each element serialised).
    * dict -> dict (each value serialised).
    * Everything else passes through (str, int, float, bool, None).
    """
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _serialise(asdict(value))
    if isinstance(value, (tuple, list)):
        return [_serialise(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialise(val) for key, val in value.items()}
    return value


class EmeraudeHTTPServer(ThreadingHTTPServer):
    """:class:`ThreadingHTTPServer` enriched with our :class:`AppContext`.

    Multi-threaded so the WebView can issue concurrent requests
    (e.g. fetch dashboard + journal in parallel). The PythonActivity
    Android thread keeps the Python interpreter alive ; the server
    runs in a daemon worker thread.
    """

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        context: AppContext,
        web_root: Path,
        auth_token: str,
    ) -> None:
        super().__init__(server_address, _RequestHandler)
        self.app_context = context
        self.web_root = web_root
        self.auth_token = auth_token


def create_server(
    *,
    context: AppContext,
    web_root: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> tuple[EmeraudeHTTPServer, str]:
    """Build the HTTP server (without starting its thread).

    Caller is responsible for invoking :meth:`EmeraudeHTTPServer.serve_forever`
    or scheduling it via :func:`serve_in_thread`. Splitting the
    construction from the start lets the desktop entry point block on
    ``serve_forever`` in the main thread while the Android entry point
    spawns a background thread and keeps Kivy's event loop in main.

    Args:
        context: composed :class:`AppContext`.
        web_root: directory containing ``index.html`` and assets.
        host: bind address. Defaults to loopback ; never override on
            production builds.
        port: TCP port. Defaults to :data:`DEFAULT_PORT`.

    Returns:
        ``(server, auth_token)`` :

        * ``server`` : the :class:`EmeraudeHTTPServer` instance, ready
          to serve.
        * ``auth_token`` : the random token used by the auth cookie.
    """
    auth_token = secrets.token_urlsafe(32)
    server = EmeraudeHTTPServer(
        server_address=(host, port),
        context=context,
        web_root=web_root,
        auth_token=auth_token,
    )
    _LOGGER.info("Emeraude HTTP server bound to http://%s:%d/", host, port)
    return server, auth_token


def serve_in_thread(server: EmeraudeHTTPServer) -> threading.Thread:
    """Start the server's ``serve_forever`` loop in a daemon thread.

    Used by the Android entry point so Kivy keeps the main Python
    thread (its event loop is the only thing that keeps the app
    process alive on Android).
    """
    thread = threading.Thread(
        target=server.serve_forever,
        name="emeraude-http",
        daemon=True,
    )
    thread.start()
    return thread


class _RequestHandler(BaseHTTPRequestHandler):
    """Dispatcher for routes ``/``, ``/static/*``, ``/api/*``.

    Note : the BaseHTTPRequestHandler default logging goes to stderr
    line-by-line — verbose in production. We override ``log_message``
    to route through Python's :mod:`logging` (which the audit /
    crash logger pipeline can capture).
    """

    timeout = REQUEST_TIMEOUT_SECONDS
    server_version = "Emeraude/0.0"

    # ─── Logging override ───────────────────────────────────────────────────

    def log_message(self, format: str, *args: Any) -> None:
        """Route stdlib's noisy stderr logs through :mod:`logging`.

        Signature (parameter named ``format``) is inherited from
        :class:`http.server.BaseHTTPRequestHandler`.
        """
        _LOGGER.debug("%s - %s", self.address_string(), format % args)

    # ─── Properties to access server state without casting noise ────────────

    @property
    def emeraude_server(self) -> EmeraudeHTTPServer:
        """Typed accessor for ``self.server``."""
        return cast("EmeraudeHTTPServer", self.server)

    @property
    def app_context(self) -> AppContext:
        """The :class:`AppContext` shared across requests."""
        return self.emeraude_server.app_context

    @property
    def web_root(self) -> Path:
        """The ``web/`` directory containing the SPA assets."""
        return self.emeraude_server.web_root

    # ─── GET dispatcher ─────────────────────────────────────────────────────

    def do_GET(self) -> None:
        """Dispatch ``GET`` (method name fixed by stdlib API)."""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/":
            self._serve_index()
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
            return
        if path.startswith("/api/"):
            self._serve_api(path[len("/api/") :])
            return

        self._send_text(HTTPStatus.NOT_FOUND, "Not found")

    # ─── Index : sets the auth cookie and serves the SPA ────────────────────

    def _serve_index(self) -> None:
        """Serve ``index.html`` and set the auth cookie."""
        index_path = self.web_root / "index.html"
        if not index_path.is_file():
            self._send_text(HTTPStatus.NOT_FOUND, "index.html not found")
            return

        body = index_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Auth cookie : HttpOnly + SameSite=Strict + path=/. The cookie
        # is set on every / fetch ; the WebView caches it for API calls.
        cookie = (
            f"{AUTH_COOKIE}={self.emeraude_server.auth_token}; HttpOnly; SameSite=Strict; Path=/"
        )
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ─── Static files ───────────────────────────────────────────────────────

    def _serve_static(self, relative: str) -> None:
        """Serve a static file from ``web/static/<relative>``.

        Path traversal protection : the resolved file must be under
        ``web/static/``. Anything escaping is a 403.
        """
        static_root = self.web_root / "static"
        try:
            target = (static_root / relative).resolve()
            if not target.is_relative_to(static_root.resolve()):
                self._send_text(HTTPStatus.FORBIDDEN, "Forbidden")
                return
        except (OSError, ValueError):
            self._send_text(HTTPStatus.BAD_REQUEST, "Bad path")
            return

        if not target.is_file():
            self._send_text(HTTPStatus.NOT_FOUND, "Not found")
            return

        suffix = target.suffix.lower()
        mime = _MIME_TYPES.get(suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    # ─── API routes ─────────────────────────────────────────────────────────

    def _serve_api(self, route: str) -> None:
        """Dispatch ``/api/<route>``. Requires the auth cookie."""
        if not self._auth_ok():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return

        if route == "dashboard":
            snapshot = self.app_context.dashboard_data_source.fetch_snapshot()
            self._send_json(HTTPStatus.OK, _serialise(snapshot))
            return

        # Iter #79 will add 'journal' and 'config'.
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _auth_ok(self) -> bool:
        """Check the auth cookie matches the server's random token.

        Constant-time comparison (``secrets.compare_digest``) to
        defeat timing oracles, even though the attack surface is
        loopback-only.
        """
        cookie_header = self.headers.get("Cookie", "")
        for raw_fragment in cookie_header.split(";"):
            fragment = raw_fragment.strip()
            if "=" not in fragment:
                continue
            key, _, value = fragment.partition("=")
            if key == AUTH_COOKIE:
                return secrets.compare_digest(value, self.emeraude_server.auth_token)
        return False

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        """Serialise ``payload`` to JSON and send."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        """Send a small text response — used for error paths only."""
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
