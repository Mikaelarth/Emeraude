"""Emeraude — agent de trading crypto autonome, mobile-first Android, 100% local."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__: str = _pkg_version("emeraude")
except PackageNotFoundError:  # pragma: no cover  (fresh checkout pre-uv-sync)
    __version__ = "unknown"
