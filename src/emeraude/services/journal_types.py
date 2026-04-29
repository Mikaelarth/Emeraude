"""Pure data types + formatter for the Journal screen (no Kivy).

Mission UX (doc 02 §"💼 PORTFOLIO" §6 "Journal du bot") :

    montrer les décisions clés du bot — entrée, sortie, skip avec
    raison.

Le journal expose au format mobile-readable les événements stockés
dans la table ``audit_log`` (cf. :func:`emeraude.infra.audit.query_events`).

Pourquoi un module séparé du widget Kivy ?
:class:`JournalScreen` (dans :mod:`emeraude.ui.screens.journal`) est
Kivy-dépendant ; importer ses types depuis :mod:`emeraude.services`
chargerait Kivy en cascade et casserait la couche services en CI
headless (cf. iter #59 retro). Ce module héberge donc la **moitié
Kivy-free** du contrat journal :

* :class:`JournalEventRow` — une ligne formattée prête à afficher.
* :class:`JournalSnapshot` — collection ordonnée (most-recent-first).
* :class:`JournalDataSource` — Protocol consommé par l'écran.
* :func:`format_event_row` — pure function event-dict → row.
* :func:`format_payload_summary` — résumé compact des payloads.

Le widget Kivy en :mod:`emeraude.ui.screens.journal` ne fait que
consommer ces types + binder dans une `ScrollView`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Limite par défaut. 50 événements = ~3-4 écrans mobiles, lisible sans
#: rendre la liste fastidieuse au scroll.
DEFAULT_HISTORY_LIMIT: Final[int] = 50

#: Longueur max du résumé de payload affiché par événement. Au-delà,
#: troncature avec ellipsis ASCII ``...``. 80 caractères tiennent sur
#: un téléphone moderne en `FONT_SIZE_CAPTION` sans wrap.
DEFAULT_SUMMARY_MAX_LEN: Final[int] = 80

#: Ellipsis ASCII (3 dots) — pas de glyphe Unicode `…` pour rester
#: lisible sur Android Roboto sans dépendre d'un fallback de police.
_ELLIPSIS: Final[str] = "..."


# ─── Row ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JournalEventRow:
    """One formatted audit event, ready to put in a Label.

    Attributes:
        event_id: row id côté DB ``audit_log``. Stable, unique,
            utile pour cross-référencer entre écrans (Portfolio /
            Signaux / Dashboard).
        ts: epoch seconds, UTC. Conservé pour tri / filtres ; le
            widget consomme :attr:`time_label` pour l'affichage.
        event_type: type stable (cf. constantes
            ``AUDIT_*`` des modules services). Surfacé tel quel
            dans le badge de la ligne.
        time_label: ``HH:MM:SS`` UTC, formaté pour la colonne gauche.
        summary: payload aplati en ``key=value key=value`` tronqué à
            :data:`DEFAULT_SUMMARY_MAX_LEN`. Vide si payload vide.
    """

    event_id: int
    ts: int
    event_type: str
    time_label: str
    summary: str


# ─── Snapshot ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JournalSnapshot:
    """Read-only state que l'écran consomme à chaque ``refresh()``.

    Attributes:
        rows: ordered most-recent-first. Vide si aucun événement
            n'est encore dans ``audit_log`` (cold start).
        total_returned: ``len(rows)`` — facilite l'affichage d'un
            badge "X événements" sans recompter côté widget.
    """

    rows: tuple[JournalEventRow, ...]
    total_returned: int


# ─── DataSource Protocol ───────────────────────────────────────────────────


class JournalDataSource(Protocol):
    """Contract consumed by ``JournalScreen``.

    Implementations vivent côté ``services/`` (cf.
    :class:`emeraude.services.journal_data_source.QueryEventsJournalDataSource`).
    Tests passent un fake implémentant ce Protocol — pas besoin de
    populer la table ``audit_log``.
    """

    def fetch_snapshot(self) -> JournalSnapshot:
        """Snapshot frais. Appelé par ``JournalScreen.refresh``."""
        ...  # pragma: no cover  (Protocol method, never invoked)


# ─── Pure formatters ───────────────────────────────────────────────────────


def format_event_row(event: Mapping[str, Any]) -> JournalEventRow:
    """Convert an :func:`audit.query_events` dict to a :class:`JournalEventRow`.

    Pure function — no I/O, no Kivy. The input dict has the shape
    returned by :func:`emeraude.infra.audit.query_events` :
    ``{"id", "ts", "event_type", "payload", "version"}``.

    Args:
        event: one row from ``query_events()``.

    Returns:
        :class:`JournalEventRow` ready to be displayed.

    Raises:
        KeyError: if a required key is missing — surfacing the
            schema mismatch loudly is preferred to a silent
            corrupted display (anti-règle A8).
    """
    ts = int(event["ts"])
    payload = event.get("payload") or {}
    return JournalEventRow(
        event_id=int(event["id"]),
        ts=ts,
        event_type=str(event["event_type"]),
        time_label=_format_time_label(ts),
        summary=format_payload_summary(payload),
    )


def format_payload_summary(
    payload: Mapping[str, Any],
    *,
    max_len: int = DEFAULT_SUMMARY_MAX_LEN,
) -> str:
    """Flatten a payload dict to a single-line ``key=value`` string.

    Empty payloads return an empty string. Long payloads are
    truncated with the ASCII ellipsis ``...``. Order respects the
    dict iteration order (insertion order in Python 3.7+).

    Args:
        payload: dict-like mapping. Values are stringified via
            :func:`str` ; nested structures are accepted but their
            ``repr`` is opaque — fine for a one-line summary.
        max_len: hard cap on the returned string length, including
            the ellipsis. Default :data:`DEFAULT_SUMMARY_MAX_LEN`.

    Returns:
        A compact summary string. Empty when ``payload`` is empty.

    Raises:
        ValueError: on ``max_len`` smaller than the ellipsis itself
            (would be impossible to truncate meaningfully).
    """
    if max_len <= len(_ELLIPSIS):
        msg = f"max_len must be > {len(_ELLIPSIS)} (ellipsis size), got {max_len}"
        raise ValueError(msg)
    if not payload:
        return ""
    parts = [f"{k}={v}" for k, v in payload.items()]
    summary = " ".join(parts)
    if len(summary) <= max_len:
        return summary
    return summary[: max_len - len(_ELLIPSIS)] + _ELLIPSIS


def _format_time_label(ts: int) -> str:
    """``HH:MM:SS`` UTC. Fixed-width column for the journal list."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%H:%M:%S")
