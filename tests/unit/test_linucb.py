"""Unit tests for emeraude.agent.learning.linucb."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.linucb import (
    DEFAULT_ALPHA,
    DEFAULT_LAMBDA_REG,
    LinUCBArmState,
    LinUCBBandit,
    _dot,
    _eye,
    _matvec,
    _outer,
    _scalar_mat,
    _sherman_morrison_update,
)

_TOL = Decimal("1E-10")


def _close(actual: Decimal, expected: Decimal, *, tol: Decimal = _TOL) -> bool:
    return abs(actual - expected) <= tol


# ─── Defaults ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_alpha_default(self) -> None:
        # Doc 10 R14 / Li et al. 2010 : moderate exploration alpha = 1.
        assert Decimal("1.0") == DEFAULT_ALPHA

    def test_lambda_reg_default(self) -> None:
        # Ridge regularization moderate.
        assert Decimal("1.0") == DEFAULT_LAMBDA_REG


# ─── Algebra helpers ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEye:
    def test_dim_one(self) -> None:
        assert _eye(1) == [[Decimal("1")]]

    def test_dim_three_default_scale(self) -> None:
        eye3 = _eye(3)
        assert eye3 == [
            [Decimal("1"), Decimal("0"), Decimal("0")],
            [Decimal("0"), Decimal("1"), Decimal("0")],
            [Decimal("0"), Decimal("0"), Decimal("1")],
        ]

    def test_custom_scale(self) -> None:
        eye2 = _eye(2, scale=Decimal("0.5"))
        assert eye2 == [
            [Decimal("0.5"), Decimal("0")],
            [Decimal("0"), Decimal("0.5")],
        ]


@pytest.mark.unit
class TestMatvec:
    def test_identity_is_identity(self) -> None:
        eye = _eye(3)
        v = [Decimal("1"), Decimal("2"), Decimal("3")]
        assert _matvec(eye, v) == v

    def test_known_product(self) -> None:
        # M = [[1, 2], [3, 4]] ; v = [5, 6] -> [17, 39].
        m = [[Decimal("1"), Decimal("2")], [Decimal("3"), Decimal("4")]]
        v = [Decimal("5"), Decimal("6")]
        assert _matvec(m, v) == [Decimal("17"), Decimal("39")]


@pytest.mark.unit
class TestDot:
    def test_orthogonal(self) -> None:
        u = [Decimal("1"), Decimal("0")]
        v = [Decimal("0"), Decimal("1")]
        assert _dot(u, v) == Decimal("0")

    def test_known_product(self) -> None:
        u = [Decimal("1"), Decimal("2"), Decimal("3")]
        v = [Decimal("4"), Decimal("5"), Decimal("6")]
        # 1*4 + 2*5 + 3*6 = 32.
        assert _dot(u, v) == Decimal("32")


@pytest.mark.unit
class TestOuter:
    def test_outer_3_2(self) -> None:
        u = [Decimal("1"), Decimal("2"), Decimal("3")]
        v = [Decimal("4"), Decimal("5")]
        assert _outer(u, v) == [
            [Decimal("4"), Decimal("5")],
            [Decimal("8"), Decimal("10")],
            [Decimal("12"), Decimal("15")],
        ]


@pytest.mark.unit
class TestScalarMat:
    def test_zero_yields_zero_matrix(self) -> None:
        m = [[Decimal("1"), Decimal("2")], [Decimal("3"), Decimal("4")]]
        assert _scalar_mat(Decimal("0"), m) == [
            [Decimal("0"), Decimal("0")],
            [Decimal("0"), Decimal("0")],
        ]

    def test_double(self) -> None:
        m = [[Decimal("1"), Decimal("2")]]
        assert _scalar_mat(Decimal("2"), m) == [[Decimal("2"), Decimal("4")]]


# ─── Sherman-Morrison update ───────────────────────────────────────────────


@pytest.mark.unit
class TestShermanMorrison:
    def test_inverse_remains_inverse_of_updated(self) -> None:
        # Start with A = 2*I, A^{-1} = 0.5*I. Add x x^T where
        # x = [1, 0]. New A = [[3, 0], [0, 2]], so A^{-1} = [[1/3, 0], [0, 0.5]].
        a_inv = _eye(2, scale=Decimal("0.5"))
        x = [Decimal("1"), Decimal("0")]
        new_inv = _sherman_morrison_update(a_inv, x)
        assert _close(new_inv[0][0], Decimal("1") / Decimal("3"), tol=Decimal("1E-15"))
        assert new_inv[0][1] == Decimal("0")
        assert new_inv[1][0] == Decimal("0")
        assert new_inv[1][1] == Decimal("0.5")

    def test_general_update(self) -> None:
        # Start with A = I, A^{-1} = I. Add x x^T where x = [1, 1].
        # New A = [[2, 1], [1, 2]], det = 3, A^{-1} = [[2/3, -1/3], [-1/3, 2/3]].
        a_inv = _eye(2)
        x = [Decimal("1"), Decimal("1")]
        new_inv = _sherman_morrison_update(a_inv, x)
        expected_diag = Decimal("2") / Decimal("3")
        expected_off = -Decimal("1") / Decimal("3")
        assert _close(new_inv[0][0], expected_diag, tol=Decimal("1E-15"))
        assert _close(new_inv[1][1], expected_diag, tol=Decimal("1E-15"))
        assert _close(new_inv[0][1], expected_off, tol=Decimal("1E-15"))
        assert _close(new_inv[1][0], expected_off, tol=Decimal("1E-15"))


# ─── LinUCBBandit construction validation ──────────────────────────────────


@pytest.mark.unit
class TestConstruction:
    def test_default_params(self) -> None:
        bandit = LinUCBBandit(arms=["a", "b"], context_dim=3)
        state = bandit.state()
        assert set(state.keys()) == {"a", "b"}
        for arm in state.values():
            assert arm.n_updates == 0
            assert arm.theta == [Decimal("0"), Decimal("0"), Decimal("0")]

    def test_empty_arms_rejected(self) -> None:
        with pytest.raises(ValueError, match="arms must not be empty"):
            LinUCBBandit(arms=[], context_dim=2)

    def test_duplicate_arms_rejected(self) -> None:
        with pytest.raises(ValueError, match="arms must be unique"):
            LinUCBBandit(arms=["a", "a", "b"], context_dim=2)

    def test_zero_context_dim_rejected(self) -> None:
        with pytest.raises(ValueError, match="context_dim must be >= 1"):
            LinUCBBandit(arms=["a"], context_dim=0)

    def test_negative_alpha_rejected(self) -> None:
        with pytest.raises(ValueError, match="alpha must be > 0"):
            LinUCBBandit(arms=["a"], context_dim=2, alpha=Decimal("-1"))

    def test_zero_alpha_rejected(self) -> None:
        with pytest.raises(ValueError, match="alpha must be > 0"):
            LinUCBBandit(arms=["a"], context_dim=2, alpha=Decimal("0"))

    def test_zero_lambda_rejected(self) -> None:
        with pytest.raises(ValueError, match="lambda_reg must be > 0"):
            LinUCBBandit(arms=["a"], context_dim=2, lambda_reg=Decimal("0"))


# ─── select ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSelect:
    def test_initial_tie_breaks_alphabetically(self) -> None:
        # All arms have the same priors -> alphabetical wins.
        bandit = LinUCBBandit(arms=["zebra", "alpha", "mango"], context_dim=2)
        ctx = [Decimal("1"), Decimal("0")]
        assert bandit.select(ctx) == "alpha"

    def test_context_dim_mismatch_rejected(self) -> None:
        bandit = LinUCBBandit(arms=["a"], context_dim=3)
        with pytest.raises(ValueError, match="dimension 3"):
            bandit.select([Decimal("1"), Decimal("2")])  # length 2

    def test_select_returns_arm_with_history(self) -> None:
        # Train arm "good" with positive rewards on a context.
        bandit = LinUCBBandit(arms=["good", "bad"], context_dim=2)
        ctx = [Decimal("1"), Decimal("0")]
        for _ in range(5):
            bandit.update(arm="good", context=ctx, reward=Decimal("1"))
            bandit.update(arm="bad", context=ctx, reward=Decimal("-1"))
        # On the same context, "good" should win.
        assert bandit.select(ctx) == "good"


# ─── update ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestUpdate:
    def test_unknown_arm_rejected(self) -> None:
        bandit = LinUCBBandit(arms=["a"], context_dim=2)
        with pytest.raises(ValueError, match="unknown arm"):
            bandit.update(
                arm="ghost",
                context=[Decimal("1"), Decimal("0")],
                reward=Decimal("1"),
            )

    def test_update_increments_n_updates(self) -> None:
        bandit = LinUCBBandit(arms=["a"], context_dim=2)
        ctx = [Decimal("1"), Decimal("0")]
        for i in range(5):
            bandit.update(arm="a", context=ctx, reward=Decimal("1"))
            assert bandit.state()["a"].n_updates == i + 1

    def test_context_dim_mismatch_rejected(self) -> None:
        bandit = LinUCBBandit(arms=["a"], context_dim=2)
        with pytest.raises(ValueError, match="dimension 2"):
            bandit.update(
                arm="a",
                context=[Decimal("1")],  # wrong length
                reward=Decimal("1"),
            )

    def test_update_changes_theta(self) -> None:
        bandit = LinUCBBandit(arms=["a"], context_dim=2)
        before = bandit.state()["a"].theta
        bandit.update(arm="a", context=[Decimal("1"), Decimal("0")], reward=Decimal("1"))
        after = bandit.state()["a"].theta
        assert before != after


# ─── Convergence behavior ──────────────────────────────────────────────────


@pytest.mark.unit
class TestConvergence:
    def test_single_arm_recovers_linear_signal(self) -> None:
        # Reward = 2 * context[0] + 0 * context[1]. With many samples
        # the bandit's theta should approach [2, 0] (modulo ridge bias).
        bandit = LinUCBBandit(arms=["a"], context_dim=2, lambda_reg=Decimal("0.01"))
        # Sample contexts varying first feature, second feature noise zero.
        for x0 in (Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")):
            for _ in range(20):  # many repetitions to pin down theta
                ctx = [x0, Decimal("0")]
                bandit.update(arm="a", context=ctx, reward=Decimal("2") * x0)
        theta = bandit.state()["a"].theta
        # Should converge to roughly [2, 0].
        assert _close(theta[0], Decimal("2"), tol=Decimal("0.01"))
        # Second feature was always 0 -> theta[1] stays 0.
        assert theta[1] == Decimal("0")

    def test_arms_specialize_to_their_rewards(self) -> None:
        # Two arms with opposite sign rewards on the same context.
        bandit = LinUCBBandit(arms=["plus", "minus"], context_dim=1)
        ctx = [Decimal("1")]
        for _ in range(20):
            bandit.update(arm="plus", context=ctx, reward=Decimal("1"))
            bandit.update(arm="minus", context=ctx, reward=Decimal("-1"))
        state = bandit.state()
        # plus theta > 0, minus theta < 0.
        assert state["plus"].theta[0] > Decimal("0")
        assert state["minus"].theta[0] < Decimal("0")


# ─── Exploration behavior ──────────────────────────────────────────────────


@pytest.mark.unit
class TestExploration:
    def test_under_explored_arm_has_higher_bonus(self) -> None:
        # Both arms learn the same reward magnitude on the same context
        # but "explored" gets 100 updates while "fresh" gets 1 update.
        # The UCB bonus on "fresh" should be larger -> in a tied-mean
        # scenario, "fresh" wins on exploration alone.
        bandit = LinUCBBandit(
            arms=["explored", "fresh"],
            context_dim=1,
            alpha=Decimal("2"),  # generous exploration weight
        )
        ctx = [Decimal("1")]
        for _ in range(100):
            bandit.update(arm="explored", context=ctx, reward=Decimal("0"))
        # One small reward for "fresh" matching "explored".
        bandit.update(arm="fresh", context=ctx, reward=Decimal("0"))

        # Both means are 0 ; bonus picks the one with less data ("fresh").
        # (alphabetical tie would pick "explored" so bonus > 0 must
        # actually fire here for "fresh" to win.)
        assert bandit.select(ctx) == "fresh"

    def test_alpha_zero_ish_disables_bonus(self) -> None:
        # Very small alpha : exploration negligible, mean dominates.
        bandit = LinUCBBandit(
            arms=["a", "b"],
            context_dim=1,
            alpha=Decimal("0.001"),  # tiny exploration
        )
        ctx = [Decimal("1")]
        # Train "a" with positive reward, "b" with negative.
        for _ in range(20):
            bandit.update(arm="a", context=ctx, reward=Decimal("1"))
            bandit.update(arm="b", context=ctx, reward=Decimal("-1"))
        # Mean dominates -> "a" picked even though bonus would push
        # to under-explored regions if alpha were large.
        assert bandit.select(ctx) == "a"


# ─── State snapshot ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestState:
    def test_returns_one_entry_per_arm(self) -> None:
        bandit = LinUCBBandit(arms=["a", "b", "c"], context_dim=2)
        state = bandit.state()
        assert set(state.keys()) == {"a", "b", "c"}

    def test_arm_state_frozen(self) -> None:
        bandit = LinUCBBandit(arms=["a"], context_dim=2)
        arm_state = bandit.state()["a"]
        assert isinstance(arm_state, LinUCBArmState)
        with pytest.raises(AttributeError):
            arm_state.n_updates = 99  # type: ignore[misc]

    def test_initial_theta_is_zero_vector(self) -> None:
        # Fresh bandit : b = 0 -> theta = A_inv @ 0 = 0.
        bandit = LinUCBBandit(arms=["a"], context_dim=3)
        assert bandit.state()["a"].theta == [Decimal("0")] * 3


# ─── End-to-end : doc 10 R14 narrative ─────────────────────────────────────


@pytest.mark.unit
class TestDoc10R14Narrative:
    def test_context_dependent_specialization(self) -> None:
        # Arm "trend_follower" performs well in BULL regime, badly in
        # BEAR. Arm "mean_reversion" the opposite. Context = [is_bull,
        # is_bear, vol]. The bandit should learn which arm to pick
        # given the regime.
        bandit = LinUCBBandit(
            arms=["trend_follower", "mean_reversion"],
            context_dim=3,
        )
        bull_ctx = [Decimal("1"), Decimal("0"), Decimal("0.02")]
        bear_ctx = [Decimal("0"), Decimal("1"), Decimal("0.04")]

        for _ in range(15):
            # Trend wins in bull, loses in bear.
            bandit.update(arm="trend_follower", context=bull_ctx, reward=Decimal("2"))
            bandit.update(arm="trend_follower", context=bear_ctx, reward=Decimal("-1.5"))
            # Mean rev opposite.
            bandit.update(arm="mean_reversion", context=bull_ctx, reward=Decimal("-0.5"))
            bandit.update(arm="mean_reversion", context=bear_ctx, reward=Decimal("1.5"))

        # In bull, trend should win.
        assert bandit.select(bull_ctx) == "trend_follower"
        # In bear, mean reversion should win.
        assert bandit.select(bear_ctx) == "mean_reversion"
