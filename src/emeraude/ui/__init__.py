"""Kivy UI layer (mobile-first).

See ADR-0002 for the architecture decisions :

* :class:`EmeraudeApp` (in :mod:`emeraude.ui.app`) is the composition
  root. It instantiates the concrete services from :mod:`emeraude.services`
  and injects them into each :class:`~kivy.uix.screenmanager.Screen`.
* :mod:`emeraude.ui.theme` exposes the palette + sizing constants.
* :mod:`emeraude.ui.screens` will host the 5 mobile screens (Dashboard,
  Configuration, Backtest, Audit, Learning) — added one screen per
  iteration starting iter #59.

This package is **excluded from coverage** by ``pyproject.toml`` until
the screen-level L2 tests are mature. The L1 smoke test in
``tests/unit/test_ui_smoke.py`` guarantees importability + a non-empty
ScreenManager root.
"""
