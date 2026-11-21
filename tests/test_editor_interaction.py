"""Editor interaction tests: placement, persistence, drops and fit zoom."""

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
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    pytest.skip("PySide6 is not available", allow_module_level=True)

from core.import_service import ImportService  # noqa: E402
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
    # These tests exercise placement mechanics with small images; the automatic
    # "card size" snap (see tests/test_card_placement.py) is opted out here.
    settings.set("layout", "snap_small_cards", False)
    editor = LayoutEditor(workspace, settings)
    editor.resize(1000, 800)
    editor.show()
    QApplication.processEvents()
    yield editor
    editor.setParent(None)
    editor.deleteLater()


def _items(editor):
    return editor.layout.items(editor.current_page_id)


def test_new_images_are_not_stacked_on_top_of_each_other(editor):
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents)
    items = _items(editor)
    assert len(items) == 4
    positions = {(round(item.position_x, 1), round(item.position_y, 1)) for item in items}
    assert len(positions) == 4, "images were placed on top of each other"


def test_find_free_position_avoids_existing_items(editor):
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:1])
    page = editor.current_page()
    item = _items(editor)[0]
    x, y = editor.find_free_position(page, item.width, item.height, [item])
    overlaps = (
        x < item.position_x + item.width
        and x + item.width > item.position_x
        and y < item.position_y + item.height
        and y + item.height > item.position_y
    )
    assert not overlaps


def test_a_moved_image_keeps_its_position_when_another_is_added(editor):
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:1])
    first = _items(editor)[0]

    graphic = editor.scene.item_by_id(first.id)
    graphic.interaction_started.emit(first.id)
    graphic.model.position_y = 12.5
    graphic.sync_from_model()
    graphic.interaction_finished.emit(first.id)
    QApplication.processEvents()

    assert editor.layout.db.items.get(first.id).position_y == pytest.approx(12.5)

    editor.add_documents(documents[1:2])
    QApplication.processEvents()
    assert editor.layout.db.items.get(first.id).position_y == pytest.approx(12.5)
    assert editor.undo_stack.undoText() in ("Move image", "Add image")


def test_drop_places_the_image_near_the_cursor(editor):
    documents = list(editor.layout.documents().values())
    editor._on_documents_dropped([documents[0].id], QPointF(60.0, 200.0))
    items = _items(editor)
    assert len(items) == 1
    item = items[0]
    assert 20.0 <= item.position_x <= 100.0
    assert 150.0 <= item.position_y <= 240.0


def test_select_all_delete_and_undo_is_stable(editor):
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents)
    page_id = editor.current_page_id
    for _ in range(3):
        editor.select_all()
        QApplication.processEvents()
        editor.delete_selection()
        QApplication.processEvents()
        editor.undo_stack.undo()
        QApplication.processEvents()
        editor.undo_stack.redo()
        QApplication.processEvents()
    assert len(editor.layout.items(page_id)) == 0


def test_reloading_the_page_while_items_are_selected_is_safe(editor):
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents)
    editor.select_all()
    for _ in range(3):
        editor.reload_current_page()
        QApplication.processEvents()
    assert len(editor.scene.image_items()) == len(documents)


def test_delete_from_the_context_menu_is_safe(editor):
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:2])
    item = _items(editor)[0]
    menu = editor._build_item_menu(item.id)
    delete = next(action for action in menu.actions() if action.text() == "Delete")
    delete.trigger()
    QApplication.processEvents()
    assert len(_items(editor)) == 1


def test_drop_event_delivers_documents(qt_app, editor):
    """A real QDropEvent must reach the editor (regression: pos().toPoint crash)."""
    from app.dnd import DOCUMENT_MIME
    from PySide6.QtCore import QMimeData, Qt
    from PySide6.QtGui import QDropEvent

    documents = list(editor.layout.documents().values())
    mime = QMimeData()
    mime.setData(DOCUMENT_MIME, documents[0].id.encode("utf-8"))
    event = QDropEvent(
        QPointF(120.0, 200.0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    before = len(_items(editor))
    editor.view.dropEvent(event)  # must not raise
    QApplication.processEvents()
    assert len(_items(editor)) == before + 1


def test_duplicate_from_the_context_menu_is_safe(editor):
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:2])
    item = _items(editor)[0]
    editor.select_all()
    before = len(_items(editor))
    menu = editor._build_item_menu(item.id)
    duplicate = next(action for action in menu.actions() if action.text() == "Duplicate")
    duplicate.trigger()
    QApplication.processEvents()
    assert len(_items(editor)) == before * 2


def test_fit_zoom_waits_for_a_usable_viewport(editor):
    """Fitting before layout produced a 4 % view where nothing was visible."""
    view = editor.view
    view.resize(60, 60)
    view.request_fit()
    assert view._fit_pending is True  # deferred, not applied at 60 px

    view.resize(900, 700)
    QApplication.processEvents()
    assert view._fit_pending is False
    assert view.zoom > 0.4, f"fit produced an unusable zoom of {view.zoom:.3f}"


def _click(editor, graphic, modifiers=None, drag_mm=None):
    """Send a real press/move/release to the canvas at the graphic's centre."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    if modifiers is None:
        modifiers = Qt.KeyboardModifier.NoModifier

    centre = graphic.mapToScene(graphic.boundingRect().center())
    start = editor.view.mapFromScene(centre)
    viewport = editor.view.viewport()

    def send(kind, point, buttons):
        event = QMouseEvent(
            kind,
            QPointF(point),
            QPointF(viewport.mapToGlobal(point)),
            Qt.MouseButton.LeftButton,
            buttons,
            modifiers,
        )
        QApplication.sendEvent(viewport, event)
        QApplication.processEvents()

    send(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton)
    if drag_mm is not None:
        end = editor.view.mapFromScene(centre + QPointF(*drag_mm))
        send(QEvent.Type.MouseMove, end, Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseButtonRelease, start, Qt.MouseButton.NoButton)


def test_ctrl_click_adds_and_removes_images_from_the_selection(editor):
    """Qt toggled the Ctrl+click off again on release, so only one stayed selected."""
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:3])
    QApplication.processEvents()
    items = editor.scene.image_items()
    assert len(items) == 3, "the fixture placed the wrong number of images"

    editor.scene.clearSelection()
    _click(editor, items[0])
    assert [model.id for model in editor.selected_models()] == [items[0].item_id]

    _click(editor, items[1], Qt.KeyboardModifier.ControlModifier)
    assert {model.id for model in editor.selected_models()} == {
        items[0].item_id,
        items[1].item_id,
    }, "Ctrl+click did not add the second image to the selection"

    _click(editor, items[2], Qt.KeyboardModifier.ControlModifier)
    assert len(editor.selected_models()) == 3

    # Ctrl+click on a selected image removes it again.
    _click(editor, items[1], Qt.KeyboardModifier.ControlModifier)
    assert {model.id for model in editor.selected_models()} == {
        items[0].item_id,
        items[2].item_id,
    }


def test_clicking_the_middle_of_a_card_drags_it_instead_of_resizing(editor):
    """An 8 mm handle at 100 % zoom covered the card, so the centre resized it."""
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:1])
    QApplication.processEvents()
    item = editor.scene.image_items()[0]
    width, height = item.model.width, item.model.height
    start_x = item.model.position_x

    _click(editor, item, drag_mm=(20.0, 12.0))

    model = editor.layout.db.items.get(item.item_id)
    assert (model.width, model.height) == (pytest.approx(width), pytest.approx(height)), (
        "dragging from the middle changed the size instead of moving the image"
    )
    assert model.position_x > start_x + 5.0, "the image did not move with the drag"


def test_rotation_handle_takes_priority_over_top_resize_handle(editor):
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[:1])
    QApplication.processEvents()
    item = editor.scene.image_items()[0]
    points = item._handle_points()

    assert item.handle_at(points["rotate"]) == "rotate"
    assert item.handle_at(points["t"]) == "t"
