"""Exponential-backoff retry decorator for transient HTTP failures.

Wraps a synchronous callable so that transient errors (network blips,
``HTTP 429`` rate-limit responses, ``HTTP 5xx`` server errors) trigger
automatic retries with exponential backoff and jitter, while
non-retryable errors (client bugs, validation failures) propagate
immediately.

Default policy is tuned for the Binance + CoinGecko API patterns we
observe in 2026:

* 5 attempts maximum (1 initial + 4 retries).
* Initial wait 0.5 s, backoff factor 2, max wait 30 s — fits inside
  the niveau-entreprise 30 s decision-cycle SLA only when the upstream
  is responsive ; degraded upstream forces the orchestrator to skip
  the cycle, not block.
* Jitter 0.5x-1.5x of the computed wait — avoids synchronized
  thundering herd when many cycles trigger at the same minute mark.
* Retry on :class:`urllib.error.URLError` (transient network) and
  :class:`urllib.error.HTTPError` with code ``429`` (rate limit) or
  ``5xx`` (server fault).
* Do **not** retry on ``HTTPError`` ``4xx`` other than 429 — client
  bugs do not heal themselves.

Each retry emits a structured ``WARNING`` log line containing the
attempt number, the exception class + message, and the wait until
the next attempt. This is the audit trail of HTTP retries — no
caller code needs to log them manually.
"""

from __future__ import annotations

import functools
import logging
import random
import time
import urllib.error
from typing import TYPE_CHECKING, Final, ParamSpec, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

# ─── Default policy constants ────────────────────────────────────────────────

DEFAULT_MAX_ATTEMPTS: Final[int] = 5
DEFAULT_INITIAL_DELAY: Final[float] = 0.5
DEFAULT_BACKOFF_FACTOR: Final[float] = 2.0
DEFAULT_MAX_DELAY: Final[float] = 30.0
DEFAULT_JITTER_RANGE: Final[tuple[float, float]] = (0.5, 1.5)

# Cryptographically secure RNG — used purely for jitter, but `random.random`
# would trip bandit S311 with no behavioral benefit. SystemRandom is one
# instance, no per-call setup cost.
_RNG: Final[random.SystemRandom] = random.SystemRandom()

# HTTP status code constants used by the default retry predicate.
_HTTP_TOO_MANY_REQUESTS: Final[int] = 429
_HTTP_SERVER_ERROR_MIN: Final[int] = 500
_HTTP_SERVER_ERROR_MAX: Final[int] = 600  # exclusive upper bound (5xx ⇒ < 600)


# ─── Default retry predicate ─────────────────────────────────────────────────


def _is_retryable_http_status(code: int) -> bool:
    """Return ``True`` iff ``code`` is a transient-server signal worth retrying.

    * ``429``  — rate-limited (Binance / CoinGecko).
    * ``5xx``  — server-side errors (likely transient).
    """
    return (
        code == _HTTP_TOO_MANY_REQUESTS or _HTTP_SERVER_ERROR_MIN <= code < _HTTP_SERVER_ERROR_MAX
    )


def default_should_retry(exc: BaseException) -> bool:
    """Standard retry predicate : URLError + retryable HTTP status codes."""
    if isinstance(exc, urllib.error.HTTPError):
        return _is_retryable_http_status(exc.code)
    return isinstance(exc, urllib.error.URLError)


# ─── Decorator ───────────────────────────────────────────────────────────────


def retry(
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter_range: tuple[float, float] = DEFAULT_JITTER_RANGE,
    should_retry: Callable[[BaseException], bool] = default_should_retry,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Build a decorator that retries the wrapped callable on transient errors.

    Args:
        max_attempts: total attempts including the first call. Must be ≥ 1.
            ``1`` disables retrying entirely (the decorator becomes a no-op).
        initial_delay: base wait in seconds before the first retry.
        backoff_factor: each retry waits ``initial_delay * factor**(n-1)``,
            where ``n`` is the attempt number (1-indexed). ``factor=1``
            yields a constant backoff.
        max_delay: hard cap on the wait between retries (jitter applied
            after the cap).
        jitter_range: ``(min, max)`` multiplier applied to each computed
            wait. ``(1.0, 1.0)`` disables jitter (deterministic timing).
        should_retry: predicate called with the raised exception.
            Returning ``False`` causes the decorator to re-raise immediately.

    Raises:
        ValueError: on invalid ``max_attempts`` (< 1).
    """
    if max_attempts < 1:
        msg = f"max_attempts must be >= 1, got {max_attempts}"
        raise ValueError(msg)

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except BaseException as exc:
                    if not should_retry(exc):
                        # Non-retryable (e.g. HTTP 404, ValueError). Surface
                        # immediately so the caller sees the real bug.
                        raise
                    if attempt >= max_attempts:
                        # Last attempt failed ; out of budget.
                        _LOGGER.warning(
                            "retry exhausted for %s after %d attempts : %s : %s",
                            func.__name__,
                            attempt,
                            type(exc).__name__,
                            exc,
                        )
                        raise

                    base_wait = min(
                        initial_delay * (backoff_factor ** (attempt - 1)),
                        max_delay,
                    )
                    wait = base_wait * _RNG.uniform(*jitter_range)
                    _LOGGER.warning(
                        "retry attempt %d/%d for %s : %s : %s ; waiting %.2fs",
                        attempt,
                        max_attempts,
                        func.__name__,
                        type(exc).__name__,
                        exc,
                        wait,
                    )
                    time.sleep(wait)

            # Unreachable : the loop either returns or raises. Kept for
            # type-checkers that don't model exhaustive returns.
            msg = f"retry: unreachable code in {func.__name__}"  # pragma: no cover
            raise RuntimeError(msg)  # pragma: no cover

        return wrapper

    return decorator
