"""Tests for project storage safety and missing-project recovery."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core import paths
from core.import_service import ImportService
from core.layout_service import LayoutService
from core.project_service import (
    ProjectServiceError,
    looks_like_project,
    missing_project_message,
)
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS


def test_projects_are_not_stored_inside_the_application_folder(monkeypatch, tmp_path):
    """A rebuild of the app deletes its own folder - projects must live outside."""
    app_folder = tmp_path / "app"
    app_folder.mkdir()
    monkeypatch.setattr(paths, "app_root", lambda: app_folder)

    assert paths.is_inside_app_folder(app_folder / "projects")
    assert not paths.is_inside_app_folder(tmp_path / "elsewhere")


def test_default_folder_ignores_a_path_inside_the_app_folder(
    monkeypatch, tmp_path, project_service
):
    from core import project_service as project_service_module

    app_folder = tmp_path / "app"
    app_folder.mkdir()
    monkeypatch.setattr(paths, "app_root", lambda: app_folder)
    # The service imports the helpers directly, so patch them there as well.
    monkeypatch.setattr(
        project_service_module, "documents_root", lambda: tmp_path / "docs"
    )
    monkeypatch.setattr(
        project_service_module, "projects_dir", lambda: tmp_path / "docs" / "Projects"
    )

    project_service._settings.set(
        "general", "default_project_directory", str(app_folder / "projects")
    )
    assert project_service.unsafe_project_folder() == app_folder / "projects"
    resolved = project_service.default_project_folder()
    assert resolved == tmp_path / "docs" / "Projects"
    assert resolved.is_dir()


def test_default_folder_keeps_a_safe_custom_path(project_service, tmp_path):
    custom = tmp_path / "my projects"
    project_service._settings.set("general", "default_project_directory", str(custom))
    assert project_service.unsafe_project_folder() is None
    assert project_service.default_project_folder() == custom


def test_unsafe_folder_warning_is_remembered(monkeypatch, tmp_path, project_service):
    """Saying "keep my folder" must stop the warning from reappearing."""
    from core import project_service as project_service_module

    app_folder = tmp_path / "app"
    app_folder.mkdir()
    monkeypatch.setattr(paths, "app_root", lambda: app_folder)
    monkeypatch.setattr(
        project_service_module, "documents_root", lambda: tmp_path / "docs"
    )
    monkeypatch.setattr(
        project_service_module, "projects_dir", lambda: tmp_path / "docs" / "Projects"
    )
    unsafe = app_folder / "projects"
    project_service._settings.set("general", "default_project_directory", str(unsafe))

    # Warning shown the first time.
    assert project_service.unsafe_project_folder() == unsafe
    # New projects are redirected to the safe place.
    assert project_service.default_project_folder() == tmp_path / "docs" / "Projects"

    # The user explicitly keeps the folder: no more warnings, folder honoured.
    project_service._settings.set("general", "allow_unsafe_project_folder", True)
    assert project_service.unsafe_project_folder() is None
    assert project_service.default_project_folder() == unsafe


def test_unsafe_default_is_off(settings):
    assert settings.get("general", "allow_unsafe_project_folder", None) in (
        False,
        None,
    )


def test_missing_message_explains_what_happened(tmp_path):
    message = missing_project_message(tmp_path / "gone" / "project.db")
    assert "project.db" in message
    assert "Locate" in message


@pytest.mark.parametrize(
    "name", ["Trip #1", "50% done", "مشروع ٢٠٢٤", "with space", "a+b"]
)
def test_project_in_an_unusual_folder_can_be_reopened(
    project_service, tmp_path, name
):
    """A legal Windows folder name must never make the project unreadable.

    ``project_file_problem`` built a ``file:<path>?mode=ro`` URI by hand, so a
    ``#`` in the folder name started a URI fragment: SQLite opened a different,
    empty file, the project was reported as "empty" and could not be reopened.
    """
    folder = tmp_path / "projects" / name
    workspace = project_service.create_project(name, folder=folder)
    db_path = workspace.db_path
    workspace.close()

    assert looks_like_project(db_path), "the project was not recognised"
    reopened = project_service.open_project(db_path)
    try:
        assert reopened.project.name == name
        assert reopened.project.folder == folder
        assert reopened.database.pages.count(reopened.id) == 1
    finally:
        reopened.close()


def test_moved_project_keeps_layout_and_reachable_files(
    project_service, workspace, image_factory, tmp_path
):
    """Moving the project folder must not disturb pages, items, labels or paths."""
    import uuid

    from models.page_label import PageLabel

    source = image_factory(fmt="JPEG")
    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    summary = importer.commit(previews, category.id)
    document = workspace.database.documents.get(summary.imported_ids[0])

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    item = layout.create_item(document, page, width=44.0, height=33.0)
    item.crop_left = 0.2
    item.rotation = 90.0
    item = layout.insert_item(item)
    label = workspace.database.labels.insert(
        PageLabel(id=str(uuid.uuid4()), page_id=page.id, text="Moved")
    )

    old_folder = workspace.project.folder
    workspace.close()
    moved = tmp_path / "moved # project"
    shutil.move(str(old_folder), str(moved))

    reopened = project_service.relink_project(workspace.id, moved / "project.db")
    try:
        stored = reopened.database.items.get(item.id)
        assert stored.width == item.width
        assert stored.crop_left == item.crop_left
        assert stored.rotation == item.rotation
        assert reopened.database.labels.get(label.id) is not None
        reloaded = reopened.database.documents.get(document.id)
        assert Path(reloaded.source_path).is_file(), "the original path broke"
        assert Path(reloaded.thumbnail_path).is_file(), "the thumbnail broke"
        assert reopened.project.folder == moved
    finally:
        reopened.close()


def test_relink_project_follows_a_moved_folder(project_service, workspace, tmp_path):
    """The saved project keeps working after its folder is renamed."""
    workspace.database.categories.list_for_project(workspace.id)
    workspace.close()

    source = workspace.project.folder
    moved = tmp_path / "renamed project"
    shutil.move(str(source), str(moved))
    assert not source.exists()

    with pytest.raises(ProjectServiceError):
        project_service.open_recent(workspace.id)

    reopened = project_service.relink_project(workspace.id, moved / "project.db")
    try:
        assert reopened.project.folder == moved
        # The database itself now points at the new folder.
        assert reopened.project.db_path == moved / "project.db"
        assert reopened.database.projects.first().project_folder == str(moved)
        known = project_service.known_project(reopened.id)
        assert known is not None and known.db_path == str(moved / "project.db")
        assert Path(known.db_path).is_file()
    finally:
        reopened.close()


def test_relink_failure_keeps_the_old_folder_for_a_retry(
    project_service, workspace, tmp_path, monkeypatch
):
    """A failed thumbnail rebase must not store the new folder.

    If the folder were committed but the rebase lost, a retry would see the new
    folder already saved, skip the rebase for good and leave every preview
    pointing into the old folder.  Both writes are one transaction instead.
    """
    from core import project_service as project_service_module

    real_rebase = project_service_module._rebase_thumbnail_paths
    attempts: list[str] = []

    def flaky_rebase(connection, old_folder, new_folder):
        if "raised" not in attempts:
            attempts.append("raised")
            raise RuntimeError("rebase failed")
        attempts.append("rebased")
        return real_rebase(connection, old_folder, new_folder)

    monkeypatch.setattr(
        project_service_module, "_rebase_thumbnail_paths", flaky_rebase
    )

    workspace.database.categories.list_for_project(workspace.id)
    workspace.close()

    moved = tmp_path / "renamed project"
    shutil.move(str(workspace.project.folder), str(moved))

    with pytest.raises(RuntimeError):
        project_service.relink_project(workspace.id, moved / "project.db")

    # A retry must still see the move and repeat the whole update.
    assert attempts == ["raised"]  # the stub really intercepted the first call
    reopened = project_service.relink_project(workspace.id, moved / "project.db")
    try:
        assert reopened.database.projects.first().project_folder == str(moved)
        assert reopened.project.folder == moved
    finally:
        reopened.close()
    assert attempts == ["raised", "rebased"]  # the retry ran the rebase


def test_relink_rejects_a_file_that_is_not_a_project(
    project_service, workspace, tmp_path
):
    other = tmp_path / "not-a-project.db"
    other.write_text("", encoding="utf-8")
    with pytest.raises(ProjectServiceError):
        project_service.relink_project(workspace.id, other)


def test_open_does_not_write_into_a_foreign_database(project_service, tmp_path):
    """Opening a random SQLite file must not add our tables to it."""
    import sqlite3

    foreign = tmp_path / "other.db"
    connection = sqlite3.connect(foreign)
    connection.execute("CREATE TABLE notes (body TEXT)")
    connection.execute("INSERT INTO notes (body) VALUES ('keep me')")
    connection.commit()
    connection.close()
    before = foreign.read_bytes()

    with pytest.raises(ProjectServiceError):
        project_service.open_project(foreign)

    assert foreign.read_bytes() == before
    assert project_service.known_projects() == []


def test_user_data_dir_can_be_redirected(monkeypatch, tmp_path):
    """Tools and tests must never touch the real %APPDATA% registry."""
    monkeypatch.setenv("DPB_USER_DATA_DIR", str(tmp_path / "userdata"))
    assert paths.user_data_dir() == tmp_path / "userdata"
    monkeypatch.delenv("DPB_USER_DATA_DIR")


def test_frozen_build_resolves_bundled_resources_logs_and_schema(monkeypatch, tmp_path):
    """PyInstaller folder build: resources in _internal, logs beside the .exe.

    ``CardCompile.spec`` bundles ``resources`` and ``database/schema.sql``
    into the build's ``_internal`` directory, which is what ``sys._MEIPASS``
    points at, while logs and user data must stay next to the executable (that
    is the path the README documents).
    """
    import sys

    install = tmp_path / "CardCompile"
    internal = install / "_internal"
    (internal / "resources" / "styles").mkdir(parents=True)
    (internal / "resources" / "styles" / "app.qss").write_text(
        "QWidget {}", encoding="utf-8"
    )
    (internal / "database").mkdir(parents=True)
    root = Path(__file__).resolve().parent.parent
    shutil.copy(root / "database" / "schema.sql", internal / "database" / "schema.sql")
    (internal / "database" / "connection.py").write_text("", encoding="utf-8")
    exe = install / "CardCompile.exe"
    exe.write_bytes(b"stub")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)

    assert paths.app_root() == install.resolve()
    assert paths.resources_dir().resolve() == (internal / "resources").resolve()
    assert (paths.resources_dir() / "styles" / "app.qss").is_file()
    assert paths.logs_dir().resolve() == (install / "logs").resolve()
    assert paths.logs_dir().is_dir(), "the log folder was not created in the build"
    assert paths.is_inside_app_folder(install / "projects")
    assert not paths.is_inside_app_folder(tmp_path / "elsewhere")

    # A frozen database module must find schema.sql beside itself in _MEIPASS.
    import database.connection as connection_module

    monkeypatch.setattr(
        connection_module, "__file__", str(internal / "database" / "connection.py")
    )
    from database.connection import Database

    database = Database(tmp_path / "frozen-schema.db")
    try:
        database.init_schema()
        assert database.table_exists("page_labels")
    finally:
        database.close()
