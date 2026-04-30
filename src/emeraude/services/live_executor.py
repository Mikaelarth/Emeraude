"""LiveExecutor — couche d'abstraction entre l'AutoTrader et Binance.

L'iter #95 a livré la route ``POST /api/run-cycle`` mais l'audit franc
qui a suivi a confirmé un trou critique : :func:`AutoTrader._maybe_open`
appelait directement :meth:`PositionTracker.open_position`, qui n'écrit
qu'en DB locale. Conséquence : le toggle "mode Réel" était cosmétique,
aucun ordre Binance ne partait jamais — ni en paper ni en réel.

Cette iter introduit un **Protocol** :class:`LiveExecutor` que
:class:`AutoTrader` consulte avant chaque ouverture de position.
Deux implémentations :

* :class:`PaperLiveExecutor` — comportement historique : retourne
  immédiatement avec ``fill_price = intended_price`` (le prix calculé
  par l'orchestrator). Aucun appel réseau. C'est le default ; tant que
  l'utilisateur n'a pas configuré de credentials Binance, ce chemin
  reste actif et le runtime est strictement identique à pré-iter #96.

* :class:`BinanceLiveExecutor` — appelle
  :meth:`BinanceClient.place_market_order` quand le mode courant est
  ``"real"`` ET que des credentials sont configurés. Sur succès,
  extrait le prix moyen pondéré depuis le tableau ``fills`` de la
  réponse Binance et le rend disponible au tracker pour qu'il
  enregistre la position au prix de fill réel (pas au prix théorique
  de l'orchestrator). Sur ``OSError`` / ``HTTPError`` réseau, l'erreur
  est laissée remonter — le serveur HTTP la mappe en 502 (anti-règle
  A8 : pas de ``except: pass`` silencieux).

  En mode Paper ou sans credentials, fait fallback automatique sur le
  comportement Paper avec un audit ``LIVE_ORDER_FALLBACK_PAPER`` —
  rendre le fallback explicite est crucial pour qu'un opérateur qui
  **croit** trader en réel s'en rende compte (anti-règle A1 : pas de
  fonctionnalité fictive).

Décisions architecturales :

* **Pas de :class:`BinanceClient` cache** : la classe est stateless ;
  on l'instancie à chaque appel (coût négligeable) plutôt que de
  garder un client en mémoire qui invaliderait son état si l'utilisateur
  faisait une rotation de clé.

* **``intended_price`` toujours retourné en cas de fallback** : on ne
  fabrique JAMAIS un faux ``order_id`` qui ressemble à un vrai
  Binance order ID — le format ``"paper-{ts}"`` est délibérément
  identifiable pour éviter toute confusion en post-mortem.

* **Audit obligatoire des trois chemins** : ``LIVE_ORDER_PLACED``
  (succès Binance), ``LIVE_ORDER_REJECTED`` (échec Binance, levée),
  ``LIVE_ORDER_FALLBACK_PAPER`` (mode/credentials manquants). Cela
  garantit qu'aucune position n'est ouverte sans audit trail (R9).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Protocol

from emeraude.infra import audit, crypto
from emeraude.infra.exchange import BinanceClient, OrderSide
from emeraude.services.binance_credentials import (
    ENV_PASSPHRASE,
    SETTING_KEY_API_KEY,
    SETTING_KEY_API_SECRET,
)
from emeraude.services.dashboard_types import MODE_REAL

if TYPE_CHECKING:
    from collections.abc import Callable


_AUDIT_PLACED: Final[str] = "LIVE_ORDER_PLACED"
_AUDIT_REJECTED: Final[str] = "LIVE_ORDER_REJECTED"
_AUDIT_FALLBACK: Final[str] = "LIVE_ORDER_FALLBACK_PAPER"


@dataclass(frozen=True, slots=True)
class LiveOrderResult:
    """Résultat compact d'une tentative d'ouverture de position.

    Quel que soit le chemin emprunté (paper ou Binance), l'AutoTrader
    consomme cette structure et l'utilise pour enregistrer la position
    en DB. ``fill_price`` est l'unique champ load-bearing pour le
    PnL — le ``order_id`` et ``status`` sont là pour l'audit trail.

    Attributes:
        fill_price: prix moyen pondéré du fill (Binance) ou prix théorique
            (paper). C'est ce prix qui sert d'``entry_price`` dans le
            tracker.
        order_id: identifiant unique de l'ordre. Format ``"paper-{ts}"``
            pour le chemin paper, sinon l'``orderId`` de la réponse
            Binance (entier converti en str).
        status: status reporté. ``"PAPER_FILLED"`` pour le paper,
            sinon le ``status`` Binance (``"FILLED"``, ``"PARTIALLY_FILLED"``,
            ``"NEW"``, etc.).
        executed_qty: quantité réellement exécutée (string pour préserver
            la précision décimale Binance). Égale à la quantité
            demandée pour le paper ; peut différer pour Binance en cas
            de fill partiel ou de rounding lot-size.
        is_paper: ``True`` quand le chemin paper a été emprunté (mode
            Paper, ou mode Réel + credentials manquants + fallback).
            Permet à l'AutoTrader de remonter ce flag dans le
            CycleReport pour audit / UI.
    """

    fill_price: Decimal
    order_id: str
    status: str
    executed_qty: Decimal
    is_paper: bool


class LiveExecutor(Protocol):
    """Contrat structurel consommé par :class:`AutoTrader`.

    Le Protocol existe pour découpler ``AutoTrader`` de
    :class:`BinanceClient` : les tests injectent un fake stateless,
    la prod injecte :class:`BinanceLiveExecutor`.
    """

    def open_market_position(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        intended_price: Decimal,
    ) -> LiveOrderResult:
        """Ouvre une position au marché, retourne le résultat.

        Args:
            symbol: paire Binance, par exemple ``"BTCUSDT"``.
            side: ``"BUY"`` (LONG) ou ``"SELL"`` (SHORT). Format
                Binance directement (l'AutoTrader convertit le
                :class:`Side` interne).
            quantity: quantité base-asset (Decimal).
            intended_price: prix calculé par l'orchestrator. Sert de
                fallback en cas de chemin paper, et de référence
                d'audit pour mesurer le slippage en chemin Binance.

        Returns:
            :class:`LiveOrderResult`.

        Raises:
            OSError: erreur réseau Binance (laissée remonter).
            urllib.error.HTTPError: erreur API Binance (laissée remonter).
        """
        ...  # pragma: no cover  (Protocol)


class PaperLiveExecutor:
    """Implémentation par défaut : aucun appel réseau, fill immédiat.

    Conserve strictement le comportement pré-iter-#96 — utile pour
    pytest, pour les démarrages cold sans credentials, et pour le
    mode Paper en runtime production.
    """

    def open_market_position(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        intended_price: Decimal,
    ) -> LiveOrderResult:
        """Retourne immédiatement avec ``fill_price = intended_price``."""
        order_id = f"paper-{int(time.time() * 1000)}"
        audit.audit(
            _AUDIT_FALLBACK,
            {
                "reason": "paper_executor",
                "symbol": symbol,
                "side": side,
                "quantity": str(quantity),
                "intended_price": str(intended_price),
                "order_id": order_id,
            },
        )
        return LiveOrderResult(
            fill_price=intended_price,
            order_id=order_id,
            status="PAPER_FILLED",
            executed_qty=quantity,
            is_paper=True,
        )


class BinanceLiveExecutor:
    """Place des ordres MARKET réels sur Binance quand le mode == real.

    En mode Paper ou sans credentials, fallback automatique sur le
    comportement Paper avec audit ``LIVE_ORDER_FALLBACK_PAPER``. Cela
    garantit que :

    1. Le toggle "mode Réel" UI sans credentials configurés ne
       produit pas d'erreur fatale ; il fait juste un paper trade
       avec un audit explicite.
    2. Inversement, un mode Paper avec credentials configurés ne
       déclenche **aucun** appel Binance.

    Args:
        mode_provider: callable lue à chaque appel ; retourne le mode
            courant (``"paper"`` ou ``"real"``). Lue à chaque appel
            pour refléter un toggle UI sans redémarrage.
        passphrase_provider: callable retournant le passphrase
            (``None`` si non disponible). Default lit
            :data:`ENV_PASSPHRASE`. Tests injectent une closure.
        client_factory: factory ``(api_key, api_secret) -> BinanceClient``.
            Default = constructeur standard. Tests injectent un fake.
            Permet aussi de pointer vers le testnet.
        paper_fallback: instance :class:`PaperLiveExecutor` utilisée
            comme fallback. Default = nouvelle instance ; injectable
            pour les tests qui veulent espionner le fallback.
    """

    def __init__(
        self,
        *,
        mode_provider: Callable[[], str],
        passphrase_provider: Callable[[], str | None] | None = None,
        client_factory: Callable[[str, str], BinanceClient] | None = None,
        paper_fallback: PaperLiveExecutor | None = None,
    ) -> None:
        self._mode_provider = mode_provider
        self._passphrase_provider: Callable[[], str | None] = (
            passphrase_provider
            if passphrase_provider is not None
            else (lambda: os.environ.get(ENV_PASSPHRASE))
        )
        self._client_factory: Callable[[str, str], BinanceClient] = (
            client_factory if client_factory is not None else BinanceClient
        )
        self._paper_fallback: PaperLiveExecutor = (
            paper_fallback if paper_fallback is not None else PaperLiveExecutor()
        )

    def open_market_position(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        intended_price: Decimal,
    ) -> LiveOrderResult:
        """Place un ordre MARKET réel ou tombe sur le paper fallback."""
        mode = self._mode_provider()
        if mode != MODE_REAL:
            return self._paper_fallback.open_market_position(
                symbol=symbol,
                side=side,
                quantity=quantity,
                intended_price=intended_price,
            )

        passphrase = self._passphrase_provider()
        if not passphrase:
            audit.audit(
                _AUDIT_FALLBACK,
                {
                    "reason": "passphrase_missing",
                    "symbol": symbol,
                    "side": side,
                    "quantity": str(quantity),
                    "intended_price": str(intended_price),
                },
            )
            return self._paper_fallback.open_market_position(
                symbol=symbol,
                side=side,
                quantity=quantity,
                intended_price=intended_price,
            )

        api_key = crypto.get_secret_setting(SETTING_KEY_API_KEY, passphrase)
        api_secret = crypto.get_secret_setting(SETTING_KEY_API_SECRET, passphrase)
        if not api_key or not api_secret:
            audit.audit(
                _AUDIT_FALLBACK,
                {
                    "reason": "credentials_missing",
                    "symbol": symbol,
                    "side": side,
                    "quantity": str(quantity),
                    "intended_price": str(intended_price),
                },
            )
            return self._paper_fallback.open_market_position(
                symbol=symbol,
                side=side,
                quantity=quantity,
                intended_price=intended_price,
            )

        client = self._client_factory(api_key, api_secret)
        try:
            response = client.place_market_order(
                symbol=symbol,
                side=_to_order_side(side),
                quantity=quantity,
            )
        except Exception as exc:
            audit.audit(
                _AUDIT_REJECTED,
                {
                    "symbol": symbol,
                    "side": side,
                    "quantity": str(quantity),
                    "intended_price": str(intended_price),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        fill_price = _extract_fill_price(response, intended_price)
        executed_qty = _extract_executed_qty(response, quantity)
        order_id = str(response.get("orderId", ""))
        status = str(response.get("status", "UNKNOWN"))

        audit.audit(
            _AUDIT_PLACED,
            {
                "symbol": symbol,
                "side": side,
                "quantity": str(quantity),
                "intended_price": str(intended_price),
                "fill_price": str(fill_price),
                "executed_qty": str(executed_qty),
                "order_id": order_id,
                "status": status,
                "slippage_bps": str(_slippage_bps(intended_price, fill_price, side)),
            },
        )
        return LiveOrderResult(
            fill_price=fill_price,
            order_id=order_id,
            status=status,
            executed_qty=executed_qty,
            is_paper=False,
        )


# ─── Helpers ────────────────────────────────────────────────────────────────


def _to_order_side(side: str) -> OrderSide:
    """Convertit le ``side`` AutoTrader en format strict Binance.

    AutoTrader passe déjà ``"BUY"``/``"SELL"`` ; cette fonction est une
    sécurité défensive qui rejette tout autre format pour éviter
    qu'un :class:`Side` enum mal converti (par exemple ``"LONG"``)
    n'arrive à Binance et soit silencieusement rejeté côté API.
    """
    upper = side.upper()
    if upper == "BUY":
        return "BUY"
    if upper == "SELL":
        return "SELL"
    msg = f"side must be 'BUY' or 'SELL', received {side!r}"
    raise ValueError(msg)


def _extract_fill_price(response: dict[str, Any], intended_price: Decimal) -> Decimal:
    """Calcule le prix moyen pondéré depuis ``response['fills']``.

    Réponse Binance MARKET typique :

    .. code-block:: json

        {
            "orderId": 28,
            "status": "FILLED",
            "fills": [
                {"price": "30000.00", "qty": "0.001"},
                {"price": "30001.50", "qty": "0.0005"}
            ]
        }

    Si ``fills`` est absent ou vide (cas testnet ou retour partiel),
    fallback sur ``intended_price`` plutôt que de retourner 0 ou de
    raise — l'audit trail enregistre déjà le response complet en
    amont, donc l'opérateur peut investiguer post-mortem.
    """
    fills = response.get("fills") or []
    if not fills:
        return intended_price
    total_qty = Decimal("0")
    total_quote = Decimal("0")
    for fill in fills:
        try:
            qty = Decimal(str(fill.get("qty", "0")))
            price = Decimal(str(fill.get("price", "0")))
        except (ValueError, TypeError, ArithmeticError):
            continue
        total_qty += qty
        total_quote += qty * price
    if total_qty <= 0:
        return intended_price
    return total_quote / total_qty


def _extract_executed_qty(response: dict[str, Any], requested_qty: Decimal) -> Decimal:
    """Lit ``executedQty`` de la réponse, fallback sur ``requested_qty``."""
    raw = response.get("executedQty")
    if raw is None:
        return requested_qty
    try:
        return Decimal(str(raw))
    except (ValueError, TypeError, ArithmeticError):
        return requested_qty


def _slippage_bps(intended: Decimal, fill: Decimal, side: str) -> Decimal:
    """Slippage en basis points (1 bp = 0.01 %).

    Convention : positif = défavorable. Pour un BUY, payer plus que
    prévu est défavorable ; pour un SELL, recevoir moins que prévu
    est défavorable. Affiché tel quel dans l'audit pour qu'une
    surveillance future puisse trier les exécutions par qualité.
    """
    if intended <= 0:
        return Decimal("0")
    diff = fill - intended
    if side.upper() == "SELL":
        diff = -diff
    return (diff / intended) * Decimal("10000")
