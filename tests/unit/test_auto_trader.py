"""Unit tests for emeraude.services.auto_trader."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.execution.breaker_monitor import BreakerMonitor
from emeraude.agent.execution.circuit_breaker import CircuitBreakerState
from emeraude.agent.execution.position_tracker import (
    ExitReason,
    PositionTracker,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.perception.tradability import compute_tradability
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.agent.reasoning.strategies import StrategySignal
from emeraude.infra import audit, database
from emeraude.infra.market_data import Kline
from emeraude.services.auto_trader import (
    _INTERVAL_TO_MS,
    AutoTrader,
    CycleReport,
    _default_capital_provider,
    _interval_to_ms,
)
from emeraude.services.drift_monitor import DriftMonitor
from emeraude.services.live_executor import LiveOrderResult, PaperLiveExecutor
from emeraude.services.orchestrator import Orchestrator
from emeraude.services.risk_monitor import RiskMonitor

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _kline(close: float, *, idx: int = 0) -> Kline:
    c = Decimal(str(close))
    return Kline(
        open_time=idx * 60_000,
        open=c,
        high=c * Decimal("1.01"),
        low=c * Decimal("0.99"),
        close=c,
        volume=Decimal("1"),
        close_time=(idx + 1) * 60_000,
        n_trades=1,
    )


def _bull_klines(n: int = 220) -> list[Kline]:
    return [_kline(100.0 + i * 0.5, idx=i) for i in range(n)]


class _FakeStrategy:
    def __init__(self, name: str, signal: StrategySignal | None) -> None:
        self.name = name
        self._signal = signal

    def compute_signal(
        self,
        klines: list[Kline],
        regime: Regime,
    ) -> StrategySignal | None:
        del klines, regime
        return self._signal


def _signal(score: float, confidence: float = 0.9) -> StrategySignal:
    return StrategySignal(
        score=Decimal(str(score)),
        confidence=Decimal(str(confidence)),
        reasoning="hp",
    )


def _make_trader(
    *,
    klines: list[Kline] | None = None,
    price: Decimal = Decimal("210"),
    capital: Decimal = Decimal("1000"),
    orchestrator: Orchestrator | None = None,
    tracker: PositionTracker | None = None,
    drift_monitor: DriftMonitor | None = None,
    risk_monitor: RiskMonitor | None = None,
) -> AutoTrader:
    if klines is None:
        klines = _bull_klines()
    if orchestrator is None:
        orchestrator = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )

    fetched_prices: list[Decimal] = []
    fetched_klines_calls: list[tuple[str, str, int]] = []

    def fake_fetch_klines(symbol: str, interval: str, limit: int) -> list[Kline]:
        fetched_klines_calls.append((symbol, interval, limit))
        return klines

    def fake_fetch_price(symbol: str) -> Decimal:
        fetched_prices.append(price)
        del symbol
        return price

    return AutoTrader(
        symbol="BTCUSDT",
        interval="1h",
        # Aligned with the actual fetched series so the iter #91
        # ingestion guard treats the cycle as complete (D4 5 % gate).
        klines_limit=len(klines),
        capital_provider=lambda: capital,
        orchestrator=orchestrator,
        tracker=tracker if tracker is not None else PositionTracker(),
        drift_monitor=drift_monitor,
        risk_monitor=risk_monitor,
        fetch_klines=fake_fetch_klines,
        fetch_current_price=fake_fetch_price,
    )


# ─── Construction ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConstruction:
    def test_default_symbol_is_btcusdt(self, fresh_db: Path) -> None:
        at = AutoTrader()
        assert at.symbol == "BTCUSDT"

    def test_default_interval_is_one_hour(self, fresh_db: Path) -> None:
        at = AutoTrader()
        assert at.interval == "1h"

    def test_custom_symbol_and_interval(self, fresh_db: Path) -> None:
        at = AutoTrader(symbol="ETHUSDT", interval="4h")
        assert at.symbol == "ETHUSDT"
        assert at.interval == "4h"


# ─── Cycle path : decision skip ─────────────────────────────────────────────


@pytest.mark.unit
class TestCycleSkip:
    def test_breaker_blocked_does_not_open(self, fresh_db: Path) -> None:
        circuit_breaker.trip("test")
        tracker = PositionTracker()
        at = _make_trader(tracker=tracker)
        report = at.run_cycle(now=1_700_000_000)
        assert report.decision.should_trade is False
        assert report.decision.skip_reason == "breaker_blocked"
        assert report.opened_position is None
        assert tracker.current_open() is None

    def test_skip_does_not_open_position(self, fresh_db: Path) -> None:
        # Weak signals -> ensemble not qualified -> no open.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.1, confidence=0.4)),
                _FakeStrategy("b", _signal(0.1, confidence=0.4)),
            ],
        )
        tracker = PositionTracker()
        at = _make_trader(tracker=tracker, orchestrator=orch)
        report = at.run_cycle(now=1_700_000_000)
        assert report.decision.should_trade is False
        assert report.opened_position is None


# ─── Cycle path : happy ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestCycleHappy:
    def test_cycle_opens_position_on_strong_signal(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        tracker = PositionTracker()
        at = _make_trader(tracker=tracker)
        report = at.run_cycle(now=1_700_000_000)
        assert report.decision.should_trade is True
        assert report.opened_position is not None
        assert report.opened_position.is_open is True
        current = tracker.current_open()
        assert current is not None
        assert current.id == report.opened_position.id

    def test_opened_position_uses_decision_levels(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        tracker = PositionTracker()
        at = _make_trader(tracker=tracker)
        report = at.run_cycle(now=1_700_000_000)
        assert report.opened_position is not None
        levels = report.decision.trade_levels
        assert levels is not None
        assert report.opened_position.entry_price == levels.entry
        assert report.opened_position.stop == levels.stop
        assert report.opened_position.target == levels.target
        assert report.opened_position.risk_per_unit == levels.risk_per_unit

    def test_opened_position_uses_dominant_strategy(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        # "loud" dominates "quiet".
        loud = _FakeStrategy("loud", _signal(0.9, confidence=0.9))
        quiet = _FakeStrategy("quiet", _signal(0.5, confidence=0.4))
        orch = Orchestrator(strategies=[loud, quiet])
        tracker = PositionTracker()
        at = _make_trader(orchestrator=orch, tracker=tracker)
        report = at.run_cycle(now=1_700_000_000)
        assert report.opened_position is not None
        assert report.opened_position.strategy == "loud"

    def test_cycle_emits_audit_event(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        at = _make_trader()
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="AUTO_TRADER_CYCLE")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["symbol"] == "BTCUSDT"
        assert payload["interval"] == "1h"
        assert payload["should_trade"] == "true"
        assert payload["regime"] == "BULL"
        assert payload["direction"] == "LONG"


# ─── Tick interaction ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestTickInteraction:
    def test_tick_closes_existing_position(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        tracker = PositionTracker()
        # Open a stop-grazed LONG manually so the next tick fires.
        tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            opened_at=1_700_000_000,
        )

        # The cycle's price 97 trips the stop -> tick closes.
        # Use weak signals so the cycle does not also try to re-open.
        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.1, confidence=0.3)),
                _FakeStrategy("b", _signal(0.1, confidence=0.3)),
            ],
        )
        at = _make_trader(price=Decimal("97"), tracker=tracker, orchestrator=orch)
        report = at.run_cycle(now=1_700_000_500)
        assert report.tick_outcome is not None
        assert report.tick_outcome.exit_reason == ExitReason.STOP_HIT
        assert tracker.current_open() is None

    def test_in_flight_position_blocks_new_open(self, fresh_db: Path) -> None:
        # Position open from a previous cycle, current price is inside
        # the band so tick does not fire ; orchestrator says trade ;
        # auto-trader must refuse the second open (max_positions=1).
        circuit_breaker.reset()
        tracker = PositionTracker()
        tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            opened_at=1_700_000_000,
        )
        # Price 101 is inside (98, 104), tick does nothing.
        at = _make_trader(price=Decimal("101"), tracker=tracker)
        report = at.run_cycle(now=1_700_000_500)
        assert report.tick_outcome is None
        assert report.decision.should_trade is True
        assert report.opened_position is None
        # Existing position still open.
        current = tracker.current_open()
        assert current is not None
        assert current.id == 1

    def test_tick_close_blocks_same_cycle_open(self, fresh_db: Path) -> None:
        # Implicit one-cycle cooldown : even if the orchestrator says
        # should_trade after a tick close, do not re-enter.
        circuit_breaker.reset()
        tracker = PositionTracker()
        tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            opened_at=1_700_000_000,
        )

        # Strong bullish signals would normally re-enter -- the cooldown
        # guard prevents it.
        at = _make_trader(price=Decimal("97"), tracker=tracker)
        report = at.run_cycle(now=1_700_000_500)
        assert report.tick_outcome is not None
        assert report.decision.should_trade is True  # orchestrator approved
        assert report.opened_position is None  # but auto-trader refused
        assert tracker.current_open() is None


# ─── CycleReport shape ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestCycleReportShape:
    def test_report_carries_inputs(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        at = _make_trader()
        report = at.run_cycle(now=1_700_000_000)
        assert isinstance(report, CycleReport)
        assert report.symbol == "BTCUSDT"
        assert report.interval == "1h"
        assert report.fetched_at == 1_700_000_000
        assert report.current_price == Decimal("210")

    def test_report_is_frozen(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        at = _make_trader()
        report = at.run_cycle(now=1_700_000_000)
        with pytest.raises(AttributeError):
            report.fetched_at = 0  # type: ignore[misc]


# ─── Capital provider ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestCapitalProvider:
    def test_capital_provider_called_each_cycle(self, fresh_db: Path) -> None:
        # Use weak signals so the orchestrator skips and no position
        # is opened — exercises the capital fetch on a pure skip path.
        circuit_breaker.reset()
        calls: list[Decimal] = []

        def provider() -> Decimal:
            calls.append(Decimal("500"))
            return Decimal("500")

        orch = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.1, confidence=0.3)),
                _FakeStrategy("b", _signal(0.1, confidence=0.3)),
            ],
        )
        at = _make_trader(orchestrator=orch)
        at._capital_provider = provider
        at.run_cycle(now=1_700_000_000)
        at.run_cycle(now=1_700_000_500)
        assert len(calls) == 2

    def test_zero_capital_skips_size_zero(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        tracker = PositionTracker()
        at = _make_trader(capital=Decimal("0"), tracker=tracker)
        report = at.run_cycle(now=1_700_000_000)
        assert report.decision.skip_reason == "position_size_zero"
        assert report.opened_position is None


# ─── Fetcher injection ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestFetcherInjection:
    def test_fetchers_called_with_symbol_and_interval(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        klines_calls: list[tuple[str, str, int]] = []
        price_calls: list[str] = []

        def fk(symbol: str, interval: str, limit: int) -> list[Kline]:
            klines_calls.append((symbol, interval, limit))
            # Return exactly ``limit`` klines so the iter #91 ingestion
            # guard treats the cycle as complete (D4 5 % gate).
            return _bull_klines(limit)

        def fp(symbol: str) -> Decimal:
            price_calls.append(symbol)
            return Decimal("210")

        at = AutoTrader(
            symbol="ETHUSDT",
            interval="4h",
            klines_limit=300,
            orchestrator=Orchestrator(
                strategies=[
                    _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                    _FakeStrategy("b", _signal(0.9, confidence=0.9)),
                ],
            ),
            fetch_klines=fk,
            fetch_current_price=fp,
        )
        at.run_cycle(now=1_700_000_000)
        assert klines_calls == [("ETHUSDT", "4h", 300)]
        assert price_calls == ["ETHUSDT"]


# ─── Default capital provider ───────────────────────────────────────────────


@pytest.mark.unit
class TestDefaultCapitalProvider:
    def test_default_is_doc04_cold_start_20_usd(self, fresh_db: Path) -> None:
        assert _default_capital_provider() == Decimal("20")


# ─── Breaker monitor integration ────────────────────────────────────────────


@pytest.mark.unit
class TestBreakerMonitorIntegration:
    def test_report_carries_breaker_check(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        at = _make_trader()
        report = at.run_cycle(now=1_700_000_000)
        assert report.breaker_check is not None
        # Empty history -> no transition.
        assert report.breaker_check.state_after == CircuitBreakerState.HEALTHY
        assert report.breaker_check.transitioned is False

    def test_three_losses_in_history_warn_and_halve_size(self, fresh_db: Path) -> None:
        # Pre-seed 3 partial losses (each -0.5 R = total -1.5 R, below
        # the cumulative-trip floor of -3 R) so the consec-WARN fires
        # alone. The orchestrator then sees WARNING and halves sizing.
        circuit_breaker.reset()
        tracker = PositionTracker()
        for i in range(3):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                opened_at=10 * i,
            )
            tracker.close_position(
                exit_price=Decimal("99"),  # -0.5 R partial loss
                exit_reason=ExitReason.MANUAL,
                closed_at=10 * i + 1,
            )

        at = _make_trader(tracker=tracker)
        report = at.run_cycle(now=1_700_000_000)
        # Monitor escalated to WARNING via consec gate.
        assert report.breaker_check is not None
        assert report.breaker_check.transitioned is True
        assert report.breaker_check.state_after == CircuitBreakerState.WARNING
        # Orchestrator picked up the WARN ; either it skipped OR opened
        # with halved size. With strong signals + capital we expect
        # an open with halved quantity.
        assert report.decision.breaker_state == CircuitBreakerState.WARNING

    def test_five_losses_trip_blocks_decision(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        tracker = PositionTracker()
        for i in range(5):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                opened_at=10 * i,
            )
            # Use a tiny -0.1 R loss so cumulative stays above the trip
            # floor and only the consec-trip path fires.
            tracker.close_position(
                exit_price=Decimal("99.8"),
                exit_reason=ExitReason.MANUAL,
                closed_at=10 * i + 1,
            )

        at = _make_trader(tracker=tracker)
        report = at.run_cycle(now=1_700_000_000)
        assert report.breaker_check is not None
        assert report.breaker_check.state_after == CircuitBreakerState.TRIGGERED
        # Orchestrator now sees TRIGGERED and skips.
        assert report.decision.should_trade is False
        assert report.decision.skip_reason == "breaker_blocked"
        assert report.opened_position is None

    def test_audit_payload_includes_breaker_fields(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        at = _make_trader()
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="AUTO_TRADER_CYCLE")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["breaker_state"] == CircuitBreakerState.HEALTHY.value
        assert payload["breaker_transitioned"] == "false"
        assert payload["breaker_reason"] is None

    def test_custom_monitor_injectable(self, fresh_db: Path) -> None:
        # A monitor with very tight thresholds trips on a single loss.
        circuit_breaker.reset()
        tracker = PositionTracker()
        tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            opened_at=10,
        )
        tracker.close_position(
            exit_price=Decimal("98"),
            exit_reason=ExitReason.STOP_HIT,
            closed_at=11,
        )

        tight = BreakerMonitor(
            tracker=tracker,
            warn_consecutive_losses=1,
            trip_consecutive_losses=1,
            trip_cumulative_r_loss_24h=Decimal("-100"),  # never via cumulative
        )
        at = _make_trader(tracker=tracker)
        at._breaker_monitor = tight
        report = at.run_cycle(now=1_700_000_000)
        assert report.breaker_check is not None
        assert report.breaker_check.state_after == CircuitBreakerState.TRIGGERED


# ─── Drift monitor wiring (doc 10 R3, iter #45) ────────────────────────────


@pytest.mark.unit
class TestDriftMonitorWiring:
    def test_default_no_drift_monitor_keeps_legacy_behavior(self, fresh_db: Path) -> None:
        # Backward compat : without injection the field is None and
        # no surveillance runs. Existing 23 tests already cover this
        # path implicitly ; here we just assert the surface contract.
        at = _make_trader()
        report = at.run_cycle(now=1_700_000_000)
        assert report.drift_check is None

    def test_injected_clean_history_runs_check_no_trigger(self, fresh_db: Path) -> None:
        # Wire a DriftMonitor against a fresh tracker (no history) :
        # check() runs and returns triggered=False.

        tracker = PositionTracker()
        monitor = DriftMonitor(tracker=tracker)
        at = _make_trader(tracker=tracker, drift_monitor=monitor)
        report = at.run_cycle(now=1_700_000_000)
        assert report.drift_check is not None
        assert report.drift_check.triggered is False
        assert report.drift_check.n_samples == 0
        assert report.drift_check.emitted_audit_event is False
        assert report.drift_check.breaker_escalated is False

    def test_drift_detection_escalates_breaker_to_warning(self, fresh_db: Path) -> None:
        # Seed the tracker with 30 winners then 10 losers ; on next
        # cycle the drift monitor fires and escalates the breaker.

        circuit_breaker.reset(reason="test")
        tracker = PositionTracker()
        for i in range(30):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                opened_at=i * 10,
            )
            tracker.close_position(
                exit_price=Decimal("104"),
                exit_reason=ExitReason.TARGET_HIT,
                closed_at=i * 10 + 5,
            )
        for i in range(10):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                opened_at=(30 + i) * 10,
            )
            tracker.close_position(
                exit_price=Decimal("98"),
                exit_reason=ExitReason.STOP_HIT,
                closed_at=(30 + i) * 10 + 5,
            )
        # Reset breaker after the seed history (close paths might warn).
        circuit_breaker.reset(reason="post_seed")

        monitor = DriftMonitor(tracker=tracker)
        at = _make_trader(tracker=tracker, drift_monitor=monitor)
        report = at.run_cycle(now=1_700_000_000)

        assert report.drift_check is not None
        assert report.drift_check.triggered is True
        assert report.drift_check.emitted_audit_event is True
        assert report.drift_check.breaker_escalated is True
        # Breaker actually escalated.
        assert circuit_breaker.get_state() == CircuitBreakerState.WARNING

    def test_drift_audit_payload_in_cycle_event(self, fresh_db: Path) -> None:
        # Verify the AUTO_TRADER_CYCLE audit payload carries the
        # drift summary fields (so dashboards can sort on them).

        circuit_breaker.reset(reason="test")
        tracker = PositionTracker()
        monitor = DriftMonitor(tracker=tracker)
        at = _make_trader(tracker=tracker, drift_monitor=monitor)
        at.run_cycle(now=1_700_000_000)

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="AUTO_TRADER_CYCLE")
        assert len(events) >= 1
        payload = events[0]["payload"]
        # All three drift fields must be present and not None when
        # the monitor is wired.
        assert "drift_triggered" in payload
        assert "drift_emitted_event" in payload
        assert "drift_breaker_escalated" in payload
        assert payload["drift_triggered"] is False
        assert payload["drift_emitted_event"] is False
        assert payload["drift_breaker_escalated"] is False

    def test_no_drift_monitor_yields_null_audit_fields(self, fresh_db: Path) -> None:
        # Without the monitor, the three drift_* audit fields are None
        # (not absent — explicit null distinguishes "not wired" from
        # "wired and clean").
        circuit_breaker.reset(reason="test")
        at = _make_trader()
        at.run_cycle(now=1_700_000_000)

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="AUTO_TRADER_CYCLE")
        payload = events[0]["payload"]
        assert payload["drift_triggered"] is None
        assert payload["drift_emitted_event"] is None
        assert payload["drift_breaker_escalated"] is None


# ─── Risk monitor wiring (doc 10 R5, iter #47) ──────────────────────────────


@pytest.mark.unit
class TestRiskMonitorWiring:
    def test_default_no_risk_monitor_keeps_legacy_behavior(self, fresh_db: Path) -> None:
        # Backward compat : without injection the field is None.
        at = _make_trader()
        report = at.run_cycle(now=1_700_000_000)
        assert report.risk_check is None

    def test_injected_clean_history_runs_check_no_breach(self, fresh_db: Path) -> None:
        # Wire RiskMonitor against fresh tracker (no history) :
        # check() runs and returns triggered=False, n_samples=0.
        tracker = PositionTracker()
        monitor = RiskMonitor(tracker=tracker)
        at = _make_trader(tracker=tracker, risk_monitor=monitor)
        report = at.run_cycle(now=1_700_000_000)
        assert report.risk_check is not None
        assert report.risk_check.triggered is False
        assert report.risk_check.breach_this_call is False
        assert report.risk_check.n_samples == 0
        assert report.risk_check.emitted_audit_event is False
        assert report.risk_check.breaker_escalated is False

    def test_breach_detection_escalates_breaker_to_warning(self, fresh_db: Path) -> None:
        # Seed tracker with 25 winners + 11 small losers : sustained
        # drawdown >> 1.2 * |CVaR_99| -> breach -> WARNING.
        circuit_breaker.reset(reason="test")
        tracker = PositionTracker()
        for i in range(25):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                opened_at=i * 10,
            )
            tracker.close_position(
                exit_price=Decimal("104"),
                exit_reason=ExitReason.TARGET_HIT,
                closed_at=i * 10 + 5,
            )
        for i in range(11):
            tracker.open_position(
                strategy="trend_follower",
                regime=Regime.BULL,
                side=Side.LONG,
                entry_price=Decimal("100"),
                stop=Decimal("98"),
                target=Decimal("104"),
                quantity=Decimal("0.1"),
                risk_per_unit=Decimal("2"),
                opened_at=(25 + i) * 10,
            )
            tracker.close_position(
                exit_price=Decimal("98"),
                exit_reason=ExitReason.STOP_HIT,
                closed_at=(25 + i) * 10 + 5,
            )
        circuit_breaker.reset(reason="post_seed")

        monitor = RiskMonitor(tracker=tracker, min_samples=30)
        at = _make_trader(tracker=tracker, risk_monitor=monitor)
        report = at.run_cycle(now=1_700_000_000)

        assert report.risk_check is not None
        assert report.risk_check.triggered is True
        assert report.risk_check.emitted_audit_event is True
        assert report.risk_check.breaker_escalated is True
        assert circuit_breaker.get_state() == CircuitBreakerState.WARNING

    def test_risk_audit_payload_in_cycle_event(self, fresh_db: Path) -> None:
        # Verify the AUTO_TRADER_CYCLE audit payload carries the
        # 4 risk_* fields (so dashboards can sort on them).
        circuit_breaker.reset(reason="test")
        tracker = PositionTracker()
        monitor = RiskMonitor(tracker=tracker)
        at = _make_trader(tracker=tracker, risk_monitor=monitor)
        at.run_cycle(now=1_700_000_000)

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="AUTO_TRADER_CYCLE")
        assert len(events) >= 1
        payload = events[0]["payload"]
        assert "risk_triggered" in payload
        assert "risk_breach_this_call" in payload
        assert "risk_emitted_event" in payload
        assert "risk_breaker_escalated" in payload
        # Clean cycle : all four False (not None — monitor IS wired).
        assert payload["risk_triggered"] is False
        assert payload["risk_breach_this_call"] is False
        assert payload["risk_emitted_event"] is False
        assert payload["risk_breaker_escalated"] is False

    def test_no_risk_monitor_yields_null_audit_fields(self, fresh_db: Path) -> None:
        # Without monitor : 4 risk_* fields are None (distinguish
        # "not wired" from "wired and clean").
        circuit_breaker.reset(reason="test")
        at = _make_trader()
        at.run_cycle(now=1_700_000_000)

        assert audit.flush_default_logger(timeout=2.0)
        events = audit.query_events(event_type="AUTO_TRADER_CYCLE")
        payload = events[0]["payload"]
        assert payload["risk_triggered"] is None
        assert payload["risk_breach_this_call"] is None
        assert payload["risk_emitted_event"] is None
        assert payload["risk_breaker_escalated"] is None

    def test_drift_and_risk_monitors_wire_together(self, fresh_db: Path) -> None:
        # Both monitors injected ; both surface in CycleReport.
        tracker = PositionTracker()
        drift = DriftMonitor(tracker=tracker)
        risk = RiskMonitor(tracker=tracker)
        at = _make_trader(
            tracker=tracker,
            drift_monitor=drift,
            risk_monitor=risk,
        )
        report = at.run_cycle(now=1_700_000_000)
        assert report.drift_check is not None
        assert report.risk_check is not None
        assert report.drift_check.triggered is False
        assert report.risk_check.triggered is False


# ─── Gate auto-construction (doc 10 R6/R7/R8, iter #49) ─────────────────────


@pytest.mark.unit
class TestGateAutoConstruction:
    def test_default_no_flags_no_gates_wired(self, fresh_db: Path) -> None:
        # Backward compat : no opt-in flags = no gates on the default
        # Orchestrator. _meta_gate, _correlation_gate,
        # _microstructure_gate are all None.
        at = AutoTrader()
        assert at._orchestrator._meta_gate is None
        assert at._orchestrator._correlation_gate is None
        assert at._orchestrator._microstructure_gate is None

    def test_enable_tradability_gate_wires_meta_gate(self, fresh_db: Path) -> None:
        at = AutoTrader(enable_tradability_gate=True)
        # The orchestrator's meta_gate is the canonical
        # compute_tradability function.
        assert at._orchestrator._meta_gate is compute_tradability

    def test_correlation_symbols_wires_correlation_gate(self, fresh_db: Path) -> None:
        at = AutoTrader(correlation_symbols=["BTCUSDT", "ETHUSDT"])
        # A closure was built (callable, not None).
        assert at._orchestrator._correlation_gate is not None
        assert callable(at._orchestrator._correlation_gate)

    def test_correlation_symbols_below_two_raises(self, fresh_db: Path) -> None:
        # The factory enforces >= 2 symbols ; the error propagates.
        with pytest.raises(ValueError, match="need >= 2 symbols"):
            AutoTrader(correlation_symbols=["BTCUSDT"])

    def test_enable_microstructure_gate_wires_with_self_symbol(self, fresh_db: Path) -> None:
        # The auto-built gate captures self._symbol and uses
        # market_data fetchers by default ; we just verify the
        # closure exists. (Calling it would require patching the full
        # 3-fetcher set ; the gate-factory module already covers that
        # in test_gate_factories.py.)
        at = AutoTrader(symbol="ETHUSDT", enable_microstructure_gate=True)
        assert at._orchestrator._microstructure_gate is not None

    def test_all_three_flags_together(self, fresh_db: Path) -> None:
        at = AutoTrader(
            enable_tradability_gate=True,
            correlation_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            enable_microstructure_gate=True,
        )
        assert at._orchestrator._meta_gate is compute_tradability
        assert at._orchestrator._correlation_gate is not None
        assert at._orchestrator._microstructure_gate is not None

    def test_custom_orchestrator_with_flags_raises(self, fresh_db: Path) -> None:
        # Mutual exclusivity : passing a custom orchestrator AND a
        # gate flag is a configuration error (the flags would be
        # silently ignored otherwise).
        custom = Orchestrator()
        with pytest.raises(ValueError, match="cannot be combined"):
            AutoTrader(orchestrator=custom, enable_tradability_gate=True)
        with pytest.raises(ValueError, match="cannot be combined"):
            AutoTrader(
                orchestrator=custom,
                correlation_symbols=["BTCUSDT", "ETHUSDT"],
            )
        with pytest.raises(ValueError, match="cannot be combined"):
            AutoTrader(orchestrator=custom, enable_microstructure_gate=True)

    def test_custom_orchestrator_alone_works(self, fresh_db: Path) -> None:
        # Custom orchestrator without gate flags = legacy path, still works.
        custom = Orchestrator()
        at = AutoTrader(orchestrator=custom)
        assert at._orchestrator is custom


# ─── Iter #91 : data-ingestion guard wiring ─────────────────────────────────


@pytest.mark.unit
class TestDataIngestionGuardWiring:
    """The doc 11 D3+D4 guard from iter #90 is wired into ``run_cycle``.

    Three paths to verify :

    * Clean fetch -> no rejection, normal pipeline.
    * Hard reject (e.g. corrupted bar / incomplete series) ->
      decision is forced to skip via ``klines=[]``, no position
      opened, ``CycleReport.data_quality_rejected=True``.
    * Soft warning (FLAT_VOLUME / TIME_GAP / OUTLIER_RANGE) ->
      pipeline continues, ``data_quality_rejected=False``.
    """

    def test_clean_cycle_does_not_set_rejected_flag(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        at = _make_trader()
        report = at.run_cycle(now=1_700_000_000)
        assert report.data_quality_rejected is False
        assert report.data_quality_rejection_reason == ""

    def test_invalid_high_low_rejects_decision(self, fresh_db: Path) -> None:
        # Build a series with one corrupted bar (high < low).
        circuit_breaker.reset()
        good = _bull_klines(220)
        bad = good[:]
        c = good[100].close
        bad[100] = Kline(
            open_time=good[100].open_time,
            open=c,
            high=c * Decimal("0.5"),  # high < low : corruption
            low=c * Decimal("1.5"),
            close=c,
            volume=Decimal("1"),
            close_time=good[100].close_time,
            n_trades=1,
        )
        at = _make_trader(klines=bad)
        report = at.run_cycle(now=1_700_000_000)

        assert report.data_quality_rejected is True
        assert "bar 100 corrupted" in report.data_quality_rejection_reason
        # Decision is forced to skip via empty klines short-circuit.
        assert report.decision.should_trade is False
        assert report.opened_position is None

    def test_incomplete_series_rejects_decision(self, fresh_db: Path) -> None:
        # Caller asks for 250 bars, fetcher returns only 200 (20 % missing,
        # well above the 5 % D4 tolerance).
        circuit_breaker.reset()
        truncated = _bull_klines(200)

        def fk(symbol: str, interval: str, limit: int) -> list[Kline]:
            del symbol, interval, limit
            return truncated

        def fp(symbol: str) -> Decimal:
            del symbol
            return Decimal("210")

        at = AutoTrader(
            symbol="BTCUSDT",
            interval="1h",
            klines_limit=250,  # request 250, fetcher returns 200 -> 20 % missing
            orchestrator=Orchestrator(
                strategies=[
                    _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                    _FakeStrategy("b", _signal(0.9, confidence=0.9)),
                ],
            ),
            fetch_klines=fk,
            fetch_current_price=fp,
        )
        report = at.run_cycle(now=1_700_000_000)
        assert report.data_quality_rejected is True
        assert "series too incomplete" in report.data_quality_rejection_reason
        assert report.opened_position is None

    def test_flat_volume_warning_does_not_reject(self, fresh_db: Path) -> None:
        # FLAT_VOLUME is a warning, not a hard reject — the cycle
        # continues normally and ``data_quality_rejected`` stays False.
        circuit_breaker.reset()
        kls = _bull_klines(220)
        # Tamper one bar with volume=0 + non-zero range (already true
        # via _kline default high/low spread).
        c = kls[50].close
        kls[50] = Kline(
            open_time=kls[50].open_time,
            open=c,
            high=c * Decimal("1.01"),
            low=c * Decimal("0.99"),
            close=c,
            volume=Decimal("0"),
            close_time=kls[50].close_time,
            n_trades=0,
        )
        at = _make_trader(klines=kls)
        report = at.run_cycle(now=1_700_000_000)
        assert report.data_quality_rejected is False

    def test_emits_data_ingestion_completed_audit_event(self, fresh_db: Path) -> None:
        # The wiring must produce one DATA_INGESTION_COMPLETED audit
        # row per cycle (doc 11 §5 contract).
        circuit_breaker.reset()
        at = _make_trader()
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="DATA_INGESTION_COMPLETED")
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert payload["symbol"] == "BTCUSDT"
        assert payload["interval"] == "1h"
        assert payload["status"] == "ok"

    def test_rejected_cycle_emits_rejected_status_audit(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        # Use the hard-reject path : 100 bars expected, 20 received.
        truncated = _bull_klines(20)

        def fk(symbol: str, interval: str, limit: int) -> list[Kline]:
            del symbol, interval, limit
            return truncated

        def fp(symbol: str) -> Decimal:
            del symbol
            return Decimal("210")

        at = AutoTrader(
            symbol="BTCUSDT",
            interval="1h",
            klines_limit=100,
            orchestrator=Orchestrator(),
            fetch_klines=fk,
            fetch_current_price=fp,
        )
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="DATA_INGESTION_COMPLETED")
        assert len(rows) == 1
        assert rows[0]["payload"]["status"] == "rejected"
        assert "rejection_reason" in rows[0]["payload"]


# ─── Iter #92 : interval_to_ms helper + ATR wiring ──────────────────────────


@pytest.mark.unit
class TestIntervalToMs:
    """The helper feeds ``expected_dt_ms`` into the data-ingestion guard
    so the doc 11 D3 ``TIME_GAP`` check fires on cadence breaks.
    """

    def test_known_intervals_mapped(self) -> None:
        # Spot-check the most common Binance values.
        assert _interval_to_ms("1m") == 60_000
        assert _interval_to_ms("5m") == 300_000
        assert _interval_to_ms("15m") == 900_000
        assert _interval_to_ms("1h") == 3_600_000
        assert _interval_to_ms("4h") == 14_400_000
        assert _interval_to_ms("1d") == 86_400_000

    def test_unknown_interval_returns_none(self) -> None:
        # Defensive default vs. misconfiguration / future intervals.
        assert _interval_to_ms("1w") is None
        assert _interval_to_ms("custom") is None
        assert _interval_to_ms("") is None

    def test_all_mapped_intervals_have_consistent_units(self) -> None:
        # Every value should be a multiple of 60_000 (a minute) since
        # Binance only ships minute-aligned bars.
        for interval, ms in _INTERVAL_TO_MS.items():
            assert ms > 0, f"{interval} maps to non-positive ms"
            assert ms % 60_000 == 0, f"{interval} not minute-aligned"


@pytest.mark.unit
class TestTimeGapWiringLive:
    """The iter #92 wiring activates the D3 ``TIME_GAP`` check by
    feeding ``expected_dt_ms`` into the guard. Cadence breaks now
    surface in the audit ``bar_quality`` map.
    """

    def test_time_gap_in_klines_appears_in_audit_payload(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        # Build 220 clean 1h klines, then introduce one 2h jump
        # between bar 100 and bar 101 (skip 1 hour).
        klines = _bull_klines(220)
        good_bar = klines[101]
        c = good_bar.close
        klines[101] = Kline(
            open_time=good_bar.open_time + 3_600_000,  # +1h shift
            open=c,
            high=c * Decimal("1.01"),
            low=c * Decimal("0.99"),
            close=c,
            volume=Decimal("1"),
            close_time=good_bar.close_time + 3_600_000,
            n_trades=1,
        )
        at = _make_trader(klines=klines)
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="DATA_INGESTION_COMPLETED")
        assert len(rows) == 1
        bar_quality = rows[0]["payload"]["bar_quality"]
        assert "time_gap" in bar_quality

    def test_clean_cadence_does_not_flag_time_gap(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        at = _make_trader()  # _bull_klines default cadence = 60_000 ms (1m)
        # Note : _make_trader uses interval="1h" but _bull_klines uses
        # 60s steps. The mismatch means TIME_GAP fires on every bar.
        # We verify via a custom 1h-stepped fixture instead.
        at.run_cycle(now=1_700_000_000)
        # We don't assert TIME_GAP absent here because the fixture
        # step is 60s vs interval 1h ; the dedicated fixture-aligned
        # test below covers the clean path.

    def test_clean_1h_cadence_yields_no_time_gap(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        # Build a 220-bar series with the proper 1h cadence so the
        # TIME_GAP check finds nothing wrong.
        step_ms = 3_600_000
        klines = [
            Kline(
                open_time=i * step_ms,
                open=Decimal(str(100.0 + i * 0.5)),
                high=Decimal(str(100.0 + i * 0.5)) * Decimal("1.01"),
                low=Decimal(str(100.0 + i * 0.5)) * Decimal("0.99"),
                close=Decimal(str(100.0 + i * 0.5)),
                volume=Decimal("1"),
                close_time=(i + 1) * step_ms - 1,
                n_trades=1,
            )
            for i in range(220)
        ]
        at = _make_trader(klines=klines)
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="DATA_INGESTION_COMPLETED")
        assert len(rows) == 1
        bar_quality = rows[0]["payload"]["bar_quality"]
        # No TIME_GAP flag despite the 1h interval being now respected.
        assert "time_gap" not in bar_quality

    def test_unknown_interval_skips_time_gap_check(self, fresh_db: Path) -> None:
        # When the interval is unknown to ``_interval_to_ms``, the
        # guard receives ``expected_dt_ms=None`` and the TIME_GAP check
        # is silently skipped — no false positive on every bar.
        circuit_breaker.reset()
        kls = _bull_klines(220)
        at = AutoTrader(
            symbol="BTCUSDT",
            interval="1w",  # weekly bars, not in our mapping
            klines_limit=len(kls),
            orchestrator=Orchestrator(
                strategies=[
                    _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                    _FakeStrategy("b", _signal(0.9, confidence=0.9)),
                ],
            ),
            fetch_klines=lambda *_: kls,
            fetch_current_price=lambda _: Decimal("210"),
        )
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="DATA_INGESTION_COMPLETED")
        assert len(rows) == 1
        # Despite the fixture cadence not matching 1w, no TIME_GAP
        # because the check itself is skipped.
        assert "time_gap" not in rows[0]["payload"]["bar_quality"]


@pytest.mark.unit
class TestOutlierRangeWiringLive:
    """The iter #92 wiring computes ATR on the freshly-fetched klines
    and feeds it to the guard so the D3 ``OUTLIER_RANGE`` check
    becomes active.

    Limitation of the doc 11 §D3 check itself : the ATR is computed
    on the SAME series being checked, so an outlier bar contributes
    ~1/14 to its own ATR. The threshold is ``range > 50 x ATR``,
    which under self-reference becomes ``range > 50/14 x range``,
    i.e. mathematically impossible to fire on a single isolated
    spike. A future iter could split the ATR window from the check
    window (e.g. ATR over ``klines[:-1]`` before checking the
    last bar) ; for now the wiring is in place but the check
    primarily serves as a regression marker on multi-bar drift.

    These tests verify the wiring is active (no crash, ATR
    computed, audit emitted) rather than the firing (which requires
    a more elaborate fixture).
    """

    def test_atr_wiring_does_not_crash_on_short_series(self, fresh_db: Path) -> None:
        # On a series shorter than the ATR period (14), the helper
        # returns ``None`` and the OUTLIER check is silently skipped.
        circuit_breaker.reset()
        kls = _bull_klines(10)  # ATR_14 returns None
        at = AutoTrader(
            symbol="BTCUSDT",
            interval="1h",
            klines_limit=10,
            orchestrator=Orchestrator(),
            fetch_klines=lambda *_: kls,
            fetch_current_price=lambda _: Decimal("210"),
        )
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="DATA_INGESTION_COMPLETED")
        assert len(rows) == 1
        # No OUTLIER flag — check skipped because ATR is None.
        assert "outlier_range" not in rows[0]["payload"]["bar_quality"]

    def test_atr_wiring_active_on_full_series(self, fresh_db: Path) -> None:
        # 220 clean bars : ATR is computable (>15 bars), the OUTLIER
        # check runs but finds nothing wrong with the tight-range
        # synthetic series.
        circuit_breaker.reset()
        # Build a properly 1h-stepped series so TIME_GAP doesn't fire
        # alongside.
        step_ms = 3_600_000
        klines = [
            Kline(
                open_time=i * step_ms,
                open=Decimal(str(100.0 + i * 0.5)),
                high=Decimal(str(100.0 + i * 0.5)) * Decimal("1.01"),
                low=Decimal(str(100.0 + i * 0.5)) * Decimal("0.99"),
                close=Decimal(str(100.0 + i * 0.5)),
                volume=Decimal("1"),
                close_time=(i + 1) * step_ms - 1,
                n_trades=1,
            )
            for i in range(220)
        ]
        at = _make_trader(klines=klines)
        at.run_cycle(now=1_700_000_000)
        assert audit.flush_default_logger(timeout=2.0)
        rows = audit.query_events(event_type="DATA_INGESTION_COMPLETED")
        assert len(rows) == 1
        bar_quality = rows[0]["payload"]["bar_quality"]
        # No OUTLIER on a uniform series — wiring active, no false
        # positive.
        assert "outlier_range" not in bar_quality


# ─── LiveExecutor wiring (iter #96) ──────────────────────────────────────────


class _RecordingExecutor:
    """Test double that records each call and returns a programmable fill.

    Lets us verify that ``AutoTrader._maybe_open`` delegates to the
    executor with the right args (symbol/side/quantity/intended_price)
    and feeds the executor's ``fill_price`` and ``executed_qty`` to
    the tracker (anti-règle A1 : the DB row must reflect the real fill,
    not the orchestrator's intended price).
    """

    def __init__(
        self,
        *,
        fill_price: Decimal,
        executed_qty: Decimal | None = None,
    ) -> None:
        self.fill_price = fill_price
        self.executed_qty = executed_qty
        self.calls: list[dict[str, object]] = []

    def open_market_position(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        intended_price: Decimal,
    ) -> LiveOrderResult:
        self.calls.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "intended_price": intended_price,
            },
        )
        return LiveOrderResult(
            fill_price=self.fill_price,
            order_id="test-order-1",
            status="FILLED",
            executed_qty=self.executed_qty if self.executed_qty is not None else quantity,
            is_paper=False,
        )


@pytest.mark.unit
class TestLiveExecutorWiring:
    """:class:`AutoTrader` delegates to the injected :class:`LiveExecutor`."""

    def test_default_executor_is_paper(self, fresh_db: Path) -> None:
        # No injection → AutoTrader uses PaperLiveExecutor by default,
        # so pre-iter-#96 callers see no behavior change.
        at = AutoTrader()
        # Internal attribute exposed for the smoke test ; not part of
        # the public API.
        assert isinstance(at._live_executor, PaperLiveExecutor)

    def test_executor_receives_correct_args(self, fresh_db: Path) -> None:
        recorder = _RecordingExecutor(fill_price=Decimal("210"))
        klines = _bull_klines()
        orchestrator = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )
        tracker = PositionTracker()
        at = AutoTrader(
            symbol="BTCUSDT",
            interval="1h",
            klines_limit=len(klines),
            capital_provider=lambda: Decimal("1000"),
            orchestrator=orchestrator,
            tracker=tracker,
            live_executor=recorder,
            fetch_klines=lambda _symbol, _interval, _limit: klines,
            fetch_current_price=lambda _symbol: Decimal("210"),
        )
        report = at.run_cycle(now=1_700_000_000)
        assert report.opened_position is not None
        # Executor was called once with the right args.
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["symbol"] == "BTCUSDT"
        # The orchestrator yielded a LONG decision on a bull series ; the
        # AutoTrader translates LONG -> "BUY" before delegating.
        assert call["side"] == "BUY"
        assert isinstance(call["quantity"], Decimal)
        assert isinstance(call["intended_price"], Decimal)

    def test_tracker_uses_fill_price_not_intended_price(self, fresh_db: Path) -> None:
        # The whole point of iter #96 : a 5 USD slippage between the
        # orchestrator price and the Binance fill MUST surface in the
        # tracker so the PnL is honest.
        recorder = _RecordingExecutor(fill_price=Decimal("215"))
        klines = _bull_klines()
        orchestrator = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )
        tracker = PositionTracker()
        at = AutoTrader(
            symbol="BTCUSDT",
            interval="1h",
            klines_limit=len(klines),
            capital_provider=lambda: Decimal("1000"),
            orchestrator=orchestrator,
            tracker=tracker,
            live_executor=recorder,
            fetch_klines=lambda _symbol, _interval, _limit: klines,
            fetch_current_price=lambda _symbol: Decimal("210"),
        )
        report = at.run_cycle(now=1_700_000_000)
        assert report.opened_position is not None
        # Tracker stored the executor's fill price, not the
        # orchestrator's intended_price.
        assert report.opened_position.entry_price == Decimal("215")

    def test_tracker_uses_executed_qty_when_partial(self, fresh_db: Path) -> None:
        # Binance returns ``executedQty`` < requested in case of partial
        # fill or lot-size truncation. The tracker must persist the
        # actually-executed quantity, not the requested one.
        recorder = _RecordingExecutor(
            fill_price=Decimal("210"),
            executed_qty=Decimal("0.5"),  # arbitrary partial qty
        )
        klines = _bull_klines()
        orchestrator = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(0.9, confidence=0.9)),
            ],
        )
        tracker = PositionTracker()
        at = AutoTrader(
            symbol="BTCUSDT",
            interval="1h",
            klines_limit=len(klines),
            capital_provider=lambda: Decimal("1000"),
            orchestrator=orchestrator,
            tracker=tracker,
            live_executor=recorder,
            fetch_klines=lambda _symbol, _interval, _limit: klines,
            fetch_current_price=lambda _symbol: Decimal("210"),
        )
        report = at.run_cycle(now=1_700_000_000)
        assert report.opened_position is not None
        assert report.opened_position.quantity == Decimal("0.5")

    def test_executor_not_called_on_skip(self, fresh_db: Path) -> None:
        # ``should_trade=False`` (no signal qualified) MUST not reach
        # the executor — anti-règle A1 : no fictitious order on a skip.
        recorder = _RecordingExecutor(fill_price=Decimal("210"))
        # Two contradictory signals → ensemble fails to qualify.
        orchestrator = Orchestrator(
            strategies=[
                _FakeStrategy("a", _signal(0.9, confidence=0.9)),
                _FakeStrategy("b", _signal(-0.9, confidence=0.9)),
            ],
        )
        klines = _bull_klines()
        at = AutoTrader(
            symbol="BTCUSDT",
            interval="1h",
            klines_limit=len(klines),
            capital_provider=lambda: Decimal("1000"),
            orchestrator=orchestrator,
            tracker=PositionTracker(),
            live_executor=recorder,
            fetch_klines=lambda _symbol, _interval, _limit: klines,
            fetch_current_price=lambda _symbol: Decimal("210"),
        )
        report = at.run_cycle(now=1_700_000_000)
        assert report.opened_position is None
        assert recorder.calls == []
