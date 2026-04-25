"""Shared pytest fixtures and global test isolation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from emeraude.infra import database

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_emeraude_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset Emeraude-related env vars and DB state around every test.

    * Strips ``EMERAUDE_STORAGE_DIR``, ``ANDROID_ARGUMENT``, ``ANDROID_PRIVATE``
      so a polluted host environment cannot leak into tests.
    * After the test, closes the per-thread DB connection if any was opened —
      otherwise the next test inherits a connection pointing at a deleted
      ``tmp_path`` DB.
    """
    for var in ("EMERAUDE_STORAGE_DIR", "ANDROID_ARGUMENT", "ANDROID_PRIVATE"):
        monkeypatch.delenv(var, raising=False)

    yield

    database.close_thread_connection()
