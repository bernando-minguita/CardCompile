"""Regression tests for the second review pass (undo/FK, durability, page cache).

A. Removing an image from the Image Library invalidated the undo history but the
   stack was never cleared, so a later Ctrl+Z re-inserted page items whose
   foreign key was gone.
B. A project.db copied without its ``-wal`` sidecar reads back as an empty
   database and used to be reported as "not a project".
C. The Projects page re-opened every database on every search keystroke.
D. "New project" accepted a folder inside the application folder without a word.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commands.page_command import capture_state, restore_state  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.layout_service import LayoutService  # noqa: E402
from core.paths import app_root  # noqa: E402
from core.project_service import (  # noqa: E402
    PROBLEM_EMPTY,
    PROBLEM_MISSING,
    ProjectService,
    ProjectServiceError,
    empty_project_message,
    project_file_problem,
)
from core.settings_service import (  # noqa: E402
    SUPPORTED_IMAGE_EXTENSIONS,
    SettingsService,
)
from database.app_database import AppDatabase  # noqa: E402
from tests.conftest import make_image  # noqa: E402


@pytest.fixture()
def layout(workspace):
    return LayoutService(workspace)


def _import(workspace, count: int = 1):
    paths = [
        make_image(
            workspace.project.folder / "sources" / f"doc_{index}.jpg",
            size=(600, 950),
            fmt="JPEG",
        )
        for index in range(count)
    ]
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)


# ------------------------------------------------------------------ Bug A: undo
def test_page_restore_skips_items_whose_image_was_removed(layout, workspace):
    """restore_state must not hit the FK after a library removal."""
    documents = _import(workspace, 2)
    page = layout.pages()[0]
    items = [
        layout.create_item(document, page, original_size=False)
        for document in documents
    ]
    layout.insert_items(items)
    snapshot = capture_state(layout)

    # Remove one library record (cascades its placement away).
    workspace.database.documents.delete(documents[0].id)

    restore_state(layout, snapshot)  # must not raise

    restored = layout.items(page.id)
    assert [item.document_id for item in restored] == [documents[1].id]


def test_project_database_uses_full_sync(workspace):
    """Committed edits must not live only in the -wal sidecar."""
    connection = workspace.database.db.connection
    value = connection.execute("PRAGMA synchronous").fetchone()[0]
    assert int(value) == 2, f"expected synchronous=FULL (2), got {value}"


def test_undo_history_is_cleared_when_the_library_changes(qt_app, workspace, tmp_path):
    """Removing a library record must drop the undo history that referenced it."""
    from app.context import AppContext
    from app.editor_workspace import EditorWorkspace

    settings = SettingsService(tmp_path / "ws_settings.json")
    settings.load()
    app_db = AppDatabase(tmp_path / "ws_registry.db")
    widget = EditorWorkspace(
        AppContext(
            settings=settings,
            app_db=app_db,
            projects=ProjectService(app_db, settings),
        )
    )
    try:
        widget.load_workspace(workspace)
        editor = widget.editor
        assert editor is not None
        documents = _import(workspace, 1)
        editor.add_documents(documents)
        assert editor.undo_stack.count() > 0

        widget._on_documents_changed()  # emitted after "Remove from Image Library"

        assert editor.undo_stack.count() == 0, "stale undo entries survived"
        editor.undo_stack.undo()  # must be a no-op, not an FK error
    finally:
        widget.close_workspace()
        widget.deleteLater()
        qt_app.processEvents()


# ------------------------------------------------------------- Bug B: diagnosis
def test_empty_sqlite_file_is_reported_as_empty(tmp_path):
    path = tmp_path / "project.db"
    sqlite3.connect(path).close()  # header only, no tables

    assert project_file_problem(path) == PROBLEM_EMPTY
    assert "project.db-wal" in empty_project_message(path)


def test_missing_file_and_foreign_database_are_distinguished(tmp_path):
    missing = tmp_path / "gone.db"
    assert project_file_problem(missing) == PROBLEM_MISSING

    foreign = tmp_path / "other.db"
    connection = sqlite3.connect(foreign)
    connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    assert project_file_problem(foreign) == "foreign"


def test_open_project_explains_an_empty_database(tmp_path):
    service = ProjectService(
        AppDatabase(tmp_path / "app.db"),
        SettingsService(tmp_path / "settings.json"),
    )
    empty = tmp_path / "empty_project" / "project.db"
    empty.parent.mkdir(parents=True)
    sqlite3.connect(empty).close()

    with pytest.raises(ProjectServiceError) as excinfo:
        service.open_project(empty)
    message = str(excinfo.value).lower()
    assert "empty" in message
    assert "project.db-wal" in message


def test_summary_reports_the_problem_kind(tmp_path):
    service = ProjectService(
        AppDatabase(tmp_path / "app2.db"),
        SettingsService(tmp_path / "settings2.json"),
    )
    empty = tmp_path / "p" / "project.db"
    empty.parent.mkdir(parents=True)
    sqlite3.connect(empty).close()

    summary = service.project_summary(empty)
    assert summary.valid is False
    assert summary.problem == PROBLEM_EMPTY

    assert service.project_summary(tmp_path / "nope.db").problem == PROBLEM_MISSING


# ------------------------------------------------------- Bug D: unsafe new folder
def test_new_project_inside_the_application_folder_is_refused(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    settings.load()
    service = ProjectService(AppDatabase(tmp_path / "app.db"), settings)
    unsafe = app_root() / "inside_app_project"

    with pytest.raises(ProjectServiceError) as excinfo:
        service.create_project("Unsafe", folder=unsafe)
    assert "application folder" in str(excinfo.value)
    assert not (unsafe / "project.db").exists()


def test_new_project_inside_the_application_folder_can_be_allowed(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    settings.load()
    settings.set("general", "allow_unsafe_project_folder", True)
    service = ProjectService(AppDatabase(tmp_path / "app.db"), settings)
    unsafe = app_root() / "allowed_project"
    import shutil

    shutil.rmtree(unsafe, ignore_errors=True)
    workspace = None
    try:
        workspace = service.create_project("Allowed", folder=unsafe)
        assert Path(workspace.project.db_path).is_file()
    finally:
        if workspace is not None:
            workspace.close()
        shutil.rmtree(unsafe, ignore_errors=True)
        assert not unsafe.exists(), "test leftovers must be removed"


# ------------------------------------------------------- Bug C: projects listing
pyqt_available = True
try:  # pragma: no cover - environment guard
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    pyqt_available = False


@pytest.fixture(scope="module")
def qt_app():
    if not pyqt_available:  # pragma: no cover
        pytest.skip("PySide6 is not available")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def projects_page(qt_app, tmp_path):
    from app.context import AppContext
    from app.projects_page import ProjectsPage

    settings = SettingsService(tmp_path / "ui_settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp_path / "projects"))
    app_db = AppDatabase(tmp_path / "registry.db")
    projects = ProjectService(app_db, settings)
    created = [projects.create_project(f"Listed {index}") for index in range(2)]
    page = ProjectsPage(AppContext(settings=settings, app_db=app_db, projects=projects))
    yield page, projects, created
    page.setParent(None)
    page.deleteLater()
    for workspace in created:
        workspace.close()


def test_projects_page_caches_summaries(projects_page):
    page, projects, created = projects_page
    page.refresh()
    first = page._summary_calls
    assert first >= len(created), "the first pass must read the databases"

    page.refresh()
    assert page._summary_calls == first, "a second refresh re-read unchanged files"

    # Touching one database invalidates only that entry.
    target = Path(created[0].project.db_path)
    target.touch()
    page.refresh()
    assert page._summary_calls == first + 1


def test_projects_page_reuses_the_open_workspace(projects_page):
    page, _projects, created = projects_page
    page.set_active_workspace(created[0])
    page.refresh()
    calls = page._summary_calls

    listed = [p for p in _projects.known_projects() if p.id == created[0].id]
    assert listed, "the created project must be registered"
    summary = page.summary_for(listed[0])
    assert summary.valid and summary.problem == ""
    assert page._summary_calls == calls, "the open project was re-opened"
    assert summary.page_count == 1


def test_projects_page_search_is_debounced(projects_page, qt_app):
    from PySide6.QtTest import QTest

    page, _projects, _created = projects_page
    page.refresh()
    calls = page._summary_calls

    page.search_edit.setText("listed 0")
    page.search_edit.setText("listed 0 ")
    assert page._summary_calls == calls, "typing re-read the databases"

    QTest.qWait(400)
    qt_app.processEvents()
    assert page.table.rowCount() == 1
    assert page._summary_calls == calls, "cached rows were read again"


