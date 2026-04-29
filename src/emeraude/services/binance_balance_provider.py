"""Live Binance balance provider for mode REAL (iter #67).

Bridge entre les credentials chiffrées (iter #66) et l'affichage capital
réel sur le Dashboard. Le :class:`WalletService` en mode
:data:`MODE_REAL` délègue à ce provider via un ``Callable[[], Decimal |
None]`` injecté.

Architecture cache :

* **TTL** : valeur par défaut 60 s. Premier appel après la TTL expirée
  déclenche un appel HTTP signed à Binance ; les appels suivants dans
  la fenêtre TTL retournent la valeur cachée. Empêche le cycle pump
  (5 s) de saturer Binance + l'UI.
* **Synchrone** : l'appel HTTP bloque le thread courant. Sur
  smartphone, ~500 ms-2 s. Acceptable pour un toggle Config →
  prochain refresh tick. iter futur extrait un poll asynchrone.
* **Defense in depth** : decrypt + validation format + HTTP retry
  (via le retry decorator côté ``BinanceClient``). Tout chemin
  échec retourne ``None`` et émet un audit event explicite.

Sécurité :

* Les clés API sont lues à chaque appel (pas de cache plaintext).
* Décryptage juste avant l'appel HTTP, ``client`` discardé après.
  Minimise la fenêtre où plaintext est en mémoire.
* Si decryption échoue (bad passphrase) ou format check échoue
  (DB row corrompue), le provider retourne ``None`` et audit-log
  la raison. Anti-règle A8 : pas de silence.

Tests :

* Le constructeur accepte un ``client_factory`` injectable
  (``Callable[[str, str], BinanceClientLike]``). Tests passent un
  fake qui simule succès / échec sans réseau réel.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final, Protocol
from urllib.error import HTTPError, URLError

from emeraude.infra import audit, crypto
from emeraude.infra.exchange import BinanceClient
from emeraude.services.binance_credentials import (
    SETTING_KEY_API_KEY,
    SETTING_KEY_API_SECRET,
    CredentialFormatError,
    validate_credential,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal


#: Default TTL pour le cache balance (secondes). 60 s = ~12 cycles
#: pump (5 s) entre 2 calls HTTP à Binance. Compromis lisibilité /
#: charge réseau.
DEFAULT_CACHE_TTL_SECONDS: Final[float] = 60.0

#: Audit event emis à chaque fetch réussi (debugging + traçabilité).
AUDIT_BALANCE_FETCHED: Final[str] = "WALLET_REAL_BALANCE_FETCHED"

#: Audit event emis sur échec (reason champ explicite).
AUDIT_BALANCE_FAILED: Final[str] = "WALLET_REAL_BALANCE_FAILED"

# Reasons stables pour audit filtering.
REASON_NO_PASSPHRASE: Final[str] = "no_passphrase"  # noqa: S105
REASON_NO_CREDENTIALS: Final[str] = "no_credentials"
REASON_DECRYPT_FAILED: Final[str] = "decrypt_failed"
REASON_HTTP_ERROR: Final[str] = "http_error"
REASON_INVALID_RESPONSE: Final[str] = "invalid_response"


# ─── Client Protocol ───────────────────────────────────────────────────────


class BinanceClientLike(Protocol):
    """Structural contract pour permettre l'injection de fakes en test.

    :class:`emeraude.infra.exchange.BinanceClient` matche
    structurellement (duck-typed) ; tests passent un fake qui simule
    succès / échec sans appeler le réseau.
    """

    def get_account_balance(self, asset: str = "USDT") -> Decimal:
        """Retourne la free balance pour ``asset``."""
        ...  # pragma: no cover  (Protocol method)


# ─── Provider ──────────────────────────────────────────────────────────────


class BinanceBalanceProvider:
    """Live Binance USDT balance with TTL cache.

    Args:
        passphrase_provider: callable retournant le passphrase (env
            var ``EMERAUDE_API_PASSPHRASE``) ou ``None``. Lu à chaque
            appel — un changement runtime de l'env var est honoré
            sans rebuild.
        cache_ttl_seconds: durée de vie du cache. Default
            :data:`DEFAULT_CACHE_TTL_SECONDS`.
        client_factory: callable construisant un ``BinanceClientLike``
            depuis ``(api_key, api_secret)``. Default
            :class:`emeraude.infra.exchange.BinanceClient`.

    Raises:
        ValueError: on ``cache_ttl_seconds <= 0``.
    """

    def __init__(
        self,
        *,
        passphrase_provider: Callable[[], str | None],
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        client_factory: Callable[[str, str], BinanceClientLike] | None = None,
    ) -> None:
        if cache_ttl_seconds <= 0:
            msg = f"cache_ttl_seconds must be > 0, got {cache_ttl_seconds}"
            raise ValueError(msg)
        self._passphrase_provider = passphrase_provider
        self._cache_ttl = cache_ttl_seconds
        self._client_factory: Callable[[str, str], BinanceClientLike] = (
            client_factory if client_factory is not None else BinanceClient
        )
        self._cached_balance: Decimal | None = None
        self._cached_at: float = 0.0

    def current_balance_usdt(self) -> Decimal | None:
        """Free USDT balance — cached avec TTL.

        Returns:
            * Cache fresh : valeur cached.
            * Cache stale ou empty : appel HTTP signed, valeur
              cachée + retournée. ``None`` si tout chemin d'échec
              (passphrase manquant, credentials non saisies,
              décryptage corrupt, HTTP error).
        """
        now = time.monotonic()
        if self._cached_balance is not None and (now - self._cached_at) < self._cache_ttl:
            return self._cached_balance

        balance = self._fetch_live_balance()
        if balance is not None:
            self._cached_balance = balance
            self._cached_at = now
        return balance

    def invalidate_cache(self) -> None:
        """Force le prochain appel à hit HTTP plutôt que le cache.

        Utile pour les tests ou après un toggle Config qui invalide
        l'état précédent.
        """
        self._cached_balance = None
        self._cached_at = 0.0

    def _fetch_live_balance(self) -> Decimal | None:
        """Decrypt + HTTP. Retourne ``None`` + audit sur tout chemin échec."""
        passphrase = self._passphrase_provider()
        if not passphrase:
            audit.audit(
                AUDIT_BALANCE_FAILED,
                {"reason": REASON_NO_PASSPHRASE},
            )
            return None

        # Decrypt — `get_secret_setting` retourne ``None`` si la clé
        # n'existe pas dans ``settings``, ou la valeur (potentiellement
        # garbled si bad passphrase) sinon.
        api_key = crypto.get_secret_setting(SETTING_KEY_API_KEY, passphrase)
        api_secret = crypto.get_secret_setting(SETTING_KEY_API_SECRET, passphrase)
        if not api_key or not api_secret:
            audit.audit(
                AUDIT_BALANCE_FAILED,
                {"reason": REASON_NO_CREDENTIALS},
            )
            return None

        # Defense in depth : valider le format des clés décryptées.
        # Wrong passphrase produit du UTF-8 garbled qui échoue ici.
        try:
            validate_credential(api_key, field="api_key")
            validate_credential(api_secret, field="api_secret")
        except CredentialFormatError as exc:
            audit.audit(
                AUDIT_BALANCE_FAILED,
                {"reason": REASON_DECRYPT_FAILED, "detail": str(exc)},
            )
            return None

        try:
            client = self._client_factory(api_key, api_secret)
            balance = client.get_account_balance("USDT")
        except (URLError, HTTPError) as exc:
            audit.audit(
                AUDIT_BALANCE_FAILED,
                {"reason": REASON_HTTP_ERROR, "detail": str(exc)},
            )
            return None
        except (KeyError, ValueError, TypeError) as exc:
            # JSON shape unexpected, missing fields, type coercion
            # errors. We surface as audit + None rather than crash
            # the UI thread.
            audit.audit(
                AUDIT_BALANCE_FAILED,
                {"reason": REASON_INVALID_RESPONSE, "detail": str(exc)},
            )
            return None

        audit.audit(
            AUDIT_BALANCE_FETCHED,
            {"asset": "USDT", "balance": str(balance)},
        )
        return balance
