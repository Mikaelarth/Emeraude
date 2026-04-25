"""Unit tests for emeraude.infra.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from emeraude.infra import paths


@pytest.fixture
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin EMERAUDE_STORAGE_DIR to ``tmp_path`` for the test."""
    monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.unit
class TestStorageRoot:
    """Tests for ``paths.app_storage_dir`` and resolution order."""

    def test_env_override_used_when_set(self, isolated_storage: Path) -> None:
        assert paths.app_storage_dir() == isolated_storage

    def test_env_override_creates_missing_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "deep" / "nested" / "storage"
        monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(target))
        assert not target.exists()

        result = paths.app_storage_dir()

        assert result == target
        assert target.is_dir()

    def test_returns_absolute_path(self, isolated_storage: Path) -> None:
        assert paths.app_storage_dir().is_absolute()

    def test_env_override_wins_over_android(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Test isolation invariant: even if Android markers leak, the override
        # must take precedence.
        monkeypatch.setenv("ANDROID_ARGUMENT", "anything")
        monkeypatch.setenv("ANDROID_PRIVATE", "/should/not/be/used")
        monkeypatch.setenv("EMERAUDE_STORAGE_DIR", str(tmp_path))

        assert paths.app_storage_dir() == tmp_path

    def test_desktop_fallback_uses_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force HOME to tmp_path so the test does not pollute the real home.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows

        result = paths.app_storage_dir()

        assert result == tmp_path / ".emeraude"
        assert result.is_dir()


@pytest.mark.unit
class TestAndroidDetection:
    """Tests for ``paths.is_android`` and Android storage helpers."""

    def test_is_android_false_without_marker(self) -> None:
        # The autouse fixture in conftest.py already removed ANDROID_ARGUMENT.
        assert paths.is_android() is False

    def test_is_android_true_with_marker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANDROID_ARGUMENT", "anything")
        assert paths.is_android() is True

    def test_android_without_private_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANDROID_ARGUMENT", "anything")
        # ANDROID_PRIVATE intentionally absent.

        with pytest.raises(RuntimeError, match="ANDROID_PRIVATE"):
            paths.app_storage_dir()

    def test_android_with_private_env_uses_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANDROID_ARGUMENT", "anything")
        monkeypatch.setenv("ANDROID_PRIVATE", str(tmp_path))

        result = paths.app_storage_dir()

        assert result == tmp_path
        assert tmp_path.is_dir()


@pytest.mark.unit
class TestDerivedPaths:
    """Tests for paths derived from the storage root."""

    def test_database_path_is_emeraude_db(self, isolated_storage: Path) -> None:
        db = paths.database_path()
        assert db.parent == isolated_storage
        assert db.name == "emeraude.db"

    def test_salt_path_is_hidden_dotfile(self, isolated_storage: Path) -> None:
        salt = paths.salt_path()
        assert salt.parent == isolated_storage
        assert salt.name.startswith(".")
        assert "emeraude_salt" in salt.name

    def test_subdirs_created_on_access(self, isolated_storage: Path) -> None:
        for subdir_fn, expected_name in (
            (paths.backups_dir, "backups"),
            (paths.logs_dir, "logs"),
            (paths.audit_dir, "audit"),
        ):
            d = subdir_fn()
            assert d.is_dir(), f"{subdir_fn.__name__} did not create directory"
            assert d.name == expected_name
            assert d.parent == isolated_storage

    def test_subdirs_idempotent(self, isolated_storage: Path) -> None:
        # Calling twice must not raise even if the dir exists.
        first = paths.logs_dir()
        second = paths.logs_dir()
        assert first == second
        assert first.is_dir()
