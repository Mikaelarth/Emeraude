"""Unit tests for the iter #90 data-ingestion guard service.

Cover :

* :func:`validate_and_audit_klines` — orchestrates the D3 + D4 checks
  on a freshly-fetched series, builds an :class:`IngestionReport`,
  and emits exactly one audit event per call.
* Decision logic : empty fetch / completeness reject / per-bar
  hard-reject -> ``should_reject=True`` ; warnings stay
  ``should_reject=False``.
* Audit payload shape : symbol, interval, n_received, n_expected,
  missing_pct, bar_quality flag counts, status, rejection_reason.
* :func:`summarize_flags` pure helper.

Mocks ``audit.audit`` via ``monkeypatch`` so tests don't touch the
SQLite audit log ; the underlying audit storage is tested in
``test_audit.py``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from emeraude.infra.data_quality import (
    BarQualityFlag,
    BarQualityReport,
    HistoryCompletenessReport,
)
from emeraude.infra.market_data import Kline
from emeraude.services.data_ingestion_guard import (
    AUDIT_DATA_INGESTION_COMPLETED,
    IngestionReport,
    summarize_flags,
    validate_and_audit_klines,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


# ─── Helpers ────────────────────────────────────────────────────────────────


def _kline(
    open_time: int = 1_700_000_000_000,
    *,
    high: str = "100",
    low: str = "90",
    open_: str = "92",
    close: str = "98",
    volume: str = "10.5",
    close_time: int = 1_700_000_059_999,
    n_trades: int = 5,
) -> Kline:
    """Build a synthetic clean :class:`Kline`."""
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


def _series(n: int = 3, step_ms: int = 60_000) -> list[Kline]:
    """``n`` consecutive clean klines on a ``step_ms`` cadence."""
    return [
        _kline(
            open_time=1_700_000_000_000 + i * step_ms,
            close_time=1_700_000_000_000 + i * step_ms + step_ms - 1,
        )
        for i in range(n)
    ]


@pytest.fixture
def captured_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, Mapping[str, object]]]:
    """Capture every ``audit.audit(event_type, payload)`` invocation.

    Returns a list of ``(event_type, payload)`` tuples populated by
    side effect — callers append by simply running the SUT under
    test. The mock targets the symbol imported by
    :mod:`data_ingestion_guard` so the patch lands at the call site.
    """
    captured: list[tuple[str, Mapping[str, object]]] = []

    def _fake_audit(event_type: str, payload: Mapping[str, object] | None = None) -> None:
        captured.append((event_type, payload or {}))

    # Patch the symbol as it lives in the module under test (not the
    # original :mod:`infra.audit` module — Python resolves ``audit.audit``
    # via the local import).
    monkeypatch.setattr(
        "emeraude.services.data_ingestion_guard.audit.audit",
        _fake_audit,
    )
    return captured


# ─── Empty fetch ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEmptyFetch:
    def test_zero_klines_when_expected_is_zero_is_ok(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        # Edge case : caller didn't expect any bars (e.g. empty
        # backfill). Treat as trivially complete, no reject.
        result = validate_and_audit_klines([], symbol="BTCUSDT", interval="1h", expected_count=0)
        assert result.should_reject is False
        assert result.completeness.missing_pct == Decimal("0")
        assert len(captured_audit) == 1
        assert captured_audit[0][0] == AUDIT_DATA_INGESTION_COMPLETED
        assert captured_audit[0][1]["status"] == "ok"

    def test_zero_klines_when_expected_positive_is_reject(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        result = validate_and_audit_klines([], symbol="BTCUSDT", interval="1h", expected_count=100)
        assert result.should_reject is True
        assert "no klines fetched" in result.rejection_reason
        # Audit row still emitted with status="rejected".
        assert len(captured_audit) == 1
        assert captured_audit[0][1]["status"] == "rejected"


# ─── Clean series ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCleanSeries:
    def test_no_flags_no_reject(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        klines = _series(5)
        result = validate_and_audit_klines(
            klines,
            symbol="BTCUSDT",
            interval="1m",
            expected_count=5,
            expected_dt_ms=60_000,
        )
        assert result.should_reject is False
        assert result.flag_counts == {}
        assert all(r.is_clean for r in result.per_bar)
        # Audit row : status ok, no rejection reason.
        assert captured_audit[0][1]["status"] == "ok"
        assert "rejection_reason" not in captured_audit[0][1]
        assert captured_audit[0][1]["bar_quality"] == {}


# ─── Hard rejects ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHardRejects:
    def test_invalid_high_low_rejects(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        klines = _series(3)
        # Tamper bar 1 : high < low.
        klines[1] = _kline(
            open_time=klines[1].open_time,
            close_time=klines[1].close_time,
            high="50",
            low="60",
            close="55",
        )
        result = validate_and_audit_klines(
            klines, symbol="BTCUSDT", interval="1m", expected_count=3
        )
        assert result.should_reject is True
        assert "bar 1 corrupted" in result.rejection_reason
        bar_quality = captured_audit[0][1]["bar_quality"]
        assert isinstance(bar_quality, dict)
        assert BarQualityFlag.INVALID_HIGH_LOW.value in bar_quality

    def test_close_out_of_range_rejects(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        klines = _series(2)
        # Tamper bar 0 : close above high.
        klines[0] = _kline(
            open_time=klines[0].open_time,
            close_time=klines[0].close_time,
            high="100",
            low="90",
            close="200",
        )
        result = validate_and_audit_klines(
            klines, symbol="BTCUSDT", interval="1m", expected_count=2
        )
        assert result.should_reject is True
        assert "bar 0 corrupted" in result.rejection_reason

    def test_completeness_reject_dominates(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        # 50 bars expected, 30 received -> 40 % missing, well above 5 %.
        klines = _series(30)
        result = validate_and_audit_klines(
            klines, symbol="BTCUSDT", interval="1m", expected_count=50
        )
        assert result.should_reject is True
        assert "series too incomplete" in result.rejection_reason
        assert captured_audit[0][1]["status"] == "rejected"


# ─── Warnings only ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestWarningsOnly:
    def test_flat_volume_is_warning(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        klines = _series(3)
        # Tamper bar 1 : volume=0 with non-zero range.
        klines[1] = _kline(
            open_time=klines[1].open_time,
            close_time=klines[1].close_time,
            volume="0",
            high="105",
            low="95",
            close="100",
        )
        result = validate_and_audit_klines(
            klines, symbol="BTCUSDT", interval="1m", expected_count=3
        )
        assert result.should_reject is False
        assert result.flag_counts == {BarQualityFlag.FLAT_VOLUME.value: 1}
        assert captured_audit[0][1]["status"] == "ok"

    def test_outlier_range_is_warning(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        klines = _series(3)
        # Tamper bar 2 : huge range vs ATR.
        klines[2] = _kline(
            open_time=klines[2].open_time,
            close_time=klines[2].close_time,
            high="200",
            low="100",
            close="150",
        )
        result = validate_and_audit_klines(
            klines,
            symbol="BTCUSDT",
            interval="1m",
            expected_count=3,
            atr_value=Decimal("1"),
        )
        assert result.should_reject is False
        assert result.flag_counts == {BarQualityFlag.OUTLIER_RANGE.value: 1}

    def test_time_gap_is_warning(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        # Build a series with a time gap between bar 1 and bar 2 :
        # expected 60s cadence but bar 2 is +120s.
        klines = [
            _kline(open_time=1_700_000_000_000, close_time=1_700_000_059_999),
            _kline(open_time=1_700_000_060_000, close_time=1_700_000_119_999),
            # gap : skip 60_000 ms.
            _kline(open_time=1_700_000_180_000, close_time=1_700_000_239_999),
        ]
        result = validate_and_audit_klines(
            klines,
            symbol="BTCUSDT",
            interval="1m",
            expected_count=3,
            expected_dt_ms=60_000,
        )
        assert result.should_reject is False
        assert result.flag_counts == {BarQualityFlag.TIME_GAP.value: 1}

    def test_below_5pct_missing_is_warning(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        # 100 expected, 98 received = 2 % missing < 5 % -> interpolate
        # hint, no reject.
        klines = _series(98)
        result = validate_and_audit_klines(
            klines, symbol="BTCUSDT", interval="1m", expected_count=100
        )
        assert result.should_reject is False
        assert result.completeness.should_interpolate is True
        assert result.completeness.missing_pct == Decimal("0.02")
        assert captured_audit[0][1]["status"] == "ok"


# ─── Audit payload shape ────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditPayload:
    def test_payload_contains_required_fields(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        klines = _series(5)
        validate_and_audit_klines(klines, symbol="ETHUSDT", interval="5m", expected_count=5)
        assert len(captured_audit) == 1
        evt, payload = captured_audit[0]
        assert evt == AUDIT_DATA_INGESTION_COMPLETED
        for key in (
            "symbol",
            "interval",
            "n_received",
            "n_expected",
            "missing_pct",
            "bar_quality",
            "status",
        ):
            assert key in payload, f"missing payload key {key!r}"
        assert payload["symbol"] == "ETHUSDT"
        assert payload["interval"] == "5m"
        assert payload["n_received"] == 5
        assert payload["n_expected"] == 5

    def test_one_audit_per_call(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        # Two consecutive calls -> two audit rows.
        validate_and_audit_klines(_series(3), symbol="BTCUSDT", interval="1m", expected_count=3)
        validate_and_audit_klines([], symbol="BTCUSDT", interval="1m", expected_count=10)
        assert len(captured_audit) == 2
        assert captured_audit[0][1]["status"] == "ok"
        assert captured_audit[1][1]["status"] == "rejected"

    def test_flag_counts_aggregated(
        self, captured_audit: list[tuple[str, Mapping[str, object]]]
    ) -> None:
        # Build a series with 2 flat_volume bars + 1 time_gap on
        # the same fetch.
        klines = [
            _kline(open_time=1_700_000_000_000, close_time=1_700_000_059_999, volume="0"),
            _kline(open_time=1_700_000_060_000, close_time=1_700_000_119_999, volume="0"),
            # Gap to trigger TIME_GAP : +120s instead of +60s.
            _kline(open_time=1_700_000_240_000, close_time=1_700_000_299_999),
        ]
        validate_and_audit_klines(
            klines,
            symbol="BTCUSDT",
            interval="1m",
            expected_count=3,
            expected_dt_ms=60_000,
        )
        bar_quality = captured_audit[0][1]["bar_quality"]
        assert isinstance(bar_quality, dict)
        assert bar_quality.get(BarQualityFlag.FLAT_VOLUME.value) == 2
        assert bar_quality.get(BarQualityFlag.TIME_GAP.value) == 1


# ─── summarize_flags pure helper ───────────────────────────────────────────


@pytest.mark.unit
class TestSummarizeFlags:
    def test_empty_input(self) -> None:
        assert summarize_flags([]) == {}

    def test_no_flags(self) -> None:
        assert summarize_flags([BarQualityReport(), BarQualityReport()]) == {}

    def test_aggregates_across_reports(self) -> None:
        reports = [
            BarQualityReport(flags=(BarQualityFlag.FLAT_VOLUME,)),
            BarQualityReport(flags=(BarQualityFlag.FLAT_VOLUME, BarQualityFlag.TIME_GAP)),
            BarQualityReport(flags=(BarQualityFlag.OUTLIER_RANGE,)),
        ]
        assert summarize_flags(reports) == {
            BarQualityFlag.FLAT_VOLUME.value: 2,
            BarQualityFlag.TIME_GAP.value: 1,
            BarQualityFlag.OUTLIER_RANGE.value: 1,
        }


# ─── IngestionReport dataclass smoke ───────────────────────────────────────


@pytest.mark.unit
class TestIngestionReportShape:
    def test_frozen_immutable(self) -> None:
        report = IngestionReport(
            symbol="BTCUSDT",
            completeness=HistoryCompletenessReport(
                n_received=0,
                n_expected=0,
                missing_pct=Decimal("0"),
                should_reject=False,
                should_interpolate=False,
            ),
            per_bar=(),
            flag_counts={},
            should_reject=False,
            rejection_reason="",
        )
        with pytest.raises((AttributeError, Exception), match=r"cannot assign|frozen"):
            report.symbol = "ETH"  # type: ignore[misc]
