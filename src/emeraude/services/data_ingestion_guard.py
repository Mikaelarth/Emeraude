"""Cycle-level data-ingestion guard composing D3 + D4 with audit.

The infra modules :mod:`emeraude.infra.data_quality` (iter #86) ship
**pure functions** at the bar / series level. Their callers — typically
the orchestrator's data-fetch loop — need a single service-level entry
point that :

1. Validates the freshly-fetched series via
   :func:`check_history_completeness` (D4).
2. Validates each kline via :func:`check_bar_quality` (D3, all five
   checks : flat volume, invalid high/low, close out of range,
   outlier range, time gap).
3. Emits exactly **one** audit event per cycle (success or rejection)
   per doc 11 §5 :

       Chaque cycle doit produire dans audit_log un événement
       data_ingestion_completed avec [...] bar_quality map [...].

4. Returns an :class:`IngestionReport` so the caller can decide what
   to do with the cycle (continue, skip, escalate the breaker).

The module is **opinionated** about audit emission : every call to
:func:`validate_and_audit_klines` produces exactly one audit row, so
the doc 11 invariant "0 cycle sans data_quality field rempli" is
satisfied by construction.

Out of scope for this iter (cf. R2) :

* Wiring into :mod:`auto_trader` — the call site change requires
  handling the ``should_reject`` return + adapting :class:`CycleReport`.
  Lands in a dedicated iter so a regression there is bisectable.
* :class:`KlineSnapshot` integration (D6 hash propagation in the
  audit payload). Live snapshotting needs the live fetch path
  finalised first.
* :func:`universe_at` integration (D2). Universe is a backtest
  concept, not live trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from emeraude.infra import audit
from emeraude.infra.data_quality import (
    BarQualityFlag,
    BarQualityReport,
    HistoryCompletenessReport,
    check_bar_quality,
    check_history_completeness,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from emeraude.infra.market_data import Kline


# ─── Audit event types ──────────────────────────────────────────────────────


#: Doc 11 §5 mandates ``data_ingestion_completed`` per cycle. We
#: namespace the type uppercase to match the existing
#: ``"<DOMAIN>_<ACTION>"`` convention (cf. ``POSITION_OPENED``,
#: ``MODE_CHANGED``, ``BAR_QUALITY_*``).
AUDIT_DATA_INGESTION_COMPLETED: Final[str] = "DATA_INGESTION_COMPLETED"


# ─── Report dataclass ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """Aggregate quality verdict for one fetched kline series.

    Attributes:
        symbol: ticker pair the report covers (e.g. ``"BTCUSDT"``).
        completeness: series-level completeness verdict per
            :func:`check_history_completeness` (D4).
        per_bar: one :class:`BarQualityReport` per kline, in input
            order. Empty when the input klines are empty (allowed —
            triggers the ``no data fetched`` rejection path).
        flag_counts: dict ``flag_value -> count`` of every
            :class:`BarQualityFlag` observed across all bars. Used as
            the audit payload's ``bar_quality`` map.
        should_reject: ``True`` iff the caller MUST skip the cycle.
            Hard-reject conditions :

            * series ``HistoryCompletenessReport.should_reject`` (D4
              ≥ 5 % missing bars) ;
            * any bar's ``BarQualityReport.should_reject`` (D3
              corruption garantie : ``INVALID_HIGH_LOW`` /
              ``CLOSE_OUT_OF_RANGE``) ;
            * empty kline list (no data fetched at all).
        rejection_reason: human-readable reason when ``should_reject``
            is True. Empty otherwise. Logged in the audit payload.
    """

    symbol: str
    completeness: HistoryCompletenessReport
    per_bar: tuple[BarQualityReport, ...]
    flag_counts: dict[str, int]
    should_reject: bool
    rejection_reason: str


# ─── Public entry point ────────────────────────────────────────────────────


def validate_and_audit_klines(
    klines: list[Kline],
    *,
    symbol: str,
    interval: str,
    expected_count: int,
    atr_value: Decimal | None = None,
    expected_dt_ms: int | None = None,
) -> IngestionReport:
    """Validate a freshly-fetched kline series and emit one audit event.

    Pipeline :

    1. :func:`check_history_completeness` against ``expected_count``.
       If the series is missing too many bars (>= 5 % per default
       tolerance), the report flags ``should_reject``.
    2. :func:`check_bar_quality` on each kline. The previous bar
       and ``expected_dt_ms`` are forwarded so the time-gap check
       can fire on cadence breaks. ``atr_value`` (if known by the
       caller) feeds the outlier-range check.
    3. Aggregate per-bar flags into a ``flag_counts`` dict suitable
       for the audit payload.
    4. Emit ``DATA_INGESTION_COMPLETED`` exactly once with the full
       diagnostic payload.

    Args:
        klines: the freshly-fetched series (most recent last, as
            returned by :func:`market_data.get_klines`).
        symbol: e.g. ``"BTCUSDT"``. Used in the audit payload.
        interval: kline interval string (``"1h"``, ``"5m"``, ...).
            Used in the audit payload.
        expected_count: the number of bars the caller expected from
            the fetch (typically the ``limit`` argument).
        atr_value: optional ATR_N reference for the outlier-range
            check. Pass ``None`` at cold start when ATR is not yet
            computable.
        expected_dt_ms: expected delta in milliseconds between
            consecutive ``close_time`` values (e.g. ``60_000`` for
            1m, ``3_600_000`` for 1h). Pass ``None`` to skip the
            time-gap check (e.g. when the caller doesn't know the
            interval ms cleanly).

    Returns:
        An :class:`IngestionReport`. Caller MUST check
        :attr:`should_reject` and skip the cycle when True.
    """
    completeness = check_history_completeness(
        n_received=len(klines),
        n_expected=expected_count,
    )

    per_bar_reports: list[BarQualityReport] = []
    flag_counts: dict[str, int] = {}
    prev: Kline | None = None
    for kline in klines:
        report = check_bar_quality(
            kline,
            prev_kline=prev,
            expected_dt_ms=expected_dt_ms,
            atr_value=atr_value,
        )
        per_bar_reports.append(report)
        for flag in report.flags:
            flag_counts[flag.value] = flag_counts.get(flag.value, 0) + 1
        prev = kline

    # Decision : empty fetch is a hard reject (no data, no decision).
    # Otherwise, hard-reject conditions cascade : completeness reject
    # OR any bar with a hard-reject flag.
    should_reject = False
    rejection_reason = ""
    if not klines and expected_count > 0:
        should_reject = True
        rejection_reason = "no klines fetched"
    elif completeness.should_reject:
        should_reject = True
        rejection_reason = (
            f"series too incomplete : missing {completeness.missing_pct} "
            f"({completeness.n_received}/{completeness.n_expected} bars)"
        )
    else:
        for idx, report in enumerate(per_bar_reports):
            if report.should_reject:
                should_reject = True
                rejection_reason = f"bar {idx} corrupted : {[f.value for f in report.flags]}"
                break

    final_report = IngestionReport(
        symbol=symbol,
        completeness=completeness,
        per_bar=tuple(per_bar_reports),
        flag_counts=dict(flag_counts),
        should_reject=should_reject,
        rejection_reason=rejection_reason,
    )

    _emit_audit(
        symbol=symbol,
        interval=interval,
        expected_count=expected_count,
        report=final_report,
    )
    return final_report


# ─── Internal audit emit ──────────────────────────────────────────────────


def _emit_audit(
    *,
    symbol: str,
    interval: str,
    expected_count: int,
    report: IngestionReport,
) -> None:
    """Emit the doc 11 §5 ``DATA_INGESTION_COMPLETED`` event.

    The payload is intentionally compact : the full per-bar reports
    are NOT serialised (would be too verbose for a routine audit).
    Instead we surface the ``flag_counts`` map (e.g.
    ``{"flat_volume": 2, "time_gap": 1}``) which is enough for
    forensic queries like "show me the cycles where time_gap fired".

    Anti-règle A8 : on n'avale pas l'erreur. Even on a rejected cycle,
    the audit row is emitted with ``status="rejected"`` so a
    post-mortem query can find every aborted cycle by event_type
    alone.
    """
    payload: dict[str, object] = {
        "symbol": symbol,
        "interval": interval,
        "n_received": report.completeness.n_received,
        "n_expected": expected_count,
        "missing_pct": str(report.completeness.missing_pct),
        "bar_quality": dict(report.flag_counts),
        "status": "rejected" if report.should_reject else "ok",
    }
    if report.rejection_reason:
        payload["rejection_reason"] = report.rejection_reason
    audit.audit(AUDIT_DATA_INGESTION_COMPLETED, payload)


# ─── Convenience inspector (no audit) ──────────────────────────────────────


def summarize_flags(reports: list[BarQualityReport]) -> dict[str, int]:
    """Count occurrences of each :class:`BarQualityFlag` across reports.

    Pure function exposed for callers that want the same flag-counts
    aggregation logic as the audit payload but without emitting an
    audit row (e.g. backtest harness counting anomalies in offline
    series).
    """
    counts: dict[str, int] = {}
    for report in reports:
        for flag in report.flags:
            counts[flag.value] = counts.get(flag.value, 0) + 1
    return counts


# Re-export for convenience.
__all__ = [
    "AUDIT_DATA_INGESTION_COMPLETED",
    "BarQualityFlag",
    "IngestionReport",
    "summarize_flags",
    "validate_and_audit_klines",
]
