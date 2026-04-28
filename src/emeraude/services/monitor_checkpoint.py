"""Persistent sticky-flag checkpoint for surveillance monitors (doc 10 R10).

Doc 10 §"R10 — Mémoire long-terme + checkpoint étendu" mandates that
"100 % des états critiques [soient] restaurés après kill -9". Most of
the bot's state is already persistent thanks to SQLite WAL :

* ``settings`` (capital, breaker state, ...)
* ``audit_log`` (event history)
* ``regime_memory`` (per-(strategy, regime) outcome stats)
* ``strategy_performance`` (bandit Beta counts)
* ``champion_history`` (champion lifecycle)
* ``positions`` (open + closed trades)

But two pieces of **in-memory** state were missing : the sticky
``_triggered`` flags on :class:`DriftMonitor` (iter #44) and
:class:`RiskMonitor` (iter #46). Without persistence, after a crash
the monitor re-replays the same history, re-fires the audit event
(duplicate row) and re-escalates the breaker (idempotent but spammy).
The user can no longer tell "we already saw this" from "fresh
detection".

This module is the **bridge** that lets monitors checkpoint their
sticky flag through the existing :func:`infra.database.set_setting`
key-value store — no new schema, no new table, no migration. The
opt-in is via a :class:`MonitorId` namespace : when a monitor is
constructed with a ``monitor_id``, it loads + saves its sticky flag
under the key ``monitor.<id>.triggered``. When ``monitor_id is None``
(default), behaviour is strictly identical to pre-iter-#51 callers.

Composition pattern ::

    from emeraude.services.monitor_checkpoint import MonitorId
    from emeraude.services.drift_monitor import DriftMonitor

    # New : checkpointed monitor — sticky flag survives kill -9.
    monitor = DriftMonitor(
        tracker=tracker,
        monitor_id=MonitorId.DRIFT,
    )

The keys are namespaced under ``monitor.`` to avoid collisions with
other ``settings`` rows (capital, breaker state, etc.). Reading is
idempotent ; writing is atomic via the existing
:func:`set_setting` path.

Anti-règle A1 : no new persistence layer, no new schema. The two
extra ``settings`` rows (one per monitor) are the smallest possible
addition that makes the doc 10 I10 criterion holdable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from emeraude.infra import database

# Stable namespace prefix in the ``settings`` table. Tests +
# dashboards can scan ``settings`` for ``monitor.*`` to enumerate
# checkpointed monitors.
_SETTING_PREFIX: Final[str] = "monitor."

# Truthy / falsy string constants used for the value column. Settings
# is TEXT-only ; we encode the bool as a stable string so the
# round-trip is unambiguous.
_TRUE: Final[str] = "true"
_FALSE: Final[str] = "false"


class MonitorId(StrEnum):
    """Stable identifiers for the monitors that opt into persistence.

    Adding a new id is a backward-compatible change : existing rows
    keep their value, the new id starts with no checkpoint (which
    :func:`load_triggered` handles by returning ``False``).
    """

    DRIFT = "drift"
    RISK = "risk"


def _key_for(monitor_id: MonitorId) -> str:
    """Build the canonical settings key for a monitor's sticky flag."""
    return f"{_SETTING_PREFIX}{monitor_id.value}.triggered"


def load_triggered(monitor_id: MonitorId) -> bool:
    """Return the persisted sticky-triggered flag for ``monitor_id``.

    Args:
        monitor_id: namespace identifier.

    Returns:
        The persisted boolean. ``False`` when no row exists yet
        (fresh DB / first construction) — strictly equivalent to
        an in-memory monitor that has never fired.
    """
    raw = database.get_setting(_key_for(monitor_id))
    if raw is None:
        return False
    # Anything other than the canonical TRUE constant is treated as
    # False — defensive against manual edits / corrupt rows.
    return raw == _TRUE


def save_triggered(monitor_id: MonitorId, *, triggered: bool) -> None:
    """Persist the sticky-triggered flag for ``monitor_id``.

    Atomic via :func:`infra.database.set_setting` (UPSERT under the
    same transaction primitives the rest of the system uses).

    Args:
        monitor_id: namespace identifier.
        triggered: new value to persist.
    """
    database.set_setting(_key_for(monitor_id), _TRUE if triggered else _FALSE)


def clear_triggered(monitor_id: MonitorId) -> None:
    """Reset the persisted sticky flag to ``False``.

    Convenience wrapper around :func:`save_triggered` ``triggered=False``,
    surfaced under its own name so :meth:`Monitor.reset` reads as the
    intent (clear, not "save False").
    """
    save_triggered(monitor_id, triggered=False)
