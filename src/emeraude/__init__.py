"""Emeraude — agent de trading crypto autonome, mobile-first Android, 100% local."""

from importlib.metadata import version as _pkg_version

#: Hardcoded fallback for environments where ``importlib.metadata`` cannot
#: resolve the package version. Specifically : python-for-android packaged
#: APKs do not embed the ``.dist-info`` directory, so
#: ``importlib.metadata.version("emeraude")`` raises and the user sees
#: ``"unknown"`` (cf. iter #93 runtime test).
#:
#: **Maintenance contract** : this constant MUST stay in sync with the
#: ``version = "..."`` field in ``pyproject.toml`` and ``buildozer.spec``.
#: A pytest guard (``tests/unit/test_version_sync.py``) fails the suite
#: if the three diverge, so missed bumps are caught at CI time rather
#: than after a runtime APK install.
#:
#: Why three copies and not one ?
#: - ``pyproject.toml`` is the canonical single source of truth for the
#:   Python ecosystem (uv, pip, hatch, twine).
#: - ``buildozer.spec`` is consumed by Buildozer/p4a (separate ecosystem,
#:   no Python).
#: - This constant is consumed at Python runtime when neither tool's
#:   metadata is available (APK path).
#: The pytest guard collapses the maintenance cost to "bump 3 places at
#: once or the suite goes red".
_FALLBACK_VERSION: str = "0.0.99"

# ``importlib.metadata.version`` can fail on Android packaged apps
# (the package isn't pip-installed in the standard sense — p4a bundles
# it as a Python module without the ``.dist-info`` directory). We catch
# any exception here, not just ``PackageNotFoundError``, because the
# failure modes on Android are not strictly typed (LookupError, OSError
# from missing metadata files, etc.). Falling back to the hardcoded
# constant above is the right behaviour for an APK : the user sees the
# actual release version on the Config screen.
try:
    __version__: str = _pkg_version("emeraude")
except Exception:  # noqa: BLE001  (Android packaged app compat)
    __version__ = _FALLBACK_VERSION
