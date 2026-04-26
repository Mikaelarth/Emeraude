"""Unit tests for emeraude.agent.execution.breaker_monitor."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution import circuit_breaker
from emeraude.agent.execution.breaker_monitor import (
    BreakerCheckResult,
    BreakerMonitor,
)
from emeraude.agent.execution.circuit_breaker import CircuitBreakerState
from emeraude.agent.execution.position_tracker import (
    ExitReason,
    PositionTracker,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import database


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _close_trade(
    *,
    is_winner: bool,
    opened_at: int,
    closed_at: int,
    tracker: PositionTracker | None = None,
    exit_price: Decimal | None = None,
) -> None:
    """Open + close one position.

    Defaults : LONG entry 100, stop 98, target 104, risk 2. Exit
    defaults to 104 (winner, +2 R) or 98 (loser, -1 R). Pass
    ``exit_price`` to override (e.g. 99 for a -0.5 R partial loss).
    """
    t = tracker if tracker is not None else PositionTracker()
    t.open_position(
        strategy="trend_follower",
        regime=Regime.BULL,
        side=Side.LONG,
        entry_price=Decimal("100"),
        stop=Decimal("98"),
        target=Decimal("104"),
        quantity=Decimal("0.1"),
        risk_per_unit=Decimal("2"),
        opened_at=opened_at,
    )
    if exit_price is None:
        exit_price = Decimal("104") if is_winner else Decimal("98")
    t.close_position(
        exit_price=exit_price,
        exit_reason=ExitReason.TARGET_HIT if is_winner else ExitReason.STOP_HIT,
        closed_at=closed_at,
    )


# ─── Construction validation ────────────────────────────────────────────────


@pytest.mark.unit
class TestConstruction:
    def test_warn_zero_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="warn_consecutive_losses"):
            BreakerMonitor(warn_consecutive_losses=0)

    def test_trip_below_warn_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="trip_consecutive_losses"):
            BreakerMonitor(
                warn_consecutive_losses=3,
                trip_consecutive_losses=2,
            )

    def test_positive_r_loss_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="trip_cumulative_r_loss_24h"):
            BreakerMonitor(trip_cumulative_r_loss_24h=Decimal("0.5"))

    def test_zero_r_loss_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="trip_cumulative_r_loss_24h"):
            BreakerMonitor(trip_cumulative_r_loss_24h=Decimal("0"))

    def test_zero_window_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="window_seconds"):
            BreakerMonitor(window_seconds=0)

    def test_zero_history_limit_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match="history_limit"):
            BreakerMonitor(history_limit=0)


# ─── No trades / healthy ────────────────────────────────────────────────────


@pytest.mark.unit
class TestEmptyHistory:
    def test_no_history_keeps_healthy(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        result = BreakerMonitor().check(now=1_000_000)
        assert result.state_before == CircuitBreakerState.HEALTHY
        assert result.state_after == CircuitBreakerState.HEALTHY
        assert result.consecutive_losses == 0
        assert result.cumulative_r_24h == Decimal("0")
        assert result.n_trades_24h == 0
        assert result.transitioned is False
        assert result.triggered_reason is None

    def test_winning_trade_keeps_healthy(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        _close_trade(is_winner=True, opened_at=1, closed_at=2)
        result = BreakerMonitor().check(now=10)
        assert result.state_after == CircuitBreakerState.HEALTHY
        assert result.consecutive_losses == 0


# ─── Consecutive losses : WARN ──────────────────────────────────────────────


@pytest.mark.unit
class TestConsecutiveWarn:
    def test_two_losses_no_warn(self, fresh_db: Path) -> None:
        # Use partial losses (-0.5 R each) so the cumulative-R gate
        # does not also fire — this test isolates the consec gate.
        circuit_breaker.reset()
        for i in range(2):
            _close_trade(
                is_winner=False,
                opened_at=10 * i,
                closed_at=10 * i + 1,
                exit_price=Decimal("99"),  # -0.5 R
            )
        result = BreakerMonitor().check(now=100)
        assert result.consecutive_losses == 2
        assert result.state_after == CircuitBreakerState.HEALTHY

    def test_three_losses_warns(self, fresh_db: Path) -> None:
        # 3 partial losses (-0.5 R each = -1.5 R total) keeps the
        # cumulative gate silent so the consec-WARN fires alone.
        circuit_breaker.reset()
        for i in range(3):
            _close_trade(
                is_winner=False,
                opened_at=10 * i,
                closed_at=10 * i + 1,
                exit_price=Decimal("99"),
            )
        result = BreakerMonitor().check(now=100)
        assert result.consecutive_losses == 3
        assert result.cumulative_r_24h == Decimal("-1.5")
        assert result.state_after == CircuitBreakerState.WARNING
        assert result.transitioned is True
        assert result.triggered_reason is not None
        assert "consecutive_losses_warn" in result.triggered_reason

    def test_warn_does_not_re_trigger_on_already_warning(self, fresh_db: Path) -> None:
        # Manually set WARN, then run a check with 3 partial losses :
        # state stays WARN, no new transition is applied.
        circuit_breaker.warn("manual")
        for i in range(3):
            _close_trade(
                is_winner=False,
                opened_at=10 * i,
                closed_at=10 * i + 1,
                exit_price=Decimal("99"),
            )
        result = BreakerMonitor().check(now=100)
        assert result.state_before == CircuitBreakerState.WARNING
        assert result.state_after == CircuitBreakerState.WARNING
        assert result.transitioned is False
        assert result.triggered_reason is None

    def test_winning_trade_breaks_streak(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        _close_trade(is_winner=False, opened_at=1, closed_at=2)
        _close_trade(is_winner=False, opened_at=3, closed_at=4)
        _close_trade(is_winner=True, opened_at=5, closed_at=6)
        result = BreakerMonitor().check(now=100)
        assert result.consecutive_losses == 0
        assert result.state_after == CircuitBreakerState.HEALTHY


# ─── Consecutive losses : TRIP ──────────────────────────────────────────────


@pytest.mark.unit
class TestConsecutiveTrip:
    def test_five_losses_trips(self, fresh_db: Path) -> None:
        circuit_breaker.reset()
        for i in range(5):
            _close_trade(is_winner=False, opened_at=10 * i, closed_at=10 * i + 1)
        result = BreakerMonitor().check(now=100)
        assert result.consecutive_losses == 5
        assert result.state_after == CircuitBreakerState.TRIGGERED
        assert result.triggered_reason is not None
        assert "consecutive_losses_trip" in result.triggered_reason

    def test_trip_takes_precedence_over_warn(self, fresh_db: Path) -> None:
        # 5 losses cross both thresholds ; TRIP wins.
        circuit_breaker.reset()
        for i in range(5):
            _close_trade(is_winner=False, opened_at=10 * i, closed_at=10 * i + 1)
        result = BreakerMonitor().check(now=100)
        assert result.state_after == CircuitBreakerState.TRIGGERED


# ─── Cumulative R loss 24h ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCumulativeRLoss:
    def test_cumulative_loss_below_threshold_no_trip(self, fresh_db: Path) -> None:
        # 2 losses of -1 R each : cumulative = -2, threshold = -3 ->
        # no trip (consecutive=2 also under WARN=3).
        circuit_breaker.reset()
        _close_trade(is_winner=False, opened_at=10, closed_at=11)
        _close_trade(is_winner=False, opened_at=20, closed_at=21)
        result = BreakerMonitor().check(now=100)
        assert result.cumulative_r_24h == Decimal("-2")
        assert result.state_after == CircuitBreakerState.HEALTHY

    def test_cumulative_loss_at_threshold_trips(self, fresh_db: Path) -> None:
        # Mock 3 losses : -3 R total ; consec=3 would also WARN, but
        # cumulative-trip is checked before WARN -> TRIGGERED wins.
        circuit_breaker.reset()
        for i in range(3):
            _close_trade(is_winner=False, opened_at=10 * i, closed_at=10 * i + 1)
        result = BreakerMonitor().check(now=100)
        # cumulative = -3 == threshold -> trip.
        assert result.cumulative_r_24h == Decimal("-3")
        assert result.state_after == CircuitBreakerState.TRIGGERED
        assert result.triggered_reason is not None
        assert "cumulative_r_loss_24h_trip" in result.triggered_reason

    def test_old_trades_outside_window_excluded(self, fresh_db: Path) -> None:
        # 3 losses 25h ago + 1 loss now : cumulative within 24h = -1.
        circuit_breaker.reset()
        old_ts = 100  # 25h ago given now below
        for i in range(3):
            _close_trade(is_winner=False, opened_at=old_ts + i, closed_at=old_ts + i + 1)
        # Now : 25h later, plus one fresh loss.
        now = old_ts + 25 * 3600
        _close_trade(is_winner=False, opened_at=now - 100, closed_at=now - 50)
        result = BreakerMonitor().check(now=now)
        assert result.n_trades_24h == 1
        assert result.cumulative_r_24h == Decimal("-1")
        # consec count is 4 (history is most-recent first, all 4 losses)
        # which crosses the WARN threshold of 3.
        assert result.consecutive_losses == 4
        assert result.state_after == CircuitBreakerState.WARNING

    def test_winning_trades_offset_losses(self, fresh_db: Path) -> None:
        # -1 - 1 + 2 = 0 : no trip even though there are losses.
        circuit_breaker.reset()
        _close_trade(is_winner=False, opened_at=10, closed_at=11)
        _close_trade(is_winner=False, opened_at=20, closed_at=21)
        _close_trade(is_winner=True, opened_at=30, closed_at=31)
        result = BreakerMonitor().check(now=100)
        # (104-100)/2 = 2 ; (98-100)/2 = -1 each. Sum = -1 -1 + 2 = 0.
        assert result.cumulative_r_24h == Decimal("0")
        assert result.state_after == CircuitBreakerState.HEALTHY


# ─── Terminal states (TRIGGERED / FROZEN) ───────────────────────────────────


@pytest.mark.unit
class TestTerminalStates:
    def test_triggered_stays_triggered_no_action(self, fresh_db: Path) -> None:
        circuit_breaker.trip("manual")
        # Even with conditions screaming for a downgrade : stays TRIGGERED.
        for i in range(3):
            _close_trade(is_winner=True, opened_at=10 * i, closed_at=10 * i + 1)
        result = BreakerMonitor().check(now=100)
        assert result.state_before == CircuitBreakerState.TRIGGERED
        assert result.state_after == CircuitBreakerState.TRIGGERED
        assert result.triggered_reason is None

    def test_frozen_stays_frozen_no_action(self, fresh_db: Path) -> None:
        circuit_breaker.freeze("manual")
        for i in range(5):
            _close_trade(is_winner=False, opened_at=10 * i, closed_at=10 * i + 1)
        result = BreakerMonitor().check(now=100)
        assert result.state_before == CircuitBreakerState.FROZEN
        assert result.state_after == CircuitBreakerState.FROZEN
        # Even with 5 losses, no transition is applied.
        assert result.triggered_reason is None


# ─── BreakerCheckResult shape ───────────────────────────────────────────────


@pytest.mark.unit
class TestResultShape:
    def test_result_is_frozen(self, fresh_db: Path) -> None:
        result = BreakerCheckResult(
            state_before=CircuitBreakerState.HEALTHY,
            state_after=CircuitBreakerState.HEALTHY,
            consecutive_losses=0,
            cumulative_r_24h=Decimal("0"),
            n_trades_24h=0,
            triggered_reason=None,
        )
        with pytest.raises(AttributeError):
            result.consecutive_losses = 5  # type: ignore[misc]

    def test_transitioned_property(self, fresh_db: Path) -> None:
        same = BreakerCheckResult(
            state_before=CircuitBreakerState.HEALTHY,
            state_after=CircuitBreakerState.HEALTHY,
            consecutive_losses=0,
            cumulative_r_24h=Decimal("0"),
            n_trades_24h=0,
            triggered_reason=None,
        )
        diff = BreakerCheckResult(
            state_before=CircuitBreakerState.HEALTHY,
            state_after=CircuitBreakerState.WARNING,
            consecutive_losses=3,
            cumulative_r_24h=Decimal("-3"),
            n_trades_24h=3,
            triggered_reason="x",
        )
        assert same.transitioned is False
        assert diff.transitioned is True
