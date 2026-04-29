"""Pure-logic tests for the Dashboard formatter (no Kivy, no display).

Cover all branches of :func:`format_dashboard_labels` and the helper
formatters. Runs everywhere (including headless Linux CI) since no
widget instantiation happens.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.execution.position_tracker import (
    Position,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.ui.screens.dashboard import (
    MODE_PAPER,
    MODE_REAL,
    MODE_UNCONFIGURED,
    DashboardLabels,
    DashboardSnapshot,
    format_dashboard_labels,
)

# ─── Fixtures ──────────────────────────────────────────────────────────────


def _open_position() -> Position:
    """A representative open LONG position."""
    return Position(
        id=1,
        strategy="trend_follower",
        regime=Regime.BULL,
        side=Side.LONG,
        entry_price=Decimal("100"),
        stop=Decimal("98"),
        target=Decimal("104"),
        quantity=Decimal("0.1"),
        risk_per_unit=Decimal("2"),
        confidence=Decimal("0.7"),
        opened_at=0,
        closed_at=None,
        exit_price=None,
        exit_reason=None,
        r_realized=None,
    )


def _snapshot(
    *,
    capital_quote: Decimal | None = Decimal("20"),
    open_position: Position | None = None,
    cumulative_pnl: Decimal = Decimal("0"),
    n_closed_trades: int = 0,
    mode: str = MODE_PAPER,
) -> DashboardSnapshot:
    """Build a snapshot with sensible defaults."""
    return DashboardSnapshot(
        capital_quote=capital_quote,
        open_position=open_position,
        cumulative_pnl=cumulative_pnl,
        n_closed_trades=n_closed_trades,
        mode=mode,
    )


# ─── Capital ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCapitalFormatting:
    def test_known_capital_displays_with_currency(self) -> None:
        labels = format_dashboard_labels(_snapshot(capital_quote=Decimal("20.00")))
        assert "20.00" in labels.capital
        assert "USDT" in labels.capital

    def test_unknown_capital_displays_dash(self) -> None:
        labels = format_dashboard_labels(_snapshot(capital_quote=None))
        assert "—" in labels.capital
        # No spurious "USDT" when value is unknown.
        assert "USDT" not in labels.capital

    def test_capital_quantized_to_two_decimals(self) -> None:
        # 20.123 -> 20.12 (banker's rounding).
        labels = format_dashboard_labels(_snapshot(capital_quote=Decimal("20.123")))
        assert "20.12" in labels.capital

    def test_capital_zero_renders(self) -> None:
        # 0 USDT is a valid state (e.g. all capital in open position).
        labels = format_dashboard_labels(_snapshot(capital_quote=Decimal("0")))
        assert "0.00" in labels.capital


# ─── Open position ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestOpenPositionFormatting:
    def test_no_open_position_message(self) -> None:
        labels = format_dashboard_labels(_snapshot(open_position=None))
        assert labels.open_position == "Aucune position ouverte"

    def test_open_position_shows_side_qty_strategy_entry(self) -> None:
        labels = format_dashboard_labels(_snapshot(open_position=_open_position()))
        # All four facts must be in the formatted line.
        assert "LONG" in labels.open_position
        assert "0.1" in labels.open_position
        assert "trend_follower" in labels.open_position
        assert "100" in labels.open_position


# ─── PnL ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPnlFormatting:
    def test_positive_pnl_has_plus_sign(self) -> None:
        labels = format_dashboard_labels(_snapshot(cumulative_pnl=Decimal("1.5")))
        assert "+1.50" in labels.pnl

    def test_zero_pnl_no_sign(self) -> None:
        labels = format_dashboard_labels(_snapshot(cumulative_pnl=Decimal("0")))
        # Zero shows without a leading + (sign convention).
        assert "+" not in labels.pnl
        assert "0.00" in labels.pnl

    def test_negative_pnl_keeps_minus(self) -> None:
        labels = format_dashboard_labels(_snapshot(cumulative_pnl=Decimal("-1.5")))
        # Decimal already carries the minus.
        assert "-1.50" in labels.pnl
        # No double-minus (the sign helper only prepends + on positive).
        assert "+-" not in labels.pnl

    def test_pnl_uses_quote_currency(self) -> None:
        labels = format_dashboard_labels(_snapshot(cumulative_pnl=Decimal("1")))
        assert "USDT" in labels.pnl


# ─── Trade count ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTradeCountFormatting:
    def test_zero_trades_singular_form(self) -> None:
        labels = format_dashboard_labels(_snapshot(n_closed_trades=0))
        assert labels.n_trades == "0 trade fermé"

    def test_one_trade_singular_form(self) -> None:
        labels = format_dashboard_labels(_snapshot(n_closed_trades=1))
        assert labels.n_trades == "1 trade fermé"

    def test_many_trades_plural_form(self) -> None:
        labels = format_dashboard_labels(_snapshot(n_closed_trades=42))
        assert labels.n_trades == "42 trades fermés"


# ─── Mode badge ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestModeBadgeFormatting:
    def test_paper_mode_label(self) -> None:
        labels = format_dashboard_labels(_snapshot(mode=MODE_PAPER))
        assert labels.mode_badge == "Mode : Paper"

    def test_real_mode_label(self) -> None:
        labels = format_dashboard_labels(_snapshot(mode=MODE_REAL))
        assert labels.mode_badge == "Mode : Réel"

    def test_unconfigured_mode_label(self) -> None:
        labels = format_dashboard_labels(_snapshot(mode=MODE_UNCONFIGURED))
        assert labels.mode_badge == "Mode : Non configuré"

    def test_unknown_mode_falls_back_safely(self) -> None:
        # Pas d'exception sur une chaine inattendue (anti-A8 : pas de
        # except: pass non plus, mais pas d'exception en cascade pour
        # un simple label d'UI).
        labels = format_dashboard_labels(_snapshot(mode="future_mode"))
        assert "future_mode" in labels.mode_badge


# ─── DashboardLabels container ────────────────────────────────────────────


@pytest.mark.unit
class TestDashboardLabelsContainer:
    def test_labels_is_immutable(self) -> None:
        labels = format_dashboard_labels(_snapshot())
        with pytest.raises((AttributeError, TypeError)):
            labels.capital = "tampered"  # type: ignore[misc]

    def test_labels_carries_all_five_strings(self) -> None:
        labels = format_dashboard_labels(_snapshot())
        assert isinstance(labels, DashboardLabels)
        # All five strings exist and are non-empty.
        assert labels.capital
        assert labels.open_position
        assert labels.pnl
        assert labels.n_trades
        assert labels.mode_badge


# ─── DashboardSnapshot container ──────────────────────────────────────────


@pytest.mark.unit
class TestDashboardSnapshotContainer:
    def test_snapshot_is_immutable(self) -> None:
        snap = _snapshot()
        with pytest.raises((AttributeError, TypeError)):
            snap.cumulative_pnl = Decimal("999")  # type: ignore[misc]

    def test_snapshot_accepts_none_capital(self) -> None:
        # Cold start path : capital_quote=None must not break the
        # formatter chain.
        snap = _snapshot(capital_quote=None)
        labels = format_dashboard_labels(snap)
        assert labels is not None
