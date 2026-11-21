"""Canvas rendering tests: the image must be area-averaged when minified.

A high-frequency pattern drawn through ``drawPixmap`` alone collapses into
moire; the item pre-scales with ``SmoothTransformation`` (measured to be a true
box filter) so the drawn tone matches the source's average.
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

try:  # pragma: no cover - environment guard
    from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
except Exception:  # pragma: no cover
    pytest.skip("PySide6 is not available", allow_module_level=True)

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from graphics.image_item import PageImageItem  # noqa: E402
from models.page_item import PageItem  # noqa: E402


def _stripes(width: int, height: int, period: int = 7) -> Image.Image:
    """Half black / half white vertical stripes.

    Averaged correctly this is a flat mid grey; undersampled it becomes a
    moire pattern whose tone depends on the sampling phase.
    """
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, period):
        draw.rectangle((x, 0, x + period // 2, height - 1), fill=(0, 0, 0))
    return image


def _to_pixmap(image: Image.Image) -> QPixmap:
    qimage = QImage(
        image.tobytes("raw", "RGB"),
        image.width,
        image.height,
        image.width * 3,
        QImage.Format.Format_RGB888,
    ).copy()
    return QPixmap.fromImage(qimage)


def _render(item: PageImageItem, px_per_mm: float) -> Image.Image:
    """Paint *item* into an offscreen frame at the requested zoom."""
    width_mm, height_mm = item.model.width, item.model.height
    out_w = max(1, int(round(width_mm * px_per_mm)))
    out_h = max(1, int(round(height_mm * px_per_mm)))
    target = QImage(out_w, out_h, QImage.Format.Format_RGB888)
    target.fill(QColor("#FFFFFF"))

    painter = QPainter(target)
    painter.scale(px_per_mm, px_per_mm)
    # ``paint`` reads the world transform to size the pre-scaled pixmap.
    item.paint(painter, None, None)
    painter.end()

    path = Path(__file__).with_name("_canvas_render.png")
    target.save(str(path), "PNG")
    try:
        with Image.open(path) as saved:
            return saved.convert("RGB").copy()
    finally:
        path.unlink(missing_ok=True)


def _make_item(pixmap: QPixmap) -> PageImageItem:
    model = PageItem(
        id="i1",
        page_id="p1",
        document_id="d1",
        width=54.0,
        height=86.0,
    )
    return PageImageItem(model, pixmap)


def test_canvas_minification_is_area_averaged(qt_app):
    """A 7x minification of fine stripes must not alias into moire."""
    source = _stripes(1400, 200, period=7)
    item = _make_item(_to_pixmap(source))

    # 54 mm wide at 3.7 px/mm is ~200 px, so the 1400 px source is minified 7x.
    rendered = _render(item, 3.7)
    pixels = np.asarray(rendered.convert("L"), dtype=np.float32)

    # Correct area averaging yields one flat tone.  Undersampling would leave a
    # large spread between neighbouring columns.
    assert pixels.std() < 3.0, (
        f"minified stripes should average to a flat tone, got std={pixels.std():.2f}"
    )


def test_canvas_keeps_detail_when_not_minified(qt_app):
    """A pixmap smaller than the target must still be drawn, not skipped."""
    source = Image.new("RGB", (60, 100), (20, 40, 200))
    ImageDraw.Draw(source).rectangle((0, 0, 29, 99), fill=(220, 30, 30))
    item = _make_item(_to_pixmap(source))

    rendered = _render(item, 3.7)
    pixels = np.asarray(rendered, dtype=np.float32)
    # Left half red, right half blue - the split must survive.
    assert pixels[:, :50, 0].mean() > 150, "left half should be red"
    assert pixels[:, -50:, 2].mean() > 150, "right half should be blue"


def test_canvas_cache_is_invalidated_by_set_pixmap(qt_app):
    """A new pixmap must not be masked by the cached scaled copy."""
    red = Image.new("RGB", (400, 600), (200, 20, 20))
    blue = Image.new("RGB", (400, 600), (20, 20, 200))
    item = _make_item(_to_pixmap(red))

    first = np.asarray(_render(item, 3.7), dtype=np.float32)
    assert first[:, :, 0].mean() > first[:, :, 2].mean()

    item.set_pixmap(_to_pixmap(blue))
    second = np.asarray(_render(item, 3.7), dtype=np.float32)
    assert second[:, :, 2].mean() > second[:, :, 0].mean(), (
        "set_pixmap must drop the cached scaled pixmap"
    )
