"""Adversarial backtest fill model (doc 10 R2).

Doc 10 §"R2 — Backtest adversarial (pessimisme par défaut)" addresses
lacuna L2 (backtest optimiste). The standard backtest assumes perfect
execution — fills at the close of the signal bar, no slippage, no fees,
no gap risk, no latency. In live trading none of those hold. Bailey,
Borwein, López de Prado (2014) show that most "champion" overfits
collapse on real markets specifically because of execution drift.

This module ships the **deterministic** adversarial model with four
pessimistic assumptions :

* **Worst-of-bar fill** : a BUY fills at the **high** of the execution
  bar, a SELL at the **low**. The market always picks the worst bar
  point against us.
* **Slippage** : on top of the worst-of-bar, the fill price is moved
  further against us by ``slippage_pct`` (default ``0.001`` = 0.1 %,
  which is 2x the theoretical 0.05 % per doc 10 R2).
* **Fees** : ``fee = fill_price * quantity * fee_pct`` (default
  ``0.0011`` = 0.11 %, which is 1.1x the Binance 0.10 % taker fee per
  doc 10 R2 ; the 1.1x margin covers network costs and conversions).
* **Latency** : the fill happens ``latency_bars`` after the signal
  (default 1). The caller picks the ``execution_bar`` accordingly ;
  the parameter is carried in :class:`AdversarialParams` for audit.

The (5th) **gap-risk Monte-Carlo** mentioned in doc 10 R2 (re-sampling
gaps from an empirical distribution) is deferred per anti-rule A1 :
when replaying historical klines the gaps are already realized in the
data ; the Monte-Carlo variant becomes useful only when forward-
projecting under a synthetic regime, which requires a simulator that
does not exist yet.

Pure module : no I/O, no DB, no NumPy. Decimal everywhere.

Reference :

* Bailey, Borwein, López de Prado (2014). *The Probability of
  Backtest Overfitting*. Journal of Computational Finance 20(4) :
  39-69.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.reasoning.risk_manager import Side

if TYPE_CHECKING:
    from emeraude.infra.market_data import Kline

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

# Doc 10 R2 defaults, all in fractional form (0.001 = 0.1 %).
DEFAULT_SLIPPAGE_PCT: Final[Decimal] = Decimal("0.001")
DEFAULT_FEE_PCT: Final[Decimal] = Decimal("0.0011")
DEFAULT_LATENCY_BARS: Final[int] = 1


# ─── Parameter type ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdversarialParams:
    """Configurable pessimisms applied to every backtest fill.

    Attributes:
        slippage_pct: absolute fraction added to the worst-of-bar
            price against the trader's interest (BUY pays more,
            SELL receives less). Doc 10 R2 default ``0.001``.
        fee_pct: absolute fraction of fill notional taken as fee.
            Doc 10 R2 default ``0.0011`` (1.1x Binance 0.10 %).
        latency_bars: number of bars between signal and execution.
            The caller picks ``execution_bar = klines[signal_index +
            latency_bars]`` ; the value is carried here for audit.
            Default ``1``.
    """

    slippage_pct: Decimal = DEFAULT_SLIPPAGE_PCT
    fee_pct: Decimal = DEFAULT_FEE_PCT
    latency_bars: int = DEFAULT_LATENCY_BARS

    def __post_init__(self) -> None:
        """Validate ranges at construction."""
        if self.slippage_pct < _ZERO:
            msg = f"slippage_pct must be >= 0, got {self.slippage_pct}"
            raise ValueError(msg)
        if self.fee_pct < _ZERO:
            msg = f"fee_pct must be >= 0, got {self.fee_pct}"
            raise ValueError(msg)
        if self.latency_bars < 0:
            msg = f"latency_bars must be >= 0, got {self.latency_bars}"
            raise ValueError(msg)


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdversarialFill:
    """One realized fill after the adversarial pessimisms applied.

    Attributes:
        side: ``LONG`` for BUY, ``SHORT`` for SELL. Reused from
            :class:`risk_manager.Side` so the audit chain stays
            consistent across modules.
        signal_price: price the orchestrator's signal targeted (the
            close of the signal bar, typically).
        worst_bar_price: extreme of the execution bar against the
            trader (``execution_bar.high`` for BUY,
            ``execution_bar.low`` for SELL). Pre-slippage.
        fill_price: ``worst_bar_price * (1 +/- slippage_pct)``.
            What the trader actually got.
        quantity: base-asset units traded.
        fee: absolute fee in quote currency
            (``fill_price * quantity * fee_pct``).
        slippage_cost: absolute cost of the slippage component
            (``|fill_price - worst_bar_price| * quantity``). Carved
            out from the total to make the audit decomposition
            explicit.
    """

    side: Side
    signal_price: Decimal
    worst_bar_price: Decimal
    fill_price: Decimal
    quantity: Decimal
    fee: Decimal
    slippage_cost: Decimal

    @property
    def total_notional(self) -> Decimal:
        """``fill_price * quantity``. Pre-fee, pre-slippage-decomposition."""
        return self.fill_price * self.quantity

    @property
    def cash_flow(self) -> Decimal:
        """Signed cash flow at this fill.

        For a BUY (``side=LONG``) this is **negative** — cash leaves
        the account (notional + fee). For a SELL (``side=SHORT`` from
        the perspective of opening a short, or the closing leg of a
        LONG) it is **positive** (notional minus fee).

        Convention used by :func:`compute_realized_pnl` :
        entry of a LONG = BUY = -notional - fee ;
        exit of a LONG = SELL = +notional - fee ;
        entry of a SHORT = SELL = +notional - fee ;
        exit of a SHORT = BUY = -notional - fee.
        """
        notional = self.total_notional
        if self.side is Side.LONG:
            return -notional - self.fee
        return notional - self.fee


# ─── Public API ─────────────────────────────────────────────────────────────


def apply_adversarial_fill(
    *,
    signal_price: Decimal,
    side: Side,
    execution_bar: Kline,
    quantity: Decimal,
    params: AdversarialParams | None = None,
) -> AdversarialFill:
    """Apply the four R2 pessimisms to compute a realized fill.

    Args:
        signal_price: price the strategy aimed at (typically
            ``signal_bar.close``).
        side: ``LONG`` (BUY) or ``SHORT`` (SELL).
        execution_bar: kline at ``signal_index + params.latency_bars``.
            The caller is responsible for the offset ; the bar
            passed in is the one the fill happens *on*.
        quantity: base-asset units. Must be > 0.
        params: pessimism knobs. ``None`` (default) yields the
            doc 10 R2 defaults (``AdversarialParams()``).

    Returns:
        An :class:`AdversarialFill` with all five derivation
        components broken out (signal, worst-bar, slippage, fill,
        fee, total).

    Raises:
        ValueError: on non-positive ``signal_price`` or
            ``quantity``, or on a degenerate ``execution_bar`` whose
            ``high < low``.
    """
    if params is None:
        params = AdversarialParams()
    if signal_price <= _ZERO:
        msg = f"signal_price must be > 0, got {signal_price}"
        raise ValueError(msg)
    if quantity <= _ZERO:
        msg = f"quantity must be > 0, got {quantity}"
        raise ValueError(msg)
    if execution_bar.high < execution_bar.low:
        msg = (
            "execution_bar.high must be >= execution_bar.low, got "
            f"{execution_bar.high} < {execution_bar.low}"
        )
        raise ValueError(msg)

    # Worst-of-bar : BUY pays the high, SELL receives the low.
    if side is Side.LONG:
        worst_bar_price = execution_bar.high
        slip_factor = _ONE + params.slippage_pct
    else:
        worst_bar_price = execution_bar.low
        slip_factor = _ONE - params.slippage_pct

    fill_price = worst_bar_price * slip_factor
    # Slippage cost component, audit-friendly.
    slippage_cost = abs(fill_price - worst_bar_price) * quantity
    fee = fill_price * quantity * params.fee_pct

    return AdversarialFill(
        side=side,
        signal_price=signal_price,
        worst_bar_price=worst_bar_price,
        fill_price=fill_price,
        quantity=quantity,
        fee=fee,
        slippage_cost=slippage_cost,
    )


def compute_realized_pnl(
    *,
    entry: AdversarialFill,
    exit_fill: AdversarialFill,
) -> Decimal:
    """Net PnL after a full round-trip (entry + exit) with adversarial fills.

    Convention :

    * **LONG** : entry side ``LONG`` (BUY at fill_price), exit side
      ``SHORT`` (SELL at fill_price). PnL =
      ``(exit.fill - entry.fill) * quantity - entry.fee - exit.fee``.
    * **SHORT** : entry side ``SHORT`` (SELL high), exit side ``LONG``
      (BUY back). PnL =
      ``(entry.fill - exit.fill) * quantity - entry.fee - exit.fee``.

    Args:
        entry: opening fill.
        exit_fill: closing fill. Side must be the *opposite* of
            ``entry.side`` ; ``quantity`` must match.

    Returns:
        Realized net PnL in quote currency. Positive = profit.

    Raises:
        ValueError: on side mismatch (``entry.side == exit_fill.side``)
            or on ``entry.quantity != exit_fill.quantity``.
    """
    if entry.side is exit_fill.side:
        msg = f"entry and exit must have opposite sides, both got {entry.side.value}"
        raise ValueError(msg)
    if entry.quantity != exit_fill.quantity:
        msg = f"entry and exit quantity must match, got {entry.quantity} and {exit_fill.quantity}"
        raise ValueError(msg)

    quantity = entry.quantity
    fees = entry.fee + exit_fill.fee
    if entry.side is Side.LONG:
        # Long round-trip : profit = (sell - buy) * qty - fees.
        return (exit_fill.fill_price - entry.fill_price) * quantity - fees
    # Short round-trip : profit = (sell - buy) * qty - fees, but the
    # SELL is at entry and the BUY is at exit, so the cash leg flips.
    return (entry.fill_price - exit_fill.fill_price) * quantity - fees
