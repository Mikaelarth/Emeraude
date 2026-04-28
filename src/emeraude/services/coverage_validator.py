"""Conformal coverage validator (doc 10 R15 wiring).

Doc 10 §"R15 — Conformal Prediction" delivers
:mod:`emeraude.agent.learning.conformal` (Vovk, Gammerman, Shafer
2005). The criterion I15 mandates that ``intervalles conformes
couvrent >= 90 % des observations`` ; if the empirical coverage
drifts below the nominal target, the model has lost calibration.

This service is the **bridge** that consumes a position history,
constructs ``(prediction, outcome)`` pairs, computes the empirical
coverage of split-conformal intervals, and emits an audit event
with the verdict.

Pattern is identical to :func:`evaluate_promotion` in iter #50
R13 wiring : pure function returning a decision dataclass +
optional audit emission. Unlike the periodic monitors
(:class:`DriftMonitor` / :class:`RiskMonitor`) the gate is
one-shot — call it on demand from the operator's review or a
scheduled task ; the caller decides cadence.

Prediction model :

* Each closed position carries a ``confidence`` (iter #42 R1
  wiring) in ``[0, 1]`` and a ``r_realized`` outcome.
* The "predicted R-multiple" is built as
  ``prediction = confidence * prediction_target``. With the
  doc-04 default ``target = 2 R``, a 0.9-confidence trade
  predicts 1.8 R, a 0.5-confidence trade predicts 1.0 R.
  This is the simplest faithful proxy until the orchestrator
  exposes a richer per-trade R-multiple prediction
  (anti-règle A1).
* ``outcome = r_realized`` (signed). Residuals
  ``|outcome - prediction|`` feed the conformal quantile.

Coverage estimation : leave-one-in pattern. The full residual set
yields the quantile, then every position's interval is checked
against its own outcome. Slightly optimistic vs the canonical
split (calibration / test) but standard for a one-shot validator
with limited history.

Reference :

* Vovk, Gammerman, Shafer (2005). *Algorithmic Learning in a
  Random World*. Original split-conformal framework.
* Doc 10 §"R15" critère mesurable I15 : "Intervalles conformes
  couvrent >= 90 % des observations".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.learning.conformal import (
    DEFAULT_ALPHA,
    DEFAULT_COVERAGE_TOLERANCE,
    compute_coverage,
    compute_interval,
    compute_residuals,
)
from emeraude.infra import audit

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

# Audit event type. Public so dashboards / tests can filter on it
# without importing a private name. Doc 10 R15 observability.
AUDIT_COVERAGE_VALIDATION: Final[str] = "COVERAGE_VALIDATION"

# Reason constants — stable strings for audit-log filtering.
REASON_BELOW_MIN_SAMPLES: Final[str] = "below_min_samples"
REASON_COVERAGE_DRIFT: Final[str] = "coverage_drift"
REASON_VALID: Final[str] = "valid"

# Doc 10 R15 default prediction target : the doc 04 R/R floor.
# The orchestrator forces R = 2 by construction (4/2 ATR multipliers),
# so the predicted R-multiple is ``confidence * 2``. Configurable
# for callers who later evolve the orchestrator to expose a richer
# per-trade prediction.
DEFAULT_PREDICTION_TARGET: Final[Decimal] = Decimal("2")

# Minimum sample floor before the gate considers a verdict. Doc 10
# I15 wording is "100 trades" but a synthetic 30-sample window is
# the working minimum we use across other monitors (drift, risk,
# champion_promotion). The caller can tighten via ``min_samples``.
_DEFAULT_MIN_SAMPLES: Final[int] = 30


# ─── Result ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CoverageValidationDecision:
    """Audit-friendly outcome of one :func:`validate_coverage` call.

    Attributes:
        n_predictions: count of closed positions consumed.
        target_coverage: ``1 - alpha``. Doc 10 default 0.90.
        empirical_coverage: realized fraction of intervals
            covering their outcome. ``Decimal("0")`` below
            ``min_samples``.
        quantile: ``(1 - alpha)`` quantile of residuals used to
            size the interval. ``Decimal("0")`` below ``min_samples``.
        tolerance: maximum allowed gap between empirical and
            target coverage.
        coverage_valid: ``True`` iff ``|empirical - target| <= tolerance``
            AND samples sufficient.
        reason: one of :data:`REASON_BELOW_MIN_SAMPLES`,
            :data:`REASON_COVERAGE_DRIFT`, :data:`REASON_VALID`.
    """

    n_predictions: int
    target_coverage: Decimal
    empirical_coverage: Decimal
    quantile: Decimal
    tolerance: Decimal
    coverage_valid: bool
    reason: str


# ─── Public API ─────────────────────────────────────────────────────────────


def validate_coverage(
    *,
    positions: list[Position],
    alpha: Decimal = DEFAULT_ALPHA,
    tolerance: Decimal = DEFAULT_COVERAGE_TOLERANCE,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    prediction_target: Decimal = DEFAULT_PREDICTION_TARGET,
    emit_audit: bool = True,
) -> CoverageValidationDecision:
    """Evaluate the doc 10 R15 / I15 coverage criterion on a history.

    Step 1 — sample floor : ``n_predictions >= min_samples``. Below
    the floor the empirical coverage is dominated by sampling noise ;
    the gate stays silent and reports ``REASON_BELOW_MIN_SAMPLES``.

    Step 2 — coverage drift : compute the conformal quantile from
    all residuals, build the per-prediction interval, count coverage,
    compare ``|empirical - (1 - alpha)| <= tolerance``. ``True`` ->
    ``REASON_VALID`` ; ``False`` -> ``REASON_COVERAGE_DRIFT``.

    Args:
        positions: closed-position history (typically from
            :meth:`PositionTracker.history`). Positions without
            ``confidence`` (legacy rows pre-iter-#42) or without
            ``r_realized`` (still open) are filtered out.
        alpha: miscoverage rate. Default ``0.10`` -> 90 % nominal.
        tolerance: maximum allowed gap between empirical and target
            coverage. Default ``0.05`` per doc 10 I15.
        min_samples: floor before the gate considers a verdict.
            Default 30.
        prediction_target: scales ``confidence`` into a predicted
            R-multiple. Default 2 R per doc 04 R/R floor.
        emit_audit: when ``True`` (default), emit one
            ``COVERAGE_VALIDATION`` audit event. Set ``False`` for
            dry-run / preview calls.

    Returns:
        A :class:`CoverageValidationDecision` with the full
        diagnostic.

    Raises:
        ValueError: forwarded from the conformal primitives on
            ``alpha`` outside ``(0, 1)`` or ``tolerance < 0``.
    """
    if min_samples < 1:
        msg = f"min_samples must be >= 1, got {min_samples}"
        raise ValueError(msg)

    predictions, outcomes = _extract_pairs(positions, prediction_target)
    n = len(predictions)
    target = _ONE - alpha

    if n < min_samples:
        decision = CoverageValidationDecision(
            n_predictions=n,
            target_coverage=target,
            empirical_coverage=_ZERO,
            quantile=_ZERO,
            tolerance=tolerance,
            coverage_valid=False,
            reason=REASON_BELOW_MIN_SAMPLES,
        )
        if emit_audit:
            _emit_audit(decision)
        return decision

    residuals = compute_residuals(predictions, outcomes)
    intervals = [
        compute_interval(prediction=p, calibration_residuals=residuals, alpha=alpha)
        for p in predictions
    ]
    coverage_report = compute_coverage(intervals, outcomes)

    empirical = coverage_report.empirical_coverage
    quantile = intervals[0].quantile  # uniform across the cohort

    gap = abs(empirical - target)
    valid = gap <= tolerance
    reason = REASON_VALID if valid else REASON_COVERAGE_DRIFT

    decision = CoverageValidationDecision(
        n_predictions=n,
        target_coverage=target,
        empirical_coverage=empirical,
        quantile=quantile,
        tolerance=tolerance,
        coverage_valid=valid,
        reason=reason,
    )
    if emit_audit:
        _emit_audit(decision)
    return decision


# ─── Internals ──────────────────────────────────────────────────────────────


def _extract_pairs(
    positions: list[Position],
    prediction_target: Decimal,
) -> tuple[list[Decimal], list[Decimal]]:
    """Pull ``(prediction, outcome)`` pairs from a closed-position history.

    Filters applied :

    * ``position.confidence is None`` — legacy row opened before
      iter #42 (no recorded prediction).
    * ``position.r_realized is None`` — position still open or
      corrupt row.

    Args:
        positions: closed-position records.
        prediction_target: scaled by confidence to derive the
            predicted R-multiple.

    Returns:
        ``(predictions, outcomes)`` parallel lists. Both empty
        when no eligible row exists.
    """
    predictions: list[Decimal] = []
    outcomes: list[Decimal] = []
    for p in positions:
        if p.confidence is None or p.r_realized is None:
            continue
        predictions.append(p.confidence * prediction_target)
        outcomes.append(p.r_realized)
    return predictions, outcomes


def _emit_audit(decision: CoverageValidationDecision) -> None:
    """Log the doc 10 R15 ``COVERAGE_VALIDATION`` audit event."""
    audit.audit(
        AUDIT_COVERAGE_VALIDATION,
        {
            "n_predictions": decision.n_predictions,
            "target_coverage": str(decision.target_coverage),
            "empirical_coverage": str(decision.empirical_coverage),
            "quantile": str(decision.quantile),
            "tolerance": str(decision.tolerance),
            "coverage_valid": decision.coverage_valid,
            "reason": decision.reason,
        },
    )
