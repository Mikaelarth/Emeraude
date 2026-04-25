"""Property-based tests for emeraude.infra.paths.

Hypothesis explores the input space to find counter-examples that hand-written
tests miss. We focus on invariants the storage layer must maintain regardless
of arbitrary (but valid) directory names supplied via the env override.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from emeraude.infra import paths

# Filename-safe segments: lowercase ASCII + digits only, length 1-20. This
# guarantees compatibility across Windows (NTFS), Linux (ext4), and Android
# (ext4/f2fs) without requiring per-OS skipping logic.
_safe_segment = st.text(
    alphabet=st.characters(
        whitelist_categories=(),
        whitelist_characters="abcdefghijklmnopqrstuvwxyz0123456789",
    ),
    min_size=1,
    max_size=20,
)


@pytest.mark.property
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(segment=_safe_segment)
def test_storage_dir_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    segment: str,
) -> None:
    """``app_storage_dir()`` is idempotent: calling N times returns the same path."""
    target = tmp_path / segment
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(target))

    first = paths.app_storage_dir()
    second = paths.app_storage_dir()
    third = paths.app_storage_dir()

    assert first == second == third
    assert target.is_dir()


@pytest.mark.property
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(segment=_safe_segment)
def test_subdirs_remain_under_storage_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    segment: str,
) -> None:
    """No subdir helper escapes the storage root, regardless of override."""
    target = tmp_path / segment
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(target))

    root = paths.app_storage_dir()

    for subdir_fn in (paths.backups_dir, paths.logs_dir, paths.audit_dir):
        d = subdir_fn()
        # Resolve both ends to canonical absolute form before comparing.
        d_resolved = d.resolve()
        root_resolved = root.resolve()
        assert root_resolved in d_resolved.parents or d_resolved == root_resolved


@pytest.mark.property
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(segment=_safe_segment)
def test_database_and_salt_filenames_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    segment: str,
) -> None:
    """File NAMES (not paths) are stable regardless of where storage lives.

    Critical invariant: if a user moves their storage dir between machines,
    the filenames for ``emeraude.db`` and the salt must remain stable so the
    bot can find its own data.
    """
    target = tmp_path / segment
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(target))

    assert paths.database_path().name == "emeraude.db"
    assert paths.salt_path().name == ".emeraude_salt"
