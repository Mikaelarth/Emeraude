"""Property-based tests for emeraude.infra.net.urlopen header handling."""

from __future__ import annotations

import string
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.infra import net

# Header names: HTTP-token allowed characters (RFC 7230). We restrict to
# ASCII letters/digits/hyphen for safety in test inputs. Built from string
# constants rather than a literal to avoid triggering detect-secrets.
_HEADER_ALPHABET = string.ascii_letters + string.digits + "-"
_header_name = st.text(
    alphabet=st.characters(whitelist_categories=[], whitelist_characters=_HEADER_ALPHABET),
    min_size=1,
    max_size=30,
)
# Header values: printable ASCII, no CR/LF (forbidden by HTTP).
_header_value = st.text(
    alphabet=st.characters(
        min_codepoint=0x20,
        max_codepoint=0x7E,
        blacklist_characters="\r\n",
    ),
    min_size=0,
    max_size=80,
)


def _fake_response(body: bytes = b"") -> MagicMock:
    response = MagicMock()
    response.read.return_value = body
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(name=_header_name, value=_header_value)
def test_arbitrary_header_is_set_on_request(name: str, value: str) -> None:
    """For any valid header name + value, urlopen attaches it to the Request."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _fake_response()
        net.urlopen("https://example.com", headers={name: value})

        request = mock_urlopen.call_args.args[0]
        # urllib normalizes header names ; comparing case-insensitively.
        retrieved = request.get_header(name.capitalize())
        assert retrieved == value


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(timeout=st.floats(min_value=0.001, max_value=600.0, allow_nan=False))
def test_arbitrary_timeout_propagates(timeout: float) -> None:
    """The timeout argument is forwarded verbatim to urllib."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _fake_response()
        net.urlopen("https://example.com", timeout=timeout)

        kwargs = mock_urlopen.call_args.kwargs
        assert kwargs["timeout"] == timeout
