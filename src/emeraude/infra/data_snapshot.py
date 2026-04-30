"""D6 Immutable OHLCV snapshots for reproducible backtests.

Doc 11 §"D6 — Data revision (Binance corrige a posteriori)" requires
that any backtest re-run uses the **same data bit-for-bit** as the
original run. Binance occasionally corrects a kline post-hoc (rare in
spot but possible after exchange rollbacks) ; without snapshots, two
runs of the "same" backtest can diverge silently because the
underlying data shifted.

This module ships :

* :class:`KlineSnapshot` — immutable record of a fetched OHLCV series
  with metadata (symbol, interval, period bounds, capture timestamp,
  content hash).
* :func:`compute_snapshot_hash` — pure function returning a
  deterministic SHA-256 over a canonical kline representation (pipe-
  separated Decimal-as-string fields). Independent of JSON
  serialization quirks (whitespace, key ordering, etc.).
* :func:`save_snapshot` / :func:`load_snapshot` — JSONL file format
  (header JSON on line 1, then one Binance-positional kline per line).
  Load verifies the hash on read ; corrupted files raise
  :class:`SnapshotIntegrityError`.

The hash is computed on the **kline content** itself, NOT on the
on-disk byte stream. That way the file format can evolve (different
JSON formatting, extra header fields) without breaking hash
verification of older snapshots.

Out of scope for this iter (cf. R2) :

* Wiring into the live data_ingestion path — the orchestrator will
  call :func:`save_snapshot` once the engine of backtest is wired in
  a later iter.
* :func:`paths.data_snapshots_dir` helper — caller decides where the
  file lives. Adding the helper is trivial when needed.
* Compressed format (gzip, zstd) — current series are small enough
  (~100 klines x ~120 bytes each = ~12 KB) that compression is not
  worth the complexity.

References :

* Doc 11 §D6 — "2 runs successifs du même backtest -> résultats
  identiques au cent près".
* SHA-256 (FIPS 180-4) — collision-resistant for our purpose
  (validation, not adversarial integrity).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypedDict

from emeraude.infra.market_data import Kline

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class _SnapshotHeader(TypedDict):
    """Strongly-typed view of the parsed JSON header.

    Used internally by :func:`_parse_header` to give mypy enough
    information to type-check the :class:`KlineSnapshot` construction
    in :func:`load_snapshot`.
    """

    version: int
    symbol: str
    interval: str
    period_start_ms: int
    period_end_ms: int
    captured_at_ms: int
    n_klines: int
    content_hash: str


#: Schema version embedded in the snapshot header. Bumped only when
#: payload semantics change in a way that older readers should opt
#: into explicitly.
SNAPSHOT_FORMAT_VERSION: Final[int] = 1

#: Number of fields per kline body line (Binance-positional minus
#: the unused quote_volume / taker_volume fields). Used to validate
#: the body shape on load.
_EXPECTED_KLINE_FIELDS: Final[int] = 8

#: Hash algorithm name. ``sha256`` is in stdlib, fast on every
#: target (Android included), and collision-resistant for our
#: validation use case (we are not defending against adversaries
#: who can choose two different snapshots with the same hash).
_HASH_ALGORITHM: Final[str] = "sha256"

#: Prefix used in the stored hash string. ``"sha256:"`` mirrors the
#: convention used elsewhere in the codebase
#: (e.g. ``audit_log`` data_snapshot_hash field per doc 11 §5).
_HASH_PREFIX: Final[str] = f"{_HASH_ALGORITHM}:"


# ─── Errors ────────────────────────────────────────────────────────────────


class SnapshotFormatError(ValueError):
    """Raised when a snapshot file cannot be parsed structurally.

    Examples : bad JSON, missing required header field, malformed
    kline row. Distinct from :class:`SnapshotIntegrityError` so
    callers can distinguish "file is structurally broken" from
    "file parsed but content does not match its declared hash".
    """


class SnapshotIntegrityError(RuntimeError):
    """Raised when the recomputed hash differs from the declared hash.

    Always indicates either tampering or silent data revision (e.g.
    Binance corrected a bar). Callers MUST treat this as a hard
    error and refuse to use the snapshot.
    """


# ─── Snapshot dataclass ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class KlineSnapshot:
    """Immutable record of a fetched OHLCV series with metadata.

    Attributes:
        symbol: trading pair (e.g. ``"BTCUSDT"``).
        interval: Binance kline interval string (e.g. ``"1h"``,
            ``"5m"``, ``"1d"``).
        period_start_ms: epoch ms of the first bar's ``open_time``.
            Stored explicitly even though it could be derived from
            ``klines[0].open_time`` because the snapshot may
            represent an intentionally empty range (e.g. for testing
            or to record "we asked for X but got nothing back").
        period_end_ms: epoch ms of the last bar's ``close_time + 1``
            (exclusive end), or the requested end if klines is empty.
        klines: ordered tuple of :class:`Kline`. Empty allowed.
        captured_at_ms: epoch ms when the snapshot was created.
            Used for forensic tracing ("which version of Binance's
            data did we fetch").
        content_hash: deterministic SHA-256 over the kline content,
            in the form ``"sha256:<64 hex chars>"``. Computed by
            :func:`compute_snapshot_hash`.
    """

    symbol: str
    interval: str
    period_start_ms: int
    period_end_ms: int
    klines: tuple[Kline, ...]
    captured_at_ms: int
    content_hash: str


# ─── Pure hash function ────────────────────────────────────────────────────


def compute_snapshot_hash(klines: Iterable[Kline]) -> str:
    r"""Deterministic SHA-256 over a canonical kline representation.

    The canonical form is one pipe-separated line per kline, fields
    in order : ``open_time | open | high | low | close | volume |
    close_time | n_trades``. Decimals are stringified via
    :func:`str` (which preserves the exact Python representation —
    no trailing zeros stripped, no scientific notation injected).
    Lines are joined with ``\n`` and encoded as UTF-8.

    The on-disk JSON format is irrelevant to this hash : two
    snapshots with different JSON formatting but identical kline
    content produce the same hash. Conversely, any change to a
    kline field (even a trailing zero in a Decimal) yields a
    different hash.

    Returns:
        ``"sha256:<64 hex chars>"`` — empty input still returns a
        valid hash (the SHA-256 of an empty string).
    """
    parts: list[str] = []
    for k in klines:
        parts.append(
            f"{k.open_time}|{k.open}|{k.high}|{k.low}|{k.close}|"
            f"{k.volume}|{k.close_time}|{k.n_trades}"
        )
    canonical = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"{_HASH_PREFIX}{digest}"


# ─── Save / load ───────────────────────────────────────────────────────────


def make_snapshot(
    *,
    symbol: str,
    interval: str,
    period_start_ms: int,
    period_end_ms: int,
    klines: list[Kline],
    captured_at_ms: int,
) -> KlineSnapshot:
    """Convenience constructor that computes the content_hash for you.

    Use this in callers that have just fetched a series — pass the
    fields and get back a fully-built :class:`KlineSnapshot` with
    its hash already populated.
    """
    return KlineSnapshot(
        symbol=symbol,
        interval=interval,
        period_start_ms=period_start_ms,
        period_end_ms=period_end_ms,
        klines=tuple(klines),
        captured_at_ms=captured_at_ms,
        content_hash=compute_snapshot_hash(klines),
    )


def save_snapshot(snapshot: KlineSnapshot, path: Path) -> None:
    """Persist the snapshot as a JSONL file.

    Format :

    * Line 1 : JSON object header with version, symbol, interval,
      period bounds, capture timestamp, n_klines, content_hash.
    * Lines 2+ : one Binance-positional JSON array per kline
      (``[open_time, open_str, high_str, low_str, close_str,
      volume_str, close_time, n_trades]``). Decimals as strings to
      preserve precision.

    Atomic write : we write to ``<path>.tmp`` then rename, so a
    crash mid-write doesn't leave a half-formed file. The parent
    directory must exist (caller's responsibility).

    Args:
        snapshot: the :class:`KlineSnapshot` to persist.
        path: destination file path.

    Raises:
        OSError: on any filesystem error (parent dir missing,
            permission denied, disk full).
    """
    header = {
        "version": SNAPSHOT_FORMAT_VERSION,
        "symbol": snapshot.symbol,
        "interval": snapshot.interval,
        "period_start_ms": snapshot.period_start_ms,
        "period_end_ms": snapshot.period_end_ms,
        "captured_at_ms": snapshot.captured_at_ms,
        "n_klines": len(snapshot.klines),
        "content_hash": snapshot.content_hash,
    }
    lines: list[str] = [json.dumps(header, sort_keys=True, ensure_ascii=False)]
    for k in snapshot.klines:
        lines.append(
            json.dumps(
                [
                    k.open_time,
                    str(k.open),
                    str(k.high),
                    str(k.low),
                    str(k.close),
                    str(k.volume),
                    k.close_time,
                    k.n_trades,
                ],
                ensure_ascii=False,
            )
        )

    payload = "\n".join(lines) + "\n"

    # Atomic write : tmp + rename. Avoids leaving a half-written file
    # on disk if the process dies mid-flush.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def load_snapshot(path: Path) -> KlineSnapshot:
    """Read a snapshot file and verify its content hash.

    Args:
        path: source file path.

    Returns:
        The loaded :class:`KlineSnapshot`.

    Raises:
        FileNotFoundError: if the file does not exist (passes
            through from :meth:`Path.read_text`).
        SnapshotFormatError: on any structural problem (bad JSON,
            missing required field, wrong type, malformed kline row).
        SnapshotIntegrityError: if the recomputed hash does not match
            the hash declared in the header. The snapshot must be
            considered corrupted and rejected.
    """
    raw = path.read_text(encoding="utf-8")
    raw_lines = raw.split("\n")
    # Strip a trailing empty line introduced by the final newline of
    # ``save_snapshot`` ; do not be lenient with mid-file blanks.
    if raw_lines and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]
    if not raw_lines:
        msg = "Snapshot file is empty (no header)"
        raise SnapshotFormatError(msg)

    header = _parse_header(raw_lines[0])
    klines = tuple(_parse_kline(line, idx + 1) for idx, line in enumerate(raw_lines[1:]))

    expected_n = header["n_klines"]
    if len(klines) != expected_n:
        msg = (
            f"Snapshot header declares n_klines={expected_n} but file "
            f"contains {len(klines)} kline rows"
        )
        raise SnapshotFormatError(msg)

    declared_hash = header["content_hash"]
    actual_hash = compute_snapshot_hash(klines)
    if declared_hash != actual_hash:
        msg = (
            f"Snapshot integrity check failed : header declares "
            f"{declared_hash} but recomputed hash is {actual_hash}. "
            "The file has been modified or the underlying data was "
            "revised. Refusing to load."
        )
        raise SnapshotIntegrityError(msg)

    return KlineSnapshot(
        symbol=header["symbol"],
        interval=header["interval"],
        period_start_ms=header["period_start_ms"],
        period_end_ms=header["period_end_ms"],
        klines=klines,
        captured_at_ms=header["captured_at_ms"],
        content_hash=declared_hash,
    )


# ─── Internal parsers ──────────────────────────────────────────────────────


def _parse_header(raw: str) -> _SnapshotHeader:
    """Parse line 1 (JSON header) and validate required fields.

    Type validation is intentionally strict — the snapshot is a
    contract artefact, not a free-form input. Returns a TypedDict
    so :func:`load_snapshot` can construct the :class:`KlineSnapshot`
    without unsafe casts.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Snapshot header is not valid JSON : {exc}"
        raise SnapshotFormatError(msg) from exc

    if not isinstance(parsed, dict):
        msg = f"Snapshot header must be a JSON object, got {type(parsed).__name__}"
        raise SnapshotFormatError(msg)

    required: dict[str, type] = {
        "version": int,
        "symbol": str,
        "interval": str,
        "period_start_ms": int,
        "period_end_ms": int,
        "captured_at_ms": int,
        "n_klines": int,
        "content_hash": str,
    }
    for key, expected_type in required.items():
        if key not in parsed:
            msg = f"Snapshot header missing required field : {key!r}"
            raise SnapshotFormatError(msg)
        if not isinstance(parsed[key], expected_type):
            msg = (
                f"Snapshot header field {key!r} has type "
                f"{type(parsed[key]).__name__}, expected {expected_type.__name__}"
            )
            raise SnapshotFormatError(msg)

    version = parsed["version"]
    if version != SNAPSHOT_FORMAT_VERSION:
        msg = (
            f"Snapshot format version {version} is not supported "
            f"(this build understands version {SNAPSHOT_FORMAT_VERSION})"
        )
        raise SnapshotFormatError(msg)

    # All required keys validated above ; build the TypedDict explicitly
    # so the field types match :class:`_SnapshotHeader`.
    return _SnapshotHeader(
        version=parsed["version"],
        symbol=parsed["symbol"],
        interval=parsed["interval"],
        period_start_ms=parsed["period_start_ms"],
        period_end_ms=parsed["period_end_ms"],
        captured_at_ms=parsed["captured_at_ms"],
        n_klines=parsed["n_klines"],
        content_hash=parsed["content_hash"],
    )


def _parse_kline(raw: str, line_index: int) -> Kline:
    """Parse one body line back into a :class:`Kline`.

    The body format is the Binance-positional array we wrote in
    :func:`save_snapshot`. We re-derive the kline via
    :meth:`Kline.from_binance_array` so any future change to that
    factory propagates automatically.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Snapshot body line {line_index} is not valid JSON : {exc}"
        raise SnapshotFormatError(msg) from exc

    if not isinstance(parsed, list):
        msg = f"Snapshot body line {line_index} must be a JSON array, got {type(parsed).__name__}"
        raise SnapshotFormatError(msg)

    # ``Kline.from_binance_array`` reads positions 0,1,2,3,4,5,6,8.
    # We wrote 8 fields (positions 0-7 ; the orig Binance array has
    # 12 fields and ``n_trades`` is at position 8 — but our snapshot
    # array uses position 7 for n_trades to keep the file small).
    # Pad with placeholder strings so ``from_binance_array`` reads
    # the right indices.
    if len(parsed) != _EXPECTED_KLINE_FIELDS:
        msg = (
            f"Snapshot body line {line_index} expected "
            f"{_EXPECTED_KLINE_FIELDS} fields "
            f"(open_time, open, high, low, close, volume, close_time, n_trades), "
            f"got {len(parsed)}"
        )
        raise SnapshotFormatError(msg)

    # Build a 9-element list shaped like the Binance array so the
    # existing factory works without modification.
    binance_shaped = [
        parsed[0],  # open_time
        parsed[1],  # open
        parsed[2],  # high
        parsed[3],  # low
        parsed[4],  # close
        parsed[5],  # volume
        parsed[6],  # close_time
        "",  # quote_volume placeholder (Binance position 7, unused)
        parsed[7],  # n_trades (Binance position 8 reads from here)
    ]
    try:
        return Kline.from_binance_array(binance_shaped)
    except (ValueError, ArithmeticError, KeyError, IndexError, TypeError) as exc:
        msg = f"Snapshot body line {line_index} is not a valid kline : {exc}"
        raise SnapshotFormatError(msg) from exc
