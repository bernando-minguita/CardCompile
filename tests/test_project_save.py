"""Project persistence tests: creation, insertion, reopen, schema, recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.auto_layout_engine import AutoLayoutEngine
from core.document_service import DocumentService
from core.import_service import ImportService
from core.layout_service import LayoutService
from core.project_service import ProjectServiceError
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS
from database.connection import (
    CURRENT_SCHEMA_VERSION,
    Database,
    DatabaseError,
)
from database.project_database import ProjectDatabase
from tests.conftest import make_image


def _import(workspace, paths, category_id=None):
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    if category_id is None:
        category_id = workspace.database.categories.list_for_project(workspace.id)[0].id
    return service.commit(previews, category_id)


from core.paths import PROJECT_DB_FILENAME  # noqa: E402


def test_project_structure_created(workspace):
    project = workspace.project
    assert Path(project.project_folder).is_dir()
    assert project.db_path.is_file()
    assert project.documents_folder.is_dir()
    assert project.thumbs_folder.is_dir()
    assert workspace.database.db.schema_version() == CURRENT_SCHEMA_VERSION


def test_default_categories(workspace):
    names = [c.name for c in workspace.database.categories.list_for_project(workspace.id)]
    assert names == ["Iqama", "Driving License", "Istimara", "Passport", "Other"]


def test_first_page_is_a4_portrait(workspace):
    pages = workspace.database.pages.list_for_project(workspace.id)
    assert len(pages) == 1
    page = pages[0]
    assert (page.width_mm, page.height_mm) == (210.0, 297.0)
    assert page.margin_top == page.margin_bottom == 10.0


def test_document_insert_and_reopen(workspace, project_service, image_factory):
    source = image_factory(fmt="JPEG")
    summary = _import(workspace, [source])
    assert summary.imported_count == 1
    document_id = summary.imported_ids[0]
    db_path = workspace.db_path
    workspace.close()

    reopened = project_service.open_project(db_path)
    try:
        documents = reopened.database.documents.list_for_project(reopened.id)
        assert len(documents) == 1
        assert documents[0].id == document_id
        assert documents[0].source_path == str(source)
        assert documents[0].file_hash
    finally:
        reopened.close()


def test_layout_survives_reopen(workspace, project_service, image_factory):
    paths = [image_factory(fmt="JPEG") for _ in range(2)]
    _import(workspace, paths)
    layout = LayoutService(workspace)
    page = layout.pages()[0]
    documents = layout.documents()
    engine = AutoLayoutEngine(layout)
    engine.apply(list(documents.values()), page.id, AutoLayoutEngine.options_for_preset("2v"))

    items = layout.items(page.id)
    assert len(items) == 2
    snapshot = [(i.position_x, i.position_y, i.width, i.height) for i in items]

    db_path = workspace.db_path
    workspace.close()
    reopened = project_service.open_project(db_path)
    try:
        reloaded = LayoutService(reopened).items(page.id)
        assert [
            (i.position_x, i.position_y, i.width, i.height) for i in reloaded
        ] == snapshot
    finally:
        reopened.close()


def test_duplicate_project_name_creates_new_folder(project_service):
    first = project_service.create_project("Trip 2024")
    second = project_service.create_project("Trip 2024")
    try:
        assert first.project.folder != second.project.folder
        assert first.id != second.id
    finally:
        first.close()
        second.close()


def test_empty_name_rejected(project_service):
    with pytest.raises(ProjectServiceError):
        project_service.create_project("   ")


def test_non_empty_folder_rejected(project_service, tmp_path):
    folder = tmp_path / "busy"
    folder.mkdir()
    (folder / "existing.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(ProjectServiceError):
        project_service.create_project("Busy", folder=folder)
    assert (folder / "existing.txt").is_file()


def test_missing_image_reference_detected(workspace, image_factory):
    source = image_factory(fmt="PNG")
    _import(workspace, [source])
    source.unlink()
    service = DocumentService(workspace)
    assert len(service.missing_documents()) == 1


def test_schema_initialization_idempotent(workspace):
    db_path = workspace.db_path
    workspace.close()
    database = ProjectDatabase(db_path)
    try:
        assert database.db.schema_version() == CURRENT_SCHEMA_VERSION
        database.db.init_schema()  # running again must be a no-op
        assert database.db.schema_version() == CURRENT_SCHEMA_VERSION
    finally:
        database.close()


def test_older_schema_is_rejected_without_being_modified(tmp_path):
    from database.connection import MIN_PROJECT_SCHEMA_VERSION, PROJECT_MIGRATIONS

    database = Database(tmp_path / "old_project.db")
    database.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
    )
    database.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (5, 'earlier')"
    )

    try:
        with pytest.raises(DatabaseError, match="schema version 5 is not supported"):
            # The project schema owns the migration table and the version floor;
            # a bare init_schema() has no floor at all, so the project arguments
            # are what make this a project database.
            database.init_schema(
                migrations=PROJECT_MIGRATIONS,
                min_version=MIN_PROJECT_SCHEMA_VERSION,
            )
        assert database.schema_version() == 5
        assert not database.table_exists("projects")
    finally:
        database.close()


def test_registry_tracks_projects(project_service, workspace):
    known = project_service.recent_projects()
    assert any(item.id == workspace.id for item in known)


def test_project_summary_counts(workspace, project_service, image_factory):
    _import(workspace, [image_factory(fmt="JPEG")])
    summary = project_service.project_summary(workspace.db_path)
    assert summary.valid is True
    assert summary.document_count == 1
    assert summary.page_count == 1
    assert summary.name == "Test Project"


def test_category_assignment_persists(workspace):
    categories = workspace.database.categories.list_for_project(workspace.id)
    passport = next(c for c in categories if c.name == "Passport")
    source = make_image(workspace.project.folder / "sources" / "passport.jpg")
    _import(workspace, [source], category_id=passport.id)
    document = workspace.database.documents.list_for_project(workspace.id)[0]
    assert document.category_id == passport.id
    assert document.category_name == "Passport"


def test_failed_save_as_leaves_no_partial_project(
    project_service, workspace, image_factory, monkeypatch
):
    """A failure while re-keying the copy must remove the staging folder.

    The leftover ``<name>.partial`` held a complete copy of the project with the
    *original* project id, i.e. exactly the half-saved duplicate the staging
    folder exists to prevent.
    """
    _import(workspace, [image_factory(fmt="JPEG")])
    original = ProjectDatabase.checkpoint

    def failing_checkpoint(self):
        if ".partial" in str(self.path):
            raise RuntimeError("simulated failure while saving")
        return original(self)

    monkeypatch.setattr(ProjectDatabase, "checkpoint", failing_checkpoint)
    with pytest.raises(RuntimeError):
        project_service.duplicate_project(workspace, "Broken copy")
    monkeypatch.undo()

    leftovers = [
        path.name
        for path in workspace.project.folder.parent.iterdir()
        if ".partial" in path.name
    ]
    assert leftovers == [], f"failed Save-as left {leftovers} behind"
    names = {item.name for item in project_service.known_projects()}
    assert "Broken copy" not in names


def test_reopen_restores_every_project_property(
    project_service, workspace, image_factory
):
    """Pages, items, labels, crops, rotation and custom sizes survive a reopen."""
    import uuid

    from models.page_label import PageLabel

    _import(workspace, [image_factory(fmt="JPEG"), image_factory(fmt="PNG")])
    documents = workspace.database.documents.list_for_project(workspace.id)
    layout = LayoutService(workspace)
    first = layout.pages()[0]
    second = layout.add_page()
    second.paper_size = "Custom"
    second.orientation = "landscape"
    second.width_mm, second.height_mm = 150.0, 100.0
    second.margin_top = second.margin_bottom = 4.0
    second.margin_left = second.margin_right = 6.0
    workspace.database.pages.update(second)

    item = layout.create_item(documents[0], first)
    item.position_x, item.position_y = 21.5, 33.25
    item.rotation = 37.5
    item.crop_left, item.crop_top = 0.1, 0.05
    item.crop_right, item.crop_bottom = 0.02, 0.15
    item.fit_mode = "cover"
    layout.insert_item(item)
    layout.insert_item(layout.create_item(documents[1], second, width=55.0, height=35.0))
    workspace.database.labels.insert(
        PageLabel(
            id=str(uuid.uuid4()),
            page_id=second.id,
            text="Persisted label",
            position_x=12.0,
            position_y=20.0,
        )
    )

    expected = {
        "documents": [
            doc.to_dict()
            for doc in workspace.database.documents.list_for_project(workspace.id)
        ],
        "pages": [
            page.to_dict()
            for page in workspace.database.pages.list_for_project(workspace.id)
        ],
        "items": [
            row.to_dict()
            for row in workspace.database.items.list_for_project(workspace.id)
        ],
        "labels": [
            row.to_dict()
            for row in workspace.database.labels.list_for_project(workspace.id)
        ],
        "project": workspace.project.to_dict(),
    }
    db_path = workspace.db_path
    workspace.close()

    reopened = project_service.open_project(db_path)
    try:
        actual = {
            "documents": [
                doc.to_dict()
                for doc in reopened.database.documents.list_for_project(reopened.id)
            ],
            "pages": [
                page.to_dict()
                for page in reopened.database.pages.list_for_project(reopened.id)
            ],
            "items": [
                row.to_dict()
                for row in reopened.database.items.list_for_project(reopened.id)
            ],
            "labels": [
                row.to_dict()
                for row in reopened.database.labels.list_for_project(reopened.id)
            ],
            "project": reopened.project.to_dict(),
        }
        for key, value in expected.items():
            assert actual[key] == value, f"{key} changed after reopening the project"
    finally:
        reopened.close()


