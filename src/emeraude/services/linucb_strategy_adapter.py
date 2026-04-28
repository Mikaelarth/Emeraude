"""LinUCB-as-StrategyBandit adapter (doc 10 R14 wiring).

Doc 10 §"R14 — Bandit contextuel" delivers
:class:`emeraude.agent.learning.linucb.LinUCBBandit` (Li et al. 2010).
Its native API is **winner-take-all** : :meth:`select` returns the
single arm with the highest UCB score, :meth:`update` records one
realized reward against that arm.

The :class:`Orchestrator` (iter #28) was wired to a
**weighted-ensemble** bandit (:class:`StrategyBandit` Thompson) that
returns one ``[0, 1]`` multiplier *per* strategy. Migrating the
orchestrator to a winner-take-all paradigm would lose the smooth
per-strategy contribution that the doc 04 ensemble vote builds on.

This module is the **adapter** that lets LinUCB plug into the
ensemble path without refactoring it :

* :class:`LinUCBStrategyAdapter` wraps a :class:`LinUCBBandit` and
  satisfies the :class:`StrategyBanditLike` Protocol from
  :mod:`emeraude.agent.learning.bandit`. The orchestrator (and the
  :class:`PositionTracker` that calls ``update_outcome`` on close)
  now accepts either a Thompson bandit or a LinUCB adapter via the
  same ``bandit`` parameter.
* :func:`build_regime_context` ships a **simple 3-D one-hot
  context** (BULL / NEUTRAL / BEAR). The doc 10 R14 vision describes
  richer features (volatility, hour, average correlation) ; we
  start with the regime-only context per anti-rule A1 and grow the
  feature set in a future iter once the LinUCB shows traction on
  the simple version.

Composition pattern ::

    from emeraude.agent.learning.linucb import LinUCBBandit
    from emeraude.agent.perception.regime import Regime
    from emeraude.services.linucb_strategy_adapter import (
        LinUCBStrategyAdapter,
        build_regime_context,
    )
    from emeraude.services.orchestrator import Orchestrator

    # 3 strategies, 3-D context (regime one-hot).
    bandit = LinUCBBandit(arms=["a", "b", "c"], context_dim=3)
    adapter = LinUCBStrategyAdapter(bandit=bandit)

    # Each cycle, update the context BEFORE the orchestrator runs.
    adapter.set_context(build_regime_context(Regime.BULL))
    orchestrator = Orchestrator(bandit=adapter, ...)
    orchestrator.make_decision(...)

The context-set call happens **outside** the orchestrator (which
remains context-agnostic) — caller is responsible for keeping the
adapter's context fresh. When :meth:`update_outcome` is called by
the tracker on trade close, the adapter uses the **current**
context. This is a v1 simplification : a richer per-trade-context
adapter can be built later.

Reference :

* Li, Chu, Langford, Schapire (2010). *A Contextual-Bandit Approach
  to Personalized News Article Recommendation*. WWW '10.
* Doc 10 §"R14" critère mesurable I14 : "LinUCB choisit la
  stratégie spécialisée du régime".
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from emeraude.agent.learning.linucb import LinUCBBandit
    from emeraude.agent.perception.regime import Regime


_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

# Default minimum weight a non-winning arm receives. Without a floor,
# LinUCB scores can be all-zero on cold start which would zero out
# every strategy in the ensemble vote. The floor keeps every strategy
# at least 1 % of its regime-base weight so the ensemble never
# collapses.
DEFAULT_FLOOR: Final[Decimal] = Decimal("0.01")


# ─── Context builder ───────────────────────────────────────────────────────


def build_regime_context(regime: Regime) -> list[Decimal]:
    """Encode a regime as a 3-D one-hot feature vector.

    Order matches :class:`Regime` enum : ``[BULL, NEUTRAL, BEAR]``.
    Compatible with a :class:`LinUCBBandit` constructed with
    ``context_dim=3``.

    Args:
        regime: the perceived market regime at decision time.

    Returns:
        A 3-element ``Decimal`` list with exactly one ``1`` and
        two ``0``s.
    """
    # Local import keeps the runtime cycle through Regime explicit
    # without polluting the module-import graph at TYPE_CHECKING-only
    # time. Regime is a StrEnum so equality check is cheap.
    from emeraude.agent.perception.regime import Regime as _Regime  # noqa: PLC0415

    return [
        _ONE if regime is _Regime.BULL else _ZERO,
        _ONE if regime is _Regime.NEUTRAL else _ZERO,
        _ONE if regime is _Regime.BEAR else _ZERO,
    ]


# ─── Adapter ───────────────────────────────────────────────────────────────


class LinUCBStrategyAdapter:
    """Adapter wrapping LinUCBBandit to satisfy the StrategyBanditLike Protocol.

    The wrapped bandit's UCB scores are normalized into ``[floor, 1]``
    multipliers : the highest-scoring arm gets ``1.0`` and every
    other arm gets ``score / max_score`` with a floor at
    ``floor``. The floor (default ``0.01``) keeps every strategy
    in the ensemble even when the bandit strongly favours one — the
    doc 04 ensemble vote breaks down if every weight is zero.

    Construct once at process start, call :meth:`set_context` before
    each decision, plug the adapter into the orchestrator's
    ``bandit`` parameter.

    Stateful : holds the current context vector. The adapter is
    **not** thread-safe — the doc 05 cycle is single-threaded.
    """

    def __init__(
        self,
        *,
        bandit: LinUCBBandit,
        floor: Decimal = DEFAULT_FLOOR,
    ) -> None:
        """Wire the adapter.

        Args:
            bandit: the :class:`LinUCBBandit` to wrap.
            floor: minimum multiplier returned for any arm. Default
                ``0.01``. Must be in ``[0, 1]``.

        Raises:
            ValueError: on ``floor`` outside ``[0, 1]``.
        """
        if not (_ZERO <= floor <= _ONE):
            msg = f"floor must be in [0, 1], got {floor}"
            raise ValueError(msg)
        self._bandit = bandit
        self._floor = floor
        # The current context vector. The orchestrator does not pass
        # context — the caller sets it via :meth:`set_context` before
        # each cycle.
        self._context: list[Decimal] | None = None

    # ─── Context plumbing ───────────────────────────────────────────────────

    def set_context(self, context: list[Decimal]) -> None:
        """Set the context vector for the next sample / update calls.

        Validates the dimension against the wrapped bandit. The
        same context is used for both :meth:`sample_weights` (decision
        time) and :meth:`update_outcome` (trade close) — a v1
        simplification ; a per-trade context store is a future iter.

        Args:
            context: feature vector of length ``bandit.context_dim``.

        Raises:
            ValueError: on dimension mismatch.
        """
        if len(context) != self._bandit.context_dim:
            msg = f"context must have dimension {self._bandit.context_dim}, got {len(context)}"
            raise ValueError(msg)
        self._context = list(context)

    @property
    def context(self) -> list[Decimal] | None:
        """Read-only view of the current context (or ``None`` if unset)."""
        return None if self._context is None else list(self._context)

    # ─── StrategyBanditLike Protocol ────────────────────────────────────────

    def sample_weights(self, strategies: list[str]) -> dict[str, Decimal]:
        """Per-strategy multipliers derived from the LinUCB UCB scores.

        Algorithm :

        1. Compute the UCB score for every requested strategy at
           the current context.
        2. Find the maximum.
        3. Normalize so the top arm gets ``1.0`` ; others get
           ``score / max_score``, floored at ``self._floor``.

        Edge cases :

        * **No context set** : returns uniform ``1.0`` weights — the
          bandit declines to express a preference. The orchestrator
          falls through to its regime-base weights unchanged.
        * **All scores ``<= 0``** : same uniform fallback (the
          ensemble vote never zeroes-out, doc 04 mandate).
        * **Unknown strategy** : raises a ``ValueError`` from
          :meth:`LinUCBBandit.score` — the caller must keep the
          orchestrator's strategy set in sync with the bandit's arms.
        """
        if self._context is None:
            return dict.fromkeys(strategies, _ONE)

        scores = {arm: self._bandit.score(arm, self._context) for arm in strategies}
        max_score = max(scores.values())
        if max_score <= _ZERO:
            return dict.fromkeys(strategies, _ONE)

        weights: dict[str, Decimal] = {}
        for arm, score in scores.items():
            normalized = score / max_score
            weights[arm] = max(self._floor, normalized)
        return weights

    def update_outcome(self, strategy: str, *, won: bool) -> None:
        """Forward a trade outcome to the wrapped LinUCB.

        Reward convention :

        * ``won=True``  -> reward ``1.0``.
        * ``won=False`` -> reward ``0.0``.

        The trivial 0/1 mapping matches the orchestrator's
        :meth:`StrategyBandit.update_outcome` semantic. A future iter
        could pass the realized R-multiple instead.

        When no context has been set yet, the call is a no-op : the
        wrapped LinUCB cannot be updated without a feature vector.
        This matches the v1 design where the caller is responsible
        for keeping the context fresh.
        """
        if self._context is None:
            return
        reward = _ONE if won else _ZERO
        self._bandit.update(arm=strategy, context=self._context, reward=reward)
