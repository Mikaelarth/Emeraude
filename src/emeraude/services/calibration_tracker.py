"""R1 calibration loop wiring : positions history -> Brier + ECE.

Doc 10 §"R1 — Calibration tracking" defines the analytics primitives
in :mod:`emeraude.agent.learning.calibration`. This module is the
**bridge** that pulls ``(predicted_confidence, won)`` pairs out of
the position history and feeds them to the primitives — closing the
loop between what the agent *predicted* and what *actually happened*.

Design (anti-règle A1) :

* No new state — the source of truth is the positions table updated
  by :class:`PositionTracker` at open + close. Everything is derived.
* No I/O at module-import time. Caller passes a :class:`Position`
  list (typically from ``tracker.history(limit=N)``). The bridge
  filters out positions that lack the ingredients (``confidence is
  None`` for legacy rows opened before migration 008, or
  ``r_realized is None`` for still-open rows) and returns the
  primitive's report.
* The ``won`` outcome is derived from ``r_realized > 0`` to match
  the convention the rest of the learning stack uses
  (:meth:`StrategyBandit.update_outcome`,
  :meth:`RegimeMemory.record_outcome`).

Composition pattern ::

    from emeraude.agent.execution.position_tracker import PositionTracker
    from emeraude.services.calibration_tracker import (
        compute_calibration_from_positions,
        is_well_calibrated_history,
    )

    tracker = PositionTracker()
    history = tracker.history(limit=200)
    report = compute_calibration_from_positions(history)
    if not is_well_calibrated_history(report):
        # ECE > 5 % over the available trades : surface to UI / freeze
        # adaptive sizing / log a calibration_drift event.
        ...

The calibration criterion I1 ("ECE < 5 % sur 100 trades", doc 10 R1)
is decided by the underlying primitive ; this module forwards the
threshold transparently.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.learning.calibration import (
    DEFAULT_ECE_THRESHOLD,
    DEFAULT_N_BINS,
    CalibrationReport,
    compute_calibration_report,
    is_well_calibrated,
)

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


_ZERO: Final[Decimal] = Decimal("0")


def extract_predictions_outcomes(
    positions: list[Position],
) -> tuple[list[Decimal], list[bool]]:
    """Pull ``(confidence, won)`` pairs from a closed-position history.

    Filters applied :

    * ``position.confidence is None`` — legacy row opened before
      migration 008. No prediction was recorded ; cannot contribute
      to the calibration loop.
    * ``position.r_realized is None`` — position is still open (or
      a corrupt row that escaped the close path). No outcome ;
      cannot contribute.

    The remaining rows yield two parallel lists of equal length, ready
    to be passed to :func:`compute_calibration_report`.

    Args:
        positions: typically the result of ``tracker.history(limit=N)``.
            Order does not affect the calibration computation
            (the primitives are commutative over the input).

    Returns:
        ``(predictions, outcomes)``. Either both are empty (no
        eligible row) or both have the same length.
    """
    predictions: list[Decimal] = []
    outcomes: list[bool] = []
    for p in positions:
        if p.confidence is None or p.r_realized is None:
            continue
        predictions.append(p.confidence)
        outcomes.append(p.r_realized > _ZERO)
    return predictions, outcomes


def compute_calibration_from_positions(
    positions: list[Position],
    *,
    n_bins: int = DEFAULT_N_BINS,
) -> CalibrationReport:
    """Build the doc 10 R1 calibration report from a position history.

    Args:
        positions: closed positions to evaluate (typically from
            :meth:`PositionTracker.history`). Open positions and
            legacy rows lacking ``confidence`` are filtered out.
        n_bins: reliability-diagram resolution. Default 10 per doc
            10 R1 (matches the canonical literature binning).

    Returns:
        A :class:`CalibrationReport`. ``n_samples == 0`` when no
        eligible row exists ; the primitive returns zero-filled
        per-bin stats in that case so the UI can render an empty
        diagram without branching.

    Raises:
        ValueError: forwarded from
            :func:`compute_calibration_report` on invalid bins.
    """
    predictions, outcomes = extract_predictions_outcomes(positions)
    return compute_calibration_report(predictions, outcomes, n_bins=n_bins)


def is_well_calibrated_history(
    report: CalibrationReport,
    *,
    threshold: Decimal = DEFAULT_ECE_THRESHOLD,
    min_samples: int = 100,
) -> bool:
    """Return True iff the report passes the doc 10 I1 acceptance gate.

    Doc 10 R1 criterion I1 reads "ECE < 5 % sur 100 trades". This
    helper enforces *both* halves : the report must have at least
    ``min_samples`` rows AND the underlying primitive must report
    well-calibrated. Below the sample threshold the result is
    ``False`` — there is not enough data to declare the agent's
    confidences calibrated.

    Args:
        report: from :func:`compute_calibration_from_positions`.
        threshold: maximum acceptable ECE in ``[0, 1]``. Default
            5 % per doc 10 R1.
        min_samples: minimum number of (confidence, outcome) pairs.
            Default 100 per doc 10 I1.

    Returns:
        Boolean verdict.

    Raises:
        ValueError: forwarded from
            :func:`emeraude.agent.learning.calibration.is_well_calibrated`.
    """
    if min_samples < 0:
        msg = f"min_samples must be >= 0, got {min_samples}"
        raise ValueError(msg)
    if report.n_samples < min_samples:
        return False
    return is_well_calibrated(report, threshold=threshold)
