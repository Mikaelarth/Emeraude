"""Integration tests for emeraude.infra.audit under concurrency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from emeraude.infra import audit, database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    database.close_thread_connection()
    return tmp_path / "emeraude.db"


@pytest.mark.integration
def test_high_volume_async_log_persists_all_events(fresh_db: Path) -> None:
    """Every enqueued event survives if the queue is large enough.

    With queue_maxsize > N*K, no drop should occur and the row count must
    match exactly.
    """
    n_threads = 8
    events_per_thread = 50
    total = n_threads * events_per_thread

    logger = audit.AuditLogger(queue_maxsize=total + 100)
    logger.start()

    def worker(thread_id: int) -> None:
        try:
            for i in range(events_per_thread):
                logger.log("CONCURRENT", {"tid": thread_id, "i": i})
        finally:
            database.close_thread_connection()

    try:
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker, t) for t in range(n_threads)]
            for fut in as_completed(futures):
                fut.result()

        assert logger.flush(timeout=10.0)
        assert logger.dropped_events == 0
    finally:
        logger.stop(timeout=5.0)

    rows = audit.query_events(limit=total + 10)
    assert len(rows) == total, f"Expected {total} events, got {len(rows)}"


@pytest.mark.integration
def test_concurrent_sync_loggers_no_lost_events(fresh_db: Path) -> None:
    """Multiple sync loggers from multiple threads — all writes serialize."""
    n_threads = 6
    events_per_thread = 30

    def worker() -> None:
        try:
            local_logger = audit.AuditLogger(sync=True)
            for i in range(events_per_thread):
                local_logger.log("SYNC_CONCURRENT", {"i": i})
        finally:
            database.close_thread_connection()

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(worker) for _ in range(n_threads)]
        for fut in as_completed(futures):
            fut.result()

    rows = audit.query_events(limit=1000)
    assert len(rows) == n_threads * events_per_thread


@pytest.mark.integration
def test_async_logger_survives_write_failure(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad payload must not kill the worker — subsequent events still go through.

    We monkey-patch the writer to raise on the first call only, then
    confirm the second event lands.
    """
    logger = audit.AuditLogger()
    logger.start()

    original_write = logger._write
    raised = {"once": False}

    def flaky_write(event: audit.AuditEvent) -> None:
        if not raised["once"]:
            raised["once"] = True
            msg = "simulated write failure"
            raise RuntimeError(msg)
        original_write(event)

    monkeypatch.setattr(logger, "_write", flaky_write)

    try:
        logger.log("FIRST", {})  # this one will fail
        logger.log("SECOND", {})  # this one must succeed
        assert logger.flush(timeout=5.0)
    finally:
        logger.stop(timeout=5.0)

    rows = audit.query_events()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "SECOND"
    assert raised["once"] is True
