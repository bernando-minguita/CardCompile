"""Group alignment of page objects (rotation-aware).

The images on a page are often rotated by 90 degrees.  Aligning the *unrotated*
frames left the visible edges staggered, so alignment and distribution work on
the visible outline (rotation happens around the item centre).
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

from core.import_service import ImportService  # noqa: E402
from core.layout_service import (  # noqa: E402
    LayoutService,
    rotated_size,
    visual_bounds,
)
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS  # noqa: E402
from models.page_item import PageItem  # noqa: E402
from tests.conftest import make_image  # noqa: E402


@pytest.fixture()
def layout(workspace):
    return LayoutService(workspace)


def _import(workspace, count: int = 3):
    paths = [
        make_image(
            workspace.project.folder / "sources" / f"align_{index}.jpg",
            size=(600, 950),
            fmt="JPEG",
        )
        for index in range(count)
    ]
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)


def _items(layout, workspace, rotations, sizes=None):
    """Placed items with the given rotations and staggered positions."""
    documents = _import(workspace, len(rotations))
    page = layout.pages()[0]
    items = []
    for index, (document, rotation) in enumerate(zip(documents, rotations)):
        width, height = (sizes or [(54.0, 86.0)] * len(rotations))[index]
        items.append(
            PageItem(
                id=f"item-{index}",
                page_id=page.id,
                document_id=document.id,
                position_x=10.0 + index * 30.0,
                position_y=20.0 + index * 25.0,
                width=width,
                height=height,
                rotation=rotation,
            )
        )
    layout.insert_items(items)
    return items, page


def test_rotated_size_of_a_quarter_turn():
    assert rotated_size(54.0, 86.0, 0.0) == (54.0, 86.0)
    width, height = rotated_size(54.0, 86.0, 90.0)
    assert width == pytest.approx(86.0, abs=0.001)
    assert height == pytest.approx(54.0, abs=0.001)


def test_visual_bounds_follow_the_centre():
    item = PageItem(
        id="x",
        page_id="p",
        document_id="d",
        position_x=10.0,
        position_y=20.0,
        width=54.0,
        height=86.0,
        rotation=90.0,
    )
    left, top, width, height = visual_bounds(item)
    assert width == pytest.approx(86.0, abs=0.001)
    assert height == pytest.approx(54.0, abs=0.001)
    # The centre must not move: rotation happens around it.
    assert left + width / 2 == pytest.approx(10.0 + 54.0 / 2, abs=0.001)
    assert top + height / 2 == pytest.approx(20.0 + 86.0 / 2, abs=0.001)


def test_align_top_uses_the_visible_edges_of_rotated_images(layout, workspace):
    items, page = _items(layout, workspace, rotations=[90.0, 0.0, 90.0])

    layout.align_items(items, "top", page)

    tops = [visual_bounds(item)[1] for item in items]
    assert max(tops) - min(tops) < 0.01, f"visible tops differ: {tops}"
    # The unrotated frame of the middle item is *not* at the same y as the
    # rotated ones - that is the whole point of the fix.
    assert abs(items[0].position_y - items[1].position_y) > 0.5


def test_align_left_and_right_use_the_visible_edges(layout, workspace):
    items, page = _items(layout, workspace, rotations=[90.0, 0.0, 90.0])

    layout.align_items(items, "left", page)
    lefts = [visual_bounds(item)[0] for item in items]
    assert max(lefts) - min(lefts) < 0.01

    layout.align_items(items, "right", page)
    rights = [visual_bounds(item)[0] + visual_bounds(item)[2] for item in items]
    assert max(rights) - min(rights) < 0.01


def test_align_centres_and_middles_line_up_the_visual_centres(layout, workspace):
    items, page = _items(layout, workspace, rotations=[90.0, 0.0, 45.0])

    layout.align_items(items, "center", page)
    centers = [visual_bounds(item)[0] + visual_bounds(item)[2] / 2 for item in items]
    assert max(centers) - min(centers) < 0.01

    layout.align_items(items, "middle", page)
    middles = [visual_bounds(item)[1] + visual_bounds(item)[3] / 2 for item in items]
    assert max(middles) - min(middles) < 0.01


def test_align_bottom_uses_the_visible_bottom(layout, workspace):
    items, page = _items(layout, workspace, rotations=[90.0, 0.0, 90.0])

    layout.align_items(items, "bottom", page)

    bottoms = [visual_bounds(item)[1] + visual_bounds(item)[3] for item in items]
    assert max(bottoms) - min(bottoms) < 0.01


def test_unrotated_alignment_is_unchanged(layout, workspace):
    """Regression: without rotation the previous behaviour must be reproduced."""
    items, page = _items(layout, workspace, rotations=[0.0, 0.0, 0.0])

    layout.align_items(items, "top", page)

    assert len({round(item.position_y, 3) for item in items}) == 1
    assert min(item.position_y for item in items) == pytest.approx(20.0, abs=0.01)


def test_page_centring_centres_the_visual_box(layout, workspace):
    items, page = _items(layout, workspace, rotations=[90.0])
    left, top, width, height = page.printable_rect()

    layout.align_items(items, "page_center", page)

    bounds = visual_bounds(items[0])
    assert bounds[0] + bounds[2] / 2 == pytest.approx(left + width / 2, abs=0.02)
    assert bounds[1] + bounds[3] / 2 == pytest.approx(top + height / 2, abs=0.02)


def test_distribute_horizontal_gives_equal_visual_gaps(layout, workspace):
    items, _page = _items(layout, workspace, rotations=[90.0, 0.0, 90.0])

    layout.distribute_items(items, "horizontal")

    ordered = sorted((visual_bounds(item) for item in items), key=lambda b: b[0])
    gaps = [
        ordered[index + 1][0] - (ordered[index][0] + ordered[index][2])
        for index in range(len(ordered) - 1)
    ]
    assert gaps[0] == pytest.approx(gaps[1], abs=0.02)


def test_distribute_vertical_gives_equal_visual_gaps(layout, workspace):
    items, _page = _items(layout, workspace, rotations=[0.0, 90.0, 45.0])

    layout.distribute_items(items, "vertical")

    ordered = sorted((visual_bounds(item) for item in items), key=lambda b: b[1])
    gaps = [
        ordered[index + 1][1] - (ordered[index][1] + ordered[index][3])
        for index in range(len(ordered) - 1)
    ]
    assert gaps[0] == pytest.approx(gaps[1], abs=0.02)


def test_alignment_does_not_move_other_objects(layout, workspace):
    items, page = _items(layout, workspace, rotations=[90.0, 0.0, 90.0])
    untouched = items[2]
    snapshot = (untouched.position_x, untouched.position_y)

    layout.align_items(items[:2], "top", page)

    assert (untouched.position_x, untouched.position_y) == snapshot
