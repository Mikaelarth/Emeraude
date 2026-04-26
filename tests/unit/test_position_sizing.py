"""Unit tests for emeraude.agent.reasoning.position_sizing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.reasoning import position_sizing as ps

# ─── kelly_fraction ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestKellyFraction:
    def test_classic_50_50_two_to_one(self) -> None:
        # Standard textbook : p=0.5, b=2 → f = (0.5*2 - 0.5)/2 = 0.25.
        result = ps.kelly_fraction(Decimal("0.5"), Decimal("2"))
        assert result == Decimal("0.25")

    def test_full_win_rate_returns_one(self) -> None:
        # p=1 means every bet wins → bet the whole bankroll.
        result = ps.kelly_fraction(Decimal("1"), Decimal("2"))
        assert result == Decimal("1")

    def test_zero_win_rate_returns_zero(self) -> None:
        # p=0 means every bet loses → don't bet.
        result = ps.kelly_fraction(Decimal("0"), Decimal("2"))
        assert result == Decimal("0")

    def test_negative_ev_returns_zero(self) -> None:
        # p=0.3, b=1 → (0.3 - 0.7)/1 = -0.4 → coerced to 0.
        result = ps.kelly_fraction(Decimal("0.3"), Decimal("1"))
        assert result == Decimal("0")

    def test_break_even_returns_zero(self) -> None:
        # p=0.5, b=1 → (0.5 - 0.5)/1 = 0 → exactly break-even, don't bet.
        result = ps.kelly_fraction(Decimal("0.5"), Decimal("1"))
        assert result == Decimal("0")

    @pytest.mark.parametrize("win_rate", [Decimal("-0.01"), Decimal("1.01"), Decimal("2")])
    def test_win_rate_out_of_bounds_raises(self, win_rate: Decimal) -> None:
        with pytest.raises(ValueError, match="win_rate"):
            ps.kelly_fraction(win_rate, Decimal("2"))

    @pytest.mark.parametrize("ratio", [Decimal("0"), Decimal("-1")])
    def test_non_positive_ratio_raises(self, ratio: Decimal) -> None:
        with pytest.raises(ValueError, match="win_loss_ratio"):
            ps.kelly_fraction(Decimal("0.5"), ratio)


# ─── position_size : invalid inputs ─────────────────────────────────────────


@pytest.mark.unit
class TestPositionSizeInvalidInputs:
    def test_zero_capital_returns_zero(self) -> None:
        result = ps.position_size(
            capital=Decimal("0"),
            win_rate=Decimal("0.6"),
            win_loss_ratio=Decimal("2"),
            price=Decimal("100"),
            atr=Decimal("1"),
        )
        assert result == Decimal("0")

    def test_zero_price_returns_zero(self) -> None:
        result = ps.position_size(
            capital=Decimal("100"),
            win_rate=Decimal("0.6"),
            win_loss_ratio=Decimal("2"),
            price=Decimal("0"),
            atr=Decimal("1"),
        )
        assert result == Decimal("0")

    def test_negative_capital_returns_zero(self) -> None:
        result = ps.position_size(
            capital=Decimal("-50"),
            win_rate=Decimal("0.6"),
            win_loss_ratio=Decimal("2"),
            price=Decimal("100"),
            atr=Decimal("1"),
        )
        assert result == Decimal("0")

    def test_negative_atr_returns_zero(self) -> None:
        result = ps.position_size(
            capital=Decimal("100"),
            win_rate=Decimal("0.6"),
            win_loss_ratio=Decimal("2"),
            price=Decimal("100"),
            atr=Decimal("-1"),
        )
        assert result == Decimal("0")

    def test_negative_kelly_returns_zero(self) -> None:
        # Negative-EV trade : Kelly = 0 → position = 0.
        result = ps.position_size(
            capital=Decimal("100"),
            win_rate=Decimal("0.3"),
            win_loss_ratio=Decimal("1"),
            price=Decimal("100"),
            atr=Decimal("1"),
        )
        assert result == Decimal("0")

    @pytest.mark.parametrize("multiplier", [Decimal("-0.01"), Decimal("1.01")])
    def test_kelly_multiplier_out_of_bounds_raises(self, multiplier: Decimal) -> None:
        with pytest.raises(ValueError, match="kelly_multiplier"):
            ps.position_size(
                capital=Decimal("100"),
                win_rate=Decimal("0.6"),
                win_loss_ratio=Decimal("2"),
                price=Decimal("100"),
                atr=Decimal("1"),
                kelly_multiplier=multiplier,
            )

    def test_max_pct_per_trade_out_of_bounds_raises(self) -> None:
        with pytest.raises(ValueError, match="max_pct_per_trade"):
            ps.position_size(
                capital=Decimal("100"),
                win_rate=Decimal("0.6"),
                win_loss_ratio=Decimal("2"),
                price=Decimal("100"),
                atr=Decimal("1"),
                max_pct_per_trade=Decimal("1.5"),
            )

    def test_negative_vol_target_raises(self) -> None:
        with pytest.raises(ValueError, match="vol_target"):
            ps.position_size(
                capital=Decimal("100"),
                win_rate=Decimal("0.6"),
                win_loss_ratio=Decimal("2"),
                price=Decimal("100"),
                atr=Decimal("1"),
                vol_target=Decimal("-0.01"),
            )


# ─── position_size : caps binding ───────────────────────────────────────────


@pytest.mark.unit
class TestCapsBinding:
    def test_absolute_cap_binds_when_kelly_is_aggressive(self) -> None:
        # Full-Kelly with high win rate suggests a huge size ; the 5 % cap
        # should clamp it.
        position = ps.position_size(
            capital=Decimal("100"),
            win_rate=Decimal("0.9"),
            win_loss_ratio=Decimal("3"),
            price=Decimal("10"),
            atr=Decimal("0"),  # no vol info → cap binds
            kelly_multiplier=Decimal("1"),  # full Kelly
            max_pct_per_trade=Decimal("0.05"),
        )
        # 5 % of 100 USD = 5 USD ; at 10/unit → 0.5 units.
        assert position == Decimal("0.5")

    def test_vol_targeting_reduces_size_for_high_volatility(self) -> None:
        # Same setup, but with high ATR (10 % of price) the vol cap binds
        # well below the 5 % absolute cap.
        position_low_vol = ps.position_size(
            capital=Decimal("1000"),
            win_rate=Decimal("0.9"),
            win_loss_ratio=Decimal("3"),
            price=Decimal("100"),
            atr=Decimal("1"),  # 1 % of price
            kelly_multiplier=Decimal("1"),
            max_pct_per_trade=Decimal("0.5"),
            vol_target=Decimal("0.01"),
        )
        position_high_vol = ps.position_size(
            capital=Decimal("1000"),
            win_rate=Decimal("0.9"),
            win_loss_ratio=Decimal("3"),
            price=Decimal("100"),
            atr=Decimal("10"),  # 10 % of price (very volatile)
            kelly_multiplier=Decimal("1"),
            max_pct_per_trade=Decimal("0.5"),
            vol_target=Decimal("0.01"),
        )
        assert position_high_vol < position_low_vol

    def test_zero_atr_uses_absolute_cap(self) -> None:
        position = ps.position_size(
            capital=Decimal("100"),
            win_rate=Decimal("0.9"),
            win_loss_ratio=Decimal("3"),
            price=Decimal("10"),
            atr=Decimal("0"),
            max_pct_per_trade=Decimal("0.10"),
        )
        # Cap = 10 USD, price = 10 → 1 unit.
        # Kelly full * 0.5 (half-Kelly default) on capital 100 = 50 * Kelly value.
        # Kelly(0.9, 3) = (0.9*3 - 0.1)/3 = 2.6/3 ≈ 0.867 → *0.5 = 0.433
        # → kelly_usd = 100 * 0.433 = 43.3 USD ; cap = 10 USD → cap binds.
        assert position == Decimal("1")

    def test_kelly_multiplier_scales_position(self) -> None:
        common = {
            "capital": Decimal("1000"),
            "win_rate": Decimal("0.6"),
            "win_loss_ratio": Decimal("2"),
            "price": Decimal("100"),
            "atr": Decimal("0"),
            "max_pct_per_trade": Decimal("1"),  # cap doesn't bind
        }
        full = ps.position_size(**common, kelly_multiplier=Decimal("1"))
        half = ps.position_size(**common, kelly_multiplier=Decimal("0.5"))
        quarter = ps.position_size(**common, kelly_multiplier=Decimal("0.25"))
        # Each step halves the position.
        assert half == full / Decimal("2")
        assert quarter == half / Decimal("2")


# ─── position_size : realistic 20 USD scenario ──────────────────────────────


@pytest.mark.unit
class TestRealistic20UsdScenario:
    """Sanity checks with the user's actual 20 USD capital constraint."""

    def test_default_caps_yield_modest_size(self) -> None:
        # Champion stats from doc 04 : Sharpe +0.93, win rate 40 %,
        # PF 3.03 → R/R ~ 1.5 (rough). Capital 20 USD.
        position = ps.position_size(
            capital=Decimal("20"),
            win_rate=Decimal("0.4"),
            win_loss_ratio=Decimal("1.5"),
            price=Decimal("60000"),  # BTCUSDT
            atr=Decimal("600"),  # 1 % daily ATR
        )
        # Negative-EV check : (0.4*1.5 - 0.6)/1.5 = 0.0 → trade refused.
        assert position == Decimal("0")

    def test_strong_signal_yields_capped_size(self) -> None:
        # Kelly with strong p=0.6, R/R=2 → f = (0.6*2 - 0.4)/2 = 0.4 → 40 %.
        # Half-Kelly = 20 %. With max_pct=5 %, cap binds.
        position = ps.position_size(
            capital=Decimal("20"),
            win_rate=Decimal("0.6"),
            win_loss_ratio=Decimal("2"),
            price=Decimal("100"),
            atr=Decimal("1"),  # 1 % daily ATR
        )
        # vol cap : 20 * 0.01 / 0.01 = 20 USD → too generous, cap absolu
        # binds at 5 % of 20 = 1 USD.
        # 1 USD / 100 = 0.01 unit.
        assert position == Decimal("0.01")
