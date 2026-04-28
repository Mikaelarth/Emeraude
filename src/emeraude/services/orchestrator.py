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
4. **Tradability meta-gate** *(optional, doc 10 R8)* — when injected,
   skips with ``"low_tradability"`` if the current market state is
   untradable (high vol / low volume / blackout hour).
5. **Correlation stress gate** *(optional, doc 10 R7)* — when injected,
   skips with ``"correlation_stress"`` if the average pairwise
   correlation across tracked coins crossed the stress threshold
   (default 0.8). Diversification illusoire ; no new entry.
6. **Per-strategy signals** — :meth:`Strategy.compute_signal` for each
   strategy. ``None`` signals are silently dropped by the ensemble.
7. **Adaptive weights** — :meth:`RegimeMemory.get_adaptive_weights`
   produces a regime-specific weight per strategy, falling back to
   :data:`REGIME_WEIGHTS` for couples below 30 trades. Optional
   Thompson multiplier from :class:`StrategyBandit` when injected.
8. **Ensemble vote** — :func:`vote`. ``None`` returns
   ``"no_contributors"`` skip.
9. **Quality gate** — :func:`is_qualified`. ``False`` returns
   ``"ensemble_not_qualified"`` skip.
10. **Position size** — Kelly fractional + vol-targeting + abs cap.
    Inputs : (a) the dominant strategy's win rate from
    :class:`RegimeMemory` (with a ``0.4`` fallback below 30 trades, cf.
    doc 04 walk-forward), (b) a ``1.5`` R-multiple default until
    per-strategy R is tracked (next iteration).
11. **WARNING sizing** — multiply quantity by ``warning_size_factor``
    (default ``0.5``) when the breaker is in WARNING.
12. **Zero-quantity guard** — return ``"position_size_zero"`` skip if
    Kelly + caps collapse to zero.
13. **Direction** — ``LONG`` if ensemble score > 0, ``SHORT`` otherwise.
14. **Risk levels** — :func:`compute_levels` produces stop / target /
    R-multiple from ATR. ``"degenerate_risk"`` skip if risk-per-unit
    is zero (ATR=0 + non-zero stop multiplier still yields zero risk).
15. **R/R floor (anti-rule A4)** — :func:`is_acceptable_rr` rejects
    trades below ``min_rr`` (default ``1.5``). The full
    :class:`TradeLevels` is included in the skipped
    :class:`CycleDecision` so the audit can show *why* the trade was
    degraded to a skip.
16. **Microstructure gate** *(optional, doc 10 R6)* — last gate before
    commit. When injected, called with the intended
    :class:`TradeDirection` ; skips with ``"low_microstructure"`` on
    wide spread, thin volume, or directional flow opposing the side.

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
from emeraude.agent.learning.hoeffding import (
    DEFAULT_DELTA,
    HoeffdingDecision,
    evaluate_hoeffding_gate,
)
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
from emeraude.agent.reasoning.risk_manager import (
    DEFAULT_MIN_RR,
    DEFAULT_STOP_ATR_MULTIPLIER,
    DEFAULT_TARGET_ATR_MULTIPLIER,
    Side,
    TradeLevels,
    compute_levels,
    is_acceptable_rr,
)
from emeraude.agent.reasoning.strategies import (
    BreakoutHunter,
    MeanReversion,
    Strategy,
    TrendFollower,
)
from emeraude.infra import audit

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from emeraude.agent.learning.bandit import StrategyBanditLike
    from emeraude.agent.perception.correlation import CorrelationReport
    from emeraude.agent.perception.microstructure import MicrostructureReport
    from emeraude.agent.perception.tradability import TradabilityReport
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
# Anti-rule A4 : R/R below the configured floor (default 1.5) -> skip.
SKIP_RR_TOO_LOW: Final[str] = "rr_too_low"
# Degenerate ATR=0 yields zero risk and zero reward — non-meaningful trade.
SKIP_DEGENERATE_RISK: Final[str] = "degenerate_risk"
# Doc 10 R8 : meta-gate says the current market state is untradable
# (high volatility / low volume / blackout hour).
SKIP_LOW_TRADABILITY: Final[str] = "low_tradability"
# Doc 10 R7 : average pairwise correlation across tracked coins crossed
# the stress threshold (default 0.8). Diversification illusoire ; any
# new entry would compound the same systemic exposure.
SKIP_CORRELATION_STRESS: Final[str] = "correlation_stress"
# Doc 10 R6 : post-signal microstructure filter rejected the trade
# (wide spread, thin volume, or directional flow opposing the side).
SKIP_LOW_MICROSTRUCTURE: Final[str] = "low_microstructure"


# ─── Audit events ───────────────────────────────────────────────────────────


# Doc 10 R11 observability : every adaptive override decision (win
# rate or win/loss ratio) emits one of these so an audit replay can
# explain why the orchestrator used the fallback vs. the empirical
# estimate on any given cycle. Public so callers (and tests) can
# query the audit log by event_type without importing a private name.
AUDIT_HOEFFDING_DECISION: Final[str] = "HOEFFDING_DECISION"
# Special reason : the W/L ratio short-circuits before the Hoeffding
# gate when the realized ratio is non-positive (a fresh bucket with
# zero wins or zero losses can't be Kelly-usable). Surfaced as a
# distinct ``reason`` so audits don't mistake it for a sample-floor or
# significance failure.
GATE_RATIO_NON_POSITIVE: Final[str] = "ratio_non_positive"


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
        dominant_strategy: name of the strategy with the largest
            ``|score * confidence * weight|`` contribution. Set on every
            decision computed *after* the qualification gate (the late
            skips and the happy path) ; ``None`` for early skips. Used
            by :class:`emeraude.services.auto_trader.AutoTrader` to
            pass the right ``strategy`` key to
            :meth:`PositionTracker.open_position` so learning feedback
            lands on the correct row.
        position_quantity: base-asset units to trade. ``Decimal(0)`` for
            every skip case.
        price: last close from the kline series, or ``Decimal(0)`` for
            empty input.
        atr: current ATR. ``None`` when not computed (early skip),
            ``Decimal(0)`` when warmup unmet.
        trade_levels: stop / target / R-multiple from the risk manager.
            ``None`` for every skip happening before the levels are
            computed (gates 1-9). Set even on ``SKIP_RR_TOO_LOW`` so
            the audit can show *why* the trade was rejected.
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
    dominant_strategy: str | None
    position_quantity: Decimal
    price: Decimal
    atr: Decimal | None
    trade_levels: TradeLevels | None
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
        bandit: StrategyBanditLike | None = None,
        meta_gate: Callable[[list[Kline]], TradabilityReport] | None = None,
        correlation_gate: Callable[[], CorrelationReport] | None = None,
        microstructure_gate: Callable[[TradeDirection], MicrostructureReport] | None = None,
        regime_weights: Mapping[Regime, Mapping[str, Decimal]] | None = None,
        kelly_multiplier: Decimal = DEFAULT_KELLY_MULTIPLIER,
        max_pct_per_trade: Decimal = DEFAULT_MAX_PCT_PER_TRADE,
        vol_target: Decimal = DEFAULT_VOL_TARGET,
        warning_size_factor: Decimal = _DEFAULT_WARNING_SIZE_FACTOR,
        fallback_win_rate: Decimal = _DEFAULT_FALLBACK_WIN_RATE,
        fallback_win_loss_ratio: Decimal = _DEFAULT_FALLBACK_WIN_LOSS_RATIO,
        adaptive_min_trades: int = _DEFAULT_ADAPTIVE_MIN_TRADES,
        hoeffding_delta: Decimal = DEFAULT_DELTA,
        stop_atr_multiplier: Decimal = DEFAULT_STOP_ATR_MULTIPLIER,
        target_atr_multiplier: Decimal = DEFAULT_TARGET_ATR_MULTIPLIER,
        min_rr: Decimal = DEFAULT_MIN_RR,
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
            meta_gate: optional callable that scores the current
                market state's tradability (doc 10 R8). When ``None``
                (default), no gate fires and behaviour is unchanged.
                When injected, called after regime detection ; if
                ``report.is_tradable`` is False, the cycle skips with
                ``SKIP_LOW_TRADABILITY``. Typically wired with
                :func:`emeraude.agent.perception.tradability.compute_tradability`
                or a ``functools.partial`` of it with custom
                thresholds.
            correlation_gate: optional callable that detects a
                cross-coin correlation stress regime (doc 10 R7).
                When ``None`` (default), no gate fires. When
                injected, called after regime detection (and after
                ``meta_gate``) ; if ``report.is_stress`` is True,
                the cycle skips with ``SKIP_CORRELATION_STRESS``.
                Takes no argument because the gate's closure owns
                the multi-symbol kline history (the orchestrator
                only sees the focal-symbol series).
            microstructure_gate: optional callable that runs the
                doc 10 R6 execution gate (spread + volume + taker
                flow). When ``None`` (default), no gate fires.
                When injected, called as the last gate before commit
                with the orchestrator's intended :class:`TradeDirection`
                so the gate can include the directional flow check ;
                if ``report.accepted`` is False, the cycle skips
                with ``SKIP_LOW_MICROSTRUCTURE``.
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
                stats are *eligible* to override the fallbacks. The
                actual override fires only when the Hoeffding test
                says the empirical estimate is statistically
                distinguishable from the fallback (doc 10 R11).
            hoeffding_delta: confidence risk level for the
                Hoeffding-bounded override of fallback values. Default
                ``0.05`` (95 % confidence). Smaller delta = stricter
                bound = more conservative override.
            stop_atr_multiplier: ATR multiplier for the protective stop.
                Default ``2.0`` (doc 04 §"_compute_stop_take").
            target_atr_multiplier: ATR multiplier for the take-profit.
                Default ``4.0`` (doc 04 forces nominal R/R = 4/2 = 2.0).
            min_rr: minimum acceptable R/R ratio. Anti-rule A4 floor :
                trades below this ratio are degraded to a skip rather
                than presented as opportunities.
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
        self._bandit: StrategyBanditLike | None = bandit
        self._meta_gate: Callable[[list[Kline]], TradabilityReport] | None = meta_gate
        self._correlation_gate: Callable[[], CorrelationReport] | None = correlation_gate
        self._microstructure_gate: Callable[[TradeDirection], MicrostructureReport] | None = (
            microstructure_gate
        )
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
        self._hoeffding_delta = hoeffding_delta
        self._stop_atr_multiplier = stop_atr_multiplier
        self._target_atr_multiplier = target_atr_multiplier
        self._min_rr = min_rr

    # ─── Public API ─────────────────────────────────────────────────────────

    def make_decision(  # noqa: PLR0911, PLR0912  (one return per pipeline gate is the clearest form)
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

        # Doc 10 R8 meta-gate : "should we trade now ?". Fired after
        # regime detection so we only score a fully-warmed kline
        # history. When the caller did not inject a gate, behaviour
        # is unchanged.
        if self._meta_gate is not None:
            tradability = self._meta_gate(klines)
            if not tradability.is_tradable:
                return self._skip(
                    regime=regime,
                    vote_obj=None,
                    qualified=False,
                    price=last_price,
                    atr_value=None,
                    breaker_state=breaker_state,
                    reason=SKIP_LOW_TRADABILITY,
                    msg=(
                        f"tradability {tradability.tradability:.3f} "
                        f"(vol={tradability.volatility_score:.2f}, "
                        f"vol={tradability.volume_score:.2f}, "
                        f"hour={tradability.hour_score:.2f})"
                    ),
                )

        # Doc 10 R7 correlation stress : if the average pairwise
        # correlation across tracked coins crossed the stress
        # threshold, diversification is illusoire — skip new entries.
        # The gate's closure owns the multi-symbol kline cache ; the
        # orchestrator only sees the focal symbol's klines.
        if self._correlation_gate is not None:
            correlation = self._correlation_gate()
            if correlation.is_stress:
                return self._skip(
                    regime=regime,
                    vote_obj=None,
                    qualified=False,
                    price=last_price,
                    atr_value=None,
                    breaker_state=breaker_state,
                    reason=SKIP_CORRELATION_STRESS,
                    msg=(
                        f"correlation stress mean={correlation.mean_correlation:.3f} "
                        f">= threshold {correlation.threshold} "
                        f"(n_symbols={correlation.n_symbols}, "
                        f"n_pairs={correlation.n_pairs})"
                    ),
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
        win_loss_ratio = self._win_loss_ratio_for(dominant, regime)

        quantity = position_size(
            capital=capital,
            win_rate=win_rate,
            win_loss_ratio=win_loss_ratio,
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
                dominant_strategy=dominant,
            )

        direction = TradeDirection.LONG if ev.score > _ZERO else TradeDirection.SHORT
        side = Side.LONG if direction is TradeDirection.LONG else Side.SHORT
        levels = compute_levels(
            entry=last_price,
            atr=atr_value,
            side=side,
            stop_atr_multiplier=self._stop_atr_multiplier,
            target_atr_multiplier=self._target_atr_multiplier,
        )

        if levels.risk_per_unit == _ZERO:
            return self._skip(
                regime=regime,
                vote_obj=ev,
                qualified=True,
                price=last_price,
                atr_value=atr_value,
                breaker_state=breaker_state,
                reason=SKIP_DEGENERATE_RISK,
                msg="risk per unit is zero (ATR=0 or stop multiplier=0)",
                trade_levels=levels,
                dominant_strategy=dominant,
            )

        if not is_acceptable_rr(levels, min_rr=self._min_rr):
            return self._skip(
                regime=regime,
                vote_obj=ev,
                qualified=True,
                price=last_price,
                atr_value=atr_value,
                breaker_state=breaker_state,
                reason=SKIP_RR_TOO_LOW,
                msg=f"R/R {levels.r_multiple} below floor {self._min_rr} (anti-rule A4)",
                trade_levels=levels,
                dominant_strategy=dominant,
            )

        # Doc 10 R6 microstructure : last gate before commit. Runs
        # after R/R floor so the cheaper gates filter first ; takes
        # the intended :class:`TradeDirection` so the gate can
        # include the directional taker-flow check.
        if self._microstructure_gate is not None:
            micro = self._microstructure_gate(direction)
            if not micro.accepted:
                return self._skip(
                    regime=regime,
                    vote_obj=ev,
                    qualified=True,
                    price=last_price,
                    atr_value=atr_value,
                    breaker_state=breaker_state,
                    reason=SKIP_LOW_MICROSTRUCTURE,
                    msg="microstructure gate rejected: " + " ; ".join(micro.reasons),
                    trade_levels=levels,
                    dominant_strategy=dominant,
                )

        return CycleDecision(
            should_trade=True,
            regime=regime,
            ensemble_vote=ev,
            qualified=True,
            direction=direction,
            dominant_strategy=dominant,
            position_quantity=quantity,
            price=last_price,
            atr=atr_value,
            trade_levels=levels,
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
        """Return the per-(strategy, regime) win rate, or the fallback.

        The override fires only when (a) ``n_trades >= adaptive_min_trades``
        AND (b) the Hoeffding bound says the empirical win-rate is
        statistically distinguishable from the fallback at confidence
        ``1 - hoeffding_delta`` (doc 10 R11). Otherwise the gap could
        be sampling noise and the fallback stays.

        Every call emits one ``HOEFFDING_DECISION`` audit event so the
        audit trail can later answer "why did this cycle use the
        fallback ?" / "from which trade was the override active ?".
        """
        stats = self._regime_memory.get_stats(strategy, regime)
        decision = evaluate_hoeffding_gate(
            observed=stats.win_rate,
            prior=self._fallback_win_rate,
            n=stats.n_trades,
            min_trades=self._adaptive_min_trades,
            delta=self._hoeffding_delta,
        )
        self._audit_hoeffding(
            axis="win_rate",
            strategy=strategy,
            regime=regime,
            decision=decision,
        )
        return stats.win_rate if decision.override else self._fallback_win_rate

    def _win_loss_ratio_for(self, strategy: str, regime: Regime) -> Decimal:
        """Return the per-(strategy, regime) Kelly R-multiple.

        Adaptive when all three conditions hold :
        (a) ``n_trades >= adaptive_min_trades``,
        (b) the realized ratio is strictly positive (a freshly-warmed
        bucket with zero losses yields ``0`` and is not Kelly-usable —
        the caller must keep the fallback active until both wins and
        losses have been observed),
        (c) the Hoeffding bound says the empirical ratio is
        statistically distinguishable from the fallback at confidence
        ``1 - hoeffding_delta`` (doc 10 R11).

        Otherwise the fallback stays active. One ``HOEFFDING_DECISION``
        audit event is emitted per call for observability ; when (b)
        short-circuits the event carries reason
        ``ratio_non_positive`` rather than a Hoeffding-gate reason.
        """
        stats = self._regime_memory.get_stats(strategy, regime)
        ratio = stats.win_loss_ratio

        # Special short-circuit : a freshly-warmed bucket with zero
        # losses or zero wins yields ratio == 0 (or negative if a
        # downstream future change ever allows it). Surface as a
        # distinct audit reason so replays can tell "we had data but
        # it was lopsided" apart from "we did not have enough data".
        if ratio <= _ZERO:
            decision = HoeffdingDecision(
                observed=ratio,
                prior=self._fallback_win_loss_ratio,
                n=stats.n_trades,
                delta=self._hoeffding_delta,
                epsilon=Decimal("Infinity"),
                min_trades=self._adaptive_min_trades,
                override=False,
                reason=GATE_RATIO_NON_POSITIVE,
            )
            self._audit_hoeffding(
                axis="win_loss_ratio",
                strategy=strategy,
                regime=regime,
                decision=decision,
            )
            return self._fallback_win_loss_ratio

        decision = evaluate_hoeffding_gate(
            observed=ratio,
            prior=self._fallback_win_loss_ratio,
            n=stats.n_trades,
            min_trades=self._adaptive_min_trades,
            delta=self._hoeffding_delta,
        )
        self._audit_hoeffding(
            axis="win_loss_ratio",
            strategy=strategy,
            regime=regime,
            decision=decision,
        )
        return ratio if decision.override else self._fallback_win_loss_ratio

    def _audit_hoeffding(
        self,
        *,
        axis: str,
        strategy: str,
        regime: Regime,
        decision: HoeffdingDecision,
    ) -> None:
        """Emit one ``HOEFFDING_DECISION`` audit event.

        Decimal fields are stringified so the JSON payload round-trips
        without precision loss. ``epsilon`` may be ``Infinity`` (n=0
        or ratio short-circuit) ; ``str(Decimal("Infinity"))`` is
        ``"Infinity"`` which is valid JSON via the audit logger.
        """
        audit.audit(
            AUDIT_HOEFFDING_DECISION,
            {
                "axis": axis,
                "strategy": strategy,
                "regime": regime.value,
                "n_trades": decision.n,
                "min_trades": decision.min_trades,
                "delta": str(decision.delta),
                "observed": str(decision.observed),
                "prior": str(decision.prior),
                "epsilon": str(decision.epsilon),
                "override": decision.override,
                "reason": decision.reason,
            },
        )

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
        trade_levels: TradeLevels | None = None,
        dominant_strategy: str | None = None,
    ) -> CycleDecision:
        """Build a skip :class:`CycleDecision` with consistent defaults."""
        return CycleDecision(
            should_trade=False,
            regime=regime,
            ensemble_vote=vote_obj,
            qualified=qualified,
            direction=None,
            dominant_strategy=dominant_strategy,
            position_quantity=_ZERO,
            price=price,
            atr=atr_value,
            trade_levels=trade_levels,
            breaker_state=breaker_state,
            skip_reason=reason,
            reasoning=msg,
        )
