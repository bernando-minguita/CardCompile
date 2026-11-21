"""Tests for giving placed images a real-world (card) size."""

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

from core.import_service import ImportService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def editor(qt_app, workspace, tmp_path):
    from app.layout_editor import LayoutEditor

    source = tmp_path / "sources" / "iqama.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    # 360 x 226 px tagged 300 dpi -> prints far too small (30.5 x 19.1 mm).
    Image.new("RGB", (360, 226), (245, 245, 245)).save(
        source, format="JPEG", dpi=(300, 300)
    )
    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(workspace.project.folder / "size_settings.json")
    settings.load()
    # The automatic card-size snap is covered by tests/test_card_placement.py;
    # here the item must stay at the (too small) size the file claims so the
    # "make it card size" / "set physical size" actions have something to fix.
    settings.set("layout", "snap_small_cards", False)
    editor = LayoutEditor(workspace, settings)
    editor.add_documents(list(editor.layout.documents().values()))
    yield editor
    editor.setParent(None)
    editor.deleteLater()


def _only_item(editor):
    items = editor.layout.items(editor.current_page_id)
    assert len(items) == 1
    return items[0]


def test_card_size_gives_real_printed_dimensions(editor):
    item = _only_item(editor)
    assert item.width < 40.0  # the raw "original size" placement is too small

    editor.select_all()
    changed = editor.set_selection_card_size()

    assert changed == 1
    item = _only_item(editor)
    # The card size is a known pair of dimensions, so the height must be
    # honoured.  Deriving it from the scan's own ratio used to leave a portrait
    # photo portrait (85.6 x 137 mm) and the action looked like it did nothing.
    assert item.width == pytest.approx(85.6, abs=0.2)
    assert item.height == pytest.approx(54.0, abs=0.2)
    assert item.width > item.height, "an ID-1 card is landscape"


def test_card_size_turns_a_portrait_scan_landscape(qt_app, workspace, tmp_path):
    """The reported bug: a portrait scan stayed portrait at card size."""
    from app.layout_editor import LayoutEditor

    source = tmp_path / "portrait.jpg"
    Image.new("RGB", (400, 800), (240, 238, 232)).save(
        source, format="JPEG", dpi=(300, 300)
    )
    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(workspace.project.folder / "portrait_settings.json")
    settings.load()
    settings.set("layout", "snap_small_cards", False)
    editor = LayoutEditor(workspace, settings)
    try:
        editor.add_documents(list(editor.layout.documents().values()))
        item = editor.layout.items(editor.current_page_id)[0]
        assert item.height > item.width, "the fixture must start portrait"

        editor.select_all()
        editor.set_selection_card_size()

        item = editor.layout.items(editor.current_page_id)[0]
        assert item.width == pytest.approx(85.6, abs=0.2)
        assert item.height == pytest.approx(54.0, abs=0.2)
        assert item.width > item.height, "the card is still portrait"
    finally:
        editor.setParent(None)
        editor.deleteLater()



def test_card_size_keeps_the_centre(editor):
    item = _only_item(editor)
    # Centre the card first: enlarging an item that sits at the page edge has to
    # be clamped, which legitimately moves it.
    page = editor.current_page()
    item.position_x = (page.width_mm - item.width) / 2.0
    item.position_y = (page.height_mm - item.height) / 2.0
    editor.layout.save_item(item)
    editor.reload_current_page()
    item = _only_item(editor)
    original_center = item.center
    editor.select_all()
    editor.set_selection_card_size()
    item = _only_item(editor)
    assert item.center[0] == pytest.approx(original_center[0], abs=1.0)
    assert item.center[1] == pytest.approx(original_center[1], abs=1.0)


def test_card_size_is_undoable(editor):
    item = _only_item(editor)
    before = (item.width, item.height)
    editor.select_all()
    editor.set_selection_card_size()
    editor.undo_stack.undo()
    item = _only_item(editor)
    assert (item.width, item.height) == pytest.approx(before)


def test_custom_size_without_aspect_lock(editor):
    editor.select_all()
    editor.set_selection_physical_size(70.0, 45.0, keep_aspect=False)
    item = _only_item(editor)
    assert item.width == pytest.approx(70.0, abs=0.05)
    assert item.height == pytest.approx(45.0, abs=0.05)


def test_size_larger_than_the_page_is_reduced(editor):
    editor.select_all()
    editor.set_selection_physical_size(400.0, 300.0, keep_aspect=False)
    item = _only_item(editor)
    page = editor.current_page()
    assert item.width <= page.width_mm + 0.01
    assert item.height <= page.height_mm + 0.01
    assert item.position_x >= -0.01
    assert item.position_y >= -0.01


def test_locked_items_are_skipped(editor):
    editor.select_all()
    editor.toggle_lock_selection()
    item = _only_item(editor)
    assert item.is_locked is True
    before = (item.width, item.height)
    editor.select_all()
    changed = editor.set_selection_card_size()
    assert changed == 0
    item = _only_item(editor)
    assert (item.width, item.height) == pytest.approx(before)


def test_context_menu_offers_card_size(editor):
    from graphics.image_item import PageImageItem  # noqa: F401 - keeps import order

    item = _only_item(editor)
    editor.select_all()
    menu = editor._build_item_menu(item.id)
    assert menu is not None
    labels = [action.text() for action in menu.actions() if action.text()]
    assert "Make it card size (85.6 x 54 mm)" in labels
    assert "Set physical size..." in labels
    menu.deleteLater()


def test_physical_size_dialog_presets(qt_app):
    from app.physical_size_dialog import PhysicalSizeDialog

    dialog = PhysicalSizeDialog(None, width_mm=85.6, height_mm=54.0, count=2)
    try:
        assert dialog.size_mm() == pytest.approx((85.6, 54.0))
        # A preset names an exact size, so the aspect lock starts OFF.  It used
        # to start ON, which discarded the height and made the preset useless
        # for a portrait scan.
        assert dialog.keep_aspect() is False
        # Passport data page preset
        dialog.preset_combo.setCurrentIndex(1)
        assert dialog.size_mm() == pytest.approx((88.0, 125.0))
    finally:
        dialog.deleteLater()


def test_physical_size_dialog_aspect_lock_is_available(qt_app):
    """The lock still exists for users who want width-driven sizing."""
    from app.physical_size_dialog import PhysicalSizeDialog

    dialog = PhysicalSizeDialog(None, count=1)
    try:
        dialog.keep_aspect_check.setChecked(True)
        assert dialog.keep_aspect() is True
    finally:
        dialog.deleteLater()


def test_settings_dpi_is_not_changed_by_physical_size(editor):
    settings = editor.settings
    before = settings.get("layout", "original_size_dpi", 300)
    editor.select_all()
    editor.set_selection_card_size()
    assert settings.get("layout", "original_size_dpi", 300) == before
