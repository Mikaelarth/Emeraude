"""Unit tests for emeraude.agent.learning.drift."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.drift import (
    AdwinDetector,
    AdwinState,
    PageHinkleyDetector,
    PageHinkleyState,
)

# ─── Page-Hinkley : construction & validation ───────────────────────────────


@pytest.mark.unit
class TestPageHinkleyConstruction:
    def test_default_state_is_clean(self) -> None:
        d = PageHinkleyDetector()
        s = d.state()
        assert s.n_samples == 0
        assert s.running_mean == Decimal("0")
        assert s.cumulative_sum == Decimal("0")
        assert s.drift_detected is False
        assert d.detected is False

    def test_zero_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match="delta must be > 0"):
            PageHinkleyDetector(delta=Decimal("0"))

    def test_negative_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match="delta must be > 0"):
            PageHinkleyDetector(delta=Decimal("-0.1"))

    def test_zero_threshold_rejected(self) -> None:
        with pytest.raises(ValueError, match="threshold must be > 0"):
            PageHinkleyDetector(threshold=Decimal("0"))


# ─── Page-Hinkley : behavior ────────────────────────────────────────────────


@pytest.mark.unit
class TestPageHinkleyBehavior:
    def test_constant_values_no_drift(self) -> None:
        # All identical samples : no deviation ever, no drift.
        d = PageHinkleyDetector(delta=Decimal("0.005"), threshold=Decimal("5"))
        for _ in range(50):
            assert d.update(Decimal("1.0")) is False
        assert d.detected is False
        assert d.state().cumulative_sum == Decimal("0")

    def test_winning_then_losing_stream_triggers(self) -> None:
        # 50 wins of +2R then a long sequence of losses : the running
        # mean stays high, losing samples pull cumsum upward, alarm fires.
        d = PageHinkleyDetector(delta=Decimal("0.005"), threshold=Decimal("5"))
        for _ in range(50):
            d.update(Decimal("2.0"))
        fired = False
        for _ in range(50):
            if d.update(Decimal("-2.0")):
                fired = True
                break
        assert fired
        assert d.detected is True

    def test_drift_flag_sticky_until_reset(self) -> None:
        d = PageHinkleyDetector(delta=Decimal("0.005"), threshold=Decimal("0.5"))
        # Trip the alarm with one big drop relative to a stable base.
        for _ in range(20):
            d.update(Decimal("1.0"))
        # Successive drops accumulate.
        for _ in range(20):
            d.update(Decimal("0.0"))
        # Snapshot before reset captures the post-trip state.
        snapshot_before = d.state()
        assert snapshot_before.drift_detected is True
        # Reset : state cleared. Capture a fresh snapshot to avoid
        # mypy's narrowing of `d.detected` across the reset boundary.
        d.reset()
        snapshot_after = d.state()
        assert snapshot_after.drift_detected is False
        assert snapshot_after.n_samples == 0
        assert snapshot_after.cumulative_sum == Decimal("0")

    def test_alarm_returns_true_only_first_time(self) -> None:
        # update() returns True only on the step the alarm fires ;
        # subsequent calls return False even though detected stays True.
        d = PageHinkleyDetector(delta=Decimal("0.005"), threshold=Decimal("0.5"))
        for _ in range(20):
            d.update(Decimal("1.0"))
        first_fire = None
        for i in range(20):
            if d.update(Decimal("0.0")):
                first_fire = i
                break
        assert first_fire is not None
        # After the fire, more calls return False but detected stays True.
        for _ in range(5):
            assert d.update(Decimal("0.0")) is False
        assert d.detected is True

    def test_running_mean_tracks_input(self) -> None:
        d = PageHinkleyDetector()
        for v in (Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")):
            d.update(v)
        assert d.state().running_mean == Decimal("2.5")
        assert d.state().n_samples == 4

    def test_state_returns_frozen_snapshot(self) -> None:
        d = PageHinkleyDetector()
        s = d.state()
        assert isinstance(s, PageHinkleyState)
        with pytest.raises(AttributeError):
            s.n_samples = 999  # type: ignore[misc]


# ─── ADWIN : construction & validation ──────────────────────────────────────


@pytest.mark.unit
class TestAdwinConstruction:
    def test_default_state_is_clean(self) -> None:
        d = AdwinDetector()
        s = d.state()
        assert s.window_size == 0
        assert s.running_mean == Decimal("0")
        assert s.drift_detected is False

    def test_invalid_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"delta must be in \(0, 1\)"):
            AdwinDetector(delta=Decimal("0"))
        with pytest.raises(ValueError, match=r"delta must be in \(0, 1\)"):
            AdwinDetector(delta=Decimal("1"))
        with pytest.raises(ValueError, match=r"delta must be in \(0, 1\)"):
            AdwinDetector(delta=Decimal("-0.1"))

    def test_too_small_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_window must be >= 4"):
            AdwinDetector(max_window=3)


# ─── ADWIN : behavior ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestAdwinBehavior:
    def test_warmup_no_drift(self) -> None:
        # With < min_window=4 samples, never fires.
        d = AdwinDetector()
        for _ in range(3):
            assert d.update(Decimal("1.0")) is False
        assert d.detected is False

    def test_constant_stream_no_drift(self) -> None:
        d = AdwinDetector()
        for _ in range(50):
            assert d.update(Decimal("1.0")) is False
        assert d.detected is False

    def test_abrupt_change_triggers(self) -> None:
        # Sharp shift from +2 to -2 : ADWIN with default delta should
        # trigger after a handful of post-shift samples.
        d = AdwinDetector(delta=Decimal("0.05"), max_window=100)
        for _ in range(30):
            d.update(Decimal("2.0"))
        fired = False
        for _ in range(30):
            if d.update(Decimal("-2.0")):
                fired = True
                break
        assert fired
        assert d.detected is True

    def test_window_truncated_after_drift(self) -> None:
        # When drift fires, the older sub-window is dropped : window
        # size shrinks immediately after the alarm.
        d = AdwinDetector(delta=Decimal("0.05"), max_window=100)
        for _ in range(30):
            d.update(Decimal("2.0"))
        size_before_drift = d.state().window_size
        assert size_before_drift == 30
        for _ in range(30):
            if d.update(Decimal("-2.0")):
                break
        size_after = d.state().window_size
        # The post-drift window contains only the new regime samples.
        assert size_after < size_before_drift

    def test_reset_clears_window(self) -> None:
        d = AdwinDetector(delta=Decimal("0.05"))
        for _ in range(20):
            d.update(Decimal("1.0"))
        for _ in range(20):
            d.update(Decimal("-1.0"))

        d.reset()
        s = d.state()
        assert s.window_size == 0
        assert s.drift_detected is False
        assert d.detected is False

    def test_max_window_bounds_memory(self) -> None:
        # Feed more than max_window samples : window stays bounded.
        d = AdwinDetector(delta=Decimal("0.5"), max_window=10)
        for _ in range(50):
            d.update(Decimal("1.0"))
        assert d.state().window_size <= 10

    def test_alarm_returns_true_only_first_time(self) -> None:
        # Same convention as Page-Hinkley : update() returns True only
        # on the step the alarm fires, even though detected stays True.
        d = AdwinDetector(delta=Decimal("0.05"), max_window=100)
        for _ in range(30):
            d.update(Decimal("2.0"))
        first_fire = None
        for i in range(30):
            if d.update(Decimal("-2.0")):
                first_fire = i
                break
        assert first_fire is not None
        # Subsequent calls return False ; detected stays True.
        post_fire_returns = [d.update(Decimal("-2.0")) for _ in range(5)]
        assert all(r is False for r in post_fire_returns)
        assert d.detected is True

    def test_state_returns_frozen_snapshot(self) -> None:
        d = AdwinDetector()
        d.update(Decimal("1"))
        s = d.state()
        assert isinstance(s, AdwinState)
        with pytest.raises(AttributeError):
            s.window_size = 999  # type: ignore[misc]

    def test_running_mean_after_updates(self) -> None:
        d = AdwinDetector(delta=Decimal("0.5"))
        for v in (Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")):
            d.update(v)
        # Without drift firing, the window holds all 4 samples.
        s = d.state()
        if not s.drift_detected:
            # Mean of [1,2,3,4] = 2.5.
            assert s.running_mean == Decimal("2.5")
            assert s.window_size == 4
