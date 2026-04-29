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
    """Bootstrap the Emeraude application.

    Iter #79 (cf. ADR-0004) — la couche UI utilise désormais le p4a
    bootstrap ``webview`` : la PythonActivity Java crée la WebView,
    lance Python en thread, et redirige sur le serveur HTTP local.
    On ne touche plus à Kivy ni à pyjnius côté Python — d'où
    l'absence des guards ``KIVY_NO_ARGS`` / ``KIVY_NO_CONSOLELOG``
    qu'on avait avant.

    Any exception raised during the bootstrap (import errors, DB
    init failures, etc.) is captured to ``last_crash.log`` (iter #71
    crash logger) before being re-raised. Cela rend triageable un
    Android first-launch crash sans ADB.
    """
    try:
        from emeraude.web_app import run_web_app  # noqa: PLC0415

        run_web_app()
    except Exception:
        _write_crash_log(traceback.format_exc())
        # Re-raise so Android emits its normal crash report AND so
        # the user sees the app close with a clear error.
        raise


if __name__ == "__main__":  # pragma: no cover
    # When invoked as a script (``python -m emeraude.main``), the same
    # crash-handling path applies. We also exit with a non-zero code so
    # CI / shell scripts can detect the failure.
    try:
        main()
    except Exception:  # noqa: BLE001  (top-level boundary)
        sys.exit(1)
