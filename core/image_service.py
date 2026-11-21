"""Image loading, validation, thumbnails and rendering helpers.

Every operation works on a *copy* of the pixels: original scan files are only
ever opened for reading and are never written to or modified.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from core.logging_setup import get_logger

LOGGER = get_logger("images")

#: Guard against decompression bombs while still allowing large scans.
Image.MAX_IMAGE_PIXELS = 300_000_000

READ_CHUNK = 1024 * 1024
JPEG_QUALITY = 92
THUMBNAIL_QUALITY = 82

#: Neutral value for every tone multiplier (no change).
NEUTRAL = 1.0
#: Bounds accepted for the tone sliders (the adjustments dialog uses the same).
ADJUST_MIN = 0.2
ADJUST_MAX = 3.0

#: Upper bound on the working canvas for :func:`rotate_image`.  Supersampling
#: multiplies the pixel count by the square of the factor, so a large scan is
#: rotated in one pass instead of risking a multi-gigabyte temporary image.
MAX_SUPERSAMPLE_PIXELS = 64_000_000


class ImageValidationError(RuntimeError):
    """Raised when a file cannot be used as a document image."""


@dataclass(slots=True)
class ImageInfo:
    """Result of inspecting an image file on disk."""

    path: str
    width: int
    height: int
    image_format: str
    file_size: int
    file_hash: str
    dpi_x: float = 0.0
    dpi_y: float = 0.0

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000.0

    @property
    def has_resolution(self) -> bool:
        return self.dpi_x > 1.0


def compute_sha256(path: str | Path) -> str:
    """Hash a file without loading it into memory."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(READ_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise ImageValidationError(f"Cannot read file: {exc}") from exc
    return digest.hexdigest()


def is_supported_extension(path: str | Path, extensions: list[str]) -> bool:
    return Path(path).suffix.lower() in {ext.lower() for ext in extensions}


MIN_PLAUSIBLE_DPI = 20.0
MAX_PLAUSIBLE_DPI = 4800.0


def read_image_dpi(image: Image.Image) -> tuple[float, float]:
    """Return the resolution stored in an image file, or ``(0, 0)``.

    JPEG, PNG (pHYs) and TIFF files may carry a resolution that lets the
    application reproduce the physical size of the scan.
    """
    candidates: list[tuple[float, float]] = []
    info = getattr(image, "info", None) or {}
    for key in ("dpi", "resolution"):
        value = info.get(key)
        if isinstance(value, (tuple, list)) and len(value) == 2:
            try:
                candidates.append((float(value[0]), float(value[1])))
            except (TypeError, ValueError):
                pass
    single_x = info.get("dpi_x")
    single_y = info.get("dpi_y")
    if isinstance(single_x, (int, float)):
        candidates.append(
            (float(single_x), float(single_y) if isinstance(single_y, (int, float)) else float(single_x))
        )
    for x_dpi, y_dpi in candidates:
        if MIN_PLAUSIBLE_DPI <= x_dpi <= MAX_PLAUSIBLE_DPI:
            if not (MIN_PLAUSIBLE_DPI <= y_dpi <= MAX_PLAUSIBLE_DPI):
                y_dpi = x_dpi
            return round(x_dpi, 2), round(y_dpi, 2)
    return 0.0, 0.0


def _exif_orientation(image: Image.Image) -> int:
    """Return the EXIF orientation tag (1 = normal, 5-8 = rotating)."""
    raw = image.info.get("exif")
    if not raw:
        return 1
    try:
        exif = Image.Exif()
        exif.load(raw)
        value = exif.get(274, 1)
    except Exception:  # pragma: no cover - corrupt EXIF payload
        return 1
    try:
        orientation = int(value)
    except (TypeError, ValueError):
        return 1
    return orientation if 1 <= orientation <= 8 else 1


_ROTATING_ORIENTATIONS = frozenset({5, 6, 7, 8})


def probe_image(path: str | Path, *, with_hash: bool = True) -> ImageInfo:
    """Validate an image file and return its metadata.

    Raises :class:`ImageValidationError` for unreadable, corrupt or unsupported
    files so the import dialog can report them individually.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ImageValidationError("File not found")
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise ImageValidationError(f"Cannot read file: {exc}") from exc
    if size == 0:
        raise ImageValidationError("File is empty")

    try:
        with Image.open(file_path) as image:
            image_format = (image.format or file_path.suffix.lstrip(".")).upper()
            width, height = image.size
            dpi_x, dpi_y = read_image_dpi(image)
            # Apply EXIF orientation so stored dimensions match what the user
            # sees after ``load_rgb`` / preview transposition (orientations 5-8
            # rotate the image 90 degrees, swapping width/height and DPI axes).
            if _exif_orientation(image) in _ROTATING_ORIENTATIONS:
                width, height = height, width
                dpi_x, dpi_y = dpi_y, dpi_x
            # Force a real decode so truncated files are detected here and not
            # later during export.
            image.verify()
    except Exception as exc:  # Pillow raises many exception types
        raise ImageValidationError(f"Unsupported or corrupt image ({exc})") from exc

    if width <= 0 or height <= 0:
        raise ImageValidationError("Image has invalid dimensions")

    file_hash = compute_sha256(file_path) if with_hash else ""
    return ImageInfo(
        path=str(file_path),
        width=int(width),
        height=int(height),
        image_format=image_format,
        file_size=int(size),
        file_hash=file_hash,
        dpi_x=dpi_x,
        dpi_y=dpi_y,
    )


def load_rgb(path: str | Path) -> Image.Image:
    """Open an image, apply the EXIF orientation and return RGB pixels."""
    try:
        with Image.open(path) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                return background
            if image.mode != "RGB":
                return image.convert("RGB")
            return image.copy()
    except Exception as exc:
        raise ImageValidationError(f"Could not load image: {exc}") from exc


def load_display_image(
    path: str | Path,
    max_side: int = 1600,
    crop: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    """Load a downscaled copy used for the editor canvas (keeps memory low).

    ``crop`` is an optional pixel box ``(left, top, right, bottom)``: the image is
    cropped first so the preview shows exactly the visible part of the placed
    item instead of the whole original scan.
    """
    image = cropped_image(path, crop) if crop else load_rgb(path)
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return image


def quad_in_crop_space(
    quad: tuple[float, float, float, float, float, float, float, float],
    crop_box: tuple[float, float, float, float],
) -> tuple[float, float, float, float, float, float, float, float]:
    """Re-express a quad from ORIGINAL space in the CROPPED image's space.

    ``PageItem.quad()`` and ``PageItem.crop_box()`` are both fractions of the
    *original* image, but every render path crops first and then warps.  Feeding
    the original-space quad to the cropped image made the warp sample the wrong
    region entirely (a card the user selected on one quadrant came back from
    another).  This maps each corner through the crop rectangle so the quad
    means the same thing in both spaces.

    Corners outside the crop map outside ``0..1``; that is intentional - the
    pixels were cropped away, so the warp legitimately has nothing to sample.
    """
    left, top, right, bottom = crop_box
    span_x = max(1e-6, right - left)
    span_y = max(1e-6, bottom - top)
    values: list[float] = []
    for index in range(0, 8, 2):
        values.append((quad[index] - left) / span_x)
        values.append((quad[index + 1] - top) / span_y)
    return tuple(values)  # type: ignore[return-value]


def _crop_with_box(
    image: Image.Image, crop: tuple[int, int, int, int] | None
) -> tuple[Image.Image, tuple[float, float, float, float] | None]:
    """Crop *image* and return it with the crop as normalised fractions."""
    if not crop:
        return image, None
    width, height = image.size
    left, top, right, bottom = crop
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    box = (
        left / width,
        top / height,
        right / width,
        bottom / height,
    )
    return image.crop((left, top, right, bottom)), box


def load_corrected_display_image(
    path: str | Path,
    *,
    max_side: int = 1600,
    crop: tuple[int, int, int, int] | None = None,
    quad: tuple[float, float, float, float, float, float, float, float] | None = None,
    brightness: float = NEUTRAL,
    contrast: float = NEUTRAL,
    saturation: float = NEUTRAL,
    sharpness: float = NEUTRAL,
    shadow: bool = False,
) -> Image.Image:
    """Canvas copy of an image with its crop and corrections applied.

    The crop is applied first, then the quad is re-expressed in the cropped
    image's space before warping: both are fractions of the *original* image, so
    warping the cropped image with an original-space quad sampled the wrong
    region.  Tone work runs last, on the small result, so it is cheap.
    """
    image = load_rgb(path)
    if crop:
        image, crop_box = _crop_with_box(image, crop)
    else:
        crop_box = None
    if max(image.size) > max_side:
        # Downscale before the warp: the warp is the expensive step and it
        # resamples anyway, so nothing visible is lost.
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    identity = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
    has_quad = quad is not None and any(
        abs(a - b) > 0.0005 for a, b in zip(quad, identity)
    )
    if has_quad:
        effective = quad_in_crop_space(quad, crop_box) if crop_box else quad
        image = apply_perspective(image, effective)
    return apply_adjustments(
        image,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
        sharpness=sharpness,
        shadow=shadow,
    )


def write_thumbnail(path: str | Path, target: str | Path, max_side: int = 240) -> Path:
    """Create a JPEG thumbnail of *path* at *target* and return the path."""
    image = load_rgb(path)
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
    return destination


def cropped_image(
    path: str | Path, crop: tuple[int, int, int, int] | None
) -> Image.Image:
    """Return the (optionally cropped) image used for export or preview."""
    image = load_rgb(path)
    if crop:
        left, top, right, bottom = crop
        left = max(0, min(left, image.width - 1))
        top = max(0, min(top, image.height - 1))
        right = max(left + 1, min(right, image.width))
        bottom = max(top + 1, min(bottom, image.height))
        image = image.crop((left, top, right, bottom))
    return image


def resize_for_dpi(
    image: Image.Image,
    target_width_mm: float,
    target_height_mm: float,
    dpi: int | None,
) -> Image.Image:
    """Scale *image* for the requested print resolution without upscaling.

    ``dpi=None`` keeps the source resolution ("Original" quality preset).
    """
    if dpi is None:
        return image
    target_w = max(1, int(round(target_width_mm / 25.4 * dpi)))
    target_h = max(1, int(round(target_height_mm / 25.4 * dpi)))
    if target_w >= image.width and target_h >= image.height:
        return image  # never upscale an existing scan
    scale = min(target_w / image.width, target_h / image.height)
    new_size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def rotate_image(
    image: Image.Image,
    angle_deg: float,
    *,
    fill: tuple[int, int, int] = (255, 255, 255),
    supersample: int = 4,
) -> Image.Image:
    """Rotate *image* with proper area coverage on its slanted edges.

    ``Image.rotate`` maps every destination pixel back to a single source
    point, so a slanted edge comes out as a hard one-pixel staircase.  Rotating
    an enlarged copy and averaging it back down gives each destination pixel the
    coverage of its whole area instead, which is what removes the staircase.

    Multiples of 90 degrees are exact transposes, so they skip the resampling
    entirely and stay lossless.
    """
    angle = float(angle_deg) % 360.0
    if angle < 0.01:
        return image
    exact = {
        90.0: Image.Transpose.ROTATE_270,
        180.0: Image.Transpose.ROTATE_180,
        270.0: Image.Transpose.ROTATE_90,
    }
    for candidate, transpose in exact.items():
        if abs(angle - candidate) < 0.01:
            return image.transpose(transpose)

    factor = max(1, int(supersample))
    # ``expand=True`` grows the canvas, so the guard has to measure the rotated
    # size, not the source size: a 45-degree rotation of a wide page needs up to
    # sqrt(2) more pixels per axis.  Step the factor down instead of giving up
    # entirely - a large scan still benefits from 2x.
    radians = math.radians(angle)
    cos_a, sin_a = abs(math.cos(radians)), abs(math.sin(radians))
    grown_w = image.width * cos_a + image.height * sin_a
    grown_h = image.width * sin_a + image.height * cos_a
    while factor > 1 and grown_w * grown_h * factor * factor > MAX_SUPERSAMPLE_PIXELS:
        factor -= 1

    source = image
    if factor > 1:
        source = image.resize(
            (image.width * factor, image.height * factor),
            Image.Resampling.BICUBIC,
        )
    rotated = source.rotate(
        -angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=fill
    )
    if factor == 1:
        return rotated
    return rotated.resize(
        (
            max(1, round(rotated.width / factor)),
            max(1, round(rotated.height / factor)),
        ),
        Image.Resampling.LANCZOS,
    )


def encode_jpeg(image: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def render_pdf_image_bytes(
    path: str | Path,
    crop: tuple[int, int, int, int] | None,
    target_width_mm: float,
    target_height_mm: float,
    dpi: int | None,
    quality: int = JPEG_QUALITY,
    correction: tuple | None = None,
) -> bytes:
    """Produce JPEG bytes sized for the page placement at the chosen DPI.

    ``correction`` is :meth:`PageItem.correction_key` (quad + tone values) and is
    applied at full resolution before the downscale, so the exported PDF carries
    the same perspective and tone as the canvas.
    """
    image = load_rgb(path)
    crop_box = None
    if crop:
        image, crop_box = _crop_with_box(image, crop)
    if correction:
        quad = tuple(correction[:8])
        identity = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
        if any(abs(a - b) > 0.0005 for a, b in zip(quad, identity)):
            # The stored quad is in ORIGINAL-image space; the image is cropped,
            # so it has to be remapped or the warp samples the wrong region.
            quad = quad_in_crop_space(quad, crop_box) if crop_box else quad
        else:
            quad = None
        image = correct_image(
            image,
            quad=quad,
            brightness=correction[8],
            contrast=correction[9],
            saturation=correction[10],
            sharpness=correction[11],
            shadow=bool(correction[12]),
            sharpen=False,
        )
    image = resize_for_dpi(image, target_width_mm, target_height_mm, dpi)
    if correction:
        # Sharpen after the resize, never before it.
        image = apply_sharpness(image, correction[11])
    return encode_jpeg(image, quality)


DPI_PRESET_VALUES: dict[str, int | None] = {
    "Draft": 150,
    "Standard": 200,
    "High": 300,
    "Original": None,
}


def dpi_for_preset(preset: str) -> int | None:
    return DPI_PRESET_VALUES.get(preset, 300)


# --------------------------------------------------------------- correction
def _clamp_adjust(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NEUTRAL
    if number != number:  # NaN
        return NEUTRAL
    return max(ADJUST_MIN, min(ADJUST_MAX, number))


def perspective_coefficients(
    quad: tuple[float, float, float, float, float, float, float, float],
    source_width: int,
    source_height: int,
    target_width: int | None = None,
    target_height: int | None = None,
) -> tuple[float, ...] | None:
    """Solve the 8 coefficients that map the output rectangle onto *quad*.

    ``quad`` holds the four source corners as normalised fractions in the order
    top-left, top-right, bottom-right, bottom-left.  ``Image.transform`` with
    ``Image.Transform.PERSPECTIVE`` maps *output* pixels back to *input* pixels,
    so the coefficients must be expressed in **pixel** coordinates: the
    destination is the output rectangle ``(target_width, target_height)`` and the
    source is the quad scaled up to the image's pixel size.

    Building the system in normalised 0..1 space instead - which is what this
    function used to do - produced coefficients that were numerically fine but
    meaningless to Pillow: every output pixel then mapped to a source point far
    outside the image, so the whole result came back black (a diagonal smear).
    ``source_width``/``source_height`` are therefore required, and
    ``target_width``/``target_height`` default to the source size.

    Returns ``None`` when the quad is degenerate (a corner collapsed onto
    another): the linear system then has no unique solution and warping would
    produce garbage.

    Solved with plain Gaussian elimination.  The project ships only PySide6,
    Pillow and PyMuPDF; adding numpy or OpenCV for an 8x8 system would break the
    deliberately small PyInstaller build.
    """
    if source_width <= 0 or source_height <= 0:
        return None
    x0, y0, x1, y1, x2, y2, x3, y3 = quad

    # Degeneracy is checked in normalised space so the test is independent of
    # the image resolution.
    normalised = ((x0, y0), (x1, y1), (x2, y2), (x3, y3))
    area = 0.0
    for index in range(4):
        cx, cy = normalised[index]
        nx, ny = normalised[(index + 1) % 4]
        area += cx * ny - nx * cy
    if abs(area) < 1e-6:
        return None

    width = int(target_width) if target_width else int(source_width)
    height = int(target_height) if target_height else int(source_height)
    if width < 2 or height < 2:
        return None

    dst = (
        (0.0, 0.0),
        (width - 1.0, 0.0),
        (width - 1.0, height - 1.0),
        (0.0, height - 1.0),
    )
    src = tuple(
        (x * source_width, y * source_height) for x, y in normalised
    )

    matrix: list[list[float]] = []
    for (u, v), (x, y) in zip(dst, src):
        matrix.append([u, v, 1.0, 0.0, 0.0, 0.0, -u * x, -v * x, x])
        matrix.append([0.0, 0.0, 0.0, u, v, 1.0, -u * y, -v * y, y])

    size = 8
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            return None
        if pivot != column:
            matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column]
        for index in range(column, size + 1):
            matrix[column][index] /= divisor
        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column]
            if factor == 0.0:
                continue
            for index in range(column, size + 1):
                matrix[row][index] -= factor * matrix[column][index]

    coefficients = tuple(matrix[row][size] for row in range(size))
    if not all(abs(value) < 1e9 for value in coefficients):
        return None
    return coefficients


def apply_perspective(
    image: Image.Image,
    quad: tuple[float, float, float, float, float, float, float, float],
) -> Image.Image:
    """Warp a skewed quadrilateral back to a true rectangle.

    The output keeps the *average* of the quad's opposite edge lengths, so a
    card photographed at an angle is not stretched into a different aspect
    ratio than it really has.
    """
    if image.width <= 0 or image.height <= 0:
        return image
    x0, y0, x1, y1, x2, y2, x3, y3 = quad
    width_px = (
        math.hypot((x1 - x0) * image.width, (y1 - y0) * image.height)
        + math.hypot((x2 - x3) * image.width, (y2 - y3) * image.height)
    ) / 2.0
    height_px = (
        math.hypot((x3 - x0) * image.width, (y3 - y0) * image.height)
        + math.hypot((x2 - x1) * image.width, (y2 - y1) * image.height)
    ) / 2.0
    target_w = max(1, int(round(width_px)))
    target_h = max(1, int(round(height_px)))
    # The coefficients depend on the output size, so it has to be known first.
    coefficients = perspective_coefficients(
        quad, image.width, image.height, target_w, target_h
    )
    if coefficients is None:
        return image
    try:
        return image.transform(
            (target_w, target_h),
            Image.Transform.PERSPECTIVE,
            coefficients,
            Image.Resampling.BICUBIC,
        )
    except (ValueError, OSError) as exc:  # pragma: no cover - defensive
        LOGGER.warning("Perspective correction failed: %s", exc)
        return image


def flatten_lighting(image: Image.Image, radius: int | None = None) -> Image.Image:
    """Even out uneven lighting (a hand shadow across the card).

    Divides the image by a heavily blurred copy of itself: a cheap illumination
    estimate.  Slow gradients (shadows, a lamp on one side) are removed while
    the card's own detail survives.

    ``ImageChops`` has no ``divide``, so the per-pixel division is done with
    ``ImageMath`` on each channel: ``out = src * 255 / (background + 1)``.  The
    ``+1`` keeps a fully black region from dividing by zero.
    """
    from PIL import ImageMath

    if image.width < 8 or image.height < 8:
        return image
    if radius is None:
        radius = max(8, int(min(image.width, image.height) / 12))
    try:
        background = image.filter(ImageFilter.GaussianBlur(radius=radius))
        channels = []
        for source_band, background_band in zip(image.split(), background.split()):
            # ``lambda_eval`` needs int literals (a float colour is rejected) and
            # returns an "I" image; ``convert("L")`` clamps it back to 0..255.
            normalised = ImageMath.lambda_eval(
                lambda args: args["a"] * 255 / (args["b"] + 1),
                a=source_band,
                b=background_band,
            )
            channels.append(normalised.convert("L"))
        return Image.merge("RGB", channels)
    except (ValueError, OSError, TypeError) as exc:  # pragma: no cover - defensive
        LOGGER.warning("Lighting flattening failed: %s", exc)
        return image


def apply_sharpness(image: Image.Image, amount: float) -> Image.Image:
    """Unsharp-mask sharpening scaled by *amount* (1.0 = neutral)."""
    if abs(amount - NEUTRAL) <= 0.005:
        return image
    percent = int(round(150 * (amount - 1.0)))
    if percent <= 0:
        return image
    return image.filter(
        ImageFilter.UnsharpMask(radius=2, percent=min(400, percent), threshold=3)
    )


def apply_adjustments(
    image: Image.Image,
    *,
    brightness: float = NEUTRAL,
    contrast: float = NEUTRAL,
    saturation: float = NEUTRAL,
    sharpness: float = NEUTRAL,
    shadow: bool = False,
    sharpen: bool = True,
) -> Image.Image:
    """Apply the non-destructive tone corrections stored on a page item.

    ``sharpen=False`` skips the sharpening pass so the caller can sharpen
    *after* the final downscale: sharpening before a resize wastes the effect
    and can alias.
    """
    result = image
    if shadow:
        result = flatten_lighting(result)
    brightness = _clamp_adjust(brightness)
    contrast = _clamp_adjust(contrast)
    saturation = _clamp_adjust(saturation)
    sharpness = _clamp_adjust(sharpness)
    if abs(brightness - NEUTRAL) > 0.005:
        result = ImageEnhance.Brightness(result).enhance(brightness)
    if abs(contrast - NEUTRAL) > 0.005:
        result = ImageEnhance.Contrast(result).enhance(contrast)
    if abs(saturation - NEUTRAL) > 0.005:
        result = ImageEnhance.Color(result).enhance(saturation)
    if sharpen:
        result = apply_sharpness(result, sharpness)
    return result


def correct_image(
    image: Image.Image,
    *,
    quad: tuple[float, float, float, float, float, float, float, float] | None = None,
    brightness: float = NEUTRAL,
    contrast: float = NEUTRAL,
    saturation: float = NEUTRAL,
    sharpness: float = NEUTRAL,
    shadow: bool = False,
    sharpen: bool = True,
) -> Image.Image:
    """Apply perspective first, then tone - the order that matters.

    Perspective correction decides which pixels exist, so it must run before
    any per-pixel tone work; otherwise the tone pass is applied to pixels that
    are about to be resampled away.
    """
    result = image
    if quad is not None:
        result = apply_perspective(result, quad)
    return apply_adjustments(
        result,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
        sharpness=sharpness,
        shadow=shadow,
        sharpen=sharpen,
    )
