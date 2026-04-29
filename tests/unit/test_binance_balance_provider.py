"""Unit tests for :class:`BinanceBalanceProvider` (no Kivy)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from urllib.error import URLError

import pytest

from emeraude.infra import audit, database
from emeraude.services.binance_balance_provider import (
    AUDIT_BALANCE_FAILED,
    AUDIT_BALANCE_FETCHED,
    DEFAULT_CACHE_TTL_SECONDS,
    REASON_DECRYPT_FAILED,
    REASON_HTTP_ERROR,
    REASON_INVALID_RESPONSE,
    REASON_NO_CREDENTIALS,
    REASON_NO_PASSPHRASE,
    BinanceBalanceProvider,
)
from emeraude.services.binance_credentials import (
    BinanceCredentialsService,
)

# ─── Fixtures + helpers ────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


_PASSPHRASE = "test-passphrase-strong-enough-pbkdf2"  # pragma: allowlist secret
_VALID_KEY = "abcDEF0123456789xyzABC9876543210"  # pragma: allowlist secret
_VALID_SECRET = "ZYXwvu98765432101234567890abcdef"  # pragma: allowlist secret


class _FakeBinanceClient:
    """Test double for :class:`BinanceClient`.

    Records every call ; lets tests pre-program the response or
    raise an exception. Constructor signature mirrors prod exactly so
    it slots into the ``client_factory`` callable.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        # Class-level state is set by the test before instantiation.
        self.balance_to_return = _FakeBinanceClient.next_balance
        self.exception_to_raise = _FakeBinanceClient.next_exception
        _FakeBinanceClient.instances.append(self)

    # Class-level pre-programmable state.
    instances: list[_FakeBinanceClient] = []  # noqa: RUF012
    next_balance: Decimal = Decimal("100")
    next_exception: Exception | None = None

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.next_balance = Decimal("100")
        cls.next_exception = None

    def get_account_balance(self, asset: str = "USDT") -> Decimal:
        if self.exception_to_raise is not None:
            raise self.exception_to_raise
        return self.balance_to_return


@pytest.fixture(autouse=True)
def _reset_fake_client() -> None:
    """Reset the class-level fake state between tests."""
    _FakeBinanceClient.reset()


def _make_provider(
    *,
    passphrase: str | None = _PASSPHRASE,
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
) -> BinanceBalanceProvider:
    return BinanceBalanceProvider(
        passphrase_provider=lambda: passphrase,
        cache_ttl_seconds=cache_ttl_seconds,
        client_factory=_FakeBinanceClient,
    )


def _save_credentials(fresh_db: Path) -> None:
    """Save valid credentials encrypted with _PASSPHRASE.

    The ``fresh_db`` arg is the pytest fixture marker (its setup
    binds the storage dir for this test) ; we don't use the path
    directly but we need the side-effect to be ordered correctly.
    """
    _ = fresh_db  # unused but ordering-dependent
    import os  # noqa: PLC0415

    os.environ["EMERAUDE_API_PASSPHRASE"] = _PASSPHRASE
    try:
        BinanceCredentialsService().save_credentials(
            api_key=_VALID_KEY,
            api_secret=_VALID_SECRET,
        )
    finally:
        del os.environ["EMERAUDE_API_PASSPHRASE"]


# ─── Validation ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_zero_ttl_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"cache_ttl_seconds must be > 0"):
            BinanceBalanceProvider(
                passphrase_provider=lambda: None,
                cache_ttl_seconds=0,
            )

    def test_negative_ttl_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"cache_ttl_seconds must be > 0"):
            BinanceBalanceProvider(
                passphrase_provider=lambda: None,
                cache_ttl_seconds=-1.0,
            )


# ─── Failure paths ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFailurePaths:
    def test_no_passphrase_returns_none(self, fresh_db: Path) -> None:
        provider = _make_provider(passphrase=None)
        assert provider.current_balance_usdt() is None

    def test_no_passphrase_emits_audit(self, fresh_db: Path) -> None:
        provider = _make_provider(passphrase=None)
        provider.current_balance_usdt()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_BALANCE_FAILED)
        assert any(e["payload"]["reason"] == REASON_NO_PASSPHRASE for e in events)

    def test_no_credentials_returns_none(self, fresh_db: Path) -> None:
        # Passphrase set but no keys saved.
        provider = _make_provider()
        assert provider.current_balance_usdt() is None

    def test_no_credentials_emits_audit(self, fresh_db: Path) -> None:
        provider = _make_provider()
        provider.current_balance_usdt()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_BALANCE_FAILED)
        assert any(e["payload"]["reason"] == REASON_NO_CREDENTIALS for e in events)

    def test_wrong_passphrase_returns_none(self, fresh_db: Path) -> None:
        # Save with _PASSPHRASE, read with a different one.
        _save_credentials(fresh_db)
        provider = _make_provider(passphrase="wrong-passphrase-here")
        assert provider.current_balance_usdt() is None

    def test_wrong_passphrase_emits_decrypt_failed(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        provider = _make_provider(passphrase="wrong-passphrase-here")
        provider.current_balance_usdt()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_BALANCE_FAILED)
        assert any(e["payload"]["reason"] == REASON_DECRYPT_FAILED for e in events)

    def test_http_error_returns_none(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = URLError("connection refused")
        provider = _make_provider()
        assert provider.current_balance_usdt() is None

    def test_http_error_emits_audit(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = URLError("network down")
        provider = _make_provider()
        provider.current_balance_usdt()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_BALANCE_FAILED)
        assert any(e["payload"]["reason"] == REASON_HTTP_ERROR for e in events)

    def test_invalid_response_returns_none(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = KeyError("balances")
        provider = _make_provider()
        assert provider.current_balance_usdt() is None

    def test_invalid_response_emits_audit(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = ValueError("bad JSON")
        provider = _make_provider()
        provider.current_balance_usdt()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_BALANCE_FAILED)
        assert any(e["payload"]["reason"] == REASON_INVALID_RESPONSE for e in events)


# ─── Success path ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSuccessPath:
    def test_returns_fetched_balance(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_balance = Decimal("42.5")
        provider = _make_provider()
        assert provider.current_balance_usdt() == Decimal("42.5")

    def test_emits_audit_on_success(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_balance = Decimal("17")
        provider = _make_provider()
        provider.current_balance_usdt()
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type=AUDIT_BALANCE_FETCHED)
        assert len(events) >= 1
        assert events[-1]["payload"]["balance"] == "17"
        assert events[-1]["payload"]["asset"] == "USDT"

    def test_client_factory_receives_decrypted_keys(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        provider = _make_provider()
        provider.current_balance_usdt()
        # Exactly one client instantiated, with the decrypted credentials.
        assert len(_FakeBinanceClient.instances) == 1
        client = _FakeBinanceClient.instances[0]
        assert client.api_key == _VALID_KEY
        assert client.api_secret == _VALID_SECRET


# ─── Cache TTL ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCacheTTL:
    def test_default_ttl_value(self) -> None:
        assert DEFAULT_CACHE_TTL_SECONDS == 60.0

    def test_repeated_calls_within_ttl_hit_cache(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_balance = Decimal("100")
        provider = _make_provider(cache_ttl_seconds=60)

        first = provider.current_balance_usdt()
        # Even if we change the next_balance, the cached value wins.
        _FakeBinanceClient.next_balance = Decimal("999")
        second = provider.current_balance_usdt()
        third = provider.current_balance_usdt()

        assert first == second == third == Decimal("100")
        # Only one client was constructed (single HTTP call).
        assert len(_FakeBinanceClient.instances) == 1

    def test_invalidate_cache_forces_refetch(self, fresh_db: Path) -> None:
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_balance = Decimal("100")
        provider = _make_provider(cache_ttl_seconds=60)

        first = provider.current_balance_usdt()
        provider.invalidate_cache()
        _FakeBinanceClient.next_balance = Decimal("250")
        second = provider.current_balance_usdt()

        assert first == Decimal("100")
        assert second == Decimal("250")
        assert len(_FakeBinanceClient.instances) == 2

    def test_failure_not_cached(self, fresh_db: Path) -> None:
        # If HTTP fails on first call, the next call should retry
        # rather than returning the failure (None) cached.
        _save_credentials(fresh_db)
        _FakeBinanceClient.next_exception = URLError("transient")
        provider = _make_provider(cache_ttl_seconds=60)

        first = provider.current_balance_usdt()
        # Recovery : second call succeeds.
        _FakeBinanceClient.next_exception = None
        _FakeBinanceClient.next_balance = Decimal("42")
        second = provider.current_balance_usdt()

        assert first is None
        assert second == Decimal("42")


# ─── Idempotence ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestIdempotence:
    def test_invalidate_on_empty_cache_is_safe(self, fresh_db: Path) -> None:
        provider = _make_provider(passphrase=None)
        # No prior fetch ; invalidate must not raise.
        provider.invalidate_cache()
        assert provider.current_balance_usdt() is None
