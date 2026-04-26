"""Breakout Hunter — "buy the break, sell the breakdown" (doc 04 §3).

Fires when the price clearly clears the recent range with volume
confirmation. Returns ``None`` when no breakout is visible.

Indicators :

1. **Resistance / support breach** :
   - ``close > max(high)`` over the last :data:`_RANGE_LOOKBACK`
     bars (excluding current bar) times :data:`_BREACH_MARGIN` →
     bullish breakout vote.
   - ``close < min(low)`` times ``(2 - _BREACH_MARGIN)`` → bearish
     breakdown vote.
   - Else : silent — the strategy emits ``None``.
2. **Volume confirmation** : current bar's volume > median volume of
   the lookback window. Failed confirmation halves the confidence.
3. **Bollinger squeeze release** : if the BB width over the window
   was historically narrow (current width > median width), confidence
   is boosted ; squeeze releases are the typical breakout pattern.

The score is binary in {-1, +1} : a breakout has a clear direction.
Confidence reflects volume + squeeze quality.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Final

from emeraude.agent.perception.indicators import bollinger_bands
from emeraude.agent.reasoning.strategies.base import StrategySignal

if TYPE_CHECKING:
    from emeraude.agent.perception.regime import Regime
    from emeraude.infra.market_data import Kline


_RANGE_LOOKBACK: Final[int] = 20
_BREACH_MARGIN: Final[Decimal] = Decimal("1.005")  # 0.5 % beyond the level
_BASE_CONFIDENCE: Final[Decimal] = Decimal("0.4")
_VOLUME_BOOST: Final[Decimal] = Decimal("0.3")
_SQUEEZE_BOOST: Final[Decimal] = Decimal("0.3")
_MIN_KLINES: Final[int] = 21  # 20 lookback bars + current


class BreakoutHunter:
    """Detects momentum breakouts of the recent trading range."""

    name: ClassVar[str] = "breakout_hunter"

    def compute_signal(
        self,
        klines: list[Kline],
        regime: Regime,  # noqa: ARG002  (kept for protocol uniformity)
    ) -> StrategySignal | None:
        """See :meth:`Strategy.compute_signal`."""
        if len(klines) < _MIN_KLINES:
            return None

        # Lookback window EXCLUDES the current bar.
        window = klines[-(_RANGE_LOOKBACK + 1) : -1]
        current = klines[-1]

        resistance = max(k.high for k in window)
        support = min(k.low for k in window)

        # Determine direction.
        breach_up = resistance * _BREACH_MARGIN
        breach_down = support * (Decimal("2") - _BREACH_MARGIN)

        if current.close > breach_up:
            direction = Decimal("1")
            base_reason = f"close>{resistance:f}*1.005 breakout"
        elif current.close < breach_down:
            direction = Decimal("-1")
            base_reason = f"close<{support:f}*0.995 breakdown"
        else:
            return None  # no clear breakout

        confidence = _BASE_CONFIDENCE
        reasons = [base_reason]

        # Volume confirmation : compare current volume to median of window.
        sorted_volumes = sorted(k.volume for k in window)
        median_idx = len(sorted_volumes) // 2
        median_volume = sorted_volumes[median_idx]
        if current.volume > median_volume:
            confidence += _VOLUME_BOOST
            reasons.append("volume>median")

        # Bollinger squeeze release : compare current BB width to median width.
        # We look at BB widths in a small window of recent bars.
        widths = self._bb_width_history(klines)
        # ``widths`` is always non-empty here : the upfront ``_MIN_KLINES``
        # check guarantees BB(20) is computable for at least one sub-window.
        current_width = widths[-1]
        median_width = sorted(widths)[len(widths) // 2]
        if current_width > median_width:
            confidence += _SQUEEZE_BOOST
            reasons.append("BB-squeeze-release")

        # Cap confidence at 1.0 so the StrategySignal validation never
        # fails. With current constants (0.4 + 0.3 + 0.3 = 1.0) the cap
        # is at exactly the boundary ; future tuning may push past it.
        confidence = min(confidence, Decimal("1"))

        return StrategySignal(
            score=direction,
            confidence=confidence,
            reasoning=" + ".join(reasons),
        )

    @staticmethod
    def _bb_width_history(klines: list[Kline]) -> list[Decimal]:
        """Return ``upper - lower`` BB widths over a recent window.

        Used to detect "squeeze release" : the current width relative
        to its short history is more meaningful than its absolute value.

        The caller guarantees ``len(klines) >= _MIN_KLINES`` so at least
        one BB call succeeds.
        """
        widths: list[Decimal] = []
        for i in range(max(_RANGE_LOOKBACK, len(klines) - 5), len(klines) + 1):
            sub = klines[:i]
            bb = bollinger_bands([k.close for k in sub])
            if bb is not None:
                widths.append(bb.upper - bb.lower)
        return widths
