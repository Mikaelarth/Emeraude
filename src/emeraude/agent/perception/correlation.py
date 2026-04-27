"""Correlation stress detection across tracked coins (doc 10 R7).

Doc 10 §"R7 — Régime de stress de corrélation" addresses lacuna L7
("corrélations supposées stables"). In a calm bull market BTC/ETH/SOL
typically correlate around 0.5 ; in a crash they jump to 0.95+ and
the diversification the bot was relying on collapses. This module
detects that regime by computing the **average pairwise correlation**
over recent 1h returns and flagging a "stress regime" when the mean
exceeds a configurable threshold (default 0.8 per doc 10).

Once a stress regime is flagged, downstream callers can :

* reduce ``max_positions`` aggressively (3 -> 1 per doc 10),
* block new entries while the regime persists,
* notify the user.

This iteration ships the **pure analytics primitives**. Orchestrator
wiring is deferred (anti-rule A1) : the current
:class:`AutoTrader` operates on a single symbol's klines, so multi-
coin correlation stress requires a multi-symbol fetching loop that
does not exist yet. The :func:`compute_correlation_report` API is
ready to be plugged into that future loop with a single call.

Method :

1. For each tracked symbol, compute simple returns
   ``(close_i - close_{i-1}) / close_{i-1}``.
2. For every distinct pair of symbols, compute the **Pearson
   correlation coefficient** using the numerically stable
   "deviation form" :
   ``rho = sum((x-mx)(y-my)) / sqrt(sum((x-mx)^2) * sum((y-my)^2))``.
3. Average the off-diagonal coefficients to get the cohort-level
   stress score. Compare to ``threshold``.

Edge cases :

* Constant series (zero variance) -> ``rho = 0`` (degenerate, no
  relationship inferable).
* Series shorter than 2 -> ``rho = 0`` (need at least two points
  for a slope).
* Single tracked symbol -> empty matrix, ``mean = 0``, no stress.

Pure module : no I/O, no DB, no NumPy. Decimal everywhere.

Reference :

* Forbes & Rigobon (2002). *No Contagion, Only Interdependence :
  Measuring Stock Market Co-Movements*. Journal of Finance 57(5) :
  2223-2261. Establishes that conditional correlations are
  systematically biased upward in high-volatility regimes — exactly
  the mechanism this module detects.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext
from itertools import combinations
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from emeraude.infra.market_data import Kline

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")
_NEG_ONE: Final[Decimal] = Decimal("-1")

# Doc 10 R7 stress threshold : average pairwise correlation > 0.8.
DEFAULT_STRESS_THRESHOLD: Final[Decimal] = Decimal("0.8")


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CorrelationReport:
    """Pairwise correlation snapshot at one point in time.

    Attributes:
        n_symbols: number of tracked symbols.
        n_pairs: number of distinct unordered pairs computed
            (``n_symbols * (n_symbols - 1) / 2``).
        mean_correlation: arithmetic mean of the off-diagonal
            coefficients. ``0`` for a single symbol or empty input.
        matrix: ``{(symbol_a, symbol_b) -> rho}`` for every pair
            with ``symbol_a < symbol_b`` lexicographically.
        threshold: stress threshold used to compute ``is_stress``.
        is_stress: ``mean_correlation >= threshold``. Inclusive.
    """

    n_symbols: int
    n_pairs: int
    mean_correlation: Decimal
    matrix: dict[tuple[str, str], Decimal]
    threshold: Decimal
    is_stress: bool


# ─── Pure helpers ───────────────────────────────────────────────────────────


def compute_returns(klines: list[Kline]) -> list[Decimal]:
    """Simple percentage returns from a kline series.

    For ``n`` klines yields ``n - 1`` returns. ``r_i = (close_i -
    close_{i-1}) / close_{i-1}``.

    Args:
        klines: chronological kline history.

    Returns:
        List of returns. Empty when ``len(klines) < 2``.

    Raises:
        ValueError: if any close price is non-positive (corrupt data
            or division by zero).
    """
    if len(klines) < 2:  # noqa: PLR2004
        return []
    returns: list[Decimal] = []
    for i in range(1, len(klines)):
        prev_close = klines[i - 1].close
        curr_close = klines[i].close
        if prev_close <= _ZERO:
            msg = f"klines[{i - 1}].close must be > 0, got {prev_close}"
            raise ValueError(msg)
        returns.append((curr_close - prev_close) / prev_close)
    return returns


def pearson_correlation(
    x: list[Decimal],
    y: list[Decimal],
) -> Decimal:
    """Pearson correlation coefficient between two equal-length series.

    Uses the numerically-stable deviation form :

        rho = sum((x_i - mx)(y_i - my))
              / sqrt( sum((x_i - mx)^2) * sum((y_i - my)^2) )

    Returns a value in ``[-1, 1]`` (modulo Decimal precision drift on
    extreme cases, which is bounded by the stdlib ``Context.sqrt``
    precision — default 28 digits).

    Args:
        x: first series.
        y: second series. Must have the same length as ``x``.

    Returns:
        Correlation coefficient. ``0`` when either series is empty,
        has length < 2, or has zero variance (constant values —
        degenerate, no relationship inferable).

    Raises:
        ValueError: on mismatched lengths.
    """
    if len(x) != len(y):
        msg = f"x and y must have the same length, got {len(x)} and {len(y)}"
        raise ValueError(msg)
    n = len(x)
    if n < 2:  # noqa: PLR2004
        return _ZERO

    n_d = Decimal(n)
    mean_x = sum(x, _ZERO) / n_d
    mean_y = sum(y, _ZERO) / n_d

    cov_sum = _ZERO
    var_x_sum = _ZERO
    var_y_sum = _ZERO
    for xi, yi in zip(x, y, strict=True):
        dx = xi - mean_x
        dy = yi - mean_y
        cov_sum += dx * dy
        var_x_sum += dx * dx
        var_y_sum += dy * dy

    if _ZERO in (var_x_sum, var_y_sum):
        # At least one series is constant : no relationship inferable.
        return _ZERO
    denom = getcontext().sqrt(var_x_sum * var_y_sum)
    rho = cov_sum / denom
    # Clamp to [-1, 1] for the rare case of precision drift past the
    # mathematical bound (Cauchy-Schwarz guarantees |rho| <= 1, but
    # finite Decimal precision can produce rho = 1.0000...0001 on
    # near-perfect inputs). Defensive — never reached on the test
    # corpus's well-conditioned inputs.
    if rho > _ONE:  # pragma: no cover
        return _ONE
    if rho < _NEG_ONE:  # pragma: no cover
        return _NEG_ONE
    return rho


def compute_correlation_matrix(
    returns_by_symbol: dict[str, list[Decimal]],
) -> dict[tuple[str, str], Decimal]:
    """Pairwise Pearson correlations over a returns dictionary.

    Args:
        returns_by_symbol: ``{symbol -> returns}`` ; all return
            series must share the same length (caller is responsible
            for alignment by timestamp).

    Returns:
        ``{(symbol_a, symbol_b) -> rho}`` for every pair with
        ``symbol_a < symbol_b`` lexicographically. Empty when fewer
        than two symbols are tracked.

    Raises:
        ValueError: if return series have different lengths.
    """
    if len(returns_by_symbol) < 2:  # noqa: PLR2004
        return {}

    # Validate all series have the same length (alignment guard).
    lengths = {len(r) for r in returns_by_symbol.values()}
    if len(lengths) > 1:
        msg = f"return series must have the same length, got {sorted(lengths)}"
        raise ValueError(msg)

    matrix: dict[tuple[str, str], Decimal] = {}
    for sym_a, sym_b in combinations(sorted(returns_by_symbol.keys()), 2):
        matrix[sym_a, sym_b] = pearson_correlation(
            returns_by_symbol[sym_a],
            returns_by_symbol[sym_b],
        )
    return matrix


def mean_pairwise_correlation(
    matrix: dict[tuple[str, str], Decimal],
) -> Decimal:
    """Average of the off-diagonal correlation coefficients.

    Args:
        matrix: from :func:`compute_correlation_matrix`.

    Returns:
        Arithmetic mean. ``0`` for an empty matrix.
    """
    if not matrix:
        return _ZERO
    return sum(matrix.values(), _ZERO) / Decimal(len(matrix))


# ─── Public API : combined report + gate ────────────────────────────────────


def compute_correlation_report(
    klines_by_symbol: dict[str, list[Kline]],
    *,
    threshold: Decimal = DEFAULT_STRESS_THRESHOLD,
) -> CorrelationReport:
    """Aggregate correlation diagnostic over multiple kline series.

    Args:
        klines_by_symbol: ``{symbol -> klines}`` ; each kline series
            should be chronologically aligned (same time grid). The
            caller is responsible for the alignment ; this function
            checks that the resulting return series have the same
            length and rejects otherwise.
        threshold: stress threshold in ``[-1, 1]``. Default ``0.8``
            per doc 10 R7. ``mean_correlation >= threshold`` flags
            a stress regime.

    Returns:
        A :class:`CorrelationReport`.

    Raises:
        ValueError: on ``threshold`` outside ``[-1, 1]`` or on
            misaligned return series (different lengths after
            ``compute_returns``).
    """
    if not (_NEG_ONE <= threshold <= _ONE):
        msg = f"threshold must be in [-1, 1], got {threshold}"
        raise ValueError(msg)

    returns_by_symbol = {
        symbol: compute_returns(klines) for symbol, klines in klines_by_symbol.items()
    }
    matrix = compute_correlation_matrix(returns_by_symbol)
    mean = mean_pairwise_correlation(matrix)
    n_sym = len(klines_by_symbol)
    return CorrelationReport(
        n_symbols=n_sym,
        n_pairs=len(matrix),
        mean_correlation=mean,
        matrix=matrix,
        threshold=threshold,
        is_stress=(n_sym >= 2 and mean >= threshold),  # noqa: PLR2004
    )


def is_stress_regime(report: CorrelationReport) -> bool:
    """Convenience predicate : True iff the report flagged a stress.

    Equivalent to ``report.is_stress`` but expresses the intent at
    the call site. Doc 10 R7 stress threshold is the default
    (``0.8``) ; the report carries the threshold used for audit.
    """
    return report.is_stress
