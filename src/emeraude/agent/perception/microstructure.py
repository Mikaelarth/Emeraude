"""Microstructure execution gate (doc 10 R6).

Doc 10 §"R6 — Microstructure : order flow + spread" addresses lacuna
L6 (microstructure ignoree). After the multi-strategy signal fires,
this gate consults three free Binance public signals as last-line
filters before the order goes out :

1. **Bid-ask spread** (``/api/v3/ticker/bookTicker``) — reject if the
   spread exceeds ``max_spread_bps`` (default 15 bps = 0.15 %). A
   wide spread means crossing it costs more than the edge.
2. **Volume vs N-bar mean** (``/api/v3/klines`` 1m) — reject if the
   current bar's volume is below ``min_volume_ratio`` (default 30 %)
   of the trailing 20-bar average. Thin liquidity means slippage on
   any reasonable size.
3. **Taker buy ratio** (``/api/v3/aggTrades``) — fraction of taker
   volume that hit the ask (aggressive buys). Exposed raw ; the
   caller can require directional confirmation by passing
   ``direction="long"`` or ``"short"`` and a
   ``min_directional_taker_ratio`` (default 0.55).

The hard rejects (spread + volume) are ``rejet d'entree`` per doc 10.
The taker-buy filter is **optional** : doc 10 lists the ratio as a
signal without an explicit reject threshold ; we expose a directional
guard so the caller can opt in (signals confirming flow direction
make sense — chasing flow that opposes us does not).

Pure module : no I/O, no DB, no NumPy. Decimal everywhere. The HTTP
fetches live in :mod:`emeraude.infra.market_data` ; this module
operates on the parsed domain types.

Reference :

* Cont, Kukanov, Stoikov (2014). *The Price Impact of Order Book
  Events*. Journal of Financial Econometrics 12(1) : 47-88.
  Establishes that order-book imbalance and trade direction predict
  short-horizon price moves — exactly the signals this gate consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from emeraude.infra.market_data import AggTrade, BookTicker, Kline

_ZERO: Final[Decimal] = Decimal("0")
_TWO: Final[Decimal] = Decimal("2")
_TEN_THOUSAND: Final[Decimal] = Decimal("10000")

# Doc 10 R6 thresholds.
DEFAULT_MAX_SPREAD_BPS: Final[Decimal] = Decimal("15")  # 0.15 %
DEFAULT_MIN_VOLUME_RATIO: Final[Decimal] = Decimal("0.30")
DEFAULT_VOLUME_MA_PERIOD: Final[int] = 20
# Default directional confirmation threshold when the caller opts in.
# 0.55 = at least 55 % of taker volume on the side we want to trade.
DEFAULT_MIN_DIRECTIONAL_TAKER_RATIO: Final[Decimal] = Decimal("0.55")


# ─── Result types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MicrostructureParams:
    """Configurable thresholds for :func:`evaluate_microstructure`.

    Attributes:
        max_spread_bps: reject if ``(ask - bid) / mid * 10000`` exceeds
            this. Default 15 bps per doc 10 R6.
        min_volume_ratio: reject if ``current_volume / mean_volume``
            falls below this. Default 0.30 per doc 10 R6.
        volume_ma_period: window size for the trailing volume mean.
            Default 20 bars per doc 10 R6.
        min_directional_taker_ratio: when the caller passes
            ``direction``, require at least this fraction of taker
            volume on the matching side. Default 0.55. Ignored when
            ``direction`` is ``None``.
    """

    max_spread_bps: Decimal = DEFAULT_MAX_SPREAD_BPS
    min_volume_ratio: Decimal = DEFAULT_MIN_VOLUME_RATIO
    volume_ma_period: int = DEFAULT_VOLUME_MA_PERIOD
    min_directional_taker_ratio: Decimal = DEFAULT_MIN_DIRECTIONAL_TAKER_RATIO


@dataclass(frozen=True, slots=True)
class MicrostructureReport:
    """Outcome of :func:`evaluate_microstructure`.

    Attributes:
        spread_bps: instantaneous bid-ask spread in basis points.
        volume_ratio: current bar volume / trailing N-bar mean.
        taker_buy_ratio: aggressive-buy volume / total taker volume.
            ``Decimal("0.5")`` when no taker volume present.
        direction: directional intent passed by the caller (or
            ``None`` for no directional check).
        accepted: ``True`` iff every applicable filter passed.
        reasons: human-readable reasons the gate rejected. Empty
            when ``accepted is True``.
        params: thresholds used for this evaluation, for audit.
    """

    spread_bps: Decimal
    volume_ratio: Decimal
    taker_buy_ratio: Decimal
    direction: Literal["long", "short"] | None
    accepted: bool
    reasons: tuple[str, ...]
    params: MicrostructureParams


# ─── Pure helpers ───────────────────────────────────────────────────────────


def spread_bps(book: BookTicker) -> Decimal:
    """Relative bid-ask spread in basis points (10 000 bps = 100 %).

    ``spread_bps = (ask - bid) / mid * 10000`` where
    ``mid = (bid + ask) / 2``.

    Args:
        book: best bid/ask snapshot.

    Returns:
        Non-negative basis-point spread. ``Decimal("Infinity")`` when
        the mid is zero (degenerate book).

    Raises:
        ValueError: on negative bid or ask, or on an inverted book
            (``ask < bid``) — both indicate corrupt data.
    """
    if book.bid_price < _ZERO or book.ask_price < _ZERO:
        msg = f"book has negative side : bid={book.bid_price}, ask={book.ask_price}"
        raise ValueError(msg)
    if book.ask_price < book.bid_price:
        msg = f"inverted book : bid={book.bid_price} > ask={book.ask_price}"
        raise ValueError(msg)
    mid = (book.bid_price + book.ask_price) / _TWO
    if mid == _ZERO:
        return Decimal("Infinity")
    return (book.ask_price - book.bid_price) / mid * _TEN_THOUSAND


def volume_ratio(klines: list[Kline], *, period: int = DEFAULT_VOLUME_MA_PERIOD) -> Decimal:
    """Last bar's volume divided by the mean of the prior ``period`` bars.

    The denominator is the average of the ``period`` bars **preceding**
    the most recent one — never including the current bar itself, so
    the ratio is unbiased by its own value.

    Args:
        klines: chronological 1m klines, oldest first. Must contain
            at least ``period + 1`` entries (one current + ``period``
            history) to produce a meaningful ratio.
        period: window size for the mean. Default 20 per doc 10 R6.

    Returns:
        ``current_volume / mean_volume``. ``Decimal("Infinity")`` if
        the trailing mean is zero (unusual : truly dead market) and
        ``current_volume > 0``.

    Raises:
        ValueError: on ``period < 1`` or fewer than ``period + 1``
            klines (caller must back-pad).
    """
    if period < 1:
        msg = f"period must be >= 1, got {period}"
        raise ValueError(msg)
    if len(klines) < period + 1:
        msg = f"need at least period + 1 = {period + 1} klines, got {len(klines)}"
        raise ValueError(msg)
    current = klines[-1].volume
    history = klines[-(period + 1) : -1]
    mean = sum((k.volume for k in history), _ZERO) / Decimal(period)
    if mean == _ZERO:
        return Decimal("Infinity") if current > _ZERO else _ZERO
    return current / mean


def taker_buy_ratio(trades: list[AggTrade]) -> Decimal:
    """Fraction of taker volume that hit the ask (aggressive buys).

    Convention (Binance) : ``is_buyer_maker == False`` means the
    buyer was the **taker** — an aggressive buy that crossed the
    spread. ``is_buyer_maker == True`` means the buyer rested on
    the bid and a seller came in : aggressive sell.

    Args:
        trades: chronological aggregated trades over the recent
            window (doc 10 R6 uses ~60 s).

    Returns:
        ``aggressive_buy_volume / total_volume`` in ``[0, 1]``.
        ``Decimal("0.5")`` for an empty list (no information ->
        neutral) — chosen so that downstream directional checks
        with the default 0.55 threshold reject by default rather
        than wave through.
    """
    total = _ZERO
    aggressive_buy = _ZERO
    for t in trades:
        total += t.quantity
        if not t.is_buyer_maker:
            aggressive_buy += t.quantity
    if total == _ZERO:
        return Decimal("0.5")
    return aggressive_buy / total


# ─── Public API : combined gate ─────────────────────────────────────────────


def evaluate_microstructure(
    *,
    book: BookTicker,
    klines_1m: list[Kline],
    trades: list[AggTrade],
    direction: Literal["long", "short"] | None = None,
    params: MicrostructureParams | None = None,
) -> MicrostructureReport:
    """Run the doc 10 R6 microstructure gate.

    Filters applied :

    * **Spread filter** (always) : reject if
      ``spread_bps > params.max_spread_bps``.
    * **Volume filter** (always) : reject if the trailing-20 ratio
      is below ``params.min_volume_ratio``.
    * **Direction filter** (only when ``direction`` is set) : reject
      a long entry if ``taker_buy_ratio < min_directional_taker_ratio``
      or a short entry if ``(1 - taker_buy_ratio) < threshold``.

    Args:
        book: best bid/ask snapshot from
            :func:`emeraude.infra.market_data.get_book_ticker`.
        klines_1m: trailing 1-minute klines, oldest first, length
            >= ``params.volume_ma_period + 1``.
        trades: recent aggregated trades from
            :func:`emeraude.infra.market_data.get_agg_trades`.
        direction: ``"long"``, ``"short"``, or ``None`` to skip the
            directional check.
        params: thresholds. Defaults to the doc 10 R6 values.

    Returns:
        A :class:`MicrostructureReport`. ``accepted`` is ``True``
        iff every applicable filter passed.

    Raises:
        ValueError: on corrupt inputs (negative book, inverted book,
            insufficient kline history). Never on empty trades.
    """
    params = params or MicrostructureParams()

    spread = spread_bps(book)
    vol_ratio = volume_ratio(klines_1m, period=params.volume_ma_period)
    buy_ratio = taker_buy_ratio(trades)

    reasons: list[str] = []
    if spread > params.max_spread_bps:
        reasons.append(f"spread {spread} bps > max {params.max_spread_bps} bps")
    if vol_ratio < params.min_volume_ratio:
        reasons.append(f"volume ratio {vol_ratio} < min {params.min_volume_ratio}")
    if direction is not None:
        side_ratio = buy_ratio if direction == "long" else (Decimal("1") - buy_ratio)
        if side_ratio < params.min_directional_taker_ratio:
            reasons.append(
                f"taker {direction} ratio {side_ratio} < min {params.min_directional_taker_ratio}"
            )

    return MicrostructureReport(
        spread_bps=spread,
        volume_ratio=vol_ratio,
        taker_buy_ratio=buy_ratio,
        direction=direction,
        accepted=not reasons,
        reasons=tuple(reasons),
        params=params,
    )
