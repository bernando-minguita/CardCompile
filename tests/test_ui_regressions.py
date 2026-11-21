"""Regression tests for the UI bugs reported after 1.1.0.

These tests need a Qt application; they run with the offscreen platform so they
also work on a build server.
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

from PySide6.QtCore import QEvent, QPoint, QPointF  # noqa: E402
from PySide6.QtWidgets import QGraphicsSceneMouseEvent  # noqa: E402

from core.import_service import ImportService  # noqa: E402
from core.pdf_service import create_temp_pdf_path  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS  # noqa: E402
from models.document import Document  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def editor(qt_app, workspace, tmp_path):
    from app.layout_editor import LayoutEditor

    source = tmp_path / "sources" / "card.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1263, 798), (250, 250, 250))
    image.save(source, format="JPEG", dpi=(300, 300))

    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    editor = LayoutEditor(workspace, workspace_settings(workspace))
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents, original_size=True)
    yield editor
    editor.setParent(None)
    editor.deleteLater()


def workspace_settings(workspace):
    from core.settings_service import SettingsService

    settings = SettingsService(workspace.project.folder / "test_settings.json")
    settings.load()
    return settings


def test_context_menu_builds_with_original_size_entry(editor):
    item = editor.scene.image_items()[0]
    menu = editor._build_item_menu(item.item_id)
    assert menu is not None
    labels = [action.text() for action in menu.actions() if action.text()]
    assert "Restore original size" in labels
    assert "Crop..." in labels
    assert "Duplicate to page" in labels
    menu.deleteLater()


def test_context_signal_carries_a_qpoint(editor):
    captured: list[object] = []
    item = editor.scene.image_items()[0]
    # Detach the editor handler so the modal menu is not opened during the test.
    editor.scene.context_menu_requested.disconnect()
    editor.scene.context_menu_requested.connect(
        lambda _id, pos: captured.append(pos)
    )
    item.context_requested.emit(item.item_id, QPoint(12, 34))
    assert captured
    assert isinstance(captured[-1], QPoint)


def test_menu_position_helper_converts_to_qpoint():
    """QMenu.exec() raises TypeError when it receives a QPointF."""
    from app.layout_editor import menu_position

    point = menu_position(QPoint(5, 6))
    assert isinstance(point, QPoint)
    assert (point.x(), point.y()) == (5, 6)

    converted = menu_position(QPointF(7.4, 8.6))
    assert isinstance(converted, QPoint)
    assert (converted.x(), converted.y()) == (7, 9)


def test_original_size_setting_defaults_to_enabled(workspace):
    from core.settings_service import SettingsService

    settings = SettingsService(workspace.project.folder / "layout_settings.json")
    data = settings.load()
    assert data["layout"]["place_original_size"] is True
    assert data["layout"]["original_size_dpi"] == 300


def test_double_click_emits_preview_signal(editor):
    item = editor.scene.image_items()[0]
    hits: list[str] = []
    # Detach the editor handler that would open the modal image viewer.
    editor.scene.double_click_requested.disconnect()
    item.double_clicked.connect(hits.append)
    event = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMouseDoubleClick)
    event.setScenePos(QPointF(4.0, 4.0))
    event.setScreenPos(QPoint(4, 4))
    item.mouseDoubleClickEvent(event)
    assert hits == [item.item_id]


def test_tooltip_is_readable(qt_app):
    """Tooltips must not inherit a *local* widget style sheet.

    ``QToolBar`` containers used to carry ``setStyleSheet("background:#FFFFFF")``.
    Qt resolves a tooltip's style against the sheet of the widget that owns it, so
    the tooltip inherited the white background while keeping white text from the
    palette: a blank white box (measured: 658x43 px, zero dark pixels).
    """
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QToolButton, QToolTip, QVBoxLayout, QWidget

    from app.theme import apply_theme

    apply_theme(qt_app)
    host = QWidget()
    layout = QVBoxLayout(host)
    button = QToolButton()
    button.setText("1:1 Original size")
    button.setToolTip("Place newly added images at their real physical size")
    layout.addWidget(button)
    host.show()
    qt_app.processEvents()

    QToolTip.showText(
        button.mapToGlobal(button.rect().center()), button.toolTip(), button
    )
    label = None
    for _ in range(20):
        qt_app.processEvents()
        for widget in qt_app.topLevelWidgets():
            if widget.metaObject().className() == "QTipLabel" and widget.width() > 50:
                label = widget
        if label is not None:
            break
    try:
        assert label is not None, "the tooltip window was not created"
        QTest.qWait(400)  # let the unroll animation finish
        qt_app.processEvents()
        image = label.grab().toImage()
        assert image.width() > 50 and image.height() > 10
        dark = light = 0
        for y in range(image.height()):
            for x in range(image.width()):
                value = image.pixelColor(x, y).value()
                if value < 90:
                    dark += 1
                elif value > 200:
                    light += 1
        total = image.width() * image.height()
        assert dark > total * 0.5, f"tooltip background is not dark ({dark}/{total})"
        assert light > 0, "the tooltip text is not visible"
    finally:
        QToolTip.hideText()
        host.close()
        host.deleteLater()
        qt_app.processEvents()


def test_properties_panel_width_resizes_with_aspect_lock(editor):
    """Typing a new width with *Keep aspect ratio* on must actually resize.

    The panel pushes width *and* height; the height box still held the old value,
    so applying it right after the width put the old size back - reported as
    "increasing the width in the right panel is not working".
    """
    from app.properties_panel import PropertiesPanel

    panel = PropertiesPanel()
    panel.attach_editor(editor)
    try:
        editor.select_all()
        panel.refresh()
        assert panel.aspect_check.isChecked(), "images are aspect locked by default"

        model = editor.selected_models()[0]
        ratio = model.width / model.height
        new_width = model.width + 20.0

        panel.w_spin.setValue(new_width)  # emits valueChanged -> height follows
        assert panel.h_spin.value() == pytest.approx(new_width / ratio, abs=0.02)

        # the arrow steps apply immediately (like the rotation box) ...
        live = editor.layout.db.items.get(model.id)
        assert live.width == pytest.approx(new_width, abs=0.02)

        panel._apply_item_changes()  # ... and Enter/Tab applies again, unchanged

        stored = editor.layout.db.items.get(model.id)
        assert stored.width == pytest.approx(new_width, abs=0.02)
        assert stored.height == pytest.approx(new_width / ratio, abs=0.05)
        graphic = editor.scene.image_item(model.id)
        assert graphic.model.width == pytest.approx(new_width, abs=0.02)
    finally:
        panel.deleteLater()


def test_properties_panel_height_resizes_with_aspect_lock(editor):
    from app.properties_panel import PropertiesPanel

    panel = PropertiesPanel()
    panel.attach_editor(editor)
    try:
        editor.select_all()
        panel.refresh()
        model = editor.selected_models()[0]
        ratio = model.width / model.height
        new_height = model.height + 10.0

        panel.h_spin.setValue(new_height)
        panel._apply_item_changes()

        stored = editor.layout.db.items.get(model.id)
        assert stored.height == pytest.approx(new_height, abs=0.02)
        assert stored.width == pytest.approx(new_height * ratio, abs=0.05)
    finally:
        panel.deleteLater()


def test_properties_panel_size_fields_are_independent_without_lock(editor):
    """With the lock off the two fields must stay independent."""
    from app.properties_panel import PropertiesPanel

    panel = PropertiesPanel()
    panel.attach_editor(editor)
    try:
        editor.select_all()
        model = editor.selected_models()[0]
        old_width, old_height = model.width, model.height
        panel.aspect_check.setChecked(False)
        panel.refresh()
        assert not panel.aspect_check.isChecked()

        panel.w_spin.setValue(old_width + 15.0)
        assert panel.h_spin.value() == pytest.approx(old_height, abs=0.02), (
            "the height field followed the width although the lock is off"
        )
        panel._apply_item_changes()

        stored = editor.layout.db.items.get(model.id)
        assert stored.width == pytest.approx(old_width + 15.0, abs=0.02)
        assert stored.height == pytest.approx(old_height, abs=0.02)
    finally:
        panel.deleteLater()


def test_geometry_ignores_a_stale_partner_size(editor):
    """A stale height must never cancel a width change (order independent)."""
    editor.select_all()
    model = editor.selected_models()[0]
    old_width, old_height = model.width, model.height
    ratio = old_width / old_height

    editor.apply_item_geometry(
        model.id, {"width": old_width + 25.0, "height": old_height}
    )
    stored = editor.layout.db.items.get(model.id)
    assert stored.width == pytest.approx(old_width + 25.0, abs=0.02)
    assert stored.height == pytest.approx((old_width + 25.0) / ratio, abs=0.05)

    # and the other way round: a stale width must not cancel a height change
    editor.apply_item_geometry(
        model.id, {"width": stored.width, "height": stored.height + 10.0}
    )
    grown = editor.layout.db.items.get(model.id)
    assert grown.height == pytest.approx(stored.height + 10.0, abs=0.02)
    assert grown.width == pytest.approx((stored.height + 10.0) * ratio, abs=0.05)


def test_geometry_stretches_when_the_lock_is_off(editor):
    editor.select_all()
    model = editor.selected_models()[0]
    old_width, old_height = model.width, model.height
    editor.set_aspect_locked(False)
    editor.apply_item_geometry(
        model.id, {"width": old_width + 30.0, "height": old_height}
    )
    stored = editor.layout.db.items.get(model.id)
    assert stored.width == pytest.approx(old_width + 30.0, abs=0.02)
    assert stored.height == pytest.approx(old_height, abs=0.02)


def test_crash_logging_is_installed(qt_app):
    """Native crashes never reach ``sys.excepthook``; faulthandler must log them.

    The 1.3.10 report ("clicked *One document per page*, the app exited") left an
    application log that simply stopped: no traceback, no "Exiting with code 0".
    ``logs/crash.log`` now records the C level stack for exactly that case.
    """
    from core import paths

    from app.application import _install_crash_logging

    _install_crash_logging()
    path = paths.logs_dir() / "crash.log"
    assert path.is_file(), "crash.log was not created"
    text = path.read_text(encoding="utf-8", errors="ignore")
    assert "starting" in text


def test_app_widgets_do_not_set_local_style_sheets():
    """Only the application style sheet may be used.

    A widget level ``setStyleSheet()`` is inherited by that widget's tooltips and
    menus, which is how the tooltips lost their dark background. Styling must go
    through object names defined in ``resources/styles/app.qss``.
    """
    import re

    app_dir = Path(__file__).resolve().parent.parent / "app"
    call = re.compile(r"(?<![\w.])(\w+)\.setStyleSheet\(")
    offenders: list[str] = []
    for path in sorted(app_dir.glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            for match in call.finditer(code):
                if match.group(1) != "app":  # app.setStyleSheet() is the theme itself
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "local style sheets found:\n" + "\n".join(offenders)


def test_temp_preview_path_is_deletable():
    """mkstemp leaves an open handle on Windows which blocked deletion."""
    path = create_temp_pdf_path("dpb_test_")
    try:
        path.write_bytes(b"%PDF-1.4 test")
        assert path.read_bytes().startswith(b"%PDF")
        path.unlink(missing_ok=True)
        assert not path.exists()
    finally:
        path.unlink(missing_ok=True)


def test_document_without_dpi_uses_fallback():
    document = Document(
        id="d",
        project_id="p",
        filename="a.jpg",
        source_path="C:/a.jpg",
        file_hash="h",
        original_width=600,
        original_height=400,
    )
    width, height = document.natural_size_mm(300)
    assert round(width, 1) == 50.8
    assert round(height, 1) == 33.9


def test_missing_source_is_reported_when_adding_selected_images(
    qt_app, workspace, tmp_path, monkeypatch
):
    """A missing file must be named, not silently filtered out.

    Adding the library selection to new pages used to drop missing documents
    without a word; when all of them were missing the only feedback was
    "Select the images ... first" while they were visibly selected.
    """
    from app.editor_workspace import EditorWorkspace
    from core.import_service import ImportService
    from core.settings_service import SettingsService, SUPPORTED_IMAGE_EXTENSIONS

    source = tmp_path / "gone.jpg"
    Image.new("RGB", (600, 400), (200, 120, 120)).save(
        source, format="JPEG", dpi=(300, 300)
    )
    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(tmp_path / "ui_settings.json")
    settings.load()

    class _Context:
        pass

    context = _Context()
    context.settings = settings
    surface = EditorWorkspace(context)
    surface.load_workspace(workspace)
    try:
        document = list(surface.editor.layout.documents().values())[0]
        source.unlink()

        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "app.editor_workspace.QMessageBox.warning",
            lambda _parent, title, text: warnings.append((title, text)),
        )
        statuses: list[str] = []
        surface.status_message.connect(statuses.append)
        surface.organizer.selected_ids = lambda: [document.id]

        pages_before = len(surface.editor.pages())
        surface._run_add_to_new_pages("1")
        qt_app.processEvents()

        assert warnings, "adding a missing source produced no warning"
        assert document.filename in warnings[0][1]
        assert len(surface.editor.pages()) == pages_before, "pages were added anyway"
        assert any("missing" in message.lower() for message in statuses), statuses
    finally:
        surface.close_workspace()
        surface.deleteLater()
        qt_app.processEvents()


def test_editor_labels_fit_the_smallest_window(qt_app, workspace, tmp_path):
    """Real-font clipping found by the manual Windows smoke check.

    * the page navigator was 150 px wide, cutting its caption to "A4 Po...";
    * "Duplicate page"/"Delete page" were elided inside the 290 px panel;
    * the dropdown arrow of the Arrange/Align buttons covered the last letter.
    """
    from app.editor_workspace import EditorWorkspace
    from core.settings_service import SettingsService

    settings = SettingsService(tmp_path / "fit_settings.json")
    settings.load()

    class _Context:
        pass

    context = _Context()
    context.settings = settings
    surface = EditorWorkspace(context)
    surface.resize(1280, 900)
    surface.show()
    qt_app.processEvents()
    try:
        assert surface.navigator.minimumWidth() >= 200, (
            "the page navigator minimum width clips its caption"
        )
        # The three page buttons share a 290 px panel; the long labels were
        # elided even at 1920 px.  (Pixel widths are font dependent, so assert
        # the labels and their tooltips instead of a measured width.)
        assert surface.properties.duplicate_page_button.text() == "Duplicate"
        assert surface.properties.delete_page_button.text() == "Delete"
        for button in (
            surface.properties.duplicate_page_button,
            surface.properties.delete_page_button,
        ):
            assert "page" in button.toolTip().lower()
        for button in (surface.layout_button, surface.align_button):
            assert button.objectName() == "MenuToolButton"
            text_width = button.fontMetrics().horizontalAdvance(button.text())
            assert button.sizeHint().width() >= text_width + 16, (
                f"{button.text()!r} leaves no room for its menu arrow"
            )
    finally:
        surface.close_workspace()
        surface.deleteLater()
        qt_app.processEvents()


def test_removing_a_placed_image_from_the_library_keeps_the_canvas(
    qt_app, workspace, tmp_path, monkeypatch
):
    """The reported bug: library delete made the placed image disappear.

    The confirm dialog used to warn that page placements would be removed, and
    the delete cascaded through ``page_items.document_id``.
    """
    from app.editor_workspace import EditorWorkspace
    from core.import_service import ImportService
    from core.settings_service import SettingsService, SUPPORTED_IMAGE_EXTENSIONS

    sources = [
        Image.new("RGB", (600, 400), colour).save(
            tmp_path / f"keep_{index}.jpg", format="JPEG", dpi=(300, 300)
        )
        or (tmp_path / f"keep_{index}.jpg")
        for index, colour in enumerate(((220, 180, 140), (140, 180, 220)))
    ]
    importer = ImportService(workspace)
    previews = importer.analyze(sources, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(tmp_path / "library_settings.json")
    settings.load()

    class _Context:
        pass

    context = _Context()
    context.settings = settings
    surface = EditorWorkspace(context)
    surface.load_workspace(workspace)
    try:
        documents = list(surface.editor.layout.documents().values())
        surface.editor_workspace = surface
        surface._add_documents_to_page([doc.id for doc in documents])
        qt_app.processEvents()
        placed_before = len(surface.editor.scene.image_items())
        assert placed_before == 2

        monkeypatch.setattr(
            "app.organizer_widget.QMessageBox.question",
            lambda *args, **kwargs: __import__(
                "PySide6.QtWidgets", fromlist=["QMessageBox"]
            ).QMessageBox.StandardButton.Yes,
        )
        surface.organizer.selected_ids = lambda: [documents[0].id]
        surface.organizer._remove_selected()
        qt_app.processEvents()

        assert len(surface.editor.scene.image_items()) == placed_before, (
            "deleting a library entry removed its placed canvas image"
        )
        assert {item.model.document_id for item in surface.editor.scene.image_items()} == {
            documents[0].id,
            documents[1].id,
        }
        assert surface.organizer.list.count() == 1, "library entry was not removed"
    finally:
        surface.close_workspace()
        surface.deleteLater()
        qt_app.processEvents()
