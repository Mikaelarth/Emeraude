"""Parameter-robustness check (doc 10 R4 partie 2).

Doc 10 §"R4 — Walk-forward + parameter robustness check" requires
that any "champion" found by grid search resists a **±20 %
perturbation of each parameter individually**. If a small perturbation
destroys the performance, the champion is an overfit local optimum
and must be rejected.

Iter #30 (walk-forward windowing) shipped the **temporal** validation.
This iter ships the **parametric** validation — sweep each parameter
across a small grid centered on the baseline value, run the caller's
objective function on each perturbed config, and aggregate the result
into a stability heatmap.

Method (one parameter at a time, the others fixed at baseline) :

1. For each ``param_name`` in ``baseline_params`` :
   * For each side ``-1, +1`` and each step ``s = 1..n_per_side`` :
     compute ``offset = sign * (s / n_per_side) * perturbation_pct``
     and the perturbed value ``baseline_value * (1 + offset)``.
   * Call ``objective_fn(perturbed_params)`` (with that one
     parameter replaced).
   * ``degradation = (baseline_score - perturbed_score) / baseline_score``.
     ``is_destructive = degradation > destruction_threshold``.
2. Aggregate per-parameter : ``destructive_fraction =
   n_destructive / total_perturbations``.
3. Aggregate cohort : same fraction over all (param, perturbation)
   pairs. Doc 10 R4 criterion I4 :
   ``destructive_fraction <= 0.25`` for a publishable champion.

Robustness against ``objective_fn`` failures : a perturbation that
makes the objective raise an :class:`Exception` is counted as
destructive (score forced to ``0`` -> ``degradation = 1`` ->
above any reasonable threshold). The cohort's stability tells us if
the champion is structurally sensitive ; the per-param breakdown
tells us *which* knob is fragile.

Pure module : no I/O, no DB, no NumPy. Decimal everywhere ; the
caller's ``objective_fn`` is responsible for its own state (Sharpe
computation, walk-forward, whatever metric is chosen).

Reference :

* López de Prado (2018). *Advances in Financial Machine Learning*,
  ch. 11 (Backtesting through Cross-Validation).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

# Doc 10 R4 default : ±20 % perturbation around each baseline value.
DEFAULT_PERTURBATION_PCT: Final[Decimal] = Decimal("0.20")
# Default sweep resolution. ``n_per_side = 2`` gives 4 perturbations
# per parameter : -pct, -pct/2, +pct/2, +pct.
DEFAULT_N_PER_SIDE: Final[int] = 2
# Doc 10 R4 : "fraction des perturbations qui dégradent la perf >
# 30 %" -> the 30 % threshold for declaring a perturbation destructive.
DEFAULT_DESTRUCTION_THRESHOLD: Final[Decimal] = Decimal("0.30")
# Doc 10 R4 criterion I4 : destructive fraction <= 25 % for a
# publishable champion.
DEFAULT_MAX_DESTRUCTIVE_FRACTION: Final[Decimal] = Decimal("0.25")


# ─── Result types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PerturbationResult:
    """One (param, perturbed_value) evaluation.

    Attributes:
        param_name: which parameter was perturbed.
        baseline_value: the unperturbed value of that parameter.
        perturbed_value: ``baseline_value * (1 + offset)``.
        offset_pct: signed perturbation ratio (e.g. ``-0.20``,
            ``+0.10``).
        baseline_score: reference score (same across all
            perturbations of one cohort).
        perturbed_score: score returned by ``objective_fn`` on the
            perturbed config. ``Decimal('0')`` when the objective
            raised an exception.
        degradation: ``(baseline_score - perturbed_score) /
            baseline_score`` ; positive = perturbation hurt.
        is_destructive: ``degradation > destruction_threshold``.
    """

    param_name: str
    baseline_value: Decimal
    perturbed_value: Decimal
    offset_pct: Decimal
    baseline_score: Decimal
    perturbed_score: Decimal
    degradation: Decimal
    is_destructive: bool


@dataclass(frozen=True, slots=True)
class ParamStability:
    """Per-parameter row of the stability heatmap.

    Attributes:
        param_name: parameter under test.
        n_perturbations: total perturbations applied to this param.
        n_destructive: count of perturbations exceeding the threshold.
        destructive_fraction: ``n_destructive / n_perturbations``.
            ``0`` when ``n_perturbations == 0`` (defensive).
        worst_degradation: maximum ``degradation`` observed for this
            param across all its perturbations.
    """

    param_name: str
    n_perturbations: int
    n_destructive: int
    destructive_fraction: Decimal
    worst_degradation: Decimal


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    """Aggregate robustness diagnostic across all params.

    Attributes:
        baseline_score: reference metric (typically Sharpe of the
            champion).
        n_params: number of parameters perturbed.
        total_perturbations: sum of per-param perturbation counts.
        total_destructive: sum of per-param destructive counts.
        destructive_fraction: cohort-level fraction (used by
            :func:`is_robust`).
        per_param: list of :class:`ParamStability`, one per
            parameter. UI can render this as the doc-10 heatmap.
        perturbations: full sweep results (for audit / debugging).
    """

    baseline_score: Decimal
    n_params: int
    total_perturbations: int
    total_destructive: int
    destructive_fraction: Decimal
    per_param: list[ParamStability]
    perturbations: list[PerturbationResult]


# ─── Pure helpers ───────────────────────────────────────────────────────────


def _generate_offsets(
    *,
    perturbation_pct: Decimal,
    n_per_side: int,
) -> list[Decimal]:
    """Generate the symmetric sweep of offsets, excluding zero.

    With ``n_per_side = 2`` and ``perturbation_pct = 0.20`` :
    ``[-0.20, -0.10, +0.10, +0.20]``.
    """
    offsets: list[Decimal] = []
    n_d = Decimal(n_per_side)
    for step in range(1, n_per_side + 1):
        scale = Decimal(step) / n_d
        magnitude = scale * perturbation_pct
        offsets.append(-magnitude)
        offsets.append(magnitude)
    # Sort by signed value so the heatmap row reads -pct ... +pct.
    return sorted(offsets)


def _safe_objective(
    objective_fn: Callable[[dict[str, Decimal]], Decimal],
    perturbed_params: dict[str, Decimal],
) -> Decimal:
    """Call the objective, return ``0`` on any exception.

    A perturbation that crashes the objective is treated as a
    catastrophic degradation (score = 0 -> degradation = 1 ->
    destructive). This is the pessimistic interpretation : we do
    not give the champion the benefit of the doubt when its own
    code path fails on a small perturbation.
    """
    try:
        return objective_fn(perturbed_params)
    except Exception:  # noqa: BLE001
        # Any failure is opaque from this module's POV ; we deliberately
        # swallow the exception to keep the sweep going. The downstream
        # report will show 100 % destructive on the failing perturbation
        # so the failure is visible in the audit anyway.
        return _ZERO


# ─── Public API ─────────────────────────────────────────────────────────────


def compute_robustness_report(
    *,
    baseline_score: Decimal,
    baseline_params: dict[str, Decimal],
    objective_fn: Callable[[dict[str, Decimal]], Decimal],
    perturbation_pct: Decimal = DEFAULT_PERTURBATION_PCT,
    n_per_side: int = DEFAULT_N_PER_SIDE,
    destruction_threshold: Decimal = DEFAULT_DESTRUCTION_THRESHOLD,
) -> RobustnessReport:
    """Sweep each parameter ±``perturbation_pct`` and aggregate stability.

    Args:
        baseline_score: reference score on the unperturbed
            ``baseline_params``. Must be ``> 0`` (champion has
            positive metric ; a non-positive baseline makes the
            relative degradation undefined).
        baseline_params: ``{name -> value}`` for every parameter
            to perturb. Must be non-empty.
        objective_fn: takes a perturbed copy of ``baseline_params``
            and returns its score. Must be deterministic for a
            given input. Exceptions are caught and counted as
            destructive.
        perturbation_pct: maximum perturbation magnitude in ``(0, 1)``.
            ``0.20`` per doc 10 R4.
        n_per_side: number of points on each side of the baseline
            (``>= 1``). Default 2 -> 4 perturbations per param.
        destruction_threshold: degradation fraction above which a
            perturbation is "destructive". Default 0.30 per doc 10.

    Returns:
        A :class:`RobustnessReport`. Per-parameter heatmap data in
        ``per_param`` ; full audit trail in ``perturbations``.

    Raises:
        ValueError: on non-positive ``baseline_score``, empty
            ``baseline_params``, or invalid sweep parameters.
    """
    if baseline_score <= _ZERO:
        msg = (
            "baseline_score must be > 0 (relative degradation undefined "
            f"for non-positive baseline), got {baseline_score}"
        )
        raise ValueError(msg)
    if not baseline_params:
        msg = "baseline_params must not be empty"
        raise ValueError(msg)
    if not (_ZERO < perturbation_pct < _ONE):
        msg = f"perturbation_pct must be in (0, 1), got {perturbation_pct}"
        raise ValueError(msg)
    if n_per_side < 1:
        msg = f"n_per_side must be >= 1, got {n_per_side}"
        raise ValueError(msg)
    if not (_ZERO < destruction_threshold < _ONE):
        msg = f"destruction_threshold must be in (0, 1), got {destruction_threshold}"
        raise ValueError(msg)

    offsets = _generate_offsets(
        perturbation_pct=perturbation_pct,
        n_per_side=n_per_side,
    )

    perturbations: list[PerturbationResult] = []
    per_param: list[ParamStability] = []

    for param_name, baseline_value in baseline_params.items():
        param_perturbations: list[PerturbationResult] = []
        for offset in offsets:
            perturbed_value = baseline_value * (_ONE + offset)
            # Build the perturbed config : copy + override.
            perturbed_params = dict(baseline_params)
            perturbed_params[param_name] = perturbed_value

            perturbed_score = _safe_objective(objective_fn, perturbed_params)
            degradation = (baseline_score - perturbed_score) / baseline_score
            is_destructive = degradation > destruction_threshold

            result = PerturbationResult(
                param_name=param_name,
                baseline_value=baseline_value,
                perturbed_value=perturbed_value,
                offset_pct=offset,
                baseline_score=baseline_score,
                perturbed_score=perturbed_score,
                degradation=degradation,
                is_destructive=is_destructive,
            )
            param_perturbations.append(result)
            perturbations.append(result)

        n_pert = len(param_perturbations)
        n_dest = sum(1 for r in param_perturbations if r.is_destructive)
        worst = max(r.degradation for r in param_perturbations)
        per_param.append(
            ParamStability(
                param_name=param_name,
                n_perturbations=n_pert,
                n_destructive=n_dest,
                destructive_fraction=Decimal(n_dest) / Decimal(n_pert),
                worst_degradation=worst,
            ),
        )

    total_pert = len(perturbations)
    total_dest = sum(1 for r in perturbations if r.is_destructive)
    cohort_fraction = Decimal(total_dest) / Decimal(total_pert) if total_pert > 0 else _ZERO

    return RobustnessReport(
        baseline_score=baseline_score,
        n_params=len(baseline_params),
        total_perturbations=total_pert,
        total_destructive=total_dest,
        destructive_fraction=cohort_fraction,
        per_param=per_param,
        perturbations=perturbations,
    )


# ─── Decision gate (doc 10 R4 criterion I4) ─────────────────────────────────


def is_robust(
    report: RobustnessReport,
    *,
    max_destructive_fraction: Decimal = DEFAULT_MAX_DESTRUCTIVE_FRACTION,
) -> bool:
    """Return True iff ``report.destructive_fraction <= max_destructive_fraction``.

    Doc 10 R4 criterion I4 : 25 % is the publishable threshold.
    Inclusive at the boundary.

    Args:
        report: from :func:`compute_robustness_report`.
        max_destructive_fraction: floor in ``[0, 1]`` ; default
            ``0.25`` per doc 10.

    Returns:
        Boolean verdict.

    Raises:
        ValueError: on ``max_destructive_fraction`` outside ``[0, 1]``.
    """
    if not (_ZERO <= max_destructive_fraction <= _ONE):
        msg = f"max_destructive_fraction must be in [0, 1], got {max_destructive_fraction}"
        raise ValueError(msg)
    return report.destructive_fraction <= max_destructive_fraction
