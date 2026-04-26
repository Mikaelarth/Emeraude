"""Agent layer — pure-domain logic with no I/O.

Sub-packages :

* ``perception``  — SENSE : market data, regime, indicators.
* ``reasoning``   — DECIDE : meta-gate, ensemble, calibration, sizing.
* ``execution``   — ACT : order placement, circuit breaker.
* ``learning``    — LEARN : Thompson, UCB, drift detection.
* ``governance``  — META : champion lifecycle, audit.

Every module here MUST be importable and testable without filesystem,
database, or network access. I/O lives in :mod:`emeraude.infra` ; the
agent layer reads inputs that the orchestrator pre-fetches.
"""
