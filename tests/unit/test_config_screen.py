"""L2 tests for :class:`ConfigScreen` Kivy widget (iter #64).

ADR-0002 §7 — gated by ``_DISPLAY_AVAILABLE`` because Kivy 2.3
instantiates a Window as soon as a Label / Button is created.
Headless ubuntu-latest CI runners skip this class.

Covers :

* Construction + initial render with snapshot data.
* Toggle button arming + double-tap confirmation triggers
  ``data_source.set_mode``.
* Active mode shows a non-clickable badge instead of a button.
* Refresh after mode change rebuilds panels.
"""

from __future__ import annotations

import os
import platform
from decimal import Decimal

import pytest
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from emeraude.services.binance_credentials import (
    BinanceCredentialsStatus,
    CredentialFormatError,
)
from emeraude.services.config_types import ConfigSnapshot
from emeraude.services.dashboard_types import (
    MODE_PAPER,
    MODE_REAL,
    MODE_UNCONFIGURED,
)
from emeraude.ui.screens.config import (
    CONFIG_SCREEN_NAME,
    ConfigScreen,
    _TwoStageButton,
)

# ─── Display gating ────────────────────────────────────────────────────────

_DISPLAY_AVAILABLE: bool = (
    platform.system() in {"Windows", "Darwin"}
    or bool(os.environ.get("DISPLAY"))
    or bool(os.environ.get("WAYLAND_DISPLAY"))
)
_NO_DISPLAY_REASON = "Kivy Window cannot init without a display backend (headless CI)"


# ─── Fakes ────────────────────────────────────────────────────────────────


class _FakeConfigDataSource:
    """In-memory ConfigDataSource for widget tests."""

    def __init__(self, initial_mode: str = MODE_PAPER) -> None:
        self.next_snapshot = self._build_snapshot(initial_mode)
        self.fetch_calls = 0
        self.set_mode_calls: list[str] = []

    def _build_snapshot(self, mode: str) -> ConfigSnapshot:
        return ConfigSnapshot(
            mode=mode,
            starting_capital=Decimal("20"),
            app_version="0.0.64",
            total_audit_events=42,
            db_path="emeraude-test.db",
        )

    def fetch_snapshot(self) -> ConfigSnapshot:
        self.fetch_calls += 1
        return self.next_snapshot

    def set_mode(self, mode: str) -> None:
        self.set_mode_calls.append(mode)
        self.next_snapshot = self._build_snapshot(mode)


class _FakeBinanceCredentialsService:
    """In-memory BinanceCredentialsService for widget tests.

    Mirrors the production API surface : ``get_status``,
    ``save_credentials``, ``clear_credentials``. Keeps a record of
    every call so the widget tests can assert on dispatched values.
    """

    def __init__(
        self,
        *,
        passphrase_available: bool = True,
        api_key_set: bool = False,
        api_secret_set: bool = False,
        api_key_suffix: str | None = None,
    ) -> None:
        self._passphrase_available = passphrase_available
        self._api_key_set = api_key_set
        self._api_secret_set = api_secret_set
        self._api_key_suffix = api_key_suffix
        self.save_calls: list[tuple[str, str]] = []
        self.clear_calls = 0

    def get_status(self) -> BinanceCredentialsStatus:
        return BinanceCredentialsStatus(
            api_key_set=self._api_key_set,
            api_secret_set=self._api_secret_set,
            api_key_suffix=self._api_key_suffix,
            passphrase_available=self._passphrase_available,
        )

    def save_credentials(self, *, api_key: str, api_secret: str) -> None:
        self.save_calls.append((api_key, api_secret))
        # Mimic the prod side-effect : after a successful save, the
        # status reflects the new keys.
        self._api_key_set = True
        self._api_secret_set = True
        self._api_key_suffix = api_key[-4:]

    def clear_credentials(self) -> None:
        self.clear_calls += 1
        self._api_key_set = False
        self._api_secret_set = False
        self._api_key_suffix = None


def _make_screen(
    *,
    initial_mode: str = MODE_PAPER,
    passphrase_available: bool = True,
    api_key_set: bool = False,
    api_key_suffix: str | None = None,
) -> tuple[ConfigScreen, _FakeConfigDataSource, _FakeBinanceCredentialsService]:
    """Helper to build a fully wired ConfigScreen + its 2 fakes."""
    config_ds = _FakeConfigDataSource(initial_mode=initial_mode)
    binance_svc = _FakeBinanceCredentialsService(
        passphrase_available=passphrase_available,
        api_key_set=api_key_set,
        api_secret_set=api_key_set,  # twin
        api_key_suffix=api_key_suffix,
    )
    screen = ConfigScreen(
        data_source=config_ds,
        binance_credentials_service=binance_svc,
        name=CONFIG_SCREEN_NAME,
    )
    return screen, config_ds, binance_svc


# ─── Construction ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestConstruction:
    def test_screen_uses_provided_name(self) -> None:
        ds = _FakeConfigDataSource()
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        assert screen.name == CONFIG_SCREEN_NAME

    def test_initial_render_pulls_one_snapshot(self) -> None:
        ds = _FakeConfigDataSource()
        ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        assert ds.fetch_calls == 1

    def test_status_panel_has_5_rows(self) -> None:
        ds = _FakeConfigDataSource()
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        # 5 rows : mode, capital, version, audit count, db path.
        assert len(screen._status_panel.children) == 5

    def test_toggle_panel_has_2_widgets(self) -> None:
        ds = _FakeConfigDataSource()
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        # 2 widgets : one badge (active) + one TwoStageButton (inactive).
        assert len(screen._toggle_panel.children) == 2


# ─── Active mode badge vs toggle button ────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestActiveBadge:
    def test_paper_active_paper_button_is_badge(self) -> None:
        ds = _FakeConfigDataSource(initial_mode=MODE_PAPER)
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        # Find the widgets : Kivy stores children in REVERSE add order.
        toggle_widgets = list(screen._toggle_panel.children)
        # One is a TwoStageButton, the other a plain Label badge.
        button_count = sum(1 for w in toggle_widgets if isinstance(w, _TwoStageButton))
        assert button_count == 1

    def test_real_active_real_button_is_badge(self) -> None:
        ds = _FakeConfigDataSource(initial_mode=MODE_REAL)
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        toggle_widgets = list(screen._toggle_panel.children)
        button_count = sum(1 for w in toggle_widgets if isinstance(w, _TwoStageButton))
        assert button_count == 1

    def test_unconfigured_active_both_targets_are_buttons(self) -> None:
        # Neither paper nor real is current -> both are toggle buttons.
        ds = _FakeConfigDataSource(initial_mode=MODE_UNCONFIGURED)
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        toggle_widgets = list(screen._toggle_panel.children)
        button_count = sum(1 for w in toggle_widgets if isinstance(w, _TwoStageButton))
        assert button_count == 2


# ─── 2-stage button arming + confirmation ─────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestTwoStageButton:
    def test_first_press_arms_does_not_invoke(self) -> None:
        invocations: list[None] = []
        button = _TwoStageButton(
            idle_text="Action",
            armed_text="Confirmer Action",
            on_confirm=lambda: invocations.append(None),
        )
        button.dispatch("on_press")
        assert button._is_armed is True
        assert button.text == "Confirmer Action"
        assert invocations == []

    def test_second_press_invokes_and_disarms(self) -> None:
        invocations: list[None] = []
        button = _TwoStageButton(
            idle_text="Action",
            armed_text="Confirmer Action",
            on_confirm=lambda: invocations.append(None),
        )
        button.dispatch("on_press")  # arm
        button.dispatch("on_press")  # fire
        assert button._is_armed is False
        assert button.text == "Action"
        assert len(invocations) == 1


# ─── Mode toggle end-to-end ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestModeToggleEndToEnd:
    def test_double_tap_inactive_button_calls_set_mode(self) -> None:
        # Initial mode = paper. The Real button is the inactive one.
        ds = _FakeConfigDataSource(initial_mode=MODE_PAPER)
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        # Find the Real toggle button (the only TwoStageButton).
        toggles = [w for w in screen._toggle_panel.children if isinstance(w, _TwoStageButton)]
        assert len(toggles) == 1
        real_button = toggles[0]

        # Double-tap.
        real_button.dispatch("on_press")  # arm
        real_button.dispatch("on_press")  # fire

        assert ds.set_mode_calls == [MODE_REAL]

    def test_after_toggle_panel_repaints_with_new_active(self) -> None:
        # Initial paper -> double-tap real -> screen.refresh fires
        # automatically -> Real becomes the badge, Paper becomes the
        # button.
        ds = _FakeConfigDataSource(initial_mode=MODE_PAPER)
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        toggles = [w for w in screen._toggle_panel.children if isinstance(w, _TwoStageButton)]
        real_button = toggles[0]
        real_button.dispatch("on_press")
        real_button.dispatch("on_press")

        # After confirm + auto-refresh, the toggle panel should
        # reflect MODE_REAL as the new active.
        toggles_after = [w for w in screen._toggle_panel.children if isinstance(w, _TwoStageButton)]
        # Now Paper is the inactive toggle (1 button), Real is the badge.
        assert len(toggles_after) == 1


# ─── Refresh ──────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestRefresh:
    def test_refresh_calls_data_source(self) -> None:
        ds = _FakeConfigDataSource()
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        baseline = ds.fetch_calls
        screen.refresh()
        assert ds.fetch_calls == baseline + 1

    def test_refresh_picks_up_new_snapshot(self) -> None:
        ds = _FakeConfigDataSource(initial_mode=MODE_PAPER)
        screen = ConfigScreen(
            data_source=ds,
            binance_credentials_service=_FakeBinanceCredentialsService(),
            name=CONFIG_SCREEN_NAME,
        )
        # Mutate the snapshot externally (simulate a settings change
        # made by another flow) and call refresh().
        ds.next_snapshot = ConfigSnapshot(
            mode=MODE_REAL,
            starting_capital=Decimal("100"),
            app_version="9.9.9",
            total_audit_events=999,
            db_path="elsewhere-x.db",
        )
        screen.refresh()
        # The toggle panel reflects MODE_REAL as active now.
        toggles = [w for w in screen._toggle_panel.children if isinstance(w, _TwoStageButton)]
        assert len(toggles) == 1


# ─── Binance credentials section (iter #66) ────────────────────────────────


@pytest.mark.unit
@pytest.mark.skipif(not _DISPLAY_AVAILABLE, reason=_NO_DISPLAY_REASON)
class TestBinanceSection:
    def test_panel_present_when_passphrase_available(self) -> None:
        screen, _, _ = _make_screen(passphrase_available=True)
        # Panel must contain at least : header + 2 status rows + 2
        # text inputs + 1 save button = 6 widgets.
        assert len(screen._binance_panel.children) >= 6

    def test_form_disabled_when_passphrase_missing(self) -> None:
        # Lazy import — kivy.uix.textinput is heavier than other Kivy
        # widgets and triggers Window init on Linux headless CI even
        # at module import. Loaded only inside the gated test.
        from kivy.uix.textinput import TextInput  # noqa: PLC0415

        screen, _, _ = _make_screen(passphrase_available=False)
        # Panel must contain header + 2 status rows + hint = 4 widgets,
        # NO TextInput / TwoStageButton.
        has_input = any(isinstance(w, TextInput) for w in screen._binance_panel.children)
        has_2stage = any(isinstance(w, _TwoStageButton) for w in screen._binance_panel.children)
        assert not has_input
        assert not has_2stage

    def test_keys_set_displays_suffix_in_status(self) -> None:
        screen, _, _ = _make_screen(
            passphrase_available=True,
            api_key_set=True,
            api_key_suffix="abcd",
        )
        # The status row text should contain the masked suffix.
        # _make_status_row builds a BoxLayout(Label, Label), so we
        # walk the tree one level deeper to collect every Label.text.
        all_labels: list[str] = []
        for child in screen._binance_panel.children:
            if isinstance(child, Label):
                all_labels.append(child.text)
            elif isinstance(child, BoxLayout):
                all_labels.extend(sub.text for sub in child.children if isinstance(sub, Label))
        joined = " ".join(all_labels)
        assert "abcd" in joined
        assert "definie" in joined

    def test_save_button_double_tap_calls_service(self) -> None:
        from kivy.uix.textinput import TextInput  # noqa: PLC0415

        screen, _, binance = _make_screen(passphrase_available=True)
        # Find the inputs + the 2-stage save button.
        inputs = [w for w in screen._binance_panel.children if isinstance(w, TextInput)]
        toggles = [w for w in screen._binance_panel.children if isinstance(w, _TwoStageButton)]
        assert len(inputs) == 2
        assert len(toggles) == 1
        # Inputs are children-stacked in REVERSE add order : last added
        # (api_secret) comes first. We want to set api_key first.
        secret_input, key_input = inputs[0], inputs[1]
        valid_key = "abcDEF0123456789xyzABC9876543210"  # pragma: allowlist secret
        valid_secret = "ZYXwvu98765432101234567890abcdef"  # pragma: allowlist secret
        key_input.text = valid_key
        secret_input.text = valid_secret

        save_button = toggles[0]
        save_button.dispatch("on_press")  # arm
        save_button.dispatch("on_press")  # confirm

        assert binance.save_calls == [(valid_key, valid_secret)]

    def test_save_invalid_key_shows_error_message(self) -> None:
        from kivy.uix.textinput import TextInput  # noqa: PLC0415

        # Use the REAL service to exercise format validation.
        # Easiest path : monkey-patch the fake to raise on save.
        screen, _, binance = _make_screen(passphrase_available=True)

        def _raising_save(*, api_key: str, api_secret: str) -> None:
            raise CredentialFormatError("api_key trop court")

        binance.save_credentials = _raising_save  # type: ignore[method-assign]

        inputs = [w for w in screen._binance_panel.children if isinstance(w, TextInput)]
        toggles = [w for w in screen._binance_panel.children if isinstance(w, _TwoStageButton)]
        inputs[1].text = "bogus"
        inputs[0].text = "alsobogus"
        toggles[0].dispatch("on_press")
        toggles[0].dispatch("on_press")

        # The status message label should now carry the error.
        labels_text = [w.text for w in screen._binance_panel.children if isinstance(w, Label)]
        joined = " ".join(labels_text)
        assert "Erreur format" in joined or "trop court" in joined
