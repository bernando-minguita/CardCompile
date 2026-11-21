"""Text label tests: storage, editor integration and PDF output."""

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

import pymupdf  # noqa: E402

from core.import_service import ImportService  # noqa: E402
from core.pdf_service import ExportOptions, PdfService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService  # noqa: E402
from models.page_label import PageLabel  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def editor(qt_app, workspace, tmp_path):
    from app.layout_editor import LayoutEditor

    source = tmp_path / "sources" / "card.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 260), (250, 250, 250)).save(source, format="JPEG")
    importer = ImportService(workspace)
    previews = importer.analyze([source], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(workspace.project.folder / "label_settings.json")
    settings.load()
    editor = LayoutEditor(workspace, settings)
    editor.resize(900, 700)
    yield editor
    editor.setParent(None)
    editor.deleteLater()


def test_schema_has_label_table(workspace):
    from database.connection import CURRENT_SCHEMA_VERSION

    assert workspace.database.db.schema_version() == CURRENT_SCHEMA_VERSION
    row = workspace.database.db.query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='page_labels'"
    )
    assert row is not None


def test_label_repository_roundtrip(workspace):
    page = workspace.database.pages.list_for_project(workspace.id)[0]
    label = PageLabel(page_id=page.id, text="Hello", font_size=14.0, bold=True)
    workspace.database.labels.insert(label)

    stored = workspace.database.labels.get(label.id)
    assert stored is not None
    assert stored.text == "Hello"
    assert stored.font_size == 14.0
    assert stored.bold is True

    stored.text = "Changed"
    workspace.database.labels.update(stored)
    assert workspace.database.labels.get(label.id).text == "Changed"

    workspace.database.labels.delete(label.id)
    assert workspace.database.labels.get(label.id) is None


def test_editor_adds_moves_and_deletes_a_label(editor):
    label_id = editor.create_label(
        {"text": "Passport copy", "font_size": 16.0, "color": "#FF0000"},
        position=(30.0, 40.0),
    )
    assert label_id
    stored = editor.layout.db.labels.get(label_id)
    assert stored.text == "Passport copy"
    assert editor.scene.label_by_id(label_id) is not None

    graphic = editor.scene.label_by_id(label_id)
    graphic.interaction_started.emit(label_id)
    graphic.model.position_x = 55.5
    graphic.model.position_y = 66.5
    graphic.interaction_finished.emit(label_id)
    QApplication.processEvents()
    assert editor.layout.db.labels.get(label_id).position_x == pytest.approx(55.5)

    editor.undo_stack.undo()
    QApplication.processEvents()
    assert editor.layout.db.labels.get(label_id).position_x == pytest.approx(30.0)

    graphic = editor.scene.label_by_id(label_id)
    graphic.setSelected(True)
    editor.delete_selection()
    assert editor.layout.db.labels.get(label_id) is None
    editor.undo_stack.undo()
    assert editor.layout.db.labels.get(label_id) is not None


def test_refreshing_a_label_does_not_rotate_it_twice(qt_app):
    """``paint`` already rotates the text, so the item transform must stay flat.

    ``sync_from_model`` used to call ``setRotation`` as well, so every refreshed
    label was rotated twice (90 degrees rendered upside down).
    """
    from graphics.text_item import PageTextItem

    item = PageTextItem(PageLabel(page_id="p1", text="HI", rotation=90.0))
    item.sync_from_model()
    assert item.rotation() == 0.0
    assert item.sceneTransform().m12() == 0.0


def test_duplicating_a_page_copies_its_labels(workspace):
    from core.layout_service import LayoutService

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    workspace.database.labels.insert(PageLabel(page_id=page.id, text="Copy me"))
    copy = layout.duplicate_page(page.id)
    assert copy is not None
    texts = [label.text for label in workspace.database.labels.list_for_page(copy.id)]
    assert texts == ["Copy me"]


def test_deleting_the_page_removes_its_labels(workspace):
    from core.layout_service import LayoutService

    layout = LayoutService(workspace)
    second = layout.add_page()
    workspace.database.labels.insert(PageLabel(page_id=second.id, text="Bye"))
    assert layout.delete_page(second.id) is True
    assert workspace.database.labels.list_for_page(second.id) == []


def test_label_is_written_to_the_pdf(workspace, tmp_path):
    from core.layout_service import LayoutService

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    workspace.database.labels.insert(
        PageLabel(
            page_id=page.id,
            text="IQAMA 7654",
            position_x=20.0,
            position_y=25.0,
            font_size=18.0,
            bold=True,
            color="#0000FF",
        )
    )
    output = tmp_path / "labels.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))
    assert output.is_file()

    document = pymupdf.open(output)
    try:
        text = document[0].get_text()
        assert "IQAMA 7654" in text
        fonts = document[0].get_fonts()
        assert fonts, "the label should reference a font"
        assert any("Helvetica-Bold" in (item[3] or "") for item in fonts)
    finally:
        document.close()


def test_rotated_item_stays_centred_in_the_pdf(editor, tmp_path):
    """The canvas rotates around the item centre - the PDF must do the same."""
    layout = editor.layout
    page = layout.pages()[0]
    document = list(layout.documents().values())[0]
    item = layout.create_item(document, page, width=80.0, height=40.0)
    item.position_x = 50.0
    item.position_y = 100.0
    item.rotation = 38.0
    layout.insert_item(item)

    output = tmp_path / "rotated.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))

    pdf = pymupdf.open(output)
    try:
        pdf_page = pdf[0]
        images = pdf_page.get_images(full=True)
        assert images
        bbox = pdf_page.get_image_bbox(images[0])
        mm = 25.4 / 72.0
        centre_x = (bbox.x0 + bbox.x1) / 2.0 * mm
        # PyMuPDF shares the canvas origin (top-left, y downwards).
        centre_y = (bbox.y0 + bbox.y1) / 2.0 * mm
        assert centre_x == pytest.approx(90.0, abs=1.0)   # 50 + 80/2
        assert centre_y == pytest.approx(120.0, abs=1.0)  # 100 + 40/2
    finally:
        pdf.close()


def test_rotated_label_is_exported_oriented(editor, tmp_path):
    """A label stored with rotation must be drawn rotated in the PDF."""
    layout = editor.layout
    page = layout.pages()[0]
    layout.db.labels.insert(
        PageLabel(
            page_id=page.id,
            text="Rotated note",
            position_x=20.0,
            position_y=30.0,
            font_size=12.0,
            rotation=45.0,
        )
    )
    output = tmp_path / "rotated_label.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))

    pdf = pymupdf.open(output)
    try:
        pdf_page = pdf[0]
        text = pdf_page.get_text()
        assert "Rotated note" in text

        # The drawn text must actually be rotated: a collapsed matrix left every
        # label horizontal, so the writing direction would stay (1, 0).
        lines = [
            line
            for block in pdf_page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            if "Rotated note" in "".join(s["text"] for s in line.get("spans", []))
        ]
        assert lines, "the label line must be present"
        direction = lines[0].get("dir", (1.0, 0.0))
        assert direction[1] == pytest.approx(0.7071, abs=0.02), (
            f"label must be rotated by 45 degrees, direction={direction}"
        )
        assert direction[0] == pytest.approx(0.7071, abs=0.02), (
            f"label must be rotated by 45 degrees, direction={direction}"
        )
    finally:
        pdf.close()


def test_vertical_position_is_not_mirrored(editor, tmp_path):
    """An image at the top of the canvas must be at the top of the PDF."""
    layout = editor.layout
    page = layout.pages()[0]
    document = list(layout.documents().values())[0]
    top_item = layout.create_item(document, page, width=80.0, height=30.0)
    top_item.position_x = 20.0
    top_item.position_y = 10.0        # near the top edge
    layout.insert_item(top_item)

    output = tmp_path / "flip.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))

    pdf = pymupdf.open(output)
    try:
        pdf_page = pdf[0]
        bbox = pdf_page.get_image_bbox(pdf_page.get_images(full=True)[0])
        mm = 25.4 / 72.0
        top_mm = bbox.y0 * mm
        bottom_mm = bbox.y1 * mm
        # 10 mm from the top, 30 mm tall -> 10..40 mm from the top edge.
        assert top_mm == pytest.approx(10.0, abs=1.0), f"image starts at {top_mm:.1f} mm"
        assert bottom_mm == pytest.approx(40.0, abs=1.0), f"image ends at {bottom_mm:.1f} mm"
    finally:
        pdf.close()


def test_label_font_family_is_shared_by_the_canvas_and_the_pdf():
    """The canvas must draw the family the PDF writer maps, legacy names included."""
    from core.pdf_service import PdfService
    from models.page_label import FONT_CHOICES, PageLabel, canonical_font_family

    assert set(FONT_CHOICES) == {"Arial", "Times New Roman", "Courier New"}, (
        "the dialog must offer families Qt can resolve on Windows"
    )
    for legacy, modern in (
        ("Helvetica", "Arial"),
        ("Times-Roman", "Times New Roman"),
        ("Courier", "Courier New"),
    ):
        assert canonical_font_family(legacy) == modern
        old = PageLabel(page_id="p1", font_family=legacy)
        new = PageLabel(page_id="p1", font_family=modern)
        assert PdfService._font_name(old) == PdfService._font_name(new), (
            f"{legacy} and {modern} must export the same face"
        )


def test_label_box_height_comes_from_the_shared_font_metrics(qt_app):
    """The canvas box and the PDF box must use one ascent/descent table.

    Qt's ascent (0.909 em for Arial) and PyMuPDF's ``Font.ascender`` (1.075 em
    for Helvetica) disagree by ~0.8 mm at 12 pt, which put exported labels lower
    than the editor showed them.
    """
    from graphics.text_item import PageTextItem
    from models.page_label import (
        FONT_CHOICES,
        PageLabel,
        font_ascender,
        font_descender,
    )

    for family in FONT_CHOICES:
        em = 12.0 * 25.4 / 72.0
        item = PageTextItem(PageLabel(page_id="p1", text="HI", font_family=family))
        assert item._line_height() == pytest.approx(
            (font_ascender(family) + font_descender(family)) * em, abs=1e-6
        )


def test_multi_line_label_keeps_the_shared_line_spacing(workspace, tmp_path, qt_app):
    """Explicit line breaks must lay out at the shared line height in both places.

    Letting Qt (canvas) and PyMuPDF (PDF) each do their own multi-line layout
    spaced the second line ~1 mm apart; the canvas now draws one baseline per
    line and the writer appends one text run per line, both spaced by
    ``font_ascender + font_descender``.
    """
    from core.layout_service import LayoutService
    from graphics.text_item import PADDING_MM, PageTextItem
    from models.page_label import (
        PageLabel,
        font_ascender,
        font_descender,
    )

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    label = PageLabel(
        page_id=page.id,
        text="First line\nSecond line",
        position_x=20.0,
        position_y=30.0,
        font_size=12.0,
    )
    workspace.database.labels.insert(label)

    em = 12.0 * 25.4 / 72.0
    line_height_mm = (font_ascender("Arial") + font_descender("Arial")) * em
    item = PageTextItem(label)
    assert item._size()[1] == pytest.approx(line_height_mm * 2 + PADDING_MM * 2, abs=1e-6)

    output = tmp_path / "multiline.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))

    document = pymupdf.open(output)
    try:
        pdf_page = document[0]
        assert "First line" in pdf_page.get_text()
        assert "Second line" in pdf_page.get_text()

        baselines = sorted(
            span["origin"][1]
            for block in pdf_page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        )
        assert len(baselines) == 2, f"expected two lines, got {baselines}"
        spacing_pt = abs(baselines[1] - baselines[0])
        assert spacing_pt == pytest.approx(line_height_mm * (72.0 / 25.4), abs=0.1), (
            f"lines are {spacing_pt:.2f} pt apart instead of "
            f"{line_height_mm * 72.0 / 25.4:.2f} pt"
        )
    finally:
        document.close()


ARABIC_LABEL = "\u062c\u0648\u0627\u0632 \u0627\u0644\u0633\u0641\u0631 Passport"


def _arabic_chars(text: str) -> int:
    """Count Arabic letters/presentation forms in *text*."""
    return sum(
        1
        for char in text
        if 0x0600 <= ord(char) <= 0x06FF or 0xFB50 <= ord(char) <= 0xFEFF
    )


def test_arabic_label_is_shaped_in_the_pdf(workspace, tmp_path):
    """Arabic used to export as dots: base-14 fonts have no Arabic glyphs.

    The standard text writer does no shaping, so every Arabic letter was drawn
    as a missing-glyph dot ("\u00b7\u00b7\u00b7\u00b7 \u00b7\u00b7\u00b7\u00b7\u00b7 Passport").  Shaped labels now go
    through PyMuPDF's HTML layout, which applies Arabic shaping and bidi.
    """
    from core.layout_service import LayoutService

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    workspace.database.labels.insert(
        PageLabel(
            page_id=page.id,
            text=ARABIC_LABEL,
            position_x=20.0,
            position_y=30.0,
            font_size=16.0,
        )
    )
    output = tmp_path / "arabic.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))

    document = pymupdf.open(output)
    try:
        pdf_page = document[0]
        text = pdf_page.get_text()
        assert "Passport" in text, f"mixed Latin text was lost: {text!r}"
        assert _arabic_chars(text) >= 5, (
            f"Arabic letters are missing from the PDF text: {text!r}"
        )
        assert "\u00b7" not in text, f"Arabic exported as placement dots: {text!r}"

        # ... and the glyphs must actually be painted, not just carried along.
        mm = 72.0 / 25.4
        clip = pymupdf.Rect(19.0 * mm, 29.0 * mm, 100.0 * mm, 43.0 * mm)
        pixmap = pdf_page.get_pixmap(dpi=150, clip=clip)
        dark = sum(
            1
            for y in range(pixmap.height)
            for x in range(pixmap.width)
            if sum(pixmap.pixel(x, y)[:3]) < 600
        )
        assert dark > 120, f"Arabic label drew almost no ink ({dark} dark pixels)"
    finally:
        document.close()


def test_rotated_arabic_label_keeps_its_rotation(workspace, tmp_path):
    """A 90 degree Arabic label must stay vertical and keep its shaped text."""
    from core.layout_service import LayoutService

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    workspace.database.labels.insert(
        PageLabel(
            page_id=page.id,
            text="\u062c\u0648\u0627\u0632 \u0627\u0644\u0633\u0641\u0631",
            position_x=30.0,
            position_y=30.0,
            font_size=14.0,
            rotation=90.0,
        )
    )
    output = tmp_path / "arabic_rotated.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))

    document = pymupdf.open(output)
    try:
        pdf_page = document[0]
        spans = [
            span
            for block in pdf_page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ]
        assert spans, "the rotated Arabic label disappeared"
        assert _arabic_chars(pdf_page.get_text()) >= 5
        rect = pymupdf.Rect(spans[0]["bbox"])
        for span in spans[1:]:
            rect |= pymupdf.Rect(span["bbox"])
        assert rect.height > rect.width * 1.2, (
            f"rotated Arabic label is not vertical: {rect.width:.1f}x{rect.height:.1f} pt"
        )
    finally:
        document.close()
