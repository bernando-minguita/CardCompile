"""Tests for README workflows that shipped without pytest coverage.

The headless smoke scripts (``qt_smoke.py``, ``ui_smoke.py``) call the clipboard
and the zoom controls but only print the result; nothing asserted it.  The tests
here cover the user workflows that were in that position:

* clipboard copy / cut / paste, including pasting onto a different page
* Image Library search, category filter, sort orders, counts and placement badges
* duplicating a library image (shares the original file and its thumbnail)
* renaming a project (workspace, project row, registry, reopen, blank name)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - environment guard
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    pytest.skip("PySide6 is not available", allow_module_level=True)

from core.document_service import (  # noqa: E402
    SORT_IMPORTED,
    SORT_NAME,
    DocumentService,
)
from core.import_service import ImportService  # noqa: E402
from core.project_service import ProjectServiceError  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def editor(qt_app, workspace, tmp_path):
    from app.layout_editor import LayoutEditor

    sources = []
    for index in range(4):
        path = tmp_path / "sources" / f"card_{index}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (360, 226), (240, 240, 240)).save(path, format="JPEG")
        sources.append(path)
    importer = ImportService(workspace)
    previews = importer.analyze(sources, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(workspace.project.folder / "editor_settings.json")
    settings.load()
    settings.set("layout", "snap_small_cards", False)
    editor = LayoutEditor(workspace, settings)
    editor.resize(1000, 800)
    editor.show()
    QApplication.processEvents()
    yield editor
    editor.setParent(None)
    editor.deleteLater()


def _items(editor, page_id: str | None = None):
    return editor.layout.items(page_id or editor.current_page_id)


# ------------------------------------------------------------------- clipboard
def test_copy_and_paste_puts_a_second_copy_on_another_page(editor):
    """Ctrl+C on one page, Ctrl+V on another: new id, new page, source untouched."""
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:1])
    first_page = editor.current_page()
    original = _items(editor)[0]

    editor.select_all()
    editor.copy_selection()

    second_page = editor.layout.add_page(after_page_id=first_page.id)
    editor.set_current_page(second_page.id)
    assert _items(editor) == []

    editor.paste_clipboard()

    pasted = _items(editor)
    assert len(pasted) == 1, "paste produced no item on the target page"
    copy = pasted[0]
    assert copy.id != original.id, "the pasted copy reuses the original row id"
    assert copy.page_id == second_page.id
    assert copy.document_id == original.document_id
    assert copy.width == pytest.approx(original.width)
    assert copy.height == pytest.approx(original.height)

    # The pasted copy is offset and clamped to the page.
    expected_x = min(
        max(0.0, original.position_x + 6.0), max(0.0, second_page.width_mm - copy.width)
    )
    expected_y = min(
        max(0.0, original.position_y + 6.0),
        max(0.0, second_page.height_mm - copy.height),
    )
    assert copy.position_x == pytest.approx(expected_x, abs=0.01)
    assert copy.position_y == pytest.approx(expected_y, abs=0.01)

    assert len(editor.layout.items(first_page.id)) == 1, "the original moved"

    editor.undo_stack.undo()
    assert _items(editor, second_page.id) == []


def test_cut_then_paste_moves_an_image_between_pages(editor):
    """Ctrl+X + Ctrl+V is the documented way to move an image to another page."""
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:1])
    first_page = editor.current_page()
    original = _items(editor)[0]
    document_id = original.document_id

    editor.select_all()
    editor.cut_selection()
    QApplication.processEvents()
    assert editor.layout.items(first_page.id) == [], "cut left the image behind"

    second_page = editor.layout.add_page(after_page_id=first_page.id)
    editor.set_current_page(second_page.id)

    # The clipboard survives switching pages.
    editor.paste_clipboard()
    moved = editor.layout.items(second_page.id)
    assert len(moved) == 1
    assert moved[0].document_id == document_id
    assert moved[0].id != original.id
    assert editor.layout.items(first_page.id) == []


# ------------------------------------------------------------- Image Library
def test_library_search_filter_sort_and_badges(editor, workspace):
    """Search matches name/category/type, the filter narrows, sorting is stable."""
    from app.organizer_widget import OrganizerWidget

    service = DocumentService(workspace)
    documents = service.documents()
    assert len(documents) == 4

    # Search: file name, and the file type ("jpeg" is the stored image format).
    assert [doc.filename for doc in service.search(documents, query="card_2")] == [
        "card_2.jpg"
    ]
    assert len(service.search(documents, query="jpeg")) == 4
    assert service.search(documents, query="nothing-matches") == []

    # Category search and the category filter select the same document.
    categories = workspace.database.categories.list_for_project(workspace.id)
    second = categories[1]
    service.set_category([documents[0].id], second.id)
    documents = service.documents()
    by_category = service.search(documents, category_id=second.id)
    assert [doc.id for doc in by_category] == [documents[0].id]
    assert len(service.search(documents, query=second.name.lower())) >= 1

    # Sort orders: import order and both name directions use different keys.
    assert [doc.filename for doc in service.search(documents, sort_by=SORT_IMPORTED)][
        0
    ] == "card_0.jpg"
    assert [
        doc.filename for doc in service.search(documents, sort_by=SORT_NAME, descending=True)
    ][0] == "card_3.jpg"

    # Placement badges come from the page the image is placed on.
    placed_document = documents[1]
    editor.add_documents([placed_document])
    QApplication.processEvents()
    document = service.documents()[1]
    assert document.page_numbers, "the image was not placed on a page"

    panel = OrganizerWidget()
    panel.set_workspace(workspace)
    QApplication.processEvents()
    try:
        page_number = document.page_numbers[0]
        label = OrganizerWidget._card_label(document)
        assert f"- page {page_number}" in label
        assert "- not placed" in OrganizerWidget._card_label(service.documents()[0])
        assert "MISSING" not in label

        counts = panel.count_label.text()
        assert "4 imported" in counts
        assert "1 placed" in counts

        # Narrowing the panel shows the "N shown" counter.
        panel.search_edit.setText("card_1")
        QApplication.processEvents()
        assert panel.list.count() == 1
        assert "1 shown" in panel.count_label.text()

        # The badge updates without reloading the whole library.
        panel.search_edit.setText("")
        QApplication.processEvents()
        before = panel.list.item(0).text()
        assert "- not placed" in before or "- page" in before
        panel.refresh_placement_status()
        QApplication.processEvents()
        assert panel.list.count() == 4
    finally:
        panel.deleteLater()


def test_duplicating_a_library_image_reuses_the_source_file(editor, workspace):
    """Duplicate makes a second library entry, never a second copy of the scan."""
    service = DocumentService(workspace)
    original = service.documents()[0]
    before = len(service.documents())

    clone = service.duplicate_document(original.id)
    assert clone is not None
    assert clone.id != original.id
    assert clone.source_path == original.source_path
    assert clone.thumbnail_path == original.thumbnail_path
    assert (clone.dpi_x, clone.dpi_y) == (original.dpi_x, original.dpi_y)
    assert Path(original.source_path).is_file()
    assert len(service.documents()) == before + 1
    assert clone.sort_order > original.sort_order, "the copy would not sort last"

    # A duplicate is its own library entry: placing the original does not mark
    # the copy as placed, and exporting still uses one source file.
    editor.add_documents([original])
    QApplication.processEvents()
    placed = next(doc for doc in service.documents() if doc.id == original.id)
    assert placed.page_numbers, "the original was not placed on a page"
    fresh_clone = service.duplicate_document(original.id)
    assert fresh_clone is not None and not fresh_clone.page_numbers


# ------------------------------------------------------------------ projects
def test_renaming_a_project_persists_across_reopen(workspace, project_service):
    """Rename updates the project row, the registry and survives a reopen."""
    folder = workspace.project.folder
    project_service.rename_project(workspace, "  Renamed Project  ")
    assert workspace.project.name == "Renamed Project"

    known = project_service.known_project(workspace.id)
    assert known is not None and known.name == "Renamed Project"

    with pytest.raises(ProjectServiceError):
        project_service.rename_project(workspace, "   ")
    assert workspace.project.name == "Renamed Project", "a blank name was applied"

    workspace.close()
    reopened = project_service.open_recent(workspace.id)
    try:
        assert reopened.project.name == "Renamed Project"
        assert project_service.known_project(reopened.id).name == "Renamed Project"
    finally:
        reopened.close()

    # Renaming does not move the folder, so no project data is lost.
    assert (folder / "project.db").is_file()


def test_arrange_menu_leaves_canvas_only_actions_to_the_context_menu(qt_app, editor, tmp_path):
    """Crop/size/layer actions belong to the canvas menu only.

    They were duplicated in both menus; the Arrange menu keeps the actions that
    have no context-menu equivalent (alignment, rotation by 180/custom, text,
    page arrangement), and "Make it card size" keeps Ctrl+Shift+K through a
    window shortcut because the removed QAction no longer has a menu context.
    """
    from app.context import AppContext
    from app.main_window import MainWindow
    from core.project_service import ProjectService
    from core.settings_service import SettingsService
    from database.app_database import AppDatabase

    settings = SettingsService(tmp_path / "menu_settings.json")
    settings.load()
    app_db = AppDatabase(tmp_path / "menu_app.db")
    window = MainWindow(
        AppContext(
            settings=settings,
            app_db=app_db,
            projects=ProjectService(app_db, settings),
        )
    )
    try:
        arrange_action = next(
            action for action in window.menuBar().actions() if action.text() == "&Arrange"
        )
        arrange = arrange_action.menu()
        labels = [a.text() for a in arrange.actions() if not a.isSeparator()]

        duplicated = (
            "Crop",
            "Reset crop",
            "Restore original size",
            "Make it card size (85.6 x 54 mm)",
            "Set physical size...",
            "Bring forward",
            "Send backward",
            "Bring to front",
            "Send to back",
            "Rotate 90 degrees clockwise",
            "Rotate 90 degrees counter clockwise",
        )
        still_there = [label for label in duplicated if label in labels]
        assert not still_there, f"still duplicated in the Arrange menu: {still_there}"

        # The remaining groups are untouched and no separator dangles.
        for kept in (
            "Align left",
            "Distribute horizontally",
            "Rotate 180 degrees",
            "Custom rotation...",
            "Add text...",
            "Place new images at original size",
            "Fit all placed images on this page",
            "Arrange selected page objects",
        ):
            assert kept in labels, f"the Arrange menu lost {kept!r}"
        actions = arrange.actions()
        assert not actions[0].isSeparator(), "the menu starts with a separator"
        assert not actions[-1].isSeparator(), "the menu ends with a separator"
        assert not any(
            first.isSeparator() and second.isSeparator()
            for first, second in zip(actions, actions[1:])
        ), "two separators in a row"

        # The canvas context menu still offers every one of them.
        documents = list(editor.layout.documents().values())
        editor.add_documents(documents[:1])
        menu = editor._build_item_menu(_items(editor)[0].id)
        assert menu is not None
        context_labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        for label in (
            "Rotate left 90",
            "Rotate right 90",
            "Crop...",
            "Reset crop",
            "Restore original size",
            "Make it card size (85.6 x 54 mm)",
            "Set physical size...",
            "Bring forward",
            "Send backward",
            "Bring to front",
            "Send to back",
        ):
            assert label in context_labels, f"the canvas menu lost {label!r}"

        # Ctrl+Shift+K survives the removal of its menu item.
        assert window.shortcut_card_size.key().toString() == "Ctrl+Shift+K"
        assert window.shortcut_card_size.isEnabled()
    finally:
        window.setParent(None)
        window.deleteLater()
        app_db.close()


def test_page_navigator_offers_page_actions_in_a_context_menu(qt_app, editor):
    """The Pages section has a right-click menu, not a button strip.

    Every entry calls the same editor method the Page menu uses, so the two
    menus stay in sync; the edge cases (first page, single page) disable the
    entries that would do nothing instead of showing a dialog.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QToolButton

    from app.page_manager import PageNavigator

    navigator = PageNavigator()
    navigator.attach_editor(editor)
    try:
        assert navigator.findChildren(QToolButton) == [], "the button strip is still there"
        assert (
            navigator.list.contextMenuPolicy()
            == Qt.ContextMenuPolicy.CustomContextMenu
        )

        menu = navigator._build_page_menu()
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert labels == [
            "Add page",
            "Duplicate page",
            "Clear page",
            "Delete page",
            "Move page up",
            "Move page down",
            "Toggle orientation",
        ]

        def action(name, from_menu=None):
            return next(
                entry
                for entry in (from_menu or menu).actions()
                if entry.text() == name
            )

        # One page: nothing to move and nothing safe to delete.
        assert not action("Move page up").isEnabled()
        assert not action("Move page down").isEnabled()
        assert not action("Delete page").isEnabled()

        # Add page goes through the editor (undoable), so it is the real action.
        action("Add page").trigger()
        QApplication.processEvents()
        assert len(editor.pages()) == 2

        # The navigator rebuilt itself from the editor's signal: the new page is
        # current, so "up" works, "down" does not, and delete is allowed again.
        second_menu = navigator._build_page_menu()
        assert action("Delete page", second_menu).isEnabled()
        assert action("Move page up", second_menu).isEnabled()
        assert not action("Move page down", second_menu).isEnabled()

        action("Duplicate page", second_menu).trigger()
        QApplication.processEvents()
        assert len(editor.pages()) == 3

        before = [page.id for page in editor.pages()]
        action("Move page up", navigator._build_page_menu()).trigger()
        QApplication.processEvents()
        assert [page.id for page in editor.pages()] != before, "move up did nothing"

        current = editor.current_page_id
        action("Clear page", navigator._build_page_menu()).trigger()
        QApplication.processEvents()
        assert editor.current_page_id == current, "clearing a page removed the page"

        # The Page menu's orientation toggle is reachable from the strip too.
        before_page = editor.current_page()
        was = before_page.orientation
        width, height = before_page.width_mm, before_page.height_mm
        action("Toggle orientation", navigator._build_page_menu()).trigger()
        QApplication.processEvents()
        page = editor.current_page()
        assert page.orientation == ("portrait" if was == "landscape" else "landscape")
        assert page.width_mm == pytest.approx(height), "the page did not turn"
        assert page.height_mm == pytest.approx(width)
    finally:
        navigator.deleteLater()


def test_clipboard_bitmap_is_saved_as_a_real_png(qt_app, tmp_path):
    """A bitmap on the clipboard has no path; it must land on disk as a PNG."""
    from PySide6.QtGui import QColor, QImage

    from core.clipboard_paste import save_clipboard_image

    image = QImage(120, 80, QImage.Format_RGB32)
    image.fill(QColor("#3366cc"))
    path = save_clipboard_image(image, tmp_path)
    assert path is not None and path.is_file()
    assert path.suffix == ".png"
    with Image.open(path) as saved:
        assert saved.size == (120, 80)

    # An empty clipboard is not an error - the caller falls back to the items.
    assert save_clipboard_image(QImage(), tmp_path) is None


def test_ctrl_v_imports_a_screenshot_and_places_it(qt_app, tmp_path, monkeypatch):
    """Ctrl+V with a snip imports it into the library and puts it on the page."""
    from PySide6.QtGui import QColor, QImage

    from app.context import AppContext
    from app.main_window import MainWindow
    from core.project_service import ProjectService
    from core.settings_service import SettingsService
    from database.app_database import AppDatabase

    monkeypatch.setenv("DPB_USER_DATA_DIR", str(tmp_path / "userdata"))
    settings = SettingsService(tmp_path / "paste_settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp_path / "projects"))
    settings.set("general", "default_export_directory", str(tmp_path / "exports"))
    app_db = AppDatabase(tmp_path / "paste_app.db")
    projects = ProjectService(app_db, settings)
    window = MainWindow(
        AppContext(settings=settings, app_db=app_db, projects=projects)
    )
    workspace = projects.create_project("Paste Project")
    window._attach_workspace(workspace)
    try:
        page_id = window.editor.current_page_id
        image = QImage(320, 200, QImage.Format_RGB32)
        image.fill(QColor("#3366cc"))
        QApplication.clipboard().setImage(image)

        window.paste()
        QApplication.processEvents()

        documents = workspace.database.documents.list_for_project(workspace.id)
        assert len(documents) == 1, "the pasted bitmap was not imported"
        document = documents[0]
        assert (document.original_width, document.original_height) == (320, 200)
        source = Path(document.source_path)
        assert source.is_file()
        assert source.suffix == ".png"
        assert source.parent.name == "Pasted images"
        assert document.thumbnail_path, "no thumbnail was generated"

        items = window.editor.layout.items(page_id)
        assert len(items) == 1, "the pasted image was not placed"
        assert items[0].document_id == document.id
    finally:
        QApplication.clipboard().clear()
        window.setParent(None)
        window.deleteLater()
        app_db.close()


def test_pasted_page_objects_still_take_priority_over_the_bitmap(qt_app, editor):
    """Copying an image inside the app must not paste a stale clipboard bitmap."""
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:1])
    page_id = editor.current_page_id
    assert editor.paste_clipboard() is False, "an empty clipboard pasted something"

    editor.select_all()
    editor.copy_selection()
    before = len(editor.layout.items(page_id))
    assert editor.paste_clipboard() is True
    assert len(editor.layout.items(page_id)) == before * 2
