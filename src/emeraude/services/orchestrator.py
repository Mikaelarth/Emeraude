"""Single-cycle decision orchestrator (doc 05 §"BotMaitre cycle 60 min").

This is the first end-to-end wiring of the agent layer : it takes the
inputs the bot has at the top of every cycle (current capital + recent
klines) and produces a :class:`CycleDecision` bundling every piece of
audit-relevant context — including the cycles where the bot chose not
to trade.

Pipeline (step by step) :

1. **Circuit breaker** — read state. If TRIGGERED or FROZEN, return a
   ``"breaker_blocked"`` skip immediately. R10 cannot be bypassed.
2. **Klines guard** — empty input returns ``"empty_klines"`` skip.
3. **Regime** — :func:`detect_regime`. ``None`` returns
   ``"insufficient_data"`` skip.
4. **Per-strategy signals** — :meth:`Strategy.compute_signal` for each
   strategy. ``None`` signals are silently dropped by the ensemble.
5. **Adaptive weights** — :meth:`RegimeMemory.get_adaptive_weights`
   produces a regime-specific weight per strategy, falling back to
   :data:`REGIME_WEIGHTS` for couples below 30 trades. Optional
   Thompson multiplier from :class:`StrategyBandit` when injected.
6. **Ensemble vote** — :func:`vote`. ``None`` returns
   ``"no_contributors"`` skip.
7. **Quality gate** — :func:`is_qualified`. ``False`` returns
   ``"ensemble_not_qualified"`` skip.
8. **Position size** — Kelly fractional + vol-targeting + abs cap.
   Inputs : (a) the dominant strategy's win rate from
   :class:`RegimeMemory` (with a ``0.4`` fallback below 30 trades, cf.
   doc 04 walk-forward), (b) a ``1.5`` R-multiple default until
   per-strategy R is tracked (next iteration).
9. **WARNING sizing** — multiply quantity by ``warning_size_factor``
   (default ``0.5``) when the breaker is in WARNING.
10. **Zero-quantity guard** — return ``"position_size_zero"`` skip if
    Kelly + caps collapse to zero.
11. **Direction** — ``LONG`` if ensemble score > 0, ``SHORT`` otherwise.

This function is **pure** in the sense that doc 05 cares about : no
network, no order placement, no scheduling. It still reads from the
local SQLite DB (breaker state, regime memory counts, bandit posteriors)
because those reads are part of what shapes the decision.

The actual order placement, position management, and learning feedback
(calling :meth:`RegimeMemory.record_outcome` after a trade closes) belong
to the upcoming ``services.auto_trader`` (anti-rule A1 : we do not
anticipate it here).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final, cast

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.execution.circuit_breaker import CircuitBreakerState
from emeraude.agent.learning.regime_memory import RegimeMemory
from emeraude.agent.perception.indicators import atr
from emeraude.agent.perception.regime import Regime, detect_regime
from emeraude.agent.reasoning.ensemble import (
    REGIME_WEIGHTS,
    EnsembleVote,
    is_qualified,
    vote,
)
from emeraude.agent.reasoning.position_sizing import (
    DEFAULT_KELLY_MULTIPLIER,
    DEFAULT_MAX_PCT_PER_TRADE,
    DEFAULT_VOL_TARGET,
    position_size,
)
from emeraude.agent.reasoning.strategies import (
    BreakoutHunter,
    MeanReversion,
    Strategy,
    TrendFollower,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from emeraude.agent.learning.bandit import StrategyBandit
    from emeraude.agent.reasoning.strategies import StrategySignal
    from emeraude.infra.market_data import Kline


_ZERO: Final[Decimal] = Decimal("0")
_DEFAULT_WARNING_SIZE_FACTOR: Final[Decimal] = Decimal("0.5")
# Slight edge over break-even (p > 1/(1+b), i.e. > 0.4 at b=1.5) so that
# Kelly is non-zero on cold start. The half-Kelly multiplier and the 5 %
# cap keep the actual exposure prudent.
_DEFAULT_FALLBACK_WIN_RATE: Final[Decimal] = Decimal("0.45")
_DEFAULT_FALLBACK_WIN_LOSS_RATIO: Final[Decimal] = Decimal("1.5")
_DEFAULT_ADAPTIVE_MIN_TRADES: Final[int] = 30


# ─── Skip reasons ───────────────────────────────────────────────────────────

SKIP_BREAKER_BLOCKED: Final[str] = "breaker_blocked"
SKIP_EMPTY_KLINES: Final[str] = "empty_klines"
SKIP_INSUFFICIENT_DATA: Final[str] = "insufficient_data"
SKIP_NO_CONTRIBUTORS: Final[str] = "no_contributors"
SKIP_ENSEMBLE_NOT_QUALIFIED: Final[str] = "ensemble_not_qualified"
SKIP_POSITION_SIZE_ZERO: Final[str] = "position_size_zero"


# ─── Public types ───────────────────────────────────────────────────────────


class TradeDirection(StrEnum):
    """Directional intent emitted by the orchestrator.

    The actual executor (``services.auto_trader``, future iteration)
    decides whether the surrounding venue (e.g. Binance Spot) supports
    the requested direction. The orchestrator does not filter here.
    """

    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class CycleDecision:
    """Result of one :class:`Orchestrator` cycle.

    Captures the full reasoning chain so the audit trail (R9) can
    reconstruct any past decision — including the ones where the bot
    chose not to trade — without having to re-run the pipeline.

    A ``should_trade=False`` decision is **never** an error : it is the
    bot's normal "stay flat" signal. ``skip_reason`` documents why and
    is one of the ``SKIP_*`` constants exported by this module.

    Attributes:
        should_trade: ``True`` iff the orchestrator wants to open a
            new position this cycle.
        regime: detected market regime, or ``None`` when classification
            could not happen (insufficient data, breaker block before
            regime detection).
        ensemble_vote: aggregated ensemble vote, or ``None`` when no
            strategy contributed (or no vote was computed).
        qualified: ``True`` iff the ensemble vote passed
            :func:`is_qualified`. May still be ``False`` for skipped
            cycles (e.g. weak conviction).
        direction: ``LONG`` / ``SHORT`` — only set when ``should_trade``.
        position_quantity: base-asset units to trade. ``Decimal(0)`` for
            every skip case.
        price: last close from the kline series, or ``Decimal(0)`` for
            empty input.
        atr: current ATR. ``None`` when not computed (early skip),
            ``Decimal(0)`` when warmup unmet.
        breaker_state: the breaker state observed at decision time.
        skip_reason: one of the ``SKIP_*`` constants, or ``None`` when
            ``should_trade=True``.
        reasoning: short human-readable trace for audit + UX.
    """

    should_trade: bool
    regime: Regime | None
    ensemble_vote: EnsembleVote | None
    qualified: bool
    direction: TradeDirection | None
    position_quantity: Decimal
    price: Decimal
    atr: Decimal | None
    breaker_state: CircuitBreakerState
    skip_reason: str | None
    reasoning: str


# ─── Orchestrator ───────────────────────────────────────────────────────────


class Orchestrator:
    """Single-cycle pure decision : klines + capital -> :class:`CycleDecision`.

    Construct once at process start and call :meth:`make_decision`
    every cycle. The same instance is safe to reuse across cycles : it
    holds no mutable internal state, and every stateful read goes
    through DB-backed components (breaker, regime memory, bandit).

    Most knobs default to the values agreed in doc 04 / doc 05. Override
    a knob when you have a measurement-driven reason ; do not override
    by aesthetics (anti-rule A1).
    """

    def __init__(
        self,
        *,
        strategies: list[Strategy] | None = None,
        regime_memory: RegimeMemory | None = None,
        bandit: StrategyBandit | None = None,
        regime_weights: Mapping[Regime, Mapping[str, Decimal]] | None = None,
        kelly_multiplier: Decimal = DEFAULT_KELLY_MULTIPLIER,
        max_pct_per_trade: Decimal = DEFAULT_MAX_PCT_PER_TRADE,
        vol_target: Decimal = DEFAULT_VOL_TARGET,
        warning_size_factor: Decimal = _DEFAULT_WARNING_SIZE_FACTOR,
        fallback_win_rate: Decimal = _DEFAULT_FALLBACK_WIN_RATE,
        fallback_win_loss_ratio: Decimal = _DEFAULT_FALLBACK_WIN_LOSS_RATIO,
        adaptive_min_trades: int = _DEFAULT_ADAPTIVE_MIN_TRADES,
    ) -> None:
        """Wire the orchestrator with explicit dependencies.

        Args:
            strategies: list of strategies to vote. Defaults to the
                doc-04 trio (TrendFollower, MeanReversion, BreakoutHunter).
            regime_memory: per-(strategy, regime) outcome memory.
                Defaults to a fresh :class:`RegimeMemory` (DB-backed).
            bandit: optional Thompson sampler. When ``None`` (default)
                the orchestrator is fully deterministic given its
                inputs and DB state.
            regime_weights: fallback weights when regime memory has
                fewer than ``adaptive_min_trades`` observations for a
                couple. Defaults to :data:`REGIME_WEIGHTS`.
            kelly_multiplier: forwarded to :func:`position_size`.
            max_pct_per_trade: forwarded to :func:`position_size`.
            vol_target: forwarded to :func:`position_size`.
            warning_size_factor: multiplier applied to the position
                quantity when the breaker is in WARNING. Default
                ``0.5`` — sizing halved.
            fallback_win_rate: win rate used by Kelly while regime
                memory has fewer than ``adaptive_min_trades`` trades
                for the dominant strategy in the current regime.
            fallback_win_loss_ratio: average R-multiple used by Kelly
                until per-strategy R is tracked (future iteration).
            adaptive_min_trades: trade count above which regime memory
                stats override the fallbacks.
        """
        if strategies is None:
            # mypy treats ClassVar `name` on the concrete classes as
            # incompatible with the Protocol's instance attribute, so the
            # explicit cast widens the inferred element type.
            strategies = cast(
                "list[Strategy]",
                [TrendFollower(), MeanReversion(), BreakoutHunter()],
            )
        if not strategies:
            msg = "strategies must not be empty"
            raise ValueError(msg)

        self._strategies: list[Strategy] = list(strategies)
        self._strategy_names: list[str] = [s.name for s in self._strategies]
        self._regime_memory: RegimeMemory = (
            regime_memory if regime_memory is not None else RegimeMemory()
        )
        self._bandit: StrategyBandit | None = bandit
        self._regime_weights: Mapping[Regime, Mapping[str, Decimal]] = (
            regime_weights if regime_weights is not None else REGIME_WEIGHTS
        )
        self._kelly_multiplier = kelly_multiplier
        self._max_pct_per_trade = max_pct_per_trade
        self._vol_target = vol_target
        self._warning_size_factor = warning_size_factor
        self._fallback_win_rate = fallback_win_rate
        self._fallback_win_loss_ratio = fallback_win_loss_ratio
        self._adaptive_min_trades = adaptive_min_trades

    # ─── Public API ─────────────────────────────────────────────────────────

    def make_decision(  # noqa: PLR0911  (one return per pipeline gate is the clearest form)
        self,
        *,
        capital: Decimal,
        klines: list[Kline],
    ) -> CycleDecision:
        """Run the full pipeline and return a :class:`CycleDecision`.

        Args:
            capital: USD capital currently available to the bot. Used
                by Kelly sizing only ; the orchestrator does not write
                to capital.
            klines: chronological list of OHLCV bars. The last entry is
                treated as "now". An empty list short-circuits to a
                ``SKIP_EMPTY_KLINES`` decision.

        Returns:
            A :class:`CycleDecision`. ``should_trade`` is ``True`` iff
            every gate of the pipeline passed and a positive position
            quantity was computed.
        """
        breaker_state = circuit_breaker.get_state()
        last_price = klines[-1].close if klines else _ZERO

        if not circuit_breaker.is_trade_allowed_with_warning():
            return self._skip(
                regime=None,
                vote_obj=None,
                qualified=False,
                price=last_price,
                atr_value=None,
                breaker_state=breaker_state,
                reason=SKIP_BREAKER_BLOCKED,
                msg=f"circuit breaker is {breaker_state.value}",
            )

        if not klines:
            return self._skip(
                regime=None,
                vote_obj=None,
                qualified=False,
                price=_ZERO,
                atr_value=None,
                breaker_state=breaker_state,
                reason=SKIP_EMPTY_KLINES,
                msg="no klines provided",
            )

        regime = detect_regime(klines)
        if regime is None:
            return self._skip(
                regime=None,
                vote_obj=None,
                qualified=False,
                price=last_price,
                atr_value=None,
                breaker_state=breaker_state,
                reason=SKIP_INSUFFICIENT_DATA,
                msg=f"insufficient klines ({len(klines)}) to detect regime",
            )

        signals: dict[str, StrategySignal | None] = {
            s.name: s.compute_signal(klines, regime) for s in self._strategies
        }

        weights = self._compute_weights(regime)

        ev = vote(signals, weights)
        if ev is None:
            return self._skip(
                regime=regime,
                vote_obj=None,
                qualified=False,
                price=last_price,
                atr_value=None,
                breaker_state=breaker_state,
                reason=SKIP_NO_CONTRIBUTORS,
                msg="no strategy produced a usable signal",
            )

        if not is_qualified(ev):
            return self._skip(
                regime=regime,
                vote_obj=ev,
                qualified=False,
                price=last_price,
                atr_value=None,
                breaker_state=breaker_state,
                reason=SKIP_ENSEMBLE_NOT_QUALIFIED,
                msg=ev.reasoning,
            )

        atr_value = atr(klines)
        # The regime gate already requires 210+ klines and ATR's warmup
        # is only 15, so this branch is unreachable in production flows.
        if atr_value is None:  # pragma: no cover
            atr_value = _ZERO

        dominant = self._dominant_strategy(signals, weights)
        win_rate = self._win_rate_for(dominant, regime)

        quantity = position_size(
            capital=capital,
            win_rate=win_rate,
            win_loss_ratio=self._fallback_win_loss_ratio,
            price=last_price,
            atr=atr_value,
            kelly_multiplier=self._kelly_multiplier,
            max_pct_per_trade=self._max_pct_per_trade,
            vol_target=self._vol_target,
        )

        if breaker_state == CircuitBreakerState.WARNING:
            quantity = quantity * self._warning_size_factor

        if quantity == _ZERO:
            return self._skip(
                regime=regime,
                vote_obj=ev,
                qualified=True,
                price=last_price,
                atr_value=atr_value,
                breaker_state=breaker_state,
                reason=SKIP_POSITION_SIZE_ZERO,
                msg="kelly and caps collapsed to zero",
            )

        direction = TradeDirection.LONG if ev.score > _ZERO else TradeDirection.SHORT

        return CycleDecision(
            should_trade=True,
            regime=regime,
            ensemble_vote=ev,
            qualified=True,
            direction=direction,
            position_quantity=quantity,
            price=last_price,
            atr=atr_value,
            breaker_state=breaker_state,
            skip_reason=None,
            reasoning=ev.reasoning,
        )

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _compute_weights(self, regime: Regime) -> dict[str, Decimal]:
        """Build the final per-strategy weights for the current regime."""
        adaptive = self._regime_memory.get_adaptive_weights(
            self._strategy_names,
            fallback=self._regime_weights,
            min_trades=self._adaptive_min_trades,
        )
        weights = dict(adaptive[regime])

        if self._bandit is not None:
            samples = self._bandit.sample_weights(self._strategy_names)
            weights = {name: weights[name] * samples[name] for name in self._strategy_names}

        return weights

    def _dominant_strategy(
        self,
        signals: Mapping[str, StrategySignal | None],
        weights: Mapping[str, Decimal],
    ) -> str:
        """Return the strategy with the largest absolute contribution.

        ``contribution = |score * confidence * weight|``. Used to pick
        whose win rate feeds the position-size Kelly term. Defaults to
        the first strategy name when every contribution is zero (in
        practice the vote would already have returned ``None`` in that
        case ; the fallback is just a type-narrowing safety).
        """
        best_name = self._strategy_names[0]
        best_value = _ZERO
        for name, sig in signals.items():
            if sig is None:
                continue
            w = weights.get(name, _ZERO)
            value = abs(sig.score * sig.confidence * w)
            if value > best_value:
                best_value = value
                best_name = name
        return best_name

    def _win_rate_for(self, strategy: str, regime: Regime) -> Decimal:
        """Return the per-(strategy, regime) win rate, or the fallback."""
        stats = self._regime_memory.get_stats(strategy, regime)
        if stats.n_trades >= self._adaptive_min_trades:
            return stats.win_rate
        return self._fallback_win_rate

    def _skip(
        self,
        *,
        regime: Regime | None,
        vote_obj: EnsembleVote | None,
        qualified: bool,
        price: Decimal,
        atr_value: Decimal | None,
        breaker_state: CircuitBreakerState,
        reason: str,
        msg: str,
    ) -> CycleDecision:
        """Build a skip :class:`CycleDecision` with consistent defaults."""
        return CycleDecision(
            should_trade=False,
            regime=regime,
            ensemble_vote=vote_obj,
            qualified=qualified,
            direction=None,
            position_quantity=_ZERO,
            price=price,
            atr=atr_value,
            breaker_state=breaker_state,
            skip_reason=reason,
            reasoning=msg,
        )
