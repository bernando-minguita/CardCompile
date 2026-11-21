"""Rename migration: data written under the old application name must survive.

The application was renamed from "Image to PDF Builder" to "CardCompile".  Both
``documents_root()`` and ``user_data_dir()`` derive from that name, so without a
migration every existing project would silently disappear from the dashboard
while its folder still sat under the old name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import paths
from core.paths import (
    APP_NAME,
    LEGACY_APP_NAMES,
    LEGACY_APP_SLUGS,
    migrate_legacy_locations,
)


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Redirect ``Path.home()`` and ``%APPDATA%`` into a temporary folder."""
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("DPB_USER_DATA_DIR", raising=False)
    return home, appdata


def test_legacy_names_are_declared():
    """The old names must stay listed, or the migration silently does nothing."""
    assert "Image to PDF Builder" in LEGACY_APP_NAMES
    assert "ImageToPDFBuilder" in LEGACY_APP_SLUGS
    assert APP_NAME == "CardCompile"


def test_documents_folder_is_migrated(fake_home):
    """An old Documents folder is moved to the new name with its contents."""
    home, _appdata = fake_home
    legacy = home / "Documents" / "Image to PDF Builder"
    project = legacy / "Projects" / "Job One"
    project.mkdir(parents=True)
    (project / "project.db").write_bytes(b"db")

    migrated = migrate_legacy_locations()

    new_root = home / "Documents" / APP_NAME
    assert (new_root / "Projects" / "Job One" / "project.db").is_file()
    assert not legacy.exists(), "the old folder should be gone after migrating"
    assert any("Image to PDF Builder" in entry for entry in migrated)


def test_user_data_folder_is_migrated(fake_home):
    """The %APPDATA% registry/settings folder is moved to the new slug."""
    _home, appdata = fake_home
    legacy = appdata / "ImageToPDFBuilder"
    legacy.mkdir()
    (legacy / "settings.json").write_text("{}", encoding="utf-8")

    migrate_legacy_locations()

    assert (appdata / "CardCompile" / "settings.json").is_file()
    assert not legacy.exists()


def test_migration_merges_when_target_exists(fake_home):
    """A folder created by an earlier launch must not block the migration."""
    home, _appdata = fake_home
    legacy = home / "Documents" / "Image to PDF Builder"
    (legacy / "Projects" / "Old").mkdir(parents=True)
    (legacy / "Projects" / "Old" / "project.db").write_bytes(b"old")
    # The new folder already exists (first launch created its Projects dir).
    (home / "Documents" / APP_NAME / "Projects").mkdir(parents=True)

    migrate_legacy_locations()

    assert (home / "Documents" / APP_NAME / "Projects" / "Old" / "project.db").is_file()


def test_migration_is_idempotent(fake_home):
    """Running twice must be harmless and the second run must find nothing."""
    home, _appdata = fake_home
    legacy = home / "Documents" / "Image to PDF Builder"
    (legacy / "Projects" / "A").mkdir(parents=True)

    first = migrate_legacy_locations()
    second = migrate_legacy_locations()

    assert first, "the first run should migrate the legacy folder"
    assert second == [], "the second run has nothing left to move"


def test_migration_respects_explicit_override(fake_home, monkeypatch):
    """With DPB_USER_DATA_DIR set the caller owns the location - do not move."""
    home, _appdata = fake_home
    legacy = home / "Documents" / "Image to PDF Builder"
    legacy.mkdir(parents=True)
    monkeypatch.setenv("DPB_USER_DATA_DIR", str(home / "custom"))

    assert migrate_legacy_locations() == []
    assert legacy.exists(), "an explicit override must disable the migration"


def test_registry_paths_are_rebased(fake_home):
    """Registry rows still pointing at the old folder are repaired on open."""
    from database.app_database import AppDatabase

    home, appdata = fake_home
    # A project that was migrated: the file now lives under the new name.
    project_dir = home / "Documents" / APP_NAME / "Projects" / "Job"
    project_dir.mkdir(parents=True)
    db_file = project_dir / "project.db"
    db_file.write_bytes(b"db")

    database = AppDatabase(appdata / "app_data.db")
    try:
        with database.db.transaction() as connection:
            connection.execute(
                "INSERT INTO known_projects (id, name, db_path, project_folder, "
                "created_at, last_opened_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "p1",
                    "Job",
                    str(home / "Documents" / "Image to PDF Builder" / "Projects" / "Job" / "project.db"),
                    str(home / "Documents" / "Image to PDF Builder" / "Projects" / "Job"),
                    "2026-01-01T00:00:00",
                    "2026-01-01T00:00:00",
                ),
            )
        repaired = database.repair_legacy_paths()
        assert repaired == 1
        entry = database.get_project("p1")
        assert entry is not None
        assert Path(entry.db_path) == db_file
        assert Path(entry.project_folder) == project_dir
    finally:
        database.close()
