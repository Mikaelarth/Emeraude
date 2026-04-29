"""Emeraude application entry point.

Invoked by Buildozer on Android (`MAIN`) and by the developer on
desktop via ``uv run python -m emeraude.main``. The module deliberately
stays thin : its only job is to instantiate :class:`EmeraudeApp` and
run the Kivy main loop.

This file is excluded from ``coverage.run`` (cf. ``pyproject.toml``)
because the only meaningful path is ``app.run()`` which would block
the test process on the Kivy event loop.
"""

from __future__ import annotations

import os


def main() -> None:  # pragma: no cover  (entry point, runtime-only)
    """Bootstrap the Kivy application.

    Two environment guards are set **before** importing the UI module to
    keep Kivy quiet in CI / headless contexts (ADR-0002 §7) :

    * ``KIVY_NO_ARGS`` : Kivy must not parse ``sys.argv`` (which
      collides with our own CLI).
    * ``KIVY_NO_CONSOLELOG`` : silence the Kivy banner ; the app emits
      its own audit + log lines via :mod:`emeraude.infra.audit`.
    """
    os.environ.setdefault("KIVY_NO_ARGS", "1")
    os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

    # Imported here, after the env guards, so Kivy never sees our argv
    # and never spams the console banner. ``noqa PLC0415`` : the
    # placement is intentional and documented in ADR-0002 §7.
    from emeraude.ui.app import EmeraudeApp  # noqa: PLC0415

    EmeraudeApp().run()


if __name__ == "__main__":  # pragma: no cover
    main()
