"""Canvas snapping tests: dragging must align the *visible* edge.

``PageScene.snap_move`` compares candidate positions against the item's
top-left corner, so the right and bottom page margins have to be offered as
``margin - size``.  Using the raw margin coordinate snapped the left edge onto
the right margin and pushed the item off the paper.
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
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPixmap
except Exception:  # pragma: no cover
    pytest.skip("PySide6 is not available", allow_module_level=True)

from graphics.image_item import DragStart, PageImageItem  # noqa: E402
from graphics.page_scene import PageScene  # noqa: E402
from models.page_item import PageItem  # noqa: E402
from models.pdf_page import PdfPage  # noqa: E402

ITEM_W = 50.0
ITEM_H = 30.0


def _scene() -> tuple[PageScene, PageImageItem]:
    """A 210 x 297 mm page with 10 mm margins and one 50 x 30 mm item."""
    page = PdfPage(
        id="page",
        project_id="project",
        page_number=1,
        width_mm=210.0,
        height_mm=297.0,
        margin_left=10.0,
        margin_right=10.0,
        margin_top=10.0,
        margin_bottom=10.0,
    )
    scene = PageScene()
    scene.set_page(page)
    scene.configure(show_grid=False, snap_to_grid=False, show_guides=True)
    model = PageItem(
        id="item",
        page_id="page",
        document_id="document",
        position_x=100.0,
        position_y=100.0,
        width=ITEM_W,
        height=ITEM_H,
    )
    item = PageImageItem(model, QPixmap(10, 10))
    scene.addItem(item)
    scene._items["item"] = item
    return scene, item


def _begin_drag(item: PageImageItem) -> None:
    item._drag = DragStart(
        rect=(100.0, 100.0, ITEM_W, ITEM_H),
        rotation=0.0,
        handle="move",
        scene_pos=QPointF(0.0, 0.0),
        positions={},
    )


def test_snap_aligns_right_edge_with_right_margin(qt_app):
    """The right margin must snap the item's right edge, not its left edge."""
    scene, item = _scene()
    page = scene.page
    _left, _top, printable_w, _printable_h = page.printable_rect()
    right_margin = _left + printable_w

    _begin_drag(item)
    # Candidate is right_margin - ITEM_W = 150; propose 151 (inside tolerance).
    dx, _dy = scene.snap_move(item, 51.0, 0.0)

    new_left = 100.0 + dx
    assert new_left + ITEM_W == pytest.approx(right_margin, abs=0.01), (
        "the item's right edge should land on the right margin"
    )
    assert new_left + ITEM_W <= page.width_mm + 0.01, "the item must stay on the page"


def test_snap_aligns_bottom_edge_with_bottom_margin(qt_app):
    """The bottom margin must snap the item's bottom edge, not its top edge."""
    scene, item = _scene()
    page = scene.page
    _left, _top, _printable_w, printable_h = page.printable_rect()
    bottom_margin = _top + printable_h

    _begin_drag(item)
    # Candidate is bottom_margin - ITEM_H = 257; propose 258 (inside tolerance).
    _dx, dy = scene.snap_move(item, 0.0, 158.0)

    new_top = 100.0 + dy
    assert new_top + ITEM_H == pytest.approx(bottom_margin, abs=0.01), (
        "the item's bottom edge should land on the bottom margin"
    )
    assert new_top + ITEM_H <= page.height_mm + 0.01, "the item must stay on the page"


def test_snap_aligns_left_edge_with_left_margin(qt_app):
    """The left/top margins keep snapping the near edge (unchanged behaviour)."""
    scene, item = _scene()
    page = scene.page
    left_margin, top_margin, _w, _h = page.printable_rect()

    _begin_drag(item)
    # Candidate is left_margin = 10; propose 9 (inside tolerance).
    dx, _dy = scene.snap_move(item, -91.0, 0.0)
    assert 100.0 + dx == pytest.approx(left_margin, abs=0.01)

    _begin_drag(item)
    _dx, dy = scene.snap_move(item, 0.0, -91.0)
    assert 100.0 + dy == pytest.approx(top_margin, abs=0.01)


def test_snap_right_aligns_with_another_item(qt_app):
    """Two items can be right-aligned by dragging, not only left-aligned."""
    scene, item = _scene()
    other = PageImageItem(
        PageItem(
            id="other",
            page_id="page",
            document_id="document",
            position_x=20.0,
            position_y=20.0,
            width=60.0,
            height=40.0,
        ),
        QPixmap(10, 10),
    )
    scene.addItem(other)
    scene._items["other"] = other

    # The other item's right edge is at 80, so the candidate for our left edge
    # is 80 - ITEM_W = 30; propose 31 (inside tolerance).
    _begin_drag(item)
    dx, _dy = scene.snap_move(item, -69.0, 0.0)
    assert 100.0 + dx + ITEM_W == pytest.approx(80.0, abs=0.01), (
        "the dragged item's right edge should align with the other item's right edge"
    )
