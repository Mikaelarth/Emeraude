"""D-style guard : ``_FALLBACK_VERSION`` stays in sync with packaging files.

Iter #94 fixed the runtime APK bug where ``Version: vunknown`` was
displayed on the Config screen. The fix introduced a hardcoded
``_FALLBACK_VERSION`` constant in :mod:`emeraude.__init__` that is
returned when ``importlib.metadata.version`` fails (the APK case).

Without a guard, that constant would inevitably drift from
``pyproject.toml`` / ``buildozer.spec`` (developer bumps the latter,
forgets the former, the next APK ships with the wrong version on its
own UI). This module adds a pytest test that fails the suite when
any of the three diverges, so missed bumps are caught at CI time
rather than after a runtime APK install.

The test parses the two packaging files via :mod:`tomllib` (stdlib)
and a small regex respectively — no third-party dependency.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final

import pytest

from emeraude import _FALLBACK_VERSION, __version__

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_PYPROJECT: Final[Path] = _PROJECT_ROOT / "pyproject.toml"
_BUILDOZER_SPEC: Final[Path] = _PROJECT_ROOT / "buildozer.spec"


def _read_pyproject_version() -> str:
    """Parse ``pyproject.toml`` and return the ``[project] version`` field."""
    with _PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"])


def _read_buildozer_version() -> str:
    """Parse ``buildozer.spec`` and return the ``version =`` line.

    Buildozer specs are INI-flavoured but with a few quirks (no
    string quoting, comments via ``#``). A targeted regex is more
    robust here than spinning up :mod:`configparser`.
    """
    text = _BUILDOZER_SPEC.read_text(encoding="utf-8")
    match = re.search(r"^version\s*=\s*([^\s#]+)", text, re.MULTILINE)
    if match is None:
        msg = "buildozer.spec : 'version = ...' line not found"
        raise AssertionError(msg)
    return match.group(1).strip()


@pytest.mark.unit
class TestVersionSync:
    """The three version sources MUST agree byte-for-byte."""

    def test_fallback_matches_pyproject(self) -> None:
        pyproject_version = _read_pyproject_version()
        assert pyproject_version == _FALLBACK_VERSION, (
            f"emeraude.__init__._FALLBACK_VERSION ({_FALLBACK_VERSION!r}) "
            f"must match pyproject.toml version ({pyproject_version!r}). "
            "Bumping the version requires updating BOTH pyproject.toml AND "
            "src/emeraude/__init__.py — see the maintenance contract in "
            "_FALLBACK_VERSION's docstring."
        )

    def test_buildozer_matches_pyproject(self) -> None:
        pyproject_version = _read_pyproject_version()
        buildozer_version = _read_buildozer_version()
        assert buildozer_version == pyproject_version, (
            f"buildozer.spec version ({buildozer_version!r}) must match "
            f"pyproject.toml version ({pyproject_version!r}). Bumping "
            "the version requires updating both files."
        )

    def test_fallback_matches_buildozer(self) -> None:
        # Transitive — if the two assertions above pass, this one follows.
        # Kept as an explicit test so a CI failure points to the exact
        # pair that diverged.
        buildozer_version = _read_buildozer_version()
        assert buildozer_version == _FALLBACK_VERSION

    def test_runtime_version_is_set(self) -> None:
        # ``__version__`` resolves either to the importlib-metadata
        # value (dev / CI path) or to ``_FALLBACK_VERSION`` (APK path).
        # Either way it must NOT be the ``"unknown"`` placeholder
        # we used before iter #94.
        assert __version__ != "unknown", (
            "emeraude.__version__ resolved to 'unknown' — the iter #94 "
            "fallback fix did not take effect. Check that "
            "_FALLBACK_VERSION is set and that the import chain is intact."
        )
