"""SQLite WAL connection management — single source of truth for persistence.

Provides:

* :func:`get_connection` — per-thread connection with WAL, foreign keys, and
  busy_timeout enforced on every open.
* :func:`transaction` — context manager wrapping ``BEGIN IMMEDIATE`` with
  exponential-backoff retry on ``SQLITE_BUSY`` (up to 6 attempts).
* :func:`execute` / :func:`query_one` / :func:`query_all` — convenience
  wrappers for simple queries.
* :func:`get_setting` / :func:`set_setting` / :func:`increment_numeric_setting`
  — high-level access to the ``settings`` table; the latter is atomic under
  thread concurrency.

Migrations are applied lazily on the first :func:`get_connection` call per
thread — the operation is idempotent thanks to ``schema_version`` tracking,
so the cost is negligible after the first call.

Thread safety:
    Each thread holds its own connection in ``threading.local`` storage.
    SQLite WAL mode allows concurrent readers and a single writer; the
    ``BEGIN IMMEDIATE`` pattern in :func:`transaction` plus the retry loop
    serialize writers safely.

References:
    * Cahier des charges, doc 05 §"Persistance" et §"Atomic SQL via
      BEGIN IMMEDIATE + retry exponentiel".
    * SQLite WAL: https://www.sqlite.org/wal.html
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Final

from emeraude.infra import paths
from emeraude.infra.migrations import apply_migrations

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# Busy timeout : combien de millisecondes attendre un lock avant de lever
# SQLITE_BUSY. 5 secondes est un compromis raisonnable mobile / desktop.
_BUSY_TIMEOUT_MS: Final[int] = 5_000

# Backoff exponentiel pour BEGIN IMMEDIATE : 6 tentatives au total.
# Première à délai 0 (immédiate), puis 5 retries.
_RETRY_DELAYS_S: Final[tuple[float, ...]] = (0.0, 0.05, 0.1, 0.2, 0.5, 1.0)

# Pragmas appliqués à chaque nouvelle connexion. WAL est persistant (mode
# stocké dans le fichier DB), les autres sont par-connexion.
_INIT_PRAGMAS: Final[tuple[str, ...]] = (
    f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}",
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
)

_thread_local = threading.local()


# ─── Bas niveau : connexion ──────────────────────────────────────────────────


def _new_connection(db_path: Path) -> sqlite3.Connection:
    """Open and configure a fresh connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        timeout=_BUSY_TIMEOUT_MS / 1000,
        # autocommit mode ; nous gérons les transactions manuellement via
        # transaction() pour pouvoir utiliser BEGIN IMMEDIATE.
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    for pragma in _INIT_PRAGMAS:
        conn.execute(pragma)
    return conn


def get_connection() -> sqlite3.Connection:
    """Return the current thread's connection (creating + migrating on first call).

    The DB path is resolved via :func:`emeraude.infra.paths.database_path`
    (which honors the ``EMERAUDE_STORAGE_DIR`` override for tests).
    """
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = _new_connection(paths.database_path())
        apply_migrations(conn)
        _thread_local.conn = conn
    return conn


def close_thread_connection() -> None:
    """Close the current thread's connection if any. Idempotent."""
    conn: sqlite3.Connection | None = getattr(_thread_local, "conn", None)
    if conn is not None:
        conn.close()
        del _thread_local.conn


# ─── Transactions ────────────────────────────────────────────────────────────


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Context manager around ``BEGIN IMMEDIATE`` with retry on busy.

    Yields the active connection. Commits on normal exit, rolls back on any
    exception. Up to 6 attempts to acquire the write lock with exponential
    backoff (0, 50ms, 100ms, 200ms, 500ms, 1s).

    Raises:
        sqlite3.OperationalError: if the database remains locked after all
            retries (extremely unlikely in single-process SQLite WAL).
    """
    conn = get_connection()
    last_err: sqlite3.OperationalError | None = None

    for delay in _RETRY_DELAYS_S:
        if delay > 0:
            time.sleep(delay)
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            last_err = exc
    else:
        msg = f"BEGIN IMMEDIATE failed after {len(_RETRY_DELAYS_S)} attempts"
        raise sqlite3.OperationalError(msg) from last_err

    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        # BaseException catches KeyboardInterrupt / SystemExit too — we still
        # want to roll back the partial transaction in that case.
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover
            _LOGGER.exception("ROLLBACK failed (transaction may be inconsistent)")
        raise


# ─── Convenience wrappers ────────────────────────────────────────────────────


def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    """Run a single write statement inside an atomic transaction."""
    with transaction() as conn:
        conn.execute(sql, params)


def query_one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    """Execute a read query and return the first row, or ``None`` if empty."""
    conn = get_connection()
    cur = conn.execute(sql, params)
    row: sqlite3.Row | None = cur.fetchone()
    return row


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    """Execute a read query and return all rows."""
    conn = get_connection()
    cur = conn.execute(sql, params)
    return list(cur.fetchall())


# ─── Settings : API haut niveau ──────────────────────────────────────────────


def get_setting(key: str, default: str | None = None) -> str | None:
    """Read the value of ``key`` from the ``settings`` table.

    Returns ``default`` (default ``None``) if the key is absent.
    """
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return default
    value: str = row["value"]
    return value


def set_setting(key: str, value: str) -> None:
    """Insert or update a setting (atomic upsert).

    The ``updated_at`` column is refreshed on every write so that callers
    can detect staleness without external bookkeeping.
    """
    execute(
        "INSERT INTO settings (key, value, updated_at) "
        "VALUES (?, ?, strftime('%s', 'now')) "
        "ON CONFLICT(key) DO UPDATE SET "
        "  value = excluded.value, "
        "  updated_at = excluded.updated_at",
        (key, value),
    )


def increment_numeric_setting(key: str, delta: float, default: float = 0.0) -> float:
    """Atomically increment a numeric setting, returning the new value.

    Reads the current value (using ``default`` if absent), adds ``delta``,
    and writes back — all inside a single ``BEGIN IMMEDIATE`` transaction.
    Multiple concurrent threads are safe: SQLite WAL serializes writers.

    The setting is stored as TEXT (the column type) but parsed/written as
    a float; non-numeric current values raise ``ValueError``.

    Args:
        key: setting name.
        delta: amount to add (may be negative).
        default: assumed value when the key is absent.

    Returns:
        The new value after the increment.
    """
    with transaction() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        current = float(row["value"]) if row is not None else default
        new_value = current + delta
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES (?, ?, strftime('%s', 'now')) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  value = excluded.value, "
            "  updated_at = excluded.updated_at",
            (key, str(new_value)),
        )
    return new_value
