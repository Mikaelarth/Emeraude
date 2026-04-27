"""Concept-drift detectors on R-multiple streams (doc 10 R3).

Doc 10 §"R3 — Détection de drift de concept" addresses lacuna L3
(drift ignored). Markets change silently ; without detection the bot
keeps trading with obsolete parameters until the crash. Two detectors
are run **in parallel** on the realized R-multiples and either one
firing is sufficient to declare drift :

* **Page-Hinkley test** (Page 1954) : CUSUM filter on the deviations
  from the running mean. Detects a sustained drop — small per-step
  deviations under the tolerance ``delta`` accumulate ; the cumulative
  sum is reset to zero on positive deviations. When the cumsum
  exceeds ``threshold``, drift is declared. Bounded statistical
  detection delay.
* **ADWIN — Adaptive Windowing** (Bifet & Gavaldà 2007) : maintains a
  sliding window of recent samples, exhaustively tests every split
  point ``W = W0 | W1`` for an Hoeffding-bounded gap between the
  sub-windows' means. When a split shows ``|mean(W0) - mean(W1)| >
  epsilon_cut``, the older sub-window is dropped and drift is
  declared.

The two detectors complement each other :

* Page-Hinkley is **fast** (O(1) per update) and reacts to *gradual*
  drops accumulated over many samples.
* ADWIN is **flexible** (no pre-set drift magnitude) and reacts well
  to *abrupt* changes by adapting its window size.

Both expose the same shape : ``update(value) -> bool`` (True iff drift
fires this step), plus ``state()`` and ``reset()``. The caller wires
them into the agent's life cycle (a future iter will call
:meth:`update` on every closed trade and trigger
:meth:`ChampionLifecycle.transition(SUSPECT)` on detection).

This module is **pure** : no I/O, no DB, no NumPy. Decimal arithmetic
throughout ; ``getcontext().sqrt()`` for the ADWIN bound.

References :

* Page (1954). *Continuous Inspection Schemes*. Biometrika 41 :
  100-115. Original CUSUM / Page-Hinkley test.
* Bifet & Gavaldà (2007). *Learning from Time-Changing Data with
  Adaptive Windowing*. SDM '07. ADWIN algorithm + Hoeffding-based
  ``epsilon_cut`` formula.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_TWO: Final[Decimal] = Decimal("2")
_FOUR: Final[Decimal] = Decimal("4")

# Page-Hinkley defaults : tuned for R-multiple streams typically in
# [-2, +2]. ``delta = 0.005 R`` ignores low-amplitude noise while
# ``threshold = 5 R`` of cumulative deviation triggers the alarm.
_PH_DEFAULT_DELTA: Final[Decimal] = Decimal("0.005")
_PH_DEFAULT_THRESHOLD: Final[Decimal] = Decimal("5")

# ADWIN defaults : ``delta = 0.002`` corresponds to a 99.8 %
# confidence ; ``max_window = 200`` is enough to detect drifts over
# the last few weeks of trading without scanning the full history.
_ADWIN_DEFAULT_DELTA: Final[Decimal] = Decimal("0.002")
_ADWIN_DEFAULT_MAX_WINDOW: Final[int] = 200
# Smallest window before ADWIN starts looking for a split. Below this,
# any apparent gap is more likely to be small-sample noise than drift.
_ADWIN_MIN_WINDOW: Final[int] = 4


# ─── Page-Hinkley state ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PageHinkleyState:
    """Snapshot of a :class:`PageHinkleyDetector` at one point in time.

    Attributes:
        n_samples: total samples observed since last reset.
        running_mean: arithmetic mean of all samples.
        cumulative_sum: current value of the CUSUM filter (always
            ``>= 0`` by construction — the filter floors at zero on
            positive deviations).
        drift_detected: True iff an alarm has fired in the lifetime
            of this detector instance (sticky until reset).
    """

    n_samples: int
    running_mean: Decimal
    cumulative_sum: Decimal
    drift_detected: bool


# ─── Page-Hinkley detector ──────────────────────────────────────────────────


class PageHinkleyDetector:
    """Page-Hinkley test for detecting a sustained drop in mean.

    Detects when a stream of values trends below its running mean by
    at least ``delta`` per step, sustained long enough that the
    cumulative deviation exceeds ``threshold``.

    The classical CUSUM formulation is used (with floor at zero) :

        deviation_t = mean_t - x_t - delta
        cumsum_t = max(0, cumsum_{t-1} + deviation_t)
        ALARM if cumsum_t > threshold

    where ``mean_t`` is the running mean. ``delta`` is a per-step
    tolerance below the running mean before a drop is "counted" ;
    ``threshold`` is the cumulative drop magnitude that fires the
    alarm. Once an alarm fires, ``drift_detected`` is sticky until
    :meth:`reset` is called.

    Construct once per stream ; the detector keeps internal state.
    """

    def __init__(
        self,
        *,
        delta: Decimal = _PH_DEFAULT_DELTA,
        threshold: Decimal = _PH_DEFAULT_THRESHOLD,
    ) -> None:
        """Wire the detector with explicit thresholds.

        Args:
            delta: per-step tolerance ``> 0``. Below this magnitude
                of mean - sample, no drop is counted.
            threshold: cumulative deviation ``> 0`` that fires the
                alarm.

        Raises:
            ValueError: on non-positive ``delta`` or ``threshold``.
        """
        if delta <= _ZERO:
            msg = f"delta must be > 0, got {delta}"
            raise ValueError(msg)
        if threshold <= _ZERO:
            msg = f"threshold must be > 0, got {threshold}"
            raise ValueError(msg)
        self._delta = delta
        self._threshold = threshold
        self._n = 0
        self._mean = _ZERO
        self._cumsum = _ZERO
        self._drift = False

    def update(self, value: Decimal) -> bool:
        """Process one sample. Returns ``True`` iff drift fires this step.

        ``drift_detected`` is sticky once True ; subsequent calls
        return False unless :meth:`reset` is called in between.
        """
        self._n += 1
        # Running mean update : mean_n = mean_{n-1} + (x - mean_{n-1}) / n
        self._mean = self._mean + (value - self._mean) / Decimal(self._n)
        # Cumulative deviation : positive when value is below mean - delta.
        deviation = self._mean - value - self._delta
        new_cumsum = self._cumsum + deviation
        # Floor at zero (CUSUM filter). Positive values of the stream
        # cancel out earlier accumulated deviations.
        self._cumsum = max(_ZERO, new_cumsum)
        if self._cumsum > self._threshold and not self._drift:
            self._drift = True
            return True
        return False

    def reset(self) -> None:
        """Clear all state. The detector starts fresh from zero."""
        self._n = 0
        self._mean = _ZERO
        self._cumsum = _ZERO
        self._drift = False

    @property
    def detected(self) -> bool:
        """True iff drift has fired at any point since last reset."""
        return self._drift

    def state(self) -> PageHinkleyState:
        """Return a frozen snapshot of the current state."""
        return PageHinkleyState(
            n_samples=self._n,
            running_mean=self._mean,
            cumulative_sum=self._cumsum,
            drift_detected=self._drift,
        )


# ─── ADWIN state ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdwinState:
    """Snapshot of an :class:`AdwinDetector`.

    Attributes:
        window_size: count of samples currently in the window.
        running_mean: mean of the window (after any drift-induced
            truncation).
        drift_detected: True iff an alarm has fired since reset.
    """

    window_size: int
    running_mean: Decimal
    drift_detected: bool


# ─── ADWIN detector ─────────────────────────────────────────────────────────


class AdwinDetector:
    """ADWIN — Adaptive Windowing drift detector (Bifet & Gavaldà 2007).

    Maintains a sliding window of the last ``max_window`` samples and
    on each new sample exhaustively scans every split point
    ``W = W0 | W1`` for an Hoeffding-bounded gap between the two
    sub-windows' means :

        epsilon_cut = sqrt( ln(4 * |W| / delta) / (2 * m) )

    where ``m = 2 * |W0| * |W1| / |W|`` is the harmonic mean of the
    sub-window sizes. When ``|mean(W0) - mean(W1)| > epsilon_cut`` for
    any split, the older sub-window ``W0`` is dropped (the window
    re-initializes to ``W1``) and drift is declared.

    The exhaustive split scan is ``O(|W|^2)`` per update ; with
    ``max_window = 200`` this is well below 1 ms and avoids the
    complexity of the exponential-histogram implementation in the
    original paper. Anti-rule A1 — we deliver the simple version
    first ; the histogram version is a follow-up if profiling shows
    the cost matters in production (it will not, given a one-trade-
    per-cycle update rate).
    """

    def __init__(
        self,
        *,
        delta: Decimal = _ADWIN_DEFAULT_DELTA,
        max_window: int = _ADWIN_DEFAULT_MAX_WINDOW,
    ) -> None:
        """Wire the detector with explicit thresholds.

        Args:
            delta: confidence level for the Hoeffding bound. Smaller
                = stricter (fewer false positives, slower detection).
                Default ``0.002`` corresponds to ~99.8 % confidence.
            max_window: maximum window size in samples. Default
                ``200`` ; older samples are forgotten before any
                drift can use them.

        Raises:
            ValueError: on ``delta`` outside ``(0, 1)`` or
                non-positive ``max_window``.
        """
        if not (_ZERO < delta < _ONE):
            msg = f"delta must be in (0, 1), got {delta}"
            raise ValueError(msg)
        if max_window < _ADWIN_MIN_WINDOW:
            msg = f"max_window must be >= {_ADWIN_MIN_WINDOW}, got {max_window}"
            raise ValueError(msg)
        self._delta = delta
        self._max_window = max_window
        self._window: list[Decimal] = []
        self._drift = False

    def update(self, value: Decimal) -> bool:
        """Process one sample. Returns ``True`` iff drift fires this step.

        ``drift_detected`` is sticky once True ; subsequent calls
        return False unless :meth:`reset` is called in between.
        """
        self._window.append(value)
        if len(self._window) > self._max_window:
            # Drop the oldest sample to bound memory.
            self._window.pop(0)
        if len(self._window) < _ADWIN_MIN_WINDOW:
            return False

        # Search for a split where the gap exceeds epsilon_cut.
        n_total = len(self._window)
        for split in range(1, n_total):
            w0 = self._window[:split]
            w1 = self._window[split:]
            n0 = len(w0)
            n1 = len(w1)
            mean0 = sum(w0, _ZERO) / Decimal(n0)
            mean1 = sum(w1, _ZERO) / Decimal(n1)
            harmonic_n = _TWO * Decimal(n0) * Decimal(n1) / Decimal(n0 + n1)
            inner = (_FOUR * Decimal(n_total) / self._delta).ln() / (_TWO * harmonic_n)
            epsilon_cut = getcontext().sqrt(inner)
            if abs(mean0 - mean1) > epsilon_cut:
                # Drift : drop the older sub-window, keep only W1.
                self._window = list(w1)
                if not self._drift:
                    self._drift = True
                    return True
                return False
        return False

    def reset(self) -> None:
        """Clear the window and drift flag."""
        self._window = []
        self._drift = False

    @property
    def detected(self) -> bool:
        """True iff drift has fired at any point since last reset."""
        return self._drift

    def state(self) -> AdwinState:
        """Return a frozen snapshot of the current state."""
        if not self._window:
            return AdwinState(
                window_size=0,
                running_mean=_ZERO,
                drift_detected=self._drift,
            )
        running_mean = sum(self._window, _ZERO) / Decimal(len(self._window))
        return AdwinState(
            window_size=len(self._window),
            running_mean=running_mean,
            drift_detected=self._drift,
        )
