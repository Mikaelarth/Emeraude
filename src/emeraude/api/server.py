"""HTTP server bridging the Vue/Vuetify WebView to the Python core.

Architecture (cf. ADR-0004) :

* La WebView Android charge ``http://127.0.0.1:8765/`` au boot.
* Ce serveur sert :
    - ``GET  /``                  index.html (Vue 3 + Vuetify SPA)
    - ``GET  /static/<path>``     assets statiques (JS, CSS, fonts)
    - ``GET    /api/dashboard``       :class:`DashboardSnapshot` -> JSON
    - ``GET    /api/journal``         :class:`JournalSnapshot`  -> JSON
    - ``GET    /api/config``          :class:`ConfigSnapshot`   -> JSON
    - ``GET    /api/credentials``     :class:`BinanceCredentialsStatus` -> JSON
    - ``GET    /api/learning``        :class:`LearningSnapshot` -> JSON
    - ``GET    /api/performance``     :class:`PerformanceSnapshot` -> JSON
    - ``POST   /api/toggle-mode``     ``{"mode": ...}`` -> :class:`ConfigSnapshot`
    - ``POST   /api/credentials``     ``{"api_key", "api_secret"}`` -> status
    - ``POST   /api/emergency-stop``  -> ``{state}`` (freeze breaker, audit)
    - ``POST   /api/emergency-reset`` -> ``{state}`` (reset breaker, audit)
    - ``POST   /api/run-cycle``       -> ``{ok, summary}`` (trigger cycle, iter #95)
    - ``DELETE /api/credentials``     -> updated status (idempotent)

* Iter #78 a livré la route ``/api/dashboard`` ; iter #79 ajoute
  ``/api/journal`` + ``/api/config`` (lecture seule) ; iter #80 ajoute
  la première mutation : ``POST /api/toggle-mode`` ; iter #81 ajoute
  la saisie des clés API Binance (``GET/POST/DELETE /api/credentials``) ;
  iter #82 ajoute l'arrêt d'urgence (``POST /api/emergency-stop`` /
  ``POST /api/emergency-reset``) ; iter #83 ajoute la lecture de
  l'apprentissage (``GET /api/learning`` : strategy posteriors +
  champion) ; iter #84 ajoute le rapport R12 (``GET /api/performance``
  : 12 métriques sur les positions réellement fermées). À noter :
  le critère P1.5 "Backtest UI sur historique" reste 🔴 — l'engine
  simulateur kline -> position n'existe pas encore, anti-règle A1.

Sécurité loopback
=================

Le serveur écoute sur ``127.0.0.1`` uniquement (jamais sur
``0.0.0.0``). Les requêtes API nécessitent un **token aléatoire**
généré au démarrage et passé à la WebView via un cookie ``HttpOnly``
(initialisé par ``GET /``). Une autre app malveillante sur le device
qui essaierait de fetch ``localhost:8765/api/dashboard`` ne peut pas
forger ce cookie et reçoit un 403.

Note iter #78quater : Android 9+ refuse le HTTP cleartext dans la
WebView par défaut. La tentative iter #78ter de patcher le manifest
via ``android.extra_manifest_application_arguments`` a cassé Gradle
ManifestMerger sans message d'erreur exploitable. Trois solutions
sont possibles pour une prochaine iter :

1. Java helper ``TrustingWebViewClient.java`` compilé via p4a, qui
   override ``onReceivedSslError`` ; et ce serveur passe en HTTPS
   avec un cert auto-signé bundlé.
2. NetworkSecurityConfig XML resource + manifest patch via une
   autre voie (TBD).
3. JavaScript bridge (``addJavascriptInterface``) pour appeler le
   coeur Python directement depuis JS, court-circuitant HTTP.

Iter #78quater livre uniquement le revert du manifest fix cassé ;
la WebView Android affichera ``ERR_CLEARTEXT_NOT_PERMITTED`` jusqu'à
ce qu'on tackle le sujet en iter dédié.

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
    from collections.abc import Callable
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

#: Hard cap on POST body size. Our mutation payloads are tiny
#: (``{"mode": "real"}`` ~= 20 bytes) ; 4 KB leaves head-room for the
#: future API-key payload and rejects oversized bodies as DoS attempts.
_MAX_BODY_BYTES: Final[int] = 4096

#: Audit event type for ``set_mode`` triggered by the API. Mirrors the
#: ``"<DOMAIN>_<ACTION>"`` convention used elsewhere (cf.
#: ``POSITION_OPENED``, ``MICROSTRUCTURE_GATE``, etc.).
_AUDIT_MODE_CHANGED: Final[str] = "MODE_CHANGED"

#: Audit event types for the credentials lifecycle. The payload never
#: includes the API key or secret value — only the suffix (last 4
#: chars) and persistence flags. ``CLEARED`` events never carry a
#: suffix (we wouldn't have one to expose).
_AUDIT_CREDENTIALS_SAVED: Final[str] = "CREDENTIALS_SAVED"
_AUDIT_CREDENTIALS_CLEARED: Final[str] = "CREDENTIALS_CLEARED"

#: Audit event types for the emergency stop / reset. Distinct from the
#: ``CIRCUIT_BREAKER_STATE_CHANGE`` event emitted by ``circuit_breaker``
#: itself : the latter is a technical state transition log, the former
#: is an explicit user-decision marker (queryable as "show me when the
#: user pulled the plug" without false positives from automated trips).
_AUDIT_EMERGENCY_STOP: Final[str] = "EMERGENCY_STOP"
_AUDIT_EMERGENCY_RESET: Final[str] = "EMERGENCY_RESET"

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


#: Read-only API routes : ``route -> AppContext -> payload``. The
#: ``_serve_api`` dispatcher looks up the handler here and serialises
#: the result. Adding a new GET endpoint is one line. POST / DELETE
#: keep their explicit handlers because they emit audits + parse bodies.
_GET_API_HANDLERS: Final[dict[str, Callable[[AppContext], Any]]] = {
    "dashboard": lambda ctx: ctx.dashboard_data_source.fetch_snapshot(),
    "journal": lambda ctx: ctx.journal_data_source.fetch_snapshot(),
    "config": lambda ctx: ctx.config_data_source.fetch_snapshot(),
    "credentials": lambda ctx: ctx.binance_credentials_service.get_status(),
    "learning": lambda ctx: ctx.learning_data_source.fetch_snapshot(),
    "performance": lambda ctx: ctx.performance_data_source.fetch_snapshot(),
    "scheduler": lambda ctx: ctx.cycle_scheduler.fetch_snapshot(),
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

    # ─── POST dispatcher ────────────────────────────────────────────────────

    def do_POST(self) -> None:
        """Dispatch ``POST`` (method name fixed by stdlib API)."""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.startswith("/api/"):
            self._serve_api_post(path[len("/api/") :])
            return

        self._send_text(HTTPStatus.NOT_FOUND, "Not found")

    # ─── DELETE dispatcher ──────────────────────────────────────────────────

    def do_DELETE(self) -> None:
        """Dispatch ``DELETE`` (method name fixed by stdlib API)."""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.startswith("/api/"):
            self._serve_api_delete(path[len("/api/") :])
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
        """Dispatch ``/api/<route>``. Requires the auth cookie.

        Read routes (no body, no side effect) are looked up in a dict
        of ``route -> callable(AppContext) -> payload`` so adding a new
        GET endpoint is one line. POST / DELETE keep their explicit
        if-chain because each handler has its own pre/post-conditions
        (audit emit, body parsing, error mapping).
        """
        if not self._auth_ok():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return

        handler = _GET_API_HANDLERS.get(route)
        if handler is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})
            return
        self._send_json(HTTPStatus.OK, _serialise(handler(self.app_context)))

    # ─── POST API routes ────────────────────────────────────────────────────

    def _serve_api_post(self, route: str) -> None:
        """Dispatch ``POST /api/<route>``. Requires the auth cookie.

        Mutations are gated by the same ``HttpOnly`` cookie as ``GET``
        — there is no separate CSRF token for the loopback case (the
        cookie is unforgeable across origins). Future iter #81 may add
        an explicit anti-CSRF header if we ever expose beyond loopback.
        """
        if not self._auth_ok():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return

        # Dispatch table : route -> bound handler. Keeps the dispatcher
        # under the PLR0911 cap and makes adding a route a one-line
        # change. Each handler owns its own body parsing + audit emit.
        handlers: dict[str, Callable[[], None]] = {
            "toggle-mode": self._handle_toggle_mode,
            "credentials": self._handle_save_credentials,
            "emergency-stop": self._handle_emergency_stop,
            "emergency-reset": self._handle_emergency_reset,
            "run-cycle": self._handle_run_cycle,
            "scheduler": self._handle_scheduler_update,
        }
        handler = handlers.get(route)
        if handler is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})
            return
        handler()

    # ─── DELETE API routes ──────────────────────────────────────────────────

    def _serve_api_delete(self, route: str) -> None:
        """Dispatch ``DELETE /api/<route>``. Requires the auth cookie."""
        if not self._auth_ok():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return

        if route == "credentials":
            self._handle_clear_credentials()
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})

    def _handle_toggle_mode(self) -> None:
        """Persist the new mode (anti-règle A5 — UI must double-tap).

        Note : the 5-second double-tap protection is enforced **côté
        UI** (cf. ``index.html`` ``v-dialog`` countdown). The server
        accepts any well-formed call ; the audit trail records the
        change so a misuse via direct API call is observable. Defense
        in depth would also enforce a server-side delay, but the only
        attack surface here is loopback + cookie-gated, so the UI
        gate is sufficient at this stage.
        """
        # ``noqa: PLC0415`` : local imports keep the module importable
        # without ``infra.audit`` side effects in test-only contexts
        # that don't construct a server (``test_api_server.TestSerialise``).
        from emeraude.infra import audit  # noqa: PLC0415
        from emeraude.services.config_types import is_valid_mode  # noqa: PLC0415

        body = self._read_json_object()
        if body is None:
            return  # _read_json_object already sent the error response.

        mode = body.get("mode")
        if not isinstance(mode, str) or not is_valid_mode(mode):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid mode"})
            return

        previous = self.app_context.config_data_source.fetch_snapshot().mode
        self.app_context.config_data_source.set_mode(mode)
        audit.audit(
            _AUDIT_MODE_CHANGED,
            {"from": previous, "to": mode, "source": "api"},
        )
        snapshot = self.app_context.config_data_source.fetch_snapshot()
        self._send_json(HTTPStatus.OK, _serialise(snapshot))

    def _handle_save_credentials(self) -> None:
        """Persist Binance API credentials (encrypted via PBKDF2+XOR).

        Service errors map to HTTP codes :

        * :class:`PassphraseUnavailableError` (env var
          ``EMERAUDE_API_PASSPHRASE`` absent) -> 503 Service
          Unavailable. Honest signal that the server is not
          configured to accept credentials right now ; UI can show
          the env-var hint without offering save.
        * :class:`CredentialFormatError` (bad key shape) -> 400 Bad
          Request with the validator's message reused (already user
          friendly, in French, mirroring the doc 02 spec).

        Audits ``CREDENTIALS_SAVED`` with the suffix on success — the
        plaintext key never reaches the audit log (would defeat
        encryption-at-rest).
        """
        # Local imports : the ``services`` package is heavier than the
        # GET routes need ; defer until POST actually fires.
        from emeraude.infra import audit  # noqa: PLC0415
        from emeraude.services.binance_credentials import (  # noqa: PLC0415
            CredentialFormatError,
            PassphraseUnavailableError,
        )

        body = self._read_json_object()
        if body is None:
            return

        api_key = body.get("api_key")
        api_secret = body.get("api_secret")
        if not isinstance(api_key, str) or not isinstance(api_secret, str):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "api_key and api_secret are required strings"},
            )
            return

        service = self.app_context.binance_credentials_service
        try:
            service.save_credentials(api_key=api_key, api_secret=api_secret)
        except CredentialFormatError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except PassphraseUnavailableError as exc:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
            return

        status = service.get_status()
        audit.audit(
            _AUDIT_CREDENTIALS_SAVED,
            {"api_key_suffix": status.api_key_suffix, "source": "api"},
        )
        self._send_json(HTTPStatus.OK, _serialise(status))

    def _handle_clear_credentials(self) -> None:
        """Wipe both stored credentials (idempotent).

        Always emits ``CREDENTIALS_CLEARED`` so multiple back-to-back
        clears from the UI are still observable in the audit trail.
        Returns the updated status (both flags False, suffix None).
        """
        from emeraude.infra import audit  # noqa: PLC0415

        service = self.app_context.binance_credentials_service
        service.clear_credentials()
        status = service.get_status()
        audit.audit(_AUDIT_CREDENTIALS_CLEARED, {"source": "api"})
        self._send_json(HTTPStatus.OK, _serialise(status))

    def _handle_emergency_stop(self) -> None:
        """Freeze the Circuit Breaker (manual emergency stop).

        FROZEN is the strongest non-recoverable state : only an explicit
        :func:`circuit_breaker.reset` (= the matching emergency-reset
        endpoint) clears it. The bot trade path checks
        :func:`circuit_breaker.is_trade_allowed` and refuses to enter
        new positions while frozen.

        No request body required — the action is unambiguous. The
        previous state is captured for the audit payload so
        post-mortem can tell whether the user froze a bot that was
        already in trouble (TRIGGERED) or hit the panic button on
        a healthy bot.
        """
        # Local imports : ``circuit_breaker`` pulls ``infra.audit`` and
        # SQLite via ``infra.database`` — defer until POST actually fires.
        from emeraude.agent.execution import circuit_breaker  # noqa: PLC0415
        from emeraude.infra import audit  # noqa: PLC0415

        previous = circuit_breaker.get_state()
        circuit_breaker.freeze(reason="emergency_stop:user")
        new_state = circuit_breaker.get_state()
        audit.audit(
            _AUDIT_EMERGENCY_STOP,
            {"from": previous.value, "to": new_state.value, "source": "api"},
        )
        self._send_json(HTTPStatus.OK, {"state": new_state.value})

    def _handle_emergency_reset(self) -> None:
        """Reset the Circuit Breaker to HEALTHY (admin operation).

        This unfreezes a previously frozen breaker — but does **not**
        re-activate real-money trading on its own. The mode is unchanged
        ; if the user wants to go back to real trading, they must still
        toggle the mode (which goes through the A5 5-second countdown).

        Idempotent : resetting an already-healthy breaker is a no-op
        but still emits the audit event so the user-decision is
        observable in the trail.
        """
        from emeraude.agent.execution import circuit_breaker  # noqa: PLC0415
        from emeraude.infra import audit  # noqa: PLC0415

        previous = circuit_breaker.get_state()
        circuit_breaker.reset(reason="emergency_reset:user")
        new_state = circuit_breaker.get_state()
        audit.audit(
            _AUDIT_EMERGENCY_RESET,
            {"from": previous.value, "to": new_state.value, "source": "api"},
        )
        self._send_json(HTTPStatus.OK, {"state": new_state.value})

    def _handle_run_cycle(self) -> None:
        """Trigger one :meth:`AutoTrader.run_cycle` invocation (iter #95).

        This is the manual cycle trigger exposed to the UI : the user
        taps "Lancer un cycle" on the Dashboard, the SPA POSTs here,
        and the bot performs one full pipeline pass (fetch klines /
        price -> data-quality guard -> tick -> breaker / drift /
        risk monitors -> decision -> maybe-open). The
        :class:`CycleReport` summary is returned so the UI can show
        immediate feedback without polling.

        Errors :

        * :class:`OSError` / :class:`urllib.error.URLError` from the
          network fetchers -> 502 Bad Gateway with the upstream message.
        * Any other unexpected exception -> 500 Internal Server Error
          with the exception message. We do NOT swallow exceptions
          silently (anti-règle A8).

        Returns a compact JSON summary :

        .. code-block:: json

            {
              "ok": true,
              "summary": {
                "symbol": "BTCUSDT",
                "mode": "paper",
                "should_trade": false,
                "skip_reason": "ensemble_not_qualified",
                "data_quality_rejected": false,
                "opened_position_id": null,
                "tick_outcome_id": null
              }
            }
        """
        # Local import : keeps the URLError class scoped to where it
        # is caught and avoids paying the cost on cycles that don't
        # error (the vast majority).
        import urllib.error  # noqa: PLC0415

        try:
            report = self.app_context.auto_trader.run_cycle()
        except (OSError, urllib.error.URLError) as exc:
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "ok": False,
                    "error": f"upstream fetch failed : {exc}",
                },
            )
            return
        except Exception as exc:  # noqa: BLE001  (last-resort fallback)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": f"cycle failed : {type(exc).__name__}: {exc}",
                },
            )
            return

        # Compact summary — the full :class:`CycleReport` is heavy
        # (nested dataclasses with Decimal trade levels etc.). The UI
        # just needs enough to render the result toast and refresh
        # the dashboard.
        summary = {
            "symbol": report.symbol,
            "interval": report.interval,
            "fetched_at": report.fetched_at,
            "mode": report.decision.breaker_state.value,
            "should_trade": report.decision.should_trade,
            "skip_reason": report.decision.skip_reason,
            "data_quality_rejected": report.data_quality_rejected,
            "data_quality_rejection_reason": report.data_quality_rejection_reason,
            "opened_position_id": (
                report.opened_position.id if report.opened_position is not None else None
            ),
            "tick_outcome_id": (
                report.tick_outcome.id if report.tick_outcome is not None else None
            ),
        }
        self._send_json(HTTPStatus.OK, {"ok": True, "summary": summary})

    def _handle_scheduler_update(self) -> None:
        """Update ``scheduler.enabled`` / ``scheduler.interval_seconds``.

        Body : ``{"enabled": bool, "interval_seconds": int}`` — both
        fields optional. Returns the updated :class:`SchedulerSnapshot`
        so the UI doesn't need a second GET.

        Validation errors (``interval_seconds`` out of range) -> 400
        with the validator message. Anti-règle A8 : invalid data is
        rejected, never silently coerced.
        """
        from emeraude.services.cycle_scheduler import (  # noqa: PLC0415
            set_scheduler_enabled,
            set_scheduler_interval_seconds,
        )

        body = self._read_json_object()
        if body is None:
            return

        enabled = body.get("enabled")
        interval_seconds = body.get("interval_seconds")

        if enabled is not None and not isinstance(enabled, bool):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "enabled must be a boolean"},
            )
            return
        if interval_seconds is not None and not isinstance(interval_seconds, int):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "interval_seconds must be an integer"},
            )
            return

        if enabled is not None:
            set_scheduler_enabled(enabled)
        if interval_seconds is not None:
            try:
                set_scheduler_interval_seconds(interval_seconds)
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": str(exc)},
                )
                return

        snapshot = self.app_context.cycle_scheduler.fetch_snapshot()
        self._send_json(HTTPStatus.OK, _serialise(snapshot))

    def _read_json_object(self) -> dict[str, Any] | None:
        """Parse a JSON object from the POST body.

        On any error, this method sends the appropriate error response
        itself and returns ``None``. The caller should bail early when
        the result is ``None``.

        Returns the parsed dict on success.
        """
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
            return None
        if length <= 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing body"})
            return None
        if length > _MAX_BODY_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "body too large"},
            )
            return None
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return None
        if not isinstance(body, dict):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "body must be a JSON object"},
            )
            return None
        return body

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
