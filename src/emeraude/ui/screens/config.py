"""Config Screen — 3ème écran fonctionnel Pilier #1 (iter #64).

Mission UX (doc 02 §"⚙ CONFIG — Tout paramétrer en sécurité") :

    permettre toutes les configurations critiques avec zéro risque
    d'action accidentelle.

Slice 1 livrée iter #64 :

* **Affichage status système** : mode courant, starting capital,
  version applicative, compteur audit, chemin DB.
* **Toggle persistant paper ↔ real** avec confirmation **inline
  double-tap** (anti-règle A5 + doc 02 §3 "Aucune action critique
  sans confirmation explicite").

Slices à venir : saisie clés Binance (iter #65+, réutilise
``infra/crypto.py`` PBKDF2+XOR), Capital éditable, Telegram,
Emergency Stop, Backtest. Les sections doc 02 non encore livrées
n'apparaissent **pas** dans l'écran (anti-règle A1 — pas de
"Coming soon").

Effet du toggle (iter #65) : **propagation live** dans les ~5
secondes via le cycle pump (``Clock.schedule_interval`` iter #63).
Le :class:`WalletService` reçoit désormais un ``mode_provider:
Callable[[], str]`` qui re-lit la table ``settings`` à chaque
appel ; le Dashboard, le Journal et le Config screen voient le
nouveau mode au prochain refresh. Pas de redémarrage requis.

ADR-0002 §6 + §7 — pure logique dans
:mod:`emeraude.services.config_types` ; ce module ne contient que
le widget Kivy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from emeraude.services.config_types import (
    format_audit_count_label,
    format_mode_label,
    format_starting_capital_label,
)
from emeraude.services.dashboard_types import MODE_PAPER, MODE_REAL
from emeraude.ui import theme

if TYPE_CHECKING:
    from emeraude.services.config_types import ConfigDataSource


#: Nom stable de l'écran dans le ScreenManager.
CONFIG_SCREEN_NAME: Final[str] = "config"

#: Durée d'arming du bouton avant désarmement automatique. 5 s laisse
#: au user le temps de lire le message de confirmation sans être
#: oppressant. Doc 02 §"3. Aucune action critique sans confirmation
#: explicite".
_ARM_DURATION_SECONDS: Final[float] = 5.0

_REFRESH_HINT: Final[str] = "La modification est appliquee automatiquement dans quelques secondes."


class _TwoStageButton(Button):  # type: ignore[misc]  # Kivy classes are untyped (ADR-0002).
    """Inline 2-stage button for A5 critical-action confirmation.

    State machine :

    * ``IDLE`` (initial) : displays ``idle_text`` ;
    * ``ARMED`` (after first tap) : displays ``armed_text`` for up to
      :data:`_ARM_DURATION_SECONDS` ;
    * second tap while armed : invokes ``on_confirm`` then back to IDLE ;
    * timer expires while armed : back to IDLE without invoking.

    Args:
        idle_text: button label in the idle state.
        armed_text: button label in the armed state. Must include a
            visual cue (color via theme + word like "Confirmer").
        on_confirm: callable invoked on the second tap (within timer).
        **kwargs: forwarded to :class:`Button`.
    """

    def __init__(
        self,
        *,
        idle_text: str,
        armed_text: str,
        on_confirm: Callable[[], None],
        **kwargs: object,
    ) -> None:
        kwargs.setdefault("font_size", theme.FONT_SIZE_BODY)
        kwargs.setdefault("color", theme.COLOR_TEXT_PRIMARY)
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_color", theme.COLOR_SURFACE)
        super().__init__(text=idle_text, **kwargs)
        self._idle_text = idle_text
        self._armed_text = armed_text
        self._on_confirm = on_confirm
        self._is_armed = False
        self._disarm_event: object | None = None
        self.bind(on_press=self._handle_press)

    def _handle_press(self, _instance: object) -> None:
        if not self._is_armed:
            self._arm()
        else:
            self._disarm()
            self._on_confirm()

    def _arm(self) -> None:
        self._is_armed = True
        self.text = self._armed_text
        self.color = theme.COLOR_DANGER
        self.background_color = theme.COLOR_BACKGROUND
        self._disarm_event = Clock.schedule_once(self._on_timer_expired, _ARM_DURATION_SECONDS)

    def _disarm(self) -> None:
        self._is_armed = False
        self.text = self._idle_text
        self.color = theme.COLOR_TEXT_PRIMARY
        self.background_color = theme.COLOR_SURFACE
        if self._disarm_event is not None:
            Clock.unschedule(self._disarm_event)
            self._disarm_event = None

    def _on_timer_expired(self, _dt: float) -> None:
        if self._is_armed:
            self._disarm()


# Kivy-style import resolution — ``Callable`` only used in type
# annotations, kept inside the if TYPE_CHECKING block.
if TYPE_CHECKING:
    from collections.abc import Callable


def _make_status_row(label_text: str, value_text: str) -> BoxLayout:
    """One ``label : value`` horizontal row for the status panel."""
    row = BoxLayout(
        orientation="horizontal",
        size_hint_y=None,
        height=theme.FONT_SIZE_BODY * 2,
        spacing=theme.SPACING_MD,
    )
    label = Label(
        text=label_text,
        font_size=theme.FONT_SIZE_BODY,
        color=theme.COLOR_TEXT_SECONDARY,
        size_hint_x=0.4,
        halign="left",
        valign="middle",
    )
    value = Label(
        text=value_text,
        font_size=theme.FONT_SIZE_BODY,
        color=theme.COLOR_TEXT_PRIMARY,
        size_hint_x=0.6,
        halign="left",
        valign="middle",
    )
    row.add_widget(label)
    row.add_widget(value)
    return row


class ConfigScreen(Screen):  # type: ignore[misc]  # Kivy classes are untyped (ADR-0002).
    """Mobile config screen — status panel + mode toggle.

    The widget tree :

    * **Header** : titre "Configuration".
    * **Status panel** (vertical BoxLayout) : 5 rows (mode, capital,
      version, audit count, db path).
    * **Mode toggle** : 2 :class:`_TwoStageButton` (Mode Paper / Mode
      Reel). The active mode's button is disabled (grayed) so the
      user can only toggle to the other.
    * **Refresh hint** : label expliquant que la modification
      s'applique automatiquement dans les quelques secondes (cycle
      pump iter #63 + live mode_provider iter #65).

    On :meth:`refresh` (initial + cycle pump) the snapshot is pulled
    from the data source and the rows are repopulated. The toggle
    buttons are also recreated so the ``active`` styling reflects
    the persisted mode.

    Args:
        data_source: any object implementing
            :class:`~emeraude.services.config_types.ConfigDataSource`.
        **kwargs: forwarded to :class:`Screen` (typically ``name=``).
    """

    def __init__(
        self,
        *,
        data_source: ConfigDataSource,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._data_source = data_source

        self._outer = BoxLayout(
            orientation="vertical",
            padding=theme.SPACING_LG,
            spacing=theme.SPACING_MD,
        )
        self._header_label = Label(
            text="Configuration",
            font_size=theme.FONT_SIZE_HEADING,
            color=theme.COLOR_TEXT_PRIMARY,
            size_hint_y=None,
            height=theme.FONT_SIZE_HEADING * 2,
            halign="left",
            valign="middle",
        )
        self._status_panel = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=theme.SPACING_SM,
        )
        self._status_panel.bind(
            minimum_height=self._status_panel.setter("height"),
        )
        self._toggle_panel = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=theme.SPACING_SM,
        )
        self._toggle_panel.bind(
            minimum_height=self._toggle_panel.setter("height"),
        )
        self._refresh_hint = Label(
            text=_REFRESH_HINT,
            font_size=theme.FONT_SIZE_CAPTION,
            color=theme.COLOR_TEXT_SECONDARY,
            size_hint_y=None,
            height=theme.FONT_SIZE_CAPTION * 2,
            halign="left",
            valign="middle",
        )

        self._outer.add_widget(self._header_label)
        self._outer.add_widget(self._status_panel)
        self._outer.add_widget(self._toggle_panel)
        self._outer.add_widget(self._refresh_hint)
        self.add_widget(self._outer)

        self.refresh()

    def refresh(self) -> None:
        """Pull a fresh snapshot and rebuild the status + toggle panels."""
        snapshot = self._data_source.fetch_snapshot()

        self._status_panel.clear_widgets()
        self._status_panel.add_widget(_make_status_row("Mode", format_mode_label(snapshot.mode)))
        self._status_panel.add_widget(
            _make_status_row(
                "Capital de demarrage",
                format_starting_capital_label(snapshot.starting_capital),
            )
        )
        self._status_panel.add_widget(_make_status_row("Version", snapshot.app_version))
        self._status_panel.add_widget(
            _make_status_row(
                "Evenements audit",
                format_audit_count_label(snapshot.total_audit_events),
            )
        )
        self._status_panel.add_widget(_make_status_row("Stockage", snapshot.db_path))

        self._toggle_panel.clear_widgets()
        self._toggle_panel.add_widget(
            self._make_mode_button(
                target_mode=MODE_PAPER,
                idle_text="Passer en mode Paper",
                current_mode=snapshot.mode,
            )
        )
        self._toggle_panel.add_widget(
            self._make_mode_button(
                target_mode=MODE_REAL,
                idle_text="Passer en mode Reel",
                current_mode=snapshot.mode,
            )
        )

    def _make_mode_button(
        self,
        *,
        target_mode: str,
        idle_text: str,
        current_mode: str,
    ) -> Label | _TwoStageButton:
        """Build either a disabled badge (current) or a 2-stage toggle."""
        if target_mode == current_mode:
            # Active mode : show a non-clickable indicator.
            return Label(
                text=f"{idle_text}  [actif]",
                font_size=theme.FONT_SIZE_BODY,
                color=theme.COLOR_PRIMARY,
                size_hint_y=None,
                height=theme.FONT_SIZE_BODY * 2,
                halign="left",
                valign="middle",
            )

        # Capture target_mode in a default arg to avoid late-binding
        # through ``self``. Wrap in an explicit lambda whose return is
        # typed ``None`` so mypy can infer the on_confirm contract.
        def _on_confirm(mode: str = target_mode) -> None:
            self._apply_mode(mode)

        return _TwoStageButton(
            idle_text=idle_text,
            armed_text=f"Confirmer : {idle_text} (5s)",
            on_confirm=_on_confirm,
            size_hint_y=None,
            height=theme.FONT_SIZE_BODY * 2,
        )

    def _apply_mode(self, mode: str) -> None:
        """Called on confirmed double-tap. Persists + refreshes the panel."""
        self._data_source.set_mode(mode)
        # Repaint immediately so the badge moves to the new active mode.
        self.refresh()
