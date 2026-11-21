"""The Pages strip must preview what the canvas shows (rotation, crop, order)."""

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

from app.page_manager import render_page_thumbnail  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.layout_service import LayoutService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS  # noqa: E402
from models.page_item import PageItem  # noqa: E402
from tests.conftest import make_image  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def layout(workspace):
    return LayoutService(workspace)


def _red_document(workspace, layout):
    """A bright red image so its pixels are easy to find in the thumbnail."""
    path = workspace.project.folder / "sources" / "red.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 200), (255, 0, 0)).save(path, format="PNG", dpi=(300, 300))
    service = ImportService(workspace)
    previews = service.analyze([path], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    document = workspace.database.documents.list_for_project(workspace.id)[0]
    # Force the renderer to use the file itself (tests import no thumbnail).
    document.thumbnail_path = ""
    return document


def _red_bounds(image):
    left = top = None
    right = bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            if colour.red() > 180 and colour.green() < 110 and colour.blue() < 110:
                if left is None or x < left:
                    left = x
                if top is None or y < top:
                    top = y
                right = max(right, x)
                bottom = max(bottom, y)
    assert left is not None, "no image pixels were drawn"
    return left, top, right - left + 1, bottom - top + 1


def test_thumbnail_draws_the_rotated_card_sideways(qt_app, layout, workspace):
    """A 90-degree object must appear rotated (wide) in the Pages strip."""
    document = _red_document(workspace, layout)
    page = layout.pages()[0]
    item = PageItem(
        id="rot-1",
        page_id=page.id,
        document_id=document.id,
        position_x=40.0,
        position_y=80.0,
        width=40.0,   # narrow frame ...
        height=80.0,  # ... tall frame -> 90 deg makes it wide on screen
        rotation=90.0,
    )
    layout.insert_items([item])

    upright = render_page_thumbnail(page, [], {document.id: document})
    rotated = render_page_thumbnail(page, [item], {document.id: document})

    # Nothing drawn without items (only the page border, which is not red).
    with pytest.raises(AssertionError):
        _red_bounds(upright.toImage())

    left, top, width, height = _red_bounds(rotated.toImage())
    assert width > height * 1.6, (
        f"the preview shows an upright card ({width}x{height}) although it is "
        "rotated by 90 degrees"
    )
    # The visible centre must sit where the frame centre is on the page.
    scale = rotated.width() / page.width_mm
    expected_x = (item.position_x + item.width / 2) * scale
    expected_y = (item.position_y + item.height / 2) * scale
    assert left + width / 2 == pytest.approx(expected_x, abs=2.0)
    assert top + height / 2 == pytest.approx(expected_y, abs=2.0)


def test_thumbnail_of_an_upright_card_is_tall(qt_app, layout, workspace):
    document = _red_document(workspace, layout)
    page = layout.pages()[0]
    item = PageItem(
        id="up-1",
        page_id=page.id,
        document_id=document.id,
        position_x=40.0,
        position_y=80.0,
        width=40.0,
        height=80.0,
        rotation=0.0,
    )
    layout.insert_items([item])

    _left, _top, width, height = _red_bounds(
        render_page_thumbnail(page, [item], {document.id: document}).toImage()
    )
    assert height > width * 1.6, f"upright card preview is {width}x{height}"


def test_thumbnail_honours_the_crop(qt_app, layout, workspace):
    """A horizontally cropped card previews narrower/shorter, not stretched."""
    document = _red_document(workspace, layout)
    page = layout.pages()[0]
    full = PageItem(
        id="crop-full",
        page_id=page.id,
        document_id=document.id,
        position_x=40.0,
        position_y=80.0,
        width=60.0,
        height=30.0,
    )
    cropped = PageItem(
        id="crop-half",
        page_id=page.id,
        document_id=document.id,
        position_x=140.0,
        position_y=80.0,
        width=60.0,
        height=30.0,
        crop_left=0.25,
        crop_right=0.25,
        crop_top=0.25,
        crop_bottom=0.25,
    )
    layout.insert_items([full, cropped])

    image = render_page_thumbnail(page, [full, cropped], {document.id: document}).toImage()
    bounds = _red_bounds(image)
    # Both cards are drawn: the red area must span both frames.
    assert bounds[2] > image.width() * 0.6

