"""Calibration tracking — Brier score + Expected Calibration Error (doc 10 R1).

Doc 10 §"R1 — Calibration tracking (Brier score + ECE)" addresses
lacuna L1 (confiance non calibrée). The agent's strategies emit a
``confidence: Decimal`` in ``[0, 1]`` for each signal — but nothing
checks whether those confidences match reality. A strategy that
predicts "90 % confidence" on 100 trades and lands 50 % wins is
silently miscalibrated, and any downstream Kelly sizing built on
those confidences is overconfident.

This module ships the **diagnostic primitives** :

* **Brier score** : ``mean((p - y)^2)`` where ``y = 1`` for a win
  and ``y = 0`` for a loss. Range ``[0, 1]`` ; 0 = perfect, 0.25 =
  random with uniform 0.5 confidence.
* **Expected Calibration Error (ECE)** : binning of confidences,
  weighted absolute gap between in-bin mean confidence and in-bin
  win rate. Range ``[0, 1]`` ; 0 = perfect, large = systematic bias.
* **Reliability bins** : per-bin payload for the future
  "IA / Apprentissage" UI screen (doc 03) — bin bounds, sample
  count, average confidence, observed accuracy.

This iteration delivers the **diagnostic only**. Doc 10 also
describes Platt scaling / isotonic regression for *correcting*
miscalibrated confidences ; that is delivered in a future iter
when a concrete pipeline is ready to consume rescaled values
(anti-rule A1).

Pure module : no I/O, no DB, no NumPy. Decimal arithmetic
throughout. Caller fetches `(confidence, outcome)` pairs from
:meth:`PositionTracker.history` (or any other source that produced
the predictions).

References :

* Brier (1950). *Verification of Forecasts Expressed in Terms of
  Probability*. Monthly Weather Review 78(1) : 1-3. The original
  Brier score.
* Niculescu-Mizil & Caruana (2005). *Predicting Good Probabilities
  with Supervised Learning*. ICML '05. ECE binning + Platt scaling.
* Naeini, Cooper & Hauskrecht (2015). *Obtaining Well Calibrated
  Probabilities Using Bayesian Binning*. AAAI '15. Modern ECE form.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

# Doc 10 R1 critère mesurable I1 : "ECE < 5 % sur 100 trades".
DEFAULT_ECE_THRESHOLD: Final[Decimal] = Decimal("0.05")
# Standard binning resolution. 10 bins gives a clean reliability
# diagram (0-10 %, 10-20 %, ..., 90-100 %) and matches the canonical
# ECE definition in the literature.
DEFAULT_N_BINS: Final[int] = 10


# ─── Result types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CalibrationBinStat:
    """One bin of the reliability diagram.

    Attributes:
        bin_low: inclusive lower bound of the bin.
        bin_high: upper bound. Exclusive for all bins except the
            last one (which is inclusive at ``1.0``).
        n_samples: count of predictions falling in this bin.
        avg_confidence: mean of the predictions in this bin.
            ``0`` when ``n_samples == 0``.
        accuracy: fraction of wins among samples in this bin
            (``Decimal('0.7')`` = 70 % winning trades).
            ``0`` when ``n_samples == 0``.
    """

    bin_low: Decimal
    bin_high: Decimal
    n_samples: int
    avg_confidence: Decimal
    accuracy: Decimal


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Aggregate calibration diagnostics over a sample.

    Edge cases :

    * Empty input -> all numeric fields ``0``, ``bins`` empty.
    * Single sample -> Brier well-defined, ECE = ``|p - outcome|``
      in the appropriate bin.
    * All samples in one bin -> the other bins have ``n_samples = 0``
      and contribute zero to the ECE.

    Attributes:
        n_samples: total predictions aggregated.
        brier_score: ``mean((p - y)^2)`` in ``[0, 1]``. Lower =
            better calibrated. 0.25 = uniform 0.5 confidence with
            random outcomes.
        ece: Expected Calibration Error in ``[0, 1]``. Lower = better.
        bins: per-bin reliability stats. Always exactly ``n_bins``
            entries (some may have ``n_samples = 0``).
    """

    n_samples: int
    brier_score: Decimal
    ece: Decimal
    bins: list[CalibrationBinStat]


# ─── Pure helpers ───────────────────────────────────────────────────────────


def _validate_pair(predictions: list[Decimal], outcomes: list[bool]) -> None:
    """Common input validation."""
    if len(predictions) != len(outcomes):
        msg = (
            "predictions and outcomes must have the same length, got "
            f"{len(predictions)} and {len(outcomes)}"
        )
        raise ValueError(msg)
    for i, p in enumerate(predictions):
        if not (_ZERO <= p <= _ONE):
            msg = f"predictions[{i}] must be in [0, 1], got {p}"
            raise ValueError(msg)


def _bin_index(value: Decimal, n_bins: int) -> int:
    """Return the bin index in ``[0, n_bins)`` for ``value`` in ``[0, 1]``.

    The last bin is inclusive at ``1.0`` so a perfectly confident
    prediction of ``Decimal('1')`` lands in bin ``n_bins - 1``
    rather than overflowing.
    """
    # value in [0, 1] is guaranteed by _validate_pair.
    if value == _ONE:
        return n_bins - 1
    # int() truncates ; for value in [0, 1) and n_bins=10 :
    #   value 0.0 -> 0
    #   value 0.05 -> 0
    #   value 0.10 -> 1
    #   value 0.99 -> 9
    return int(value * Decimal(n_bins))


# ─── Public API ─────────────────────────────────────────────────────────────


def compute_brier_score(
    predictions: list[Decimal],
    outcomes: list[bool],
) -> Decimal:
    """Brier score : ``mean((p - y)^2)``.

    Args:
        predictions: list of confidence values in ``[0, 1]``.
        outcomes: list of trade outcomes (True = win, False = loss).
            Must have the same length as ``predictions``.

    Returns:
        Brier score in ``[0, 1]``. ``0`` for empty input.

    Raises:
        ValueError: on mismatched lengths or out-of-range
            predictions.
    """
    _validate_pair(predictions, outcomes)
    if not predictions:
        return _ZERO
    sq_sum = _ZERO
    for p, y in zip(predictions, outcomes, strict=True):
        actual = _ONE if y else _ZERO
        diff = p - actual
        sq_sum += diff * diff
    return sq_sum / Decimal(len(predictions))


def compute_ece(
    predictions: list[Decimal],
    outcomes: list[bool],
    *,
    n_bins: int = DEFAULT_N_BINS,
) -> Decimal:
    """Expected Calibration Error.

    Bins predictions into ``n_bins`` equal-width buckets over
    ``[0, 1]``. For each non-empty bin computes the absolute gap
    between mean confidence and observed win rate, weights by bin
    population.

    Args:
        predictions: confidences in ``[0, 1]``.
        outcomes: matching True/False outcomes.
        n_bins: bin resolution. Must be ``>= 1``.

    Returns:
        ECE in ``[0, 1]``. ``0`` for empty input or perfect
        calibration.

    Raises:
        ValueError: on mismatched lengths, out-of-range predictions,
            or ``n_bins < 1``.
    """
    if n_bins < 1:
        msg = f"n_bins must be >= 1, got {n_bins}"
        raise ValueError(msg)
    _validate_pair(predictions, outcomes)
    if not predictions:
        return _ZERO

    # Per-bin accumulators.
    bin_count = [0] * n_bins
    bin_conf_sum = [_ZERO] * n_bins
    bin_wins = [0] * n_bins

    for p, y in zip(predictions, outcomes, strict=True):
        idx = _bin_index(p, n_bins)
        bin_count[idx] += 1
        bin_conf_sum[idx] += p
        if y:
            bin_wins[idx] += 1

    n_total = Decimal(len(predictions))
    ece = _ZERO
    for i in range(n_bins):
        if bin_count[i] == 0:
            continue
        n_b = Decimal(bin_count[i])
        avg_conf = bin_conf_sum[i] / n_b
        accuracy = Decimal(bin_wins[i]) / n_b
        ece += (n_b / n_total) * abs(avg_conf - accuracy)
    return ece


def compute_calibration_report(
    predictions: list[Decimal],
    outcomes: list[bool],
    *,
    n_bins: int = DEFAULT_N_BINS,
) -> CalibrationReport:
    """Combined diagnostic : Brier + ECE + per-bin reliability stats.

    Args:
        predictions: confidences in ``[0, 1]``.
        outcomes: matching outcomes.
        n_bins: reliability-diagram resolution.

    Returns:
        A :class:`CalibrationReport` with ``n_bins`` bin entries
        (some may have ``n_samples = 0``).

    Raises:
        ValueError: on mismatched lengths, out-of-range predictions,
            or ``n_bins < 1``.
    """
    if n_bins < 1:
        msg = f"n_bins must be >= 1, got {n_bins}"
        raise ValueError(msg)
    _validate_pair(predictions, outcomes)

    if not predictions:
        empty_bins = [
            CalibrationBinStat(
                bin_low=Decimal(i) / Decimal(n_bins),
                bin_high=Decimal(i + 1) / Decimal(n_bins),
                n_samples=0,
                avg_confidence=_ZERO,
                accuracy=_ZERO,
            )
            for i in range(n_bins)
        ]
        return CalibrationReport(
            n_samples=0,
            brier_score=_ZERO,
            ece=_ZERO,
            bins=empty_bins,
        )

    # Per-bin accumulators.
    bin_count = [0] * n_bins
    bin_conf_sum = [_ZERO] * n_bins
    bin_wins = [0] * n_bins
    sq_sum = _ZERO

    for p, y in zip(predictions, outcomes, strict=True):
        idx = _bin_index(p, n_bins)
        bin_count[idx] += 1
        bin_conf_sum[idx] += p
        if y:
            bin_wins[idx] += 1
        actual = _ONE if y else _ZERO
        diff = p - actual
        sq_sum += diff * diff

    n_total = Decimal(len(predictions))
    brier = sq_sum / n_total

    bins: list[CalibrationBinStat] = []
    ece = _ZERO
    for i in range(n_bins):
        bin_low = Decimal(i) / Decimal(n_bins)
        bin_high = Decimal(i + 1) / Decimal(n_bins)
        if bin_count[i] == 0:
            bins.append(
                CalibrationBinStat(
                    bin_low=bin_low,
                    bin_high=bin_high,
                    n_samples=0,
                    avg_confidence=_ZERO,
                    accuracy=_ZERO,
                ),
            )
            continue
        n_b = Decimal(bin_count[i])
        avg_conf = bin_conf_sum[i] / n_b
        accuracy = Decimal(bin_wins[i]) / n_b
        ece += (n_b / n_total) * abs(avg_conf - accuracy)
        bins.append(
            CalibrationBinStat(
                bin_low=bin_low,
                bin_high=bin_high,
                n_samples=bin_count[i],
                avg_confidence=avg_conf,
                accuracy=accuracy,
            ),
        )

    return CalibrationReport(
        n_samples=len(predictions),
        brier_score=brier,
        ece=ece,
        bins=bins,
    )


# ─── Decision gate (doc 10 R1 criterion I1) ─────────────────────────────────


def is_well_calibrated(
    report: CalibrationReport,
    *,
    threshold: Decimal = DEFAULT_ECE_THRESHOLD,
) -> bool:
    """Return True iff ``report.ece <= threshold``.

    Doc 10 R1 sets ``threshold = 0.05`` ("ECE < 5 % sur 100
    trades"). Inclusive at the boundary so the literal "5 %"
    counts as well-calibrated.

    Args:
        report: aggregate from :func:`compute_calibration_report`.
        threshold: maximum acceptable ECE in ``[0, 1]``.

    Returns:
        Boolean verdict. Empty report (``n_samples == 0``) returns
        ``False`` — without data there is nothing to be confident
        about.

    Raises:
        ValueError: on ``threshold`` outside ``[0, 1]``.
    """
    if not (_ZERO <= threshold <= _ONE):
        msg = f"threshold must be in [0, 1], got {threshold}"
        raise ValueError(msg)
    if report.n_samples == 0:
        return False
    return report.ece <= threshold
