"""Pure-logic tests for the Journal types + formatter (no Kivy).

Cover :func:`format_event_row` and :func:`format_payload_summary`
plus container immutability. Runs everywhere (including headless
CI) since no widget instantiation happens.
"""

from __future__ import annotations

import pytest

from emeraude.services.journal_types import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_SUMMARY_MAX_LEN,
    JournalEventRow,
    JournalSnapshot,
    format_event_row,
    format_payload_summary,
)

# ─── Time label ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTimeLabel:
    def test_known_epoch_renders_utc_hms(self) -> None:
        # 0 = 1970-01-01T00:00:00Z -> "00:00:00".
        row = format_event_row(
            {"id": 1, "ts": 0, "event_type": "X", "payload": {}, "version": 1},
        )
        assert row.time_label == "00:00:00"

    def test_noon_epoch_renders_correctly(self) -> None:
        # 12*3600 = noon UTC -> "12:00:00".
        row = format_event_row(
            {"id": 1, "ts": 12 * 3600, "event_type": "X", "payload": {}, "version": 1},
        )
        assert row.time_label == "12:00:00"


# ─── Payload summary ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestPayloadSummary:
    def test_empty_payload_returns_empty_string(self) -> None:
        assert format_payload_summary({}) == ""

    def test_single_kv_pair_rendered(self) -> None:
        assert format_payload_summary({"x": 1}) == "x=1"

    def test_multiple_kv_pairs_space_separated(self) -> None:
        # Insertion order honored (Python 3.7+).
        out = format_payload_summary({"a": 1, "b": 2, "c": 3})
        assert out == "a=1 b=2 c=3"

    def test_long_payload_truncated_with_ellipsis(self) -> None:
        # Build a payload that exceeds max_len.
        out = format_payload_summary({"k": "x" * 200}, max_len=20)
        assert out.endswith("...")
        assert len(out) <= 20

    def test_short_payload_not_truncated(self) -> None:
        # Right at the boundary : exactly max_len -> not truncated.
        out = format_payload_summary({"k": "v"}, max_len=10)
        assert "..." not in out

    def test_decimal_str_values_serialized(self) -> None:
        # Audit payloads typically carry Decimals as strings (cf.
        # services that emit them with str(Decimal)). Make sure the
        # formatter doesn't choke on stringified values.
        out = format_payload_summary({"capital": "20.00", "n_trades": 3})
        assert "capital=20.00" in out
        assert "n_trades=3" in out

    def test_max_len_too_small_rejected(self) -> None:
        # max_len <= len(ellipsis) is meaningless.
        with pytest.raises(ValueError, match=r"max_len must be"):
            format_payload_summary({"k": "v"}, max_len=3)


# ─── Event row ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEventRow:
    def test_required_fields_passed_through(self) -> None:
        row = format_event_row(
            {
                "id": 42,
                "ts": 1700000000,
                "event_type": "POSITION_OPENED",
                "payload": {"strategy": "trend_follower"},
                "version": 1,
            },
        )
        assert row.event_id == 42
        assert row.ts == 1700000000
        assert row.event_type == "POSITION_OPENED"
        assert "trend_follower" in row.summary

    def test_missing_payload_treated_as_empty(self) -> None:
        # ``payload`` key absent -> empty summary, no crash.
        row = format_event_row(
            {"id": 1, "ts": 0, "event_type": "X", "version": 1},
        )
        assert row.summary == ""

    def test_none_payload_treated_as_empty(self) -> None:
        row = format_event_row(
            {"id": 1, "ts": 0, "event_type": "X", "payload": None, "version": 1},
        )
        assert row.summary == ""

    def test_missing_required_field_raises(self) -> None:
        # Anti-A8 : surface schema mismatch loudly rather than
        # silently corrupting display.
        with pytest.raises(KeyError):
            format_event_row({"id": 1, "ts": 0, "version": 1})  # no event_type


# ─── Containers ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestContainers:
    def test_event_row_immutable(self) -> None:
        row = format_event_row(
            {"id": 1, "ts": 0, "event_type": "X", "payload": {}, "version": 1},
        )
        with pytest.raises((AttributeError, TypeError)):
            row.event_id = 999  # type: ignore[misc]

    def test_snapshot_immutable(self) -> None:
        snap = JournalSnapshot(rows=(), total_returned=0)
        with pytest.raises((AttributeError, TypeError)):
            snap.total_returned = 99  # type: ignore[misc]

    def test_snapshot_rows_is_tuple(self) -> None:
        # Tuple makes the snapshot deeply immutable (a list would be
        # frozen on the dataclass but mutable on its content).
        snap = JournalSnapshot(rows=(), total_returned=0)
        assert isinstance(snap.rows, tuple)


# ─── Constants ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConstants:
    def test_default_history_limit_reasonable(self) -> None:
        # Mobile UX : ~50 events fits within scroll comfort.
        assert DEFAULT_HISTORY_LIMIT == 50

    def test_default_summary_max_len_reasonable(self) -> None:
        # Single-line caption : 80 chars fits modern phone widths.
        assert DEFAULT_SUMMARY_MAX_LEN == 80

    def test_event_row_dataclass_shape(self) -> None:
        # Stable contract — the screen builds widgets from these
        # attributes by name.
        row = JournalEventRow(
            event_id=1,
            ts=0,
            event_type="X",
            time_label="00:00:00",
            summary="",
        )
        assert row.event_id == 1
        assert row.event_type == "X"
        assert row.time_label == "00:00:00"
