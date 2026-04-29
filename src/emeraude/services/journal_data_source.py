"""Concrete :class:`JournalDataSource` backed by :func:`audit.query_events`.

Wraps the SQL-backed ``audit_log`` query so the Journal screen can
pull a snapshot without depending on :mod:`emeraude.infra.audit`
directly. This keeps the ui layer testable with fakes (the screen
only talks to the Protocol).

Read-only : never writes to ``audit_log``. The audit producer side is
the various services (champion_promotion, drift_monitor, etc.) — the
journal is a downstream consumer only.
"""

from __future__ import annotations

from typing import Final

from emeraude.infra import audit
from emeraude.services.journal_types import (
    DEFAULT_HISTORY_LIMIT,
    JournalSnapshot,
    format_event_row,
)

_ZERO: Final[int] = 0


class QueryEventsJournalDataSource:
    """Read-only :class:`JournalDataSource` backed by ``audit.query_events``.

    Implements the
    :class:`emeraude.services.journal_types.JournalDataSource`
    Protocol structurally (no inheritance — Protocols are duck-typed).

    Args:
        history_limit: maximum number of audit events to fetch per
            snapshot. Default :data:`DEFAULT_HISTORY_LIMIT` = 50.
        event_type: optional filter passed through to
            :func:`audit.query_events`. ``None`` = all event types.

    Raises:
        ValueError: on ``history_limit < 1``.
    """

    def __init__(
        self,
        *,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        event_type: str | None = None,
    ) -> None:
        if history_limit < 1:
            msg = f"history_limit must be >= 1, got {history_limit}"
            raise ValueError(msg)
        self._history_limit = history_limit
        self._event_type = event_type

    def fetch_snapshot(self) -> JournalSnapshot:
        """Pull a fresh snapshot from ``audit_log``.

        Most-recent-first ordering preserved (the underlying
        :func:`audit.query_events` already sorts by ``ts DESC,
        id DESC``). Returns an empty snapshot when the log is empty
        — cold start path.
        """
        events = audit.query_events(
            event_type=self._event_type,
            limit=self._history_limit,
        )
        rows = tuple(format_event_row(event) for event in events)
        return JournalSnapshot(rows=rows, total_returned=len(rows))
