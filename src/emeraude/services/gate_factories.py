"""Concrete closures for the optional gates exposed by :class:`Orchestrator`.

Iter #40 made :class:`Orchestrator` accept two new optional callables :

* ``correlation_gate: Callable[[], CorrelationReport]`` (doc 10 R7)
* ``microstructure_gate: Callable[[TradeDirection], MicrostructureReport]``
  (doc 10 R6)

This module ships the **factories** that build those closures from
the public Binance fetchers in :mod:`emeraude.infra.market_data`.
Why a separate module rather than baking it into
:mod:`emeraude.services.auto_trader` ?

* **Single responsibility** — :class:`AutoTrader`'s job is the cycle
  loop. Composing gates is a different concern, useful in tests, in
  the future UI ("preview a decision now"), and in offline replays.
* **Loose coupling** — the gates close over their fetchers ; the
  caller chooses default Binance fetchers or test stubs without
  threading new arguments through :class:`AutoTrader`.
* **Testability** — every fetcher is a callable injected by keyword,
  so unit tests run with deterministic stubs and never touch the
  network.

Composition pattern (production caller) ::

    from emeraude.services import gate_factories
    from emeraude.services.orchestrator import Orchestrator

    correlation = gate_factories.make_correlation_gate(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    )
    microstructure = gate_factories.make_microstructure_gate(
        symbol="BTCUSDT",
    )
    orch = Orchestrator(
        correlation_gate=correlation,
        microstructure_gate=microstructure,
    )

The factories are **pure** (no I/O at construction time) ; the I/O
happens lazily inside the returned closures, on each cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from emeraude.agent.perception.correlation import (
    DEFAULT_STRESS_THRESHOLD,
    CorrelationReport,
    compute_correlation_report,
)
from emeraude.agent.perception.microstructure import (
    MicrostructureParams,
    MicrostructureReport,
    evaluate_microstructure,
)
from emeraude.infra import market_data
from emeraude.services.orchestrator import TradeDirection

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from emeraude.infra.market_data import AggTrade, BookTicker, Kline


# Doc 10 R7 default cohort window : 100 bars of 1h candles -> ~4 days
# of returns, a sane default for "are coins moving together right now".
_DEFAULT_CORRELATION_INTERVAL: Final[str] = "1h"
_DEFAULT_CORRELATION_LIMIT: Final[int] = 100

# Doc 10 R6 microstructure inputs :
# * 1m klines : 21 = 20-bar trailing mean + 1 current (matches
#   ``DEFAULT_VOLUME_MA_PERIOD`` in the microstructure module).
# * aggTrades : 500 covers ~60 s on a liquid pair without hitting the
#   Binance ceiling on a flush ; matches market_data's default.
_DEFAULT_MICRO_KLINES_INTERVAL: Final[str] = "1m"
_DEFAULT_MICRO_KLINES_LIMIT: Final[int] = 21
_DEFAULT_MICRO_TRADES_LIMIT: Final[int] = market_data.DEFAULT_AGG_TRADES_LIMIT


# ─── Correlation gate factory (doc 10 R7) ──────────────────────────────────


def make_correlation_gate(
    symbols: list[str],
    *,
    fetch_klines: Callable[[str], list[Kline]] | None = None,
    interval: str = _DEFAULT_CORRELATION_INTERVAL,
    limit: int = _DEFAULT_CORRELATION_LIMIT,
    threshold: Decimal = DEFAULT_STRESS_THRESHOLD,
) -> Callable[[], CorrelationReport]:
    """Build a closure that fetches multi-symbol klines and computes R7.

    The returned closure has signature ``() -> CorrelationReport`` —
    the shape :class:`Orchestrator` expects for its
    ``correlation_gate`` parameter. Each call refetches every symbol,
    aligns the resulting return series, and computes the cohort-level
    correlation report. There is no internal caching ; the orchestrator
    cycle is the natural rate-limit (one fire per cycle).

    Args:
        symbols: list of trading pairs to correlate. At least 2 are
            required ; the cohort-level correlation is meaningless on
            a single symbol.
        fetch_klines: per-symbol kline fetcher with signature
            ``(symbol) -> list[Kline]``. When ``None`` (default), the
            factory wraps :func:`market_data.get_klines` with the
            ``interval`` and ``limit`` arguments below. When the caller
            passes an explicit fetcher, the ``interval`` and ``limit``
            arguments are ignored — the caller is responsible for the
            kline window.
        interval: kline width passed to the default fetcher. Default
            ``"1h"`` per doc 10 R7. Ignored when ``fetch_klines`` is
            supplied.
        limit: number of bars per symbol passed to the default fetcher.
            Default ``100`` per doc 10 R7. Ignored when ``fetch_klines``
            is supplied.
        threshold: stress threshold forwarded to
            :func:`compute_correlation_report`. Default ``0.8`` per
            doc 10 R7.

    Returns:
        A no-arg closure suitable for
        :class:`Orchestrator(correlation_gate=...)`.

    Raises:
        ValueError: when fewer than 2 symbols are passed (the
            correlation cohort is degenerate below 2).
    """
    if len(symbols) < 2:  # noqa: PLR2004
        msg = f"need >= 2 symbols, got {len(symbols)}"
        raise ValueError(msg)

    fetcher: Callable[[str], list[Kline]]
    if fetch_klines is None:

        def _default_fetcher(sym: str) -> list[Kline]:
            return market_data.get_klines(sym, interval=interval, limit=limit)

        fetcher = _default_fetcher
    else:
        fetcher = fetch_klines

    # Snapshot the symbol list at factory time so post-construction
    # mutations of the caller's list cannot silently change the cohort.
    cohort = list(symbols)

    def gate() -> CorrelationReport:
        klines_by_symbol = {sym: fetcher(sym) for sym in cohort}
        return compute_correlation_report(klines_by_symbol, threshold=threshold)

    return gate


# ─── Microstructure gate factory (doc 10 R6) ───────────────────────────────


def make_microstructure_gate(
    symbol: str,
    *,
    fetch_book: Callable[[str], BookTicker] | None = None,
    fetch_klines_1m: Callable[[str], list[Kline]] | None = None,
    fetch_trades: Callable[[str], list[AggTrade]] | None = None,
    klines_limit: int = _DEFAULT_MICRO_KLINES_LIMIT,
    trades_limit: int = _DEFAULT_MICRO_TRADES_LIMIT,
    params: MicrostructureParams | None = None,
) -> Callable[[TradeDirection], MicrostructureReport]:
    """Build a closure that fetches book + klines_1m + trades and runs R6.

    The returned closure has signature
    ``(TradeDirection) -> MicrostructureReport`` — the shape
    :class:`Orchestrator` expects for its ``microstructure_gate``
    parameter. The orchestrator passes the intended trade direction so
    the gate can include the doc 10 R6 directional flow check
    (rejecting a long entry when taker volume is overwhelmingly on the
    sell side, and vice-versa).

    Args:
        symbol: trading pair to evaluate, uppercase Binance format.
        fetch_book: book ticker fetcher with signature
            ``(symbol) -> BookTicker``. Default
            :func:`market_data.get_book_ticker`.
        fetch_klines_1m: 1-minute kline fetcher with signature
            ``(symbol) -> list[Kline]``. Default wraps
            :func:`market_data.get_klines` with ``interval="1m"`` and
            ``limit=klines_limit``. The default 21-bar window matches
            ``DEFAULT_VOLUME_MA_PERIOD = 20`` plus the current bar.
        fetch_trades: aggregated-trades fetcher with signature
            ``(symbol) -> list[AggTrade]``. Default wraps
            :func:`market_data.get_agg_trades` with ``limit=trades_limit``.
        klines_limit: bar count for the default 1m fetcher. Ignored
            when ``fetch_klines_1m`` is supplied. Default 21.
        trades_limit: trade count for the default aggTrades fetcher.
            Ignored when ``fetch_trades`` is supplied. Default
            :data:`market_data.DEFAULT_AGG_TRADES_LIMIT` (500).
        params: forwarded to :func:`evaluate_microstructure`. Default
            :class:`MicrostructureParams()` (doc 10 R6 thresholds).

    Returns:
        A direction-taking closure suitable for
        :class:`Orchestrator(microstructure_gate=...)`.
    """
    book_fetcher = fetch_book if fetch_book is not None else market_data.get_book_ticker

    klines_fetcher: Callable[[str], list[Kline]]
    if fetch_klines_1m is None:

        def _default_klines(sym: str) -> list[Kline]:
            return market_data.get_klines(
                sym,
                interval=_DEFAULT_MICRO_KLINES_INTERVAL,
                limit=klines_limit,
            )

        klines_fetcher = _default_klines
    else:
        klines_fetcher = fetch_klines_1m

    trades_fetcher: Callable[[str], list[AggTrade]]
    if fetch_trades is None:

        def _default_trades(sym: str) -> list[AggTrade]:
            return market_data.get_agg_trades(sym, limit=trades_limit)

        trades_fetcher = _default_trades
    else:
        trades_fetcher = fetch_trades

    captured_params = params if params is not None else MicrostructureParams()

    def gate(direction: TradeDirection) -> MicrostructureReport:
        book = book_fetcher(symbol)
        klines_1m = klines_fetcher(symbol)
        trades = trades_fetcher(symbol)
        # Map TradeDirection (orchestrator enum) -> Literal the
        # microstructure module expects. Done at the seam, not inside
        # the perception layer, to keep the agent module decoupled
        # from the services layer's enum.
        direction_lit: Literal["long", "short"] = (
            "long" if direction is TradeDirection.LONG else "short"
        )
        return evaluate_microstructure(
            book=book,
            klines_1m=klines_1m,
            trades=trades,
            direction=direction_lit,
            params=captured_params,
        )

    return gate
