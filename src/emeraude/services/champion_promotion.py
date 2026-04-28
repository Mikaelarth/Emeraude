"""Champion promotion gate (doc 10 R13 wiring).

Doc 10 §"R13 — Probabilistic + Deflated Sharpe Ratio" delivers
:func:`emeraude.agent.learning.sharpe_significance.compute_psr` and
:func:`compute_dsr` (Bailey & López de Prado 2012, 2014). Doc 10
mandates ``DSR >= 0.95`` for the production champion : a strategy
whose Deflated Sharpe Ratio falls below this threshold should not
be promoted to ACTIVE because the high empirical Sharpe might be a
selection-bias artifact of the optimization grid.

This service is the **bridge** that consumes a position history,
computes PSR + DSR, decides whether the doc 10 I13 criterion is
met, and emits a structured audit event so an operator can reconstruct
*why* a candidate was promoted (or not) on any given cycle.

Pattern is identical to :func:`evaluate_hoeffding_gate` in iter #43
R11 observability : pure function returning a decision dataclass +
optional audit emission. The :class:`ChampionLifecycle` itself is
not modified — keeping ``governance/`` focused on the state machine
and ``services/`` carrying the cross-module composition.

Composition pattern ::

    from emeraude.agent.execution.position_tracker import PositionTracker
    from emeraude.agent.governance.champion_lifecycle import (
        ChampionLifecycle,
    )
    from emeraude.services.champion_promotion import evaluate_promotion

    tracker = PositionTracker()
    lifecycle = ChampionLifecycle()

    decision = evaluate_promotion(
        positions=tracker.history(limit=200),
        n_trials=10,  # grid-search trials behind the candidate
    )
    if decision.allow_promotion:
        lifecycle.promote(
            champion_id="trend_v3",
            sharpe_walk_forward=decision.sharpe_ratio,
        )

The decision carries the full statistical diagnostic so the audit log
can replay : SR, n_samples, skewness, kurtosis, PSR, DSR, threshold,
verdict and reason.

Reference :

* Bailey & López de Prado (2012). *The Sharpe Ratio Efficient Frontier*.
  Journal of Risk 15(2) : 3-44.
* Bailey & López de Prado (2014). *The Deflated Sharpe Ratio*.
  Journal of Portfolio Management 40(5) : 94-107.
* Doc 10 §"R13" : ``DSR >= 0.95`` for the production champion.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.learning.performance_report import compute_performance_report
from emeraude.agent.learning.risk_metrics import compute_tail_metrics
from emeraude.agent.learning.sharpe_significance import (
    DEFAULT_DSR_THRESHOLD,
    compute_dsr,
    compute_psr,
    is_sharpe_significant,
)
from emeraude.infra import audit

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


_ZERO: Final[Decimal] = Decimal("0")
_THREE: Final[Decimal] = Decimal("3")

# Audit event type. Public so dashboards / tests can filter on it
# without importing a private name. Doc 10 R13 observability.
AUDIT_CHAMPION_PROMOTION_DECISION: Final[str] = "CHAMPION_PROMOTION_DECISION"

# Reason constants — stable strings for audit-log filtering.
REASON_BELOW_MIN_SAMPLES: Final[str] = "below_min_samples"
REASON_DSR_TOO_LOW: Final[str] = "dsr_too_low"
REASON_APPROVED: Final[str] = "approved"

# Minimum sample count before the gate even considers a verdict.
# PSR over fewer than ~30 observations is dominated by sampling
# noise. 30 matches the rest of the system's adaptive_min_trades
# floor (orchestrator.py, drift_monitor.py, risk_monitor.py).
_DEFAULT_MIN_SAMPLES: Final[int] = 30


# ─── Result ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Audit-friendly outcome of one :func:`evaluate_promotion` call.

    Attributes:
        sharpe_ratio: empirical SR of the candidate (per-trade).
            ``Decimal("0")`` below ``min_samples``.
        n_samples: count of closed positions consumed.
        skewness: empirical Fisher-Pearson skewness.
        kurtosis: empirical full kurtosis (NOT excess) — Gaussian = 3.
        psr: Probabilistic Sharpe Ratio at ``benchmark_sharpe = 0``.
            Probability the true SR exceeds zero.
        dsr: Deflated Sharpe Ratio — PSR adjusted for the
            multiple-testing benchmark across ``n_trials``.
        n_trials: grid-search trial count provided by the caller.
        threshold: doc 10 R13 floor (default 0.95).
        allow_promotion: ``True`` iff DSR >= threshold AND samples
            sufficient. Caller uses this to gate a
            :meth:`ChampionLifecycle.promote` call.
        reason: one of :data:`REASON_BELOW_MIN_SAMPLES`,
            :data:`REASON_DSR_TOO_LOW`, :data:`REASON_APPROVED`.
    """

    sharpe_ratio: Decimal
    n_samples: int
    skewness: Decimal
    kurtosis: Decimal
    psr: Decimal
    dsr: Decimal
    n_trials: int
    threshold: Decimal
    allow_promotion: bool
    reason: str


# ─── Public API ─────────────────────────────────────────────────────────────


def evaluate_promotion(
    *,
    positions: list[Position],
    n_trials: int,
    sharpe_variance: Decimal = Decimal("1"),
    threshold: Decimal = DEFAULT_DSR_THRESHOLD,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    emit_audit: bool = True,
) -> PromotionDecision:
    """Evaluate whether a candidate champion clears doc 10 R13.

    Step 1 — sample floor : ``n_samples >= min_samples``. Below the
    floor we refuse to promote regardless of the empirical Sharpe :
    PSR over a tiny sample is dominated by noise and any "approval"
    would be statistically meaningless.

    Step 2 — DSR significance : compute PSR + DSR via the doc 10
    R13 primitives, compare DSR to ``threshold`` (default 0.95).

    Args:
        positions: closed-position history (typically from
            :meth:`PositionTracker.history`). Open positions
            (``r_realized is None``) are filtered out.
        n_trials: number of grid-search configurations behind the
            candidate. Required by :func:`compute_dsr` to deflate
            the SR for multiple-testing bias. Must be ``>= 2``.
        sharpe_variance: estimated variance ``V[SR_k]`` across the
            grid-search trials. Default ``1`` (conservative — gives
            a larger SR* benchmark, harder to clear).
        threshold: DSR floor in ``[0, 1]``. Default ``0.95`` per
            doc 10 R13.
        min_samples: sample-floor for the gate. Default 30 (matches
            the rest of the system's adaptive thresholds). Must be
            ``>= 2`` (the PSR primitive requires ``n >= 2``).
        emit_audit: when ``True`` (default), emit one
            ``CHAMPION_PROMOTION_DECISION`` audit event. Set to
            ``False`` for dry-run / preview calls that should not
            pollute the audit trail.

    Returns:
        A :class:`PromotionDecision` with the full statistical
        diagnostic. ``allow_promotion`` is ``True`` iff every gate
        condition holds.

    Raises:
        ValueError: on ``n_trials < 2`` (forwarded from
            :func:`compute_dsr`'s validation), ``min_samples < 2``,
            or ``threshold`` outside ``[0, 1]``.
    """
    if min_samples < 2:  # noqa: PLR2004
        msg = f"min_samples must be >= 2, got {min_samples}"
        raise ValueError(msg)
    if not (_ZERO <= threshold <= Decimal("1")):
        msg = f"threshold must be in [0, 1], got {threshold}"
        raise ValueError(msg)

    returns = [p.r_realized for p in positions if p.r_realized is not None]
    n = len(returns)

    if n < min_samples:
        decision = PromotionDecision(
            sharpe_ratio=_ZERO,
            n_samples=n,
            skewness=_ZERO,
            kurtosis=_ZERO,
            psr=_ZERO,
            dsr=_ZERO,
            n_trials=n_trials,
            threshold=threshold,
            allow_promotion=False,
            reason=REASON_BELOW_MIN_SAMPLES,
        )
        if emit_audit:
            _emit_audit(decision)
        return decision

    # Empirical SR + moments via the existing pure modules so we don't
    # duplicate the implementation. compute_tail_metrics returns
    # excess_kurtosis (Gaussian = 0) ; PSR primitive expects full
    # kurtosis (Gaussian = 3) — convert at the seam.
    perf = compute_performance_report(positions)
    tail = compute_tail_metrics(returns)

    sharpe = perf.sharpe_ratio
    skewness = tail.skewness
    kurtosis = tail.excess_kurtosis + _THREE

    psr = compute_psr(
        sharpe_ratio=sharpe,
        n_samples=n,
        skewness=skewness,
        kurtosis=kurtosis,
    )
    dsr = compute_dsr(
        sharpe_ratio=sharpe,
        n_samples=n,
        skewness=skewness,
        kurtosis=kurtosis,
        n_trials=n_trials,
        sharpe_variance=sharpe_variance,
    )

    if is_sharpe_significant(dsr, threshold=threshold):
        allow = True
        reason = REASON_APPROVED
    else:
        allow = False
        reason = REASON_DSR_TOO_LOW

    decision = PromotionDecision(
        sharpe_ratio=sharpe,
        n_samples=n,
        skewness=skewness,
        kurtosis=kurtosis,
        psr=psr,
        dsr=dsr,
        n_trials=n_trials,
        threshold=threshold,
        allow_promotion=allow,
        reason=reason,
    )
    if emit_audit:
        _emit_audit(decision)
    return decision


# ─── Internals ──────────────────────────────────────────────────────────────


def _emit_audit(decision: PromotionDecision) -> None:
    """Log the doc 10 R13 ``CHAMPION_PROMOTION_DECISION`` audit event.

    Decimal fields are stringified so the JSON column round-trips
    without precision loss (matches the convention used by the
    Hoeffding observability event).
    """
    audit.audit(
        AUDIT_CHAMPION_PROMOTION_DECISION,
        {
            "n_samples": decision.n_samples,
            "n_trials": decision.n_trials,
            "sharpe_ratio": str(decision.sharpe_ratio),
            "skewness": str(decision.skewness),
            "kurtosis": str(decision.kurtosis),
            "psr": str(decision.psr),
            "dsr": str(decision.dsr),
            "threshold": str(decision.threshold),
            "allow_promotion": decision.allow_promotion,
            "reason": decision.reason,
        },
    )
