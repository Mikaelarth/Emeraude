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
from emeraude.services.champion_promotion import (
    AUDIT_CHAMPION_PROMOTION_DECISION,
    PromotionDecision,
    evaluate_promotion,
)
from emeraude.services.drift_monitor import (
    AUDIT_DRIFT_DETECTED,
    DriftCheckResult,
    DriftMonitor,
)
from emeraude.services.gate_factories import (
    make_correlation_gate,
    make_microstructure_gate,
)
from emeraude.services.linucb_strategy_adapter import (
    LinUCBStrategyAdapter,
    build_regime_context,
)
from emeraude.services.monitor_checkpoint import (
    MonitorId,
    clear_triggered,
    load_triggered,
    save_triggered,
)
from emeraude.services.orchestrator import (
    CycleDecision,
    Orchestrator,
    TradeDirection,
)
from emeraude.services.performance_export import (
    export_from_positions,
    report_to_dict,
    report_to_json,
    report_to_markdown,
)
from emeraude.services.risk_monitor import (
    AUDIT_TAIL_RISK_BREACH,
    RiskCheckResult,
    RiskMonitor,
)

__all__ = [
    "AUDIT_CHAMPION_PROMOTION_DECISION",
    "AUDIT_DRIFT_DETECTED",
    "AUDIT_TAIL_RISK_BREACH",
    "AutoTrader",
    "BackupRecord",
    "BackupService",
    "CycleDecision",
    "CycleReport",
    "DriftCheckResult",
    "DriftMonitor",
    "LinUCBStrategyAdapter",
    "MonitorId",
    "Orchestrator",
    "PromotionDecision",
    "RiskCheckResult",
    "RiskMonitor",
    "TradeDirection",
    "build_regime_context",
    "clear_triggered",
    "compute_calibration_from_positions",
    "evaluate_promotion",
    "export_from_positions",
    "extract_predictions_outcomes",
    "is_well_calibrated_history",
    "load_triggered",
    "make_correlation_gate",
    "make_microstructure_gate",
    "report_to_dict",
    "report_to_json",
    "report_to_markdown",
    "save_triggered",
]
