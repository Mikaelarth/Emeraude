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
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.agent.reasoning.strategies import StrategySignal
from emeraude.infra import audit, database
from emeraude.infra.market_data import Kline
from emeraude.services.auto_trader import (
    AutoTrader,
    CycleReport,
    _default_capital_provider,
)
from emeraude.services.drift_monitor import DriftMonitor
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
        klines_limit=250,
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
            return _bull_klines()

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
