"""Strategy abstractions — :class:`Strategy` Protocol + :class:`StrategySignal`.

Every strategy in :mod:`emeraude.agent.reasoning.strategies` honors this
protocol. The output dataclass :class:`StrategySignal` is what feeds the
ensemble vote (next iteration) and the audit trail (R9).

Conventions :

* ``score`` is in ``[-1, 1]``. Positive = long bias, negative = short
  bias, magnitude = strength of conviction.
* ``confidence`` is in ``[0, 1]``. Reflects how strongly the strategy's
  indicators agree among themselves.
* ``reasoning`` is a short human-readable explanation, used by the
  audit logger and the UX (the user sees *why* the strategy says BUY).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from emeraude.agent.perception.regime import Regime
    from emeraude.infra.market_data import Kline

_SCORE_MIN: Final[Decimal] = Decimal("-1")
_SCORE_MAX: Final[Decimal] = Decimal("1")
_CONF_MIN: Final[Decimal] = Decimal("0")
_CONF_MAX: Final[Decimal] = Decimal("1")


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """Output of a single strategy evaluating the current market.

    The bounds are validated at construction time so that downstream
    consumers (ensemble, audit) can rely on the invariants without
    re-checking.
    """

    score: Decimal
    confidence: Decimal
    reasoning: str

    def __post_init__(self) -> None:
        """Enforce score and confidence bounds at construction time."""
        if not _SCORE_MIN <= self.score <= _SCORE_MAX:
            msg = f"score must be in [-1, 1], got {self.score}"
            raise ValueError(msg)
        if not _CONF_MIN <= self.confidence <= _CONF_MAX:
            msg = f"confidence must be in [0, 1], got {self.confidence}"
            raise ValueError(msg)


class Strategy(Protocol):
    """Common shape for any directional strategy."""

    name: str

    def compute_signal(self, klines: list[Kline], regime: Regime) -> StrategySignal | None:
        """Return a directional signal, or ``None`` if no opinion.

        ``None`` signals one of two situations :

        * **Insufficient data** — not enough klines for the indicators
          this strategy depends on (warmup unsatisfied).
        * **No clear opinion** — the strategy's conditions (e.g. RSI
          extremes for mean reversion) are not met. The strategy
          declines to vote rather than emitting a noise score around 0.
        """
        ...  # pragma: no cover  (Protocol body — never executed)
