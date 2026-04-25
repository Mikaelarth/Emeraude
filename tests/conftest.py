"""Shared pytest fixtures and global test isolation."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_emeraude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip Emeraude/Android env vars before every test.

    Prevents tests from leaking into one another via os.environ, and prevents
    the host machine's environment from accidentally activating Android-mode
    detection during local runs.
    """
    for var in ("EMERAUDE_STORAGE_DIR", "ANDROID_ARGUMENT", "ANDROID_PRIVATE"):
        monkeypatch.delenv(var, raising=False)
