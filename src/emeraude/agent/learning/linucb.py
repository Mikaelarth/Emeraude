"""Contextual bandit — Linear UCB (Li, Chu, Langford, Schapire 2010).

Doc 10 §"R14 — Bandit contextuel (LinUCB) au lieu d'UCB1 plat"
addresses the structural limitation of the iter-#11 Thompson bandit
plus iter-#25 adaptive RegimeMemory : both factor the choice as
``argmax_strategy(regime)`` followed by ``argmax_param(strategy)``,
which is a coarse decomposition. A **contextual bandit** unifies
the two by learning a linear reward model conditioned on a feature
vector :

    E[r_t | a, x_t] = theta_a^T * x_t

where ``x_t`` is the current context (regime, volatility, hour UTC,
distance from ATH, average correlation R7, ...). At each cycle the
bandit picks the arm ``a`` maximizing :

    score_a = theta_a^T * x_t  +  alpha * sqrt(x_t^T * A_a^{-1} * x_t)

The first term is the linear-regression mean ; the second is the
UCB exploration bonus, modulated by the context (uncertain in
under-explored regions of the context space).

Online updates are O(d^2) per reward via the **Sherman-Morrison**
rank-1 formula :

    (A + x x^T)^{-1} = A^{-1} - (A^{-1} x x^T A^{-1}) / (1 + x^T A^{-1} x)

so the bandit never inverts a matrix from scratch after construction.

Pure Python : matrices are ``list[list[Decimal]]``, vectors are
``list[Decimal]``. No NumPy dependency. Decimal everywhere ;
``getcontext().sqrt`` for the UCB bonus.

This iteration ships the **algorithm**. Wiring into
:class:`Orchestrator` (replacing or blending with the existing
:class:`StrategyBandit`) is deferred per anti-rule A1 — the
substitution should be measured against UCB1+RegimeMemory on real
trade history before it lands.

References :

* Li, Chu, Langford, Schapire (2010). *A Contextual-Bandit Approach
  to Personalized News Article Recommendation*. WWW '10.
* Sherman & Morrison (1950). *Adjustment of an Inverse Matrix
  Corresponding to a Change in One Element of a Given Matrix*.
  Annals of Mathematical Statistics 21(1).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext
from typing import Final

_ZERO: Final[Decimal] = Decimal("0")
_ONE: Final[Decimal] = Decimal("1")

# Doc 10 R14 default exploration weight. alpha = 1.0 is the canonical
# Li et al. (2010) "moderate exploration" choice.
DEFAULT_ALPHA: Final[Decimal] = Decimal("1.0")
# Ridge regularization on the initial A_a matrix : A_a starts at
# lambda_reg * I so the inverse exists from the first call.
DEFAULT_LAMBDA_REG: Final[Decimal] = Decimal("1.0")


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LinUCBArmState:
    """Audit-friendly snapshot of one arm's learned parameters.

    Attributes:
        name: arm identifier.
        theta: estimated coefficients ``A^{-1} * b``. Length ``d``.
        n_updates: number of rewards observed for this arm.
    """

    name: str
    theta: list[Decimal]
    n_updates: int


# ─── Linear-algebra helpers (pure Python Decimal) ──────────────────────────


def _eye(d: int, *, scale: Decimal = _ONE) -> list[list[Decimal]]:
    """``scale * I`` of dimension ``d``."""
    return [[scale if i == j else _ZERO for j in range(d)] for i in range(d)]


def _matvec(matrix: list[list[Decimal]], vec: list[Decimal]) -> list[Decimal]:
    """Matrix-vector product ``M @ v``."""
    return [sum((row[j] * vec[j] for j in range(len(vec))), _ZERO) for row in matrix]


def _dot(u: list[Decimal], v: list[Decimal]) -> Decimal:
    """Inner product ``u . v``."""
    return sum((a * b for a, b in zip(u, v, strict=True)), _ZERO)


def _outer(u: list[Decimal], v: list[Decimal]) -> list[list[Decimal]]:
    """Outer product ``u v^T`` — yields a ``len(u) x len(v)`` matrix."""
    return [[ui * vj for vj in v] for ui in u]


def _scalar_mat(s: Decimal, m: list[list[Decimal]]) -> list[list[Decimal]]:
    """``s * M`` (element-wise)."""
    return [[s * x for x in row] for row in m]


def _mat_sub(
    a: list[list[Decimal]],
    b: list[list[Decimal]],
) -> list[list[Decimal]]:
    """``A - B`` (element-wise)."""
    return [
        [aij - bij for aij, bij in zip(row_a, row_b, strict=True)]
        for row_a, row_b in zip(a, b, strict=True)
    ]


def _sherman_morrison_update(
    a_inv: list[list[Decimal]],
    x: list[Decimal],
) -> list[list[Decimal]]:
    """Return ``(A + x x^T)^{-1}`` given ``A^{-1}`` and ``x``.

    Standard Sherman-Morrison rank-1 update :

        new_inv = A_inv - (A_inv x x^T A_inv) / (1 + x^T A_inv x)

    Cost : O(d^2). Numerically stable while ``1 + x^T A_inv x`` stays
    well above zero (which it does when ``A_inv`` is positive
    definite — guaranteed by initialization at ``(1/lambda) * I``).
    """
    a_inv_x = _matvec(a_inv, x)  # u = A_inv * x   (length d)
    denom = _ONE + _dot(x, a_inv_x)  # 1 + x^T u
    # Outer product u u^T then divided by denom.
    correction_numerator = _outer(a_inv_x, a_inv_x)
    correction = _scalar_mat(_ONE / denom, correction_numerator)
    return _mat_sub(a_inv, correction)


# ─── LinUCBBandit ───────────────────────────────────────────────────────────


class LinUCBBandit:
    """Contextual bandit using Linear UCB.

    Construct with the list of arm names and the context dimension ;
    call :meth:`select` to choose an arm given the current context,
    and :meth:`update` once the realized reward is known.

    The bandit maintains, per arm :

    * ``A^{-1}`` : ``d x d`` inverse of the regularized design matrix.
      Initialized at ``(1/lambda_reg) * I``. Updated via
      Sherman-Morrison on each reward.
    * ``b`` : ``d``-vector of weighted rewards, summed per arm.

    The estimated coefficients ``theta = A^{-1} b`` can be read via
    :meth:`state`. They are not cached — recomputed on demand because
    they cost O(d^2) and audit reads are infrequent.

    Tie-breaking : when several arms share the maximum score (e.g. on
    the very first cycle when all priors agree), the alphabetically
    smallest arm name wins. This makes the bandit deterministic given
    its context history.
    """

    def __init__(
        self,
        *,
        arms: list[str],
        context_dim: int,
        alpha: Decimal = DEFAULT_ALPHA,
        lambda_reg: Decimal = DEFAULT_LAMBDA_REG,
    ) -> None:
        """Wire the bandit.

        Args:
            arms: list of arm identifiers. Must be non-empty and
                contain unique strings.
            context_dim: dimension ``d`` of the context vector.
                Must be >= 1.
            alpha: exploration weight (UCB bonus multiplier). Must
                be > 0. Higher = more exploration. ``1.0`` is the
                canonical Li et al. choice.
            lambda_reg: ridge regularization. Must be > 0. ``A``
                starts at ``lambda_reg * I`` so the inverse exists
                from the first call.

        Raises:
            ValueError: on empty / duplicate arms, ``context_dim < 1``,
                ``alpha <= 0``, or ``lambda_reg <= 0``.
        """
        if not arms:
            msg = "arms must not be empty"
            raise ValueError(msg)
        if len(set(arms)) != len(arms):
            msg = f"arms must be unique, got {arms}"
            raise ValueError(msg)
        if context_dim < 1:
            msg = f"context_dim must be >= 1, got {context_dim}"
            raise ValueError(msg)
        if alpha <= _ZERO:
            msg = f"alpha must be > 0, got {alpha}"
            raise ValueError(msg)
        if lambda_reg <= _ZERO:
            msg = f"lambda_reg must be > 0, got {lambda_reg}"
            raise ValueError(msg)

        self._arms: list[str] = list(arms)
        self._context_dim: int = context_dim
        self._alpha: Decimal = alpha
        self._lambda_reg: Decimal = lambda_reg

        # A starts at lambda * I, so A^{-1} starts at (1/lambda) * I.
        inv_scale = _ONE / lambda_reg
        self._a_inv: dict[str, list[list[Decimal]]] = {
            name: _eye(context_dim, scale=inv_scale) for name in arms
        }
        self._b: dict[str, list[Decimal]] = {name: [_ZERO] * context_dim for name in arms}
        self._n_updates: dict[str, int] = dict.fromkeys(arms, 0)

    # ─── Public API ─────────────────────────────────────────────────────────

    def select(self, context: list[Decimal]) -> str:
        """Pick the arm with the highest UCB score given ``context``.

        Args:
            context: feature vector of length ``context_dim``.

        Returns:
            Name of the chosen arm. Ties broken alphabetically.

        Raises:
            ValueError: on context dimension mismatch.
        """
        self._validate_context(context)

        best_name = self._arms[0]
        best_score = self._score(best_name, context)
        for name in self._arms[1:]:
            score = self._score(name, context)
            # Strict > preserves the alphabetical tie-break since the
            # arms list is iterated in insertion order ; we made it
            # alphabetical-first by sorting at the call site if the
            # caller wants that. By default we keep insertion order
            # but break ties on alphabetical comparison.
            if score > best_score or (score == best_score and name < best_name):
                best_name = name
                best_score = score
        return best_name

    def update(
        self,
        *,
        arm: str,
        context: list[Decimal],
        reward: Decimal,
    ) -> None:
        """Apply one observed reward to ``arm``.

        Args:
            arm: name of the arm that was actually played.
            context: feature vector that was passed to :meth:`select`.
            reward: realized reward (e.g. realized R-multiple).

        Raises:
            ValueError: on unknown ``arm`` or context dimension
                mismatch.
        """
        if arm not in self._a_inv:
            msg = f"unknown arm {arm!r} ; known: {self._arms}"
            raise ValueError(msg)
        self._validate_context(context)

        # Sherman-Morrison rank-1 update of A^{-1}.
        self._a_inv[arm] = _sherman_morrison_update(self._a_inv[arm], context)
        # b += reward * x.
        self._b[arm] = [bi + reward * xi for bi, xi in zip(self._b[arm], context, strict=True)]
        self._n_updates[arm] += 1

    def state(self) -> dict[str, LinUCBArmState]:
        """Snapshot of all arms' learned parameters.

        Returns:
            ``{arm_name -> LinUCBArmState}`` with the current
            ``theta`` (computed on demand) and ``n_updates``.
        """
        snapshot: dict[str, LinUCBArmState] = {}
        for name in self._arms:
            theta = _matvec(self._a_inv[name], self._b[name])
            snapshot[name] = LinUCBArmState(
                name=name,
                theta=theta,
                n_updates=self._n_updates[name],
            )
        return snapshot

    # ─── Internals ──────────────────────────────────────────────────────────

    def _score(self, arm: str, context: list[Decimal]) -> Decimal:
        """UCB score : ``theta . x + alpha * sqrt(x . (A_inv @ x))``."""
        a_inv = self._a_inv[arm]
        b = self._b[arm]
        # theta = A_inv * b ; mean = theta . x.
        # We compute (A_inv @ x) once and reuse for both mean and bonus :
        #   mean  = (A_inv @ b) . x
        #   bonus = alpha * sqrt(x . (A_inv @ x))
        # The bonus term is the cleaner one with the cached A_inv_x.
        a_inv_x = _matvec(a_inv, context)
        # mean = b . A_inv_x  (using A_inv symmetric -> theta.x = b.(A_inv @ x))
        mean = _dot(b, a_inv_x)
        bonus_inner = _dot(context, a_inv_x)
        # bonus_inner is x^T A_inv x ; non-negative because A_inv is
        # positive definite (inherited from A = lambda I + sum xx^T).
        bonus = self._alpha * getcontext().sqrt(bonus_inner)
        return mean + bonus

    def _validate_context(self, context: list[Decimal]) -> None:
        """Check the context vector has the right dimension."""
        if len(context) != self._context_dim:
            msg = f"context must have dimension {self._context_dim}, got {len(context)}"
            raise ValueError(msg)
