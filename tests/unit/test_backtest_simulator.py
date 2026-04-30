"""Unit tests for the iter #93 backtest fill simulator.

Cover :

* :func:`simulate_position` — entry fill via apply_adversarial_fill +
  scan SL/TP on subsequent bars + EXPIRED fallback at max_hold.
* Each :class:`SimulatedExitReason` path : ``STOP`` / ``TARGET`` /
  ``BOTH_STOP_WINS`` (pessimistic doc 10 R2) / ``EXPIRED``.
* LONG and SHORT symmetry.
* Edge cases : insufficient klines (returns None), max_hold=0,
  R-multiple computation, fees deducted in PnL.
* Validation errors : negative quantity, negative max_hold, invalid
  level placement (e.g. LONG stop above signal).

Pure tests : no DB, no network. Synthetic klines built ad-hoc per
test for reproducibility.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.backtest_simulator import (
    SimulatedExitReason,
    SimulatedTrade,
    simulate_position,
)
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra.market_data import Kline

# ─── Helpers ────────────────────────────────────────────────────────────────


def _kline(
    *,
    high: str,
    low: str,
    close: str,
    open_time: int = 0,
    open_: str | None = None,
    volume: str = "10",
    close_time: int | None = None,
    n_trades: int = 5,
) -> Kline:
    """Build a synthetic :class:`Kline` with the OHLC the test cares about."""
    actual_open = open_ if open_ is not None else close
    actual_close_time = close_time if close_time is not None else open_time + 60_000
    return Kline(
        open_time=open_time,
        open=Decimal(actual_open),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        close_time=actual_close_time,
        n_trades=n_trades,
    )


def _flat_series(n: int, price: str = "100", *, start_open_time: int = 0) -> list[Kline]:
    """``n`` bars at a flat price (high = low = close = price)."""
    return [
        _kline(
            high=price,
            low=price,
            close=price,
            open_time=start_open_time + i * 60_000,
        )
        for i in range(n)
    ]


# ─── LONG happy paths ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestLongTargetHit:
    def test_target_reached_returns_target_exit(self) -> None:
        # Signal at bar 0 (close=100), entry at bar 1 (latency=1).
        # Stop = 90, target = 110. Bar 3 high reaches 115 -> target hit.
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),  # signal bar
            _kline(high="102", low="100", close="101", open_time=60_000),  # entry bar
            _kline(high="105", low="100", close="103", open_time=120_000),
            _kline(high="115", low="105", close="112", open_time=180_000),  # tp hit
        ]
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=10,
        )
        assert trade is not None
        assert trade.exit_reason is SimulatedExitReason.TARGET
        assert trade.entry_bar_index == 1
        assert trade.exit_bar_index == 3
        # Target = 110, entry filled at worst-of-bar high (102) +
        # slippage. R-multiple = (110 - entry_fill) / risk_per_unit
        # where risk_per_unit = 100 - 90 = 10.
        assert trade.r_realized > Decimal("0")
        # Fees are deducted from PnL (positive but less than gross).
        gross = (Decimal("110") - trade.entry_fill.fill_price) * Decimal("1")
        assert trade.realized_pnl < gross


@pytest.mark.unit
class TestLongStopHit:
    def test_stop_breached_returns_stop_exit(self) -> None:
        # Stop = 90. Bar 2 low reaches 88 -> stop hit at 90.
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),
            _kline(high="102", low="100", close="101", open_time=60_000),
            _kline(high="98", low="88", close="92", open_time=120_000),  # sl hit
        ]
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=10,
        )
        assert trade is not None
        assert trade.exit_reason is SimulatedExitReason.STOP
        assert trade.exit_bar_index == 2
        # Negative R-multiple : entry filled around 102+slippage, exit
        # at stop=90, risk_per_unit=10. R = (90 - 102) / 10 ≈ -1.2
        # (entry above signal_price due to worst-of-bar pessimism).
        assert trade.r_realized < Decimal("0")


@pytest.mark.unit
class TestLongBothSameBar:
    def test_both_stop_and_target_in_same_bar_stop_wins(self) -> None:
        # Bar 2 has high=115 (tp) AND low=88 (sl). Pessimistic
        # ordering : stop fires first.
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),
            _kline(high="102", low="100", close="101", open_time=60_000),
            _kline(high="115", low="88", close="100", open_time=120_000),  # both
        ]
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=10,
        )
        assert trade is not None
        assert trade.exit_reason is SimulatedExitReason.BOTH_STOP_WINS
        # Exit price = stop (the loser side wins under pessimism).
        assert trade.exit_fill.fill_price == Decimal("90")
        assert trade.r_realized < Decimal("0")


@pytest.mark.unit
class TestLongExpired:
    def test_no_hit_within_max_hold_expires_at_last_bar(self) -> None:
        # All bars are flat at 101 -> never touches stop=90 or target=110.
        klines = _flat_series(6, price="101")
        # Adjust the entry bar (idx=1) to have non-zero range so
        # ``apply_adversarial_fill`` doesn't degenerate.
        klines[1] = _kline(high="102", low="100", close="101", open_time=60_000)
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=3,
        )
        assert trade is not None
        assert trade.exit_reason is SimulatedExitReason.EXPIRED
        # max_hold = 3 -> scan bars 2, 3, 4 (entry bar = 1). Last
        # scanned bar index = 4.
        assert trade.exit_bar_index == 4


# ─── SHORT mirror ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestShortMirror:
    def test_short_target_hit(self) -> None:
        # SHORT : signal=100, stop=110 (above), target=90 (below).
        # Bar 2 low reaches 85 -> target hit at 90.
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),
            _kline(high="100", low="98", close="99", open_time=60_000),
            _kline(high="98", low="85", close="92", open_time=120_000),
        ]
        trade = simulate_position(
            side=Side.SHORT,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("110"),
            target=Decimal("90"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=10,
        )
        assert trade is not None
        assert trade.exit_reason is SimulatedExitReason.TARGET
        assert trade.exit_fill.fill_price == Decimal("90")
        assert trade.r_realized > Decimal("0")

    def test_short_stop_hit(self) -> None:
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),
            _kline(high="100", low="98", close="99", open_time=60_000),
            _kline(high="115", low="105", close="112", open_time=120_000),
        ]
        trade = simulate_position(
            side=Side.SHORT,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("110"),
            target=Decimal("90"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=10,
        )
        assert trade is not None
        assert trade.exit_reason is SimulatedExitReason.STOP
        assert trade.exit_fill.fill_price == Decimal("110")
        assert trade.r_realized < Decimal("0")


# ─── Insufficient klines ────────────────────────────────────────────────────


@pytest.mark.unit
class TestInsufficientKlines:
    def test_signal_at_last_bar_returns_none(self) -> None:
        # Signal at bar 0, but klines has only 1 bar -> entry would be
        # at bar 1 which doesn't exist.
        klines = [_kline(high="101", low="99", close="100")]
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=5,
        )
        assert trade is None

    def test_max_hold_zero_yields_expired_at_entry_bar(self) -> None:
        # max_hold = 0 -> no scan window, immediate EXPIRED at entry.
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),
            _kline(high="102", low="100", close="101", open_time=60_000),
        ]
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=0,
        )
        assert trade is not None
        assert trade.exit_reason is SimulatedExitReason.EXPIRED
        # Entry bar = 1, scan window empty -> exit on the entry bar
        # itself (degenerate but well-defined).
        assert trade.exit_bar_index == 1


# ─── Validation errors ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_zero_quantity_raises(self) -> None:
        with pytest.raises(ValueError, match="quantity must be > 0"):
            simulate_position(
                side=Side.LONG,
                signal_bar_index=0,
                signal_price=Decimal("100"),
                stop=Decimal("90"),
                target=Decimal("110"),
                quantity=Decimal("0"),
                klines=_flat_series(3),
                max_hold=2,
            )

    def test_negative_max_hold_raises(self) -> None:
        with pytest.raises(ValueError, match="max_hold must be >= 0"):
            simulate_position(
                side=Side.LONG,
                signal_bar_index=0,
                signal_price=Decimal("100"),
                stop=Decimal("90"),
                target=Decimal("110"),
                quantity=Decimal("1"),
                klines=_flat_series(3),
                max_hold=-1,
            )

    def test_long_stop_above_signal_raises(self) -> None:
        # LONG stop must be below signal — defense against caller
        # passing the wrong levels.
        with pytest.raises(ValueError, match="LONG stop"):
            simulate_position(
                side=Side.LONG,
                signal_bar_index=0,
                signal_price=Decimal("100"),
                stop=Decimal("105"),  # invalid
                target=Decimal("110"),
                quantity=Decimal("1"),
                klines=_flat_series(3),
                max_hold=2,
            )

    def test_long_target_below_signal_raises(self) -> None:
        with pytest.raises(ValueError, match="LONG target"):
            simulate_position(
                side=Side.LONG,
                signal_bar_index=0,
                signal_price=Decimal("100"),
                stop=Decimal("90"),
                target=Decimal("95"),  # invalid
                quantity=Decimal("1"),
                klines=_flat_series(3),
                max_hold=2,
            )

    def test_short_stop_below_signal_raises(self) -> None:
        with pytest.raises(ValueError, match="SHORT stop"):
            simulate_position(
                side=Side.SHORT,
                signal_bar_index=0,
                signal_price=Decimal("100"),
                stop=Decimal("95"),  # invalid (must be > signal)
                target=Decimal("90"),
                quantity=Decimal("1"),
                klines=_flat_series(3),
                max_hold=2,
            )

    def test_zero_signal_price_raises(self) -> None:
        with pytest.raises(ValueError, match="signal_price"):
            simulate_position(
                side=Side.LONG,
                signal_bar_index=0,
                signal_price=Decimal("0"),
                stop=Decimal("-10"),
                target=Decimal("10"),
                quantity=Decimal("1"),
                klines=_flat_series(3),
                max_hold=2,
            )


# ─── R-multiple consistency ────────────────────────────────────────────────


@pytest.mark.unit
class TestRMultiple:
    def test_target_hit_r_close_to_plus_one(self) -> None:
        # 1R target : risk_per_unit = 10 (entry 100, stop 90), target
        # at 110 (=entry + 1R). The R-multiple should be slightly less
        # than 1 because the entry filled ~ 102 due to worst-of-bar
        # pessimism : (110 - 102) / 10 ≈ 0.8.
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),
            _kline(high="102", low="100", close="101", open_time=60_000),
            _kline(high="115", low="105", close="112", open_time=120_000),
        ]
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=10,
        )
        assert trade is not None
        assert Decimal("0.5") < trade.r_realized < Decimal("1.0")

    def test_stop_hit_r_close_to_minus_one(self) -> None:
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),
            _kline(high="102", low="100", close="101", open_time=60_000),
            _kline(high="98", low="88", close="92", open_time=120_000),
        ]
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=10,
        )
        assert trade is not None
        # Negative because stop hit. Between -2 and -1 due to worst-of-bar
        # entry pessimism inflating the loss.
        assert Decimal("-2.0") < trade.r_realized < Decimal("-1.0")


# ─── Dataclass shape ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestSimulatedTradeShape:
    def test_frozen_immutable(self) -> None:
        klines = [
            _kline(high="101", low="99", close="100", open_time=0),
            _kline(high="102", low="100", close="101", open_time=60_000),
            _kline(high="115", low="105", close="112", open_time=120_000),
        ]
        trade = simulate_position(
            side=Side.LONG,
            signal_bar_index=0,
            signal_price=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("110"),
            quantity=Decimal("1"),
            klines=klines,
            max_hold=5,
        )
        assert trade is not None
        assert isinstance(trade, SimulatedTrade)
        with pytest.raises((AttributeError, Exception), match=r"cannot assign|frozen"):
            trade.exit_reason = SimulatedExitReason.STOP  # type: ignore[misc]
