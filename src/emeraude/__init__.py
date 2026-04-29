"""Emeraude — agent de trading crypto autonome, mobile-first Android, 100% local."""

from importlib.metadata import version as _pkg_version

# ``importlib.metadata.version`` can fail on Android packaged apps
# (the package isn't pip-installed in the standard sense — p4a bundles
# it as a Python module without the ``.dist-info`` directory). We catch
# any exception here, not just ``PackageNotFoundError``, because the
# failure modes on Android are not strictly typed (LookupError, OSError
# from missing metadata files, etc.). Falling back to ``"unknown"``
# is acceptable — the version is only used for display in the Config
# screen.
try:
    __version__: str = _pkg_version("emeraude")
except Exception:  # noqa: BLE001  (Android packaged app compat)
    __version__ = "unknown"
