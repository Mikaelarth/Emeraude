"""Unit tests for emeraude.services.performance_export (doc 10 R12 wiring)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    Position,
)
from emeraude.agent.learning.performance_report import (
    PerformanceReport,
    compute_performance_report,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.services.performance_export import (
    export_from_positions,
    report_to_dict,
    report_to_json,
    report_to_markdown,
)

# ─── Fixtures + helpers ──────────────────────────────────────────────────────


def _position(*, pid: int, r: Decimal | None) -> Position:
    """Synthetic Position with just the fields the report consumes."""
    return Position(
        id=pid,
        strategy="trend_follower",
        regime=Regime.BULL,
        side=Side.LONG,
        entry_price=Decimal("100"),
        stop=Decimal("98"),
        target=Decimal("104"),
        quantity=Decimal("0.1"),
        risk_per_unit=Decimal("2"),
        confidence=None,
        opened_at=0,
        closed_at=1,
        exit_price=Decimal("101"),
        exit_reason=ExitReason.MANUAL,
        r_realized=r,
    )


def _empty_report() -> PerformanceReport:
    return compute_performance_report([])


def _typical_report() -> PerformanceReport:
    """7 wins of +1 R + 3 losses of -1 R : populated typical case."""
    positions = [_position(pid=i + 1, r=Decimal("1")) for i in range(7)] + [
        _position(pid=i + 8, r=Decimal("-1")) for i in range(3)
    ]
    return compute_performance_report(positions)


def _all_wins_report() -> PerformanceReport:
    """No losses -> profit_factor = Infinity, max_dd = 0 -> calmar = Infinity."""
    positions = [_position(pid=i + 1, r=Decimal("2")) for i in range(5)]
    return compute_performance_report(positions)


# ─── report_to_dict ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestReportToDict:
    def test_empty_report_yields_zero_padded_dict(self) -> None:
        d = report_to_dict(_empty_report())
        assert d["n_trades"] == 0
        assert d["n_wins"] == 0
        assert d["n_losses"] == 0
        # Decimal values stringified.
        assert d["win_rate"] == "0"
        assert d["expectancy"] == "0"
        assert d["max_drawdown"] == "0"

    def test_typical_report_preserves_all_fields(self) -> None:
        d = report_to_dict(_typical_report())
        # Schema mirrors the dataclass field names.
        expected_keys = {
            "n_trades",
            "n_wins",
            "n_losses",
            "win_rate",
            "expectancy",
            "avg_win",
            "avg_loss",
            "profit_factor",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "max_drawdown",
        }
        assert set(d.keys()) == expected_keys

    def test_int_counts_stay_int(self) -> None:
        d = report_to_dict(_typical_report())
        assert isinstance(d["n_trades"], int)
        assert isinstance(d["n_wins"], int)
        assert isinstance(d["n_losses"], int)
        assert d["n_trades"] == 10
        assert d["n_wins"] == 7
        assert d["n_losses"] == 3

    def test_decimal_values_stringified(self) -> None:
        d = report_to_dict(_typical_report())
        # Win rate = 7 / 10 = 0.7.
        assert d["win_rate"] == "0.7"
        # Expectancy = (7 * 1 + 3 * -1) / 10 = 0.4.
        assert d["expectancy"] == "0.4"
        # avg_win = 1, avg_loss = 1.
        assert d["avg_win"] == "1"
        assert d["avg_loss"] == "1"

    def test_decimal_precision_preserved(self) -> None:
        # Decimal("0.7") must round-trip exactly, not as float "0.7".
        d = report_to_dict(_typical_report())
        round_trip = Decimal(str(d["win_rate"]))
        assert round_trip == Decimal("0.7")

    def test_infinity_stringified_as_word(self) -> None:
        # all wins -> profit_factor = Infinity, calmar = Infinity.
        d = report_to_dict(_all_wins_report())
        assert d["profit_factor"] == "Infinity"
        assert d["calmar_ratio"] == "Infinity"


# ─── report_to_json ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestReportToJson:
    def test_returns_string(self) -> None:
        s = report_to_json(_typical_report())
        assert isinstance(s, str)

    def test_round_trips_via_json_parse(self) -> None:
        s = report_to_json(_typical_report())
        parsed = json.loads(s)
        assert parsed["n_trades"] == 10
        assert parsed["win_rate"] == "0.7"
        assert parsed["expectancy"] == "0.4"

    def test_decimal_values_round_trip_losslessly(self) -> None:
        s = report_to_json(_typical_report())
        parsed = json.loads(s)
        # Reconstruct Decimal from the string -> no precision loss.
        assert Decimal(parsed["win_rate"]) == Decimal("0.7")
        assert Decimal(parsed["expectancy"]) == Decimal("0.4")

    def test_infinity_round_trips(self) -> None:
        # JSON does not have native Infinity ; we encode as the
        # string "Infinity" and Decimal can parse it back.
        s = report_to_json(_all_wins_report())
        parsed = json.loads(s)
        assert parsed["profit_factor"] == "Infinity"
        # Decimal can re-build it.
        assert Decimal(parsed["profit_factor"]).is_infinite() is True

    def test_compact_default(self) -> None:
        # Default = no indent = single-line output.
        s = report_to_json(_typical_report())
        assert "\n" not in s

    def test_indented_form(self) -> None:
        s = report_to_json(_typical_report(), indent=2)
        # indent=2 inserts newlines.
        assert "\n" in s
        # Still valid JSON.
        json.loads(s)


# ─── report_to_markdown ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestReportToMarkdown:
    def test_empty_report_no_table(self) -> None:
        md = report_to_markdown(_empty_report())
        assert "No trades yet" in md
        assert "| Metric |" not in md

    def test_typical_report_renders_table(self) -> None:
        md = report_to_markdown(_typical_report())
        # Header, separator, all 12 metric rows.
        assert "| Metric | Value |" in md
        assert "|---|---|" in md
        assert "| Trades | 10 |" in md
        assert "| Wins | 7 |" in md
        assert "| Losses | 3 |" in md

    def test_win_rate_formatted_as_percentage(self) -> None:
        md = report_to_markdown(_typical_report())
        # 0.7 -> "70.00 %".
        assert "70.00 %" in md

    def test_r_units_have_4_decimals(self) -> None:
        md = report_to_markdown(_typical_report())
        # Expectancy 0.4 -> "0.4000 R".
        assert "0.4000 R" in md
        # avg_win and avg_loss = 1 -> "1.0000 R".
        assert "1.0000 R" in md

    def test_infinity_rendered_as_word(self) -> None:
        md = report_to_markdown(_all_wins_report())
        # profit_factor + calmar are Infinity in this scenario.
        assert "Infinity" in md
        # Should appear at least twice (profit_factor + calmar).
        assert md.count("Infinity") >= 2

    def test_heading_includes_trade_count(self) -> None:
        md = report_to_markdown(_typical_report())
        assert "n=10 trades" in md

    def test_lf_line_endings(self) -> None:
        md = report_to_markdown(_typical_report())
        # No CR characters — LF only.
        assert "\r" not in md
        # Trailing newline for clean concatenation.
        assert md.endswith("\n")


# ─── export_from_positions ───────────────────────────────────────────────────


@pytest.mark.unit
class TestExportFromPositions:
    def test_chains_compute_and_dict(self) -> None:
        positions = [_position(pid=i + 1, r=Decimal("1")) for i in range(5)]
        d = export_from_positions(positions)
        # 5 wins -> n_trades=5, win_rate=1, expectancy=1.
        assert d["n_trades"] == 5
        assert d["n_wins"] == 5
        assert d["n_losses"] == 0
        assert d["win_rate"] == "1"
        assert d["expectancy"] == "1"

    def test_empty_input_yields_zero_padded(self) -> None:
        d = export_from_positions([])
        assert d["n_trades"] == 0
        assert d["win_rate"] == "0"

    def test_open_positions_filtered(self) -> None:
        # r_realized=None means open ; the underlying primitive
        # filters those out before computing.
        positions = [
            _position(pid=1, r=Decimal("1")),
            _position(pid=2, r=None),
            _position(pid=3, r=Decimal("-1")),
        ]
        d = export_from_positions(positions)
        assert d["n_trades"] == 2  # only the 2 closed rows


# ─── Round-trip end-to-end ───────────────────────────────────────────────────


@pytest.mark.unit
class TestRoundTrip:
    def test_dict_then_json_then_back_preserves_decimals(self) -> None:
        # Full pipeline : compute -> dict -> json -> parse ->
        # rebuild Decimals -> values still equal the originals.
        report = _typical_report()
        s = report_to_json(report)
        parsed = json.loads(s)

        assert int(parsed["n_trades"]) == report.n_trades
        assert Decimal(parsed["win_rate"]) == report.win_rate
        assert Decimal(parsed["expectancy"]) == report.expectancy
        assert Decimal(parsed["avg_win"]) == report.avg_win
        assert Decimal(parsed["avg_loss"]) == report.avg_loss
