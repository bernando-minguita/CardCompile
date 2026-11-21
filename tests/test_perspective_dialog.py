"""The Perspective Correction dialog: wiring, gating, apply/cancel and panning.

The dialog replaced the inline four-corner overlay, which was unusable on a
card placed at 54 mm.  These tests cover the parts that can regress silently:
the dialog must load a source, must round-trip the quad and the tone values, the
editor must write nothing when the user cancels, and a zoomed photo must be
pannable without changing the zoom.
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

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.perspective_dialog import (  # noqa: E402
    IDENTITY_QUAD,
    PerspectiveDialog,
    PreviewPane,
    SourceCanvas,
)
from core.image_service import NEUTRAL  # noqa: E402


@pytest.fixture()
def source_image(tmp_path):
    """A plain card-like photo on disk."""
    path = tmp_path / "card.png"
    Image.new("RGB", (600, 400), (232, 228, 220)).save(path)
    return path


def test_dialog_loads_a_source_and_shows_the_canvas(qt_app, source_image):
    dialog = PerspectiveDialog()
    try:
        dialog.set_source(str(source_image), IDENTITY_QUAD, {})
        assert dialog.canvas.has_image()
        assert dialog.quad() == IDENTITY_QUAD
        assert dialog.apply_button.isEnabled()
    finally:
        dialog.done(0)


def test_dialog_round_trips_the_quad(qt_app, source_image):
    """The quad the user set is the quad the caller reads back."""
    quad = (0.05, 0.04, 0.95, 0.0, 1.0, 0.96, 0.0, 1.0)
    dialog = PerspectiveDialog()
    try:
        dialog.set_source(str(source_image), quad, {})
        returned = dialog.quad()
        for expected, actual in zip(quad, returned):
            assert actual == pytest.approx(expected, abs=0.01)
    finally:
        dialog.done(0)


def test_dialog_round_trips_the_tone_values(qt_app, source_image):
    values = {
        "brightness": 1.25,
        "contrast": 1.1,
        "saturation": 0.9,
        "sharpness": 1.3,
        "shadow": True,
    }
    dialog = PerspectiveDialog()
    try:
        dialog.set_source(str(source_image), IDENTITY_QUAD, values)
        returned = dialog.values()
        assert returned["brightness"] == pytest.approx(1.25, abs=0.02)
        assert returned["contrast"] == pytest.approx(1.1, abs=0.02)
        assert returned["saturation"] == pytest.approx(0.9, abs=0.02)
        assert returned["sharpness"] == pytest.approx(1.3, abs=0.02)
        assert returned["shadow"] is True
    finally:
        dialog.done(0)


def test_dialog_starts_neutral_when_given_no_values(qt_app, source_image):
    dialog = PerspectiveDialog()
    try:
        dialog.set_source(str(source_image), IDENTITY_QUAD, {})
        values = dialog.values()
        for key in ("brightness", "contrast", "saturation", "sharpness"):
            assert values[key] == pytest.approx(NEUTRAL, abs=0.01)
        assert values["shadow"] is False
    finally:
        dialog.done(0)


def test_dialog_reset_corners_restores_the_identity(qt_app, source_image):
    skewed = (0.1, 0.1, 0.9, 0.0, 1.0, 0.9, 0.0, 1.0)
    dialog = PerspectiveDialog()
    try:
        dialog.set_source(str(source_image), skewed, {})
        assert dialog.quad() != IDENTITY_QUAD
        dialog.reset_corners()
        assert dialog.quad() == IDENTITY_QUAD
    finally:
        dialog.done(0)


def test_dialog_reset_lighting_restores_neutral(qt_app, source_image):
    dialog = PerspectiveDialog()
    try:
        dialog.set_source(
            str(source_image),
            IDENTITY_QUAD,
            {"brightness": 1.5, "contrast": 1.4, "shadow": True},
        )
        dialog.reset_lighting()
        values = dialog.values()
        assert values["brightness"] == pytest.approx(NEUTRAL, abs=0.01)
        assert values["contrast"] == pytest.approx(NEUTRAL, abs=0.01)
        assert values["shadow"] is False
    finally:
        dialog.done(0)


def test_dialog_reports_an_unreadable_source(qt_app, tmp_path):
    """A missing file must not crash the dialog or enable Apply."""
    dialog = PerspectiveDialog()
    try:
        dialog.set_source(str(tmp_path / "missing.png"), IDENTITY_QUAD, {})
        assert not dialog.canvas.has_image()
        assert not dialog.apply_button.isEnabled()
        assert dialog.status_label.text()
    finally:
        dialog.done(0)


def test_canvas_quad_is_normalised(qt_app, source_image):
    """Whatever the handles do, the reported quad stays inside 0..1."""
    from PySide6.QtGui import QPixmap

    canvas = SourceCanvas()
    canvas.set_image(QPixmap(str(source_image)), IDENTITY_QUAD)
    quad = canvas.quad()
    assert len(quad) == 8
    assert all(0.0 <= value <= 1.0 for value in quad)


def test_preview_pane_toggles_comparison(qt_app):
    pane = PreviewPane()
    assert not pane.is_comparing()
    pane.set_compare(True)
    assert pane.is_comparing()
    pane.set_compare(False)
    assert not pane.is_comparing()


def test_editor_routes_both_legacy_entry_points_to_the_dialog():
    """Old menu hooks must not raise; they open the dialog instead."""
    from app.layout_editor import LayoutEditor

    assert hasattr(LayoutEditor, "open_perspective_dialog")
    assert hasattr(LayoutEditor, "start_perspective")
    assert hasattr(LayoutEditor, "open_adjustments_dialog")


def test_inline_perspective_overlay_is_gone():
    """The old scene overlay must no longer exist as a module."""
    assert not (ROOT / "graphics" / "perspective_overlay.py").exists()
    assert not (ROOT / "app" / "adjustments_dialog.py").exists()


# ------------------------------------------------------------ drag to pan
def _canvas_with_photo(qt_app, tmp_path, size=(1200, 900)):
    """A shown, fitted SourceCanvas holding a real photo."""
    from PySide6.QtGui import QPixmap

    from app.perspective_dialog import IDENTITY_QUAD, SourceCanvas

    path = tmp_path / "photo.png"
    Image.new("RGB", size, (200, 200, 200)).save(path)
    canvas = SourceCanvas()
    canvas.resize(400, 300)
    canvas.show()
    QApplication.processEvents()
    canvas.set_image(QPixmap(str(path)), IDENTITY_QUAD)
    QApplication.processEvents()
    canvas.fit()
    QApplication.processEvents()
    return canvas


def _mouse(kind, pos, button=Qt.MouseButton.LeftButton, buttons=None):
    if buttons is None:
        buttons = (
            Qt.MouseButton.NoButton
            if kind == QMouseEvent.Type.MouseButtonRelease
            else Qt.MouseButton.LeftButton
        )
    return QMouseEvent(
        kind, pos, pos.toPoint(), button, buttons, Qt.KeyboardModifier.NoModifier
    )


def test_fitted_photo_does_not_need_panning(qt_app, tmp_path):
    canvas = _canvas_with_photo(qt_app, tmp_path)
    try:
        assert not canvas.is_zoomed_in()
    finally:
        canvas.deleteLater()


def test_zoomed_photo_can_be_panned(qt_app, tmp_path):
    """The reported request: zoom in, then drag to see other parts."""
    canvas = _canvas_with_photo(qt_app, tmp_path)
    try:
        canvas.scale(3.0, 3.0)
        canvas._user_zoomed = True
        QApplication.processEvents()
        assert canvas.is_zoomed_in()

        before = (
            canvas.horizontalScrollBar().value(),
            canvas.verticalScrollBar().value(),
        )
        canvas.mousePressEvent(
            _mouse(QMouseEvent.Type.MouseButtonPress, QPointF(200, 150))
        )
        assert canvas._panning is True
        canvas.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, QPointF(120, 90)))
        canvas.mouseReleaseEvent(
            _mouse(
                QMouseEvent.Type.MouseButtonRelease,
                QPointF(120, 90),
                Qt.MouseButton.NoButton,
            )
        )
        after = (
            canvas.horizontalScrollBar().value(),
            canvas.verticalScrollBar().value(),
        )
        assert after != before, "the view did not move"
        assert canvas._panning is False
    finally:
        canvas.deleteLater()


def test_panning_never_changes_the_zoom(qt_app, tmp_path):
    """Dragging must only scroll; the zoom level has to stay put."""
    canvas = _canvas_with_photo(qt_app, tmp_path)
    try:
        canvas.scale(3.0, 3.0)
        canvas._user_zoomed = True
        QApplication.processEvents()
        zoom_before = canvas.zoom()

        canvas.mousePressEvent(
            _mouse(QMouseEvent.Type.MouseButtonPress, QPointF(200, 150))
        )
        for pos in (QPointF(180, 130), QPointF(140, 100), QPointF(100, 70)):
            canvas.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, pos))
        canvas.mouseReleaseEvent(
            _mouse(
                QMouseEvent.Type.MouseButtonRelease,
                QPointF(100, 70),
                Qt.MouseButton.NoButton,
            )
        )
        assert canvas.zoom() == zoom_before, "panning changed the zoom"
    finally:
        canvas.deleteLater()


def test_corner_handle_press_does_not_pan(qt_app, tmp_path):
    """A press on a corner must drag the corner, never the photo."""
    canvas = _canvas_with_photo(qt_app, tmp_path)
    try:
        canvas.scale(3.0, 3.0)
        canvas._user_zoomed = True
        QApplication.processEvents()
        handle = canvas._handles["tl"]
        handle_view = canvas.mapFromScene(handle.pos())
        canvas.mousePressEvent(
            _mouse(QMouseEvent.Type.MouseButtonPress, QPointF(handle_view))
        )
        assert canvas._panning is False, "pressing a corner handle started panning"
    finally:
        canvas.deleteLater()


def test_panning_is_unavailable_when_fitted(qt_app, tmp_path):
    """With the whole photo visible there is nothing to pan to."""
    canvas = _canvas_with_photo(qt_app, tmp_path)
    try:
        canvas.mousePressEvent(
            _mouse(QMouseEvent.Type.MouseButtonPress, QPointF(200, 150))
        )
        assert canvas._panning is False
    finally:
        canvas.deleteLater()


def test_resize_keeps_a_user_zoom(qt_app, tmp_path):
    """Resizing the dialog must not throw away the zoom the user chose."""
    from PySide6.QtGui import QResizeEvent

    canvas = _canvas_with_photo(qt_app, tmp_path)
    try:
        canvas.scale(3.0, 3.0)
        canvas._user_zoomed = True
        zoom_before = canvas.zoom()
        canvas.resizeEvent(QResizeEvent(canvas.size(), canvas.size()))
        assert canvas.zoom() == zoom_before
    finally:
        canvas.deleteLater()



# ------------------------------------------ editor -> dialog integration
def _editor_with_placed_image(qt_app, workspace, tmp_path):
    """Build a LayoutEditor with one imported image placed on the page."""
    from app.layout_editor import LayoutEditor
    from core.import_service import ImportService
    from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService

    source = tmp_path / "sources" / "card.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (360, 226), (240, 238, 232)).save(source, format="JPEG")

    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(workspace.project.folder / "editor_settings.json")
    settings.load()
    settings.set("layout", "snap_small_cards", False)
    editor = LayoutEditor(workspace, settings)
    editor.resize(1000, 800)
    editor.show()

    document = workspace.database.documents.list_for_project(workspace.id)[0]
    editor.add_documents([document])
    QApplication.processEvents()
    editor.select_all()
    QApplication.processEvents()
    return editor, document


def test_open_perspective_dialog_does_not_raise(qt_app, workspace, tmp_path, monkeypatch):
    """Right-click -> Perspective Correction must open, not raise AttributeError.

    ``Document.source_path`` is a ``str``, but the editor called
    ``.exists()`` on it.  Every other call site uses the ``Document.exists``
    property; this one did not, so the dialog died before it was built and the
    user saw "AttributeError: 'str' object has no attribute 'exists'".
    """
    from app import perspective_dialog as dialog_module

    editor, document = _editor_with_placed_image(qt_app, workspace, tmp_path)
    try:
        # The document really is a str path, which is what broke the check.
        assert isinstance(document.source_path, str)
        assert document.exists is True

        captured = {}

        class FakeDialog:
            """Stand-in that records the call instead of blocking on exec()."""

            def __init__(self, parent=None):
                captured["parent"] = parent
                captured["path"] = None

            def set_source(self, path, quad, values):
                captured["path"] = path
                captured["quad"] = quad
                captured["values"] = values

            def exec(self):
                return 0  # Cancel: nothing is written

            def quad(self):
                return captured["quad"]

            def values(self):
                return captured["values"]

        monkeypatch.setattr(dialog_module, "PerspectiveDialog", FakeDialog)
        editor.open_perspective_dialog()

        assert captured["path"] == document.source_path
        assert isinstance(captured["path"], str)
    finally:
        editor.setParent(None)
        editor.deleteLater()


def test_open_perspective_dialog_reports_a_missing_source(
    qt_app, workspace, tmp_path, monkeypatch
):
    """A deleted original must be reported, not crash the editor."""
    from app import perspective_dialog as dialog_module

    editor, document = _editor_with_placed_image(qt_app, workspace, tmp_path)
    try:
        Path(document.source_path).unlink()

        opened = {"count": 0}

        class NeverOpened:
            def __init__(self, parent=None):
                opened["count"] += 1

        monkeypatch.setattr(dialog_module, "PerspectiveDialog", NeverOpened)
        messages = []
        editor.status_message.connect(messages.append)
        editor.open_perspective_dialog()

        assert opened["count"] == 0, "the dialog opened for a missing file"
        assert any("missing" in text.lower() for text in messages)
    finally:
        editor.setParent(None)
        editor.deleteLater()

