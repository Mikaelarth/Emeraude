"""D5 Timezone guard — defense-in-depth scanner over the source tree.

Doc 11 §"D5 — Timezone mismatch" requires *two* guard-rails :

1. **Linter** (ruff ``DTZ`` rules, already active in
   ``pyproject.toml``). Catches the issue at lint time but can be
   bypassed by ``# noqa: DTZ``.
2. **pytest scanner** (this module). Parses every ``.py`` file under
   ``src/emeraude/`` to AST and rejects calls to known naive-datetime
   constructors **regardless of inline noqa**. There is no escape
   hatch from this scan : if a forbidden pattern lands in the source
   tree the suite goes red.

Why both ? The linter is convenient (fixes shown in editor) ; the
scanner is contractual (doc 11 D5 explicitly requires "0 cycle sans
``data_quality`` field rempli", which is impossible to guarantee if
even one timestamp is naive). Defense-in-depth costs ~50 LOC and
removes a whole category of latent bug.

Forbidden constructors :

* ``datetime.now()`` without an explicit ``tz=`` argument — returns
  the local naive time, varies across machines.
* ``datetime.utcnow()`` — historically used for "UTC clock" but
  returns a NAIVE datetime. Deprecated in 3.12. The correct form is
  ``datetime.now(UTC)``.
* ``datetime.fromtimestamp(ts)`` without an explicit ``tz=`` — local
  time again. The correct form is
  ``datetime.fromtimestamp(ts, tz=UTC)``.

Patterns NOT covered by this scanner (out of scope for iter #85) :

* ``datetime.fromisoformat("...")`` on naive strings — would require
  string-content analysis. Most of our usage parses ``isoformat() + "Z"``
  or includes an offset, which yields aware datetimes ; left as a
  follow-up.
* ``datetime.combine(date, time)`` where ``time.tzinfo`` is None —
  would require call-site type inference.

If a future iter wants to widen the contract, just append entries to
:data:`_FORBIDDEN_CALLS` and add a fixture-style test below.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


# ─── Configuration ──────────────────────────────────────────────────────────


#: Project root resolved from this test file's location. The scanner
#: walks ``<root>/src/emeraude``. Tests live next door (``<root>/tests``)
#: so we go up two levels.
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_SOURCE_ROOT: Final[Path] = _PROJECT_ROOT / "src" / "emeraude"

#: Methods of :class:`datetime.datetime` that produce a naive value
#: when called with the wrong shape. Maps the method name to a callable
#: that, given the :class:`ast.Call` node, returns ``True`` if the
#: call site is naive (i.e. needs to fail the test).
_FORBIDDEN_CALLS: Final[dict[str, str]] = {
    # ``datetime.now()`` is naive when no ``tz=`` argument is provided.
    "now": "datetime.now() must pass tz=... (e.g. tz=UTC)",
    # ``datetime.utcnow()`` is *always* naive (returns the UTC time as
    # a naive value). Deprecated in 3.12. Use datetime.now(UTC) instead.
    "utcnow": "datetime.utcnow() is naive and deprecated ; use datetime.now(UTC)",
    # ``datetime.fromtimestamp(ts)`` without ``tz=`` is local-naive.
    "fromtimestamp": "datetime.fromtimestamp() must pass tz=... (e.g. tz=UTC)",
}


# ─── AST visitor ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestNoNaiveDatetime:
    """Scan every source file for forbidden naive-datetime patterns.

    Each :meth:`test_*` is parametrised over the source tree by the
    helper fixture :func:`_python_files`. The visitor below walks the
    AST and reports per-file violations with ``file:line`` precision.
    """

    def test_source_tree_has_no_naive_datetime_calls(self) -> None:
        """Single-shot scan with a per-violation aggregate report.

        We collect every violation across the tree and assert the
        list is empty. Failing one file at a time would hide siblings ;
        this format surfaces the full picture in one go.
        """
        violations = list(_scan_source_tree(_SOURCE_ROOT))
        if violations:
            formatted = "\n".join(
                f"  {path.relative_to(_PROJECT_ROOT)}:{lineno}  {message}"
                for path, lineno, message in violations
            )
            msg = (
                "Found forbidden naive-datetime calls (cf. doc 11 §D5) :\n"
                f"{formatted}\n\n"
                "Always pass tz=... (e.g. tz=UTC) to datetime.now() and "
                "datetime.fromtimestamp(). datetime.utcnow() is deprecated ; "
                "use datetime.now(UTC) instead."
            )
            raise AssertionError(msg)


# ─── Implementation ─────────────────────────────────────────────────────────


def _scan_source_tree(root: Path) -> Iterator[tuple[Path, int, str]]:
    """Yield one ``(path, lineno, message)`` per forbidden call site."""
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:  # pragma: no cover  (defensive)
            # An unreadable / un-parseable source under src/emeraude is
            # already a bigger problem than D5 ; surface it as a
            # violation so the suite goes red.
            yield path, 0, f"could not parse {path.name} : {exc}"
            continue
        yield from _visit_calls(tree, path)


def _visit_calls(tree: ast.AST, path: Path) -> Iterator[tuple[Path, int, str]]:
    """Walk the AST once, yield each forbidden call.

    We match calls of the form ``<X>.<method>(...)`` where ``<method>``
    is one of :data:`_FORBIDDEN_CALLS` and the call lacks a ``tz=``
    keyword **and** lacks a positional argument that could be a tz
    (heuristic : 2nd positional for ``fromtimestamp``, 1st for
    ``now``). A keyword argument ``tz=...`` always exempts the call.

    The visitor does **not** check that ``<X>`` resolves to
    ``datetime.datetime`` — same-named methods on other classes
    (e.g. ``time.fromtimestamp``) don't exist in stdlib so the false-
    positive surface is empty in practice. Custom user classes
    re-using the names would trigger false positives, which we
    consider acceptable (rename / fix call site).
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        method = func.attr
        if method not in _FORBIDDEN_CALLS:
            continue
        if _has_explicit_tz(node, method):
            continue
        yield path, node.lineno, _FORBIDDEN_CALLS[method]


def _has_explicit_tz(call: ast.Call, method: str) -> bool:
    """Return True if the call passes a tz argument.

    Two acceptance paths :

    * ``tz=...`` keyword (always wins).
    * Positional ``tz`` per :func:`datetime.datetime.<method>` signature.
      ``now`` accepts a single positional ; ``fromtimestamp`` accepts
      ``tz`` as the second positional after ``timestamp``. ``utcnow``
      takes no argument that could fix it — always naive.
    """
    if any(kw.arg == "tz" for kw in call.keywords):
        return True
    if method == "now" and len(call.args) >= 1:
        return True
    return method == "fromtimestamp" and len(call.args) >= 2


# ─── Self-tests for the scanner ────────────────────────────────────────────


@pytest.mark.unit
class TestScannerImplementation:
    """Exercise :func:`_visit_calls` on hand-crafted snippets so we
    are confident the scanner accepts the legal forms and rejects the
    illegal ones.
    """

    def _scan(self, source: str) -> list[tuple[int, str]]:
        tree = ast.parse(source)
        return [(lineno, msg) for _path, lineno, msg in _visit_calls(tree, Path("x.py"))]

    def test_naive_now_flagged(self) -> None:
        # ``datetime.now()`` with no argument is naive.
        assert self._scan("import datetime\ndatetime.now()") == [(2, _FORBIDDEN_CALLS["now"])]

    def test_now_with_tz_kwarg_ok(self) -> None:
        assert self._scan("import datetime\ndatetime.now(tz=datetime.UTC)") == []

    def test_now_with_positional_tz_ok(self) -> None:
        # ``datetime.now(UTC)`` (positional) is valid per stdlib signature.
        assert self._scan("import datetime\ndatetime.now(UTC)") == []

    def test_utcnow_always_flagged(self) -> None:
        # ``utcnow`` is naive even with no possible argument.
        assert self._scan("import datetime\ndatetime.utcnow()") == [(2, _FORBIDDEN_CALLS["utcnow"])]

    def test_fromtimestamp_without_tz_flagged(self) -> None:
        assert self._scan("import datetime\ndatetime.fromtimestamp(123)") == [
            (2, _FORBIDDEN_CALLS["fromtimestamp"])
        ]

    def test_fromtimestamp_with_tz_kwarg_ok(self) -> None:
        assert self._scan("import datetime\ndatetime.fromtimestamp(123, tz=datetime.UTC)") == []

    def test_fromtimestamp_with_positional_tz_ok(self) -> None:
        assert self._scan("import datetime\ndatetime.fromtimestamp(123, datetime.UTC)") == []

    def test_unrelated_method_not_flagged(self) -> None:
        # ``some.other_method()`` is irrelevant — we match by attr name only.
        assert self._scan("foo.bar()") == []

    def test_module_level_not_flagged(self) -> None:
        # ``time.now`` doesn't exist in stdlib, but we still match by
        # attr name. This documents the false-positive surface : if a
        # user class re-uses ``now``, ``utcnow``, or ``fromtimestamp``
        # as method names, the scanner will flag them. We accept this
        # trade-off — the search-and-replace cost on rename is trivial
        # and we never re-use these names anywhere in emeraude.
        assert self._scan("custom.fromtimestamp(123)") == [(1, _FORBIDDEN_CALLS["fromtimestamp"])]

    def test_multiple_violations_all_yielded(self) -> None:
        source = "import datetime\ndatetime.now()\ndatetime.utcnow()\ndatetime.fromtimestamp(0)\n"
        violations = self._scan(source)
        assert len(violations) == 3
        assert {v[1] for v in violations} == set(_FORBIDDEN_CALLS.values())
