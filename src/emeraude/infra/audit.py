"""Structured JSON audit trail — non-blocking by default.

Implements R9 of the cahier des charges (`07_REGLES_OR_ET_ANTI_REGLES.md`):
every bot decision (entry, exit, skip, override, parameter update, drift
detection, etc.) generates an entry in :data:`audit_log` so that any state
or decision can be reconstructed post-mortem.

Architecture:

* :class:`AuditEvent` — immutable record (dataclass).
* :class:`AuditLogger` — async by default. Calls to :meth:`AuditLogger.log`
  enqueue an event ; a daemon worker thread drains the queue and writes
  rows in atomic transactions. A synchronous mode (``sync=True``) is
  provided for tests and emergency replay scenarios.
* Module-level helpers :func:`audit`, :func:`flush_default_logger`,
  :func:`shutdown_default_logger` operate on a process-wide singleton
  for ergonomic call sites in the bot's main loop.
* Query helpers :func:`query_events`, :func:`purge_older_than` read
  the table without requiring a running logger.

Failure policy (anti-règle A8 — pas d'erreur silencieuse) :

* Worker thread catches all exceptions and logs them via :mod:`logging` ;
  it never dies on a bad event.
* Bounded queue (default 1000) — when full, new events are **dropped with
  a warning**, never silently discarded ; the warning carries the event
  type for post-mortem.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from emeraude.infra import database

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

_DEFAULT_QUEUE_MAXSIZE: Final[int] = 1000
_DEFAULT_FLUSH_TIMEOUT: Final[float] = 5.0
_DEFAULT_STOP_TIMEOUT: Final[float] = 5.0
_POLL_INTERVAL_S: Final[float] = 0.005

# Schema version embedded with each row. Bump when payload semantics change
# in a way that older readers should explicitly opt into.
_AUDIT_VERSION: Final[int] = 1


# ─── Event record ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Immutable audit event ready for serialization."""

    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    ts: int = field(default_factory=lambda: int(time.time()))
    version: int = _AUDIT_VERSION


# ─── Logger ──────────────────────────────────────────────────────────────────


class AuditLogger:
    """Async JSON audit logger with synchronous fallback.

    Args:
        sync: when ``True``, :meth:`log` writes inline instead of
            enqueuing — useful for tests.
        queue_maxsize: bounded queue size in async mode. ``0`` = unbounded
            (use with care — memory growth).
    """

    def __init__(self, *, sync: bool = False, queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE) -> None:
        self._sync = sync
        self._queue: queue.Queue[AuditEvent | None] = queue.Queue(maxsize=queue_maxsize)
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._dropped: int = 0

    # ── Lifecycle ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """Whether the worker thread is alive (always ``False`` in sync mode)."""
        return self._worker is not None and self._worker.is_alive()

    @property
    def dropped_events(self) -> int:
        """Total events dropped because the queue was full (since :meth:`start`)."""
        return self._dropped

    def start(self) -> None:
        """Start the worker thread. No-op in sync mode or if already running."""
        with self._lock:
            if self._sync or self.is_running:
                return
            self._dropped = 0
            self._worker = threading.Thread(
                target=self._run,
                name="emeraude-audit-worker",
                daemon=True,
            )
            self._worker.start()

    def stop(self, *, timeout: float = _DEFAULT_STOP_TIMEOUT) -> None:
        """Stop the worker thread gracefully (drains pending events first).

        Pushes a sentinel ``None`` and waits up to ``timeout`` seconds for
        the worker to consume the queue and exit. After this call, calling
        :meth:`log` again falls back to synchronous writes (the worker is
        gone) — call :meth:`start` first to re-arm.
        """
        with self._lock:
            if self._sync or not self.is_running:
                return
            worker = self._worker
            if worker is None:  # pragma: no cover  (guarded by is_running)
                return
        # Sentinel sent OUTSIDE the lock to avoid deadlock if the worker is
        # blocked waiting for queue space.
        self._queue.put(None)
        worker.join(timeout=timeout)
        with self._lock:
            if not worker.is_alive():
                self._worker = None
            else:  # pragma: no cover  (worker did not honor sentinel within timeout)
                _LOGGER.warning(
                    "audit worker did not stop within %.1fs ; events may be lost", timeout
                )

    # ── Public API ──────────────────────────────────────────────────────────

    def log(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        """Record an audit event.

        In async mode, enqueues the event for the worker. If the queue is
        full, drops the event and increments :attr:`dropped_events`.
        In sync mode (or if :meth:`start` was never called), writes inline.
        """
        event = AuditEvent(
            event_type=event_type,
            payload=dict(payload) if payload is not None else {},
        )
        if self._sync or not self.is_running:
            self._write(event)
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._dropped += 1
            _LOGGER.warning(
                "audit queue full (size=%d) ; dropping event %r",
                self._queue.maxsize,
                event_type,
            )

    def flush(self, *, timeout: float = _DEFAULT_FLUSH_TIMEOUT) -> bool:
        """Block until all queued events are written, or ``timeout`` elapses.

        Returns ``True`` if drained, ``False`` if timeout was hit. Always
        ``True`` in sync mode.
        """
        if self._sync:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(_POLL_INTERVAL_S)
        return self._queue.unfinished_tasks == 0

    # ── Internal ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        """Worker loop : drain the queue and write each event to DB."""
        while True:
            event = self._queue.get()
            try:
                if event is None:  # sentinel
                    return
                try:
                    self._write(event)
                except Exception:
                    # Anti-règle A8 : never silent. Log and continue ; one bad
                    # event must not kill the worker.
                    _LOGGER.exception("audit write failed (event_type=%r)", event.event_type)
            finally:
                self._queue.task_done()

    def _write(self, event: AuditEvent) -> None:
        """Persist a single event to the audit_log table.

        JSON encoding uses ``sort_keys=True`` for deterministic output (eases
        diffing of audit trails between runs).
        """
        try:
            payload_json = json.dumps(event.payload, sort_keys=True, default=str)
        except (TypeError, ValueError):
            _LOGGER.exception(
                "audit payload not JSON-serializable (event_type=%r) ; storing repr() as fallback",
                event.event_type,
            )
            payload_json = json.dumps({"_unserializable_repr": repr(event.payload)})
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, event_type, payload_json, version) VALUES (?, ?, ?, ?)",
                (event.ts, event.event_type, payload_json, event.version),
            )


# ─── Module-level singleton ──────────────────────────────────────────────────


class _DefaultLoggerHolder:
    """Holder for the process-wide :class:`AuditLogger`.

    Class attributes (mutable) avoid the ``global`` statement while keeping
    a single canonical instance per process. Synchronized via :attr:`lock`.
    """

    instance: AuditLogger | None = None
    lock: threading.Lock = threading.Lock()


def get_default_logger() -> AuditLogger:
    """Return the process-wide :class:`AuditLogger`, starting it lazily."""
    with _DefaultLoggerHolder.lock:
        if _DefaultLoggerHolder.instance is None:
            _DefaultLoggerHolder.instance = AuditLogger()
            _DefaultLoggerHolder.instance.start()
        return _DefaultLoggerHolder.instance


def audit(event_type: str, payload: Mapping[str, Any] | None = None) -> None:
    """Convenience wrapper : log via :func:`get_default_logger`.

    This is the call site used throughout the bot's main loop. The default
    logger is started on first call and remains alive until the process
    exits or :func:`shutdown_default_logger` is invoked.
    """
    get_default_logger().log(event_type, payload)


def flush_default_logger(*, timeout: float = _DEFAULT_FLUSH_TIMEOUT) -> bool:
    """Flush the singleton, if any. Returns ``True`` if drained or absent."""
    with _DefaultLoggerHolder.lock:
        logger = _DefaultLoggerHolder.instance
    if logger is None:
        return True
    return logger.flush(timeout=timeout)


def shutdown_default_logger(*, timeout: float = _DEFAULT_STOP_TIMEOUT) -> None:
    """Stop and dispose the singleton (idempotent)."""
    with _DefaultLoggerHolder.lock:
        logger = _DefaultLoggerHolder.instance
        _DefaultLoggerHolder.instance = None
    if logger is not None:
        logger.stop(timeout=timeout)


# ─── Query helpers ───────────────────────────────────────────────────────────


def query_events(
    *,
    event_type: str | None = None,
    since: int | None = None,
    until: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read audit events with optional filters.

    Args:
        event_type: filter on ``event_type`` (equality). ``None`` = all.
        since: only events with ``ts >= since`` (epoch seconds).
        until: only events with ``ts < until`` (epoch seconds).
        limit: max rows returned, ordered most-recent first.

    Returns:
        A list of dicts ``{"id", "ts", "event_type", "payload", "version"}``
        with ``payload`` already JSON-decoded for convenience.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if event_type is not None:
        clauses.append("event_type = ?")
        params.append(event_type)
    if since is not None:
        clauses.append("ts >= ?")
        params.append(since)
    if until is not None:
        clauses.append("ts < ?")
        params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    # The dynamic part of the SQL (``where``) is built from a closed set of
    # constant fragments above ; user-supplied values are bound via parameters
    # only. The ruff S608 / bandit B608 warnings are therefore false positives.
    sql = (
        "SELECT id, ts, event_type, payload_json, version "  # noqa: S608  # nosec B608
        f"FROM audit_log{where} "
        "ORDER BY ts DESC, id DESC LIMIT ?"
    )
    params.append(limit)
    rows = database.query_all(sql, tuple(params))
    return [
        {
            "id": row["id"],
            "ts": row["ts"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "version": row["version"],
        }
        for row in rows
    ]


def purge_older_than(days: int, *, now: int | None = None) -> int:
    """Delete audit rows older than ``days`` days.

    Args:
        days: retention window. Must be ≥ 0. Doc 05 §"Audit trail JSON 30j
            queryable" recommends 30.
        now: epoch seconds reference (default: ``time.time()``). Tests
            inject this to make purge deterministic.

    Returns:
        Number of rows deleted.
    """
    if days < 0:
        msg = f"retention days must be >= 0, got {days}"
        raise ValueError(msg)
    cutoff = (now if now is not None else int(time.time())) - days * 86_400
    with database.transaction() as conn:
        cur = conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
        deleted: int = cur.rowcount
    return deleted
