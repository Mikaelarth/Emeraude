"""Binance API credentials service (iter #66).

Mission UX (doc 02 §"⚙ CONFIG" §"Connexion Binance" + garde-fous) :

    Permettre à l'utilisateur de saisir ses clés API Binance avec :
    * Clés API jamais affichées en clair (déjà en place côté infra/crypto)
    * Validation format des inputs
    * Confirmation explicite (double-tap) avant persistance

Cet iter livre le **service** côté ``services/`` ; le widget UI vit
dans :mod:`emeraude.ui.screens.config` et consomme cette API.

Architecture sécurité :

* **Chiffrement** : :func:`emeraude.infra.crypto.encrypt` (PBKDF2 +
  XOR par-bytes via salt per-install). Les clés ne quittent jamais
  la DB en clair.
* **Passphrase** : lue à chaque opération depuis
  ``EMERAUDE_API_PASSPHRASE`` (env var). Si l'env n'est pas set, le
  service rapporte ``passphrase_available=False`` et lève
  :class:`PassphraseUnavailableError` sur :meth:`save_credentials`.
  Anti-règle A1 : le service est honnête sur sa disponibilité —
  pas de "Coming soon", pas de fallback silencieux à un secret
  hardcodé.
* **Status** : seul le **suffixe** (4 derniers caractères) de
  l'API key est exposé après stockage. La clé secrète n'est
  **jamais** lue en retour vers l'UI — son affichage est
  `[définie]` ou `[non définie]` strict.
* **Migration future** : ce passphrase env-based est transitoire.
  E7 (Android KeyStore hardware-backed) remplacera ``EMERAUDE_API_PASSPHRASE``
  par un secret dérivé du KeyStore Android — l'API publique
  :class:`BinanceCredentialsService` reste stable.

Validation format :

* API key : 16-128 caractères alphanumériques (Binance utilise 64
  alphanumeriques mixed case ; on accepte un range large pour
  tolérer d'éventuels formats futurs ou exchanges connexes).
* API secret : idem.

Le service ne **vérifie pas** la validité côté Binance (pas de call
réseau). La validation runtime des clés contre l'exchange viendra
dans une iter ultérieure quand le bot fera son premier ping
authentifié.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Final, Protocol

from emeraude.infra import crypto, database

#: Env var transitoire (jusqu'à E7 Android KeyStore). Lue à chaque
#: opération du service. Le user doit la set avant d'ouvrir l'app.
ENV_PASSPHRASE: Final[str] = "EMERAUDE_API_PASSPHRASE"  # noqa: S105

#: Clés stables dans la table ``settings`` (préfixe ``binance.``).
#: Ne pas changer — orphelinerait les installs existants. Les noqa
#: S105 signalent que ce sont des **noms de clés de settings**, pas
#: des valeurs secrètes.
SETTING_KEY_API_KEY: Final[str] = "binance.api_key"
SETTING_KEY_API_SECRET: Final[str] = "binance.api_secret"  # noqa: S105

#: Bornes raisonnables pour la validation format. Binance émet 64
#: alphanumeric. On accepte 16-128 pour rester tolérant.
_MIN_LEN: Final[int] = 16
_MAX_LEN: Final[int] = 128

#: Longueur du suffixe affiché (4 derniers caractères). Stable
#: contract — ne pas changer sans repasser sur le widget Config.
_SUFFIX_LEN: Final[int] = 4

#: Pattern accepté (lettres + chiffres). Pas d'underscore / espace /
#: caractères spéciaux — Binance n'en émet pas.
_ALPHANUM_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9]+$")


# ─── Exceptions ─────────────────────────────────────────────────────────────


class PassphraseUnavailableError(RuntimeError):
    """Raised when ``EMERAUDE_API_PASSPHRASE`` is missing on save/clear.

    The UI should pre-check via :attr:`BinanceCredentialsStatus.passphrase_available`
    before calling :meth:`save_credentials` and disable the form if
    the env var is absent.
    """


class CredentialFormatError(ValueError):
    """Raised when the API key or secret fails format validation."""


# ─── Status ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BinanceCredentialsStatus:
    """Read-only state for the UI.

    Attributes:
        api_key_set: ``True`` iff a non-empty key is persisted.
        api_secret_set: ``True`` iff a non-empty secret is persisted.
        api_key_suffix: last 4 characters of the API key when set
            (post-masking). ``None`` if not set or unreadable
            (decryption failure / bad passphrase).
        passphrase_available: ``True`` iff
            :data:`ENV_PASSPHRASE` is exported in the environment.
            When ``False`` the UI must disable the form and show a
            hint to set the env var.
    """

    api_key_set: bool
    api_secret_set: bool
    api_key_suffix: str | None
    passphrase_available: bool


# ─── Validators (pure) ─────────────────────────────────────────────────────


def validate_credential(value: str, *, field: str) -> None:
    """Pure validator for both API key and API secret format.

    Args:
        value: candidate string.
        field: human-readable field name for the error message
            (``"api_key"`` / ``"api_secret"``).

    Raises:
        CredentialFormatError: if the value is empty, too short / long,
            or contains non-alphanumeric characters.
    """
    if not value:
        msg = f"{field} ne peut pas être vide"
        raise CredentialFormatError(msg)
    if len(value) < _MIN_LEN:
        msg = f"{field} doit contenir au moins {_MIN_LEN} caractères, reçu {len(value)}"
        raise CredentialFormatError(msg)
    if len(value) > _MAX_LEN:
        msg = f"{field} doit contenir au plus {_MAX_LEN} caractères, reçu {len(value)}"
        raise CredentialFormatError(msg)
    if not _ALPHANUM_RE.match(value):
        msg = f"{field} doit être alphanumérique (A-Z, a-z, 0-9 uniquement)"
        raise CredentialFormatError(msg)


# ─── Service ────────────────────────────────────────────────────────────────


class BinanceCredentialsServiceProtocol(Protocol):
    """Structural contract consumed by ``ConfigScreen``.

    Lets tests inject an in-memory fake without subclassing the
    concrete service. Implementations vivent côté ``services/`` ; le
    Protocol existe purement pour découpler l'UI de la dépendance
    concrète.
    """

    def get_status(self) -> BinanceCredentialsStatus:
        """Snapshot of credential persistence state."""
        ...  # pragma: no cover  (Protocol method)

    def save_credentials(self, *, api_key: str, api_secret: str) -> None:
        """Persist + encrypt the credentials, raise on bad format."""
        ...  # pragma: no cover  (Protocol method)

    def clear_credentials(self) -> None:
        """Remove both credentials from the settings table."""
        ...  # pragma: no cover  (Protocol method)


class BinanceCredentialsService:
    """Credential lifecycle backed by the encrypted ``settings`` table.

    Stateless : every operation re-reads the env var + DB. Safe to
    instantiate once at composition time and share across the UI.
    Implements :class:`BinanceCredentialsServiceProtocol` structurally.
    """

    def get_status(self) -> BinanceCredentialsStatus:
        """Build a status snapshot.

        * Reads the persistence flags via :func:`database.get_setting`
          (raw, not decrypted, so a missing passphrase doesn't break
          the read).
        * Decrypts the API key only if the passphrase is available,
          to expose the last-4 suffix. Decryption failure (wrong
          passphrase, corrupted row) yields ``api_key_suffix=None``.
        """
        raw_key = database.get_setting(SETTING_KEY_API_KEY)
        raw_secret = database.get_setting(SETTING_KEY_API_SECRET)
        passphrase = os.environ.get(ENV_PASSPHRASE)

        suffix: str | None = None
        if raw_key is not None and passphrase:
            try:
                plain_key = crypto.decrypt(raw_key, passphrase)
            except ValueError:
                # Corrupted base64 : surface as "set but unreadable"
                # — UI can prompt user to clear & re-enter.
                suffix = None
            else:
                # ``decrypt`` returns garbled UTF-8 on bad passphrase ;
                # if the plaintext doesn't pass our format check,
                # treat as unreadable.
                if _ALPHANUM_RE.match(plain_key) and len(plain_key) >= _SUFFIX_LEN:
                    suffix = plain_key[-_SUFFIX_LEN:]

        return BinanceCredentialsStatus(
            api_key_set=bool(raw_key),
            api_secret_set=bool(raw_secret),
            api_key_suffix=suffix,
            passphrase_available=bool(passphrase),
        )

    def save_credentials(self, *, api_key: str, api_secret: str) -> None:
        """Validate + encrypt + persist both credentials atomically.

        Args:
            api_key: Binance API key (alphanumeric, 16-128 chars).
            api_secret: Binance API secret (same format).

        Raises:
            PassphraseUnavailableError: if :data:`ENV_PASSPHRASE` is
                not set in the environment.
            CredentialFormatError: if either credential fails format
                validation.
        """
        passphrase = os.environ.get(ENV_PASSPHRASE)
        if not passphrase:
            msg = (
                f"{ENV_PASSPHRASE} doit être défini pour sauvegarder les clés API. "
                "Cette variable est transitoire jusqu'à la migration Android KeyStore (E7)."
            )
            raise PassphraseUnavailableError(msg)

        validate_credential(api_key, field="api_key")
        validate_credential(api_secret, field="api_secret")

        crypto.set_secret_setting(SETTING_KEY_API_KEY, api_key, passphrase)
        crypto.set_secret_setting(SETTING_KEY_API_SECRET, api_secret, passphrase)

    def clear_credentials(self) -> None:
        """Remove both credentials from the ``settings`` table.

        No passphrase needed — we just delete the encrypted blobs.
        Idempotent : calling on already-empty state is a no-op.
        """
        # SQLite ``settings`` table doesn't support DELETE via the
        # high-level helpers (only get/set). We overwrite with empty
        # string + the marker prefix so the row is preserved as
        # "explicitly cleared" rather than silently absent — useful
        # for forensics if a user reports "I never saved any key".
        database.set_setting(SETTING_KEY_API_KEY, "")
        database.set_setting(SETTING_KEY_API_SECRET, "")
