"""Unit tests for emeraude.infra.retry."""

from __future__ import annotations

import urllib.error
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

import pytest

from emeraude.infra import retry

# ─── Test helpers ────────────────────────────────────────────────────────────


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.com",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,  # type: ignore[arg-type]
        fp=BytesIO(b""),
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``time.sleep`` with a mock so tests run fast."""
    mock = MagicMock()
    monkeypatch.setattr("emeraude.infra.retry.time.sleep", mock)
    return mock


@pytest.fixture
def deterministic_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force jitter to 1.0 so backoff timing is exactly initial * factor**n."""
    monkeypatch.setattr("emeraude.infra.retry._RNG.uniform", lambda *_args: 1.0)


# ─── default_should_retry ────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaultShouldRetry:
    def test_url_error_is_retryable(self) -> None:
        assert retry.default_should_retry(urllib.error.URLError("boom")) is True

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504, 599])
    def test_retryable_http_codes(self, code: int) -> None:
        assert retry.default_should_retry(_http_error(code)) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 418, 422])
    def test_non_retryable_http_codes(self, code: int) -> None:
        assert retry.default_should_retry(_http_error(code)) is False

    def test_value_error_is_not_retryable(self) -> None:
        assert retry.default_should_retry(ValueError("bad input")) is False

    def test_runtime_error_is_not_retryable(self) -> None:
        assert retry.default_should_retry(RuntimeError("oops")) is False


# ─── Decorator behavior ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestDecoratorBasics:
    def test_function_returning_normally_runs_once(self, no_sleep: MagicMock) -> None:
        call_count = {"n": 0}

        @retry.retry()
        def ok() -> str:
            call_count["n"] += 1
            return "done"

        assert ok() == "done"
        assert call_count["n"] == 1
        no_sleep.assert_not_called()

    def test_retries_on_url_error_then_succeeds(
        self, no_sleep: MagicMock, deterministic_jitter: None
    ) -> None:
        call_count = {"n": 0}

        @retry.retry(max_attempts=5, initial_delay=1.0)
        def flaky() -> str:
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise urllib.error.URLError("transient")
            return "ok"

        assert flaky() == "ok"
        assert call_count["n"] == 3
        # Two retries → two sleeps.
        assert no_sleep.call_count == 2

    def test_exhausts_retries_then_raises_last_error(
        self, no_sleep: MagicMock, deterministic_jitter: None
    ) -> None:
        @retry.retry(max_attempts=3, initial_delay=0.1)
        def always_fails() -> Any:
            raise urllib.error.URLError("boom")

        with pytest.raises(urllib.error.URLError, match="boom"):
            always_fails()

        # 3 attempts → 2 retries → 2 sleeps.
        assert no_sleep.call_count == 2

    def test_non_retryable_propagates_immediately(self, no_sleep: MagicMock) -> None:
        call_count = {"n": 0}

        @retry.retry(max_attempts=5)
        def bad_input() -> Any:
            call_count["n"] += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            bad_input()

        assert call_count["n"] == 1
        no_sleep.assert_not_called()

    def test_http_429_is_retried(self, no_sleep: MagicMock, deterministic_jitter: None) -> None:
        call_count = {"n": 0}

        @retry.retry(max_attempts=3, initial_delay=0.1)
        def rate_limited() -> str:
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise _http_error(429)
            return "passed"

        assert rate_limited() == "passed"
        assert call_count["n"] == 2

    def test_http_404_is_not_retried(self, no_sleep: MagicMock) -> None:
        call_count = {"n": 0}

        @retry.retry(max_attempts=5)
        def not_found() -> Any:
            call_count["n"] += 1
            raise _http_error(404)

        with pytest.raises(urllib.error.HTTPError):
            not_found()

        assert call_count["n"] == 1
        no_sleep.assert_not_called()


# ─── Backoff timing ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestBackoffTiming:
    def test_exponential_schedule(self, no_sleep: MagicMock, deterministic_jitter: None) -> None:
        @retry.retry(
            max_attempts=4,
            initial_delay=1.0,
            backoff_factor=2.0,
            max_delay=100.0,
        )
        def always_fails() -> Any:
            raise urllib.error.URLError("boom")

        with pytest.raises(urllib.error.URLError):
            always_fails()

        # Attempts 1-4 ; sleeps between attempts 1->2, 2->3, 3->4.
        # Wait formula : initial_delay * factor**(attempt-1) ; with jitter=1.0
        # we expect 1.0, 2.0, 4.0.
        actual_waits = [call.args[0] for call in no_sleep.call_args_list]
        assert actual_waits == pytest.approx([1.0, 2.0, 4.0])

    def test_max_delay_caps_wait(self, no_sleep: MagicMock, deterministic_jitter: None) -> None:
        @retry.retry(
            max_attempts=5,
            initial_delay=10.0,
            backoff_factor=10.0,  # would produce 10, 100, 1000, 10000
            max_delay=50.0,
        )
        def always_fails() -> Any:
            raise urllib.error.URLError("boom")

        with pytest.raises(urllib.error.URLError):
            always_fails()

        # All waits capped at 50.
        actual_waits = [call.args[0] for call in no_sleep.call_args_list]
        for wait in actual_waits:
            assert wait <= 50.0
        # First wait : 10 (under cap) ; subsequent : capped to 50.
        assert actual_waits[0] == pytest.approx(10.0)
        assert actual_waits[1] == pytest.approx(50.0)

    def test_jitter_range_applied(
        self, no_sleep: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force jitter to its upper bound to verify multiplication.
        monkeypatch.setattr("emeraude.infra.retry._RNG.uniform", lambda *_args: 1.5)

        @retry.retry(
            max_attempts=2,
            initial_delay=2.0,
            backoff_factor=1.0,
            jitter_range=(0.5, 1.5),
        )
        def always_fails() -> Any:
            raise urllib.error.URLError("boom")

        with pytest.raises(urllib.error.URLError):
            always_fails()

        actual_wait = no_sleep.call_args_list[0].args[0]
        # Base wait = initial * factor**0 = 2.0 ; jitter = 1.5 → 3.0.
        assert actual_wait == pytest.approx(3.0)


# ─── Custom should_retry ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCustomShouldRetry:
    def test_custom_predicate_overrides_default(
        self, no_sleep: MagicMock, deterministic_jitter: None
    ) -> None:
        # Retry only on RuntimeError.
        call_count = {"n": 0}

        @retry.retry(
            max_attempts=3,
            initial_delay=0.1,
            should_retry=lambda exc: isinstance(exc, RuntimeError),
        )
        def custom_fail() -> str:
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        assert custom_fail() == "ok"
        assert call_count["n"] == 3

    def test_custom_predicate_blocks_default_retryables(self, no_sleep: MagicMock) -> None:
        # An overly restrictive policy : retry nothing.
        @retry.retry(max_attempts=5, should_retry=lambda _exc: False)
        def fails_with_url_error() -> Any:
            raise urllib.error.URLError("transient")

        with pytest.raises(urllib.error.URLError):
            fails_with_url_error()

        no_sleep.assert_not_called()


# ─── Validation ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_max_attempts_zero_raises(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            retry.retry(max_attempts=0)

    def test_max_attempts_negative_raises(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            retry.retry(max_attempts=-1)

    def test_max_attempts_one_disables_retrying(self, no_sleep: MagicMock) -> None:
        @retry.retry(max_attempts=1)
        def failing() -> Any:
            raise urllib.error.URLError("boom")

        with pytest.raises(urllib.error.URLError):
            failing()

        no_sleep.assert_not_called()


# ─── functools.wraps preserves metadata ─────────────────────────────────────


@pytest.mark.unit
class TestMetadataPreservation:
    def test_function_name_preserved(self) -> None:
        @retry.retry()
        def my_special_function() -> int:
            return 42

        assert my_special_function.__name__ == "my_special_function"

    def test_docstring_preserved(self) -> None:
        @retry.retry()
        def documented() -> int:
            """My function docstring."""
            return 1

        assert documented.__doc__ == "My function docstring."
