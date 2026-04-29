"""Emeraude application entry point.

Invoked by Buildozer on Android (`MAIN`) and by the developer on
desktop via ``uv run python -m emeraude.main``. The module deliberately
stays thin : its only job is to instantiate :class:`EmeraudeApp` and
run the Kivy main loop.

This file is excluded from ``coverage.run`` (cf. ``pyproject.toml``)
because the only meaningful path is ``app.run()`` which would block
the test process on the Kivy event loop.

Iter #71 : crash-to-file logging added. On Android, when the bootstrap
raises (recipe missing, DB init fail, etc.), the traceback is written
to ``ANDROID_PRIVATE/last_crash.log`` so the user can extract it
without ADB. The exception is then re-raised so Kivy / Android
report the crash normally.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _write_crash_log(exc_text: str) -> None:
    """Best-effort dump of a traceback to a file the user can find.

    On Android, writes to ``$ANDROID_PRIVATE/last_crash.log`` (private
    app dir, accessible by the app itself but readable via ADB
    ``adb shell run-as org.mikaelarth.emeraude cat files/last_crash.log``).
    On desktop, writes next to the storage dir resolved by
    :mod:`emeraude.infra.paths` if importable, else a temp dir.

    Never raises — the goal is to capture diagnostic info, not to
    cascade a second crash on top of the first.
    """
    try:
        # 1. Prefer ANDROID_PRIVATE (Android packaged app).
        target_dir_str = os.environ.get("ANDROID_PRIVATE")
        if target_dir_str:
            target_dir = Path(target_dir_str)
        else:
            # 2. Try the resolved storage dir (desktop dev).
            try:
                from emeraude.infra import paths  # noqa: PLC0415

                target_dir = paths.app_storage_dir()
            except Exception:  # noqa: BLE001  (best-effort, can fail anywhere)
                # 3. Last resort : temp dir.
                import tempfile  # noqa: PLC0415

                target_dir = Path(tempfile.gettempdir())

        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "last_crash.log").write_text(exc_text, encoding="utf-8")
    except Exception:  # noqa: BLE001  (best-effort, see below)
        # We're already in a bootstrap failure path. If the log dump
        # itself crashes, there's nothing else we can do — surface
        # the original failure via the re-raise upstream. Logging the
        # secondary exception here would just mask the original.
        return


def main() -> None:  # pragma: no cover  (entry point, runtime-only)
    """Bootstrap the Kivy application.

    Two environment guards are set **before** importing the UI module to
    keep Kivy quiet in CI / headless contexts (ADR-0002 §7) :

    * ``KIVY_NO_ARGS`` : Kivy must not parse ``sys.argv`` (which
      collides with our own CLI).
    * ``KIVY_NO_CONSOLELOG`` : silence the Kivy banner ; the app emits
      its own audit + log lines via :mod:`emeraude.infra.audit`.

    Any exception raised during the bootstrap (import errors, DB
    init failures, missing recipes on Android, etc.) is captured to
    ``last_crash.log`` before being re-raised. This makes triaging
    Android first-launch crashes possible without ADB access.
    """
    os.environ.setdefault("KIVY_NO_ARGS", "1")
    os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

    try:
        # Imported here, after the env guards, so Kivy never sees our argv
        # and never spams the console banner. ``noqa PLC0415`` : the
        # placement is intentional and documented in ADR-0002 §7.
        from emeraude.ui.app import EmeraudeApp  # noqa: PLC0415

        EmeraudeApp().run()
    except Exception:
        _write_crash_log(traceback.format_exc())
        # Re-raise so Kivy / Android emit their normal crash report
        # AND so the user sees the app close with a clear error.
        raise


if __name__ == "__main__":  # pragma: no cover
    # When invoked as a script (``python -m emeraude.main``), the same
    # crash-handling path applies. We also exit with a non-zero code so
    # CI / shell scripts can detect the failure.
    try:
        main()
    except Exception:  # noqa: BLE001  (top-level boundary)
        sys.exit(1)
