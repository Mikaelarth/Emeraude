"""Learning layer — LEARN.

Persistent memory of trade outcomes that lets the agent **improve over
time** (Pilier #2 — doc 03). Modules in this package read from and write
to the database, but do not perform I/O outside of it ; they consume
trade outcomes provided by the future ``services/auto_trader``.

Modules :

* ``regime_memory`` — per-(strategy, regime) outcome tracking with
  adaptive ensemble weights overriding the static doc-04 defaults
  once enough data is accumulated.

Future modules (cf. CLAUDE.md) :

* ``bandit``           — Thompson sampling over strategies.
* ``contextual_bandit`` — LinUCB on (regime, vol, hour, ...) features (R14).
* ``hoeffding``        — statistical guarantees on weight updates (R11).
* ``drift``            — Page-Hinkley / ADWIN drift detection (R3).
"""
