"""Shared pytest fixtures and global test isolation."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

# Kivy headless guards (ADR-0002 §7) — set BEFORE any kivy import that
# may happen via test modules. Keeps Kivy from parsing pytest's argv
# (KIVY_NO_ARGS) and from spamming the console banner during the run
# (KIVY_NO_CONSOLELOG). Window is not created until App.run(), so no
# display backend is needed for the L1 smoke test in tests/unit/.
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

import pytest

from emeraude.infra import audit, database

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_emeraude_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset Emeraude env vars + DB connection + audit singleton around every test.

    * Strips ``EMERAUDE_STORAGE_DIR``, ``ANDROID_ARGUMENT``, ``ANDROID_PRIVATE``
      so a polluted host environment cannot leak into tests.
    * After the test, closes the per-thread DB connection and shuts down
      the default audit logger if any. Otherwise the next test inherits
      a worker thread pointing at a deleted ``tmp_path`` DB.
    """
    for var in ("EMERAUDE_STORAGE_DIR", "ANDROID_ARGUMENT", "ANDROID_PRIVATE"):
        monkeypatch.delenv(var, raising=False)

    yield

    # Stop the audit worker BEFORE closing the DB connection : the worker
    # may still try to write while we tear down.
    audit.shutdown_default_logger(timeout=2.0)
    database.close_thread_connection()
