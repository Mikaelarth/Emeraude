"""D3 + D4 data quality checks for incoming OHLCV klines.

Implements doc 11 §"D3 — Bougies corrompues" + §"D4 — Bougies
manquantes".

Pure module : no I/O, no DB, no exchange access. Caller passes already-
fetched :class:`emeraude.infra.market_data.Kline` objects in ; the
module returns a structured report. The decision to log to audit
``bar_quality_warning`` and/or skip the cycle stays with the caller
(typically the orchestrator's data_ingestion path).

Why a dedicated utility module rather than inline checks in the
orchestrator ?

1. **Reuse** : backtest, live trading, debug tools all benefit from
   the same checker. Keeps a single source of truth for "is this bar
   trustworthy ?".
2. **Test surface** : checks are pure functions over known inputs.
   We can hand-craft every corner case (high < low, close = high
   exactly, volume == 0 with non-zero range, etc.) without spinning
   up an orchestrator.
3. **Doc alignment** : doc 11 §D3 lists 5 checks ; this module ships
   exactly those 5, named after their flag, so cross-referencing is
   trivial.

Doc 11 §D3 — 5 checks per bar :

* Volume nul + range non nul -> ``FLAT_VOLUME`` (warning).
* High < Low -> ``INVALID_HIGH_LOW`` (HARD reject — corruption).
* Close hors [Low, High] -> ``CLOSE_OUT_OF_RANGE`` (HARD reject).
* Range > 50x ATR_30 -> ``OUTLIER_RANGE`` (warning).
* Δt avec bar précédent ≠ timeframe attendu -> ``TIME_GAP`` (warning).

Doc 11 §D4 — completeness check on a kline series :

* < 5 % bars manquantes -> interpolation linéaire avec flag (caller
  responsibility) ; ``HistoryCompletenessReport.should_interpolate``.
* >= 5 % bars manquantes -> rejet du cycle ;
  ``HistoryCompletenessReport.should_reject``.

The 5 % threshold is configurable via :data:`DEFAULT_INTERPOLATION_LIMIT`
on the off-chance a future iter needs a different tolerance for a
specific symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from emeraude.infra.market_data import Kline


# ─── Constants ──────────────────────────────────────────────────────────────


#: Doc 11 §D3 row 4 : "Range > 50x ATR_30 -> outlier_range". The
#: multiplier is configurable per call site for back-testing edge
#: cases (e.g. crypto altcoins with thin liquidity).
DEFAULT_OUTLIER_ATR_MULT: Final[Decimal] = Decimal("50")

#: Doc 11 §D4 : "< 5 % manquantes -> interpolation, >= 5 % -> rejet".
#: Stored as a fraction (0.05) for direct comparison against
#: ``missing_pct``.
DEFAULT_INTERPOLATION_LIMIT: Final[Decimal] = Decimal("0.05")

_ZERO: Final[Decimal] = Decimal("0")


# ─── Quality flags (D3) ─────────────────────────────────────────────────────


class BarQualityFlag(StrEnum):
    """One per anomaly category, doc 11 §D3 row.

    StrEnum keeps audit / DB serialization trivial — a flag value is
    its lowercase string and round-trips through JSON without a
    custom encoder.
    """

    FLAT_VOLUME = "flat_volume"
    INVALID_HIGH_LOW = "invalid_high_low"
    CLOSE_OUT_OF_RANGE = "close_out_of_range"
    OUTLIER_RANGE = "outlier_range"
    TIME_GAP = "time_gap"


#: Subset of :class:`BarQualityFlag` that triggers a HARD reject of
#: the bar (corruption garantie per doc 11). Anything outside this
#: set is a warning : caller logs but continues.
_REJECT_FLAGS: Final[frozenset[BarQualityFlag]] = frozenset(
    {
        BarQualityFlag.INVALID_HIGH_LOW,
        BarQualityFlag.CLOSE_OUT_OF_RANGE,
    }
)


@dataclass(frozen=True, slots=True)
class BarQualityReport:
    """Per-bar quality verdict.

    Attributes:
        flags: ordered list of detected anomalies. Empty if the bar
            passes every check (the typical case in production).
        should_reject: ``True`` iff at least one flag is in the
            "corruption garantie" set (HIGH<LOW, close ∉ [low,high]).
            Caller MUST skip the bar / cycle on True.
        is_clean: ``True`` iff no flag at all. Convenience for callers
            that want a single boolean fast-path.
    """

    flags: tuple[BarQualityFlag, ...] = ()

    @property
    def should_reject(self) -> bool:
        """Hard-reject condition per doc 11 §D3."""
        return any(f in _REJECT_FLAGS for f in self.flags)

    @property
    def is_clean(self) -> bool:
        """No anomaly detected at all."""
        return not self.flags


# ─── Bar quality check (D3) ─────────────────────────────────────────────────


def check_bar_quality(
    kline: Kline,
    *,
    prev_kline: Kline | None = None,
    expected_dt_ms: int | None = None,
    atr_value: Decimal | None = None,
    outlier_atr_mult: Decimal = DEFAULT_OUTLIER_ATR_MULT,
) -> BarQualityReport:
    """Run all 5 D3 checks against a single bar.

    Args:
        kline: the bar to validate.
        prev_kline: optional previous bar for the time-gap check.
            Skipped when ``None`` (e.g. first bar of a series).
        expected_dt_ms: expected delta (close_time - prev close_time)
            in milliseconds. Skipped when ``None`` ; required for the
            time-gap check.
        atr_value: optional ATR_N reference for the outlier range
            check. Skipped when ``None``. The doc says ATR_30 ;
            caller decides the period and passes the resulting Decimal.
        outlier_atr_mult: multiplier above which the range is flagged.
            Defaults to :data:`DEFAULT_OUTLIER_ATR_MULT` (50).

    Returns:
        A :class:`BarQualityReport` with all detected flags.

    Notes:
        Pure function : no logging, no audit emit. Caller decides what
        to do with the report (drop bar, skip cycle, log to audit).
    """
    flags: list[BarQualityFlag] = []
    bar_range = kline.high - kline.low

    # Check 1 : Volume nul + range non nul
    # A bar with a non-trivial price range but zero volume is
    # suspicious (no actual trading happened, but the price moved).
    # Most exchanges report 0 volume for "stitched" / replay candles.
    if kline.volume == _ZERO and bar_range != _ZERO:
        flags.append(BarQualityFlag.FLAT_VOLUME)

    # Check 2 : High < Low (HARD reject)
    # Pure data corruption. There's no plausible market scenario where
    # the high of a bar is below its low.
    if kline.high < kline.low:
        flags.append(BarQualityFlag.INVALID_HIGH_LOW)

    # Check 3 : Close hors [Low, High] (HARD reject)
    # Same pattern as #2 — the close is the last trade in the window
    # and must by definition lie within the high/low envelope.
    if not (kline.low <= kline.close <= kline.high):
        flags.append(BarQualityFlag.CLOSE_OUT_OF_RANGE)

    # Check 4 : Range > N x ATR (warning)
    # An anomalous spike. Skipped when ATR is unknown (not enough
    # history yet) or zero (degenerate, would yield Infinity ratio).
    if atr_value is not None and atr_value > _ZERO and bar_range > atr_value * outlier_atr_mult:
        flags.append(BarQualityFlag.OUTLIER_RANGE)

    # Check 5 : Δt anormal (warning)
    # The series should have a constant step. A bigger gap means
    # missing intermediate bars (flagged as TIME_GAP rather than
    # the D4 completeness check, which is series-level).
    if prev_kline is not None and expected_dt_ms is not None:
        actual_dt = kline.close_time - prev_kline.close_time
        if actual_dt != expected_dt_ms:
            flags.append(BarQualityFlag.TIME_GAP)

    return BarQualityReport(flags=tuple(flags))


# ─── History completeness (D4) ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HistoryCompletenessReport:
    """Series-level completeness verdict per doc 11 §D4.

    Attributes:
        n_received: number of klines actually in the input list.
        n_expected: number of klines the caller expected (typically
            ``(end - start) / timeframe``).
        missing_pct: ``(n_expected - n_received) / n_expected``,
            clipped to 0 if ``n_expected`` is 0 or ``n_received``
            exceeds ``n_expected`` (no negative ratios).
        should_reject: ``True`` iff ``missing_pct >= tolerance``.
        should_interpolate: ``True`` iff
            ``0 < missing_pct < tolerance``. False if completeness is
            perfect (``missing_pct == 0``) — the bar list can be used
            as-is.
        flags: convenience tuple for the caller's audit log
            (``("missing_5_bars",)`` etc.). Empty if the series is
            complete.
    """

    n_received: int
    n_expected: int
    missing_pct: Decimal
    should_reject: bool
    should_interpolate: bool
    flags: tuple[str, ...] = field(default_factory=tuple)


def check_history_completeness(
    *,
    n_received: int,
    n_expected: int,
    tolerance: Decimal = DEFAULT_INTERPOLATION_LIMIT,
) -> HistoryCompletenessReport:
    """Apply doc 11 §D4 thresholds to a kline series count.

    Args:
        n_received: ``len(klines)`` the caller actually got.
        n_expected: how many bars the request was supposed to yield
            (e.g. ``(end_ms - start_ms) // timeframe_ms``).
        tolerance: interpolation cap ; below this fraction we suggest
            interpolation, at or above we reject. Defaults to
            :data:`DEFAULT_INTERPOLATION_LIMIT` (5 %).

    Returns:
        A :class:`HistoryCompletenessReport`.

    Raises:
        ValueError: on negative counts or out-of-range tolerance.

    Notes:
        ``n_received > n_expected`` clamps ``missing_pct`` to 0 and
        treats the series as complete — receiving extras is not a
        symptom of D4 (it could happen with an off-by-one in the
        request boundary). Ban a HARD-reject path here would be too
        strict.
    """
    if n_received < 0:
        msg = f"n_received must be >= 0, got {n_received}"
        raise ValueError(msg)
    if n_expected < 0:
        msg = f"n_expected must be >= 0, got {n_expected}"
        raise ValueError(msg)
    if tolerance < _ZERO or tolerance > Decimal("1"):
        msg = f"tolerance must be in [0, 1], got {tolerance}"
        raise ValueError(msg)

    if n_expected == 0:
        # Edge case : caller didn't expect any bars. Trivially complete.
        return HistoryCompletenessReport(
            n_received=n_received,
            n_expected=0,
            missing_pct=_ZERO,
            should_reject=False,
            should_interpolate=False,
            flags=(),
        )

    missing = max(n_expected - n_received, 0)
    missing_pct = Decimal(missing) / Decimal(n_expected)

    should_reject = missing_pct >= tolerance
    should_interpolate = _ZERO < missing_pct < tolerance

    flags = (f"missing_{missing}_bars",) if missing > 0 else ()

    return HistoryCompletenessReport(
        n_received=n_received,
        n_expected=n_expected,
        missing_pct=missing_pct,
        should_reject=should_reject,
        should_interpolate=should_interpolate,
        flags=flags,
    )
