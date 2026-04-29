"""Web-based UI bootstrap — démarre le serveur HTTP et c'est tout.

Iter #79 (cf. ADR-0004) — bascule de bootstrap p4a ``sdl2`` ->
``webview``. La Java side (``PythonActivity`` du bootstrap webview)
crée la WebView nativement, lance Python en thread, et redirige la
WebView sur ``http://127.0.0.1:<port>/`` quand le serveur Python
répond. Conséquences pour ce module :

* **Plus de Kivy** : ni ``App``, ni ``mainthread``, ni ``Window``.
  Le bootstrap webview ne dépend pas de Kivy.
* **Plus de pyjnius** : on n'instancie plus la WebView nous-mêmes,
  donc plus besoin d'``autoclass("android.webkit.WebView")`` ni de
  ``run_on_ui_thread``. Tout est natif Java côté bootstrap.
* **Plus de ``ERR_CLEARTEXT_NOT_PERMITTED``** : le manifest
  auto-généré par le bootstrap webview inclut nativement
  ``android:usesCleartextTraffic="true"``.

Sur **Android**, le rôle de ce module est minimal : composer
l'``AppContext``, démarrer le serveur HTTP, et bloquer (la
``PythonActivity`` Java continue à tourner et la WebView est gérée
côté Java).

Sur **desktop**, comportement identique : démarre le serveur, log
l'URL, bloque sur ``serve_forever``. L'utilisateur ouvre
``http://127.0.0.1:8765/`` dans son navigateur.

Anti-règle A1 : aucune fonctionnalité fictive.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from emeraude.api.context import AppContext
from emeraude.api.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    create_server,
)

_LOGGER = logging.getLogger(__name__)


def _is_android() -> bool:
    """True if the runtime is the python-for-android packaged app.

    p4a sets ``ANDROID_PRIVATE`` to the app's private storage dir
    (``/data/data/<pkg>/files``). On desktop this env var is absent.
    """
    return "ANDROID_PRIVATE" in os.environ


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


def run_web_app() -> None:  # pragma: no cover  (entry point, runtime only)
    """Boot the Emeraude UI : compose context, start HTTP server, block.

    The Java side (Android) or the user (desktop) handles the WebView
    that consumes this server.
    """
    context = AppContext()
    web_root = _resolve_web_root()
    server, _auth_token = create_server(context=context, web_root=web_root)
    url = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/"

    if _is_android():
        # Android — the webview bootstrap's WebViewLoader.testConnection
        # is polling localhost:port in a Java thread ; once we accept
        # the first connection it'll loadUrl() the WebView. Block here
        # so the Python process stays alive (the bootstrap calls Python
        # via JNI in its own thread but doesn't keep it alive on its own).
        _LOGGER.info("Emeraude HTTP server starting at %s (Android)", url)
        server.serve_forever()
        return

    # Desktop — print the URL for the dev's browser, block.
    msg = f"\n  Emeraude UI ready\n  Open this URL in your browser : {url}\n  (Ctrl-C to quit)\n"
    print(msg, flush=True)  # noqa: T201  (entry point user-visible output)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.", flush=True)  # noqa: T201
        server.shutdown()
