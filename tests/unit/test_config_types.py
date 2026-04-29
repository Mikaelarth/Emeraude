"""Pure-logic tests for the Config types + formatters (no Kivy)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.services.config_types import (
    SETTING_KEY_MODE,
    ConfigSnapshot,
    format_audit_count_label,
    format_mode_label,
    format_starting_capital_label,
    is_valid_mode,
)
from emeraude.services.dashboard_types import (
    MODE_PAPER,
    MODE_REAL,
    MODE_UNCONFIGURED,
)

# ─── Mode label ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestModeLabel:
    def test_paper(self) -> None:
        assert format_mode_label(MODE_PAPER) == "Paper"

    def test_real(self) -> None:
        assert format_mode_label(MODE_REAL) == "Réel"

    def test_unconfigured(self) -> None:
        assert format_mode_label(MODE_UNCONFIGURED) == "Non configuré"

    def test_unknown_mode_passthrough(self) -> None:
        assert format_mode_label("future_mode") == "future_mode"


# ─── Starting capital label ───────────────────────────────────────────────


@pytest.mark.unit
class TestStartingCapitalLabel:
    def test_known_capital_quantized(self) -> None:
        # 20 -> "20.00 USDT"
        assert format_starting_capital_label(Decimal("20")) == "20.00 USDT"

    def test_capital_with_decimals(self) -> None:
        assert format_starting_capital_label(Decimal("21.456")) == "21.46 USDT"

    def test_zero_capital(self) -> None:
        assert format_starting_capital_label(Decimal("0")) == "0.00 USDT"

    def test_none_capital_renders_dash(self) -> None:
        assert format_starting_capital_label(None) == "—"


# ─── Audit count label ────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditCountLabel:
    def test_zero_singular_form(self) -> None:
        # 0 et 1 utilisent le singulier (convention française simple).
        assert format_audit_count_label(0) == "0 événement"

    def test_one_singular_form(self) -> None:
        assert format_audit_count_label(1) == "1 événement"

    def test_many_plural_form(self) -> None:
        assert format_audit_count_label(2) == "2 événements"
        assert format_audit_count_label(127) == "127 événements"


# ─── Validator ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestModeValidator:
    @pytest.mark.parametrize("mode", [MODE_PAPER, MODE_REAL, MODE_UNCONFIGURED])
    def test_known_modes_accepted(self, mode: str) -> None:
        assert is_valid_mode(mode)

    @pytest.mark.parametrize("mode", ["", "PAPER", "Real", "future_mode", "  paper  "])
    def test_unknown_modes_rejected(self, mode: str) -> None:
        assert not is_valid_mode(mode)


# ─── Snapshot container ───────────────────────────────────────────────────


@pytest.mark.unit
class TestSnapshotContainer:
    def test_immutable(self) -> None:
        snap = ConfigSnapshot(
            mode=MODE_PAPER,
            starting_capital=Decimal("20"),
            app_version="0.0.64",
            total_audit_events=0,
            db_path="emeraude-test.db",
        )
        with pytest.raises((AttributeError, TypeError)):
            snap.mode = MODE_REAL  # type: ignore[misc]

    def test_carries_all_fields(self) -> None:
        snap = ConfigSnapshot(
            mode=MODE_REAL,
            starting_capital=Decimal("100.50"),
            app_version="0.0.64",
            total_audit_events=42,
            db_path="/var/lib/emeraude.db",
        )
        assert snap.mode == MODE_REAL
        assert snap.starting_capital == Decimal("100.50")
        assert snap.app_version == "0.0.64"
        assert snap.total_audit_events == 42
        assert snap.db_path == "/var/lib/emeraude.db"

    def test_starting_capital_none_accepted(self) -> None:
        # Cold-start path : capital provider returned None.
        snap = ConfigSnapshot(
            mode=MODE_UNCONFIGURED,
            starting_capital=None,
            app_version="0.0.64",
            total_audit_events=0,
            db_path="emeraude-test.db",
        )
        assert snap.starting_capital is None


# ─── Constants ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConstants:
    def test_setting_key_mode_stable(self) -> None:
        # Stable contract : changing this would orphan persisted user
        # settings on existing installs.
        assert SETTING_KEY_MODE == "ui.mode"
