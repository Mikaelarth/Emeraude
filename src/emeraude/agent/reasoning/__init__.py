"""Reasoning layer — DECIDE.

Sub-packages :

* ``strategies``  — 3 directional signal generators (trend, mean reversion,
  breakout) producing :class:`StrategySignal` instances.

Future modules in this layer (see CLAUDE.md / cahier des charges) :

* ``ensemble``    — adaptive-weighted vote across strategies (R14).
* ``meta_gate``   — "should we trade now ?" classifier (R8).
* ``calibration`` — confidence calibration via Brier / ECE (R1).
* ``tail_risk``   — Cornish-Fisher VaR + CVaR (R5).
* ``conformal``   — distribution-free prediction intervals (R15).
"""
