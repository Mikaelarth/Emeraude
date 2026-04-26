"""Unit tests for emeraude.infra.net."""

from __future__ import annotations

import ssl
import sys
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import certifi
import pytest

from emeraude.infra import net

# ─── Module-level SSL_CTX ────────────────────────────────────────────────────


@pytest.mark.unit
class TestSSLContext:
    def test_ssl_ctx_is_ssl_context_instance(self) -> None:
        assert isinstance(net.SSL_CTX, ssl.SSLContext)

    def test_ssl_ctx_verifies_certificates(self) -> None:
        # CERT_REQUIRED is the only acceptable mode for HTTPS in production.
        assert net.SSL_CTX.verify_mode == ssl.CERT_REQUIRED

    def test_ssl_ctx_checks_hostname(self) -> None:
        # Disabling hostname check would defeat TLS — must be on.
        assert net.SSL_CTX.check_hostname is True

    def test_ssl_ctx_minimum_version_is_tls_1_2_or_higher(self) -> None:
        # Default ssl.create_default_context() pins TLS 1.2 minimum since
        # Python 3.10 ; verify we inherit that policy.
        assert net.SSL_CTX.minimum_version >= ssl.TLSVersion.TLSv1_2


# ─── build_ssl_context ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestBuildSSLContext:
    def test_with_cafile_creates_context(self) -> None:
        # certifi.where() returns a real PEM file ; reuse it.
        ctx = net.build_ssl_context(cafile=certifi.where())
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_without_cafile_uses_system_default(self) -> None:
        ctx = net.build_ssl_context(cafile=None)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED


# ─── _certifi_cafile ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCertifiCafile:
    def test_returns_path_when_certifi_installed(self) -> None:
        # certifi is a declared runtime dependency, so this must succeed.
        path = net._certifi_cafile()
        assert path is not None
        assert path.endswith(".pem")

    def test_returns_none_when_certifi_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate certifi missing : pop it from sys.modules + block re-import.
        monkeypatch.setitem(sys.modules, "certifi", None)
        assert net._certifi_cafile() is None


# ─── urlopen wrapper ────────────────────────────────────────────────────────


def _fake_response(body: bytes) -> MagicMock:
    """Build a context-manager-capable mock that returns ``body`` on read."""
    response = MagicMock()
    response.read.return_value = body
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


@pytest.mark.unit
class TestUrlopen:
    def test_returns_response_body_bytes(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(b'{"price": 100}')
            body = net.urlopen("https://example.com/api")
            assert body == b'{"price": 100}'

    def test_passes_ssl_context(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(b"")
            net.urlopen("https://example.com")

            kwargs = mock_urlopen.call_args.kwargs
            assert kwargs["context"] is net.SSL_CTX

    def test_passes_timeout(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(b"")
            net.urlopen("https://example.com", timeout=5.0)

            kwargs = mock_urlopen.call_args.kwargs
            assert kwargs["timeout"] == 5.0

    def test_default_timeout_is_30_seconds(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(b"")
            net.urlopen("https://example.com")

            kwargs = mock_urlopen.call_args.kwargs
            assert kwargs["timeout"] == net.DEFAULT_TIMEOUT == 30.0

    def test_default_user_agent_set(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(b"")
            net.urlopen("https://example.com")

            request = mock_urlopen.call_args.args[0]
            ua = request.get_header("User-agent")
            assert ua == net.DEFAULT_USER_AGENT
            assert "Emeraude" in ua

    def test_custom_user_agent_overrides_default(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(b"")
            net.urlopen("https://example.com", user_agent="Custom/1.0")

            request = mock_urlopen.call_args.args[0]
            assert request.get_header("User-agent") == "Custom/1.0"

    def test_custom_headers_added(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(b"")
            net.urlopen(
                "https://example.com",
                headers={"X-MBX-APIKEY": "abc123"},  # pragma: allowlist secret
            )

            request = mock_urlopen.call_args.args[0]
            # Note: urllib lowercases header names internally then uses
            # capitalize()-equivalent in get_header. Match that behavior.
            assert request.get_header("X-mbx-apikey") == "abc123"

    def test_method_propagates(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _fake_response(b"")
            net.urlopen("https://example.com", method="POST", data=b"x=1")

            request = mock_urlopen.call_args.args[0]
            assert request.get_method() == "POST"
            assert request.data == b"x=1"

    def test_http_error_propagates(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://example.com",
                code=429,
                msg="Too Many Requests",
                hdrs=None,  # type: ignore[arg-type]
                fp=BytesIO(b""),
            )
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                net.urlopen("https://example.com")
            assert excinfo.value.code == 429

    def test_url_error_propagates(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("connection refused")
            with pytest.raises(urllib.error.URLError):
                net.urlopen("https://example.com")
