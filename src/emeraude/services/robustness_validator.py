"""Robustness validation gate (doc 10 R4 wiring).

Doc 10 §"R4 — Walk-forward + parameter robustness check" delivers
:func:`emeraude.agent.learning.robustness.compute_robustness_report`
(López de Prado 2018, ch. 11). The criterion I4 mandates that any
publishable champion clear the parameter-perturbation test :
``destructive_fraction <= 25 %`` over a ±20 % sweep.

This service is the **bridge** that consumes a pre-computed
:class:`RobustnessReport`, applies the doc 10 I4 criterion, and
emits an audit event with the verdict + per-parameter heatmap
diagnostic for replay.

Pattern is identical to :func:`evaluate_promotion` (iter #50,
R13 PSR/DSR) and :func:`validate_coverage` (iter #54, R15
conformal) : pure function returning a decision dataclass +
optional audit emission. The :class:`ChampionLifecycle` is **not**
modified — the caller is responsible for chaining the gate before
``promote()`` if they want to enforce I4 at the lifecycle level.

Why pre-computed report ? :func:`compute_robustness_report` needs
an ``objective_fn`` callback (the caller's Sharpe / walk-forward /
whatever metric is chosen). Inlining the callback into the
validator would couple the service layer to a specific metric
choice. The split keeps the validator cohesive — verdict + audit —
while delegating the heavy lifting to the caller.

Composition pattern ::

    from emeraude.agent.learning.robustness import compute_robustness_report
    from emeraude.services.robustness_validator import validate_robustness


    # Caller's domain logic : choose a Sharpe objective.
    def my_objective(params: dict[str, Decimal]) -> Decimal:
        return run_walk_forward(params).sharpe


    report = compute_robustness_report(
        baseline_score=current_sharpe,
        baseline_params=champion_params,
        objective_fn=my_objective,
    )
    decision = validate_robustness(report=report)
    if decision.is_robust:
        lifecycle.promote(...)

Reference :

* López de Prado (2018). *Advances in Financial Machine Learning*,
  ch. 11 (Backtesting through Cross-Validation).
* Doc 10 §"R4" critère mesurable I4 : "champion robuste à ±20 %
  perturbation paramètres".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.learning.robustness import (
    DEFAULT_MAX_DESTRUCTIVE_FRACTION,
)
from emeraude.infra import audit

if TYPE_CHECKING:
    from emeraude.agent.learning.robustness import RobustnessReport


_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

# Audit event type. Public so dashboards / tests can filter on it
# without importing a private name. Doc 10 R4 observability.
AUDIT_ROBUSTNESS_VALIDATION: Final[str] = "ROBUSTNESS_VALIDATION"

# Reason constants — stable strings for audit-log filtering.
REASON_ROBUST: Final[str] = "robust"
REASON_FRAGILE: Final[str] = "fragile"


# ─── Result ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RobustnessValidationDecision:
    """Audit-friendly outcome of one :func:`validate_robustness` call.

    The full per-parameter heatmap lives in the wrapped
    :class:`RobustnessReport` ; this decision carries only the
    summary fields that fit into a flat audit row.

    Attributes:
        baseline_score: champion's reference metric.
        n_params: number of parameters perturbed.
        total_perturbations: total perturbation count.
        total_destructive: how many perturbations exceeded the
            destruction threshold.
        destructive_fraction: cohort-level fraction.
        max_destructive_fraction: the I4 threshold compared against
            (default 0.25).
        is_robust: ``True`` iff
            ``destructive_fraction <= max_destructive_fraction``.
        reason: :data:`REASON_ROBUST` or :data:`REASON_FRAGILE`.
    """

    baseline_score: Decimal
    n_params: int
    total_perturbations: int
    total_destructive: int
    destructive_fraction: Decimal
    max_destructive_fraction: Decimal
    is_robust: bool
    reason: str


# ─── Public API ─────────────────────────────────────────────────────────────


def validate_robustness(
    *,
    report: RobustnessReport,
    max_destructive_fraction: Decimal = DEFAULT_MAX_DESTRUCTIVE_FRACTION,
    emit_audit: bool = True,
) -> RobustnessValidationDecision:
    """Apply the doc 10 I4 criterion to a precomputed robustness report.

    Args:
        report: from
            :func:`emeraude.agent.learning.robustness.compute_robustness_report`.
            Caller is responsible for the underlying ``objective_fn``
            and the perturbation sweep parameters.
        max_destructive_fraction: I4 threshold in ``[0, 1]``.
            Default ``0.25`` per doc 10 R4 — a champion that
            destructively breaks on more than a quarter of the
            perturbations is rejected.
        emit_audit: when ``True`` (default), emit one
            ``ROBUSTNESS_VALIDATION`` audit event. Set to ``False``
            for dry-run / preview calls.

    Returns:
        A :class:`RobustnessValidationDecision`.

    Raises:
        ValueError: on ``max_destructive_fraction`` outside ``[0, 1]``.
    """
    if not (_ZERO <= max_destructive_fraction <= _ONE):
        msg = f"max_destructive_fraction must be in [0, 1], got {max_destructive_fraction}"
        raise ValueError(msg)

    is_robust_verdict = report.destructive_fraction <= max_destructive_fraction
    reason = REASON_ROBUST if is_robust_verdict else REASON_FRAGILE

    decision = RobustnessValidationDecision(
        baseline_score=report.baseline_score,
        n_params=report.n_params,
        total_perturbations=report.total_perturbations,
        total_destructive=report.total_destructive,
        destructive_fraction=report.destructive_fraction,
        max_destructive_fraction=max_destructive_fraction,
        is_robust=is_robust_verdict,
        reason=reason,
    )
    if emit_audit:
        _emit_audit(decision, report=report)
    return decision


# ─── Internals ──────────────────────────────────────────────────────────────


def _emit_audit(
    decision: RobustnessValidationDecision,
    *,
    report: RobustnessReport,
) -> None:
    """Log the doc 10 R4 ``ROBUSTNESS_VALIDATION`` audit event.

    The audit payload includes the per-parameter destructive fractions
    so an operator can spot which knob is the most fragile without
    re-running the sweep. The full perturbation list (one row per
    point) stays in :attr:`RobustnessReport.perturbations` — too
    voluminous for the audit row.
    """
    audit.audit(
        AUDIT_ROBUSTNESS_VALIDATION,
        {
            "baseline_score": str(decision.baseline_score),
            "n_params": decision.n_params,
            "total_perturbations": decision.total_perturbations,
            "total_destructive": decision.total_destructive,
            "destructive_fraction": str(decision.destructive_fraction),
            "max_destructive_fraction": str(decision.max_destructive_fraction),
            "is_robust": decision.is_robust,
            "reason": decision.reason,
            # Per-parameter heatmap : `name=fraction` joined.
            "per_param_destructive_fraction": ";".join(
                f"{p.param_name}={p.destructive_fraction}" for p in report.per_param
            ),
            # Worst per-parameter degradation : same flat encoding.
            "per_param_worst_degradation": ";".join(
                f"{p.param_name}={p.worst_degradation}" for p in report.per_param
            ),
        },
    )
