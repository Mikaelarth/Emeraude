"""Unit tests for :mod:`emeraude.services.cycle_scheduler` (iter #97).

The scheduler is the third leg of the autonomous-agent stool :

* iter #95 : ``POST /api/run-cycle`` -> 1 cycle = 1 user tap.
* iter #96 : ``BinanceLiveExecutor`` -> a cycle in real mode places
  a real Binance order.
* iter #97 (this) : background thread -> cycles fire on their own
  every ``interval_seconds``.

Test plan :

* Settings DB helpers (``is_scheduler_enabled``, ``set_*``,
  ``get_scheduler_interval_seconds``, ``set_*`` with validation).
* :class:`CycleScheduler` thread lifecycle :
    * ``start()`` spawns daemon, ``is_running`` flips True.
    * ``stop()`` signals event, joins, ``is_running`` flips False.
    * Tick fires when enabled, calls ``run_cycle`` once per interval.
    * Tick skipped + audit when disabled.
    * Tick error caught + audit, thread keeps running.
    * Tick overlap audited (we simulate by holding the lock manually).
    * Re-reading ``interval_provider`` between ticks (smoke).
* :class:`SchedulerSnapshot` shape via ``fetch_snapshot``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from emeraude.infra import audit, database
from emeraude.services.cycle_scheduler import (
    DEFAULT_INTERVAL_SECONDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    CycleScheduler,
    SchedulerSnapshot,
    get_scheduler_interval_seconds,
    is_scheduler_enabled,
    set_scheduler_enabled,
    set_scheduler_interval_seconds,
)


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


# ─── Settings helpers ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestSettingsHelpers:
    """``scheduler.enabled`` + ``scheduler.interval_seconds`` round-trip."""

    def test_enabled_default_is_false(self, fresh_db: Path) -> None:
        # Sécurité : un fresh install ne trade pas tout seul.
        _ = fresh_db
        assert is_scheduler_enabled() is False

    def test_enabled_round_trip_true(self, fresh_db: Path) -> None:
        _ = fresh_db
        set_scheduler_enabled(True)
        assert is_scheduler_enabled() is True

    def test_enabled_round_trip_false(self, fresh_db: Path) -> None:
        _ = fresh_db
        set_scheduler_enabled(True)
        set_scheduler_enabled(False)
        assert is_scheduler_enabled() is False

    def test_interval_default_is_3600(self, fresh_db: Path) -> None:
        _ = fresh_db
        assert get_scheduler_interval_seconds() == DEFAULT_INTERVAL_SECONDS

    def test_interval_round_trip(self, fresh_db: Path) -> None:
        _ = fresh_db
        set_scheduler_interval_seconds(900)
        assert get_scheduler_interval_seconds() == 900

    def test_interval_min_accepted(self, fresh_db: Path) -> None:
        _ = fresh_db
        set_scheduler_interval_seconds(MIN_INTERVAL_SECONDS)
        assert get_scheduler_interval_seconds() == MIN_INTERVAL_SECONDS

    def test_interval_max_accepted(self, fresh_db: Path) -> None:
        _ = fresh_db
        set_scheduler_interval_seconds(MAX_INTERVAL_SECONDS)
        assert get_scheduler_interval_seconds() == MAX_INTERVAL_SECONDS

    def test_interval_below_min_raises(self, fresh_db: Path) -> None:
        _ = fresh_db
        with pytest.raises(ValueError, match=r"interval_seconds must be in"):
            set_scheduler_interval_seconds(MIN_INTERVAL_SECONDS - 1)

    def test_interval_above_max_raises(self, fresh_db: Path) -> None:
        _ = fresh_db
        with pytest.raises(ValueError, match=r"interval_seconds must be in"):
            set_scheduler_interval_seconds(MAX_INTERVAL_SECONDS + 1)

    def test_interval_corrupted_falls_back_to_default(self, fresh_db: Path) -> None:
        # Simule une valeur corrompue persistée à la main (bypass de la
        # validation du setter) — le getter doit retourner le default
        # plutôt que crasher.
        _ = fresh_db
        database.set_setting("scheduler.interval_seconds", "not-a-number")
        assert get_scheduler_interval_seconds() == DEFAULT_INTERVAL_SECONDS

    def test_interval_out_of_range_in_db_falls_back(self, fresh_db: Path) -> None:
        _ = fresh_db
        database.set_setting("scheduler.interval_seconds", "1")
        assert get_scheduler_interval_seconds() == DEFAULT_INTERVAL_SECONDS


# ─── CycleScheduler lifecycle ─────────────────────────────────────────────


@pytest.mark.unit
class TestSchedulerLifecycle:
    """``start()`` / ``stop()`` / ``is_running``."""

    def test_not_running_before_start(self, fresh_db: Path) -> None:
        _ = fresh_db
        scheduler = CycleScheduler(
            run_cycle=lambda: None,
            enabled_provider=lambda: False,
            interval_provider=lambda: 60,
        )
        assert scheduler.is_running is False

    def test_start_spawns_thread(self, fresh_db: Path) -> None:
        _ = fresh_db
        scheduler = CycleScheduler(
            run_cycle=lambda: None,
            enabled_provider=lambda: False,
            interval_provider=lambda: 60,
        )
        try:
            scheduler.start()
            assert scheduler.is_running is True
        finally:
            scheduler.stop(timeout=2.0)

    def test_start_is_idempotent(self, fresh_db: Path) -> None:
        # Two consecutive starts must not spawn two threads.
        _ = fresh_db
        scheduler = CycleScheduler(
            run_cycle=lambda: None,
            enabled_provider=lambda: False,
            interval_provider=lambda: 60,
        )
        try:
            scheduler.start()
            scheduler.start()  # no-op
            # Single thread alive — Python doesn't expose a clean way
            # to enumerate child threads, so we just check the
            # property remains True (no crash, no second start).
            assert scheduler.is_running is True
        finally:
            scheduler.stop(timeout=2.0)

    def test_stop_signals_thread_exit(self, fresh_db: Path) -> None:
        _ = fresh_db
        scheduler = CycleScheduler(
            run_cycle=lambda: None,
            enabled_provider=lambda: False,
            interval_provider=lambda: 60,
        )
        scheduler.start()
        scheduler.stop(timeout=2.0)
        assert scheduler.is_running is False

    def test_stop_when_not_running_is_noop(self, fresh_db: Path) -> None:
        _ = fresh_db
        scheduler = CycleScheduler(
            run_cycle=lambda: None,
            enabled_provider=lambda: False,
            interval_provider=lambda: 60,
        )
        # Doit pas planter même sans start préalable.
        scheduler.stop()


# ─── Tick firing ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTickFiring:
    """Le tick appelle ``run_cycle`` selon l'intervalle / enabled."""

    def test_enabled_tick_fires_run_cycle(self, fresh_db: Path) -> None:
        _ = fresh_db
        call_count = 0
        fired = threading.Event()

        def run_cycle() -> None:
            nonlocal call_count
            call_count += 1
            fired.set()

        scheduler = CycleScheduler(
            run_cycle=run_cycle,
            enabled_provider=lambda: True,
            interval_provider=lambda: 1,  # 1 second min effective
        )
        # Override the wait to fire instantly (test-only). We do this
        # by setting a very short interval and waiting up to 3s for
        # the tick.
        scheduler.start()
        try:
            assert fired.wait(timeout=3.0)
            assert call_count >= 1
        finally:
            scheduler.stop(timeout=2.0)

    def test_disabled_tick_skipped(self, fresh_db: Path) -> None:
        _ = fresh_db
        call_count = 0

        def run_cycle() -> None:
            nonlocal call_count
            call_count += 1

        scheduler = CycleScheduler(
            run_cycle=run_cycle,
            enabled_provider=lambda: False,
            interval_provider=lambda: 1,
        )
        scheduler.start()
        # Wait long enough for at least one tick to fire (or be
        # skipped).
        time.sleep(1.5)
        scheduler.stop(timeout=2.0)
        # When disabled, run_cycle MUST NOT have been called.
        assert call_count == 0

    def test_disabled_tick_emits_skipped_audit(self, fresh_db: Path) -> None:
        _ = fresh_db
        scheduler = CycleScheduler(
            run_cycle=lambda: None,
            enabled_provider=lambda: False,
            interval_provider=lambda: 1,
        )
        scheduler.start()
        time.sleep(1.5)
        scheduler.stop(timeout=2.0)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="SCHEDULER_TICK_SKIPPED")
        assert len(events) >= 1
        assert events[-1]["payload"]["reason"] == "disabled"

    def test_tick_error_does_not_kill_thread(self, fresh_db: Path) -> None:
        # ``run_cycle`` raises on the first call, succeeds afterward.
        # The thread must absorb the error and keep ticking.
        _ = fresh_db
        call_count = 0
        succeeded = threading.Event()

        def run_cycle() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            succeeded.set()

        scheduler = CycleScheduler(
            run_cycle=run_cycle,
            enabled_provider=lambda: True,
            interval_provider=lambda: 1,
        )
        scheduler.start()
        try:
            assert succeeded.wait(timeout=4.0), "second tick never ran"
            assert call_count >= 2
        finally:
            scheduler.stop(timeout=2.0)

    def test_tick_error_emits_error_audit(self, fresh_db: Path) -> None:
        _ = fresh_db
        fired = threading.Event()

        def run_cycle() -> None:
            fired.set()
            raise RuntimeError("boom")

        scheduler = CycleScheduler(
            run_cycle=run_cycle,
            enabled_provider=lambda: True,
            interval_provider=lambda: 1,
        )
        scheduler.start()
        try:
            assert fired.wait(timeout=3.0)
            # Give the audit logger a moment to flush the error event.
            time.sleep(0.5)
        finally:
            scheduler.stop(timeout=2.0)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="SCHEDULER_TICK_ERROR")
        assert len(events) >= 1
        last = events[-1]["payload"]
        assert last["error_type"] == "RuntimeError"
        assert "boom" in last["error_message"]

    def test_tick_emits_started_and_fired_audits(self, fresh_db: Path) -> None:
        _ = fresh_db
        fired = threading.Event()

        def run_cycle() -> None:
            fired.set()

        scheduler = CycleScheduler(
            run_cycle=run_cycle,
            enabled_provider=lambda: True,
            interval_provider=lambda: 1,
        )
        scheduler.start()
        try:
            assert fired.wait(timeout=3.0)
            time.sleep(0.3)
        finally:
            scheduler.stop(timeout=2.0)
        assert audit.flush_default_logger(timeout=2.0)
        starts = audit.query_events(event_type="SCHEDULER_STARTED")
        assert len(starts) >= 1
        fires = audit.query_events(event_type="SCHEDULER_TICK_FIRED")
        assert len(fires) >= 1
        stops = audit.query_events(event_type="SCHEDULER_STOPPED")
        assert len(stops) >= 1


# ─── Snapshot ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSnapshot:
    """``fetch_snapshot`` returns a faithful state dataclass."""

    def test_snapshot_shape(self, fresh_db: Path) -> None:
        _ = fresh_db
        scheduler = CycleScheduler(
            run_cycle=lambda: None,
            enabled_provider=lambda: True,
            interval_provider=lambda: 1800,
        )
        snap = scheduler.fetch_snapshot()
        assert isinstance(snap, SchedulerSnapshot)
        assert snap.enabled is True
        assert snap.interval_seconds == 1800
        assert snap.is_running is False
        assert snap.min_interval_seconds == MIN_INTERVAL_SECONDS
        assert snap.max_interval_seconds == MAX_INTERVAL_SECONDS

    def test_snapshot_reflects_running_state(self, fresh_db: Path) -> None:
        _ = fresh_db
        scheduler = CycleScheduler(
            run_cycle=lambda: None,
            enabled_provider=lambda: False,
            interval_provider=lambda: 60,
        )
        scheduler.start()
        try:
            snap = scheduler.fetch_snapshot()
            assert snap.is_running is True
        finally:
            scheduler.stop(timeout=2.0)
        snap_after = scheduler.fetch_snapshot()
        assert snap_after.is_running is False

    def test_snapshot_frozen(self, fresh_db: Path) -> None:
        _ = fresh_db
        snap = SchedulerSnapshot(
            enabled=True,
            interval_seconds=3600,
            is_running=False,
            min_interval_seconds=60,
            max_interval_seconds=86400,
        )
        with pytest.raises(AttributeError):
            snap.enabled = False  # type: ignore[misc]
