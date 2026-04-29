"""Web-based UI bootstrap — démarre le serveur HTTP et la WebView.

Iter #78 (cf. ADR-0004) — point d'entrée alternatif à l'ancien
:mod:`emeraude.ui.app` (Kivy widgets). Les responsabilités :

* Composer l'``AppContext`` (services).
* Démarrer le serveur HTTP local.
* Sur Android : ouvrir une WebView native via ``pyjnius`` et la
  pointer sur le serveur. La Kivy ``App`` reste un shell minimal
  pour que python-for-android garde le process alive.
* Sur desktop : log l'URL et bloque sur le serveur. L'utilisateur
  ouvre `http://127.0.0.1:8765/` dans son navigateur.

Anti-règle A1 : aucune fonctionnalité fictive. Les routes API
exposées par le serveur reflètent l'état réel des services Python.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

from emeraude.api.context import AppContext
from emeraude.api.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    create_server,
    serve_in_thread,
)

_LOGGER = logging.getLogger(__name__)


def _is_android() -> bool:
    """True if the runtime is the python-for-android packaged app.

    p4a sets ``ANDROID_PRIVATE`` to the app's private storage dir
    (``/data/data/<pkg>/files``). On desktop this env var is absent.
    """
    return "ANDROID_PRIVATE" in os.environ


# ─── Web root resolution ────────────────────────────────────────────────────

#: Search order for the bundled ``web/`` directory. We check several
#: candidate locations to support both the Android packaged context
#: (where p4a unpacks ``source.dir`` contents under
#: ``$ANDROID_PRIVATE/app/``) and the desktop dev context (where
#: ``web/`` lives at the project root, sibling of ``src/``).
_WEB_ROOT_CANDIDATES: Final[tuple[str, ...]] = (
    # Android : ``ANDROID_APP_PATH`` points to ``files/app`` ; web/
    # ships there alongside the emeraude package.
    "${ANDROID_APP_PATH}/web",
    # Desktop : same place after ``buildozer.spec`` source.dir = src
    # mapping ; the web/ directory is added via include_patterns.
    "$(repo_root)/web",
)


def _resolve_web_root() -> Path:
    """Locate the ``web/`` directory shipped with the app.

    The web app ships **inside the Python package** at
    ``src/emeraude/web/``. Buildozer's ``source.include_patterns``
    (cf. ``buildozer.spec``) bundle the directory into the APK at
    ``$ANDROID_APP_PATH/emeraude/web/``. On desktop dev, the same
    directory is at ``Path(__file__).parent / 'web'``.

    Returns the absolute path or raises if missing.
    """
    here = Path(__file__).resolve()
    candidate = here.parent / "web"
    if (candidate / "index.html").is_file():
        return candidate.resolve()

    msg = (
        f"web/ directory not found at {candidate} ; expected to ship "
        "inside the emeraude package via buildozer source.include_patterns. "
        f"started from {here}"
    )
    raise FileNotFoundError(msg)


# ─── Android WebView ────────────────────────────────────────────────────────


def _open_android_webview(url: str, auth_token: str) -> None:
    """Replace the Kivy ContentView with an Android WebView pointed at ``url``.

    Must run on the Android UI thread — we wrap the body with
    :func:`kivy.clock.mainthread`. Keeps :mod:`pyjnius` and :mod:`kivy`
    imports lazy (this module must remain importable on desktop).

    The ``auth_token`` is set as a cookie before ``loadUrl`` so the
    SPA's first ``fetch('/api/...')`` includes it (cf. ADR-0004
    sécurité loopback).
    """
    # Android-only imports — jnius has no type stubs (we ignore[import-not-found])
    # and the mainthread decorator from Kivy is untyped (ignore[misc]).
    from jnius import autoclass  # type: ignore[import-not-found]  # noqa: PLC0415
    from kivy.clock import mainthread  # noqa: PLC0415

    # Java class names follow the JVM convention (PascalCase) ;
    # ruff N806 is silenced explicitly per autoclass call.
    PythonActivity = autoclass("org.kivy.android.PythonActivity")  # noqa: N806
    WebView = autoclass("android.webkit.WebView")  # noqa: N806
    WebViewClient = autoclass("android.webkit.WebViewClient")  # noqa: N806
    CookieManager = autoclass("android.webkit.CookieManager")  # noqa: N806

    @mainthread  # type: ignore[untyped-decorator]
    def _create() -> None:
        activity = PythonActivity.mActivity

        # Pre-set the auth cookie so the first GET / can use it. We
        # also set it for /api/ since the SPA fetches with
        # credentials: 'include'. Set-Cookie via Set-Cookie header on
        # GET / works too — belt-and-suspenders.
        cm = CookieManager.getInstance()
        cm.setAcceptCookie(True)
        cookie_value = f"emeraude_auth={auth_token}; Path=/"
        cm.setCookie(url, cookie_value)

        wv = WebView(activity)
        settings = wv.getSettings()
        settings.setJavaScriptEnabled(True)
        settings.setDomStorageEnabled(True)
        settings.setAllowFileAccess(False)
        settings.setAllowContentAccess(False)
        wv.setWebViewClient(WebViewClient())
        wv.loadUrl(url)
        activity.setContentView(wv)
        _LOGGER.info("Android WebView attached to %s", url)

    _create()


# ─── Entry points ───────────────────────────────────────────────────────────


def run_web_app() -> None:  # pragma: no cover  (entry point, runtime only)
    """Boot the Emeraude UI : HTTP server + WebView (Android) or print URL (desktop)."""
    context = AppContext()
    web_root = _resolve_web_root()
    server, auth_token = create_server(context=context, web_root=web_root)
    url = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/"

    if _is_android():
        # Android : background the server, run a minimal Kivy shell so
        # python-for-android keeps the process alive. The shell's
        # on_start replaces the ContentView with the Android WebView.
        serve_in_thread(server)

        from kivy.app import App  # noqa: PLC0415  (Android-only path)
        from kivy.uix.widget import Widget  # noqa: PLC0415

        class _Shell(App):  # type: ignore[misc]  # Kivy classes untyped.
            """Tiny Kivy shell — its only job is to host the WebView."""

            def build(self) -> Widget:
                # Empty placeholder. The WebView replaces this in on_start.
                return Widget()

            def on_start(self) -> None:
                _open_android_webview(url, auth_token)

        _Shell().run()
        return

    # Desktop : block in main thread serving the HTTP. User opens the
    # URL in any browser. Ctrl-C stops the server.
    msg = f"\n  Emeraude UI ready\n  Open this URL in your browser : {url}\n  (Ctrl-C to quit)\n"
    print(msg, flush=True)  # noqa: T201  (entry point user-visible output)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.", flush=True)  # noqa: T201
        server.shutdown()
