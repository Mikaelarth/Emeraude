"""Position sizing : Kelly fractional + volatility targeting + absolute cap.

Computes the order size in **base-asset units** given the available
capital, the strategy's expected win rate and win/loss ratio, the
current asset volatility (ATR), and an absolute percent-of-capital cap.

Algorithm (cf. doc 04 §"Position Sizing Kelly Fractional") :

1. **Kelly fraction** : ``f* = (p * b - q) / b``
   where ``p`` = win rate, ``q = 1 - p``, ``b`` = win/loss ratio.
2. **Fractional Kelly** : multiply ``f*`` by ``kelly_multiplier`` (default
   ``0.5`` = half-Kelly). Full Kelly is too volatile in practice ;
   half-Kelly is the industry default for retail-style accounts.
3. **Vol-targeting cap** : allocate at most
   ``capital * vol_target / (ATR / price)``. Reduces exposure to high-vol
   assets so daily portfolio vol stays bounded.
4. **Absolute cap** : never spend more than ``capital * max_pct_per_trade``
   on a single trade. The user's 20 USD account demands hard limits.
5. **Final** : take the minimum of (Kelly, vol-cap, abs-cap), then
   convert USD to base-asset units via ``/ price``.

Hierarchy reminder (doc 07 §3) :
    "Sécurité du capital utilisateur > tout le reste"
The cap **always** wins over Kelly's optimism.

Returns:
    A non-negative :class:`Decimal` quantity. ``Decimal(0)`` means
    "do not trade" (negative Kelly, zero capital, invalid inputs).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

DEFAULT_KELLY_MULTIPLIER: Final[Decimal] = Decimal("0.5")
DEFAULT_MAX_PCT_PER_TRADE: Final[Decimal] = Decimal("0.05")
DEFAULT_VOL_TARGET: Final[Decimal] = Decimal("0.01")


def kelly_fraction(win_rate: Decimal, win_loss_ratio: Decimal) -> Decimal:
    """Classical Kelly criterion : ``f* = (p * b - q) / b``.

    Args:
        win_rate: probability of a winning trade in ``[0, 1]``.
        win_loss_ratio: average win / average loss (R-multiple). Must be
            strictly positive.

    Returns:
        Kelly fraction clamped to ``[0, 1]``. Negative-EV setups (the
        formula returns < 0) are coerced to 0 — anti-rule A4 (no
        positive size on negative-expectation trades).

    Raises:
        ValueError: if ``win_rate`` is outside ``[0, 1]`` or if
            ``win_loss_ratio`` is non-positive.
    """
    if not _ZERO <= win_rate <= _ONE:
        msg = f"win_rate must be in [0, 1], got {win_rate}"
        raise ValueError(msg)
    if win_loss_ratio <= _ZERO:
        msg = f"win_loss_ratio must be positive, got {win_loss_ratio}"
        raise ValueError(msg)

    loss_rate = _ONE - win_rate
    raw = (win_rate * win_loss_ratio - loss_rate) / win_loss_ratio
    if raw <= _ZERO:
        return _ZERO
    if raw >= _ONE:
        return _ONE
    return raw


def position_size(
    *,
    capital: Decimal,
    win_rate: Decimal,
    win_loss_ratio: Decimal,
    price: Decimal,
    atr: Decimal,
    kelly_multiplier: Decimal = DEFAULT_KELLY_MULTIPLIER,
    max_pct_per_trade: Decimal = DEFAULT_MAX_PCT_PER_TRADE,
    vol_target: Decimal = DEFAULT_VOL_TARGET,
) -> Decimal:
    """Compute the optimal position size in base-asset units.

    Args:
        capital: available USD capital (read from DB by the caller).
        win_rate: probability of a winning trade in ``[0, 1]``.
        win_loss_ratio: average win / average loss as a positive R-multiple.
        price: current asset price (USD per unit). Must be > 0.
        atr: Average True Range in the same currency as price. ``0`` means
            "no volatility info" — the absolute cap takes over.
        kelly_multiplier: fraction of full Kelly to bet, in ``[0, 1]``.
            Default ``0.5`` (half-Kelly).
        max_pct_per_trade: hard cap on capital allocated per trade,
            in ``[0, 1]``. Default ``0.05`` (5 % of capital).
        vol_target: target daily-vol contribution per trade, in
            ``[0, 1]``. Default ``0.01`` (1 %).

    Returns:
        Quantity of base asset to trade. ``Decimal(0)`` means "skip".

    Raises:
        ValueError: on invalid Kelly inputs (delegated to
            :func:`kelly_fraction`).
    """
    if capital <= _ZERO or price <= _ZERO or atr < _ZERO:
        return _ZERO
    if not _ZERO <= kelly_multiplier <= _ONE:
        msg = f"kelly_multiplier must be in [0, 1], got {kelly_multiplier}"
        raise ValueError(msg)
    if not _ZERO <= max_pct_per_trade <= _ONE:
        msg = f"max_pct_per_trade must be in [0, 1], got {max_pct_per_trade}"
        raise ValueError(msg)
    if vol_target < _ZERO:
        msg = f"vol_target must be >= 0, got {vol_target}"
        raise ValueError(msg)

    kelly = kelly_fraction(win_rate, win_loss_ratio)
    if kelly == _ZERO:
        return _ZERO  # negative EV : do not trade

    fractional_kelly = kelly * kelly_multiplier
    kelly_usd = capital * fractional_kelly
    cap_usd = capital * max_pct_per_trade

    if atr == _ZERO:
        # No volatility information : the absolute cap is the only safety net.
        vol_usd = cap_usd
    else:
        vol_pct = atr / price  # asset's daily-ish volatility as a fraction
        vol_usd = capital * vol_target / vol_pct

    position_usd = min(kelly_usd, cap_usd, vol_usd)
    return position_usd / price
