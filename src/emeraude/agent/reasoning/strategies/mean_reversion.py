"""Mean Reversion — "extremes return to the mean" (doc 04 §2).

Contrarian strategy : fires only when at least one indicator hits an
extreme. Returns ``None`` (no opinion) when the market is in a normal
range — refusing to vote is a feature, not a bug.

Indicators (3 ternary votes : long, short, or silent) :

1. **RSI** : ``< 25`` = oversold (long bias), ``> 75`` = overbought
   (short bias), else = silent.
2. **Bollinger band position** : close below lower band = oversold
   (long), close above upper band = overbought (short), else silent.
3. **Stochastic %K** : ``< 15`` = oversold, ``> 85`` = overbought,
   else silent.

Aggregation : sum the ternary votes (each in {-1, 0, +1}). If the sum
is zero (all silent or perfectly split) the strategy returns ``None``.
Otherwise score = sum / 3 (range [-1, 1]) and confidence = |sum| / 3.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Final

from emeraude.agent.perception.indicators import bollinger_bands, rsi, stochastic
from emeraude.agent.reasoning.strategies.base import StrategySignal

if TYPE_CHECKING:
    from emeraude.agent.perception.regime import Regime
    from emeraude.infra.market_data import Kline


_RSI_OVERSOLD: Final[Decimal] = Decimal("25")
_RSI_OVERBOUGHT: Final[Decimal] = Decimal("75")
_STOCH_OVERSOLD: Final[Decimal] = Decimal("15")
_STOCH_OVERBOUGHT: Final[Decimal] = Decimal("85")
_THREE: Final[Decimal] = Decimal("3")
# Bollinger needs 20, RSI(14) needs 15, stochastic(14) needs ~16.
# 30 covers all with margin.
_MIN_KLINES: Final[int] = 30


class MeanReversion:
    """Contrarian strategy triggering on RSI / BB / Stoch extremes."""

    name: ClassVar[str] = "mean_reversion"

    def compute_signal(
        self,
        klines: list[Kline],
        regime: Regime,  # noqa: ARG002  (kept for protocol uniformity)
    ) -> StrategySignal | None:
        """See :meth:`Strategy.compute_signal`."""
        if len(klines) < _MIN_KLINES:
            return None

        closes = [k.close for k in klines]
        rsi_v = rsi(closes, 14)
        bb = bollinger_bands(closes, 20, 2.0)
        stoch_v = stochastic(klines, 14)

        if rsi_v is None or bb is None or stoch_v is None:
            return None  # pragma: no cover  (guarded by _MIN_KLINES)

        votes: list[int] = []
        reasons: list[str] = []

        # Vote 1 : RSI extremes.
        if rsi_v < _RSI_OVERSOLD:
            votes.append(1)
            reasons.append(f"RSI={rsi_v:.1f}<25 oversold")
        elif rsi_v > _RSI_OVERBOUGHT:
            votes.append(-1)
            reasons.append(f"RSI={rsi_v:.1f}>75 overbought")

        # Vote 2 : Bollinger band position.
        last_close = closes[-1]
        if last_close < bb.lower:
            votes.append(1)
            reasons.append("close<BB.lower")
        elif last_close > bb.upper:
            votes.append(-1)
            reasons.append("close>BB.upper")

        # Vote 3 : Stochastic extremes.
        if stoch_v.k < _STOCH_OVERSOLD:
            votes.append(1)
            reasons.append(f"Stoch={stoch_v.k:.1f}<15")
        elif stoch_v.k > _STOCH_OVERBOUGHT:
            votes.append(-1)
            reasons.append(f"Stoch={stoch_v.k:.1f}>85")

        if not votes:
            return None  # no extremes triggered -> no opinion

        vote_sum = sum(votes)
        if vote_sum == 0:
            # Perfectly split (e.g. RSI oversold + Stoch overbought) :
            # contradictory signals, no clear opinion.
            return None

        score = Decimal(vote_sum) / _THREE
        confidence = Decimal(abs(vote_sum)) / _THREE

        return StrategySignal(
            score=score,
            confidence=confidence,
            reasoning=" + ".join(reasons),
        )
