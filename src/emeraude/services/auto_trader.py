"""Periodic cycle wiring : data fetch -> decide -> tick + open (paper mode).

The :class:`AutoTrader` is the first end-to-end orchestrator-of-the-
orchestrator : on each :meth:`run_cycle` call it walks the full pipeline
the bot performs every cycle of its life :

1. **Fetch** — current ticker price + recent klines from Binance.
2. **Tick** — call :meth:`PositionTracker.tick` so any open position is
   auto-closed on stop / target hits *before* a new decision is taken.
3. **Breaker monitor** — :meth:`BreakerMonitor.check` auto-escalates
   the circuit breaker on streak / cumulative-loss conditions.
4. **Drift monitor** *(optional, doc 10 R3)* — when injected,
   :meth:`DriftMonitor.check` runs Page-Hinkley + ADWIN over the
   recent r_realized stream and escalates the breaker to ``WARNING``
   on detection. Sticky semantics : one audit event per drift
   regime, no spam.
5. **Risk monitor** *(optional, doc 10 R5)* — when injected,
   :meth:`RiskMonitor.check` evaluates the I5 criterion (max DD
   <= multiplier * |CVaR_99|) over the recent r_realized stream
   and escalates the breaker to ``WARNING`` on breach. Sticky
   semantics like the drift monitor.
6. **Decide** — call :meth:`Orchestrator.make_decision` with the
   current capital + klines.
7. **Open** — if the decision says ``should_trade`` *and* the tick did
   not just close a position this cycle, call
   :meth:`PositionTracker.open_position` with the levels from the
   decision and the dominant strategy. The "did not just close"
   guard is a one-cycle implicit cooldown (anti-flash-trade), looser
   than but coherent with the doc 04 ``cooldown_candles=6``.

This iteration delivers **paper mode** : the tracker records the
position in the local DB but **no real order is placed**. Anti-rule
A5 (no real money without double-tap + 5 s delay) blocks live trading
until the UI layer ships that confirmation flow ; doc 06 also requires
empirical paper-trading validation before flipping the toggle.

Architecture notes :

* All side-effecting dependencies (HTTP fetchers, capital provider,
  orchestrator, tracker) are injected — the unit tests run with
  pure Python stubs and never touch the network.
* :class:`CycleReport` aggregates everything that happened in one
  call so the future scheduler / UI can render a single payload
  per cycle and the audit trail can replay any cycle.
* Every cycle emits one ``AUTO_TRADER_CYCLE`` audit event (R9). On
  successful trades the per-position events
  (``POSITION_OPENED`` / ``POSITION_CLOSED``) come from the tracker
  itself, so the trail interleaves cycle-level and trade-level rows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.execution.breaker_monitor import (
    BreakerCheckResult,
    BreakerMonitor,
)
from emeraude.agent.execution.position_tracker import Position, PositionTracker
from emeraude.agent.perception.indicators import atr as _compute_atr
from emeraude.agent.perception.tradability import compute_tradability
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import audit, market_data
from emeraude.services.data_ingestion_guard import validate_and_audit_klines
from emeraude.services.gate_factories import (
    make_correlation_gate,
    make_microstructure_gate,
)
from emeraude.services.orchestrator import (
    CycleDecision,
    Orchestrator,
    TradeDirection,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from emeraude.infra.market_data import Kline
    from emeraude.services.drift_monitor import DriftCheckResult, DriftMonitor
    from emeraude.services.risk_monitor import RiskCheckResult, RiskMonitor


_DEFAULT_SYMBOL: Final[str] = "BTCUSDT"
_DEFAULT_INTERVAL: Final[str] = "1h"
# Regime detection needs ``ema_period (200) + slope_lookback (10) = 210``
# bars at minimum ; 250 leaves headroom for indicators / future analytics
# without forcing a second fetch.
_DEFAULT_KLINES_LIMIT: Final[int] = 250
# Doc 04 cold-start budget. Used when the caller does not inject a
# capital provider — never anticipates a real account, just a sane
# default for paper-trading sessions.
_DEFAULT_COLD_START_CAPITAL: Final[Decimal] = Decimal("20")

_AUDIT_EVENT: Final[str] = "AUTO_TRADER_CYCLE"


#: Mapping Binance interval string -> milliseconds. Used by the iter
#: #92 wiring to feed ``expected_dt_ms`` into the data-ingestion guard
#: so the doc 11 D3 ``TIME_GAP`` check can fire on cadence breaks.
#: Returning ``None`` for an unknown interval (e.g. a future
#: weekly bar or a custom string) intentionally skips the check
#: rather than raising — defensive default vs. misconfiguration.
_INTERVAL_TO_MS: Final[dict[str, int]] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def _interval_to_ms(interval: str) -> int | None:
    """Return the millisecond width of a Binance kline interval string.

    Returns ``None`` for unknown / custom intervals so the caller can
    skip the time-gap check rather than fabricate a wrong delta.
    """
    return _INTERVAL_TO_MS.get(interval)


#: Period used when the iter #92 wiring computes the ATR_N reference
#: feeding the D3 ``OUTLIER_RANGE`` check. The doc 11 row literally
#: says "Range > 50x ATR_30" but our :func:`indicators.atr` exposes
#: a 14-period default ; we keep 14 to match the rest of the
#: codebase (RSI / MACD / Stochastic also use 14) — the multiplier
#: 50 in :func:`check_bar_quality` is the actual outlier threshold,
#: not the period.
_INGESTION_ATR_PERIOD: Final[int] = 14


# ─── Public types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CycleReport:
    """Complete record of one :meth:`AutoTrader.run_cycle` invocation.

    Attributes:
        symbol: traded pair, e.g. ``"BTCUSDT"``.
        interval: kline interval, e.g. ``"1h"``.
        fetched_at: epoch-second timestamp of the cycle.
        current_price: ticker price used for the tick step.
        breaker_check: result of the pre-decision breaker monitor
            check (None when no monitor is wired).
        drift_check: result of the doc 10 R3 drift surveillance
            check. ``None`` when no monitor is wired (default
            backward-compat). When set, carries
            :class:`DriftCheckResult` with the per-cycle
            ``triggered`` / ``emitted_audit_event`` /
            ``breaker_escalated`` flags.
        risk_check: result of the doc 10 R5 tail-risk surveillance
            check. ``None`` when no monitor is wired (default
            backward-compat). When set, carries
            :class:`RiskCheckResult` with ``triggered`` /
            ``breach_this_call`` / ``max_drawdown`` / ``cvar_99``
            / ``threshold`` and the side-effect flags.
        decision: full :class:`CycleDecision` from the orchestrator.
            On a data-quality rejection the decision still runs but
            on an empty klines list, yielding ``SKIP_EMPTY_KLINES``.
        tick_outcome: position closed by the pre-decision tick (stop
            or target hit), or ``None``. The tick uses the live
            ``current_price``, NOT the klines, so it remains valid
            even when the kline series is rejected.
        opened_position: position newly opened this cycle, or ``None``
            if the orchestrator skipped or the cycle had a tick close.
        data_quality_rejected: ``True`` iff the doc 11 D3+D4 ingestion
            guard (iter #90/#91) flagged the freshly-fetched series
            as untrustworthy and forced the decision pipeline to skip.
            ``False`` on every clean cycle. Anti-règle A1 : surface
            the flag rather than hide a "skipped due to bad data"
            cycle behind a generic empty-klines reason.
        data_quality_rejection_reason: human-readable reason when
            ``data_quality_rejected`` is True (mirrors
            :class:`IngestionReport.rejection_reason`). Empty otherwise.
    """

    symbol: str
    interval: str
    fetched_at: int
    current_price: Decimal
    breaker_check: BreakerCheckResult | None
    drift_check: DriftCheckResult | None
    risk_check: RiskCheckResult | None
    decision: CycleDecision
    tick_outcome: Position | None
    opened_position: Position | None
    data_quality_rejected: bool = False
    data_quality_rejection_reason: str = ""


# ─── AutoTrader ─────────────────────────────────────────────────────────────


class AutoTrader:
    """Single-symbol paper-trading cycle (doc 05 §"BotMaitre cycle 60 min").

    Construct once at process start. Each call to :meth:`run_cycle`
    is a complete trip through fetch -> tick -> decide -> (open).

    Live order placement is **out of scope** for this iteration : the
    tracker writes a row, no exchange call is made. Real trading
    requires the A5 double-tap toggle from the UI layer (not yet
    shipped).
    """

    def __init__(
        self,
        *,
        symbol: str = _DEFAULT_SYMBOL,
        interval: str = _DEFAULT_INTERVAL,
        klines_limit: int = _DEFAULT_KLINES_LIMIT,
        capital_provider: Callable[[], Decimal] | None = None,
        orchestrator: Orchestrator | None = None,
        tracker: PositionTracker | None = None,
        breaker_monitor: BreakerMonitor | None = None,
        drift_monitor: DriftMonitor | None = None,
        risk_monitor: RiskMonitor | None = None,
        enable_tradability_gate: bool = False,
        correlation_symbols: list[str] | None = None,
        enable_microstructure_gate: bool = False,
        fetch_klines: Callable[[str, str, int], list[Kline]] | None = None,
        fetch_current_price: Callable[[str], Decimal] | None = None,
    ) -> None:
        """Wire the auto-trader with explicit dependencies.

        Args:
            symbol: trading pair (uppercase, Binance format).
            interval: kline width.
            klines_limit: number of bars to fetch ; default 250 covers
                the regime warmup (210) plus indicator headroom.
            capital_provider: callable returning the current USD
                capital for sizing. Default returns the doc 04 cold
                start of 20 USD ; production callers should inject a
                real provider that reads the account balance.
            orchestrator: decision component. Defaults to a fresh
                :class:`Orchestrator` with the doc-04 trio.
            tracker: position lifecycle component. Defaults to a fresh
                :class:`PositionTracker`.
            breaker_monitor: auto-escalation monitor that scans the
                position history and trips / warns the circuit breaker
                before the orchestrator runs. Defaults to a fresh
                :class:`BreakerMonitor` wired to the same tracker —
                a no-history cycle is a no-op so empty / unit-test
                scenarios remain unaffected. The monitor only
                escalates, never downgrades, so a manually-tripped
                breaker stays tripped.
            drift_monitor: optional doc 10 R3 drift surveillance.
                When ``None`` (default), no drift detection runs —
                strict backward-compat with pre-iter-#45 callers.
                When injected (typically as
                ``DriftMonitor(tracker=tracker)``), called after the
                breaker monitor and before the orchestrator decision ;
                the result is attached to :class:`CycleReport` and
                surfaced in the audit payload. Drift detection escalates
                the circuit breaker to ``WARNING`` (orchestrator halves
                sizing automatically) ; the operator manually resets
                via :func:`circuit_breaker.reset` after inspection.
            risk_monitor: optional doc 10 R5 tail-risk surveillance.
                When ``None`` (default), no breach detection runs —
                strict backward-compat with pre-iter-#47 callers.
                When injected (typically as
                ``RiskMonitor(tracker=tracker)``), called after the
                drift monitor and before the orchestrator decision.
                Evaluates the I5 criterion ``max DD <= 1.2 *
                |CVaR_99|`` and escalates the breaker to ``WARNING``
                on breach. Sticky semantics like the drift monitor.
            enable_tradability_gate: opt-in for the doc 10 R8
                meta-gate. When ``True`` AND ``orchestrator is None``,
                AutoTrader auto-builds the orchestrator with
                :func:`compute_tradability` as ``meta_gate``. Doc 10
                R8 default thresholds. Mutually exclusive with a
                custom ``orchestrator`` (raises ``ValueError`` if
                both are provided).
            correlation_symbols: opt-in for the doc 10 R7 correlation
                stress gate. When non-``None`` AND ``orchestrator is
                None``, AutoTrader auto-builds the orchestrator with
                :func:`make_correlation_gate(correlation_symbols)`.
                Pass at least 2 symbols (e.g. ``["BTCUSDT",
                "ETHUSDT", "SOLUSDT"]``). Mutually exclusive with
                a custom ``orchestrator``.
            enable_microstructure_gate: opt-in for the doc 10 R6
                spread + volume + flow gate. When ``True`` AND
                ``orchestrator is None``, AutoTrader auto-builds the
                orchestrator with
                :func:`make_microstructure_gate(self._symbol)` so
                the gate fetches book / trades / 1m klines for the
                same trading pair the auto-trader operates on.
                Mutually exclusive with a custom ``orchestrator``.
            fetch_klines: HTTP fetcher for klines, signature
                ``(symbol, interval, limit) -> list[Kline]``. Defaults
                to :func:`market_data.get_klines`.
            fetch_current_price: HTTP fetcher for ticker price,
                signature ``(symbol) -> Decimal``. Defaults to
                :func:`market_data.get_current_price`.
        """
        self._symbol = symbol
        self._interval = interval
        self._klines_limit = klines_limit
        self._capital_provider: Callable[[], Decimal] = (
            capital_provider if capital_provider is not None else _default_capital_provider
        )
        # Doc 10 R6/R7/R8 opt-in gates (iter #49). When the caller
        # passes their own orchestrator, gate-config flags would be
        # silently ignored — better to fail loudly so the conflict
        # is visible at construction time.
        gate_flags_set = (
            enable_tradability_gate or correlation_symbols is not None or enable_microstructure_gate
        )
        if orchestrator is not None and gate_flags_set:
            msg = (
                "gate auto-construction flags (enable_tradability_gate, "
                "correlation_symbols, enable_microstructure_gate) cannot "
                "be combined with a custom orchestrator ; pass the gates "
                "to your Orchestrator(...) constructor instead"
            )
            raise ValueError(msg)
        if orchestrator is not None:
            self._orchestrator: Orchestrator = orchestrator
        else:
            self._orchestrator = self._build_default_orchestrator(
                enable_tradability_gate=enable_tradability_gate,
                correlation_symbols=correlation_symbols,
                enable_microstructure_gate=enable_microstructure_gate,
            )
        self._tracker: PositionTracker = tracker if tracker is not None else PositionTracker()
        # Wire a default monitor against the same tracker — its check
        # is a single DB read so the cost is trivial. Tests that want
        # custom thresholds inject a configured instance.
        self._breaker_monitor: BreakerMonitor = (
            breaker_monitor
            if breaker_monitor is not None
            else BreakerMonitor(tracker=self._tracker)
        )
        # Optional (default None) so pre-iter-#45 callers see no
        # behavior change. When wired the cycle calls .check() right
        # after the breaker monitor and before the orchestrator.
        self._drift_monitor: DriftMonitor | None = drift_monitor
        # Optional (default None) for backward compat with pre-iter-#47
        # callers. Fires after the drift monitor on the same r_realized
        # stream — a breach is "the model under-predicted tail risk",
        # complementary to drift's "the distribution shifted".
        self._risk_monitor: RiskMonitor | None = risk_monitor
        self._fetch_klines: Callable[[str, str, int], list[Kline]] = (
            fetch_klines if fetch_klines is not None else market_data.get_klines
        )
        self._fetch_current_price: Callable[[str], Decimal] = (
            fetch_current_price
            if fetch_current_price is not None
            else market_data.get_current_price
        )

    @property
    def symbol(self) -> str:
        """The trading pair this auto-trader operates on."""
        return self._symbol

    @property
    def interval(self) -> str:
        """The kline interval this auto-trader operates on."""
        return self._interval

    # ─── Internals : default-orchestrator factory ───────────────────────────

    def _build_default_orchestrator(
        self,
        *,
        enable_tradability_gate: bool,
        correlation_symbols: list[str] | None,
        enable_microstructure_gate: bool,
    ) -> Orchestrator:
        """Build the default :class:`Orchestrator` with opt-in gates.

        Each gate is wired only when the corresponding flag asks for
        it. Defaults match doc 10 R6/R7/R8 (15 bps spread cap, 30 %
        volume floor, 0.55 directional taker ratio for R6 ; 0.8 mean
        correlation stress threshold for R7 ; 0.4 tradability floor
        for R8). Callers wanting custom thresholds construct their
        own :class:`Orchestrator` and inject it via the
        ``orchestrator`` parameter.
        """
        meta_gate = compute_tradability if enable_tradability_gate else None
        correlation_gate = (
            make_correlation_gate(correlation_symbols) if correlation_symbols is not None else None
        )
        microstructure_gate = (
            make_microstructure_gate(self._symbol) if enable_microstructure_gate else None
        )
        return Orchestrator(
            meta_gate=meta_gate,
            correlation_gate=correlation_gate,
            microstructure_gate=microstructure_gate,
        )

    # ─── Public API ─────────────────────────────────────────────────────────

    def run_cycle(self, *, now: int | None = None) -> CycleReport:
        """Run one full cycle and return the :class:`CycleReport`.

        Args:
            now: epoch-second timestamp. Defaults to ``time.time()``.
                Tests pass a fixed value for determinism.

        Returns:
            A :class:`CycleReport` summarizing what happened.
        """
        ts = now if now is not None else int(time.time())
        current_price = self._fetch_current_price(self._symbol)
        klines = self._fetch_klines(self._symbol, self._interval, self._klines_limit)

        # Step 0 : doc 11 D3+D4 ingestion guard (iter #91 wiring,
        # iter #92 fully active).
        # Validates the freshly-fetched series and emits the
        # ``DATA_INGESTION_COMPLETED`` audit event mandated by
        # doc 11 §5. On rejection, we still tick the tracker
        # (current_price is independent of klines and remains
        # trustworthy for SL/TP) but force the decision pipeline to
        # skip by passing an empty klines list — the existing
        # ``SKIP_EMPTY_KLINES`` short-circuit in the orchestrator
        # is the natural skip path.
        #
        # Iter #92 : we now compute the ATR reference and the
        # interval-ms delta so the ``OUTLIER_RANGE`` and
        # ``TIME_GAP`` checks fire (5/5 D3 checks active live).
        # Both are skipped silently by the guard when their input
        # is ``None`` (cold-start ATR not yet computable, or
        # unknown interval string), so passing them is always safe.
        atr_value = _compute_atr(klines, period=_INGESTION_ATR_PERIOD) if klines else None
        ingestion_report = validate_and_audit_klines(
            klines,
            symbol=self._symbol,
            interval=self._interval,
            expected_count=self._klines_limit,
            atr_value=atr_value,
            expected_dt_ms=_interval_to_ms(self._interval),
        )
        if ingestion_report.should_reject:
            klines = []

        # Step 1 : tick first so an existing position closes before
        # any new decision is taken on stale state.
        tick_outcome = self._tracker.tick(current_price=current_price, now=ts)

        # Step 2 : auto-escalate the circuit breaker if the post-tick
        # history reveals a streak / cumulative-loss situation. This
        # runs *after* tick so the just-closed trade is in the history,
        # and *before* decide so the orchestrator sees the up-to-date
        # breaker state on its own pre-decision check.
        breaker_check = self._breaker_monitor.check(now=ts)

        # Step 3 : doc 10 R3 drift surveillance (optional). Runs after
        # the breaker monitor so a streak-based escalation already has
        # the chance to fire, then drift sits on top : detection
        # escalates to WARNING (the breaker monitor never downgrades
        # so a previously-set TRIGGERED stays). The drift monitor
        # itself owns its sticky / no-duplicate semantics.
        drift_check: DriftCheckResult | None = None
        if self._drift_monitor is not None:
            drift_check = self._drift_monitor.check()

        # Step 4 : doc 10 R5 tail-risk surveillance (optional).
        # Complementary to drift : drift looks for distribution
        # *shift*, risk_monitor checks whether the realized DD
        # exceeds the multiplier * |CVaR_99| line. Independent
        # sticky state so an operator can reset one without losing
        # the other.
        risk_check: RiskCheckResult | None = None
        if self._risk_monitor is not None:
            risk_check = self._risk_monitor.check()

        # Step 5 : decision.
        capital = self._capital_provider()
        decision = self._orchestrator.make_decision(capital=capital, klines=klines)

        # Step 6 : open if all conditions hold.
        opened = self._maybe_open(
            decision=decision,
            tick_outcome=tick_outcome,
            ts=ts,
        )

        report = CycleReport(
            symbol=self._symbol,
            interval=self._interval,
            fetched_at=ts,
            current_price=current_price,
            breaker_check=breaker_check,
            drift_check=drift_check,
            risk_check=risk_check,
            decision=decision,
            tick_outcome=tick_outcome,
            opened_position=opened,
            data_quality_rejected=ingestion_report.should_reject,
            data_quality_rejection_reason=ingestion_report.rejection_reason,
        )

        audit.audit(_AUDIT_EVENT, _audit_payload(report))
        return report

    # ─── Internals ──────────────────────────────────────────────────────────

    def _maybe_open(
        self,
        *,
        decision: CycleDecision,
        tick_outcome: Position | None,
        ts: int,
    ) -> Position | None:
        """Open a position when the decision is green and no tick fired."""
        if not decision.should_trade:
            return None
        if tick_outcome is not None:
            # Implicit one-cycle cooldown : we just closed something,
            # do not flash-trade back in immediately. Looser than
            # doc 04's ``cooldown_candles = 6`` but coherent in spirit.
            return None
        if self._tracker.current_open() is not None:
            # A previous cycle's position is still in flight (price
            # has not yet hit stop or target). doc 04 max_positions=1
            # forbids stacking, so we wait for the next tick.
            return None
        if (
            decision.regime is None
            or decision.direction is None
            or decision.dominant_strategy is None
            or decision.trade_levels is None
        ):
            # Defensive : ``should_trade=True`` already guarantees
            # all four are set. Keeps mypy strict-happy.
            return None  # pragma: no cover

        side = Side.LONG if decision.direction is TradeDirection.LONG else Side.SHORT
        # Doc 10 R1 wiring : surface the ensemble confidence so the
        # tracker can persist it for the calibration loop. ``ensemble_vote``
        # is guaranteed non-None when ``should_trade`` is True (the
        # orchestrator's qualification gate runs upstream).
        confidence = (
            decision.ensemble_vote.confidence if decision.ensemble_vote is not None else None
        )
        return self._tracker.open_position(
            strategy=decision.dominant_strategy,
            regime=decision.regime,
            side=side,
            entry_price=decision.trade_levels.entry,
            stop=decision.trade_levels.stop,
            target=decision.trade_levels.target,
            quantity=decision.position_quantity,
            risk_per_unit=decision.trade_levels.risk_per_unit,
            confidence=confidence,
            opened_at=ts,
        )


# ─── Helpers ────────────────────────────────────────────────────────────────


def _default_capital_provider() -> Decimal:
    """Return the doc 04 cold-start capital (20 USD).

    Module-level rather than a lambda so the audit log can identify
    the default when the caller did not inject a provider.
    """
    return _DEFAULT_COLD_START_CAPITAL


def _audit_payload(report: CycleReport) -> dict[str, str | int | bool | None]:
    """Flatten a :class:`CycleReport` for the audit log.

    Decimal values are serialized as their string representation so
    the JSON column round-trips without precision loss.
    """
    decision = report.decision
    breaker = report.breaker_check
    drift = report.drift_check
    risk = report.risk_check
    payload: dict[str, str | int | bool | None] = {
        "symbol": report.symbol,
        "interval": report.interval,
        "fetched_at": report.fetched_at,
        "current_price": str(report.current_price),
        "breaker_state": (breaker.state_after.value if breaker is not None else None),
        "breaker_transitioned": (
            "true" if breaker is not None and breaker.transitioned else "false"
        ),
        "breaker_reason": (breaker.triggered_reason if breaker is not None else None),
        # Doc 10 R3 surveillance : surface drift summary in every cycle's
        # audit payload so an operator can spot the *first* triggered
        # cycle by sorting on AUTO_TRADER_CYCLE rows alone (the dedicated
        # DRIFT_DETECTED row from DriftMonitor is fired at most once).
        "drift_triggered": (drift.triggered if drift is not None else None),
        "drift_emitted_event": (drift.emitted_audit_event if drift is not None else None),
        "drift_breaker_escalated": (drift.breaker_escalated if drift is not None else None),
        # Doc 10 R5 surveillance : surface tail-risk breach summary on
        # every cycle. Same rationale as the drift fields — the dedicated
        # TAIL_RISK_BREACH row fires once, so operators rely on these
        # per-cycle flags for trending / dashboards.
        "risk_triggered": (risk.triggered if risk is not None else None),
        "risk_breach_this_call": (risk.breach_this_call if risk is not None else None),
        "risk_emitted_event": (risk.emitted_audit_event if risk is not None else None),
        "risk_breaker_escalated": (risk.breaker_escalated if risk is not None else None),
        "should_trade": "true" if decision.should_trade else "false",
        "skip_reason": decision.skip_reason,
        "regime": decision.regime.value if decision.regime is not None else None,
        "direction": decision.direction.value if decision.direction is not None else None,
        "dominant_strategy": decision.dominant_strategy,
        "tick_closed_id": report.tick_outcome.id if report.tick_outcome is not None else None,
        "opened_id": report.opened_position.id if report.opened_position is not None else None,
    }
    return payload
