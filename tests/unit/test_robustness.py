"""Unit tests for emeraude.agent.learning.robustness."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.learning.robustness import (
    DEFAULT_DESTRUCTION_THRESHOLD,
    DEFAULT_MAX_DESTRUCTIVE_FRACTION,
    DEFAULT_N_PER_SIDE,
    DEFAULT_PERTURBATION_PCT,
    ParamStability,
    PerturbationResult,
    RobustnessReport,
    compute_robustness_report,
    is_robust,
)

# ─── Defaults ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_perturbation_pct_default(self) -> None:
        # Doc 10 R4 : ±20 %.
        assert Decimal("0.20") == DEFAULT_PERTURBATION_PCT

    def test_destruction_threshold_default(self) -> None:
        # Doc 10 R4 : 30 % degradation = "destructive".
        assert Decimal("0.30") == DEFAULT_DESTRUCTION_THRESHOLD

    def test_max_destructive_fraction_default(self) -> None:
        # Doc 10 R4 I4 : <= 25 % for publishable champion.
        assert Decimal("0.25") == DEFAULT_MAX_DESTRUCTIVE_FRACTION

    def test_n_per_side_default(self) -> None:
        # 4 perturbations per param by default.
        assert DEFAULT_N_PER_SIDE == 2


# ─── compute_robustness_report : happy paths ───────────────────────────────


@pytest.mark.unit
class TestRobustnessReportHappyPath:
    def test_stable_objective_no_destruction(self) -> None:
        # Constant objective : every perturbation yields the baseline.
        def stable(_p: dict[str, Decimal]) -> Decimal:
            return Decimal("1.0")

        report = compute_robustness_report(
            baseline_score=Decimal("1.0"),
            baseline_params={"a": Decimal("10"), "b": Decimal("20")},
            objective_fn=stable,
        )
        assert report.total_perturbations == 8  # 2 params * 4 perturbations
        assert report.total_destructive == 0
        assert report.destructive_fraction == Decimal("0")
        assert report.n_params == 2

    def test_overfit_objective_full_destruction(self) -> None:
        # Returns baseline only at the exact unperturbed config.
        baseline = {"a": Decimal("10"), "b": Decimal("20")}

        def overfit(params: dict[str, Decimal]) -> Decimal:
            return Decimal("1.0") if params == baseline else Decimal("0.0")

        report = compute_robustness_report(
            baseline_score=Decimal("1.0"),
            baseline_params=baseline,
            objective_fn=overfit,
        )
        assert report.total_destructive == 8
        assert report.destructive_fraction == Decimal("1")

    def test_partial_robustness(self) -> None:
        # Half the perturbations are destructive : objective drops to 0.6
        # (40 % degradation, > 30 %) on negative offsets, stays at 1.0
        # on positive offsets.
        baseline = {"a": Decimal("10")}

        def partial(params: dict[str, Decimal]) -> Decimal:
            offset = (params["a"] - Decimal("10")) / Decimal("10")
            if offset < Decimal("0"):
                return Decimal("0.6")
            return Decimal("1.0")

        report = compute_robustness_report(
            baseline_score=Decimal("1.0"),
            baseline_params=baseline,
            objective_fn=partial,
        )
        # 4 perturbations on "a" : 2 negative offsets destructive,
        # 2 positive offsets fine.
        assert report.total_perturbations == 4
        assert report.total_destructive == 2
        assert report.destructive_fraction == Decimal("0.5")


# ─── Per-param breakdown ───────────────────────────────────────────────────


@pytest.mark.unit
class TestPerParamStability:
    def test_one_param_fragile_other_robust(self) -> None:
        # Fragile param 'a' : any perturbation drops score to 0.
        # Robust param 'b' : all perturbations keep score = baseline.
        baseline = {"a": Decimal("10"), "b": Decimal("20")}

        def asymmetric(params: dict[str, Decimal]) -> Decimal:
            if params["a"] != Decimal("10"):
                return Decimal("0")
            return Decimal("1.0")

        report = compute_robustness_report(
            baseline_score=Decimal("1.0"),
            baseline_params=baseline,
            objective_fn=asymmetric,
        )
        a_stab = next(p for p in report.per_param if p.param_name == "a")
        b_stab = next(p for p in report.per_param if p.param_name == "b")
        assert a_stab.destructive_fraction == Decimal("1")
        assert b_stab.destructive_fraction == Decimal("0")
        # Cohort fraction : 4 dest out of 8 = 0.5.
        assert report.destructive_fraction == Decimal("0.5")

    def test_worst_degradation_tracked(self) -> None:
        # Score scales with offset magnitude : larger perturbation =
        # larger degradation.
        baseline = {"a": Decimal("10")}

        def linear(params: dict[str, Decimal]) -> Decimal:
            offset = (params["a"] - Decimal("10")) / Decimal("10")
            # 1.0 - |offset|*2  -> baseline 1.0, drops linearly with |offset|.
            return Decimal("1.0") - abs(offset) * Decimal("2")

        report = compute_robustness_report(
            baseline_score=Decimal("1.0"),
            baseline_params=baseline,
            objective_fn=linear,
        )
        a_stab = report.per_param[0]
        # At offset ±0.20 : score = 1 - 0.4 = 0.6, degradation = 0.4.
        # At offset ±0.10 : score = 1 - 0.2 = 0.8, degradation = 0.2.
        assert a_stab.worst_degradation == Decimal("0.4")


# ─── Objective exception handling ──────────────────────────────────────────


@pytest.mark.unit
class TestObjectiveFailures:
    def test_failing_objective_treated_destructive(self) -> None:
        # Raises on every perturbation -> all destructive.
        def crashes(_params: dict[str, Decimal]) -> Decimal:
            msg = "objective broken"
            raise RuntimeError(msg)

        report = compute_robustness_report(
            baseline_score=Decimal("1.0"),
            baseline_params={"a": Decimal("10")},
            objective_fn=crashes,
        )
        # All 4 perturbations destructive ; perturbed_score forced to 0.
        assert report.total_destructive == 4
        for r in report.perturbations:
            assert r.perturbed_score == Decimal("0")
            assert r.is_destructive

    def test_partial_failures(self) -> None:
        # Crashes only on offset = -0.20 ; other 3 perturbations OK.
        baseline = {"a": Decimal("10")}

        def selective(params: dict[str, Decimal]) -> Decimal:
            if params["a"] == Decimal("8"):  # baseline 10 * (1 - 0.20)
                msg = "fragile zone"
                raise ValueError(msg)
            return Decimal("1.0")

        report = compute_robustness_report(
            baseline_score=Decimal("1.0"),
            baseline_params=baseline,
            objective_fn=selective,
        )
        # 1 destructive (the crash), 3 fine.
        assert report.total_destructive == 1
        assert report.destructive_fraction == Decimal("0.25")


# ─── Validation rejets ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def _ok_obj(self, _p: dict[str, Decimal]) -> Decimal:
        return Decimal("1.0")

    def test_zero_baseline_score_rejected(self) -> None:
        with pytest.raises(ValueError, match="baseline_score must be > 0"):
            compute_robustness_report(
                baseline_score=Decimal("0"),
                baseline_params={"a": Decimal("1")},
                objective_fn=self._ok_obj,
            )

    def test_negative_baseline_score_rejected(self) -> None:
        with pytest.raises(ValueError, match="baseline_score must be > 0"):
            compute_robustness_report(
                baseline_score=Decimal("-0.5"),
                baseline_params={"a": Decimal("1")},
                objective_fn=self._ok_obj,
            )

    def test_empty_params_rejected(self) -> None:
        with pytest.raises(ValueError, match="baseline_params must not be empty"):
            compute_robustness_report(
                baseline_score=Decimal("1"),
                baseline_params={},
                objective_fn=self._ok_obj,
            )

    def test_zero_perturbation_pct_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"perturbation_pct must be in \(0, 1\)"):
            compute_robustness_report(
                baseline_score=Decimal("1"),
                baseline_params={"a": Decimal("1")},
                objective_fn=self._ok_obj,
                perturbation_pct=Decimal("0"),
            )

    def test_one_perturbation_pct_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"perturbation_pct must be in \(0, 1\)"):
            compute_robustness_report(
                baseline_score=Decimal("1"),
                baseline_params={"a": Decimal("1")},
                objective_fn=self._ok_obj,
                perturbation_pct=Decimal("1"),
            )

    def test_zero_n_per_side_rejected(self) -> None:
        with pytest.raises(ValueError, match="n_per_side must be >= 1"):
            compute_robustness_report(
                baseline_score=Decimal("1"),
                baseline_params={"a": Decimal("1")},
                objective_fn=self._ok_obj,
                n_per_side=0,
            )

    def test_zero_destruction_threshold_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"destruction_threshold must be in \(0, 1\)"):
            compute_robustness_report(
                baseline_score=Decimal("1"),
                baseline_params={"a": Decimal("1")},
                objective_fn=self._ok_obj,
                destruction_threshold=Decimal("0"),
            )


# ─── Sweep mechanics ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestSweepMechanics:
    def test_n_per_side_two_yields_four_perturbations(self) -> None:
        # n_per_side=2 -> 4 perturbations per param.
        recorded: list[Decimal] = []

        def recording(params: dict[str, Decimal]) -> Decimal:
            recorded.append(params["a"])
            return Decimal("1")

        compute_robustness_report(
            baseline_score=Decimal("1"),
            baseline_params={"a": Decimal("10")},
            objective_fn=recording,
            n_per_side=2,
        )
        # Expected sweep : -0.20, -0.10, +0.10, +0.20 around 10.
        assert sorted(recorded) == [
            Decimal("8"),
            Decimal("9"),
            Decimal("11"),
            Decimal("12"),
        ]

    def test_n_per_side_one_yields_two_perturbations(self) -> None:
        recorded: list[Decimal] = []

        def recording(params: dict[str, Decimal]) -> Decimal:
            recorded.append(params["a"])
            return Decimal("1")

        compute_robustness_report(
            baseline_score=Decimal("1"),
            baseline_params={"a": Decimal("10")},
            objective_fn=recording,
            n_per_side=1,
        )
        # Only ±0.20 endpoints.
        assert sorted(recorded) == [Decimal("8"), Decimal("12")]

    def test_only_one_param_perturbed_at_a_time(self) -> None:
        # When sweeping "a", "b" should remain at its baseline value.
        observed_a: list[Decimal] = []
        observed_b: list[Decimal] = []

        def watcher(params: dict[str, Decimal]) -> Decimal:
            observed_a.append(params["a"])
            observed_b.append(params["b"])
            return Decimal("1")

        compute_robustness_report(
            baseline_score=Decimal("1"),
            baseline_params={"a": Decimal("10"), "b": Decimal("20")},
            objective_fn=watcher,
            n_per_side=1,  # 2 perturbations per param -> 4 total
        )
        # 4 calls : 2 perturbing 'a' (b stays 20), 2 perturbing 'b'
        # (a stays 10).
        assert observed_a.count(Decimal("10")) == 2
        assert observed_b.count(Decimal("20")) == 2

    def test_custom_perturbation_pct(self) -> None:
        recorded: list[Decimal] = []

        def recording(params: dict[str, Decimal]) -> Decimal:
            recorded.append(params["a"])
            return Decimal("1")

        compute_robustness_report(
            baseline_score=Decimal("1"),
            baseline_params={"a": Decimal("100")},
            objective_fn=recording,
            n_per_side=1,
            perturbation_pct=Decimal("0.50"),
        )
        # ±50 % of 100 -> 50 and 150.
        assert sorted(recorded) == [Decimal("50"), Decimal("150")]


# ─── is_robust ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestIsRobust:
    def _report(self, fraction: Decimal) -> RobustnessReport:
        return RobustnessReport(
            baseline_score=Decimal("1"),
            n_params=1,
            total_perturbations=4,
            total_destructive=int(fraction * Decimal("4")),
            destructive_fraction=fraction,
            per_param=[],
            perturbations=[],
        )

    def test_below_threshold_robust(self) -> None:
        assert is_robust(self._report(Decimal("0.20")))

    def test_at_threshold_robust(self) -> None:
        # Inclusive at 0.25.
        assert is_robust(self._report(Decimal("0.25")))

    def test_above_threshold_not_robust(self) -> None:
        assert not is_robust(self._report(Decimal("0.30")))

    def test_custom_threshold(self) -> None:
        # Strict 10 % rejects 0.20.
        assert not is_robust(
            self._report(Decimal("0.20")), max_destructive_fraction=Decimal("0.10")
        )
        # Loose 50 % accepts 0.30.
        assert is_robust(self._report(Decimal("0.30")), max_destructive_fraction=Decimal("0.50"))

    def test_invalid_threshold_rejected(self) -> None:
        report = self._report(Decimal("0.10"))
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            is_robust(report, max_destructive_fraction=Decimal("1.5"))
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            is_robust(report, max_destructive_fraction=Decimal("-0.1"))


# ─── Result types frozen ───────────────────────────────────────────────────


@pytest.mark.unit
class TestResultTypesFrozen:
    def test_perturbation_result_frozen(self) -> None:
        result = PerturbationResult(
            param_name="a",
            baseline_value=Decimal("10"),
            perturbed_value=Decimal("12"),
            offset_pct=Decimal("0.20"),
            baseline_score=Decimal("1"),
            perturbed_score=Decimal("0.8"),
            degradation=Decimal("0.2"),
            is_destructive=False,
        )
        with pytest.raises(AttributeError):
            result.is_destructive = True  # type: ignore[misc]

    def test_param_stability_frozen(self) -> None:
        stab = ParamStability(
            param_name="a",
            n_perturbations=4,
            n_destructive=1,
            destructive_fraction=Decimal("0.25"),
            worst_degradation=Decimal("0.5"),
        )
        with pytest.raises(AttributeError):
            stab.n_destructive = 99  # type: ignore[misc]

    def test_report_frozen(self) -> None:
        report = RobustnessReport(
            baseline_score=Decimal("1"),
            n_params=1,
            total_perturbations=4,
            total_destructive=0,
            destructive_fraction=Decimal("0"),
            per_param=[],
            perturbations=[],
        )
        with pytest.raises(AttributeError):
            report.n_params = 99  # type: ignore[misc]


# ─── End-to-end : doc 10 I4 scenario ───────────────────────────────────────


@pytest.mark.unit
class TestDoc10I4Scenario:
    def test_publishable_champion_passes(self) -> None:
        # Smooth objective : degradation < 30 % across all perturbations.
        baseline = {"min_score": Decimal("0.45"), "stop_atr": Decimal("2")}

        def smooth(params: dict[str, Decimal]) -> Decimal:
            # Score drops mildly with any perturbation.
            min_score_offset = abs(params["min_score"] - Decimal("0.45"))
            stop_offset = abs(params["stop_atr"] - Decimal("2"))
            penalty = min_score_offset + stop_offset / Decimal("10")
            return Decimal("1") - penalty

        report = compute_robustness_report(
            baseline_score=Decimal("1"),
            baseline_params=baseline,
            objective_fn=smooth,
        )
        # All perturbations stay above 0.7 -> no destructive (< 30 %).
        assert report.total_destructive == 0
        assert is_robust(report)

    def test_overfit_champion_blocked(self) -> None:
        # Tiny basin of attraction : any modest offset breaks the score.
        # Set the basin so tight that even ±10 % falls out -> 8/8 dest.
        baseline = {"min_score": Decimal("0.45"), "stop_atr": Decimal("2")}

        def cliff(params: dict[str, Decimal]) -> Decimal:
            # Score = 1 only at the exact baseline ; drops to 0.4
            # (60 % degradation > 30 % threshold) on any perturbation.
            if params == baseline:
                return Decimal("1.0")
            return Decimal("0.4")

        report = compute_robustness_report(
            baseline_score=Decimal("1"),
            baseline_params=baseline,
            objective_fn=cliff,
        )
        # All 8 perturbations move outside the basin -> all destructive.
        assert report.total_destructive == 8
        assert not is_robust(report)
