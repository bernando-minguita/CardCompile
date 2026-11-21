"""Pixmap loading with a small LRU cache.

Original scans can be large, so the canvas loads a downscaled RGBA copy once and
reuses it for painting, thumbnails and hit testing.
"""

from __future__ import annotations

from pathlib import Path
from collections import OrderedDict

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from core.image_service import (
    ImageValidationError,
    load_corrected_display_image,
    load_display_image,
)

#: LRU cache of decoded pixmaps, bounded by count and by memory.
_CACHE: "OrderedDict[tuple, QPixmap]" = OrderedDict()
_MAX_ENTRIES = 24
_MAX_BYTES = 128 * 1024 * 1024  # 128 MB


def _pixmap_bytes(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * 4


def _evict_until_fits(extra: int = 0) -> None:
    total = sum(_pixmap_bytes(item) for item in _CACHE.values()) + extra
    while _CACHE and (len(_CACHE) >= _MAX_ENTRIES or total > _MAX_BYTES):
        _, evicted = _CACHE.popitem(last=False)
        total -= _pixmap_bytes(evicted)


def pil_to_qimage(image: Image.Image) -> QImage:
    """Convert a PIL RGB image into a QImage (copied, Qt owns the data)."""
    rgb = image.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(image))


def load_pixmap(path: str | Path, max_side: int = 1600) -> QPixmap:
    """Load a cached QPixmap for *path*; returns a null pixmap on failure."""
    source = Path(path)
    try:
        stamp = source.stat().st_mtime
    except OSError:
        return QPixmap()
    key = (str(source).lower(), stamp, max_side, None)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)  # LRU: recently used entries survive
        return cached
    try:
        image = load_display_image(source, max_side=max_side)
    except ImageValidationError:
        return QPixmap()
    pixmap = pil_to_qpixmap(image)
    _evict_until_fits(_pixmap_bytes(pixmap))
    _CACHE[key] = pixmap
    return pixmap


def load_corrected_pixmap(
    path: str | Path,
    correction: tuple,
    max_side: int = 1600,
    crop: tuple[int, int, int, int] | None = None,
) -> QPixmap:
    """Load a cached QPixmap with the item's crop and corrections applied.

    ``correction`` is :meth:`models.page_item.PageItem.correction_key` and
    ``crop`` is :meth:`PageItem.crop_pixels`.  Both are part of the cache key:
    the correction because a slider or a corner change must produce new pixels
    (returning the previous entry looked like the correction "did nothing"), and
    the crop because the canvas paints from this pixmap - leaving the crop out
    of the key meant changing the crop never repainted the item.
    """
    source = Path(path)
    try:
        stamp = source.stat().st_mtime
    except OSError:
        return QPixmap()
    key = (str(source).lower(), stamp, max_side, correction, crop)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached
    quad = tuple(correction[:8]) if correction else None
    try:
        image = load_corrected_display_image(
            source,
            max_side=max_side,
            crop=crop,
            quad=quad,
            brightness=correction[8] if correction else 1.0,
            contrast=correction[9] if correction else 1.0,
            saturation=correction[10] if correction else 1.0,
            sharpness=correction[11] if correction else 1.0,
            shadow=bool(correction[12]) if correction else False,
        )
    except ImageValidationError:
        return QPixmap()
    pixmap = pil_to_qpixmap(image)
    _evict_until_fits(_pixmap_bytes(pixmap))
    _CACHE[key] = pixmap
    return pixmap


def load_thumbnail_pixmap(path: str | Path, max_side: int = 240) -> QPixmap:
    """Load a small pixmap for organizer cards, from the stored thumbnail."""
    if not path:
        return QPixmap()
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return QPixmap()
    if max(pixmap.width(), pixmap.height()) > max_side:
        pixmap = pixmap.scaled(
            max_side,
            max_side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pixmap


def clear_cache() -> None:
    _CACHE.clear()
