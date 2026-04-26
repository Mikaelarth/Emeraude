"""Shared SSL context and HTTP helpers — single audit point for outbound calls.

Implements **rule R8** of the cahier des charges (`07_REGLES_OR_ET_ANTI_REGLES.md`):

    Tout ``urllib.request.urlopen`` doit recevoir
    ``context=core.net.SSL_CTX``. Sinon échec silencieux sur Android.

The trust chain prefers :mod:`certifi` (Mozilla CA bundle, deterministic
across OSes) and falls back to the system trust store if certifi is not
installed. On Android via Buildozer, certifi must be in the requirements
(``buildozer.spec``) — without it, recent roots like Let's Encrypt may be
missing from the OS trust store on older Android versions.

Public API:
    * :data:`SSL_CTX` — module-level singleton, cheap to reuse.
    * :func:`urlopen` — thin wrapper around :func:`urllib.request.urlopen`
      that injects :data:`SSL_CTX`, the default User-Agent, and the
      30-second timeout (aligned with the niveau-entreprise SLA on
      decision-cycle latency).

Note:
    Bandit's ``S310`` rule warns about ``urllib.request.urlopen`` with
    arbitrary URL schemes (``file://``, ``http://``, etc.). We accept
    that risk because every URL in this codebase is a hard-coded
    Binance / CoinGecko / Telegram endpoint, never a user-supplied
    string. The ``# noqa: S310`` markers in this file are deliberate.
"""

from __future__ import annotations

import logging
import ssl
import urllib.request
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

# Default request timeout (seconds). The pilier #3 SLA caps a decision
# cycle at 30 s ; HTTP I/O must fit inside that envelope.
DEFAULT_TIMEOUT: Final[float] = 30.0

# User-Agent identifies Emeraude traffic to upstream services. Visible in
# Binance request logs ; useful for debugging rate-limit issues.
DEFAULT_USER_AGENT: Final[str] = "Emeraude/0.0.5 (+https://github.com/Mikaelarth/Emeraude)"


def _certifi_cafile() -> str | None:
    """Return certifi's CA bundle path, or ``None`` if certifi is absent.

    Isolated from :func:`build_ssl_context` so tests can mock the
    presence/absence of certifi cleanly.
    """
    try:
        import certifi  # noqa: PLC0415
    except ImportError:  # pragma: no cover  (certifi is a runtime dependency)
        _LOGGER.warning(
            "certifi is not installed ; falling back to system CA store. "
            "Recent roots may be missing on older Android versions."
        )
        return None
    cafile: str = certifi.where()
    return cafile


def build_ssl_context(*, cafile: str | None = None) -> ssl.SSLContext:
    """Construct an SSL context for outbound HTTPS.

    Args:
        cafile: explicit path to a PEM-encoded CA bundle. If ``None``,
            the system default is used.

    Returns:
        A context with hostname checking and certificate verification
        enabled (``CERT_REQUIRED``), configured with the given CA bundle
        or the OS trust store.
    """
    if cafile is not None:
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


# Module-level shared context. Created once at import time, reused by every
# urlopen call. Safe to share across threads (SSLContext is thread-safe for
# read access ; we never mutate it after construction).
SSL_CTX: ssl.SSLContext = build_ssl_context(cafile=_certifi_cafile())


def urlopen(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
) -> bytes:
    """Open ``url`` with the canonical SSL context and return the body.

    Always uses :data:`SSL_CTX` and an explicit timeout — the only
    blessed way to make outbound HTTP calls in Emeraude.

    Args:
        url: target URL. Must be reachable via HTTP/HTTPS.
        method: HTTP verb (``GET`` / ``POST`` / etc.).
        headers: optional headers (added on top of the default User-Agent).
        data: optional request body bytes (POST / PUT).
        timeout: per-call timeout in seconds. Defaults to
            :data:`DEFAULT_TIMEOUT`.
        user_agent: override the User-Agent header.

    Returns:
        Raw response body bytes.

    Raises:
        urllib.error.HTTPError: 4xx / 5xx responses.
        urllib.error.URLError: connection failures, timeouts.
    """
    request = urllib.request.Request(url, method=method, data=data)  # noqa: S310
    request.add_header("User-Agent", user_agent)
    if headers:
        for name, value in headers.items():
            request.add_header(name, value)

    # The S310 (ruff) and B310 (bandit) suppressions on the next line cover
    # the same false positive : URLs in this codebase are hard-coded
    # Binance/CoinGecko/Telegram endpoints, never user-supplied.
    with urllib.request.urlopen(  # noqa: S310  # nosec B310
        request, context=SSL_CTX, timeout=timeout
    ) as response:
        body: bytes = response.read()
    return body
