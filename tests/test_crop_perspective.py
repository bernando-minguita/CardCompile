"""Regression tests for the crop/perspective coordinate-space bugs.

``PageItem.crop_box()`` and ``PageItem.quad()`` are both fractions of the
*original* image, but every render path cropped first and then warped with the
original-space quad.  With both a crop and a non-identity quad the warp sampled
the wrong region entirely, and the canvas never repainted when the crop changed
because the crop was not part of the pixmap cache key.
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

from core.auto_layout_engine import _quad_extent  # noqa: E402
from core.image_service import (  # noqa: E402
    load_corrected_display_image,
    quad_in_crop_space,
)
from core.layout_service import document_aspect, quad_aspect  # noqa: E402
from models.document import Document  # noqa: E402
from models.page_item import PageItem  # noqa: E402

W = H = 400
RED_QUADRANT = (0.0, 0.0, 0.5, 0.0, 0.5, 0.5, 0.0, 0.5)
IDENTITY = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)


@pytest.fixture()
def quadrants(tmp_path):
    """A 400x400 image split into four distinctly coloured quadrants."""
    path = tmp_path / "quadrants.png"
    image = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 199, 199], fill=(255, 0, 0))
    draw.rectangle([200, 0, 399, 199], fill=(0, 255, 0))
    draw.rectangle([200, 200, 399, 399], fill=(0, 0, 255))
    draw.rectangle([0, 200, 199, 399], fill=(255, 255, 0))
    image.save(path)
    return path


def _centre(image):
    return image.getpixel((image.width // 2, image.height // 2))


# ------------------------------------------------- quad_in_crop_space (unit)
def test_quad_in_crop_space_maps_the_crop_to_the_unit_square():
    """A quad covering exactly the crop becomes the identity."""
    crop = (0.25, 0.25, 0.75, 0.75)
    quad = (0.25, 0.25, 0.75, 0.25, 0.75, 0.75, 0.25, 0.75)
    assert quad_in_crop_space(quad, crop) == pytest.approx(IDENTITY)


def test_quad_in_crop_space_shifts_and_scales():
    crop = (0.5, 0.5, 1.0, 1.0)
    quad = (0.5, 0.5, 1.0, 0.5, 1.0, 1.0, 0.5, 1.0)
    assert quad_in_crop_space(quad, crop) == pytest.approx(IDENTITY)


def test_quad_in_crop_space_allows_negative_results():
    """Corners cropped away map outside 0..1 - that is intended."""
    crop = (0.5, 0.5, 1.0, 1.0)
    remapped = quad_in_crop_space(RED_QUADRANT, crop)
    assert any(value < 0 for value in remapped)


# ------------------------------------------------------ Bug 1: wrong region
def test_quad_without_crop_selects_the_right_region(quadrants):
    out = load_corrected_display_image(quadrants, quad=RED_QUADRANT)
    assert _centre(out)[0] > 200 and _centre(out)[1] < 100, "expected red"


def test_quad_and_crop_on_the_same_region_stay_correct(quadrants):
    """Crop to the red quadrant and select it: the result must still be red."""
    item = PageItem(id="i", page_id="p", document_id="d")
    item.set_quad(RED_QUADRANT)
    item.crop_right = 0.5
    item.crop_bottom = 0.5
    crop = item.crop_pixels(W, H)

    out = load_corrected_display_image(quadrants, crop=crop, quad=item.quad())
    centre = _centre(out)
    assert centre[0] > 200 and centre[1] < 100, f"expected red, got {centre}"


def test_quad_from_another_region_is_not_silently_honoured(quadrants):
    """The bug's signature: crop the blue quadrant, select the red one.

    Before the fix the warp was applied to the cropped image with an
    original-space quad, so it returned the blue crop as though the quad had
    been honoured.  Now the quad is remapped and points outside the crop, so the
    output cannot masquerade as the blue region.
    """
    item = PageItem(id="i", page_id="p", document_id="d")
    item.set_quad(RED_QUADRANT)
    item.crop_left = 0.5
    item.crop_top = 0.5
    crop = item.crop_pixels(W, H)

    out = load_corrected_display_image(quadrants, crop=crop, quad=item.quad())
    centre = _centre(out)
    assert not (centre[2] > 200 and centre[0] < 100), (
        "the warp still returned the cropped region as if the quad matched it"
    )


# --------------------------------------------- Bug 3: layout maths and quad
def _document() -> Document:
    return Document(
        id="d1",
        project_id="p1",
        filename="scan.png",
        source_path="scan.png",
        file_hash="x",
        original_width=W,
        original_height=H,
        dpi_x=300.0,
        dpi_y=300.0,
    )


def test_quad_aspect_matches_the_warped_shape():
    """A square quad on a square image is 1.0; a wide quad is wide."""
    assert quad_aspect(IDENTITY, W, H) == pytest.approx(1.0)
    wide = (0.0, 0.25, 1.0, 0.25, 1.0, 0.75, 0.0, 0.75)
    assert quad_aspect(wide, W, H) == pytest.approx(2.0)


def test_document_aspect_prefers_the_quad_when_present():
    """A straightened card must be measured by its warped shape."""
    document = _document()
    assert document_aspect(document, None, IDENTITY) == pytest.approx(1.0)
    wide = (0.0, 0.25, 1.0, 0.25, 1.0, 0.75, 0.0, 0.75)
    # The quad describes a 2:1 card, so placement must use 2:1, not 1:1.
    assert document_aspect(document, None, wide) == pytest.approx(2.0)


def test_document_aspect_still_honours_the_crop_without_a_quad():
    document = _document()
    assert document_aspect(document, (0.0, 0.0, 0.5, 1.0), None) == pytest.approx(0.5)


def test_quad_extent_matches_the_warper():
    """_quad_extent must agree with apply_perspective's output sizing."""
    from core.image_service import apply_perspective

    image = Image.new("RGB", (W, H), (200, 200, 200))
    quad = (0.1, 0.2, 0.9, 0.1, 0.95, 0.85, 0.05, 0.9)
    warped = apply_perspective(image, quad)
    expected_w, expected_h = _quad_extent(quad, W, H)
    assert warped.width == pytest.approx(expected_w, abs=2)
    assert warped.height == pytest.approx(expected_h, abs=2)


# ------------------------------------------------ Bug 2: the pixmap cache
def test_pixmap_cache_keys_on_the_crop(qt_app, quadrants):
    """Changing the crop must produce new pixels, not the cached ones.

    The canvas paints from this pixmap, so leaving the crop out of the cache key
    meant a crop change never repainted the item.
    """
    from graphics.pixmap_cache import load_corrected_pixmap

    item = PageItem(id="i", page_id="p", document_id="d")
    item.adjust_brightness = 1.2  # any correction puts us on the corrected path

    first = load_corrected_pixmap(
        quadrants, item.correction_key(), crop=(0, 0, 200, 200)
    )
    second = load_corrected_pixmap(
        quadrants, item.correction_key(), crop=(200, 200, 400, 400)
    )
    assert not first.isNull() and not second.isNull()

    # Different crops of a four-colour image must not be the same pixmap.
    a = first.toImage().pixelColor(5, 5)
    b = second.toImage().pixelColor(5, 5)
    assert (a.red(), a.green(), a.blue()) != (b.red(), b.green(), b.blue()), (
        "the cache returned the same pixels for two different crops"
    )


def test_pixmap_cache_returns_the_same_object_for_the_same_crop(qt_app, quadrants):
    """The cache must still work - identical requests hit the same entry."""
    from graphics.pixmap_cache import load_corrected_pixmap

    item = PageItem(id="i", page_id="p", document_id="d")
    item.adjust_brightness = 1.2
    crop = (0, 0, 200, 200)
    first = load_corrected_pixmap(quadrants, item.correction_key(), crop=crop)
    second = load_corrected_pixmap(quadrants, item.correction_key(), crop=crop)
    assert first.cacheKey() == second.cacheKey()



