"""Unit tests for the iter #89 coin-universe snapshot module (D2).

Cover :

* :func:`compute_universe_hash` — determinism, sensitivity to every
  entry field, stable across multiple calls.
* :func:`save_universe_snapshot` / :func:`load_universe_snapshot` —
  round-trip preserves every field, atomic write, empty entries.
* Tampering detection : modifying an entry on disk -> hash mismatch
  raises :class:`SnapshotIntegrityError`.
* Format errors : bad JSON, missing field, wrong type, version
  mismatch, entry not array, wrong field count, file not found.
* :func:`universe_at` query : empty input, no qualifying snapshot,
  exact match, latest match wins, multiple candidates, future date.

Pure tests : no DB, no network. Uses :func:`pytest.fixture` ``tmp_path``
for filesystem isolation per test.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from emeraude.infra.coin_universe_snapshot import (
    UNIVERSE_FORMAT_VERSION,
    CoinEntry,
    SnapshotFormatError,
    SnapshotIntegrityError,
    compute_universe_hash,
    load_universe_snapshot,
    make_universe_snapshot,
    save_universe_snapshot,
    universe_at,
)

if TYPE_CHECKING:
    from pathlib import Path


# ─── Helpers ────────────────────────────────────────────────────────────────


def _entries(n: int = 3) -> list[CoinEntry]:
    """Build ``n`` synthetic :class:`CoinEntry` ranked 1..n."""
    symbols = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "TRX", "DOT", "MATIC"]
    return [CoinEntry(symbol=symbols[i], market_cap_rank=i + 1) for i in range(n)]


# ─── compute_universe_hash ─────────────────────────────────────────────────


@pytest.mark.unit
class TestComputeUniverseHash:
    def test_empty_input_yields_sha256_of_empty(self) -> None:
        h = compute_universe_hash([])
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 64

    def test_deterministic_across_calls(self) -> None:
        entries = _entries(5)
        h1 = compute_universe_hash(entries)
        h2 = compute_universe_hash(entries)
        assert h1 == h2

    def test_order_sensitive(self) -> None:
        # Reversing changes the canonical concatenation -> different hash.
        entries = _entries(3)
        h_forward = compute_universe_hash(entries)
        h_reversed = compute_universe_hash(list(reversed(entries)))
        assert h_forward != h_reversed

    def test_field_sensitive_symbol(self) -> None:
        base = [CoinEntry(symbol="BTC", market_cap_rank=1)]
        variant = [CoinEntry(symbol="BTC2", market_cap_rank=1)]
        assert compute_universe_hash(base) != compute_universe_hash(variant)

    def test_field_sensitive_rank(self) -> None:
        base = [CoinEntry(symbol="BTC", market_cap_rank=1)]
        variant = [CoinEntry(symbol="BTC", market_cap_rank=2)]
        assert compute_universe_hash(base) != compute_universe_hash(variant)


# ─── make_universe_snapshot ────────────────────────────────────────────────


@pytest.mark.unit
class TestMakeUniverseSnapshot:
    def test_populates_content_hash(self) -> None:
        entries = _entries(2)
        snap = make_universe_snapshot(
            snapshot_date_ms=1_700_000_000_000,
            entries=entries,
            captured_at_ms=1_700_010_000_000,
        )
        assert snap.snapshot_date_ms == 1_700_000_000_000
        assert snap.entries == tuple(entries)
        assert snap.content_hash == compute_universe_hash(entries)


# ─── Round-trip save / load ────────────────────────────────────────────────


@pytest.mark.unit
class TestRoundTrip:
    def test_full_round_trip_preserves_every_field(self, tmp_path: Path) -> None:
        snap = make_universe_snapshot(
            snapshot_date_ms=1_700_000_000_000,
            entries=_entries(5),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "universe.jsonl"
        save_universe_snapshot(snap, path)

        loaded = load_universe_snapshot(path)
        assert loaded == snap

    def test_empty_entries_round_trip(self, tmp_path: Path) -> None:
        snap = make_universe_snapshot(
            snapshot_date_ms=1_700_000_000_000,
            entries=[],
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "empty.jsonl"
        save_universe_snapshot(snap, path)
        loaded = load_universe_snapshot(path)
        assert loaded == snap
        assert loaded.entries == ()

    def test_atomic_write_no_tmp_left_behind(self, tmp_path: Path) -> None:
        snap = make_universe_snapshot(
            snapshot_date_ms=1_700_000_000_000,
            entries=_entries(2),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "universe.jsonl"
        save_universe_snapshot(snap, path)

        assert path.exists()
        assert not path.with_suffix(path.suffix + ".tmp").exists()


# ─── Integrity errors ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestIntegrityCheck:
    def test_tampered_entry_value_detected(self, tmp_path: Path) -> None:
        snap = make_universe_snapshot(
            snapshot_date_ms=1_700_000_000_000,
            entries=_entries(3),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "tampered.jsonl"
        save_universe_snapshot(snap, path)
        lines = path.read_text(encoding="utf-8").split("\n")
        # Body line 1 = first entry. Replace BTC -> XYZ.
        lines[1] = json.dumps(["XYZ", 1])
        path.write_text("\n".join(lines), encoding="utf-8")
        with pytest.raises(SnapshotIntegrityError, match="integrity check failed"):
            load_universe_snapshot(path)

    def test_added_entry_detected(self, tmp_path: Path) -> None:
        snap = make_universe_snapshot(
            snapshot_date_ms=1_700_000_000_000,
            entries=_entries(2),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "extra_line.jsonl"
        save_universe_snapshot(snap, path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(["FAKE", 999]) + "\n")
        with pytest.raises(SnapshotFormatError, match="declares n_entries"):
            load_universe_snapshot(path)

    def test_removed_entry_detected(self, tmp_path: Path) -> None:
        snap = make_universe_snapshot(
            snapshot_date_ms=1_700_000_000_000,
            entries=_entries(3),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "missing_line.jsonl"
        save_universe_snapshot(snap, path)
        lines = path.read_text(encoding="utf-8").split("\n")
        lines.pop(1)  # drop body line 1
        path.write_text("\n".join(lines), encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="declares n_entries"):
            load_universe_snapshot(path)


# ─── Format errors ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFormatErrors:
    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_file.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="empty"):
            load_universe_snapshot(path)

    def test_invalid_json_header(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("{not json\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="not valid JSON"):
            load_universe_snapshot(path)

    def test_header_not_object(self, tmp_path: Path) -> None:
        path = tmp_path / "header_array.jsonl"
        path.write_text('["array_header"]\n', encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="must be a JSON object"):
            load_universe_snapshot(path)

    def test_missing_required_field(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.jsonl"
        # Missing ``content_hash``.
        header = {
            "version": 1,
            "snapshot_date_ms": 0,
            "captured_at_ms": 0,
            "n_entries": 0,
        }
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="missing required field"):
            load_universe_snapshot(path)

    def test_wrong_field_type(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong_type.jsonl"
        header = {
            "version": 1,
            "snapshot_date_ms": "now",  # should be int
            "captured_at_ms": 0,
            "n_entries": 0,
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="has type str"):
            load_universe_snapshot(path)

    def test_wrong_version(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong_version.jsonl"
        header = {
            "version": UNIVERSE_FORMAT_VERSION + 99,
            "snapshot_date_ms": 0,
            "captured_at_ms": 0,
            "n_entries": 0,
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="version"):
            load_universe_snapshot(path)

    def test_entry_line_not_array(self, tmp_path: Path) -> None:
        path = tmp_path / "entry_not_array.jsonl"
        header = {
            "version": 1,
            "snapshot_date_ms": 0,
            "captured_at_ms": 0,
            "n_entries": 1,
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + '\n{"oops": true}\n', encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="must be a JSON array"):
            load_universe_snapshot(path)

    def test_entry_wrong_field_count(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong_fields.jsonl"
        header = {
            "version": 1,
            "snapshot_date_ms": 0,
            "captured_at_ms": 0,
            "n_entries": 1,
            "content_hash": "sha256:abc",
        }
        # 3 fields, expected 2.
        path.write_text(json.dumps(header) + '\n["BTC", 1, "extra"]\n', encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="expected 2 fields"):
            load_universe_snapshot(path)

    def test_entry_symbol_wrong_type(self, tmp_path: Path) -> None:
        path = tmp_path / "symbol_int.jsonl"
        header = {
            "version": 1,
            "snapshot_date_ms": 0,
            "captured_at_ms": 0,
            "n_entries": 1,
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + "\n[123, 1]\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="symbol must be str"):
            load_universe_snapshot(path)

    def test_entry_rank_wrong_type(self, tmp_path: Path) -> None:
        path = tmp_path / "rank_str.jsonl"
        header = {
            "version": 1,
            "snapshot_date_ms": 0,
            "captured_at_ms": 0,
            "n_entries": 1,
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + '\n["BTC", "first"]\n', encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="market_cap_rank must be int"):
            load_universe_snapshot(path)

    def test_entry_rank_bool_rejected(self, tmp_path: Path) -> None:
        # ``isinstance(True, int)`` is True in Python — we explicitly
        # reject bool to keep the contract strict.
        path = tmp_path / "rank_bool.jsonl"
        header = {
            "version": 1,
            "snapshot_date_ms": 0,
            "captured_at_ms": 0,
            "n_entries": 1,
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + '\n["BTC", true]\n', encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="market_cap_rank must be int"):
            load_universe_snapshot(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_universe_snapshot(tmp_path / "does_not_exist.jsonl")


# ─── universe_at query ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestUniverseAt:
    def test_empty_input_returns_none(self) -> None:
        assert universe_at(1_700_000_000_000, []) is None

    def test_no_qualifying_snapshot_returns_none(self) -> None:
        # All snapshots are AFTER the requested date -> nothing
        # qualifies and the function MUST return None (caller treats
        # that as a hard error per doc 11 §D2).
        snaps = [
            make_universe_snapshot(
                snapshot_date_ms=2_000_000_000_000,
                entries=_entries(2),
                captured_at_ms=2_000_010_000_000,
            ),
            make_universe_snapshot(
                snapshot_date_ms=2_100_000_000_000,
                entries=_entries(2),
                captured_at_ms=2_100_010_000_000,
            ),
        ]
        assert universe_at(1_700_000_000_000, snaps) is None

    def test_exact_match_returned(self) -> None:
        target = 1_700_000_000_000
        snap = make_universe_snapshot(
            snapshot_date_ms=target,
            entries=_entries(2),
            captured_at_ms=target + 60_000,
        )
        result = universe_at(target, [snap])
        assert result is snap

    def test_latest_qualifying_wins(self) -> None:
        # Three snapshots all <= target ; expect the most recent one.
        target = 1_700_000_000_000
        old = make_universe_snapshot(
            snapshot_date_ms=1_600_000_000_000,
            entries=[CoinEntry(symbol="OLD", market_cap_rank=1)],
            captured_at_ms=1_600_010_000_000,
        )
        mid = make_universe_snapshot(
            snapshot_date_ms=1_650_000_000_000,
            entries=[CoinEntry(symbol="MID", market_cap_rank=1)],
            captured_at_ms=1_650_010_000_000,
        )
        recent = make_universe_snapshot(
            snapshot_date_ms=1_690_000_000_000,
            entries=[CoinEntry(symbol="RECENT", market_cap_rank=1)],
            captured_at_ms=1_690_010_000_000,
        )
        # Pass in unsorted order to verify the function does not rely
        # on input ordering.
        result = universe_at(target, [recent, old, mid])
        assert result is recent

    def test_skips_future_snapshots(self) -> None:
        target = 1_700_000_000_000
        before = make_universe_snapshot(
            snapshot_date_ms=1_690_000_000_000,
            entries=[CoinEntry(symbol="BEFORE", market_cap_rank=1)],
            captured_at_ms=1_690_010_000_000,
        )
        after = make_universe_snapshot(
            snapshot_date_ms=1_710_000_000_000,
            entries=[CoinEntry(symbol="AFTER", market_cap_rank=1)],
            captured_at_ms=1_710_010_000_000,
        )
        result = universe_at(target, [after, before])
        assert result is before


# ─── CoinEntry / dataclass smoke ───────────────────────────────────────────


@pytest.mark.unit
class TestCoinEntry:
    def test_frozen_immutable(self) -> None:
        entry = CoinEntry(symbol="BTC", market_cap_rank=1)
        with pytest.raises((AttributeError, Exception), match=r"cannot assign|frozen"):
            entry.symbol = "ETH"  # type: ignore[misc]
