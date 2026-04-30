"""D1 Look-ahead bias guard — shift-invariance tests on every indicator.

Doc 11 §"D1 — Look-ahead bias (le plus dangereux)" demands a test
that "décaler la série de N bars dans le futur ne change rien au
signal calculé sur la fenêtre passée. Si ça change → fuite détectée."

Translated to our codebase, every indicator function in
:mod:`emeraude.agent.perception.indicators` :

* takes a ``list[Decimal]`` (or ``list[Kline]``) and a period,
* returns a scalar at the **last** position of the list.

Per that contract, the function MUST satisfy three properties :

1. **Determinism** — same input -> same output. Two consecutive calls
   with the exact same arguments yield byte-equal results. Catches
   any non-deterministic dependency (random, time, global state).
2. **Input integrity** — the input list is not mutated as a side
   effect. The caller can pass a series and reuse it afterwards.
3. **Future independence** — the result computed on ``values[:t]``
   is independent of any data appended *afterwards*. Catches any
   read past the current position (the actual look-ahead bias).

The first two are trivially testable. The third is verified by
interleaving calls : compute on a truncated slice, then compute on
the full series, then on the truncated slice again — the early and
late truncated-slice results MUST match. Any global cache / hidden
shared state that depends on call order would reveal itself here.

This module does NOT modify the indicators ; the suite documents
the contract and locks it in for future regressions.

Patterns NOT covered by this iter (out of scope) :

* :mod:`emeraude.agent.perception.regime` — signature consumes a
  list of klines + EMA period + slope window, more complex API.
* :mod:`emeraude.agent.perception.correlation` — multi-symbol input.
* Strategy signal generators — call indicators internally, so they
  inherit the property transitively, but a direct test would still
  be valuable.

These can be added by appending entries to the fixtures below.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.perception.indicators import (
    atr,
    bollinger_bands,
    ema,
    macd,
    rsi,
    sma,
    stochastic,
)
from emeraude.infra.market_data import Kline

# ─── Synthetic series generators ────────────────────────────────────────────


def _scalar_series(n: int = 60) -> list[Decimal]:
    """Deterministic non-trivial price series of length ``n``.

    A pure-Python sine-like wave with monotonic drift, distinct enough
    to make every indicator return a non-degenerate value (no
    ``None``, no ``Decimal("0")`` artefacts) for the typical periods
    we test (period 14, 20, 26, etc.).
    """
    # Coefficients chosen so the resulting series varies enough to
    # exercise gain / loss tracking (RSI), variance (Bollinger),
    # cross-overs (MACD). No randomness — pure deterministic.
    return [
        Decimal(100) + Decimal(i) * Decimal("0.5") + Decimal((i * 13) % 17) * Decimal("0.3")
        for i in range(n)
    ]


def _kline_series(n: int = 60) -> list[Kline]:
    """Deterministic OHLCV series of length ``n``.

    Built atop :func:`_scalar_series` so the close prices match the
    scalar tests ; high = close + 1, low = close - 1, volume = 10 +
    (i % 5) for variation. open_time / close_time use a 60-second
    cadence starting at epoch 1_700_000_000_000 ms.
    """
    base_prices = _scalar_series(n)
    klines: list[Kline] = []
    for i, close in enumerate(base_prices):
        open_time = 1_700_000_000_000 + i * 60_000
        close_time = open_time + 59_999
        klines.append(
            Kline(
                open_time=open_time,
                open=close - Decimal("0.5"),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal(10 + (i % 5)),
                close_time=close_time,
                n_trades=5 + (i % 7),
            )
        )
    return klines


# ─── Property assertions ────────────────────────────────────────────────────


def _assert_no_lookahead_scalar(
    func: object,
    name: str,
    values: list[Decimal],
    **kwargs: object,
) -> None:
    """Verify the 3-property contract on a scalar-input indicator.

    Order matters : we measure the **pristine** result on the truncated
    slice FIRST (before any full-series call could pollute hidden
    state), then run the polluting full-series call, then re-measure
    the truncated result. The helper would not catch a future-
    dependence bug if it ran the full call first — the pollution
    would already be in place when the "reference" truncated result
    is captured.

    Args:
        func: callable like ``sma`` or ``rsi``.
        name: human-friendly name for the assertion message.
        values: input series. Must be long enough that the indicator
            returns a non-``None`` value (caller picks a generous size).
        **kwargs: forwarded as keyword arguments to ``func``.
    """
    n = len(values)
    truncation_offset = 5  # drop the last 5 bars to leave room for the future
    truncated = values[: n - truncation_offset]
    truncated_snapshot = list(truncated)
    full_snapshot = list(values)

    # 1. Pristine truncated result — measured BEFORE anything else
    # could pollute hidden state. This is the reference value.
    result_truncated_pristine = func(truncated, **kwargs)  # type: ignore[operator]
    assert truncated == truncated_snapshot, f"{name} mutated truncated input"
    assert result_truncated_pristine is not None, (
        f"{name} returned None on truncated slice — fixture too short"
    )

    # 2. Determinism on the same truncated input (no pollution yet).
    result_truncated_repeat = func(truncated, **kwargs)  # type: ignore[operator]
    assert result_truncated_pristine == result_truncated_repeat, f"{name} is not deterministic"
    assert truncated == truncated_snapshot, f"{name} mutated truncated input"

    # 3. Pollution call : full series. If the indicator caches anything
    # global keyed by call order rather than input, this is where the
    # contamination lands.
    full_result = func(values, **kwargs)  # type: ignore[operator]
    assert values == full_snapshot, f"{name} mutated full input"
    assert full_result is not None, f"{name} returned None on full slice"

    # 4. Future independence : after the polluting call, the truncated
    # result MUST still match the pristine one. Any hidden global
    # state would surface here.
    result_truncated_after_pollution = func(truncated, **kwargs)  # type: ignore[operator]
    assert result_truncated_pristine == result_truncated_after_pollution, (
        f"{name} returned different results on the same truncated slice "
        f"after interleaving a call on the full series : "
        f"pristine={result_truncated_pristine}, "
        f"after_pollution={result_truncated_after_pollution}"
    )


def _assert_no_lookahead_klines(
    func: object,
    name: str,
    klines: list[Kline],
    **kwargs: object,
) -> None:
    """Same as :func:`_assert_no_lookahead_scalar` but for ``Kline``-input
    indicators (``atr``, ``stochastic``).

    Klines are dataclasses (frozen=True, slots=True) so mutation
    surface is the list itself, not the elements.
    """
    n = len(klines)
    truncation_offset = 5
    truncated = klines[: n - truncation_offset]
    truncated_snapshot = list(truncated)
    full_snapshot = list(klines)

    # 1. Pristine truncated result — measured BEFORE pollution.
    result_truncated_pristine = func(truncated, **kwargs)  # type: ignore[operator]
    assert truncated == truncated_snapshot, f"{name} mutated truncated input"
    assert result_truncated_pristine is not None, (
        f"{name} returned None on truncated slice — fixture too short"
    )

    # 2. Determinism on truncated.
    result_truncated_repeat = func(truncated, **kwargs)  # type: ignore[operator]
    assert result_truncated_pristine == result_truncated_repeat, f"{name} is not deterministic"
    assert truncated == truncated_snapshot, f"{name} mutated truncated input"

    # 3. Pollution call : full series.
    full_result = func(klines, **kwargs)  # type: ignore[operator]
    assert klines == full_snapshot, f"{name} mutated full input"
    assert full_result is not None, f"{name} returned None on full slice"

    # 4. Future independence : truncated result stable after pollution.
    result_truncated_after_pollution = func(truncated, **kwargs)  # type: ignore[operator]
    assert result_truncated_pristine == result_truncated_after_pollution, (
        f"{name} returned different results on the same truncated slice "
        f"after interleaving a call on the full series : "
        f"pristine={result_truncated_pristine}, "
        f"after_pollution={result_truncated_after_pollution}"
    )


# ─── Per-indicator tests ────────────────────────────────────────────────────


@pytest.mark.unit
class TestScalarIndicators:
    """One test per scalar-input indicator."""

    def test_sma(self) -> None:
        _assert_no_lookahead_scalar(sma, "sma", _scalar_series(40), period=10)

    def test_ema(self) -> None:
        _assert_no_lookahead_scalar(ema, "ema", _scalar_series(40), period=10)

    def test_rsi(self) -> None:
        _assert_no_lookahead_scalar(rsi, "rsi", _scalar_series(40), period=14)

    def test_macd(self) -> None:
        _assert_no_lookahead_scalar(macd, "macd", _scalar_series(60), fast=12, slow=26, signal=9)

    def test_bollinger_bands(self) -> None:
        _assert_no_lookahead_scalar(
            bollinger_bands,
            "bollinger_bands",
            _scalar_series(40),
            period=20,
            std_dev=2.0,
        )


@pytest.mark.unit
class TestKlineIndicators:
    """One test per kline-input indicator."""

    def test_atr(self) -> None:
        _assert_no_lookahead_klines(atr, "atr", _kline_series(40), period=14)

    def test_stochastic(self) -> None:
        _assert_no_lookahead_klines(
            stochastic,
            "stochastic",
            _kline_series(40),
            period=14,
            smooth_k=3,
            smooth_d=3,
        )


# ─── Sanity check : helpers detect a known buggy fake ───────────────────────


@pytest.mark.unit
class TestHelperCatchesBugs:
    """Build deliberately-buggy "indicators" and verify the helpers
    catch them. Locks in that the helpers are not silently passing
    every input.
    """

    def test_helper_catches_mutation(self) -> None:
        def buggy_mutates(values: list[Decimal], period: int) -> Decimal | None:
            del period  # unused
            if values:
                values.append(Decimal("999"))  # mutates input
            return Decimal("1")

        with pytest.raises(AssertionError, match="mutated truncated input"):
            _assert_no_lookahead_scalar(
                buggy_mutates, "buggy_mutates", _scalar_series(20), period=5
            )

    def test_helper_catches_non_determinism(self) -> None:
        counter = {"n": 0}

        def buggy_counter(values: list[Decimal], period: int) -> Decimal | None:
            del values, period
            counter["n"] += 1
            return Decimal(counter["n"])

        with pytest.raises(AssertionError, match="not deterministic"):
            _assert_no_lookahead_scalar(
                buggy_counter, "buggy_counter", _scalar_series(20), period=5
            )

    def test_helper_catches_future_dependence(self) -> None:
        # A function whose output for `truncated` depends on whether
        # the full-series call ran before. Hidden global state that
        # the helper's interleaved-call check is designed to flag.
        last_seen_len = {"n": 0}

        def buggy_lookahead(values: list[Decimal], period: int) -> Decimal | None:
            del period
            # Output depends on the LARGEST input seen so far — a
            # plausible-looking "cache" bug that leaks future bars.
            last_seen_len["n"] = max(last_seen_len["n"], len(values))
            return Decimal(last_seen_len["n"])

        with pytest.raises(AssertionError, match="different results on the same truncated slice"):
            _assert_no_lookahead_scalar(
                buggy_lookahead, "buggy_lookahead", _scalar_series(20), period=5
            )


# ─── Fixture sanity ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFixtureSanity:
    """Cheap smoke that the synthetic series are usable."""

    def test_scalar_series_distinct_values(self) -> None:
        # The drift + (i*13)%17 mod ensures most consecutive values
        # differ, so RSI / MACD have non-trivial deltas to chew on.
        series = _scalar_series(40)
        deltas = [series[i] - series[i - 1] for i in range(1, len(series))]
        assert any(d != Decimal("0") for d in deltas)
        # Both gains and losses are present — required for RSI to
        # exercise both branches.
        assert any(d > Decimal("0") for d in deltas)
        assert any(d < Decimal("0") for d in deltas)

    def test_kline_series_well_formed(self) -> None:
        klines = _kline_series(20)
        assert len(klines) == 20
        for k in klines:
            # Every kline satisfies the data-quality D3 invariants
            # (low <= close <= high, high >= low).
            assert k.high >= k.low
            assert k.low <= k.close <= k.high
        # Cadence is constant : 60 seconds between consecutive close_time.
        deltas = [klines[i].close_time - klines[i - 1].close_time for i in range(1, len(klines))]
        assert all(d == 60_000 for d in deltas)
