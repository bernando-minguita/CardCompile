"""Rendering of an existing PDF file for the preview window.

This service is deliberately independent from the project database: it only
reads the produced PDF so the preview always shows the real output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from core.logging_setup import get_logger
from core.paths import PT_TO_MM

LOGGER = get_logger("preview")


class PreviewError(RuntimeError):
    """Raised when a PDF cannot be previewed."""


@dataclass(slots=True)
class PdfPageInfo:
    index: int
    width_mm: float
    height_mm: float
    has_images: bool


class PreviewService:
    """Renders PDF pages to PNG bytes for the Qt preview window."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.is_file():
            raise PreviewError(f"The PDF file was not found:\n{self._path}")

    @property
    def path(self) -> Path:
        return self._path

    def _open(self):
        try:
            document = pymupdf.open(self._path)
        except Exception as exc:
            raise PreviewError(f"The PDF cannot be opened: {exc}") from exc
        if document.needs_pass:
            document.close()
            raise PreviewError("The PDF is password protected.")
        return document

    def page_count(self) -> int:
        document = self._open()
        try:
            return document.page_count
        finally:
            document.close()

    def metadata(self) -> dict:
        document = self._open()
        try:
            return dict(document.metadata or {})
        finally:
            document.close()

    def pages_info(self) -> list[PdfPageInfo]:
        document = self._open()
        try:
            infos: list[PdfPageInfo] = []
            for index in range(document.page_count):
                page = document[index]
                infos.append(
                    PdfPageInfo(
                        index=index,
                        width_mm=round(page.rect.width * PT_TO_MM, 2),
                        height_mm=round(page.rect.height * PT_TO_MM, 2),
                        has_images=bool(page.get_images(full=True)),
                    )
                )
            return infos
        finally:
            document.close()

    def render_page_png(self, index: int, dpi: int = 120) -> bytes:
        """Render page *index* (zero based) to PNG bytes."""
        document = self._open()
        try:
            if index < 0 or index >= document.page_count:
                raise PreviewError("Page number out of range.")
            page = document[index]
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            return pixmap.tobytes("png")
        except PreviewError:
            raise
        except Exception as exc:
            raise PreviewError(f"Page could not be rendered: {exc}") from exc
        finally:
            document.close()
