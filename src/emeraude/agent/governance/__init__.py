"""Governance layer — META supervision.

Long-running policies that operate **above** the trading loop : champion
lifecycle, periodic re-validation, drift escalation, etc.

Modules :

* ``champion_lifecycle`` — 4-state machine ACTIVE / SUSPECT / EXPIRED /
  IN_VALIDATION + ``champion_history`` audit table (doc 10 §7).

Future modules (cf. CLAUDE.md) :

* ``revalidation`` — monthly walk-forward + robustness check trigger.
* ``audit_query`` — high-level queries over the audit trail.
"""
