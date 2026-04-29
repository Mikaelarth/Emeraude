"""Mobile-first screens (5 cibles : Dashboard, Configuration, Backtest, Audit, Learning).

ADR-0002 §1 — chacune des 5 missions UX (doc 02 §"Cartographie des 5
écrans") a son propre module ici. Les widgets reçoivent leurs services
par injection au constructeur, jamais via singleton.

Statut iter #59 :

* :mod:`dashboard` — premier écran fonctionnel, livré.
* ``configuration``, ``backtest``, ``audit``, ``learning`` —
  itérations futures.
"""
