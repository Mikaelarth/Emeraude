"""Property-based tests for emeraude.infra.exchange."""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.infra import exchange

# Restricted alphabet : printable ASCII excluding NULL bytes (DB / network
# safe). Length bounds keep PBKDF2-style hash work bounded.
_ascii_text = st.text(
    alphabet=st.characters(
        min_codepoint=0x20,
        max_codepoint=0x7E,
    ),
    min_size=1,
    max_size=200,
)


@pytest.mark.property
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(secret=_ascii_text, query=_ascii_text)
def test_signature_matches_hmac_sha256_definition(secret: str, query: str) -> None:
    """``BinanceClient._sign(query)`` is exactly ``HMAC_SHA256(secret, query).hexdigest()``."""
    client = exchange.BinanceClient(api_key="ignored", api_secret=secret)
    signed = client._sign(query)
    expected = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    assert signed == expected


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(secret=_ascii_text, query=_ascii_text)
def test_signature_is_deterministic(secret: str, query: str) -> None:
    """Same secret + same query → same signature. No nonce."""
    client = exchange.BinanceClient(api_key="x", api_secret=secret)
    assert client._sign(query) == client._sign(query)


@pytest.mark.property
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    value=st.decimals(
        min_value=Decimal("0.00000001"),
        max_value=Decimal("1000000"),
        allow_nan=False,
        allow_infinity=False,
        places=8,
    )
)
def test_format_decimal_round_trip(value: Decimal) -> None:
    """``Decimal(_format_decimal(v)) == v`` for any reasonable trading amount."""
    formatted = exchange._format_decimal(value)
    # No scientific notation.
    assert "E" not in formatted.upper()
    # Round-trip preserves numeric value.
    assert Decimal(formatted) == value
