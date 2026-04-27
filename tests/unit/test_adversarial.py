"""Unit tests for emeraude.agent.learning.adversarial."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.adversarial import (
    DEFAULT_FEE_PCT,
    DEFAULT_LATENCY_BARS,
    DEFAULT_SLIPPAGE_PCT,
    AdversarialFill,
    AdversarialParams,
    apply_adversarial_fill,
    compute_realized_pnl,
)
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra.market_data import Kline


def _kline(
    *,
    open_p: Decimal = Decimal("100"),
    high: Decimal = Decimal("105"),
    low: Decimal = Decimal("95"),
    close: Decimal = Decimal("100"),
    volume: Decimal = Decimal("1"),
) -> Kline:
    return Kline(
        open_time=0,
        open=open_p,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time=60_000,
        n_trades=1,
    )


# ─── Defaults ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_doc10_slippage_default(self) -> None:
        # Doc 10 R2 : 2x median observed slippage ; 0.05 % theoretical
        # -> 0.1 % adversarial.
        assert Decimal("0.001") == DEFAULT_SLIPPAGE_PCT

    def test_doc10_fee_default(self) -> None:
        # Doc 10 R2 : 1.1x Binance taker fee 0.10 % -> 0.11 %.
        assert Decimal("0.0011") == DEFAULT_FEE_PCT

    def test_doc10_latency_default(self) -> None:
        # Doc 10 R2 : 1 bar lag.
        assert DEFAULT_LATENCY_BARS == 1


# ─── AdversarialParams validation ──────────────────────────────────────────


@pytest.mark.unit
class TestAdversarialParams:
    def test_defaults_constructible(self) -> None:
        p = AdversarialParams()
        assert p.slippage_pct == DEFAULT_SLIPPAGE_PCT
        assert p.fee_pct == DEFAULT_FEE_PCT
        assert p.latency_bars == DEFAULT_LATENCY_BARS

    def test_custom_values(self) -> None:
        p = AdversarialParams(
            slippage_pct=Decimal("0.005"),
            fee_pct=Decimal("0.002"),
            latency_bars=2,
        )
        assert p.slippage_pct == Decimal("0.005")
        assert p.fee_pct == Decimal("0.002")
        assert p.latency_bars == 2

    def test_negative_slippage_rejected(self) -> None:
        with pytest.raises(ValueError, match="slippage_pct must be >= 0"):
            AdversarialParams(slippage_pct=Decimal("-0.001"))

    def test_negative_fee_rejected(self) -> None:
        with pytest.raises(ValueError, match="fee_pct must be >= 0"):
            AdversarialParams(fee_pct=Decimal("-0.001"))

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValueError, match="latency_bars must be >= 0"):
            AdversarialParams(latency_bars=-1)

    def test_zero_values_accepted(self) -> None:
        # Zero slippage / fees / latency = "ideal" baseline. Useful
        # for differential tests.
        p = AdversarialParams(
            slippage_pct=Decimal("0"),
            fee_pct=Decimal("0"),
            latency_bars=0,
        )
        assert p.slippage_pct == Decimal("0")


# ─── apply_adversarial_fill : BUY ──────────────────────────────────────────


@pytest.mark.unit
class TestApplyAdversarialFillBuy:
    def test_buy_fills_at_high(self) -> None:
        # No slippage/fee for clarity : BUY worst-of-bar is the high.
        bar = _kline(high=Decimal("105"), low=Decimal("95"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("100"),
            side=Side.LONG,
            execution_bar=bar,
            quantity=Decimal("1"),
            params=AdversarialParams(
                slippage_pct=Decimal("0"),
                fee_pct=Decimal("0"),
            ),
        )
        assert fill.worst_bar_price == Decimal("105")
        assert fill.fill_price == Decimal("105")  # no slippage
        assert fill.fee == Decimal("0")
        assert fill.slippage_cost == Decimal("0")

    def test_buy_slippage_increases_fill(self) -> None:
        # 1 % slippage : fill = 105 * 1.01 = 106.05.
        bar = _kline(high=Decimal("105"), low=Decimal("95"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("100"),
            side=Side.LONG,
            execution_bar=bar,
            quantity=Decimal("1"),
            params=AdversarialParams(
                slippage_pct=Decimal("0.01"),
                fee_pct=Decimal("0"),
            ),
        )
        assert fill.worst_bar_price == Decimal("105")
        assert fill.fill_price == Decimal("106.05")
        # slippage_cost = |106.05 - 105| * 1 = 1.05.
        assert fill.slippage_cost == Decimal("1.05")

    def test_buy_fee_proportional_to_notional(self) -> None:
        bar = _kline(high=Decimal("100"), low=Decimal("95"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("99"),
            side=Side.LONG,
            execution_bar=bar,
            quantity=Decimal("2"),
            params=AdversarialParams(
                slippage_pct=Decimal("0"),
                fee_pct=Decimal("0.001"),
            ),
        )
        # fill_price = 100 (high), notional = 200, fee = 0.2.
        assert fill.fee == Decimal("0.200")

    def test_buy_cash_flow_negative(self) -> None:
        bar = _kline(high=Decimal("100"), low=Decimal("95"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("99"),
            side=Side.LONG,
            execution_bar=bar,
            quantity=Decimal("1"),
            params=AdversarialParams(
                slippage_pct=Decimal("0"),
                fee_pct=Decimal("0.01"),
            ),
        )
        # cash_flow = -(notional + fee) = -(100 + 1) = -101.
        assert fill.cash_flow == Decimal("-101")


# ─── apply_adversarial_fill : SELL ─────────────────────────────────────────


@pytest.mark.unit
class TestApplyAdversarialFillSell:
    def test_sell_fills_at_low(self) -> None:
        bar = _kline(high=Decimal("105"), low=Decimal("95"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("100"),
            side=Side.SHORT,
            execution_bar=bar,
            quantity=Decimal("1"),
            params=AdversarialParams(
                slippage_pct=Decimal("0"),
                fee_pct=Decimal("0"),
            ),
        )
        assert fill.worst_bar_price == Decimal("95")
        assert fill.fill_price == Decimal("95")

    def test_sell_slippage_decreases_fill(self) -> None:
        # 1 % slippage : fill = 95 * 0.99 = 94.05.
        bar = _kline(high=Decimal("105"), low=Decimal("95"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("100"),
            side=Side.SHORT,
            execution_bar=bar,
            quantity=Decimal("1"),
            params=AdversarialParams(
                slippage_pct=Decimal("0.01"),
                fee_pct=Decimal("0"),
            ),
        )
        assert fill.fill_price == Decimal("94.05")
        # slippage_cost = |94.05 - 95| * 1 = 0.95.
        assert fill.slippage_cost == Decimal("0.95")

    def test_sell_cash_flow_positive(self) -> None:
        bar = _kline(high=Decimal("105"), low=Decimal("100"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("99"),
            side=Side.SHORT,
            execution_bar=bar,
            quantity=Decimal("1"),
            params=AdversarialParams(
                slippage_pct=Decimal("0"),
                fee_pct=Decimal("0.01"),
            ),
        )
        # fill = 100 (low), notional = 100, fee = 1, cash = +99.
        assert fill.cash_flow == Decimal("99")


# ─── apply_adversarial_fill : edge cases & validation ──────────────────────


@pytest.mark.unit
class TestApplyAdversarialFillValidation:
    def test_default_params_used_when_none(self) -> None:
        bar = _kline(high=Decimal("100"), low=Decimal("99"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("99"),
            side=Side.LONG,
            execution_bar=bar,
            quantity=Decimal("1"),
            # params=None implicit
        )
        # fill_price = 100 * (1 + 0.001) = 100.1.
        assert fill.fill_price == Decimal("100.100")

    def test_zero_signal_price_rejected(self) -> None:
        with pytest.raises(ValueError, match="signal_price must be > 0"):
            apply_adversarial_fill(
                signal_price=Decimal("0"),
                side=Side.LONG,
                execution_bar=_kline(),
                quantity=Decimal("1"),
            )

    def test_zero_quantity_rejected(self) -> None:
        with pytest.raises(ValueError, match="quantity must be > 0"):
            apply_adversarial_fill(
                signal_price=Decimal("100"),
                side=Side.LONG,
                execution_bar=_kline(),
                quantity=Decimal("0"),
            )

    def test_degenerate_bar_rejected(self) -> None:
        # high < low is a corrupt kline.
        bar = _kline(high=Decimal("90"), low=Decimal("100"))
        with pytest.raises(ValueError, match=r"high must be >= execution_bar\.low"):
            apply_adversarial_fill(
                signal_price=Decimal("99"),
                side=Side.LONG,
                execution_bar=bar,
                quantity=Decimal("1"),
            )

    def test_fill_is_frozen(self) -> None:
        bar = _kline()
        fill = apply_adversarial_fill(
            signal_price=Decimal("99"),
            side=Side.LONG,
            execution_bar=bar,
            quantity=Decimal("1"),
        )
        assert isinstance(fill, AdversarialFill)
        with pytest.raises(AttributeError):
            fill.fill_price = Decimal("0")  # type: ignore[misc]

    def test_total_notional_property(self) -> None:
        bar = _kline(high=Decimal("100"), low=Decimal("99"))
        fill = apply_adversarial_fill(
            signal_price=Decimal("99"),
            side=Side.LONG,
            execution_bar=bar,
            quantity=Decimal("3"),
            params=AdversarialParams(slippage_pct=Decimal("0"), fee_pct=Decimal("0")),
        )
        # notional = 100 * 3 = 300.
        assert fill.total_notional == Decimal("300")


# ─── compute_realized_pnl : LONG round-trip ────────────────────────────────


@pytest.mark.unit
class TestComputeRealizedPnlLong:
    def _entry(self) -> AdversarialFill:
        # BUY at 100, 1 unit, 0.1 fee.
        return AdversarialFill(
            side=Side.LONG,
            signal_price=Decimal("99"),
            worst_bar_price=Decimal("100"),
            fill_price=Decimal("100"),
            quantity=Decimal("1"),
            fee=Decimal("0.1"),
            slippage_cost=Decimal("0"),
        )

    def test_long_winner(self) -> None:
        # SELL at 110, fee 0.11 : PnL = (110 - 100)*1 - 0.21 = 9.79.
        exit_fill = AdversarialFill(
            side=Side.SHORT,
            signal_price=Decimal("110"),
            worst_bar_price=Decimal("110"),
            fill_price=Decimal("110"),
            quantity=Decimal("1"),
            fee=Decimal("0.11"),
            slippage_cost=Decimal("0"),
        )
        pnl = compute_realized_pnl(entry=self._entry(), exit_fill=exit_fill)
        assert pnl == Decimal("9.79")

    def test_long_loser(self) -> None:
        # SELL at 95 : PnL = (95 - 100)*1 - 0.21 = -5.21.
        exit_fill = AdversarialFill(
            side=Side.SHORT,
            signal_price=Decimal("95"),
            worst_bar_price=Decimal("95"),
            fill_price=Decimal("95"),
            quantity=Decimal("1"),
            fee=Decimal("0.11"),
            slippage_cost=Decimal("0"),
        )
        pnl = compute_realized_pnl(entry=self._entry(), exit_fill=exit_fill)
        assert pnl == Decimal("-5.21")

    def test_breakeven_minus_fees(self) -> None:
        # Buy and sell at same price : PnL = 0 - fees.
        exit_fill = AdversarialFill(
            side=Side.SHORT,
            signal_price=Decimal("100"),
            worst_bar_price=Decimal("100"),
            fill_price=Decimal("100"),
            quantity=Decimal("1"),
            fee=Decimal("0.1"),
            slippage_cost=Decimal("0"),
        )
        pnl = compute_realized_pnl(entry=self._entry(), exit_fill=exit_fill)
        assert pnl == Decimal("-0.2")  # 0 - (0.1 + 0.1)


# ─── compute_realized_pnl : SHORT round-trip ───────────────────────────────


@pytest.mark.unit
class TestComputeRealizedPnlShort:
    def _entry(self) -> AdversarialFill:
        # SELL at 100, 1 unit, 0.1 fee.
        return AdversarialFill(
            side=Side.SHORT,
            signal_price=Decimal("101"),
            worst_bar_price=Decimal("100"),
            fill_price=Decimal("100"),
            quantity=Decimal("1"),
            fee=Decimal("0.1"),
            slippage_cost=Decimal("0"),
        )

    def test_short_winner(self) -> None:
        # BUY back at 90 : PnL = (100 - 90)*1 - 0.2 = 9.8.
        exit_fill = AdversarialFill(
            side=Side.LONG,
            signal_price=Decimal("90"),
            worst_bar_price=Decimal("90"),
            fill_price=Decimal("90"),
            quantity=Decimal("1"),
            fee=Decimal("0.1"),
            slippage_cost=Decimal("0"),
        )
        pnl = compute_realized_pnl(entry=self._entry(), exit_fill=exit_fill)
        assert pnl == Decimal("9.8")

    def test_short_loser(self) -> None:
        # BUY back at 110 : PnL = (100 - 110)*1 - 0.2 = -10.2.
        exit_fill = AdversarialFill(
            side=Side.LONG,
            signal_price=Decimal("110"),
            worst_bar_price=Decimal("110"),
            fill_price=Decimal("110"),
            quantity=Decimal("1"),
            fee=Decimal("0.1"),
            slippage_cost=Decimal("0"),
        )
        pnl = compute_realized_pnl(entry=self._entry(), exit_fill=exit_fill)
        assert pnl == Decimal("-10.2")


# ─── compute_realized_pnl : validation ─────────────────────────────────────


@pytest.mark.unit
class TestComputeRealizedPnlValidation:
    def _make_long(self, *, qty: Decimal = Decimal("1")) -> AdversarialFill:
        return AdversarialFill(
            side=Side.LONG,
            signal_price=Decimal("100"),
            worst_bar_price=Decimal("100"),
            fill_price=Decimal("100"),
            quantity=qty,
            fee=Decimal("0"),
            slippage_cost=Decimal("0"),
        )

    def _make_short(self, *, qty: Decimal = Decimal("1")) -> AdversarialFill:
        return AdversarialFill(
            side=Side.SHORT,
            signal_price=Decimal("100"),
            worst_bar_price=Decimal("100"),
            fill_price=Decimal("100"),
            quantity=qty,
            fee=Decimal("0"),
            slippage_cost=Decimal("0"),
        )

    def test_same_side_rejected(self) -> None:
        with pytest.raises(ValueError, match="opposite sides"):
            compute_realized_pnl(
                entry=self._make_long(),
                exit_fill=self._make_long(),
            )

    def test_quantity_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="quantity must match"):
            compute_realized_pnl(
                entry=self._make_long(qty=Decimal("1")),
                exit_fill=self._make_short(qty=Decimal("2")),
            )


# ─── End-to-end : doc 10 R2 defaults ───────────────────────────────────────


@pytest.mark.unit
class TestEndToEnd:
    def test_full_roundtrip_with_defaults(self) -> None:
        # Realistic LONG round-trip with all four pessimisms applied.
        # Signal BUY @ 100 on bar T ; execution bar T+1: H=102, L=99.
        # BUY fill = 102 * 1.001 = 102.102 ; fee = 102.102 * 1 * 0.0011
        # = 0.11231...
        # Signal SELL @ 110 on bar U ; execution bar U+1: H=111, L=109.
        # SELL fill = 109 * 0.999 = 108.891 ; fee = 108.891 * 0.0011
        # = 0.11978...
        # PnL = (108.891 - 102.102) * 1 - (0.11231 + 0.11978) = 6.557.
        entry_bar = _kline(high=Decimal("102"), low=Decimal("99"))
        exit_bar = _kline(high=Decimal("111"), low=Decimal("109"))
        entry = apply_adversarial_fill(
            signal_price=Decimal("100"),
            side=Side.LONG,
            execution_bar=entry_bar,
            quantity=Decimal("1"),
        )
        exit_fill = apply_adversarial_fill(
            signal_price=Decimal("110"),
            side=Side.SHORT,
            execution_bar=exit_bar,
            quantity=Decimal("1"),
        )
        pnl = compute_realized_pnl(entry=entry, exit_fill=exit_fill)
        # Approximate check : net PnL is well below the naive 10
        # reading "100 to 110 = +10" — pessimisms eat ~3.4 USD.
        assert Decimal("6") < pnl < Decimal("7")

    def test_pessimisms_strictly_worse_than_ideal(self) -> None:
        # Same trade with all pessimisms = 0 (ideal) yields the
        # naive PnL ; with R2 defaults, PnL is strictly lower.
        bar_entry = _kline(high=Decimal("100"), low=Decimal("99"))
        bar_exit = _kline(high=Decimal("110"), low=Decimal("109"))
        ideal_params = AdversarialParams(
            slippage_pct=Decimal("0"),
            fee_pct=Decimal("0"),
        )
        ideal_entry = apply_adversarial_fill(
            signal_price=Decimal("99"),
            side=Side.LONG,
            execution_bar=bar_entry,
            quantity=Decimal("1"),
            params=ideal_params,
        )
        ideal_exit = apply_adversarial_fill(
            signal_price=Decimal("110"),
            side=Side.SHORT,
            execution_bar=bar_exit,
            quantity=Decimal("1"),
            params=ideal_params,
        )
        ideal_pnl = compute_realized_pnl(entry=ideal_entry, exit_fill=ideal_exit)

        adv_entry = apply_adversarial_fill(
            signal_price=Decimal("99"),
            side=Side.LONG,
            execution_bar=bar_entry,
            quantity=Decimal("1"),
        )
        adv_exit = apply_adversarial_fill(
            signal_price=Decimal("110"),
            side=Side.SHORT,
            execution_bar=bar_exit,
            quantity=Decimal("1"),
        )
        adv_pnl = compute_realized_pnl(entry=adv_entry, exit_fill=adv_exit)
        assert adv_pnl < ideal_pnl
