"""Image processing tests: rotation, crop, resize, aspect, original safety."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from core.image_service import (
    cropped_image,
    encode_jpeg,
    load_rgb,
    render_pdf_image_bytes,
    resize_for_dpi,
    rotate_image,
)
from core.layout_service import fit_size
from models.page_item import PageItem


def test_crop_is_pixel_exact(image_factory):
    source = image_factory(fmt="PNG", size=(400, 200))
    image = cropped_image(source, (100, 50, 300, 150))
    assert image.size == (200, 100)


def test_crop_reversible(image_factory):
    source = image_factory(fmt="PNG", size=(400, 200))
    full = cropped_image(source, None)
    twice = cropped_image(source, (0, 0, 400, 200))
    assert full.size == twice.size == (400, 200)


def test_resize_never_upscales(image_factory):
    source = image_factory(fmt="PNG", size=(300, 200))
    image = load_rgb(source)
    resized = resize_for_dpi(image, 210.0, 297.0, dpi=300)  # would need ~2480px
    assert resized.size == (300, 200), "existing pixels must not be upscaled"


def test_resize_downsamples_at_dpi(image_factory):
    source = image_factory(fmt="PNG", size=(3000, 2000))
    image = load_rgb(source)
    resized = resize_for_dpi(image, 100.0, 150.0, dpi=150)
    expected_w = round(100 / 25.4 * 150)
    assert resized.width <= expected_w + 1
    assert resized.width < 3000


def test_fit_size_contain_keeps_aspect():
    width, height = fit_size(1000, 500, 200, 200, "contain")
    assert width == 200
    assert height == 100


def test_fit_size_cover_fills_box():
    width, height = fit_size(1000, 500, 200, 200, "cover")
    assert height == 200
    assert width == 400


def test_fit_size_stretch():
    assert fit_size(1000, 500, 120, 90, "stretch") == (120, 90)


def test_rotation_preserves_original_file(image_factory):
    source = image_factory(fmt="JPEG", size=(500, 300))
    original_bytes = source.read_bytes()
    item = PageItem(id="i1", page_id="p1", document_id="d1", rotation=90.0)
    data = render_pdf_image_bytes(source, None, item.width, item.height, 150)
    assert data[:2] == b"\xff\xd8", "JPEG stream expected"
    assert source.read_bytes() == original_bytes, "rotation must not touch the source"


def test_crop_pixels_from_normalised_values():
    item = PageItem(
        id="i1",
        page_id="p1",
        document_id="d1",
        crop_left=0.25,
        crop_top=0.1,
        crop_right=0.25,
        crop_bottom=0.1,
    )
    left, top, right, bottom = item.crop_pixels(400, 200)
    assert (left, top) == (100, 20)
    assert (right, bottom) == (300, 180)


def test_page_item_clone_keeps_geometry():
    item = PageItem(
        id="i1",
        page_id="p1",
        document_id="d1",
        position_x=10.0,
        position_y=20.0,
        width=50.0,
        height=60.0,
        rotation=15.0,
    )
    clone = item.clone(new_id="i2")
    assert clone.id == "i2"
    assert clone.width == 50.0 and clone.rotation == 15.0
    assert clone.page_id == "p1"


def test_jpeg_encoding_quality_affects_size(image_factory):
    source = image_factory(fmt="PNG", size=(900, 600))
    image = load_rgb(source)
    low = encode_jpeg(image, 50)
    high = encode_jpeg(image, 95)
    assert len(low) < len(high)


def _stripes(width: int, height: int, period: int = 7) -> Image.Image:
    """A high-frequency pattern: aliasing collapses it to moire, not grey."""
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, period):
        draw.rectangle((x, 0, x + period // 2, height - 1), fill=(0, 0, 0))
    return image


def test_rotate_image_exact_angles_are_lossless(image_factory):
    """90/180/270 must stay exact transposes - no resampling, no JPEG-style loss."""
    source = image_factory(fmt="PNG", size=(120, 80))
    image = load_rgb(source)
    for angle, transpose in (
        (90.0, Image.Transpose.ROTATE_270),
        (180.0, Image.Transpose.ROTATE_180),
        (270.0, Image.Transpose.ROTATE_90),
    ):
        rotated = rotate_image(image, angle)
        expected = image.transpose(transpose)
        assert rotated.size == expected.size
        assert ImageChops.difference(rotated, expected).getbbox() is None, (
            f"{angle} deg must be a lossless transpose"
        )


def test_rotate_image_zero_angle_returns_input(image_factory):
    source = image_factory(fmt="PNG", size=(60, 40))
    image = load_rgb(source)
    assert rotate_image(image, 0.0) is image


def test_rotate_image_supersampling_smooths_edges():
    """A slanted edge must be averaged, not snapped to whole pixels.

    Ground truth is the same rotation done at 8x and box-reduced.  A plain
    single-pass rotate leaves a hard staircase, so it sits far from the truth;
    the supersampled version must land much closer.
    """
    image = _stripes(300, 475, period=9)

    big = image.resize((image.width * 8, image.height * 8), Image.Resampling.BICUBIC)
    truth = big.rotate(
        -30.0, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255)
    )
    truth = truth.resize(
        (round(truth.width / 8), round(truth.height / 8)), Image.Resampling.BOX
    )

    plain = image.rotate(
        -30.0, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255)
    )
    smooth = rotate_image(image, 30.0, supersample=4)

    def mae(candidate: Image.Image) -> float:
        reference = truth
        if candidate.size != truth.size:
            reference = truth.resize(candidate.size, Image.Resampling.BOX)
        a = np.asarray(candidate.convert("L"), dtype=np.float32)
        b = np.asarray(reference.convert("L"), dtype=np.float32)
        return float(np.abs(a - b).mean())

    plain_error = mae(plain)
    smooth_error = mae(smooth)
    assert smooth_error < plain_error * 0.5, (
        f"supersampled rotation should be far closer to the truth: "
        f"plain={plain_error:.3f} smooth={smooth_error:.3f}"
    )


def test_rotate_image_respects_memory_guard(monkeypatch):
    """An oversized canvas must step the factor down, not blow up memory."""
    from core import image_service

    monkeypatch.setattr(image_service, "MAX_SUPERSAMPLE_PIXELS", 1_000)
    image = _stripes(50, 60, period=5)
    rotated = rotate_image(image, 30.0, supersample=8)
    # Still rotated and still a sane size - just without supersampling.
    assert rotated.width > 0 and rotated.height > 0
    assert rotated.size == image.rotate(
        -30.0, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255)
    ).size

