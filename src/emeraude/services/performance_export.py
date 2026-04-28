"""Performance report export (doc 10 R12 wiring).

Doc 10 §"R12 — Reporting operationnel" delivers
:class:`emeraude.agent.learning.performance_report.PerformanceReport`
— 12 fields covering n_trades, win_rate, expectancy, avg win/loss,
profit factor, Sharpe, Sortino, Calmar and max drawdown. This module
is the **bridge** that serialises that report into wire formats :

* :func:`report_to_dict` — JSON-friendly mapping with Decimal
  values stringified to preserve precision (no float coercion).
* :func:`report_to_json` — UTF-8 JSON string built from the dict.
* :func:`report_to_markdown` — human-readable table for the audit
  UI / CLI / Telegram reports.
* :func:`export_from_positions` — convenience that chains
  :func:`compute_performance_report` + :func:`report_to_dict` so a
  caller can go from ``tracker.history()`` to a wire payload in one
  call.

This service is **pure** — no state, no side effects, no I/O. Just
shapes data. Useful for :

* The future Kivy "IA / Apprentissage" screen that needs JSON to
  feed a chart.
* Telegram notifications that send Markdown summaries.
* CLI debug commands that print a report.
* Audit log entries that capture the cycle's performance snapshot.

Decimal handling :

* All :class:`Decimal` values are stringified via ``str(x)``. This
  preserves full precision and sidesteps ``json``'s lack of
  ``Infinity`` / ``NaN`` support : ``Decimal("Infinity")`` becomes
  the string ``"Infinity"`` in JSON, which the consumer can parse
  back with :class:`Decimal` losslessly.
* The Markdown formatter rounds for display (4 decimals on R-units,
  2 decimals on percentages) but the dict / JSON paths preserve the
  raw Decimal precision.

Anti-règle A1 : no new persistence, no scheduling, no side-effecting
write. The doc 06 I12 critère ("dashboard performance lisible
≤ 5 s") will be measured later when the UI Kivy consumes this
output ; today's iter only ships the export.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

from emeraude.agent.learning.performance_report import (
    PerformanceReport,
    compute_performance_report,
)

if TYPE_CHECKING:
    from emeraude.agent.execution.position_tracker import Position


# ─── Pure transformers ─────────────────────────────────────────────────────


def report_to_dict(report: PerformanceReport) -> dict[str, str | int]:
    """JSON-friendly mapping of a :class:`PerformanceReport`.

    Decimal values are stringified (full precision preserved) ;
    integer counts (``n_trades``, ``n_wins``, ``n_losses``) stay as
    Python ``int``. The schema mirrors the dataclass field names
    one-to-one so downstream consumers can map both directions
    without a translation table.

    Args:
        report: from
            :func:`emeraude.agent.learning.performance_report.compute_performance_report`.

    Returns:
        ``dict[str, str | int]`` ready for ``json.dumps`` (or any
        other JSON-compatible serialiser).
    """
    return {
        "n_trades": report.n_trades,
        "n_wins": report.n_wins,
        "n_losses": report.n_losses,
        "win_rate": str(report.win_rate),
        "expectancy": str(report.expectancy),
        "avg_win": str(report.avg_win),
        "avg_loss": str(report.avg_loss),
        "profit_factor": str(report.profit_factor),
        "sharpe_ratio": str(report.sharpe_ratio),
        "sortino_ratio": str(report.sortino_ratio),
        "calmar_ratio": str(report.calmar_ratio),
        "max_drawdown": str(report.max_drawdown),
    }


def report_to_json(report: PerformanceReport, *, indent: int | None = None) -> str:
    """Serialise a :class:`PerformanceReport` to a JSON string.

    Args:
        report: from :func:`compute_performance_report`.
        indent: forwarded to :func:`json.dumps`. ``None`` (default)
            yields a compact one-line payload ; pass ``2`` for a
            human-readable indented form.

    Returns:
        UTF-8 JSON string. Decimal values are quoted strings so
        ``Decimal("Infinity")`` round-trips losslessly.
    """
    return json.dumps(report_to_dict(report), indent=indent, ensure_ascii=False)


def report_to_markdown(report: PerformanceReport) -> str:
    """Render a human-readable Markdown table of the 12 metrics.

    The output is suitable for Telegram messages, audit-log payload
    snippets, or a CLI diagnostic dump. Rounds Decimal values to a
    sensible display precision : 2 decimals on percentages
    (``win_rate``), 4 decimals on R-units everywhere else.

    Empty reports (``n_trades == 0``) render a single-line "No
    trades yet" message rather than a table of zeros.

    Args:
        report: from :func:`compute_performance_report`.

    Returns:
        Markdown text (LF line endings) with a leading ``# Performance``
        heading and a 2-column metric / value table.
    """
    if report.n_trades == 0:
        return "# Performance report\n\nNo trades yet.\n"

    win_rate_pct = (report.win_rate * Decimal("100")).quantize(Decimal("0.01"))
    rows = [
        ("Trades", str(report.n_trades)),
        ("Wins", str(report.n_wins)),
        ("Losses", str(report.n_losses)),
        ("Win rate", f"{win_rate_pct} %"),
        ("Expectancy", f"{_fmt_r(report.expectancy)} R"),
        ("Avg win", f"{_fmt_r(report.avg_win)} R"),
        ("Avg loss", f"{_fmt_r(report.avg_loss)} R"),
        ("Profit factor", _fmt_r(report.profit_factor)),
        ("Sharpe (per-trade)", _fmt_r(report.sharpe_ratio)),
        ("Sortino (per-trade)", _fmt_r(report.sortino_ratio)),
        ("Calmar", _fmt_r(report.calmar_ratio)),
        ("Max drawdown", f"{_fmt_r(report.max_drawdown)} R"),
    ]

    lines: list[str] = [
        f"# Performance report (n={report.n_trades} trades)",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    lines.extend(f"| {label} | {value} |" for label, value in rows)
    lines.append("")  # trailing newline
    return "\n".join(lines)


def export_from_positions(
    positions: list[Position],
) -> dict[str, str | int]:
    """One-shot helper : positions -> JSON-friendly dict.

    Equivalent to ``report_to_dict(compute_performance_report(positions))``,
    surfaced for convenience on the call site that goes straight
    from ``tracker.history()`` to a wire payload.

    Args:
        positions: closed-position records (open ones are filtered
            by the underlying primitive).

    Returns:
        :func:`report_to_dict` output.
    """
    return report_to_dict(compute_performance_report(positions))


# ─── Internal helpers ──────────────────────────────────────────────────────


def _fmt_r(value: Decimal) -> str:
    """Display a Decimal R-multiple with 4-decimal precision.

    ``Decimal("Infinity")`` and ``Decimal("-Infinity")`` are surfaced
    as the literal strings ``"Infinity"`` / ``"-Infinity"`` so the
    Markdown stays readable in degenerate cases (no losses ->
    profit_factor = Infinity, monotonic curve -> Calmar = Infinity).
    """
    if value.is_infinite():
        return "Infinity" if value > Decimal("0") else "-Infinity"
    return f"{value.quantize(Decimal('0.0001'))}"
