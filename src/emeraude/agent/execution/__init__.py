"""Execution layer — ACT.

Modules :

* ``circuit_breaker`` — non-bypass safety net (rule R10). The 4-state
  machine that gates every order-placement decision.

Future modules (cf. CLAUDE.md) :

* ``smart_order``    — limit @ mid + TTL + market fallback (R9).
* ``orchestrator``   — sequencing of perception -> reasoning -> action.
"""
