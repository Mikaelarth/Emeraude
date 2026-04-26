"""Strategy package — directional signal generators.

Each strategy implements the :class:`Strategy` Protocol and produces a
:class:`StrategySignal` (or ``None`` when it has no opinion).

Concrete strategies (cf. doc 04) :

* :class:`TrendFollower`   — "the trend is your friend" : EMA crosses + MACD.
* :class:`MeanReversion`   — "extremes return to the mean" : RSI / BB / Stoch
  extremes.
* :class:`BreakoutHunter`  — "buy the break, sell the breakdown" : recent
  resistance/support breach with volume + ATR confirmation.
"""

from emeraude.agent.reasoning.strategies.base import Strategy, StrategySignal
from emeraude.agent.reasoning.strategies.breakout_hunter import BreakoutHunter
from emeraude.agent.reasoning.strategies.mean_reversion import MeanReversion
from emeraude.agent.reasoning.strategies.trend_follower import TrendFollower

__all__ = [
    "BreakoutHunter",
    "MeanReversion",
    "Strategy",
    "StrategySignal",
    "TrendFollower",
]
