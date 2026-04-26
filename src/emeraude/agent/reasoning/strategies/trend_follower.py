"""Trend Follower — "the trend is your friend" (doc 04 §1).

Indicators (4 binary votes, equal weight) :

1. **EMA 12 vs EMA 26** : fast above slow = bullish (golden cross),
   below = bearish (death cross).
2. **Close vs EMA 50** : long-term filter ; close above = bullish.
3. **MACD line vs MACD signal** : MACD above signal = bullish momentum.
4. **MACD histogram sign** : histogram > 0 = bullish momentum.

Aggregation : each vote contributes ±0.25 to the score ; the magnitude
is therefore in {-1, -0.5, 0, +0.5, +1}.

Confidence = fraction of indicators that voted in the score's direction
(e.g. 4/4 = 1.0, 3/4 = 0.75).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Final

from emeraude.agent.perception.indicators import ema, macd
from emeraude.agent.reasoning.strategies.base import StrategySignal

if TYPE_CHECKING:
    from emeraude.agent.perception.regime import Regime
    from emeraude.infra.market_data import Kline


_VOTE_WEIGHT: Final[Decimal] = Decimal("0.25")  # 4 votes * 0.25 = 1.0
_FOUR: Final[Decimal] = Decimal("4")
# Need EMA50 + MACD warmup. MACD default needs 26 + 9 - 1 = 34 bars ;
# EMA50 needs 50. So 50 covers both.
_MIN_KLINES: Final[int] = 50


class TrendFollower:
    """Trend-following strategy via EMA crosses + MACD."""

    name: ClassVar[str] = "trend_follower"

    def compute_signal(
        self,
        klines: list[Kline],
        regime: Regime,  # noqa: ARG002  (kept for protocol uniformity)
    ) -> StrategySignal | None:
        """See :meth:`Strategy.compute_signal`."""
        if len(klines) < _MIN_KLINES:
            return None

        closes = [k.close for k in klines]
        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        ema50 = ema(closes, 50)
        macd_result = macd(closes)

        if ema12 is None or ema26 is None or ema50 is None or macd_result is None:
            return None  # pragma: no cover  (guarded by _MIN_KLINES)

        votes: list[Decimal] = []
        reasons: list[str] = []

        # Vote 1 : EMA 12 vs EMA 26 (golden / death cross).
        if ema12 > ema26:
            votes.append(_VOTE_WEIGHT)
            reasons.append("EMA12>EMA26")
        else:
            votes.append(-_VOTE_WEIGHT)
            reasons.append("EMA12<=EMA26")

        # Vote 2 : Close vs EMA 50 (long-term trend filter).
        last_close = closes[-1]
        if last_close > ema50:
            votes.append(_VOTE_WEIGHT)
            reasons.append("close>EMA50")
        else:
            votes.append(-_VOTE_WEIGHT)
            reasons.append("close<=EMA50")

        # Vote 3 : MACD line vs signal line.
        if macd_result.macd > macd_result.signal:
            votes.append(_VOTE_WEIGHT)
            reasons.append("MACD>signal")
        else:
            votes.append(-_VOTE_WEIGHT)
            reasons.append("MACD<=signal")

        # Vote 4 : MACD histogram sign.
        if macd_result.histogram > Decimal("0"):
            votes.append(_VOTE_WEIGHT)
            reasons.append("hist>0")
        else:
            votes.append(-_VOTE_WEIGHT)
            reasons.append("hist<=0")

        score = sum(votes, Decimal("0"))

        # Confidence = fraction of votes in the dominant direction.
        positive = sum(1 for v in votes if v > 0)
        negative = sum(1 for v in votes if v < 0)
        confidence = Decimal(max(positive, negative)) / _FOUR

        return StrategySignal(
            score=score,
            confidence=confidence,
            reasoning=" + ".join(reasons),
        )
