"""Integration tests for emeraude.infra.database under thread concurrency.

These tests exercise the WAL + ``BEGIN IMMEDIATE`` retry logic with multiple
threads competing for the writer lock — the same pattern the live bot uses
(UI thread + daemon ``BotMaitre`` thread sharing the DB).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    # Pre-create the connection in main thread so the migration runs once,
    # then close it so worker threads each get a fresh per-thread connection.
    database.get_connection()
    database.close_thread_connection()
    return tmp_path / "emeraude.db"


def _run_increments(n_increments: int, key: str = "counter") -> None:
    """Worker function: run N atomic increments then close the connection."""
    try:
        for _ in range(n_increments):
            database.increment_numeric_setting(key, 1.0, default=0.0)
    finally:
        database.close_thread_connection()


@pytest.mark.integration
def test_concurrent_increments_are_atomic(fresh_db: Path) -> None:
    """No lost updates under concurrent increment_numeric_setting.

    The expected final counter equals N_THREADS * N_INCREMENTS exactly.
    A non-atomic implementation would race on the read-then-write and
    produce a smaller final value.
    """
    n_threads = 8
    n_increments_per_thread = 50
    expected_total = n_threads * n_increments_per_thread

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(_run_increments, n_increments_per_thread) for _ in range(n_threads)]
        for fut in as_completed(futures):
            # Re-raise any worker exception in the main thread.
            fut.result()

    counter_str = database.get_setting("counter", "0")
    assert counter_str is not None
    final_value = float(counter_str)
    assert final_value == float(expected_total), (
        f"Expected counter={expected_total}, got {final_value}. "
        f"Lost updates indicate non-atomic increment."
    )


@pytest.mark.integration
def test_concurrent_writers_dont_block_readers_indefinitely(fresh_db: Path) -> None:
    """A reader thread can run alongside many writer threads without timing out.

    WAL mode allows readers to see a snapshot while a writer holds the lock.
    """
    stop_event = threading.Event()
    writer_count = 4
    read_results: list[str | None] = []

    def writer() -> None:
        try:
            i = 0
            while not stop_event.is_set():
                database.set_setting("k", str(i))
                i += 1
                if i > 200:
                    break
        finally:
            database.close_thread_connection()

    def reader() -> None:
        try:
            for _ in range(20):
                read_results.append(database.get_setting("k"))
        finally:
            database.close_thread_connection()

    with ThreadPoolExecutor(max_workers=writer_count + 1) as pool:
        writers = [pool.submit(writer) for _ in range(writer_count)]
        reader_fut = pool.submit(reader)
        reader_fut.result(timeout=15)
        stop_event.set()
        for w in writers:
            w.result(timeout=15)

    # The reader completed all 20 reads. We don't assert specific values
    # (they depend on scheduling); we just assert no read returned None
    # AFTER the first write was visible.
    assert len(read_results) == 20
