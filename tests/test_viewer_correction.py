"""The document preview must show the corrected image, not the raw scan.

Double-clicking a placed image opened the original A4 scan even after the
Perspective Correction had straightened and resized it, because the viewer only
applied the crop.  These tests pin the behaviour: no correction -> raw scan;
correction -> warped result, with a label that matches the picture.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.viewer_dialog import ImageViewerDialog  # noqa: E402
from models.document import Document  # noqa: E402
from models.page_item import PageItem  # noqa: E402

SCAN_W, SCAN_H = 600, 850
CARD_BOX = (80, 200, 520, 480)


@pytest.fixture()
def scan(tmp_path):
    """A portrait A4-style scan with a white card region on it."""
    path = tmp_path / "scan.png"
    image = Image.new("RGB", (SCAN_W, SCAN_H), (110, 110, 112))
    draw = ImageDraw.Draw(image)
    draw.rectangle(CARD_BOX, fill=(250, 250, 248), outline=(20, 20, 20), width=4)
    image.save(path)
    return path


@pytest.fixture()
def document(scan):
    return Document(
        id="d1",
        project_id="p1",
        filename="scan.png",
        source_path=str(scan),
        file_hash="x",
        original_width=SCAN_W,
        original_height=SCAN_H,
    )


def _card_item() -> PageItem:
    """An item whose quad is the card region of the scan."""
    item = PageItem(id="i1", page_id="pg1", document_id="d1")
    left, top, right, bottom = CARD_BOX
    item.set_quad(
        (
            left / SCAN_W,
            top / SCAN_H,
            right / SCAN_W,
            top / SCAN_H,
            right / SCAN_W,
            bottom / SCAN_H,
            left / SCAN_W,
            bottom / SCAN_H,
        )
    )
    return item


def test_preview_without_correction_shows_the_raw_scan(qt_app, document):
    dialog = ImageViewerDialog(document, None)
    try:
        assert dialog._pixmap.width() == SCAN_W
        assert dialog._pixmap.height() == SCAN_H
        assert "corrected" not in dialog.windowTitle().lower()
    finally:
        dialog.deleteLater()


def test_preview_applies_the_correction(qt_app, document):
    """The reported bug: the preview ignored the Perspective Correction."""
    item = _card_item()
    assert item.has_correction()

    dialog = ImageViewerDialog(document, None, correction=item.correction_key())
    try:
        width, height = dialog._pixmap.width(), dialog._pixmap.height()
        assert (width, height) != (SCAN_W, SCAN_H), "the preview ignored the correction"
        # The quad is a landscape card, so the result must be landscape too.
        assert width > height, f"expected a landscape card, got {width}x{height}"
        assert width == pytest.approx(CARD_BOX[2] - CARD_BOX[0], abs=3)
        assert height == pytest.approx(CARD_BOX[3] - CARD_BOX[1], abs=3)
    finally:
        dialog.deleteLater()


def test_preview_title_mentions_the_correction(qt_app, document):
    item = _card_item()
    dialog = ImageViewerDialog(document, None, correction=item.correction_key())
    try:
        assert "corrected" in dialog.windowTitle().lower()
    finally:
        dialog.deleteLater()


def test_preview_label_describes_what_is_shown(qt_app, document):
    """The label must not claim the source size above a corrected picture."""
    item = _card_item()
    dialog = ImageViewerDialog(document, None, correction=item.correction_key())
    try:
        text = dialog.info_label.text()
        width, height = dialog._pixmap.width(), dialog._pixmap.height()
        assert f"{width} x {height} px" in text
        assert f"from {SCAN_W} x {SCAN_H} px" in text
        assert "corrected" in text
    finally:
        dialog.deleteLater()


def test_preview_applies_tone_adjustments(qt_app, document):
    """A brightness change alone must still reach the preview."""
    item = PageItem(id="i2", page_id="pg1", document_id="d1")
    item.adjust_brightness = 1.6
    assert item.has_correction()

    plain = ImageViewerDialog(document, None)
    bright = ImageViewerDialog(document, None, correction=item.correction_key())
    try:
        plain_pixel = plain._pixmap.toImage().pixelColor(5, 5).red()
        bright_pixel = bright._pixmap.toImage().pixelColor(5, 5).red()
        assert bright_pixel > plain_pixel, "the tone adjustment was ignored"
    finally:
        plain.deleteLater()
        bright.deleteLater()


def test_editor_passes_the_correction_to_the_viewer(monkeypatch):
    """The double-click handler must forward the correction, not just the crop."""
    import inspect

    from app.layout_editor import LayoutEditor

    source = inspect.getsource(LayoutEditor._on_item_double_clicked)
    assert "correction=" in source, (
        "the double-click handler does not pass the correction to the viewer"
    )
