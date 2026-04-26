"""Unit tests for emeraude.agent.governance.champion_lifecycle."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.governance.champion_lifecycle import (
    ChampionLifecycle,
    ChampionRecord,
    ChampionState,
)
from emeraude.infra import audit, database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


# ─── Migration ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMigration:
    def test_table_exists(self, fresh_db: Path) -> None:
        row = database.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='champion_history'"
        )
        assert row is not None

    def test_table_columns(self, fresh_db: Path) -> None:
        rows = database.query_all("PRAGMA table_info(champion_history)")
        col_names = {row["name"] for row in rows}
        assert col_names == {
            "id",
            "champion_id",
            "state",
            "promoted_at",
            "expired_at",
            "sharpe_walk_forward",
            "sharpe_live",
            "expiry_reason",
            "parameters_json",
        }


# ─── Empty DB ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEmpty:
    def test_current_returns_none_when_no_champion(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        assert cl.current() is None

    def test_history_empty(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        assert cl.history() == []


# ─── promote() ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPromote:
    def test_first_promotion_creates_active(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        record = cl.promote(
            "champ_v1",
            parameters={"min_score": 45},
            sharpe_walk_forward=Decimal("0.93"),
        )

        assert record.state == ChampionState.ACTIVE
        assert record.champion_id == "champ_v1"
        assert record.parameters == {"min_score": 45}
        assert record.sharpe_walk_forward == Decimal("0.93")
        assert record.expired_at is None

    def test_current_returns_promoted_champion(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        cl.promote("champ_v1", parameters={"x": 1})
        current = cl.current()
        assert current is not None
        assert current.champion_id == "champ_v1"
        assert current.state == ChampionState.ACTIVE

    def test_second_promotion_expires_first(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        first = cl.promote("champ_v1")
        second = cl.promote("champ_v2")

        # The current ACTIVE is now v2.
        current = cl.current()
        assert current is not None
        assert current.id == second.id
        assert current.champion_id == "champ_v2"

        # The previous record is no longer ACTIVE-without-expired :
        # its expired_at is set even though state remained ACTIVE for audit clarity.
        history = cl.history()
        old = next(r for r in history if r.id == first.id)
        assert old.expired_at is not None

    def test_at_most_one_active_invariant(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        for i in range(5):
            cl.promote(f"champ_v{i}")

        # Only ONE row has state=ACTIVE AND expired_at IS NULL.
        rows = database.query_all(
            "SELECT id FROM champion_history WHERE state = ? AND expired_at IS NULL",
            (ChampionState.ACTIVE.value,),
        )
        assert len(rows) == 1

    def test_promote_emits_audit_event(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        cl.promote("champ_v1", sharpe_walk_forward=Decimal("1.5"))

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="CHAMPION_PROMOTED")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["champion_id"] == "champ_v1"
        assert payload["sharpe_walk_forward"] == "1.5"

        audit.shutdown_default_logger()


# ─── transition() ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTransition:
    def test_no_active_raises(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        with pytest.raises(RuntimeError, match="no ACTIVE champion"):
            cl.transition(ChampionState.SUSPECT, reason="test")

    def test_active_to_suspect(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        cl.promote("champ_v1")
        cl.transition(ChampionState.SUSPECT, reason="re-validation failed")

        current = cl.current()
        # current() looks for ACTIVE only ; after SUSPECT there is no current.
        assert current is None

        # The record is still there with the new state.
        history = cl.history()
        assert history[0].state == ChampionState.SUSPECT

    def test_suspect_to_expired_sets_expired_at(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        cl.promote("champ_v1")
        cl.transition(ChampionState.SUSPECT, reason="first miss")
        # Re-promote to ACTIVE since transition only operates on ACTIVE.
        # In practice an EXPIRED transition would happen via re-validation
        # logic ; here we test the EXPIRED branch directly with a fresh active.
        cl.promote("champ_v2")
        cl.transition(ChampionState.EXPIRED, reason="2nd consecutive miss")

        history = cl.history()
        v2 = next(r for r in history if r.champion_id == "champ_v2")
        assert v2.state == ChampionState.EXPIRED
        assert v2.expired_at is not None
        assert v2.expiry_reason == "2nd consecutive miss"

    def test_transition_emits_audit_event(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        cl.promote("champ_v1")
        cl.transition(ChampionState.SUSPECT, reason="drift detected")

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="CHAMPION_LIFECYCLE_TRANSITION")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["from"] == "ACTIVE"
        assert payload["to"] == "SUSPECT"
        assert payload["reason"] == "drift detected"
        assert payload["champion_id"] == "champ_v1"

        audit.shutdown_default_logger()


# ─── update_live_sharpe() ───────────────────────────────────────────────────


@pytest.mark.unit
class TestUpdateLiveSharpe:
    def test_updates_current_champion(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        cl.promote("champ_v1")
        cl.update_live_sharpe(Decimal("0.85"))

        current = cl.current()
        assert current is not None
        assert current.sharpe_live == Decimal("0.85")

    def test_no_active_raises(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        with pytest.raises(RuntimeError, match="no ACTIVE champion"):
            cl.update_live_sharpe(Decimal("1.0"))

    def test_does_not_emit_audit_event(self, fresh_db: Path) -> None:
        # update_live_sharpe is called periodically ; emitting per call
        # would saturate the audit trail. Verify silence.
        cl = ChampionLifecycle()
        cl.promote("champ_v1")
        # promote emits CHAMPION_PROMOTED but no LIFECYCLE_TRANSITION yet.
        cl.update_live_sharpe(Decimal("0.5"))

        assert audit.flush_default_logger(timeout=2.0)
        transitions = audit.query_events(event_type="CHAMPION_LIFECYCLE_TRANSITION")
        # update_live_sharpe must NOT generate a transition event.
        assert len(transitions) == 0

        audit.shutdown_default_logger()


# ─── history() ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHistory:
    def test_returns_most_recent_first(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        cl.promote("v1")
        cl.promote("v2")
        cl.promote("v3")

        history = cl.history()
        ids = [r.champion_id for r in history]
        assert ids == ["v3", "v2", "v1"]

    def test_respects_limit(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        for i in range(5):
            cl.promote(f"v{i}")

        assert len(cl.history(limit=2)) == 2

    def test_includes_expired_records(self, fresh_db: Path) -> None:
        cl = ChampionLifecycle()
        cl.promote("v1")
        cl.transition(ChampionState.EXPIRED, reason="test")
        cl.promote("v2")

        history = cl.history()
        assert {r.champion_id for r in history} == {"v1", "v2"}


# ─── ChampionRecord ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestChampionRecord:
    def test_default_parameters_dict(self) -> None:
        rec = ChampionRecord(
            id=1,
            champion_id="x",
            state=ChampionState.ACTIVE,
            promoted_at=0,
            expired_at=None,
            sharpe_walk_forward=None,
            sharpe_live=None,
            expiry_reason=None,
        )
        assert rec.parameters == {}

    def test_decimal_fields(self) -> None:
        rec = ChampionRecord(
            id=1,
            champion_id="x",
            state=ChampionState.ACTIVE,
            promoted_at=0,
            expired_at=None,
            sharpe_walk_forward=Decimal("1.5"),
            sharpe_live=Decimal("0.8"),
            expiry_reason=None,
        )
        assert isinstance(rec.sharpe_walk_forward, Decimal)
        assert isinstance(rec.sharpe_live, Decimal)


# ─── Enum invariants ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEnumInvariants:
    def test_four_states_exactly(self) -> None:
        assert set(ChampionState) == {
            ChampionState.ACTIVE,
            ChampionState.SUSPECT,
            ChampionState.EXPIRED,
            ChampionState.IN_VALIDATION,
        }
