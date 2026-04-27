"""Application services — orchestration on top of the agent layer.

Where ``agent/`` provides isolated decision components and ``infra/``
provides I/O primitives, ``services/`` wires them into end-to-end use
cases that match what the bot actually has to do every cycle.

Modules :

* ``orchestrator`` — single-cycle pure decision. Takes klines and
  capital, returns a :class:`CycleDecision`. No I/O.

Future modules (cf. CLAUDE.md) :

* ``auto_trader``  — periodic scheduler that fetches data, calls
  :class:`Orchestrator`, places orders via Binance, and feeds back the
  outcome to the learning modules.
* ``backup``       — atomic SQLite backup + restore.
* ``health``       — liveness + readiness checks for the niveau-entreprise SLA.
"""

from emeraude.services.auto_trader import (
    AutoTrader,
    CycleReport,
)
from emeraude.services.backup import (
    BackupRecord,
    BackupService,
)
from emeraude.services.calibration_tracker import (
    compute_calibration_from_positions,
    extract_predictions_outcomes,
    is_well_calibrated_history,
)
from emeraude.services.gate_factories import (
    make_correlation_gate,
    make_microstructure_gate,
)
from emeraude.services.orchestrator import (
    CycleDecision,
    Orchestrator,
    TradeDirection,
)

__all__ = [
    "AutoTrader",
    "BackupRecord",
    "BackupService",
    "CycleDecision",
    "CycleReport",
    "Orchestrator",
    "TradeDirection",
    "compute_calibration_from_positions",
    "extract_predictions_outcomes",
    "is_well_calibrated_history",
    "make_correlation_gate",
    "make_microstructure_gate",
]
