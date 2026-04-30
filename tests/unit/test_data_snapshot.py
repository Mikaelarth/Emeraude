"""Unit tests for the iter #88 data-snapshot module (D6).

Cover :

* :func:`compute_snapshot_hash` — determinism, sensitivity to every
  kline field, stable across multiple calls.
* :func:`save_snapshot` / :func:`load_snapshot` — round-trip preserves
  every field, atomic write (no .tmp left behind), empty kline list.
* :class:`SnapshotIntegrityError` — tampering detection (modify a
  kline value on disk, hash recompute differs from header).
* :class:`SnapshotFormatError` — bad JSON, missing field, wrong type,
  wrong n_klines, malformed kline row, version mismatch.

Pure tests : no DB, no network. Uses :func:`pytest.fixture` ``tmp_path``
for filesystem isolation per test.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from emeraude.infra.data_snapshot import (
    SNAPSHOT_FORMAT_VERSION,
    SnapshotFormatError,
    SnapshotIntegrityError,
    compute_snapshot_hash,
    load_snapshot,
    make_snapshot,
    save_snapshot,
)
from emeraude.infra.market_data import Kline

if TYPE_CHECKING:
    from pathlib import Path


# ─── Helpers ────────────────────────────────────────────────────────────────


def _kline(
    open_time: int = 1_700_000_000_000,
    *,
    open_: str = "100",
    high: str = "105",
    low: str = "99",
    close: str = "103",
    volume: str = "10.5",
    close_time: int = 1_700_000_059_999,
    n_trades: int = 5,
) -> Kline:
    """Build a synthetic :class:`Kline` with sensible defaults."""
    return Kline(
        open_time=open_time,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        close_time=close_time,
        n_trades=n_trades,
    )


def _series(n: int = 3, start_ms: int = 1_700_000_000_000, step_ms: int = 60_000) -> list[Kline]:
    """Build ``n`` consecutive klines on a ``step_ms`` cadence."""
    return [
        _kline(
            open_time=start_ms + i * step_ms,
            close_time=start_ms + i * step_ms + step_ms - 1,
            close=str(Decimal(100) + Decimal(i)),
        )
        for i in range(n)
    ]


# ─── compute_snapshot_hash ─────────────────────────────────────────────────


@pytest.mark.unit
class TestComputeSnapshotHash:
    def test_empty_input_yields_sha256_of_empty(self) -> None:
        # SHA-256 of "" is well-known ; we just verify the result has
        # the right shape.
        h = compute_snapshot_hash([])
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 64  # 32 bytes hex = 64 chars

    def test_deterministic_across_calls(self) -> None:
        klines = _series(5)
        h1 = compute_snapshot_hash(klines)
        h2 = compute_snapshot_hash(klines)
        assert h1 == h2

    def test_order_sensitive(self) -> None:
        # Reversing the order changes the canonical concatenation
        # and therefore the hash. Sanity check that we do honor order.
        klines = _series(3)
        h_forward = compute_snapshot_hash(klines)
        h_reversed = compute_snapshot_hash(list(reversed(klines)))
        assert h_forward != h_reversed

    def test_field_sensitive_each_field(self) -> None:
        # Tweaking each kline field individually must change the hash.
        base = _kline()
        base_hash = compute_snapshot_hash([base])

        variants = [
            _kline(open_time=base.open_time + 1),
            _kline(open_="999"),
            _kline(high="999"),
            _kline(low="0"),
            _kline(close="999"),
            _kline(volume="0"),
            _kline(close_time=base.close_time + 1),
            _kline(n_trades=base.n_trades + 1),
        ]
        for variant in variants:
            assert compute_snapshot_hash([variant]) != base_hash

    def test_decimal_canonical_form(self) -> None:
        # Decimal("100") and Decimal("100.0") have different str() —
        # therefore different canonical forms — therefore different
        # hashes. Documents the contract : the user MUST use a
        # consistent Decimal representation.
        k1 = _kline(open_="100")
        k2 = _kline(open_="100.0")
        assert compute_snapshot_hash([k1]) != compute_snapshot_hash([k2])


# ─── make_snapshot ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMakeSnapshot:
    def test_populates_content_hash(self) -> None:
        klines = _series(2)
        snap = make_snapshot(
            symbol="BTCUSDT",
            interval="1h",
            period_start_ms=1_700_000_000_000,
            period_end_ms=1_700_007_200_000,
            klines=klines,
            captured_at_ms=1_700_010_000_000,
        )
        assert snap.symbol == "BTCUSDT"
        assert snap.interval == "1h"
        assert snap.klines == tuple(klines)
        assert snap.content_hash == compute_snapshot_hash(klines)


# ─── Round-trip save / load ────────────────────────────────────────────────


@pytest.mark.unit
class TestRoundTrip:
    def test_full_round_trip_preserves_every_field(self, tmp_path: Path) -> None:
        klines = _series(5)
        snap = make_snapshot(
            symbol="ETHUSDT",
            interval="5m",
            period_start_ms=1_700_000_000_000,
            period_end_ms=1_700_001_500_000,
            klines=klines,
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "snap.jsonl"
        save_snapshot(snap, path)

        loaded = load_snapshot(path)
        assert loaded == snap

    def test_empty_klines_round_trip(self, tmp_path: Path) -> None:
        snap = make_snapshot(
            symbol="BTCUSDT",
            interval="1h",
            period_start_ms=1_700_000_000_000,
            period_end_ms=1_700_000_000_000,
            klines=[],
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "empty.jsonl"
        save_snapshot(snap, path)
        loaded = load_snapshot(path)
        assert loaded == snap
        assert loaded.klines == ()

    def test_decimal_precision_preserved(self, tmp_path: Path) -> None:
        # Crypto prices commonly have 8 decimal places ; roundtrip must
        # not lose precision.
        precise = _kline(
            open_="0.12345678",
            high="0.12345679",
            low="0.12345677",
            close="0.12345678",
            volume="1234567.89012345",
        )
        snap = make_snapshot(
            symbol="BTCUSDT",
            interval="1m",
            period_start_ms=1_700_000_000_000,
            period_end_ms=1_700_000_060_000,
            klines=[precise],
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "precision.jsonl"
        save_snapshot(snap, path)
        loaded = load_snapshot(path)
        assert loaded.klines[0].open == precise.open
        assert loaded.klines[0].volume == precise.volume

    def test_atomic_write_no_tmp_left_behind(self, tmp_path: Path) -> None:
        snap = make_snapshot(
            symbol="BTCUSDT",
            interval="1h",
            period_start_ms=1_700_000_000_000,
            period_end_ms=1_700_007_200_000,
            klines=_series(2),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "snap.jsonl"
        save_snapshot(snap, path)

        # The .tmp variant must be cleaned up by the rename.
        assert path.exists()
        assert not path.with_suffix(path.suffix + ".tmp").exists()


# ─── Integrity errors ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestIntegrityCheck:
    def _write_then_tamper(self, tmp_path: Path, mutate_line: int, replacement: str) -> Path:
        """Write a valid snapshot then replace one line with ``replacement``."""
        snap = make_snapshot(
            symbol="BTCUSDT",
            interval="1h",
            period_start_ms=1_700_000_000_000,
            period_end_ms=1_700_007_200_000,
            klines=_series(3),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "tampered.jsonl"
        save_snapshot(snap, path)
        lines = path.read_text(encoding="utf-8").split("\n")
        lines[mutate_line] = replacement
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_tampered_kline_value_detected(self, tmp_path: Path) -> None:
        # Body line 1 is the first kline (lines = [header, k0, k1, k2, ""]).
        path = self._write_then_tamper(
            tmp_path,
            mutate_line=1,
            replacement=json.dumps(
                [1_700_000_000_000, "999", "999", "999", "999", "10.5", 1_700_000_059_999, 5]
            ),
        )
        with pytest.raises(SnapshotIntegrityError, match="integrity check failed"):
            load_snapshot(path)

    def test_added_kline_detected(self, tmp_path: Path) -> None:
        # Append a fake kline line — n_klines mismatch raises a
        # *format* error before we even compute the hash.
        snap = make_snapshot(
            symbol="BTCUSDT",
            interval="1h",
            period_start_ms=1_700_000_000_000,
            period_end_ms=1_700_007_200_000,
            klines=_series(2),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "extra_line.jsonl"
        save_snapshot(snap, path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps([1_700_999_999_999, "1", "1", "1", "1", "1", 1_700_999_999_999, 1])
                + "\n"
            )
        with pytest.raises(SnapshotFormatError, match="declares n_klines"):
            load_snapshot(path)

    def test_removed_kline_detected(self, tmp_path: Path) -> None:
        snap = make_snapshot(
            symbol="BTCUSDT",
            interval="1h",
            period_start_ms=1_700_000_000_000,
            period_end_ms=1_700_007_200_000,
            klines=_series(3),
            captured_at_ms=1_700_010_000_000,
        )
        path = tmp_path / "missing_line.jsonl"
        save_snapshot(snap, path)
        lines = path.read_text(encoding="utf-8").split("\n")
        # Drop body line 1.
        lines.pop(1)
        path.write_text("\n".join(lines), encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="declares n_klines"):
            load_snapshot(path)


# ─── Format errors ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFormatErrors:
    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_file.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="empty"):
            load_snapshot(path)

    def test_invalid_json_header(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("{not json\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="not valid JSON"):
            load_snapshot(path)

    def test_header_not_object(self, tmp_path: Path) -> None:
        path = tmp_path / "header_array.jsonl"
        path.write_text('["array_header"]\n', encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="must be a JSON object"):
            load_snapshot(path)

    def test_missing_required_field(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.jsonl"
        # Missing ``content_hash``.
        header = {
            "version": 1,
            "symbol": "BTCUSDT",
            "interval": "1h",
            "period_start_ms": 0,
            "period_end_ms": 0,
            "captured_at_ms": 0,
            "n_klines": 0,
        }
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="missing required field"):
            load_snapshot(path)

    def test_wrong_field_type(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong_type.jsonl"
        header = {
            "version": 1,
            "symbol": "BTCUSDT",
            "interval": "1h",
            "period_start_ms": 0,
            "period_end_ms": 0,
            "captured_at_ms": 0,
            "n_klines": "zero",  # should be int
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="has type str"):
            load_snapshot(path)

    def test_wrong_version(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong_version.jsonl"
        header = {
            "version": SNAPSHOT_FORMAT_VERSION + 99,
            "symbol": "BTCUSDT",
            "interval": "1h",
            "period_start_ms": 0,
            "period_end_ms": 0,
            "captured_at_ms": 0,
            "n_klines": 0,
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="version"):
            load_snapshot(path)

    def test_kline_line_not_array(self, tmp_path: Path) -> None:
        path = tmp_path / "kline_not_array.jsonl"
        header = {
            "version": 1,
            "symbol": "BTCUSDT",
            "interval": "1h",
            "period_start_ms": 0,
            "period_end_ms": 0,
            "captured_at_ms": 0,
            "n_klines": 1,
            "content_hash": "sha256:abc",
        }
        path.write_text(json.dumps(header) + '\n{"oops": true}\n', encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="must be a JSON array"):
            load_snapshot(path)

    def test_kline_line_wrong_field_count(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong_fields.jsonl"
        header = {
            "version": 1,
            "symbol": "BTCUSDT",
            "interval": "1h",
            "period_start_ms": 0,
            "period_end_ms": 0,
            "captured_at_ms": 0,
            "n_klines": 1,
            "content_hash": "sha256:abc",
        }
        # Only 4 fields — should be 8.
        path.write_text(json.dumps(header) + "\n[1, 2, 3, 4]\n", encoding="utf-8")
        with pytest.raises(SnapshotFormatError, match="expected 8 fields"):
            load_snapshot(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_snapshot(tmp_path / "does_not_exist.jsonl")


# ─── KlineSnapshot dataclass smoke ──────────────────────────────────────────


@pytest.mark.unit
class TestKlineSnapshot:
    def test_frozen_immutable(self) -> None:
        snap = make_snapshot(
            symbol="BTCUSDT",
            interval="1h",
            period_start_ms=0,
            period_end_ms=0,
            klines=[],
            captured_at_ms=0,
        )
        # frozen=True : assignment raises FrozenInstanceError or
        # AttributeError (Python version dependent).
        with pytest.raises((AttributeError, Exception), match=r"cannot assign|frozen"):
            snap.symbol = "ETHUSDT"  # type: ignore[misc]
