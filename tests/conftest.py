"""Shared pytest fixtures and global test isolation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

# Kivy headless guards (ADR-0002 §7) — set BEFORE any kivy import that
# may happen via test modules. Keeps Kivy from parsing pytest's argv
# (KIVY_NO_ARGS) and from spamming the console banner during the run
# (KIVY_NO_CONSOLELOG). Window is not created until App.run(), so no
# display backend is needed for the L1 smoke test in tests/unit/.
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

# Per-worker isolated KIVY_HOME : Kivy 2.3's __init__.py does a
# non-atomic ``if not exists(home): mkdir(home)`` (and same for the
# ``mods`` subdir) which races under pytest-xdist (multiple workers
# attempting to create ~/.kivy simultaneously, only one wins, others
# crash with FileExistsError). Three guards :
#
# 1. Key on ``os.getpid()`` so each worker process gets its own
#    directory unconditionally.
# 2. **Override** rather than ``setdefault`` : workers spawned by the
#    pytest-xdist controller inherit ``KIVY_HOME`` via env, so a
#    setdefault would make them all share the controller's home and
#    race on its ``mods`` subdir. Forcing the override per-PID breaks
#    the inheritance.
# 3. Pre-create ``KIVY_HOME/mods`` ourselves so kivy.__init__'s
#    ``if not exists: mkdir`` is a no-op even if the worker only
#    creates the parent dir at conftest time.
_kivy_home = Path(tempfile.gettempdir()) / f"emeraude-kivy-{os.getpid()}"
_kivy_home.mkdir(parents=True, exist_ok=True)
(_kivy_home / "mods").mkdir(parents=True, exist_ok=True)
(_kivy_home / "logs").mkdir(parents=True, exist_ok=True)
(_kivy_home / "icon").mkdir(parents=True, exist_ok=True)
os.environ["KIVY_HOME"] = str(_kivy_home)

import pytest  # noqa: E402  # Imports must follow the env guards above.

from emeraude.infra import audit, database  # noqa: E402  # Same reason.

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
