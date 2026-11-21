"""Regression tests for the data-integrity audit (1.3.3).

Each test reproduces a defect that was found in `database/`, `models/` or the
project/settings services and now guards the fix.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import recovery_service  # noqa: E402
from core.document_service import DocumentService  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.project_service import (  # noqa: E402
    ProjectServiceError,
    sanitize_folder_name,
)
from core.settings_service import (  # noqa: E402
    SUPPORTED_IMAGE_EXTENSIONS,
    SettingsService,
    sanitize_settings,
)
from database.connection import Database, DatabaseError  # noqa: E402
from database.project_database import ProjectDatabase  # noqa: E402
from database.repositories.document_repository import DocumentRepository  # noqa: E402
from models.pdf_page import PdfPage  # noqa: E402
from tests.conftest import make_image  # noqa: E402


def _import_one(workspace, name: str = "scan.jpg", size=(320, 240)):
    source = make_image(
        workspace.project.folder.parent / "sources" / name, size, "JPEG", text=name
    )
    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)[0]


# --------------------------------------------------------------- project delete
def test_delete_project_keeps_unrelated_files(project_service, workspace):
    """Deleting a project must not wipe the folder it happens to live in."""
    folder = workspace.project.folder
    note = folder / "user notes.txt"
    note.write_text("keep me", encoding="utf-8")

    project_service.delete_project_files(workspace)

    assert not (folder / "project.db").exists()
    assert not (folder / "thumbnails").exists()
    assert note.is_file(), "an unrelated file was deleted with the project"


def test_delete_project_refuses_when_other_projects_live_below(
    project_service, settings, tmp_path
):
    shared = tmp_path / "shared"
    shared.mkdir()
    outer = project_service.create_project("Outer", folder=shared)
    inner = project_service.create_project("Inner", folder=shared / "Inner")
    try:
        assert inner.project.db_path.is_file()
        with pytest.raises(ProjectServiceError):
            project_service.delete_project_files(outer)
        assert inner.project.db_path.is_file()
        assert outer.project.db_path.is_file()
    finally:
        outer.close()
        inner.close()


def test_delete_project_refuses_a_folder_without_project_db(project_service, tmp_path):
    empty = tmp_path / "not a project"
    empty.mkdir()
    workspace = project_service.create_project("Temporary", folder=empty)
    workspace.close()
    (empty / "project.db").unlink()

    with pytest.raises(ProjectServiceError):
        project_service.delete_project_files(workspace)


def test_failed_creation_leaves_nothing_behind(project_service, tmp_path, monkeypatch):
    from database.repositories.page_repository import PageRepository

    before = set(project_service.default_project_folder().iterdir())

    def boom(self, page):
        raise RuntimeError("disk full")

    monkeypatch.setattr(PageRepository, "insert", boom)
    with pytest.raises(RuntimeError):
        project_service.create_project("Broken project")

    leftovers = set(project_service.default_project_folder().iterdir()) - before
    assert not leftovers, f"half-built project left on disk: {leftovers}"


# ----------------------------------------------------------------- Save as
def test_save_as_repoints_thumbnails(project_service, workspace):
    document = _import_one(workspace)
    assert Path(document.thumbnail_path).is_file()

    copy = project_service.duplicate_project(workspace, "Copied project")
    try:
        copied = copy.database.documents.list_for_project(copy.id)[0]
        thumbnail = Path(copied.thumbnail_path)
        assert thumbnail.is_file()
        assert copy.project.folder in thumbnail.parents, (
            "the copy still points its thumbnails at the original folder"
        )
        assert copy.id != workspace.id
        # the original is untouched and the original scan is still referenced
        assert Path(document.thumbnail_path).is_file()
        assert Path(copied.source_path) == Path(document.source_path)
    finally:
        copy.close()


def test_save_as_leaves_no_partial_folder(project_service, workspace):
    document = _import_one(workspace)
    copy = project_service.duplicate_project(workspace, "Another copy")
    try:
        siblings = [p.name for p in copy.project.folder.parent.iterdir()]
        assert not any(name.endswith(".partial") for name in siblings)
        assert (copy.project.folder / "thumbnails").is_dir()
        assert Path(
            copy.database.documents.list_for_project(copy.id)[0].thumbnail_path
        ).is_file()
    finally:
        copy.close()


def test_relink_moves_thumbnail_paths(project_service, workspace, tmp_path):
    _import_one(workspace)
    old_folder = workspace.project.folder
    workspace.close()
    moved = tmp_path / "moved project"
    shutil.move(str(old_folder), str(moved))

    reopened = project_service.relink_project(workspace.id, moved / "project.db")
    try:
        document = reopened.database.documents.list_for_project(reopened.id)[0]
        thumbnail = Path(document.thumbnail_path)
        assert reopened.project.folder in thumbnail.parents
        assert thumbnail.is_file()
    finally:
        reopened.close()


# ------------------------------------------------------------ import atomicity
def test_failed_import_writes_nothing(workspace, monkeypatch):
    sources = [
        make_image(
            workspace.project.folder.parent / "batch" / f"p{index}.jpg",
            (200, 150),
            "JPEG",
            text=f"P{index}",
        )
        for index in range(3)
    ]
    importer = ImportService(workspace)
    previews = importer.analyze(sources, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]

    def boom(self, documents):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(DocumentRepository, "insert_many", boom)
    with pytest.raises(RuntimeError):
        importer.commit(previews, category.id)

    assert workspace.database.documents.list_for_project(workspace.id) == []
    orphans = list(workspace.project.thumbs_folder.glob("*.jpg"))
    assert orphans == [], f"orphan thumbnails left behind: {orphans}"


def test_removing_a_document_deletes_its_thumbnail(workspace):
    document = _import_one(workspace)
    thumbnail = Path(document.thumbnail_path)
    assert thumbnail.is_file()
    original = Path(document.source_path)

    removed = DocumentService(workspace).remove_documents([document.id])

    assert removed == 1
    assert not thumbnail.exists()
    assert original.is_file(), "the original scan must never be deleted"


# ---------------------------------------------------- transactions / the DB layer
def test_nested_transaction_rolls_back_inner_work(tmp_path):
    """A failed inner block must not be committed by the outer transaction."""
    database = Database(tmp_path / "tx.db")
    database.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, note TEXT)")
    try:
        with database.transaction() as connection:
            connection.execute("INSERT INTO probe (note) VALUES ('outer')")
            with pytest.raises(RuntimeError):
                with database.transaction() as inner:
                    inner.execute("INSERT INTO probe (note) VALUES ('inner')")
                    raise RuntimeError("inner failure")
            connection.execute("INSERT INTO probe (note) VALUES ('outer-2')")
        rows = [row["note"] for row in database.query_all("SELECT note FROM probe")]
        assert rows == ["outer", "outer-2"]
    finally:
        database.close()


def test_outer_transaction_rollback_removes_everything(tmp_path):
    database = Database(tmp_path / "tx2.db")
    database.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, note TEXT)")
    try:
        with pytest.raises(RuntimeError):
            with database.transaction() as connection:
                connection.execute("INSERT INTO probe (note) VALUES ('x')")
                raise RuntimeError("boom")
        assert database.query_all("SELECT note FROM probe") == []
    finally:
        database.close()


def test_project_database_closes_when_schema_init_fails(tmp_path, monkeypatch):
    """A failed schema initialization must not leave the file locked on Windows."""
    target = tmp_path / "broken" / "project.db"

    def boom(self, schema_sql=None, schema_version=None, **kwargs):
        self.connection  # open the handle, then fail
        raise RuntimeError("migration failed")

    monkeypatch.setattr(Database, "init_schema", boom)
    with pytest.raises(RuntimeError):
        ProjectDatabase(target)

    monkeypatch.undo()
    target.unlink()  # would raise WinError 32 if the handle leaked
    assert not target.exists()


def test_batch_geometry_save_is_all_or_nothing(workspace):
    """A failure halfway through saving placements must not keep the earlier rows."""
    from core.layout_service import LayoutService

    first = _import_one(workspace, "first.jpg")
    second = _import_one(workspace, "second.jpg")
    layout = LayoutService(workspace)
    page = layout.pages()[0]
    good = layout.insert_item(layout.create_item(first, page, width=40, height=30))
    bad = layout.insert_item(layout.create_item(second, page, width=40, height=30))
    original_x = good.position_x
    good.position_x = 12.0
    bad.document_id = "ghost-document"  # violates the foreign key

    with pytest.raises((DatabaseError, Exception)):
        layout.save_items([good, bad])

    assert workspace.database.items.get(good.id).position_x == original_x


def test_two_projects_do_not_leak_into_each_other(
    project_service, workspace, image_factory, tmp_path
):
    from core.layout_service import LayoutService

    first = _import_one(workspace)
    layout = LayoutService(workspace)
    page = layout.pages()[0]
    layout.insert_item(layout.create_item(first, page, width=40, height=30))
    first_id, first_path = workspace.id, workspace.db_path
    workspace.close()

    second = project_service.create_project("Second", folder=tmp_path / "second")
    try:
        assert second.id != first_id
        assert second.database.documents.count(second.id) == 0
        assert second.database.pages.count(second.id) == 1
        page_id = second.database.pages.list_for_project(second.id)[0].id
        assert second.database.items.list_for_page(page_id) == []
        assert second.database.labels.list_for_project(second.id) == []
    finally:
        second.close()

    reopened = project_service.open_project(first_path)
    try:
        assert reopened.id == first_id
        assert reopened.database.documents.count(first_id) == 1
        assert len(reopened.database.items.list_for_page(page.id)) == 1
        assert all(doc.project_id == first_id for doc in reopened.database.documents.list_for_project(first_id))
    finally:
        reopened.close()


def test_recovery_reopen_does_not_duplicate_pages_or_items(
    project_service, workspace, image_factory, tmp_path, monkeypatch
):
    """Opening a project from the recovery marker must load it, not replay it."""
    from core.layout_service import LayoutService

    first = _import_one(workspace)
    layout = LayoutService(workspace)
    page = layout.pages()[0]
    layout.insert_item(layout.create_item(first, page, width=40, height=30))
    expected_pages = [p.to_dict() for p in workspace.database.pages.list_for_project(workspace.id)]
    expected_items = [i.to_dict() for i in workspace.database.items.list_for_project(workspace.id)]
    db_path = workspace.db_path
    workspace.close()

    marker = tmp_path / "recovery.json"
    monkeypatch.setattr(recovery_service, "marker_path", lambda: marker)
    recovery_service.write_marker(str(db_path), "Demo", dirty=True)
    pending = recovery_service.pending_recovery()
    assert pending is not None

    for _attempt in range(2):
        recovered = project_service.open_project(Path(pending["db_path"]))
        try:
            pages = [p.to_dict() for p in recovered.database.pages.list_for_project(recovered.id)]
            items = [i.to_dict() for i in recovered.database.items.list_for_project(recovered.id)]
            assert pages == expected_pages
            assert items == expected_items
        finally:
            recovered.close()


def test_page_numbers_are_unique(workspace):
    import sqlite3

    project_id = workspace.id
    workspace.database.pages.insert(
        PdfPage(id="page-a", project_id=project_id, page_number=7)
    )
    with pytest.raises((DatabaseError, sqlite3.IntegrityError)):
        workspace.database.pages.insert(
            PdfPage(id="page-b", project_id=project_id, page_number=7)
        )


def test_resequence_can_reverse_the_order(workspace):
    project_id = workspace.id
    workspace.database.pages.delete_all(project_id)
    for index, page_id in enumerate(("p1", "p2", "p3"), start=1):
        workspace.database.pages.insert(
            PdfPage(id=page_id, project_id=project_id, page_number=index)
        )
    workspace.database.pages.resequence(project_id, ["p3", "p2", "p1"])
    numbers = {
        page.id: page.page_number
        for page in workspace.database.pages.list_for_project(project_id)
    }
    assert numbers["p3"] < numbers["p2"] < numbers["p1"]
    assert sorted(numbers.values()) == [1, 2, 3]


def test_update_many_persists_document_id(workspace):
    from models.page_item import PageItem

    first = _import_one(workspace, "first.jpg")
    second = _import_one(workspace, "second.jpg")
    page = workspace.database.pages.list_for_project(workspace.id)[0]
    item = PageItem(id="item-1", page_id=page.id, document_id=first.id)
    workspace.database.items.insert(item)

    item.document_id = second.id
    workspace.database.items.update_many([item])

    assert workspace.database.items.get(item.id).document_id == second.id


# ------------------------------------------------------------------- settings
def test_settings_validation_does_not_create_folders(tmp_path):
    ghost = tmp_path / "never" / "created" / "folder"
    data = sanitize_settings({"general": {"default_export_directory": str(ghost)}})
    assert not ghost.exists(), "loading the settings created a folder"
    # A folder that is simply not there yet is *kept*: resetting it to the default
    # silently discarded an unplugged drive or an offline network share.
    assert data["general"]["default_export_directory"] == str(ghost)


def test_settings_reject_a_file_path_and_keep_a_real_folder(tmp_path):
    target = make_image(tmp_path / "image.jpg", (40, 30), "JPEG")
    data = sanitize_settings({"general": {"default_export_directory": str(target)}})
    assert data["general"]["default_export_directory"] != str(target)

    folder = tmp_path / "exports"
    folder.mkdir()
    kept = sanitize_settings({"general": {"default_export_directory": str(folder)}})
    assert kept["general"]["default_export_directory"] == str(folder)


def test_settings_save_reports_failure_instead_of_raising(tmp_path):
    # A directory where the settings file should be makes the write fail.
    blocked = tmp_path / "settings.json"
    blocked.mkdir()
    service = SettingsService(blocked)
    assert service.save() is False
    assert service.last_error


def test_settings_recent_count_accepts_zero():
    assert sanitize_settings({"general": {"recent_project_count": 0}})[
        "general"
    ]["recent_project_count"] == 0


# ------------------------------------------------------------------- recovery
def test_corrupt_recovery_marker_is_preserved(tmp_path, monkeypatch):
    marker = tmp_path / "recovery.json"
    monkeypatch.setattr(recovery_service, "marker_path", lambda: marker)
    marker.write_text("{not json", encoding="utf-8")

    assert recovery_service.pending_recovery() is None
    kept = tmp_path / "recovery.corrupt.json"
    assert kept.is_file(), "the unreadable marker was silently deleted"
    assert kept.read_text(encoding="utf-8") == "{not json"
    assert not marker.exists()


def test_recovery_marker_written_atomically(tmp_path, monkeypatch):
    marker = tmp_path / "recovery.json"
    monkeypatch.setattr(recovery_service, "marker_path", lambda: marker)
    project_db = tmp_path / "project.db"
    project_db.write_bytes(b"x")

    recovery_service.write_marker(str(project_db), "Demo", dirty=True)

    assert marker.is_file()
    assert not list(tmp_path.glob("recovery.json.tmp")), "temporary file left behind"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["project_name"] == "Demo"


# --------------------------------------------------------------- export names
def test_export_file_names_cannot_escape_the_folder():
    from app.export_dialog import safe_pdf_file_name

    chosen = Path(r"C:\Exports\Run")
    for hostile in (
        r"..\..\evil.pdf",
        "../../evil.pdf",
        "sub/dir/x.pdf",
        'bad?name*.pdf',
        "CON.pdf",
        "   .pdf",
    ):
        name = safe_pdf_file_name(hostile)
        assert "/" not in name and "\\" not in name
        assert ".." not in name
        assert name.lower().endswith(".pdf")
        assert (chosen / name).resolve().parent == chosen.resolve()


def test_export_file_name_keeps_unicode():
    from app.export_dialog import safe_pdf_file_name

    assert safe_pdf_file_name("ملف الهوية.pdf") == "ملف الهوية.pdf"


def test_folder_name_sanitiser_keeps_arabic():
    assert sanitize_folder_name("مشروع ٢٠٢٤") == "مشروع ٢٠٢٤"



