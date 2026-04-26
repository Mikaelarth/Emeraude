"""Atomic SQLite backup, listing, restore, retention.

Implements doc 09 §"Backup atomique de la DB" : every state-bearing
component (regime memory, bandit posteriors, position history, audit
trail, champion lifecycle) lives in a single SQLite file. A corruption
or accidental wipe is catastrophic — this module exists so the user
can take and restore atomic snapshots without ever stopping the bot.

Implementation notes :

* :meth:`BackupService.create` uses :meth:`sqlite3.Connection.backup`,
  the official **Online Backup API**. It copies pages under short
  reader locks while writers can keep going (WAL mode). The
  destination file is fully self-contained — no WAL companion needed.
* :meth:`BackupService.restore` is a hard swap : it closes the
  thread-local connection, atomically replaces the active DB file
  with the backup via :meth:`pathlib.Path.replace`, and lets the
  next :func:`database.get_connection` call re-bootstrap the
  connection + re-apply migrations. The swap is filesystem-atomic
  on POSIX and Win32.
* :meth:`BackupService.prune` keeps the most recent ``retention``
  *automatic* backups (label = ``"auto"``) and never deletes
  manually-named ones (``label != "auto"``). The user's explicit
  ``my_pre_release.db`` survives forever unless explicitly removed.
* No compression, no cloud upload — anti-rule A6 (no cloud without
  explicit user opt-in). Future ``services.cloud_sync`` would be
  the place for that, behind an opt-in toggle.

Filename convention : ``emeraude-{epoch}-{label}.db``. Epoch (not
ISO date) keeps lexicographic order = chronological order, which
makes ``list_backups()`` trivial to sort.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from emeraude.infra import audit, database, paths

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_DEFAULT_RETENTION: Final[int] = 7
_DEFAULT_AUTO_LABEL: Final[str] = "auto"
# Legal label characters : ASCII letters, digits, dash, underscore. Keeps
# filenames cross-platform and unambiguous to parse back.
_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")
# Reverse parser : ``emeraude-<epoch>-<label>.db`` where epoch is digits and
# label is the same restricted alphabet.
_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"^emeraude-(\d+)-([A-Za-z0-9_-]+)\.db$")

_AUDIT_CREATED: Final[str] = "BACKUP_CREATED"
_AUDIT_RESTORED: Final[str] = "BACKUP_RESTORED"
_AUDIT_PRUNED: Final[str] = "BACKUP_PRUNED"


# ─── Record type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BackupRecord:
    """One backup file on disk.

    Attributes:
        path: absolute path to the ``.db`` snapshot.
        epoch: epoch-second timestamp parsed from the filename.
        label: human label parsed from the filename. ``"auto"`` for
            scheduled backups, free-form for manual ones.
        size_bytes: file size at the time of listing.
    """

    path: Path
    epoch: int
    label: str
    size_bytes: int

    @property
    def is_auto(self) -> bool:
        """True iff this backup was produced by the automatic scheduler."""
        return self.label == _DEFAULT_AUTO_LABEL


# ─── Service ────────────────────────────────────────────────────────────────


class BackupService:
    """Atomic backups of the active SQLite database.

    Construct once at process start (or per call — the service holds no
    mutable state). Defaults pull from :mod:`emeraude.infra.paths` so the
    service Just Works in production ; tests inject a custom
    ``backup_dir`` and ``database_path`` for isolation.
    """

    def __init__(
        self,
        *,
        backup_dir: Path | None = None,
        database_path: Path | None = None,
        retention: int = _DEFAULT_RETENTION,
    ) -> None:
        """Wire the service.

        Args:
            backup_dir: directory where snapshots live. Defaults to
                :func:`paths.backups_dir`.
            database_path: source DB path. Defaults to
                :func:`paths.database_path`.
            retention: number of *auto* backups to keep on
                :meth:`prune`. Manually-labeled backups are never
                pruned. Must be ``>= 1``.
        """
        if retention < 1:
            msg = f"retention must be >= 1, got {retention}"
            raise ValueError(msg)
        self._backup_dir = backup_dir if backup_dir is not None else paths.backups_dir()
        # ``paths.backups_dir`` already mkdirs ; an injected directory
        # might not, so we ensure it here. Idempotent on the default path.
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._database_path = database_path if database_path is not None else paths.database_path()
        self._retention = retention

    # ─── Read ───────────────────────────────────────────────────────────────

    def list_backups(self) -> list[BackupRecord]:
        """Return all backup files, most recent first.

        Files that do not match the canonical naming pattern are
        silently skipped (e.g. user-dropped ``.bak`` files). They
        are not deleted by :meth:`prune` either.
        """
        records: list[BackupRecord] = []
        for path in self._backup_dir.glob("emeraude-*-*.db"):
            match = _FILENAME_RE.match(path.name)
            if match is None:
                continue
            epoch = int(match.group(1))
            label = match.group(2)
            try:
                size = path.stat().st_size
            except OSError:  # pragma: no cover  (file vanished mid-listing)
                continue
            records.append(
                BackupRecord(path=path, epoch=epoch, label=label, size_bytes=size),
            )
        records.sort(key=lambda r: r.epoch, reverse=True)
        return records

    # ─── Create ─────────────────────────────────────────────────────────────

    def create(self, *, label: str = _DEFAULT_AUTO_LABEL, now: int | None = None) -> BackupRecord:
        """Atomically copy the active DB to a timestamped snapshot.

        Args:
            label: free-form tag baked into the filename. Defaults to
                ``"auto"``. Manual labels (anything else) survive
                :meth:`prune` indefinitely.
            now: epoch-second timestamp. Defaults to ``time.time()``.

        Returns:
            The freshly written :class:`BackupRecord`.

        Raises:
            ValueError: if ``label`` contains characters outside
                ``[A-Za-z0-9_-]`` (would break the filename parser).
        """
        if not _LABEL_RE.match(label):
            msg = f"label must match {_LABEL_RE.pattern}, got {label!r}"
            raise ValueError(msg)

        ts = now if now is not None else int(time.time())
        target = self._backup_dir / f"emeraude-{ts}-{label}.db"

        source_conn = database.get_connection()
        # Sentinel : read once before touching the destination so we
        # surface "source missing" before creating an empty file.
        if not self._database_path.exists():  # pragma: no cover  (defensive)
            msg = f"source database not found at {self._database_path}"
            raise FileNotFoundError(msg)

        target_conn = sqlite3.connect(str(target))
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()

        size = target.stat().st_size
        record = BackupRecord(path=target, epoch=ts, label=label, size_bytes=size)

        audit.audit(
            _AUDIT_CREATED,
            {
                "path": str(target),
                "epoch": ts,
                "label": label,
                "size_bytes": size,
            },
        )
        return record

    # ─── Restore ────────────────────────────────────────────────────────────

    def restore(self, backup: Path | BackupRecord) -> None:
        """Restore the active DB from a backup file.

        Uses the **inverse** of :meth:`sqlite3.Connection.backup` : we
        open the snapshot read-only and copy its pages *into* the live
        connection. This avoids any filesystem-level swap (which would
        race with the audit worker's separate thread-local connection
        on Windows) while still being atomic from a transactional
        viewpoint — concurrent readers see either the old or the new
        full state, never a torn mix.

        Args:
            backup: either a :class:`BackupRecord` or a raw
                :class:`pathlib.Path` to a ``.db`` snapshot.

        Raises:
            FileNotFoundError: if the backup path does not exist.
        """
        path = backup.path if isinstance(backup, BackupRecord) else backup
        if not path.exists():
            msg = f"backup file not found: {path}"
            raise FileNotFoundError(msg)

        target_conn = database.get_connection()
        # Connect to the snapshot read-only ; the URI form lets us
        # promise read-only access without needing the snapshot's
        # writable bit.
        source_conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            source_conn.backup(target_conn)
        finally:
            source_conn.close()

        audit.audit(
            _AUDIT_RESTORED,
            {
                "from": str(path),
                "to": str(self._database_path),
            },
        )

    # ─── Prune ──────────────────────────────────────────────────────────────

    def prune(self) -> list[BackupRecord]:
        """Delete *auto* backups beyond :attr:`retention`.

        Manually-labeled backups (``label != "auto"``) are left alone
        regardless of count. Returns the list of deleted records (for
        audit / test introspection).
        """
        all_backups = self.list_backups()
        auto_backups = [r for r in all_backups if r.is_auto]
        # Keep the most recent ``retention`` ; everything older goes.
        to_delete = auto_backups[self._retention :]

        deleted: list[BackupRecord] = []
        for record in to_delete:
            try:
                record.path.unlink()
            except OSError:  # pragma: no cover  (race or perms)
                _LOGGER.exception("failed to delete backup %s", record.path)
                continue
            deleted.append(record)

        if deleted:
            audit.audit(
                _AUDIT_PRUNED,
                {
                    "deleted_count": len(deleted),
                    "retention": self._retention,
                    "deleted_paths": [str(r.path) for r in deleted],
                },
            )
        return deleted
