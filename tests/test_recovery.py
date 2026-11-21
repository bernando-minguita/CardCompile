"""Settings validation and recovery/autosave tests."""

from __future__ import annotations

import json
from pathlib import Path

from core import recovery_service
from core.project_service import ProjectService
from core.settings_service import SettingsService, default_settings, sanitize_settings


def test_defaults_when_file_missing(tmp_path):
    service = SettingsService(tmp_path / "settings.json")
    data = service.load()
    assert data["pdf"]["default_dpi"] == "High"
    assert data["layout"]["auto_overflow"] is True
    assert (tmp_path / "settings.json").is_file()


def test_corrupt_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    service = SettingsService(path)
    data = service.load()
    assert data == default_settings()
    assert (tmp_path / "settings.corrupt.json").is_file()


def test_invalid_values_are_sanitised():
    raw = {
        "general": {"recent_project_count": "many", "autosave_interval_seconds": -5},
        "import": {"thumbnail_size": 100000, "supported_extensions": "jpg"},
        "pdf": {"default_page_size": "A9", "default_orientation": "sideways"},
        "layout": {"grid_spacing_mm": 500, "default_rows": 99},
        "unknown_section": {"x": 1},
    }
    data = sanitize_settings(raw)
    assert data["general"]["recent_project_count"] == 10
    # 0 means "autosave off", so a negative value clamps to 0 - not to 10.
    assert data["general"]["autosave_interval_seconds"] == 0
    assert sanitize_settings(
        {"general": {"autosave_interval_seconds": 99999}}
    )["general"]["autosave_interval_seconds"] == 3600
    assert sanitize_settings(
        {"general": {"autosave_interval_seconds": 0}}
    )["general"]["autosave_interval_seconds"] == 0
    assert data["import"]["thumbnail_size"] == 512
    assert ".jpg" in data["import"]["supported_extensions"]
    assert data["pdf"]["default_page_size"] == "A4"
    assert data["pdf"]["default_orientation"] == "portrait"
    assert data["layout"]["grid_spacing_mm"] == 50.0
    assert data["layout"]["default_rows"] == 8
    # Unknown keys/sections are preserved so a settings file from a newer build
    # survives a load/save cycle in an older one.
    assert data["unknown_section"] == {"x": 1}
    assert sanitize_settings({"general": {"future_key": "keep"}})["general"][
        "future_key"
    ] == "keep"


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    service = SettingsService(path)
    service.load()
    service.set("pdf", "default_dpi", "Draft")
    service.set("layout", "auto_overflow", False)
    service.save()

    reloaded = SettingsService(path)
    data = reloaded.load()
    assert data["pdf"]["default_dpi"] == "Draft"
    assert data["layout"]["auto_overflow"] is False
    json.loads(path.read_text(encoding="utf-8"))


def test_recovery_marker_lifecycle(tmp_path, monkeypatch):
    marker = tmp_path / "recovery.json"
    monkeypatch.setattr(recovery_service, "marker_path", lambda: marker)
    db_file = tmp_path / "project.db"
    db_file.write_bytes(b"sqlite placeholder")

    assert recovery_service.pending_recovery() is None
    recovery_service.write_marker(str(db_file), "Demo", dirty=True)
    pending = recovery_service.pending_recovery()
    assert pending is not None
    assert pending["project_name"] == "Demo"
    assert pending["dirty"] is True

    recovery_service.clear_marker()
    assert recovery_service.pending_recovery() is None


def test_marker_cleared_when_database_disappears(tmp_path, monkeypatch):
    marker = tmp_path / "recovery.json"
    monkeypatch.setattr(recovery_service, "marker_path", lambda: marker)
    missing_db = tmp_path / "gone.db"
    marker.write_text(
        json.dumps({"db_path": str(missing_db), "project_name": "Gone"}),
        encoding="utf-8",
    )
    assert recovery_service.pending_recovery() is None
    assert not marker.exists()


def test_autosave_persists_layout(workspace, project_service):
    """Simulates what the auto-save timer does: checkpoint + project touch."""
    project_id = workspace.id
    db_path = workspace.db_path
    workspace.touch()
    workspace.database.checkpoint()
    workspace.close()

    reopened = project_service.open_project(db_path)
    try:
        assert reopened.id == project_id
        assert reopened.database.pages.count(project_id) == 1
    finally:
        reopened.close()


def test_save_as_creates_independent_copy(workspace, project_service, image_factory):
    from core.import_service import ImportService
    from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS

    source = image_factory(fmt="JPEG")
    service = ImportService(workspace)
    previews = service.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)

    copy = project_service.duplicate_project(workspace, "Copied project")
    try:
        assert copy.id != workspace.id
        assert copy.project.folder != workspace.project.folder
        assert copy.database.documents.count(copy.id) == 1
        assert workspace.database.documents.count(workspace.id) == 1

        # Modifying the copy never touches the original
        document = copy.database.documents.list_for_project(copy.id)[0]
        copy.database.documents.delete(document.id)
        assert copy.database.documents.count(copy.id) == 0
        assert workspace.database.documents.count(workspace.id) == 1
    finally:
        copy.close()
