"""Buildozer entry point — minimal shim importing the real main.

Buildozer / python-for-android expect ``main.py`` at the root of
``source.dir`` (configured to ``src`` in :file:`buildozer.spec`).

The real bootstrap logic (Kivy env guards, App instantiation,
:meth:`run`) lives in :mod:`emeraude.main`. This file is **not**
included in the pip wheel (cf. ``[tool.hatch.build.targets.wheel]
packages = ["src/emeraude"]`` in :file:`pyproject.toml`) — it's
purely a Buildozer-side artifact.

Coverage : excluded by design (the shim is exercised via the actual
APK runtime, T4 manuel device test, not via pytest).
"""

from emeraude.main import main

if __name__ == "__main__":  # pragma: no cover  (APK entry point)
    main()
