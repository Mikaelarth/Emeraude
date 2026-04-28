"""Smart-limit execution plan (doc 10 R9).

Doc 10 §"R9 — Execution intelligente (smart limit + fallback)"
addresses lacuna L9 (slippage non maitrise). Naive market orders
cross the spread on every fill ; on a 0.15 % spread that is 15 bps
of guaranteed slippage per round-trip. Smart-limit placement saves
half the spread on average, at the cost of an occasional fallback
to market when the limit does not fill within the timeout window.

This module ships the **decision primitives** :

* :func:`passive_side_price` — the limit price on the favourable
  side : ``bid`` for a LONG buy, ``ask`` for a SHORT sell. The
  caller posts the order at this price and waits.
* :func:`cross_spread_price` — the market-equivalent price : ``ask``
  for a LONG buy, ``bid`` for a SHORT sell. The aggressive fill.
* :func:`expected_market_slippage_bps` — the half-spread cost of
  crossing in basis points (10 000 bps = 100 %).
* :func:`compute_realized_slippage_bps` — post-fill diagnostic :
  signed slippage between the expected entry (typically the kline
  close) and the actual fill price. Positive = adverse.
* :func:`decide_execution_plan` — combined entry point that returns
  an :class:`ExecutionPlan` with both the limit and the market
  price plus a recommendation : when the spread exceeds
  ``max_spread_bps_for_limit``, the gate flips to market (the
  patience cost would be too high — adverse selection risk on a
  wide book).

Pure module : no I/O, no DB, no NumPy. Decimal everywhere. The
actual order-placement loop (post limit, wait, cancel + market on
timeout) is delivered in a future iter when the live-trading path
is wired (anti-rule A1).

Reference :

* Cont, Kukanov, Stoikov (2014). *The Price Impact of Order Book
  Events*. Journal of Financial Econometrics 12(1) : 47-88. The
  same paper that grounds the doc 10 R6 microstructure gate ; the
  half-spread saving formalised here is the natural consequence
  of the same order-book imbalance dynamics.
* Doc 10 R9 critère mesurable I9 : "Slippage moyen <= 0.05 % par
  trade".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from emeraude.agent.reasoning.risk_manager import Side

if TYPE_CHECKING:
    from emeraude.infra.market_data import BookTicker


_ZERO: Final[Decimal] = Decimal("0")
_TWO: Final[Decimal] = Decimal("2")
_TEN_THOUSAND: Final[Decimal] = Decimal("10000")

# Doc 10 R9 default : a 50 bps spread is the upper bound where the
# patience cost of a passive limit still beats the market round-trip.
# Beyond this the book is too wide ; cross immediately.
DEFAULT_MAX_SPREAD_BPS_FOR_LIMIT: Final[Decimal] = Decimal("50")
# Default timeout for the future fill loop. Pure module does not use it
# but exposing it as a default keeps the operational knob centralised.
DEFAULT_LIMIT_TIMEOUT_SECONDS: Final[int] = 30


# ─── Result types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SmartLimitParams:
    """Configurable thresholds for :func:`decide_execution_plan`.

    Attributes:
        max_spread_bps_for_limit: when the instantaneous spread
            exceeds this value the planner recommends crossing
            (market) immediately rather than posting a passive
            limit. Default 50 bps per doc 10 R9 — beyond this
            the patience cost dominates.
        limit_timeout_seconds: how long the future fill-loop should
            wait on a posted limit before falling back to market.
            Pure module does not consume it ; surfaced for the
            downstream caller. Default 30 s.
    """

    max_spread_bps_for_limit: Decimal = DEFAULT_MAX_SPREAD_BPS_FOR_LIMIT
    limit_timeout_seconds: int = DEFAULT_LIMIT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Outcome of :func:`decide_execution_plan`.

    Both prices are computed unconditionally so the caller can fall
    through from limit to market without re-querying. The
    ``use_limit`` flag carries the recommendation.

    Attributes:
        side: LONG / SHORT — echoed back for audit clarity.
        limit_price: passive-side price (``bid`` for LONG,
            ``ask`` for SHORT).
        market_price: aggressive-side price (``ask`` for LONG,
            ``bid`` for SHORT).
        spread_bps: instantaneous spread in basis points.
        expected_market_slippage_bps: half-spread cost of crossing
            (positive). The expected save when posting limit instead.
        use_limit: ``True`` iff ``spread_bps <= params.max_spread_bps_for_limit``.
            ``False`` -> caller should send market immediately.
        params: thresholds used, for audit replay.
    """

    side: Side
    limit_price: Decimal
    market_price: Decimal
    spread_bps: Decimal
    expected_market_slippage_bps: Decimal
    use_limit: bool
    params: SmartLimitParams


# ─── Pure helpers ───────────────────────────────────────────────────────────


def _validate_book(book: BookTicker) -> None:
    """Raise on a degenerate or corrupt book."""
    if book.bid_price < _ZERO or book.ask_price < _ZERO:
        msg = f"book has negative side : bid={book.bid_price}, ask={book.ask_price}"
        raise ValueError(msg)
    if book.ask_price < book.bid_price:
        msg = f"inverted book : bid={book.bid_price} > ask={book.ask_price}"
        raise ValueError(msg)


def passive_side_price(book: BookTicker, side: Side) -> Decimal:
    """Return the price on the side that *waits* for the counter-party.

    * LONG (buy) -> we sit on the bid : the seller crosses to us.
    * SHORT (sell) -> we sit on the ask : the buyer crosses to us.

    Posting at this price captures the half-spread when a counter-
    party arrives. The trade-off is non-fill risk — the future
    fill loop's timeout + market-fallback handles that.

    Args:
        book: best bid/ask snapshot.
        side: trade direction.

    Returns:
        The passive-side limit price.

    Raises:
        ValueError: on negative or inverted book sides.
    """
    _validate_book(book)
    return book.bid_price if side is Side.LONG else book.ask_price


def cross_spread_price(book: BookTicker, side: Side) -> Decimal:
    """Return the price that fills *immediately* by crossing the spread.

    * LONG (buy) -> we pay the ask.
    * SHORT (sell) -> we hit the bid.

    This is the market-equivalent fill price ; expected slippage vs
    the mid is the half-spread (see
    :func:`expected_market_slippage_bps`).

    Args:
        book: best bid/ask snapshot.
        side: trade direction.

    Returns:
        The aggressive-side market-fill price.

    Raises:
        ValueError: on negative or inverted book sides.
    """
    _validate_book(book)
    return book.ask_price if side is Side.LONG else book.bid_price


def expected_market_slippage_bps(book: BookTicker) -> Decimal:
    """Half-spread expected slippage of crossing the book in bps.

    ``slippage_bps = (ask - bid) / 2 / mid * 10000``. Symmetric :
    LONG and SHORT have the same expected adverse slippage when
    crossing.

    Returns:
        Non-negative basis-point cost of an immediate market fill.
        ``Decimal("0")`` for a zero-spread book. ``Decimal("Infinity")``
        for a degenerate zero-mid book (defensive — never seen in
        production).

    Raises:
        ValueError: on negative or inverted book sides.
    """
    _validate_book(book)
    mid = (book.bid_price + book.ask_price) / _TWO
    if mid == _ZERO:
        return Decimal("Infinity")
    half_spread = (book.ask_price - book.bid_price) / _TWO
    return half_spread / mid * _TEN_THOUSAND


def compute_realized_slippage_bps(
    *,
    expected_price: Decimal,
    actual_price: Decimal,
    side: Side,
) -> Decimal:
    """Signed realized slippage in bps relative to the expected entry.

    Sign convention :

    * **Positive** = adverse slippage (we paid more on a LONG, or
      received less on a SHORT than we expected).
    * **Zero** = filled exactly at expected.
    * **Negative** = favourable slippage (we got the spread or a
      better price than expected).

    Formula :

    * LONG : ``(actual - expected) / expected * 10000``
    * SHORT : ``(expected - actual) / expected * 10000``

    The doc 10 R9 criterion I9 ("slippage moyen <= 0.05 %") averages
    this signed value over many trades. A passive-limit-fill that
    captured the half-spread shows up as a small *negative* here —
    desirable.

    Args:
        expected_price: the entry price the orchestrator emitted
            (typically the kline close at decision time, or the
            mid at order-placement time).
        actual_price: the realized fill price.
        side: trade direction.

    Returns:
        Signed slippage in basis points.

    Raises:
        ValueError: on non-positive ``expected_price`` (corrupt
            input — every real entry price is > 0).
    """
    if expected_price <= _ZERO:
        msg = f"expected_price must be > 0, got {expected_price}"
        raise ValueError(msg)
    diff = actual_price - expected_price if side is Side.LONG else expected_price - actual_price
    return diff / expected_price * _TEN_THOUSAND


# ─── Public API : combined planner ──────────────────────────────────────────


def decide_execution_plan(
    *,
    book: BookTicker,
    side: Side,
    params: SmartLimitParams | None = None,
) -> ExecutionPlan:
    """Compute the full execution plan for one prospective fill.

    Always returns both a ``limit_price`` (passive side) and a
    ``market_price`` (aggressive side) so the caller can implement
    the doc 10 R9 retry pattern : post the limit, wait, fall back
    to market on timeout — all without re-querying the book.

    The ``use_limit`` flag is the planner's recommendation : ``False``
    when the spread already exceeds ``params.max_spread_bps_for_limit``
    so any limit posting would only stretch the patience window
    without saving meaningfully more than crossing immediately.

    Args:
        book: best bid/ask snapshot from
            :func:`emeraude.infra.market_data.get_book_ticker`.
        side: LONG / SHORT.
        params: thresholds. Defaults to doc 10 R9 values
            (50 bps spread cap for the limit recommendation).

    Returns:
        An :class:`ExecutionPlan`.

    Raises:
        ValueError: forwarded from the helpers on negative /
            inverted books.
    """
    params = params or SmartLimitParams()
    limit_price = passive_side_price(book, side)
    market_price = cross_spread_price(book, side)
    mid = (book.bid_price + book.ask_price) / _TWO
    spread_bps = (
        Decimal("Infinity")
        if mid == _ZERO
        else (book.ask_price - book.bid_price) / mid * _TEN_THOUSAND
    )
    slippage_bps = expected_market_slippage_bps(book)
    use_limit = spread_bps <= params.max_spread_bps_for_limit
    return ExecutionPlan(
        side=side,
        limit_price=limit_price,
        market_price=market_price,
        spread_bps=spread_bps,
        expected_market_slippage_bps=slippage_bps,
        use_limit=use_limit,
        params=params,
    )
