"""Unit tests for :class:`WalletService` (iter #60).

Cover paper-mode aggregation (starting + cumulative P&L), real-mode
None placeholder, unconfigured-mode None, validation, and mode
passthrough. Uses the real :class:`PositionTracker` against a tmpdir
SQLite DB ; no UI / Kivy involved.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from emeraude.agent.execution.position_tracker import (
    ExitReason,
    PositionTracker,
)
from emeraude.agent.perception.regime import Regime
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra import database
from emeraude.services.dashboard_types import (
    MODE_PAPER,
    MODE_REAL,
    MODE_UNCONFIGURED,
)
from emeraude.services.wallet import (
    DEFAULT_COLD_START_CAPITAL,
    WalletService,
)

# ─── Fixtures + helpers ────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    database.get_connection()
    return tmp_path / "emeraude.db"


def _make_tracker_with_history(
    *,
    n_winning: int = 0,
    n_losing: int = 0,
) -> PositionTracker:
    """Drive a real tracker through n closed trades.

    Each winning trade : LONG 100 -> 104, r=2, risk=2, qty=0.1
        -> realized PnL = 2 * 2 * 0.1 = 0.4 USDT.
    Each losing trade : LONG 100 -> 98, r=-1, risk=2, qty=0.1
        -> realized PnL = -1 * 2 * 0.1 = -0.2 USDT.
    """
    tracker = PositionTracker()
    for i in range(n_winning):
        tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            confidence=Decimal("0.7"),
            opened_at=i * 10,
        )
        tracker.close_position(
            exit_price=Decimal("104"),
            exit_reason=ExitReason.TARGET_HIT,
            closed_at=i * 10 + 5,
        )
    for j in range(n_losing):
        tracker.open_position(
            strategy="trend_follower",
            regime=Regime.BULL,
            side=Side.LONG,
            entry_price=Decimal("100"),
            stop=Decimal("98"),
            target=Decimal("104"),
            quantity=Decimal("0.1"),
            risk_per_unit=Decimal("2"),
            confidence=Decimal("0.7"),
            opened_at=(n_winning + j) * 10,
        )
        tracker.close_position(
            exit_price=Decimal("98"),
            exit_reason=ExitReason.STOP_HIT,
            closed_at=(n_winning + j) * 10 + 5,
        )
    return tracker


# ─── Validation ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    def test_negative_starting_capital_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"starting_capital must be >= 0"):
            WalletService(
                tracker=PositionTracker(),
                mode_provider=lambda: MODE_PAPER,
                starting_capital=Decimal("-1"),
            )

    def test_zero_starting_capital_accepted(self, fresh_db: Path) -> None:
        # Boundary : a wallet starting at zero is valid (e.g. fresh
        # paper account before deposit). Realized PnL accumulates.
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_PAPER,
            starting_capital=Decimal("0"),
        )
        assert wallet.current_capital() == Decimal("0")

    def test_history_limit_below_one_rejected(self, fresh_db: Path) -> None:
        with pytest.raises(ValueError, match=r"history_limit must be >= 1"):
            WalletService(
                tracker=PositionTracker(),
                mode_provider=lambda: MODE_PAPER,
                history_limit=0,
            )


# ─── Mode dispatch ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestModeDispatch:
    def test_paper_mode_returns_decimal(self, fresh_db: Path) -> None:
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_PAPER,
        )
        capital = wallet.current_capital()
        assert capital is not None
        assert isinstance(capital, Decimal)

    def test_real_mode_returns_none_until_wired(self, fresh_db: Path) -> None:
        # Real-mode live balance integration is deferred — A1 honesty.
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_REAL,
        )
        assert wallet.current_capital() is None

    def test_unconfigured_mode_returns_none(self, fresh_db: Path) -> None:
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_UNCONFIGURED,
        )
        assert wallet.current_capital() is None

    def test_unknown_mode_falls_back_to_none(self, fresh_db: Path) -> None:
        # Pas d'exception sur un mode inattendu — we surface None
        # rather than crashing the UI (anti-A8 + safe degrade).
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: "future_mode",
        )
        assert wallet.current_capital() is None


# ─── Paper-mode aggregation ────────────────────────────────────────────────


@pytest.mark.unit
class TestPaperModeAggregation:
    def test_no_history_returns_starting_capital(self, fresh_db: Path) -> None:
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_PAPER,
        )
        # Cold-start : 20 USD, no closed trade -> capital = 20.
        assert wallet.current_capital() == DEFAULT_COLD_START_CAPITAL

    def test_winning_history_adds_pnl(self, fresh_db: Path) -> None:
        # 3 wins at +0.4 each -> capital = 20 + 1.2 = 21.2.
        tracker = _make_tracker_with_history(n_winning=3)
        wallet = WalletService(tracker=tracker, mode_provider=lambda: MODE_PAPER)
        assert wallet.current_capital() == Decimal("21.2")

    def test_losing_history_subtracts_pnl(self, fresh_db: Path) -> None:
        # 2 losses at -0.2 each -> capital = 20 - 0.4 = 19.6.
        tracker = _make_tracker_with_history(n_losing=2)
        wallet = WalletService(tracker=tracker, mode_provider=lambda: MODE_PAPER)
        assert wallet.current_capital() == Decimal("19.6")

    def test_mixed_history_signed_correctly(self, fresh_db: Path) -> None:
        # 2 wins + 1 loss : +0.8 - 0.2 = +0.6 -> capital = 20.6.
        tracker = _make_tracker_with_history(n_winning=2, n_losing=1)
        wallet = WalletService(tracker=tracker, mode_provider=lambda: MODE_PAPER)
        assert wallet.current_capital() == Decimal("20.6")

    def test_custom_starting_capital_honored(self, fresh_db: Path) -> None:
        # Bigger starting capital : 100 + 1.2 = 101.2 after 3 wins.
        tracker = _make_tracker_with_history(n_winning=3)
        wallet = WalletService(
            tracker=tracker,
            mode_provider=lambda: MODE_PAPER,
            starting_capital=Decimal("100"),
        )
        assert wallet.current_capital() == Decimal("101.2")


# ─── History limit ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHistoryLimit:
    def test_limit_caps_aggregation(self, fresh_db: Path) -> None:
        # 5 winning trades, limit=2 -> only 2 counted in PnL.
        tracker = _make_tracker_with_history(n_winning=5)
        wallet = WalletService(
            tracker=tracker,
            mode_provider=lambda: MODE_PAPER,
            history_limit=2,
        )
        # Two wins at +0.4 each -> capital = 20.8.
        assert wallet.current_capital() == Decimal("20.8")


# ─── Properties ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestProperties:
    def test_mode_passthrough(self, fresh_db: Path) -> None:
        wallet = WalletService(tracker=PositionTracker(), mode_provider=lambda: MODE_PAPER)
        assert wallet.mode == MODE_PAPER

    def test_starting_capital_passthrough(self, fresh_db: Path) -> None:
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_PAPER,
            starting_capital=Decimal("50"),
        )
        assert wallet.starting_capital == Decimal("50")

    def test_default_starting_capital_is_doc04_constant(self) -> None:
        # The default mirrors auto_trader's cold-start (doc 04).
        assert Decimal("20") == DEFAULT_COLD_START_CAPITAL


# ─── Live mode provider (iter #65) ─────────────────────────────────────────


@pytest.mark.unit
class TestLiveModeProvider:
    def test_mode_re_evaluated_on_each_access(self, fresh_db: Path) -> None:
        # The provider is a regular callable ; mutating its source
        # should be visible at the next ``wallet.mode`` access — no
        # cache, no memoization.
        current_mode = [MODE_PAPER]
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: current_mode[0],
        )
        assert wallet.mode == MODE_PAPER
        current_mode[0] = MODE_REAL
        assert wallet.mode == MODE_REAL

    def test_current_capital_reflects_live_mode_change(self, fresh_db: Path) -> None:
        # Provider returns paper -> capital is a Decimal.
        # Then provider returns real -> capital becomes None on the
        # very next call (iter #65 live propagation).
        current_mode = [MODE_PAPER]
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: current_mode[0],
        )
        assert wallet.current_capital() == DEFAULT_COLD_START_CAPITAL

        current_mode[0] = MODE_REAL
        assert wallet.current_capital() is None


# ─── Real mode delegation (iter #67) ───────────────────────────────────────


@pytest.mark.unit
class TestRealModeDelegation:
    def test_real_mode_uses_provider_when_set(self, fresh_db: Path) -> None:
        # Real mode + provider injected -> wallet returns provider's value.
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_REAL,
            real_balance_provider=lambda: Decimal("123.45"),
        )
        assert wallet.current_capital() == Decimal("123.45")

    def test_real_mode_provider_returning_none(self, fresh_db: Path) -> None:
        # Provider returns None (e.g. HTTP failure) -> wallet propagates.
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_REAL,
            real_balance_provider=lambda: None,
        )
        assert wallet.current_capital() is None

    def test_real_mode_no_provider_returns_none(self, fresh_db: Path) -> None:
        # No provider injected -> backward-compatible behavior.
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_REAL,
        )
        assert wallet.current_capital() is None

    def test_provider_only_called_in_real_mode(self, fresh_db: Path) -> None:
        # Provider must NOT be invoked in paper / unconfigured mode.
        provider_calls = [0]

        def _provider() -> Decimal | None:
            provider_calls[0] += 1
            return Decimal("999")

        current_mode = [MODE_PAPER]
        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: current_mode[0],
            real_balance_provider=_provider,
        )

        wallet.current_capital()  # paper
        assert provider_calls[0] == 0

        current_mode[0] = MODE_UNCONFIGURED
        wallet.current_capital()
        assert provider_calls[0] == 0

        current_mode[0] = MODE_REAL
        wallet.current_capital()
        assert provider_calls[0] == 1

    def test_provider_re_evaluated_each_call(self, fresh_db: Path) -> None:
        # Like mode_provider, real_balance_provider is invoked at
        # every current_capital() call — pas de cache côté wallet.
        # Le cache est porté par le provider lui-même (iter #67
        # BinanceBalanceProvider TTL).
        balances = iter([Decimal("10"), Decimal("20"), Decimal("30")])

        wallet = WalletService(
            tracker=PositionTracker(),
            mode_provider=lambda: MODE_REAL,
            real_balance_provider=lambda: next(balances),
        )
        assert wallet.current_capital() == Decimal("10")
        assert wallet.current_capital() == Decimal("20")
        assert wallet.current_capital() == Decimal("30")
