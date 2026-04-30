"""Unit tests for the iter #78 HTTP API layer.

Cover :

* :func:`emeraude.api.server._serialise` — Decimal + dataclass JSON-friendly.
* :func:`emeraude.api.server.create_server` — wiring + auth token.
* End-to-end HTTP integration : spin a server in a thread, fetch
  ``GET /api/dashboard``, assert the JSON shape mirrors the Python
  :class:`DashboardSnapshot`.
* Auth cookie enforcement : a request without the cookie gets a 403.

These tests do **not** require a Kivy display (no L2 gating). They
construct :class:`AppContext` with a fake :class:`WalletService` so
the wiring doesn't touch SQLite or Binance.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from emeraude import web_app
from emeraude.api.context import AppContext
from emeraude.api.server import (
    AUTH_COOKIE,
    create_server,
    serve_in_thread,
)
from emeraude.api.server import _serialise as serialise
from emeraude.web_app import _is_android, _resolve_web_root

# ─── Fakes ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _FakePosition:
    side: str
    entry_price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class _FakeSnapshot:
    capital_quote: Decimal | None
    open_position: _FakePosition | None
    cumulative_pnl: Decimal
    n_closed_trades: int
    mode: str


# ─── _serialise ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSerialise:
    """``_serialise`` must produce JSON-encodable output."""

    def test_decimal_to_string(self) -> None:
        assert serialise(Decimal("20.50")) == "20.50"

    def test_decimal_preserves_precision(self) -> None:
        # The Python core uses Decimal precisely to avoid floating
        # point loss ; the JSON layer must not silently coerce to
        # float (which would lose digits past 15 decimal places).
        d = Decimal("0.123456789012345678901234")
        assert serialise(d) == "0.123456789012345678901234"

    def test_dataclass_recursive(self) -> None:
        snap = _FakeSnapshot(
            capital_quote=Decimal("20"),
            open_position=None,
            cumulative_pnl=Decimal("0"),
            n_closed_trades=0,
            mode="paper",
        )
        out = serialise(snap)
        assert isinstance(out, dict)
        assert out["capital_quote"] == "20"
        assert out["open_position"] is None
        assert out["cumulative_pnl"] == "0"
        assert out["n_closed_trades"] == 0
        assert out["mode"] == "paper"

    def test_nested_dataclass(self) -> None:
        snap = _FakeSnapshot(
            capital_quote=Decimal("100.42"),
            open_position=_FakePosition(
                side="long",
                entry_price=Decimal("65000.123"),
                quantity=Decimal("0.0001"),
            ),
            cumulative_pnl=Decimal("-5.5"),
            n_closed_trades=3,
            mode="real",
        )
        out = serialise(snap)
        assert out["open_position"]["side"] == "long"
        assert out["open_position"]["entry_price"] == "65000.123"
        assert out["open_position"]["quantity"] == "0.0001"
        assert out["cumulative_pnl"] == "-5.5"

    def test_tuple_becomes_list(self) -> None:
        # JSON has no tuple type ; tuples must serialise as arrays.
        out = serialise((Decimal("1"), Decimal("2"), Decimal("3")))
        assert out == ["1", "2", "3"]

    def test_passthrough_primitives(self) -> None:
        assert serialise("hello") == "hello"
        assert serialise(42) == 42
        assert serialise(3.14) == 3.14
        assert serialise(True) is True
        assert serialise(None) is None

    def test_json_dumps_round_trip(self) -> None:
        # The whole point of _serialise is that the output passes
        # through json.dumps cleanly — the serialiser must not leak
        # Decimal or dataclass instances.
        snap = _FakeSnapshot(
            capital_quote=Decimal("20"),
            open_position=None,
            cumulative_pnl=Decimal("1.5"),
            n_closed_trades=2,
            mode="paper",
        )
        encoded = json.dumps(serialise(snap))
        decoded = json.loads(encoded)
        assert decoded["capital_quote"] == "20"
        assert decoded["cumulative_pnl"] == "1.5"


# ─── create_server ──────────────────────────────────────────────────────────


def _free_port() -> int:
    """Return an unused TCP port on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _make_temp_web_root(tmp_path: Path) -> Path:
    """Build a minimal web root with index.html + a static asset."""
    web = tmp_path / "web"
    (web / "static").mkdir(parents=True)
    (web / "index.html").write_text(
        "<!doctype html><html><body>hello emeraude</body></html>",
        encoding="utf-8",
    )
    (web / "static" / "app.js").write_text(
        "console.log('hello');",
        encoding="utf-8",
    )
    return web


@pytest.mark.unit
class TestCreateServer:
    """``create_server`` wires the AppContext + auth token."""

    def test_returns_server_and_token(self, tmp_path: Path) -> None:
        context = AppContext()
        web_root = _make_temp_web_root(tmp_path)
        server, token = create_server(
            context=context,
            web_root=web_root,
            host="127.0.0.1",
            port=_free_port(),
        )
        try:
            assert server.app_context is context
            assert server.web_root == web_root
            assert isinstance(token, str)
            # secrets.token_urlsafe(32) returns 43 base64url chars.
            assert len(token) > 30
            assert server.auth_token == token
        finally:
            server.server_close()

    def test_auth_token_is_random(self, tmp_path: Path) -> None:
        context = AppContext()
        web_root = _make_temp_web_root(tmp_path)
        port_a = _free_port()
        port_b = _free_port()
        srv_a, token_a = create_server(context=context, web_root=web_root, port=port_a)
        srv_b, token_b = create_server(context=context, web_root=web_root, port=port_b)
        try:
            assert token_a != token_b
        finally:
            srv_a.server_close()
            srv_b.server_close()


# ─── HTTP integration ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestHTTPIntegration:
    """Spin the real server in a thread and exercise the HTTP wire."""

    def _setup_server(
        self,
        tmp_path: Path,
    ) -> tuple[int, str, threading.Thread, object]:
        """Launch a server on a free port and return the connection bits."""
        web_root = _make_temp_web_root(tmp_path)
        port = _free_port()
        context = AppContext()
        server, token = create_server(context=context, web_root=web_root, port=port)
        thread = serve_in_thread(server)
        # Tiny sleep so serve_forever has actually accepted before
        # the test sends its first request.
        time.sleep(0.05)
        return port, token, thread, server

    def _stop(self, server: object, thread: threading.Thread) -> None:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]
        thread.join(timeout=5)

    def test_get_index_returns_html_and_sets_cookie(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")

            assert resp.status == 200
            assert resp.getheader("Content-Type", "").startswith("text/html")
            assert "hello emeraude" in body
            cookie = resp.getheader("Set-Cookie", "")
            assert AUTH_COOKIE in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=Strict" in cookie
        finally:
            self._stop(server, thread)

    def test_get_static_asset(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/static/app.js")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")

            assert resp.status == 200
            assert resp.getheader("Content-Type", "").startswith("application/javascript")
            assert "console.log" in body
        finally:
            self._stop(server, thread)

    def test_static_path_traversal_blocked(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            # Try to escape via .. — must be rejected.
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/static/../../etc/hosts")
            resp = conn.getresponse()
            resp.read()
            assert resp.status in (400, 403, 404)
        finally:
            self._stop(server, thread)

    def test_api_dashboard_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            # No cookie -> 403.
            conn.request("GET", "/api/dashboard")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_api_dashboard_returns_snapshot(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/dashboard",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())

            assert resp.status == 200
            # Snapshot keys (cf. DashboardSnapshot).
            assert "capital_quote" in body
            assert "open_position" in body
            assert "cumulative_pnl" in body
            assert "n_closed_trades" in body
            assert "mode" in body
            # iter #82 : circuit_breaker_state surfaced for the
            # emergency-stop banner.
            assert "circuit_breaker_state" in body
            assert isinstance(body["circuit_breaker_state"], str)
            # cumulative_pnl was Decimal("0") at cold start ; serialises
            # as the string "0".
            assert isinstance(body["cumulative_pnl"], str)
        finally:
            self._stop(server, thread)

    def test_api_journal_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/journal")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_api_journal_returns_snapshot(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/journal",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())

            assert resp.status == 200
            # JournalSnapshot keys (cf. journal_types.JournalSnapshot).
            assert "rows" in body
            assert "total_returned" in body
            assert isinstance(body["rows"], list)
            # total_returned matches len(rows) by construction.
            assert body["total_returned"] == len(body["rows"])
        finally:
            self._stop(server, thread)

    def test_api_config_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/config")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_api_config_returns_snapshot(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/config",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())

            assert resp.status == 200
            # ConfigSnapshot keys (cf. config_types.ConfigSnapshot).
            assert "mode" in body
            assert "starting_capital" in body
            assert "app_version" in body
            assert "total_audit_events" in body
            assert "db_path" in body
            # Decimal -> string per _serialise contract ; None when not
            # configured.
            assert body["starting_capital"] is None or isinstance(body["starting_capital"], str)
            assert isinstance(body["app_version"], str)
            assert isinstance(body["total_audit_events"], int)
            assert isinstance(body["db_path"], str)
        finally:
            self._stop(server, thread)

    def test_api_learning_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/learning")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_api_learning_returns_snapshot_shape(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/learning",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 200

            # LearningSnapshot keys.
            assert "strategies" in body
            assert "champion" in body

            # 3 known strategies (cf. KNOWN_STRATEGIES). Cold-start: each
            # carries the uniform prior (alpha=beta=1, n_trades=0).
            strategies = body["strategies"]
            assert isinstance(strategies, list)
            assert len(strategies) == 3
            names = {s["name"] for s in strategies}
            assert names == {"trend_follower", "mean_reversion", "breakout_hunter"}
            for s in strategies:
                assert "alpha" in s
                assert "beta" in s
                assert "n_trades" in s
                assert "win_rate" in s
                # Decimal -> string per _serialise contract.
                assert isinstance(s["win_rate"], str)
                assert isinstance(s["alpha"], int)
                assert isinstance(s["beta"], int)

            # No champion at cold start.
            assert body["champion"] is None
        finally:
            self._stop(server, thread)

    def test_api_performance_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/performance")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_api_performance_returns_snapshot_shape(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/performance",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 200

            # PerformanceSnapshot keys (cf. performance_types.PerformanceSnapshot).
            for key in (
                "n_trades",
                "n_wins",
                "n_losses",
                "win_rate",
                "expectancy",
                "avg_win",
                "avg_loss",
                "profit_factor",
                "sharpe_ratio",
                "sortino_ratio",
                "calmar_ratio",
                "max_drawdown",
                "has_data",
            ):
                assert key in body

            # Cold start : no closed positions ; ``has_data=False`` and
            # all numeric fields sit at the zero default.
            assert isinstance(body["has_data"], bool)
            assert body["has_data"] is False
            assert body["n_trades"] == 0
            # Decimal -> string per _serialise contract.
            assert isinstance(body["win_rate"], str)
            assert isinstance(body["expectancy"], str)
        finally:
            self._stop(server, thread)

    def test_api_unknown_route_returns_404(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/does-not-exist",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 404
            assert "error" in body
        finally:
            self._stop(server, thread)

    # ── POST /api/toggle-mode ────────────────────────────────────────────────

    def _post_json(
        self,
        port: int,
        path: str,
        body: object,
        *,
        token: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        """Tiny POST helper. Returns ``(status, json_body)``."""
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token is not None:
            headers["Cookie"] = f"{AUTH_COOKIE}={token}"
        encoded = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
        conn.request("POST", path, body=encoded, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, json.loads(raw) if raw else {}

    def test_toggle_mode_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(port, "/api/toggle-mode", {"mode": "real"}, token=None)
            assert status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_toggle_mode_persists_and_returns_snapshot(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # Cold start mode = MODE_PAPER (cf. AppContext.DEFAULT_MODE).
            status, body = self._post_json(port, "/api/toggle-mode", {"mode": "real"}, token=token)
            assert status == 200
            # The response is a fresh ConfigSnapshot reflecting the new mode.
            assert body["mode"] == "real"

            # Round-trip via GET /api/config to confirm persistence.
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/config", headers={"Cookie": f"{AUTH_COOKIE}={token}"})
            resp = conn.getresponse()
            persisted = json.loads(resp.read())
            assert persisted["mode"] == "real"

            # Toggle back to paper to leave the test DB clean.
            status_back, body_back = self._post_json(
                port, "/api/toggle-mode", {"mode": "paper"}, token=token
            )
            assert status_back == 200
            assert body_back["mode"] == "paper"
        finally:
            self._stop(server, thread)

    def test_toggle_mode_rejects_invalid_mode(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(port, "/api/toggle-mode", {"mode": "moon"}, token=token)
            assert status == 400
            assert body["error"] == "invalid mode"
        finally:
            self._stop(server, thread)

    def test_toggle_mode_rejects_missing_mode(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(port, "/api/toggle-mode", {}, token=token)
            assert status == 400
            assert body["error"] == "invalid mode"
        finally:
            self._stop(server, thread)

    def test_toggle_mode_rejects_non_object_body(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(port, "/api/toggle-mode", ["real"], token=token)
            assert status == 400
            # Either "body must be a JSON object" (parser sees a list) is
            # the expected message ; assert just the structure.
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_toggle_mode_rejects_invalid_json(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(port, "/api/toggle-mode", b"{not-json", token=token)
            assert status == 400
            assert body["error"] == "invalid JSON"
        finally:
            self._stop(server, thread)

    def test_toggle_mode_rejects_empty_body(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # Send a POST with Content-Length: 0.
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/api/toggle-mode",
                body=b"",
                headers={
                    "Cookie": f"{AUTH_COOKIE}={token}",
                    "Content-Length": "0",
                },
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 400
            assert body["error"] == "missing body"
        finally:
            self._stop(server, thread)

    def test_toggle_mode_rejects_non_numeric_content_length(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # http.client refuses to send a non-numeric Content-Length, so
            # we hand-craft the request via a raw socket to exercise the
            # ``except ValueError`` branch in ``_read_json_object``.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(("127.0.0.1", port))
            request = (
                "POST /api/toggle-mode HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                f"Cookie: {AUTH_COOKIE}={token}\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: not-a-number\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            sock.sendall(request.encode("utf-8"))
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            sock.close()
            response = b"".join(chunks)
            head, _, body_bytes = response.partition(b"\r\n\r\n")
            assert b"400" in head.split(b"\r\n", 1)[0]
            body = json.loads(body_bytes)
            assert body["error"] == "invalid Content-Length"
        finally:
            self._stop(server, thread)

    def test_toggle_mode_rejects_oversized_body(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # Build a body just over the 4 KB cap.
            payload = b'{"mode":"real","junk":"' + (b"x" * 5000) + b'"}'
            status, body = self._post_json(port, "/api/toggle-mode", payload, token=token)
            assert status == 413
            assert body["error"] == "body too large"
        finally:
            self._stop(server, thread)

    def test_unknown_post_route_returns_404(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(port, "/api/does-not-exist", {}, token=token)
            assert status == 404
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_post_to_non_api_path_returns_404(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/static/app.js",
                body=b"{}",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 404
        finally:
            self._stop(server, thread)

    # ── /api/credentials (iter #81) ──────────────────────────────────────────

    def _delete(
        self,
        port: int,
        path: str,
        *,
        token: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        """Tiny DELETE helper. Returns ``(status, json_body)``."""
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers: dict[str, str] = {}
        if token is not None:
            headers["Cookie"] = f"{AUTH_COOKIE}={token}"
        conn.request("DELETE", path, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, json.loads(raw) if raw else {}

    def test_credentials_get_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/credentials")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_credentials_get_returns_status_shape(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/credentials",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 200
            # BinanceCredentialsStatus shape.
            assert "api_key_set" in body
            assert "api_secret_set" in body
            assert "api_key_suffix" in body
            assert "passphrase_available" in body
            assert isinstance(body["api_key_set"], bool)
            assert isinstance(body["api_secret_set"], bool)
            assert body["api_key_suffix"] is None or isinstance(body["api_key_suffix"], str)
            assert isinstance(body["passphrase_available"], bool)
        finally:
            self._stop(server, thread)

    def test_credentials_post_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(
                port,
                "/api/credentials",
                {"api_key": "A" * 64, "api_secret": "B" * 64},
                token=None,
            )
            assert status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_credentials_delete_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._delete(port, "/api/credentials", token=None)
            assert status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_credentials_post_persists_when_passphrase_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The passphrase env var is read on each save_credentials call.
        # Set a valid one for this test ; clean up via DELETE at the end.
        monkeypatch.setenv("EMERAUDE_API_PASSPHRASE", "test-pp-xyz123")

        port, token, thread, server = self._setup_server(tmp_path)
        try:
            api_key = "A" * 60 + "WXYZ"
            api_secret = "B" * 64
            status, body = self._post_json(
                port,
                "/api/credentials",
                {"api_key": api_key, "api_secret": api_secret},
                token=token,
            )
            assert status == 200
            # Returned status reflects the new persistence state.
            assert body["api_key_set"] is True
            assert body["api_secret_set"] is True
            assert body["api_key_suffix"] == "WXYZ"
            assert body["passphrase_available"] is True

            # Round-trip via GET to confirm the persisted status.
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/credentials",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            persisted = json.loads(resp.read())
            assert persisted["api_key_set"] is True
            assert persisted["api_key_suffix"] == "WXYZ"

            # Cleanup : DELETE so the test DB doesn't leak across tests.
            del_status, del_body = self._delete(port, "/api/credentials", token=token)
            assert del_status == 200
            assert del_body["api_key_set"] is False
            assert del_body["api_secret_set"] is False
            assert del_body["api_key_suffix"] is None
        finally:
            self._stop(server, thread)

    def test_credentials_post_503_when_passphrase_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EMERAUDE_API_PASSPHRASE", raising=False)

        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(
                port,
                "/api/credentials",
                {"api_key": "A" * 64, "api_secret": "B" * 64},
                token=token,
            )
            assert status == 503
            # The error message is the service's own — surface it intact.
            error = body["error"]
            assert isinstance(error, str)
            assert "EMERAUDE_API_PASSPHRASE" in error
        finally:
            self._stop(server, thread)

    def test_credentials_post_400_on_bad_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMERAUDE_API_PASSPHRASE", "test-pp-xyz123")

        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # api_key too short — the validator rejects with a precise
            # French message that we forward verbatim.
            status, body = self._post_json(
                port,
                "/api/credentials",
                {"api_key": "tooshort", "api_secret": "B" * 64},  # pragma: allowlist secret
                token=token,
            )
            assert status == 400
            error = body["error"]
            assert isinstance(error, str)
            assert "api_key" in error
        finally:
            self._stop(server, thread)

    def test_credentials_post_400_on_missing_fields(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # Body is a JSON object but the keys are missing.
            status, body = self._post_json(
                port,
                "/api/credentials",
                {"api_key": "A" * 64},
                token=token,
            )
            assert status == 400
            error = body["error"]
            assert isinstance(error, str)
            assert "api_key" in error
            assert "api_secret" in error
        finally:
            self._stop(server, thread)

    def test_credentials_post_400_on_non_string_fields(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(
                port,
                "/api/credentials",
                {"api_key": 1234, "api_secret": None},
                token=token,
            )
            assert status == 400
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_credentials_delete_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EMERAUDE_API_PASSPHRASE", raising=False)

        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # Two consecutive DELETEs without prior save : both succeed.
            status1, body1 = self._delete(port, "/api/credentials", token=token)
            status2, body2 = self._delete(port, "/api/credentials", token=token)
            assert status1 == 200
            assert status2 == 200
            assert body1["api_key_set"] is False
            assert body2["api_key_set"] is False
        finally:
            self._stop(server, thread)

    def test_unknown_delete_route_returns_404(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._delete(port, "/api/does-not-exist", token=token)
            assert status == 404
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_delete_to_non_api_path_returns_404(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            # Non-/api/ paths use the plain-text 404 path (not the JSON
            # one), so call http.client directly rather than the JSON
            # helper.
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("DELETE", "/static/app.js")
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 404
        finally:
            self._stop(server, thread)

    # ── /api/emergency-stop + /api/emergency-reset (iter #82) ────────────────

    def test_emergency_stop_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(port, "/api/emergency-stop", {}, token=None)
            assert status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_emergency_reset_requires_auth(self, tmp_path: Path) -> None:
        port, _token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(port, "/api/emergency-reset", {}, token=None)
            assert status == 403
            assert "error" in body
        finally:
            self._stop(server, thread)

    def test_emergency_stop_freezes_breaker_and_returns_state(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # No body is required — emergency stop is unambiguous.
            status, body = self._post_json(port, "/api/emergency-stop", {}, token=token)
            assert status == 200
            assert body["state"] == "FROZEN"

            # Round-trip via /api/dashboard to confirm the snapshot
            # surfaces the state for the UI banner.
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/dashboard",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            dashboard = json.loads(resp.read())
            assert dashboard["circuit_breaker_state"] == "FROZEN"

            # Reset for the next test (state is persisted in SQLite).
            self._post_json(port, "/api/emergency-reset", {}, token=token)
        finally:
            self._stop(server, thread)

    def test_emergency_reset_returns_to_healthy(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # Freeze first so the reset has work to do.
            stop_status, stop_body = self._post_json(port, "/api/emergency-stop", {}, token=token)
            assert stop_status == 200
            assert stop_body["state"] == "FROZEN"

            reset_status, reset_body = self._post_json(
                port, "/api/emergency-reset", {}, token=token
            )
            assert reset_status == 200
            assert reset_body["state"] == "HEALTHY"

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/dashboard",
                headers={"Cookie": f"{AUTH_COOKIE}={token}"},
            )
            resp = conn.getresponse()
            dashboard = json.loads(resp.read())
            assert dashboard["circuit_breaker_state"] == "HEALTHY"
        finally:
            self._stop(server, thread)

    def test_emergency_stop_idempotent(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status1, body1 = self._post_json(port, "/api/emergency-stop", {}, token=token)
            status2, body2 = self._post_json(port, "/api/emergency-stop", {}, token=token)
            assert status1 == 200
            assert status2 == 200
            assert body1["state"] == "FROZEN"
            assert body2["state"] == "FROZEN"

            # Reset for cleanup.
            self._post_json(port, "/api/emergency-reset", {}, token=token)
        finally:
            self._stop(server, thread)

    def test_emergency_reset_idempotent_on_healthy(self, tmp_path: Path) -> None:
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            # Two resets in a row from a healthy state — still 200.
            status1, body1 = self._post_json(port, "/api/emergency-reset", {}, token=token)
            status2, body2 = self._post_json(port, "/api/emergency-reset", {}, token=token)
            assert status1 == 200
            assert status2 == 200
            assert body1["state"] == "HEALTHY"
            assert body2["state"] == "HEALTHY"
        finally:
            self._stop(server, thread)

    def test_emergency_stop_ignores_request_body(self, tmp_path: Path) -> None:
        # The endpoint takes no body parameters — sending one shouldn't
        # break it (we just don't read the body in the handler).
        port, token, thread, server = self._setup_server(tmp_path)
        try:
            status, body = self._post_json(
                port,
                "/api/emergency-stop",
                {"unexpected": "field"},
                token=token,
            )
            assert status == 200
            assert body["state"] == "FROZEN"

            # Cleanup.
            self._post_json(port, "/api/emergency-reset", {}, token=token)
        finally:
            self._stop(server, thread)


# ─── AppContext basic smoke ─────────────────────────────────────────────────


@pytest.mark.unit
class TestAppContext:
    def test_default_construction_provides_data_sources(self) -> None:
        ctx = AppContext()
        assert ctx.dashboard_data_source is not None
        assert ctx.journal_data_source is not None
        assert ctx.config_data_source is not None
        assert ctx.learning_data_source is not None
        assert ctx.performance_data_source is not None
        assert ctx.binance_credentials_service is not None
        assert ctx.wallet is not None


# ─── web_app helpers ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestWebAppHelpers:
    """Cover the desktop-side helpers from :mod:`emeraude.web_app`.

    The Android-only ``_open_android_webview`` is gated by a lazy
    ``from jnius import autoclass`` and stays uncovered on host (no
    JVM runtime here). Kept out of CI's coverage by not invoking it.
    """

    def test_is_android_false_on_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Make sure ANDROID_PRIVATE is unset.
        monkeypatch.delenv("ANDROID_PRIVATE", raising=False)
        assert _is_android() is False

    def test_is_android_true_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANDROID_PRIVATE", "/data/data/foo/files")
        assert _is_android() is True

    def test_resolve_web_root_finds_packaged_dir(self) -> None:
        # The real ``src/emeraude/web/index.html`` is shipped with
        # the package starting iter #78. The resolver finds it as a
        # sibling of the emeraude package files.
        web_root = _resolve_web_root()
        assert (web_root / "index.html").is_file()
        # Sanity : the index.html mentions Vuetify per ADR-0004.
        content = (web_root / "index.html").read_text(encoding="utf-8")
        assert "Vuetify" in content or "vuetify" in content.lower()

    def test_resolve_web_root_raises_when_index_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patch __file__ via importlib reload so the resolver looks
        # in a directory without index.html.
        fake_module_dir = tmp_path / "fake_emeraude"
        fake_module_dir.mkdir()
        # Don't create web/ — the resolver should raise.
        fake_file = fake_module_dir / "web_app.py"
        fake_file.write_text("# placeholder", encoding="utf-8")

        monkeypatch.setattr(web_app, "__file__", str(fake_file))
        with pytest.raises(FileNotFoundError, match="web/ directory not found"):
            web_app._resolve_web_root()
