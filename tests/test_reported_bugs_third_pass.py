"""Regression tests for the third review pass.

1. Deleting the project that is open in the editor removed its folder while the
   live connection kept writing to it (and opened the database a second time).
7. Reconnecting a missing image updated path/size but kept the old thumbnail, so
   the organizer and the page strip kept showing the previous picture.
8. A duplicated library entry shares its thumbnail file; removing one entry
   deleted the file the other one still used.
9. A failed save-at-exit still cleared the recovery marker, so the recovery
   offer was lost.
10. A failed autosave was only logged - the single-shot timer re-armed on the
   next edit, so a transient failure left the work unsaved.
11. An unreachable project folder (unplugged drive, offline share) was silently
   replaced with the default when the settings were loaded.
13. A hand-edited settings file could point projects/exports inside the
   application folder with no check at all.
14. "Save as" into a drive root crashed with ValueError instead of telling the
   user why it cannot work.
4. ``resequence`` with a partial list left the other pages with a negative
   number (invisible in every ordered query).
5. ``Database.vacuum()`` closed the connection it was called on.
2. Autosave kept running during the modal export (writes during the worker's
   read).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.document_service import DocumentService  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.paths import app_root  # noqa: E402
from core.project_service import ProjectService, ProjectServiceError  # noqa: E402
from core.settings_service import (  # noqa: E402
    SUPPORTED_IMAGE_EXTENSIONS,
    SettingsService,
    sanitize_settings,
)
from database.app_database import AppDatabase  # noqa: E402
from tests.conftest import make_image  # noqa: E402

pyqt_available = True
try:  # pragma: no cover - environment guard
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
except Exception:  # pragma: no cover
    pyqt_available = False


@pytest.fixture(scope="module")
def qt_app():
    if not pyqt_available:  # pragma: no cover
        pytest.skip("PySide6 is not available")
    app = QApplication.instance() or QApplication([])
    yield app


def _import(workspace, count: int = 1, color=(235, 240, 250)):
    paths = [
        make_image(
            workspace.project.folder / "sources" / f"doc_{index}.jpg",
            size=(600, 950),
            fmt="JPEG",
            color=color,
        )
        for index in range(count)
    ]
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)



# ------------------------------------------------- 1: delete the open project
class _StubMessageBox:
    """Records dialogs and answers "Yes" to every question."""

    StandardButton = QMessageBox.StandardButton
    answered: list[str] = []

    @classmethod
    def warning(cls, *args, **kwargs):
        cls.answered.append("warning")
        return QMessageBox.StandardButton.Yes

    @classmethod
    def information(cls, parent=None, title="", text="", *args, **kwargs):
        cls.answered.append(text)
        return QMessageBox.StandardButton.Ok

    @classmethod
    def question(cls, *args, **kwargs):
        cls.answered.append("question")
        return QMessageBox.StandardButton.Yes


def test_deleting_the_open_project_is_refused(qt_app, tmp_path, monkeypatch):
    from app.context import AppContext
    from app.projects_page import ProjectsPage

    settings = SettingsService(tmp_path / "ui_settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp_path / "projects"))
    app_db = AppDatabase(tmp_path / "registry.db")
    projects = ProjectService(app_db, settings)
    workspace = projects.create_project("Open Project")
    page = ProjectsPage(AppContext(settings=settings, app_db=app_db, projects=projects))
    try:
        page.set_active_workspace(workspace)
        page.refresh()
        row = page._rows.index(workspace.id)
        page.table.setCurrentCell(row, 0)
        assert page.selected_project_id() == workspace.id

        _StubMessageBox.answered.clear()
        monkeypatch.setattr("app.projects_page.QMessageBox", _StubMessageBox)
        page._delete_selected()

        folder = Path(workspace.project.folder)
        assert folder.is_dir(), "the open project folder was deleted"
        assert (folder / "project.db").is_file()
        assert app_db.get_project(workspace.id) is not None
        assert any("open in the editor" in text for text in _StubMessageBox.answered), (
            "the user was not told why nothing happened"
        )
    finally:
        page.setParent(None)
        page.deleteLater()
        workspace.close()
        app_db.close()


# ------------------------------------------------- 7: stale thumbnail
def test_reconnect_rebuilds_the_thumbnail(workspace):
    documents = _import(workspace)
    document = documents[0]
    thumbnail = Path(document.thumbnail_path)
    assert thumbnail.is_file(), "the importer must have written a thumbnail"
    before = thumbnail.read_bytes()

    replacement = make_image(
        Path(document.source_path).with_name("replacement.jpg"),
        size=(600, 950),
        fmt="JPEG",
        color=(10, 90, 200),
    )
    service = DocumentService(workspace)
    candidate = service.validate_replacement(document, replacement)
    assert service.reconnect(candidate, update_hash=True)

    stored = workspace.database.documents.get(document.id)
    assert stored.thumbnail_path, "the reconnect must keep a usable thumbnail"
    refreshed = Path(stored.thumbnail_path)
    assert refreshed.is_file()
    assert refreshed.read_bytes() != before, "the old thumbnail was kept"


def test_reconnect_without_preview_drops_the_old_thumbnail(workspace, monkeypatch):
    documents = _import(workspace)
    document = documents[0]
    stale = Path(document.thumbnail_path)
    assert stale.is_file()

    replacement = make_image(
        Path(document.source_path).with_name("broken.jpg"), size=(300, 300), fmt="JPEG"
    )
    service = DocumentService(workspace)
    candidate = service.validate_replacement(document, replacement)
    monkeypatch.setattr("core.document_service.make_preview_png", lambda *a, **k: b"")
    assert service.reconnect(candidate, update_hash=True)

    stored = workspace.database.documents.get(document.id)
    assert stored.thumbnail_path == "", "a stale thumbnail must not survive"
    assert not stale.exists()


# ------------------------------------------------- 8: shared thumbnail
def test_removing_a_duplicate_keeps_the_shared_thumbnail(workspace):
    documents = _import(workspace)
    original = documents[0]
    service = DocumentService(workspace)
    duplicate = service.duplicate_document(original.id)
    assert duplicate is not None
    assert duplicate.thumbnail_path == original.thumbnail_path
    shared = Path(original.thumbnail_path)
    assert shared.is_file()

    service.remove_documents([original.id])
    assert shared.is_file(), "the duplicate still uses this thumbnail"

    service.remove_documents([duplicate.id])
    assert not shared.exists(), "the last reference must clean the file up"


# ------------------------------------------------- 9/10/2: window behaviour
@pytest.fixture()
def window(qt_app, tmp_path):
    from app.context import AppContext
    from app.main_window import MainWindow

    settings = SettingsService(tmp_path / "win_settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp_path / "projects"))
    settings.set("general", "default_export_directory", str(tmp_path / "exports"))
    app_db = AppDatabase(tmp_path / "win_app.db")
    projects = ProjectService(app_db, settings)
    context = AppContext(settings=settings, app_db=app_db, projects=projects)
    window = MainWindow(context)
    workspace = projects.create_project("Window Project")
    window._attach_workspace(workspace)
    yield window, workspace
    window.close_project()
    window.setParent(None)
    window.deleteLater()
    app_db.close()


def test_failed_save_reports_failure_and_keeps_the_marker(window, monkeypatch):
    win, workspace = window
    markers: list[bool] = []
    monkeypatch.setattr(
        "app.main_window.write_marker",
        lambda *args, **kwargs: markers.append(True),
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    def _boom() -> None:
        raise OSError("disk is gone")

    monkeypatch.setattr(workspace.database, "checkpoint", _boom)
    win.dirty = True

    assert win.save_project(confirm=False) is False
    assert win.dirty is True, "a failed save must not clear the dirty flag"
    assert not markers, "a failed save must not clear the recovery marker"


def test_closing_after_a_failed_save_keeps_the_window_open(window, monkeypatch):
    win, workspace = window
    cleared: list[str] = []
    monkeypatch.setattr(
        "app.main_window.clear_marker", lambda *args, **kwargs: cleared.append("x")
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save),
    )

    def _boom() -> None:
        raise OSError("disk is gone")

    monkeypatch.setattr(workspace.database, "checkpoint", _boom)
    win.dirty = True

    event = QCloseEvent()
    event.accept()
    win.closeEvent(event)

    assert not event.isAccepted(), "the window closed although the save failed"
    assert not cleared, "the recovery marker was cleared after a failed save"
    assert win.workspace is not None, "the open project was dropped"


def test_autosave_failure_rearms_the_timer(window, monkeypatch):
    win, workspace = window

    def _boom() -> None:
        raise OSError("file is locked")

    monkeypatch.setattr(workspace.database, "checkpoint", _boom)
    win.dirty = True
    win.autosave_timer.stop()

    win._autosave()

    assert win.autosave_timer.isActive(), "a failed autosave was never retried"
    assert "retry" in win.status_save.text().lower()
    assert win.dirty is True


def test_export_pauses_autosave(window, monkeypatch):
    win, _workspace = window
    seen: list[bool] = []

    class _FakeDialog:
        exported_paths: list[str] = []

        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self) -> int:
            seen.append(win.autosave_timer.isActive())
            return 0

    monkeypatch.setattr("app.main_window.ExportDialog", _FakeDialog)
    win.dirty = True
    win._schedule_autosave()
    assert win.autosave_timer.isActive()

    win.export_pdf()

    assert seen == [False], "autosave kept running during the export"
    assert win.autosave_timer.isActive(), "the timer was not re-armed afterwards"



# ------------------------------------------------- 11/13: settings
def test_unreachable_project_folder_setting_is_kept():
    unreachable = "Q:/definitely-not-there/projects"
    result = sanitize_settings({"general": {"default_project_directory": unreachable}})
    # The value is normalised, not replaced: an unplugged drive or an offline
    # share must not cost the user the folder they configured.
    assert result["general"]["default_project_directory"] == str(Path(unreachable))


def test_a_file_is_still_rejected_as_a_folder(tmp_path):
    target = tmp_path / "not_a_folder.db"
    target.write_text("x", encoding="utf-8")
    result = sanitize_settings({"general": {"default_project_directory": str(target)}})
    assert result["general"]["default_project_directory"] != str(target)


def test_hand_edited_folder_inside_the_app_folder_is_ignored():
    unsafe = str(app_root() / "hand_edited_projects")
    result = sanitize_settings(
        {
            "general": {
                "default_project_directory": unsafe,
                "default_export_directory": unsafe,
            }
        }
    )
    assert result["general"]["default_project_directory"] != unsafe
    assert result["general"]["default_export_directory"] != unsafe

    allowed = sanitize_settings(
        {
            "general": {
                "default_project_directory": unsafe,
                "default_export_directory": unsafe,
                "allow_unsafe_project_folder": True,
            }
        }
    )
    assert allowed["general"]["default_project_directory"] == unsafe
    assert allowed["general"]["default_export_directory"] == unsafe


# ------------------------------------------------- 14: Save as into a root
def test_partial_folder_rejects_a_drive_root(workspace):
    root = Path(Path(workspace.project.folder).anchor)
    with pytest.raises(ProjectServiceError):
        ProjectService._partial_folder(root)


def test_duplicate_project_rejects_a_drive_root(project_service, workspace):
    root = Path(Path(workspace.project.folder).anchor)
    with pytest.raises(ProjectServiceError) as excinfo:
        project_service.duplicate_project(workspace, "Copy", folder=root)
    assert "root" in str(excinfo.value).lower()


# ------------------------------------------------- 4: resequence
def test_resequence_appends_pages_left_out(workspace):
    _import(workspace)
    from core.layout_service import LayoutService

    layout = LayoutService(workspace)
    layout.add_page()
    layout.add_page()
    project_id = workspace.project.id
    pages = workspace.database.pages.list_for_project(project_id)
    assert len(pages) >= 3
    partial = [page.id for page in reversed(pages[1:])]

    workspace.database.pages.resequence(project_id, partial)

    numbers = sorted(
        page.page_number
        for page in workspace.database.pages.list_for_project(project_id)
    )
    assert numbers == list(range(1, len(pages) + 1))


# ------------------------------------------------- 5: vacuum
def test_vacuum_keeps_the_connection_usable(workspace):
    workspace.database.db.vacuum()
    assert workspace.database.db.connection.execute("SELECT 1").fetchone()[0] == 1
