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
            # cumulative_pnl was Decimal("0") at cold start ; serialises
            # as the string "0".
            assert isinstance(body["cumulative_pnl"], str)
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


# ─── AppContext basic smoke ─────────────────────────────────────────────────


@pytest.mark.unit
class TestAppContext:
    def test_default_construction_provides_data_sources(self) -> None:
        ctx = AppContext()
        assert ctx.dashboard_data_source is not None
        assert ctx.journal_data_source is not None
        assert ctx.config_data_source is not None
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
