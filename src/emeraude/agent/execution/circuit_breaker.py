"""Circuit breaker — non-bypass trading safety net.

Implements the 4-state machine from doc 05 §"CIRCUIT BREAKER 4 niveaux"
and rule R10 from doc 07 :

    "Aucun chemin de code ne contourne le Circuit Breaker. S'il dit
    TRIGGERED, le bot s'arrête, point."

States :

* :attr:`CircuitBreakerState.HEALTHY`   — normal operation, trades allowed.
* :attr:`CircuitBreakerState.WARNING`   — degradation detected ; trades
  still allowed but the caller MUST reduce sizing (typically by half).
* :attr:`CircuitBreakerState.TRIGGERED` — automatic shutdown ; new trades
  are blocked. Existing positions can still be managed (exits).
* :attr:`CircuitBreakerState.FROZEN`    — manual lock. Only an explicit
  :func:`reset` call by the user can clear it.

Persistence :
    The current state lives in the ``settings`` table (key
    ``circuit_breaker.state``). A process restart preserves it.
    A corrupt value defaults to ``FROZEN`` — fail-safe over fail-open
    (anti-rule A8 : no silent error recovery).

Audit :
    Every state transition emits a ``CIRCUIT_BREAKER_STATE_CHANGE``
    audit event (rule R9). This makes any attempt to bypass the
    breaker visible after the fact.

This iteration ships the state machine + manual API. Automatic
triggers (drawdown, consecutive losses, latency) come in a future
iteration once the underlying signals are wired (anti-rule A1 :
no anticipatory features).
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Final

from emeraude.infra import audit, database

_LOGGER = logging.getLogger(__name__)

_SETTING_KEY: Final[str] = "circuit_breaker.state"
_AUDIT_EVENT: Final[str] = "CIRCUIT_BREAKER_STATE_CHANGE"


class CircuitBreakerState(StrEnum):
    """The four breaker levels."""

    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    TRIGGERED = "TRIGGERED"
    FROZEN = "FROZEN"


_DEFAULT_STATE: Final[CircuitBreakerState] = CircuitBreakerState.HEALTHY


# ─── Read / write ───────────────────────────────────────────────────────────


def get_state() -> CircuitBreakerState:
    """Return the current breaker state, persisted in the settings DB.

    Resolution :

    * No setting row → :attr:`HEALTHY` (first-run default).
    * Valid value → that state.
    * Corrupt value (unknown string) → :attr:`FROZEN`. Fail-safe : we
      refuse to operate with an unrecognized state.
    """
    raw = database.get_setting(_SETTING_KEY)
    if raw is None:
        return _DEFAULT_STATE
    try:
        return CircuitBreakerState(raw)
    except ValueError:
        _LOGGER.warning(
            "circuit_breaker: corrupt state %r in DB ; defaulting to FROZEN",
            raw,
        )
        return CircuitBreakerState.FROZEN


def set_state(new_state: CircuitBreakerState, *, reason: str = "") -> None:
    """Persist a new state and emit an audit event.

    Args:
        new_state: target state.
        reason: short human-readable explanation, recorded in the audit
            event payload. Examples : ``"drawdown 35% in 24h"``,
            ``"manual freeze by user"``.
    """
    old_state = get_state()
    database.set_setting(_SETTING_KEY, new_state.value)
    audit.audit(
        _AUDIT_EVENT,
        {
            "from": old_state.value,
            "to": new_state.value,
            "reason": reason,
        },
    )


# ─── Transitions ────────────────────────────────────────────────────────────


def trip(reason: str) -> None:
    """Trip the breaker to :attr:`TRIGGERED`. Use after automatic detection."""
    set_state(CircuitBreakerState.TRIGGERED, reason=reason)


def warn(reason: str) -> None:
    """Move the breaker to :attr:`WARNING`."""
    set_state(CircuitBreakerState.WARNING, reason=reason)


def freeze(reason: str = "manual") -> None:
    """Manually freeze the breaker. Only :func:`reset` clears it."""
    set_state(CircuitBreakerState.FROZEN, reason=reason)


def reset(reason: str = "manual") -> None:
    """Reset the breaker to :attr:`HEALTHY` (admin operation)."""
    set_state(CircuitBreakerState.HEALTHY, reason=reason)


# ─── Decision API (R10) ─────────────────────────────────────────────────────


def is_trade_allowed() -> bool:
    """Return ``True`` iff new trades are permitted **without restrictions**.

    This is the strictest check : only :attr:`HEALTHY` qualifies. A
    caller wishing to keep trading under :attr:`WARNING` (with reduced
    sizing) should use :func:`is_trade_allowed_with_warning` instead and
    handle the size adjustment explicitly.
    """
    return get_state() == CircuitBreakerState.HEALTHY


def is_trade_allowed_with_warning() -> bool:
    """Return ``True`` for :attr:`HEALTHY` or :attr:`WARNING`.

    The caller MUST apply reduced sizing (doc 05 §"Sécurité — Bug logique
    -> drawdown massif" : "Circuit Breaker 4 niveaux" — WARNING = sizing
    halved by convention) when in :attr:`WARNING`.
    """
    return get_state() in (
        CircuitBreakerState.HEALTHY,
        CircuitBreakerState.WARNING,
    )
