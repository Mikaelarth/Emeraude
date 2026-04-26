"""Unit tests for emeraude.agent.reasoning.risk_manager."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.reasoning.risk_manager import (
    DEFAULT_MIN_RR,
    DEFAULT_STOP_ATR_MULTIPLIER,
    DEFAULT_TARGET_ATR_MULTIPLIER,
    Side,
    TradeLevels,
    compute_levels,
    is_acceptable_rr,
)

# ─── Defaults ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_stop_multiplier_default_is_two(self) -> None:
        assert Decimal("2") == DEFAULT_STOP_ATR_MULTIPLIER

    def test_target_multiplier_default_is_four(self) -> None:
        # Forces nominal R/R = 4/2 = 2.0 (doc 04 "force le R/R a 2.0").
        assert Decimal("4") == DEFAULT_TARGET_ATR_MULTIPLIER

    def test_min_rr_default_is_one_point_five(self) -> None:
        # Anti-rule A4 floor.
        assert Decimal("1.5") == DEFAULT_MIN_RR


# ─── compute_levels ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestComputeLevelsLong:
    def test_stop_below_entry_target_above(self) -> None:
        lv = compute_levels(entry=Decimal("100"), atr=Decimal("1"), side=Side.LONG)
        assert lv.entry == Decimal("100")
        assert lv.stop == Decimal("98")  # 100 - 2 * 1
        assert lv.target == Decimal("104")  # 100 + 4 * 1

    def test_risk_reward_match_distances(self) -> None:
        lv = compute_levels(entry=Decimal("100"), atr=Decimal("1"), side=Side.LONG)
        assert lv.risk_per_unit == Decimal("2")
        assert lv.reward_per_unit == Decimal("4")
        assert lv.r_multiple == Decimal("2")

    def test_custom_multipliers(self) -> None:
        lv = compute_levels(
            entry=Decimal("100"),
            atr=Decimal("1"),
            side=Side.LONG,
            stop_atr_multiplier=Decimal("1.5"),
            target_atr_multiplier=Decimal("3"),
        )
        assert lv.stop == Decimal("98.5")
        assert lv.target == Decimal("103")
        assert lv.r_multiple == Decimal("2")  # 3 / 1.5


@pytest.mark.unit
class TestComputeLevelsShort:
    def test_stop_above_entry_target_below(self) -> None:
        lv = compute_levels(entry=Decimal("100"), atr=Decimal("1"), side=Side.SHORT)
        assert lv.entry == Decimal("100")
        assert lv.stop == Decimal("102")  # 100 + 2 * 1
        assert lv.target == Decimal("96")  # 100 - 4 * 1

    def test_short_risk_reward_positive(self) -> None:
        lv = compute_levels(entry=Decimal("100"), atr=Decimal("1"), side=Side.SHORT)
        # Distances are absolute values.
        assert lv.risk_per_unit == Decimal("2")
        assert lv.reward_per_unit == Decimal("4")
        assert lv.r_multiple == Decimal("2")


# ─── Edge cases ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEdgeCases:
    def test_zero_atr_yields_zero_distances_and_infinite_r(self) -> None:
        lv = compute_levels(entry=Decimal("100"), atr=Decimal("0"), side=Side.LONG)
        assert lv.stop == Decimal("100")
        assert lv.target == Decimal("100")
        assert lv.risk_per_unit == Decimal("0")
        assert lv.reward_per_unit == Decimal("0")
        assert lv.r_multiple == Decimal("Infinity")

    def test_zero_stop_multiplier_yields_infinite_r(self) -> None:
        # Risk = 0 even with non-zero ATR -> degenerate, surfaced as
        # +Infinity so callers can detect.
        lv = compute_levels(
            entry=Decimal("100"),
            atr=Decimal("1"),
            side=Side.LONG,
            stop_atr_multiplier=Decimal("0"),
        )
        assert lv.r_multiple == Decimal("Infinity")

    def test_zero_target_multiplier_yields_zero_r(self) -> None:
        lv = compute_levels(
            entry=Decimal("100"),
            atr=Decimal("1"),
            side=Side.LONG,
            target_atr_multiplier=Decimal("0"),
        )
        assert lv.target == Decimal("100")
        assert lv.r_multiple == Decimal("0")


# ─── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_zero_entry_rejected(self) -> None:
        with pytest.raises(ValueError, match="entry must be > 0"):
            compute_levels(entry=Decimal("0"), atr=Decimal("1"), side=Side.LONG)

    def test_negative_entry_rejected(self) -> None:
        with pytest.raises(ValueError, match="entry must be > 0"):
            compute_levels(entry=Decimal("-1"), atr=Decimal("1"), side=Side.LONG)

    def test_negative_atr_rejected(self) -> None:
        with pytest.raises(ValueError, match="atr must be >= 0"):
            compute_levels(entry=Decimal("100"), atr=Decimal("-0.5"), side=Side.LONG)

    def test_negative_stop_multiplier_rejected(self) -> None:
        with pytest.raises(ValueError, match="stop_atr_multiplier must be >= 0"):
            compute_levels(
                entry=Decimal("100"),
                atr=Decimal("1"),
                side=Side.LONG,
                stop_atr_multiplier=Decimal("-1"),
            )

    def test_negative_target_multiplier_rejected(self) -> None:
        with pytest.raises(ValueError, match="target_atr_multiplier must be >= 0"):
            compute_levels(
                entry=Decimal("100"),
                atr=Decimal("1"),
                side=Side.LONG,
                target_atr_multiplier=Decimal("-1"),
            )


# ─── is_acceptable_rr ────────────────────────────────────────────────────────


def _levels(rr: float) -> TradeLevels:
    return TradeLevels(
        side=Side.LONG,
        entry=Decimal("100"),
        stop=Decimal("99"),
        target=Decimal("100") + Decimal(str(rr)),
        risk_per_unit=Decimal("1"),
        reward_per_unit=Decimal(str(rr)),
        r_multiple=Decimal(str(rr)),
    )


@pytest.mark.unit
class TestAcceptableRR:
    def test_above_floor_accepted(self) -> None:
        assert is_acceptable_rr(_levels(2.0)) is True

    def test_at_floor_accepted(self) -> None:
        # Floor is inclusive : R == 1.5 must pass.
        assert is_acceptable_rr(_levels(1.5)) is True

    def test_below_floor_rejected(self) -> None:
        assert is_acceptable_rr(_levels(1.49)) is False

    def test_zero_r_rejected(self) -> None:
        assert is_acceptable_rr(_levels(0)) is False

    def test_infinite_r_accepted(self) -> None:
        # Degenerate ATR=0 case ; callers should also reject when
        # risk=0, but the gate by itself accepts +Infinity.
        lv = TradeLevels(
            side=Side.LONG,
            entry=Decimal("100"),
            stop=Decimal("100"),
            target=Decimal("100"),
            risk_per_unit=Decimal("0"),
            reward_per_unit=Decimal("0"),
            r_multiple=Decimal("Infinity"),
        )
        assert is_acceptable_rr(lv) is True

    def test_custom_floor(self) -> None:
        # A higher floor (e.g. 2.0) tightens A4.
        assert is_acceptable_rr(_levels(1.7), min_rr=Decimal("2.0")) is False
        assert is_acceptable_rr(_levels(2.0), min_rr=Decimal("2.0")) is True

    def test_negative_floor_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_rr must be >= 0"):
            is_acceptable_rr(_levels(1.0), min_rr=Decimal("-0.1"))
