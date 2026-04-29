"""HTTP API layer — exposes the Python core to the web UI.

Iter #78 (cf. ADR-0004 — bascule WebView + Vuetify) extrait la couche
de présentation hors de Kivy : un petit serveur HTTP stdlib expose les
data sources existants (Dashboard / Journal / Config) en JSON, et la
WebView Android consomme ces endpoints.

Le module n'a aucune dépendance externe nouvelle :

* :mod:`http.server` (stdlib) pour le serveur HTTP.
* :mod:`json` (stdlib) pour la sérialisation.
* Les data sources existants (``emeraude.services.*``) consommés
  *as-is*, sans changement.

Architecture :

* :class:`AppContext` — composition root des services. Factorise ce
  qu'avait l'ancien :class:`emeraude.ui.app.EmeraudeApp.build`.
* :class:`EmeraudeHTTPServer` — serveur HTTP qui dispatch les routes
  vers les méthodes de :class:`AppContext` et sérialise les réponses.
* Routes exposées (iter #78) : ``GET /api/dashboard``. Iters suivants :
  ``GET /api/journal``, ``GET /api/config``, ``POST /api/toggle-mode``,
  ``POST /api/credentials``.
"""

from __future__ import annotations

from emeraude.api.context import AppContext

__all__ = ["AppContext"]
