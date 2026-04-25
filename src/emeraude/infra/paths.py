"""Storage path management — Android-safe by default.

Single source of truth for every persistent file in Emeraude (database, salt,
backups, logs, audit trail). Honors rule R7 of the cahier des charges
(`Emeraude/07_REGLES_OR_ET_ANTI_REGLES.md`): no module persists data via
`Path(__file__).parent.parent` — every persistent file goes through here.

Resolution order for the storage root:

    1. ``EMERAUDE_STORAGE_DIR`` environment variable (test override / advanced)
    2. Android private app storage (``/data/data/<pkg>/files/``)
    3. Desktop fallback (``~/.emeraude/``)

Rationale:

* Android private storage survives APK updates and is wiped only on uninstall —
  the durability guarantee we need for the bot's learning history (R10).
* The env-var override exists because ``hypothesis`` and ``pytest`` need
  reproducible isolation per test, and because future Android forensic tooling
  may want to point the bot at a backup directory.

This module imports nothing outside ``stdlib`` so it is safe to import in any
test environment, including CI runners without Kivy installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

_ENV_OVERRIDE: Final[str] = "EMERAUDE_STORAGE_DIR"
_ANDROID_MARKER: Final[str] = "ANDROID_ARGUMENT"
_ANDROID_PRIVATE: Final[str] = "ANDROID_PRIVATE"
_DESKTOP_DIRNAME: Final[str] = ".emeraude"


def is_android() -> bool:
    """Return ``True`` iff the current process runs under python-for-android.

    Detection relies on the ``ANDROID_ARGUMENT`` environment variable that
    python-for-android (the runner used by Buildozer) sets at launch. This
    avoids importing Kivy here, keeping the module lightweight and importable
    in unit tests.
    """
    return os.environ.get(_ANDROID_MARKER) is not None


def _android_storage_dir() -> Path:
    """Return the Android private app storage directory.

    Reads ``ANDROID_PRIVATE`` (set by python-for-android). If the variable is
    missing we surface a ``RuntimeError`` rather than silently falling back to
    a non-persistent location — silent fallback would violate the integrity
    guarantee of the persistence layer (cf. anti-rule A8: no silent error).
    """
    private = os.environ.get(_ANDROID_PRIVATE)
    if not private:
        msg = (
            "Detected Android runtime (ANDROID_ARGUMENT set) but ANDROID_PRIVATE "
            "is missing. App storage path cannot be determined."
        )
        raise RuntimeError(msg)
    return Path(private)


def _desktop_storage_dir() -> Path:
    r"""Default desktop storage path: ``~/.emeraude/``.

    Cross-platform (resolves to ``%USERPROFILE%\.emeraude`` on Windows and
    ``$HOME/.emeraude`` on POSIX) and survives reboots. Suitable for dev work
    on macOS, Linux, and Windows.
    """
    return Path.home() / _DESKTOP_DIRNAME


def app_storage_dir() -> Path:
    """Return the absolute root storage directory, creating it on demand.

    The directory is the canonical anchor for every persistent file. Resolution
    order is documented at module level. The result is always an absolute,
    existing directory.

    Returns:
        Absolute path to the storage root.

    Raises:
        RuntimeError: If running on Android but ``ANDROID_PRIVATE`` is unset.
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        path = Path(override).expanduser().resolve()
    elif is_android():
        path = _android_storage_dir()
    else:
        path = _desktop_storage_dir()

    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    """Path to the main SQLite database file (``emeraude.db``)."""
    return app_storage_dir() / "emeraude.db"


def salt_path() -> Path:
    """Path to the PBKDF2 salt file used to derive the API-key encryption key.

    Stored as a hidden file at the storage root. On POSIX, callers should also
    enforce ``chmod 0o600`` on this file (handled by the crypto module).
    """
    return app_storage_dir() / ".emeraude_salt"


def backups_dir() -> Path:
    """Directory for atomic database backups."""
    target = app_storage_dir() / "backups"
    target.mkdir(exist_ok=True)
    return target


def logs_dir() -> Path:
    """Directory for rotated log files."""
    target = app_storage_dir() / "logs"
    target.mkdir(exist_ok=True)
    return target


def audit_dir() -> Path:
    """Directory for the structured JSON audit trail."""
    target = app_storage_dir() / "audit"
    target.mkdir(exist_ok=True)
    return target
