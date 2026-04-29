"""Concrete :class:`ConfigDataSource` backed by SQLite settings + audit.

Bridge entre la table ``settings`` (mode utilisateur), la table
``audit_log`` (compteur d'événements pour le panneau status) et le
package ``emeraude`` (version + paths). Read + write minimal — la
saisie des autres sections doc 02 (clés API, capital, telegram, etc.)
arrive en iters suivants.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emeraude import __version__ as _app_version
from emeraude.infra import database, paths
from emeraude.services.config_types import (
    SETTING_KEY_MODE,
    ConfigSnapshot,
    is_valid_mode,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal


class SettingsConfigDataSource:
    """Read/write :class:`ConfigDataSource` backed by SQLite.

    Implements the
    :class:`emeraude.services.config_types.ConfigDataSource` Protocol
    structurally (no inheritance — Protocols are duck-typed).

    Args:
        starting_capital_provider: callable retournant le baseline
            paper-mode (typiquement :meth:`WalletService.starting_capital`
            wrapped en lambda). ``None`` si non défini (UI affiche
            ``—``). Pas de valeur par défaut hardcodée — anti-règle
            A11.
        default_mode: mode renvoyé par :meth:`fetch_snapshot` si la
            table ``settings`` n'a pas encore de valeur pour
            :data:`SETTING_KEY_MODE`. Cohérent avec
            :class:`EmeraudeApp` qui passe son propre default au
            startup.
    """

    def __init__(
        self,
        *,
        starting_capital_provider: Callable[[], Decimal | None],
        default_mode: str,
    ) -> None:
        if not is_valid_mode(default_mode):
            msg = f"default_mode invalide : {default_mode!r}"
            raise ValueError(msg)
        self._starting_capital_provider = starting_capital_provider
        self._default_mode = default_mode

    def fetch_snapshot(self) -> ConfigSnapshot:
        """Build a fresh snapshot.

        * ``mode`` : valeur persistée dans ``settings`` ou
          :attr:`_default_mode`.
        * ``starting_capital`` : déléguée au provider injecté.
        * ``app_version`` : :data:`emeraude.__version__`.
        * ``total_audit_events`` : ``COUNT(*)`` sur ``audit_log``.
        * ``db_path`` : ``str(infra.paths.db_path())``.
        """
        persisted_mode = database.get_setting(SETTING_KEY_MODE)
        mode = persisted_mode if persisted_mode is not None else self._default_mode

        return ConfigSnapshot(
            mode=mode,
            starting_capital=self._starting_capital_provider(),
            app_version=_app_version,
            total_audit_events=_count_audit_events(),
            db_path=str(paths.database_path()),
        )

    def set_mode(self, mode: str) -> None:
        """Persiste le mode dans la table ``settings``.

        Raises:
            ValueError: si ``mode`` n'est pas un mode reconnu.
        """
        if not is_valid_mode(mode):
            msg = f"mode invalide : {mode!r}"
            raise ValueError(msg)
        database.set_setting(SETTING_KEY_MODE, mode)


def _count_audit_events() -> int:
    """``SELECT COUNT(*) FROM audit_log``. Cheap on the WAL DB."""
    row = database.query_one("SELECT COUNT(*) AS n FROM audit_log")
    if row is None:  # pragma: no cover  (impossible : COUNT always returns one row)
        return 0
    return int(row["n"])
