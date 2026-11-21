"""Regression tests for crop persistence (1.3.5).

The crop overlay edits the *live* model held by the scene item.  Persisting the
row freshly read from the database threw the crop away, so the image snapped
back on the next page reload even though the rectangle had moved.
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
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    pytest.skip("PySide6 is not available", allow_module_level=True)

from commands.base import item_snapshot  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService  # noqa: E402


class _Event:
    """Minimal stand-in for the QGraphicsScene events the overlay reads."""

    def __init__(self, x: float = 0.0, y: float = 0.0, key=None) -> None:
        self._point = QPointF(x, y)
        self._key = key

    def pos(self):
        return self._point

    def button(self):
        return Qt.MouseButton.LeftButton

    def key(self):
        return self._key

    def accept(self):
        pass

    def ignore(self):
        pass


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def editor(qt_app, workspace, tmp_path):
    from app.layout_editor import LayoutEditor

    source = tmp_path / "sources" / "card.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    # 1011 x 638 px at 300 dpi = 85.6 x 54 mm (a correctly tagged ID-1 card).
    Image.new("RGB", (1011, 638), (240, 240, 240)).save(
        source, format="JPEG", dpi=(300, 300)
    )
    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(workspace.project.folder / "crop_settings.json")
    settings.load()
    editor = LayoutEditor(workspace, settings)
    editor.resize(1000, 800)
    editor.show()
    QApplication.processEvents()
    editor.add_documents(list(editor.layout.documents().values()))
    QApplication.processEvents()
    yield editor
    editor.setParent(None)
    editor.deleteLater()


def _item(editor):
    items = editor.layout.items(editor.current_page().id)
    assert items
    return items[0]


def _overlay(editor, item_id):
    graphic = editor.scene.image_item(item_id)
    assert graphic is not None
    return graphic.crop_overlay


def test_crop_drag_is_saved(editor):
    item = _item(editor)
    editor.select_all()
    editor.start_crop()  # the real entry point (menu / context menu / panel)
    assert editor.scene.crop_item_id == item.id

    overlay = _overlay(editor, item.id)
    assert overlay is not None
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.25, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())

    stored = editor.layout.db.items.get(item.id)
    assert stored.crop_left == pytest.approx(0.25, abs=0.01), "the crop was lost"
    # and it survives a reload (what the user sees after changing pages)
    editor.reload_current_page()
    assert editor.layout.db.items.get(item.id).crop_left == pytest.approx(0.25, abs=0.01)


def test_crop_trims_the_frame_without_distortion(editor):
    """The passport case: drag the top handle to the centre.

    The frame must shrink with the crop (never stretch the visible part) and the
    remaining content has to stay exactly where it was on the page.
    """
    item = _item(editor)
    start = (item.position_x, item.position_y, item.width, item.height)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "t"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())

    stored = editor.layout.db.items.get(item.id)
    assert stored.crop_top == pytest.approx(0.5, abs=0.01)
    assert stored.width == pytest.approx(start[2], abs=0.2), "width must not change"
    assert stored.height == pytest.approx(start[3] / 2.0, abs=0.3), "frame not trimmed"
    assert stored.position_y == pytest.approx(start[1] + start[3] / 2.0, abs=0.3), (
        "the remaining content moved instead of staying put"
    )

    document = editor.layout.documents()[stored.document_id]
    left, top, right, bottom = stored.crop_box()
    visible_px_w = (right - left) * document.original_width
    visible_px_h = (bottom - top) * document.original_height
    assert (stored.width / stored.height) == pytest.approx(
        visible_px_w / visible_px_h, rel=0.01
    ), "the visible part is stretched"


def test_restore_original_size_uses_only_the_cropped_region(editor):
    """Restore must give 100 % of the visible part - not the whole frame, and not
    an ID-1 card scaled up from it.

    It used to apply the ``snap_small_cards`` enlargement, so a half-frame crop
    of a correctly tagged card (42.8 x 54 mm) came back as 67.8 x 85.6 mm.
    """
    from app.properties_panel import PropertiesPanel

    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())

    cropped = editor.layout.db.items.get(item.id)
    document = editor.layout.documents()[cropped.document_id]
    left, top, right, bottom = cropped.crop_box()
    visible_px_w = (right - left) * document.original_width
    visible_px_h = (bottom - top) * document.original_height
    expected_w = visible_px_w / 300 * 25.4
    expected_h = expected_w * visible_px_h / visible_px_w

    # Distort it first, so the restore has to do real work.
    editor.apply_item_geometry(item.id, {"width": 120.0, "height": 120.0})
    undo_before = editor.undo_stack.count()

    panel = PropertiesPanel()
    panel.attach_editor(editor)
    editor.select_all()
    editor.reset_selection_to_original_size()

    stored = editor.layout.db.items.get(item.id)
    assert stored.width == pytest.approx(expected_w, abs=0.05)
    assert stored.height == pytest.approx(expected_h, abs=0.05)
    assert (stored.width / stored.height) == pytest.approx(
        expected_w / expected_h, rel=0.005
    ), "the cropped aspect ratio was not preserved"
    assert editor.undo_stack.count() == undo_before + 1, "not a single undo entry"
    assert panel.w_spin.value() == pytest.approx(stored.width, abs=0.01)
    assert panel.h_spin.value() == pytest.approx(stored.height, abs=0.01)
    panel.deleteLater()


def test_reset_crop_restores_the_frame(editor):
    item = _item(editor)
    start = (item.position_x, item.position_y, item.width, item.height)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "t"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    assert editor.layout.db.items.get(item.id).height < start[3]

    editor.select_all()
    editor.reset_crop()
    stored = editor.layout.db.items.get(item.id)
    assert (
        stored.crop_left,
        stored.crop_top,
        stored.crop_right,
        stored.crop_bottom,
    ) == (0.0, 0.0, 0.0, 0.0)
    assert stored.width == pytest.approx(start[2], abs=0.2)
    assert stored.height == pytest.approx(start[3], abs=0.2)
    assert stored.position_x == pytest.approx(start[0], abs=0.2)
    assert stored.position_y == pytest.approx(start[1], abs=0.2)


def test_drag_does_not_touch_the_item(editor):
    """While dragging only the pending rectangle moves (no repaint artefacts)."""
    item = _item(editor)
    start = (item.position_x, item.position_y, item.width, item.height)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "t"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.3))

    model = editor.scene.image_item(item.id).model
    assert (model.width, model.height) == pytest.approx((start[2], start[3])), (
        "the image was resized while dragging, which causes the ghost/shadow"
    )
    assert model.position_y == pytest.approx(start[1])
    assert model.crop_top == pytest.approx(0.0)
    assert overlay.pending_size_mm()[1] == pytest.approx(start[3] * 0.7, abs=0.5)
    # the stored row is untouched until the mouse is released
    stored = editor.layout.db.items.get(item.id)
    assert stored.crop_top == pytest.approx(0.0)


def test_escape_cancels_without_changing_anything(editor):
    item = _item(editor)
    start = (item.position_x, item.position_y, item.width, item.height)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "b"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.4))

    overlay.keyPressEvent(_Event(key=Qt.Key.Key_Escape))
    QApplication.processEvents()

    stored = editor.layout.db.items.get(item.id)
    assert stored.height == pytest.approx(start[3], abs=0.2)
    assert stored.position_y == pytest.approx(start[1], abs=0.2)
    assert stored.crop_bottom == pytest.approx(0.0, abs=0.001)
    assert editor.scene.crop_item_id is None, "crop mode was not left"


def test_crop_is_undoable(editor):
    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "r"
    overlay.mouseMoveEvent(_Event(item.width * 0.7, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    assert editor.layout.db.items.get(item.id).crop_right > 0.1

    editor.undo_stack.undo()
    QApplication.processEvents()
    assert editor.layout.db.items.get(item.id).crop_right == pytest.approx(0.0, abs=0.001)


def test_second_crop_pull_back_does_nothing(editor):
    """After cropping, dragging the handle outward again must not change anything."""
    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "t"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    cropped = editor.layout.db.items.get(item.id)

    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    # the pending rectangle starts at the current frame: pulling the top handle
    # above the frame is clamped, so nothing can be cropped "away" again
    overlay._mode = "t"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, -item.height * 0.5))
    assert overlay.pending_size_mm()[1] == pytest.approx(cropped.height, abs=0.2)
    overlay.mouseReleaseEvent(_Event())

    after = editor.layout.db.items.get(item.id)
    assert after.crop_top == pytest.approx(cropped.crop_top, abs=0.001)
    assert after.height == pytest.approx(cropped.height, abs=0.2)
    assert after.position_y == pytest.approx(cropped.position_y, abs=0.2)
    assert editor.scene.crop_item_id is None, "releasing must leave crop mode"


def test_reset_crop_while_cropping_closes_the_overlay(editor):
    """Reset while the overlay is open must not leave a stale rectangle behind."""
    item = _item(editor)
    start = (item.position_x, item.position_y, item.width, item.height)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "t"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    assert editor.layout.db.items.get(item.id).height < start[3]

    # open the crop overlay again, then reset from the menu
    editor.select_all()
    editor.start_crop()
    assert editor.scene.crop_item_id is not None
    editor.reset_crop()

    assert editor.scene.crop_item_id is None, "the stale overlay was left open"
    stored = editor.layout.db.items.get(item.id)
    assert (
        stored.crop_left,
        stored.crop_top,
        stored.crop_right,
        stored.crop_bottom,
    ) == (0.0, 0.0, 0.0, 0.0)
    assert stored.height == pytest.approx(start[3], abs=0.2)
    assert stored.position_y == pytest.approx(start[1], abs=0.2)


def test_enter_applies_the_crop(editor):
    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "t"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))

    overlay.keyPressEvent(_Event(key=Qt.Key.Key_Return))
    QApplication.processEvents()

    stored = editor.layout.db.items.get(item.id)
    assert stored.crop_top == pytest.approx(0.5, abs=0.01)
    assert editor.scene.crop_item_id is None, "Enter did not leave crop mode"


def test_document_preview_shows_the_cropped_region(editor):
    """Double-click preview must match the page (the crop), not the whole scan."""
    from app.viewer_dialog import ImageViewerDialog

    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())

    stored = editor.layout.db.items.get(item.id)
    document = editor.layout.documents()[stored.document_id]
    crop = stored.crop_pixels(document.original_width, document.original_height)
    assert crop[0] > 0, "the test needs a real crop"

    dialog = ImageViewerDialog(document, None, crop=crop)
    try:
        assert dialog._pixmap.width() == pytest.approx(crop[2] - crop[0], abs=2)
        assert dialog._pixmap.height() == pytest.approx(crop[3] - crop[1], abs=2)
        assert dialog.windowTitle().endswith("(cropped)")
    finally:
        dialog.deleteLater()

    # without a crop the whole scan is shown, as before
    full = ImageViewerDialog(document, None)
    try:
        assert full._pixmap.width() == pytest.approx(document.original_width, abs=2)
    finally:
        full.deleteLater()


def test_undo_restores_the_frame_as_well(editor):
    item = _item(editor)
    start = (item.position_x, item.position_y, item.width, item.height)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "t"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    assert editor.layout.db.items.get(item.id).height < start[3]

    editor.undo_stack.undo()
    QApplication.processEvents()

    stored = editor.layout.db.items.get(item.id)
    assert stored.crop_top == pytest.approx(0.0, abs=0.001)
    assert stored.height == pytest.approx(start[3], abs=0.2)
    assert stored.position_y == pytest.approx(start[1], abs=0.2)


def test_reset_crop_clears_stored_values(editor):
    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.2, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    assert editor.layout.db.items.get(item.id).crop_left > 0.1

    editor.select_all()
    editor.reset_crop()
    stored = editor.layout.db.items.get(item.id)
    assert (
        stored.crop_left,
        stored.crop_top,
        stored.crop_right,
        stored.crop_bottom,
    ) == (0.0, 0.0, 0.0, 0.0)


def _build_editor(qt_app, workspace, tmp_path, size, name):
    """A second editor with its own single scan (for size-specific cases)."""
    from app.layout_editor import LayoutEditor

    source = tmp_path / "sources" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (240, 240, 240)).save(source, format="JPEG", dpi=(300, 300))
    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)
    settings = SettingsService(workspace.project.folder / f"{name}_settings.json")
    settings.load()
    editor = LayoutEditor(workspace, settings)
    editor.resize(1000, 800)
    editor.show()
    QApplication.processEvents()
    editor.add_documents(list(editor.layout.documents().values()))
    QApplication.processEvents()
    return editor


def _source_page_point(model, fx: float, fy: float):
    """Page position of source fraction (fx, fy) while it is visible."""
    from core.layout_service import local_to_page

    left, top, right, bottom = model.crop_box()
    return local_to_page(
        model,
        (fx - left) / (right - left) * model.width,
        (fy - top) / (bottom - top) * model.height,
    )


def test_cropping_a_rotated_image_keeps_the_visible_content_in_place(editor):
    """Cropping a 90 degree image used to skip the position adjustment, so the
    remaining content slid 10.7 mm across the page."""
    item = _item(editor)
    item.rotation = 90.0
    editor.layout.save_item(item)
    editor.reload_current_page()
    QApplication.processEvents()
    item = _item(editor)
    frame_before = (item.width, item.height)
    anchor = _source_page_point(item, 0.6, 0.5)

    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.25, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())

    stored = editor.layout.db.items.get(item.id)
    assert stored.crop_left == pytest.approx(0.25, abs=0.01)
    assert stored.width == pytest.approx(frame_before[0] * 0.75, abs=0.2)
    assert stored.height == pytest.approx(frame_before[1], abs=0.2)
    moved = _source_page_point(stored, 0.6, 0.5)
    assert moved[0] == pytest.approx(anchor[0], abs=0.2), "the image jumped sideways"
    assert moved[1] == pytest.approx(anchor[1], abs=0.2), "the image jumped vertically"


def test_reset_crop_on_a_rotated_image_keeps_the_visible_content_in_place(editor):
    item = _item(editor)
    item.rotation = 90.0
    editor.layout.save_item(item)
    editor.reload_current_page()
    QApplication.processEvents()
    item = _item(editor)
    start_frame = (item.width, item.height)

    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.25, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())

    edited = editor.layout.db.items.get(item.id)
    anchor = _source_page_point(edited, 0.6, 0.5)
    editor.select_all()
    editor.reset_crop()
    stored = editor.layout.db.items.get(item.id)

    assert stored.crop_box() == pytest.approx((0.0, 0.0, 1.0, 1.0))
    assert stored.width == pytest.approx(start_frame[0], abs=0.2)
    assert stored.height == pytest.approx(start_frame[1], abs=0.2)
    moved = _source_page_point(stored, 0.6, 0.5)
    assert moved[0] == pytest.approx(anchor[0], abs=0.2)
    assert moved[1] == pytest.approx(anchor[1], abs=0.2)


def test_repeated_crops_keep_the_scale(editor):
    """Applying crop after crop must keep millimetres-per-source-pixel fixed."""
    item = _item(editor)
    editor.select_all()
    scales = []
    for mode, point in (("l", 0.25), ("t", 0.2)):
        editor.start_crop()
        item = _item(editor)
        overlay = _overlay(editor, item.id)
        overlay._mode = mode
        overlay.mouseMoveEvent(_Event(item.width * point, item.height * point))
        overlay.mouseReleaseEvent(_Event())
        stored = editor.layout.db.items.get(item.id)
        document = editor.layout.documents()[stored.document_id]
        left, top, right, bottom = stored.crop_box()
        visible_px_w = (right - left) * document.original_width
        visible_px_h = (bottom - top) * document.original_height
        assert stored.width / stored.height == pytest.approx(
            visible_px_w / visible_px_h, rel=0.01
        ), "the frame stopped following the crop"
        scales.append(stored.width / visible_px_w)
    assert scales[0] == pytest.approx(scales[1], rel=1e-4), "cropping changed the scale"


def test_restore_original_size_is_idempotent(editor):
    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.25, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())

    editor.select_all()
    editor.reset_selection_to_original_size()
    first = editor.layout.db.items.get(item.id)
    expected = (first.width, first.height, first.position_x, first.position_y)
    expected_crop = first.crop_box()
    editor.select_all()
    editor.reset_selection_to_original_size()
    second = editor.layout.db.items.get(item.id)
    assert (
        second.width,
        second.height,
        second.position_x,
        second.position_y,
    ) == pytest.approx(expected), "the second restore changed the image again"
    assert second.rotation == pytest.approx(first.rotation)
    assert second.crop_box() == pytest.approx(expected_crop)


def test_restore_fits_a_rotated_image_inside_the_page(qt_app, workspace, tmp_path):
    """A 260 mm tall scan turned 90 degrees used to be restored 260 mm *wide* on
    a 210 mm page; the visible box has to fit the printable area instead."""
    from core.layout_service import visual_bounds

    editor = _build_editor(qt_app, workspace, tmp_path, (1772, 3071), "tall.jpg")
    try:
        item = _item(editor)
        assert item.width < item.height, "the scan should be portrait before rotating"
        item.rotation = 90.0
        editor.layout.save_item(item)
        editor.reload_current_page()
        QApplication.processEvents()

        editor.select_all()
        editor.reset_selection_to_original_size()
        QApplication.processEvents()

        stored = editor.layout.db.items.get(item.id)
        page = editor.current_page()
        left, top, width, height = visual_bounds(stored)
        assert left >= -0.01 and top >= -0.01
        assert left + width <= page.width_mm + 0.01
        assert top + height <= page.height_mm + 0.01
        assert stored.width / stored.height == pytest.approx(150.0 / 260.0, rel=0.01)
        assert width / height == pytest.approx(260.0 / 150.0, rel=0.01)
    finally:
        editor.setParent(None)
        editor.deleteLater()


def test_undo_after_restore_puts_every_field_back(editor):
    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.25, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    # Distort the frame first, otherwise restoring the cropped region would be a
    # no-op and there would be no command to undo.
    editor.apply_item_geometry(item.id, {"width": 120.0})
    editor.select_all()
    editor.rotate_selection(absolute=90.0)
    QApplication.processEvents()

    before = editor.layout.db.items.get(item.id)
    expected = item_snapshot(before)
    undo_before = editor.undo_stack.count()
    editor.select_all()
    editor.reset_selection_to_original_size()
    assert editor.undo_stack.count() == undo_before + 1, "not one logical undo entry"

    editor.undo_stack.undo()
    QApplication.processEvents()
    restored = item_snapshot(editor.layout.db.items.get(item.id))
    for field in (
        "position_x",
        "position_y",
        "width",
        "height",
        "rotation",
        "crop_left",
        "crop_top",
        "crop_right",
        "crop_bottom",
    ):
        assert restored[field] == pytest.approx(expected[field], abs=1e-6), field


def test_crop_and_restore_leave_the_source_file_untouched(editor):
    import hashlib

    item = _item(editor)
    source = editor.layout.documents()[item.document_id].source_path
    before = hashlib.sha256(Path(source).read_bytes()).hexdigest()

    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.3, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    editor.select_all()
    editor.reset_selection_to_original_size()
    editor.select_all()
    editor.reset_crop()

    after = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    assert after == before, "the original scan was modified"


def test_pdf_export_matches_the_editor_for_a_cropped_rotated_image(editor):
    """The PDF image must land on the same millimetre box, with the crop's aspect."""
    pymupdf = pytest.importorskip("pymupdf")
    from core.layout_service import visual_bounds
    from core.pdf_service import PdfService

    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.25, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())
    editor.select_all()
    editor.rotate_selection(absolute=90.0)
    QApplication.processEvents()
    stored = editor.layout.db.items.get(item.id)

    target = Path(editor.layout.project.folder) / "crop_export.pdf"
    PdfService(editor.layout).export_project(target)
    pdf = pymupdf.open(target)
    try:
        page = pdf[0]
        images = page.get_images(full=True)
        assert len(images) == 1
        box = page.get_image_bbox(images[0])
        left, top, width, height = visual_bounds(stored)
        assert box.x0 / 72 * 25.4 == pytest.approx(left, abs=0.2)
        assert box.y0 / 72 * 25.4 == pytest.approx(top, abs=0.2)
        assert box.width / 72 * 25.4 == pytest.approx(width, abs=0.2)
        assert box.height / 72 * 25.4 == pytest.approx(height, abs=0.2)
        document = editor.layout.documents()[stored.document_id]
        crop_left, crop_top, crop_right, crop_bottom = stored.crop_box()
        visible_w = (crop_right - crop_left) * document.original_width
        visible_h = (crop_bottom - crop_top) * document.original_height
        assert images[0][2] / images[0][3] == pytest.approx(
            visible_h / visible_w, rel=0.01
        ), "the exported pixels do not match the visible crop"
    finally:
        pdf.close()


def test_crop_changes_the_exported_pixels(editor):
    """The stored crop must drive the pixel box used for PDF export."""
    from core.image_service import cropped_image

    item = _item(editor)
    editor.select_all()
    editor.start_crop()
    overlay = _overlay(editor, item.id)
    overlay._mode = "l"
    overlay.mouseMoveEvent(_Event(item.width * 0.5, item.height * 0.5))
    overlay.mouseReleaseEvent(_Event())

    stored = editor.layout.db.items.get(item.id)
    document = editor.layout.documents()[stored.document_id]
    box = stored.crop_pixels(document.original_width, document.original_height)
    assert box[0] > document.original_width * 0.4
    image = cropped_image(document.source_path, box)
    assert image.width < document.original_width * 0.6

