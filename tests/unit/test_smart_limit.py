"""Unit tests for emeraude.agent.execution.smart_limit (doc 10 R9)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emeraude.agent.execution.smart_limit import (
    DEFAULT_LIMIT_TIMEOUT_SECONDS,
    DEFAULT_MAX_SPREAD_BPS_FOR_LIMIT,
    ExecutionPlan,
    SmartLimitParams,
    compute_realized_slippage_bps,
    cross_spread_price,
    decide_execution_plan,
    expected_market_slippage_bps,
    passive_side_price,
)
from emeraude.agent.reasoning.risk_manager import Side
from emeraude.infra.market_data import BookTicker

# ─── Helpers ────────────────────────────────────────────────────────────────


def _book(bid: str, ask: str) -> BookTicker:
    return BookTicker(
        symbol="BTCUSDT",
        bid_price=Decimal(bid),
        bid_qty=Decimal("1"),
        ask_price=Decimal(ask),
        ask_qty=Decimal("1"),
    )


# ─── Defaults ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaults:
    def test_default_max_spread_doc10(self) -> None:
        # Doc 10 R9 defaults : 50 bps spread cap for the limit
        # recommendation, 30 s timeout for the future fill loop.
        assert Decimal("50") == DEFAULT_MAX_SPREAD_BPS_FOR_LIMIT
        assert DEFAULT_LIMIT_TIMEOUT_SECONDS == 30

    def test_params_defaults_match_module_constants(self) -> None:
        p = SmartLimitParams()
        assert p.max_spread_bps_for_limit == DEFAULT_MAX_SPREAD_BPS_FOR_LIMIT
        assert p.limit_timeout_seconds == DEFAULT_LIMIT_TIMEOUT_SECONDS


# ─── passive_side_price ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestPassiveSidePrice:
    def test_long_sits_on_bid(self) -> None:
        # LONG buyer waits for sellers to come down to bid.
        assert passive_side_price(_book("99.99", "100.01"), Side.LONG) == Decimal("99.99")

    def test_short_sits_on_ask(self) -> None:
        # SHORT seller waits for buyers to come up to ask.
        assert passive_side_price(_book("99.99", "100.01"), Side.SHORT) == Decimal("100.01")

    def test_inverted_book_raises(self) -> None:
        with pytest.raises(ValueError, match="inverted book"):
            passive_side_price(_book("101", "100"), Side.LONG)

    def test_negative_bid_raises(self) -> None:
        with pytest.raises(ValueError, match="negative side"):
            passive_side_price(_book("-1", "100"), Side.LONG)


# ─── cross_spread_price ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestCrossSpreadPrice:
    def test_long_pays_ask(self) -> None:
        # LONG market buy crosses the spread up to the ask.
        assert cross_spread_price(_book("99.99", "100.01"), Side.LONG) == Decimal("100.01")

    def test_short_hits_bid(self) -> None:
        # SHORT market sell crosses the spread down to the bid.
        assert cross_spread_price(_book("99.99", "100.01"), Side.SHORT) == Decimal("99.99")

    def test_inverted_book_raises(self) -> None:
        with pytest.raises(ValueError, match="inverted book"):
            cross_spread_price(_book("101", "100"), Side.SHORT)


# ─── expected_market_slippage_bps ───────────────────────────────────────────


@pytest.mark.unit
class TestExpectedMarketSlippageBps:
    def test_zero_spread_zero_slippage(self) -> None:
        # bid == ask : crossing costs nothing.
        assert expected_market_slippage_bps(_book("100", "100")) == Decimal("0")

    def test_one_bps_spread_yields_half_bps_slippage(self) -> None:
        # bid 99.995 ask 100.005 mid 100 spread 1 bps -> half = 0.5 bps.
        result = expected_market_slippage_bps(_book("99.995", "100.005"))
        assert result == Decimal("0.5")

    def test_fifty_bps_spread_yields_25_bps_slippage(self) -> None:
        # bid 99.75 ask 100.25 mid 100 spread 50 bps -> half = 25 bps.
        result = expected_market_slippage_bps(_book("99.75", "100.25"))
        assert result == Decimal("25")

    def test_symmetric_for_long_and_short(self) -> None:
        # Half-spread slippage is direction-agnostic by construction.
        # Slippage doesn't even take side as input here ; the test
        # asserts the contract of "same expected magnitude both ways".
        slip = expected_market_slippage_bps(_book("99.99", "100.01"))
        assert slip > Decimal("0")
        # No assertion on direction : the value is a magnitude.

    def test_zero_mid_returns_infinity(self) -> None:
        # Defensive : never seen on a real book.
        assert expected_market_slippage_bps(_book("0", "0")) == Decimal("Infinity")

    def test_inverted_book_raises(self) -> None:
        with pytest.raises(ValueError, match="inverted book"):
            expected_market_slippage_bps(_book("101", "100"))


# ─── compute_realized_slippage_bps ──────────────────────────────────────────


@pytest.mark.unit
class TestComputeRealizedSlippage:
    def test_long_paid_more_is_positive(self) -> None:
        # LONG expected 100, actually filled at 100.10 -> +10 bps adverse.
        slip = compute_realized_slippage_bps(
            expected_price=Decimal("100"),
            actual_price=Decimal("100.10"),
            side=Side.LONG,
        )
        assert slip == Decimal("10")

    def test_long_paid_less_is_negative(self) -> None:
        # LONG expected 100, filled at 99.90 (passive limit captured
        # the spread) -> -10 bps favourable.
        slip = compute_realized_slippage_bps(
            expected_price=Decimal("100"),
            actual_price=Decimal("99.90"),
            side=Side.LONG,
        )
        assert slip == Decimal("-10")

    def test_short_received_less_is_positive(self) -> None:
        # SHORT expected 100, filled at 99.90 (got less) -> +10 bps adverse.
        slip = compute_realized_slippage_bps(
            expected_price=Decimal("100"),
            actual_price=Decimal("99.90"),
            side=Side.SHORT,
        )
        assert slip == Decimal("10")

    def test_short_received_more_is_negative(self) -> None:
        # SHORT expected 100, filled at 100.10 (got more) -> -10 bps favourable.
        slip = compute_realized_slippage_bps(
            expected_price=Decimal("100"),
            actual_price=Decimal("100.10"),
            side=Side.SHORT,
        )
        assert slip == Decimal("-10")

    def test_exact_fill_zero(self) -> None:
        slip = compute_realized_slippage_bps(
            expected_price=Decimal("100"),
            actual_price=Decimal("100"),
            side=Side.LONG,
        )
        assert slip == Decimal("0")

    def test_zero_expected_raises(self) -> None:
        with pytest.raises(ValueError, match="expected_price must be > 0"):
            compute_realized_slippage_bps(
                expected_price=Decimal("0"),
                actual_price=Decimal("100"),
                side=Side.LONG,
            )

    def test_negative_expected_raises(self) -> None:
        with pytest.raises(ValueError, match="expected_price must be > 0"):
            compute_realized_slippage_bps(
                expected_price=Decimal("-1"),
                actual_price=Decimal("100"),
                side=Side.LONG,
            )


# ─── decide_execution_plan ──────────────────────────────────────────────────


@pytest.mark.unit
class TestDecideExecutionPlan:
    def test_returns_execution_plan_instance(self) -> None:
        plan = decide_execution_plan(
            book=_book("99.99", "100.01"),
            side=Side.LONG,
        )
        assert isinstance(plan, ExecutionPlan)

    def test_tight_spread_recommends_limit(self) -> None:
        # 2 bps spread, well below the 50 bps default cap.
        plan = decide_execution_plan(
            book=_book("99.99", "100.01"),
            side=Side.LONG,
        )
        assert plan.use_limit is True

    def test_wide_spread_recommends_market(self) -> None:
        # 100 bps spread, twice the default cap.
        plan = decide_execution_plan(
            book=_book("99.50", "100.50"),
            side=Side.LONG,
        )
        assert plan.use_limit is False

    def test_at_cap_recommends_limit_inclusive(self) -> None:
        # Spread exactly at 50 bps : <= cap is inclusive -> still
        # recommend limit (boundary behaviour matches doc 10 :
        # 50 bps is the upper bound where limit still wins).
        # bid 99.75 ask 100.25 mid 100 -> 50 bps.
        plan = decide_execution_plan(
            book=_book("99.75", "100.25"),
            side=Side.LONG,
        )
        assert plan.spread_bps == Decimal("50")
        assert plan.use_limit is True

    def test_long_plan_prices(self) -> None:
        plan = decide_execution_plan(
            book=_book("99.99", "100.01"),
            side=Side.LONG,
        )
        # LONG : limit on bid, market on ask.
        assert plan.limit_price == Decimal("99.99")
        assert plan.market_price == Decimal("100.01")
        assert plan.side is Side.LONG

    def test_short_plan_prices(self) -> None:
        plan = decide_execution_plan(
            book=_book("99.99", "100.01"),
            side=Side.SHORT,
        )
        # SHORT : limit on ask, market on bid.
        assert plan.limit_price == Decimal("100.01")
        assert plan.market_price == Decimal("99.99")
        assert plan.side is Side.SHORT

    def test_plan_carries_spread_and_slippage(self) -> None:
        plan = decide_execution_plan(
            book=_book("99.95", "100.05"),
            side=Side.LONG,
        )
        # 10 bps spread -> 5 bps half-spread slippage.
        assert plan.spread_bps == Decimal("10")
        assert plan.expected_market_slippage_bps == Decimal("5")

    def test_custom_params_override_default_cap(self) -> None:
        # 60 bps spread, default cap 50 -> recommend market.
        # Loosen to 70 -> recommend limit.
        book = _book("99.70", "100.30")  # 60 bps spread
        plan_default = decide_execution_plan(book=book, side=Side.LONG)
        assert plan_default.use_limit is False

        plan_loose = decide_execution_plan(
            book=book,
            side=Side.LONG,
            params=SmartLimitParams(max_spread_bps_for_limit=Decimal("70")),
        )
        assert plan_loose.use_limit is True

    def test_plan_carries_params_for_audit(self) -> None:
        custom = SmartLimitParams(max_spread_bps_for_limit=Decimal("42"))
        plan = decide_execution_plan(
            book=_book("99.99", "100.01"),
            side=Side.LONG,
            params=custom,
        )
        assert plan.params is custom

    def test_plan_is_immutable(self) -> None:
        plan = decide_execution_plan(
            book=_book("99.99", "100.01"),
            side=Side.LONG,
        )
        with pytest.raises((AttributeError, TypeError)):
            plan.use_limit = False  # type: ignore[misc]

    def test_inverted_book_raises(self) -> None:
        with pytest.raises(ValueError, match="inverted book"):
            decide_execution_plan(
                book=_book("101", "100"),
                side=Side.LONG,
            )


# ─── Doc 10 R9 narrative ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestDoc10R9Narrative:
    def test_passive_limit_captures_half_spread_long(self) -> None:
        """Limit at bid for LONG : if filled, slippage is the negative
        half-spread vs the mid (we got the spread)."""
        book = _book("99.95", "100.05")
        plan = decide_execution_plan(book=book, side=Side.LONG)
        mid = (book.bid_price + book.ask_price) / Decimal("2")

        # Realized fill at the limit price :
        slip = compute_realized_slippage_bps(
            expected_price=mid,
            actual_price=plan.limit_price,
            side=Side.LONG,
        )
        # Negative = favourable. Magnitude = half-spread = 5 bps.
        assert slip == Decimal("-5")

    def test_market_fallback_pays_half_spread_long(self) -> None:
        """Market for LONG : pays the ask, slippage = +half-spread."""
        book = _book("99.95", "100.05")
        plan = decide_execution_plan(book=book, side=Side.LONG)
        mid = (book.bid_price + book.ask_price) / Decimal("2")

        slip = compute_realized_slippage_bps(
            expected_price=mid,
            actual_price=plan.market_price,
            side=Side.LONG,
        )
        # Positive = adverse. Magnitude = half-spread = 5 bps.
        assert slip == Decimal("5")

    def test_doc10_i9_threshold_is_5_bps(self) -> None:
        """Doc 10 I9 : 'Slippage moyen <= 0.05 % par trade' = 5 bps.

        A trade at the limit (passive fill) saves 5 bps ; a trade at
        market pays 5 bps. The average over a long-enough horizon
        depends on the limit fill rate. With a 50 % fill rate the
        net average is 0 ; below 50 % the bot underperforms a naive
        market order. The doc 10 R9 retry pattern (limit then market
        on timeout) is designed to keep the average net negative.
        """
        # Sanity check on the criterion magnitude.
        i9_threshold_bps = Decimal("0.05") * _ten_thousand() / Decimal("100")
        assert i9_threshold_bps == Decimal("5")


def _ten_thousand() -> Decimal:
    """Local helper avoiding the import of the module-private constant."""
    return Decimal("10000")
