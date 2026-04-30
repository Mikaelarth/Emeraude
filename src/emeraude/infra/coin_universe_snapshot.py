"""D2 Coin-universe snapshots — anti survivorship bias.

Doc 11 §"D2 — Survivorship bias" requires that any backtest starting
on date ``T`` operates on the **universe of coins that existed on T**,
not on today's top-10 (which by definition only contains the
survivors). The fix is to capture a periodic snapshot of the
investable universe and force every backtest to query
:func:`universe_at(t)` instead of "what's listed today".

This module is the sister of :mod:`emeraude.infra.data_snapshot` (D6)
and intentionally reuses the same idioms :

* Immutable :class:`CoinUniverseSnapshot` dataclass with an embedded
  SHA-256 ``content_hash``.
* Atomic ``save_universe_snapshot`` (tmp + rename).
* ``load_universe_snapshot`` recomputes the hash and raises
  :class:`SnapshotIntegrityError` on mismatch.
* :class:`SnapshotFormatError` for structural problems
  (bad JSON, missing fields, wrong types, etc.).

Both error classes are imported from :mod:`emeraude.infra.data_snapshot`
to keep the snapshot vocabulary unified across data kinds.

Out of scope for this iter (cf. R2) :

* Wiring into the live data_ingestion path — the orchestrator will
  call :func:`universe_at` once the engine of backtest is wired in
  a later iter.
* :func:`paths.coin_universe_snapshots_dir` helper — caller decides
  where the file lives (similar trade-off as iter #88).
* ``listing_date_ms`` per coin : CoinGecko's ``/coins/markets``
  endpoint does not return this field, and we don't want to fabricate
  it. The snapshot captures only what's observable at capture time
  (symbol + market-cap rank). Adding a richer field (first_listed_at)
  later requires bumping :data:`UNIVERSE_FORMAT_VERSION`.

References :

* Doc 11 §D2 — "table ``coin_universe_snapshots(date, symbols)`` avec
  une entrée par mois minimum".
* Doc 11 §D2 — "tout backtest produit un header listant les N coins
  de l'univers + leur date d'ajout".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypedDict

from emeraude.infra.data_snapshot import (
    SnapshotFormatError,
    SnapshotIntegrityError,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class _UniverseHeader(TypedDict):
    """Strongly-typed view of the parsed JSON header.

    Used internally by :func:`_parse_header` to give mypy enough
    information to type-check :class:`CoinUniverseSnapshot`
    construction in :func:`load_universe_snapshot`.
    """

    version: int
    snapshot_date_ms: int
    captured_at_ms: int
    n_entries: int
    content_hash: str


#: Schema version embedded in the universe-snapshot header. Bumped
#: only when payload semantics change in a way that older readers
#: should opt into explicitly.
UNIVERSE_FORMAT_VERSION: Final[int] = 1

#: Number of fields per body entry (Binance-positional minus the
#: unused fields). Used to validate the body shape on load.
_EXPECTED_ENTRY_FIELDS: Final[int] = 2

#: Hash algorithm + prefix, mirroring :mod:`data_snapshot`. Both
#: modules use SHA-256 ; the prefix lets the audit log distinguish
#: "what kind of snapshot this hash refers to" if we ever need to
#: trace back from a bare hash string.
_HASH_PREFIX: Final[str] = "sha256:"


# Re-export the unified error classes so callers can ``from
# coin_universe_snapshot import SnapshotFormatError`` without
# pulling :mod:`data_snapshot` directly.
__all__ = [
    "UNIVERSE_FORMAT_VERSION",
    "CoinEntry",
    "CoinUniverseSnapshot",
    "SnapshotFormatError",
    "SnapshotIntegrityError",
    "compute_universe_hash",
    "load_universe_snapshot",
    "make_universe_snapshot",
    "save_universe_snapshot",
    "universe_at",
]


# ─── Snapshot dataclasses ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CoinEntry:
    """One coin observed at snapshot capture time.

    Attributes:
        symbol: ticker (e.g. ``"BTC"``, ``"ETH"``). Stored as-is from
            the upstream feed (typically already uppercase via
            :class:`CoinMarketData.symbol`).
        market_cap_rank: 1-based ranking (``1`` = largest market cap
            at capture time). Used by callers that want a "top-N"
            slice of the universe at a historical date.
    """

    symbol: str
    market_cap_rank: int


@dataclass(frozen=True, slots=True)
class CoinUniverseSnapshot:
    """Immutable record of the investable universe at a point in time.

    Attributes:
        snapshot_date_ms: epoch ms representing the **logical** date
            the snapshot is meant to reflect (e.g. ``2024-01-01``).
            Distinct from ``captured_at_ms`` which records when the
            file was actually written ; the two can differ when
            backfilling old snapshots.
        entries: ordered tuple of :class:`CoinEntry`. Empty allowed
            (e.g. for testing or to record "we asked but got nothing
            back").
        captured_at_ms: epoch ms when the snapshot was created. Used
            for forensic tracing.
        content_hash: deterministic SHA-256 over the entries content,
            in the form ``"sha256:<64 hex chars>"``. Computed by
            :func:`compute_universe_hash`.
    """

    snapshot_date_ms: int
    entries: tuple[CoinEntry, ...]
    captured_at_ms: int
    content_hash: str


# ─── Pure hash function ────────────────────────────────────────────────────


def compute_universe_hash(entries: Iterable[CoinEntry]) -> str:
    r"""Deterministic SHA-256 over a canonical entries representation.

    The canonical form is one pipe-separated line per entry, fields
    in order : ``symbol | market_cap_rank``. Lines are joined with
    ``\n`` and encoded as UTF-8.

    The on-disk JSON format is irrelevant to this hash : two
    snapshots with different JSON formatting but identical entries
    content produce the same hash. Conversely, any change to a
    single entry yields a different hash.

    Returns:
        ``"sha256:<64 hex chars>"`` — empty input still returns a
        valid hash (the SHA-256 of an empty string).
    """
    parts: list[str] = []
    for e in entries:
        parts.append(f"{e.symbol}|{e.market_cap_rank}")
    canonical = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"{_HASH_PREFIX}{digest}"


# ─── Save / load ───────────────────────────────────────────────────────────


def make_universe_snapshot(
    *,
    snapshot_date_ms: int,
    entries: list[CoinEntry],
    captured_at_ms: int,
) -> CoinUniverseSnapshot:
    """Convenience constructor that computes the content_hash.

    Use this when building a snapshot from a freshly-fetched feed —
    pass the fields and get back a fully-built
    :class:`CoinUniverseSnapshot` with its hash already populated.
    """
    return CoinUniverseSnapshot(
        snapshot_date_ms=snapshot_date_ms,
        entries=tuple(entries),
        captured_at_ms=captured_at_ms,
        content_hash=compute_universe_hash(entries),
    )


def save_universe_snapshot(snapshot: CoinUniverseSnapshot, path: Path) -> None:
    """Persist the universe snapshot as a JSONL file.

    Format :

    * Line 1 : JSON object header with version, snapshot_date_ms,
      captured_at_ms, n_entries, content_hash.
    * Lines 2+ : one ``[symbol, market_cap_rank]`` array per entry.

    Atomic write : the file is written to ``<path>.tmp`` then
    renamed, so a crash mid-write doesn't leave a half-formed file.
    Parent directory must exist (caller's responsibility).

    Args:
        snapshot: the :class:`CoinUniverseSnapshot` to persist.
        path: destination file path.

    Raises:
        OSError: on any filesystem error (parent dir missing,
            permission denied, disk full).
    """
    header = {
        "version": UNIVERSE_FORMAT_VERSION,
        "snapshot_date_ms": snapshot.snapshot_date_ms,
        "captured_at_ms": snapshot.captured_at_ms,
        "n_entries": len(snapshot.entries),
        "content_hash": snapshot.content_hash,
    }
    lines: list[str] = [json.dumps(header, sort_keys=True, ensure_ascii=False)]
    for e in snapshot.entries:
        lines.append(json.dumps([e.symbol, e.market_cap_rank], ensure_ascii=False))

    payload = "\n".join(lines) + "\n"

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def load_universe_snapshot(path: Path) -> CoinUniverseSnapshot:
    """Read a universe-snapshot file and verify its content hash.

    Args:
        path: source file path.

    Returns:
        The loaded :class:`CoinUniverseSnapshot`.

    Raises:
        FileNotFoundError: if the file does not exist (passes
            through from :meth:`Path.read_text`).
        SnapshotFormatError: on any structural problem (bad JSON,
            missing field, wrong type, malformed entry row).
        SnapshotIntegrityError: if the recomputed hash does not match
            the hash declared in the header.
    """
    raw = path.read_text(encoding="utf-8")
    raw_lines = raw.split("\n")
    # Strip a trailing empty line introduced by the final newline of
    # ``save_universe_snapshot`` ; do not be lenient with mid-file
    # blanks.
    if raw_lines and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]
    if not raw_lines:
        msg = "Universe snapshot file is empty (no header)"
        raise SnapshotFormatError(msg)

    header = _parse_header(raw_lines[0])
    entries = tuple(_parse_entry(line, idx + 1) for idx, line in enumerate(raw_lines[1:]))

    expected_n = header["n_entries"]
    if len(entries) != expected_n:
        msg = (
            f"Universe snapshot header declares n_entries={expected_n} "
            f"but file contains {len(entries)} entry rows"
        )
        raise SnapshotFormatError(msg)

    declared_hash = header["content_hash"]
    actual_hash = compute_universe_hash(entries)
    if declared_hash != actual_hash:
        msg = (
            f"Universe snapshot integrity check failed : header declares "
            f"{declared_hash} but recomputed hash is {actual_hash}. "
            "The file has been modified or the underlying data was "
            "revised. Refusing to load."
        )
        raise SnapshotIntegrityError(msg)

    return CoinUniverseSnapshot(
        snapshot_date_ms=header["snapshot_date_ms"],
        entries=entries,
        captured_at_ms=header["captured_at_ms"],
        content_hash=declared_hash,
    )


# ─── Query : universe_at ───────────────────────────────────────────────────


def universe_at(
    snapshot_date_ms: int,
    snapshots: Iterable[CoinUniverseSnapshot],
) -> CoinUniverseSnapshot | None:
    """Return the most recent snapshot whose date is at or before ``snapshot_date_ms``.

    This is the **anti-survivorship-bias** API : a backtest that
    starts on date ``T`` calls
    ``universe_at(t_ms, all_known_snapshots)`` and gets back the
    universe as it was observed at or just before ``T`` — never a
    universe reconstructed post-hoc with the benefit of hindsight.

    Doc 11 §D2 explicit policy : "refus du backtest si l'univers
    passé n'est pas disponible (pas de reconstruction post-hoc)".
    This function returns ``None`` when nothing qualifies ; the
    caller MUST treat that as a hard error.

    Args:
        snapshot_date_ms: target date as epoch ms. The function looks
            for snapshots with ``snapshot_date_ms <= target``.
        snapshots: iterable of candidate snapshots. Order does not
            matter ; the function picks the one with the **largest**
            ``snapshot_date_ms <= target``.

    Returns:
        The qualifying snapshot, or ``None`` if no candidate is at or
        before the target date.

    Notes:
        Pure function : no I/O. Caller is responsible for loading the
        candidates (typically via :func:`load_universe_snapshot` over
        a directory of files).
    """
    best: CoinUniverseSnapshot | None = None
    for snap in snapshots:
        if snap.snapshot_date_ms > snapshot_date_ms:
            continue
        if best is None or snap.snapshot_date_ms > best.snapshot_date_ms:
            best = snap
    return best


# ─── Internal parsers ──────────────────────────────────────────────────────


def _parse_header(raw: str) -> _UniverseHeader:
    """Parse line 1 (JSON header) and validate required fields.

    Type validation is intentionally strict — the snapshot is a
    contract artefact, not a free-form input. Returns a TypedDict
    so :func:`load_universe_snapshot` can construct the
    :class:`CoinUniverseSnapshot` without unsafe casts.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Universe snapshot header is not valid JSON : {exc}"
        raise SnapshotFormatError(msg) from exc

    if not isinstance(parsed, dict):
        msg = f"Universe snapshot header must be a JSON object, got {type(parsed).__name__}"
        raise SnapshotFormatError(msg)

    required: dict[str, type] = {
        "version": int,
        "snapshot_date_ms": int,
        "captured_at_ms": int,
        "n_entries": int,
        "content_hash": str,
    }
    for key, expected_type in required.items():
        if key not in parsed:
            msg = f"Universe snapshot header missing required field : {key!r}"
            raise SnapshotFormatError(msg)
        if not isinstance(parsed[key], expected_type):
            msg = (
                f"Universe snapshot header field {key!r} has type "
                f"{type(parsed[key]).__name__}, expected {expected_type.__name__}"
            )
            raise SnapshotFormatError(msg)

    version = parsed["version"]
    if version != UNIVERSE_FORMAT_VERSION:
        msg = (
            f"Universe snapshot format version {version} is not supported "
            f"(this build understands version {UNIVERSE_FORMAT_VERSION})"
        )
        raise SnapshotFormatError(msg)

    return _UniverseHeader(
        version=parsed["version"],
        snapshot_date_ms=parsed["snapshot_date_ms"],
        captured_at_ms=parsed["captured_at_ms"],
        n_entries=parsed["n_entries"],
        content_hash=parsed["content_hash"],
    )


def _parse_entry(raw: str, line_index: int) -> CoinEntry:
    """Parse one body line back into a :class:`CoinEntry`.

    Body format : ``[symbol, market_cap_rank]``.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Universe snapshot body line {line_index} is not valid JSON : {exc}"
        raise SnapshotFormatError(msg) from exc

    if not isinstance(parsed, list):
        msg = (
            f"Universe snapshot body line {line_index} must be a JSON array, "
            f"got {type(parsed).__name__}"
        )
        raise SnapshotFormatError(msg)
    if len(parsed) != _EXPECTED_ENTRY_FIELDS:
        msg = (
            f"Universe snapshot body line {line_index} expected "
            f"{_EXPECTED_ENTRY_FIELDS} fields (symbol, market_cap_rank), "
            f"got {len(parsed)}"
        )
        raise SnapshotFormatError(msg)

    symbol_raw, rank_raw = parsed
    if not isinstance(symbol_raw, str):
        msg = (
            f"Universe snapshot body line {line_index} : symbol must be str, "
            f"got {type(symbol_raw).__name__}"
        )
        raise SnapshotFormatError(msg)
    if not isinstance(rank_raw, int) or isinstance(rank_raw, bool):
        # ``isinstance(True, int)`` is True in Python — explicitly
        # reject bool to keep the contract strict.
        msg = (
            f"Universe snapshot body line {line_index} : market_cap_rank must "
            f"be int, got {type(rank_raw).__name__}"
        )
        raise SnapshotFormatError(msg)

    return CoinEntry(symbol=symbol_raw, market_cap_rank=rank_raw)
