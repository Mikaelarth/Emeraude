"""SQL migration runner — applies versioned schema changes idempotently.

Migrations are plain ``.sql`` files in this directory, named ``NNN_descr.sql``
where ``NNN`` is a zero-padded three-digit integer (``001``, ``002``, …) and
``descr`` is a short snake_case description. They are applied in numeric
order on the first connection of any process.

Each migration file MUST end with::

    INSERT OR IGNORE INTO schema_version (version, name) VALUES (N, 'descr');

so that re-applying the same SQL file is a no-op (idempotency by design,
not by runner ceremony).

The bootstrap step ensures ``schema_version`` exists before any user
migration runs.

SQLite version constraint
=========================

Migrations target **SQLite >= 3.7** (the version bundled with Android 4.0,
API 14, far below our ``android.minapi = 24``). Specifically :

* No ``STRICT`` table option (added SQLite 3.37.0, Nov 2021 = Android 14+).
  Iter #75 removed ``) STRICT;`` clauses from the schema after observing
  ``sqlite3.OperationalError: near "STRICT": syntax error`` on Android 10
  (SQLite 3.22) and on the AOSP API 30 emulator (SQLite 3.28). Type
  discipline is enforced at the Python layer (mypy strict + explicit
  Decimal/int conversions in the data-access modules), not by SQLite.
* No ``RETURNING`` (added 3.35).
* No ``IIF`` / ``UPSERT`` extensions beyond ``INSERT OR IGNORE``.

If you ever need a 3.37+ feature, gate it behind a runtime version check
and provide a fallback SQL path — never assume a recent SQLite at runtime.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Final

_LOGGER = logging.getLogger(__name__)

_MIGRATIONS_DIR: Final[Path] = Path(__file__).parent
_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"^(\d{3,})_(\w+)\.sql$")

_BOOTSTRAP_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    applied_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
"""


def _list_migrations() -> list[tuple[int, str, Path]]:
    """Return migrations sorted by version: ``[(version, name, path), ...]``."""
    migrations: list[tuple[int, str, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if not match:
            _LOGGER.warning("Ignoring file with non-conformant name: %s", path.name)
            continue
        version = int(match.group(1))
        name = match.group(2)
        migrations.append((version, name, path))
    migrations.sort(key=lambda m: m[0])
    return migrations


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of migration versions already applied on ``conn``."""
    cur = conn.execute("SELECT version FROM schema_version")
    return {int(row[0]) for row in cur.fetchall()}


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations in numeric order.

    Returns the list of versions newly applied (empty if up-to-date).

    Raises:
        sqlite3.DatabaseError: if a migration fails. The migration is rolled
            back; the schema_version row is not inserted, so the next run
            retries the same migration.
    """
    conn.executescript(_BOOTSTRAP_SQL)

    already_applied = applied_versions(conn)
    newly_applied: list[int] = []

    for version, name, path in _list_migrations():
        if version in already_applied:
            continue

        sql = path.read_text(encoding="utf-8")
        _LOGGER.info("Applying migration %03d: %s", version, name)
        try:
            conn.executescript(sql)
        except sqlite3.DatabaseError:
            _LOGGER.exception("Migration %03d (%s) failed", version, name)
            raise

        # Sanity check: the migration must self-record in schema_version.
        if version not in applied_versions(conn):
            # The "SQL" in this error message is documentation, not an
            # actual query (the bandit/ruff S608 warning is suppressed
            # explicitly on the offending line below).
            msg = (
                f"Migration {version:03d} ({name}) ran but did not record itself "  # noqa: S608
                f"in schema_version. Each .sql file must end with "
                f"INSERT OR IGNORE INTO schema_version VALUES ({version}, '{name}');"
            )
            raise RuntimeError(msg)

        newly_applied.append(version)

    return newly_applied
