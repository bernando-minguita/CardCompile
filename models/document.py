"""Imported document model.

The original file is never copied over or modified: ``source_path`` always
points at the user's original scan and ``file_hash`` allows detecting when the
file changed or went missing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(slots=True)
class Document:
    id: str
    project_id: str
    filename: str
    source_path: str
    file_hash: str
    category_id: str | None = None
    original_width: int = 0
    original_height: int = 0
    file_size: int = 0
    imported_at: str = field(default_factory=_now)
    thumbnail_path: str = ""
    image_format: str = ""
    sort_order: int = 0
    dpi_x: float = 0.0
    dpi_y: float = 0.0
    #: ``False`` once the image was removed from the Image Library while it was
    #: still placed on a page.  The row is kept so the canvas/PDF keep working.
    in_library: bool = True

    # Transient (not persisted) information used by the organizer.
    category_name: str = ""
    page_numbers: list[int] = field(default_factory=list)

    # ----------------------------------------------------------- physical size
    @property
    def has_resolution(self) -> bool:
        return self.dpi_x > 1.0

    def effective_dpi(self, fallback_dpi: float = 300.0) -> float:
        """Resolution used to reproduce the physical size of the scan."""
        if self.dpi_x > 1.0:
            return float(self.dpi_x)
        fallback = float(fallback_dpi) if fallback_dpi and fallback_dpi > 1.0 else 300.0
        return fallback

    def natural_size_mm(self, fallback_dpi: float = 300.0) -> tuple[float, float]:
        """Physical size of the image in millimetres.

        Uses the DPI stored in the file; when the file carries no resolution the
        configured fallback DPI is used.  The pixel aspect ratio is preserved so
        the image is never distorted.
        """
        dpi = self.effective_dpi(fallback_dpi)
        if self.original_width <= 0 or self.original_height <= 0:
            return (0.0, 0.0)
        width_mm = self.original_width / dpi * 25.4
        height_mm = width_mm * (self.original_height / self.original_width)
        return (round(width_mm, 2), round(height_mm, 2))

    def describe_resolution(self, fallback_dpi: float = 300.0) -> str:
        width_mm, height_mm = self.natural_size_mm(fallback_dpi)
        source = (
            f"{self.dpi_x:.0f} dpi" if self.has_resolution else f"{fallback_dpi:.0f} dpi (assumed)"
        )
        return f"{width_mm:.1f} x {height_mm:.1f} mm at {source}"

    @property
    def source(self) -> Path:
        return Path(self.source_path)

    @property
    def exists(self) -> bool:
        try:
            return self.source.is_file()
        except OSError:
            return False

    @property
    def megapixels(self) -> float:
        return (self.original_width * self.original_height) / 1_000_000.0

    def human_size(self) -> str:
        size = float(self.file_size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> "Document":
        data = dict(row)
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            filename=data["filename"],
            source_path=data["source_path"],
            file_hash=data["file_hash"],
            category_id=data.get("category_id"),
            original_width=int(data.get("original_width") or 0),
            original_height=int(data.get("original_height") or 0),
            file_size=int(data.get("file_size") or 0),
            imported_at=data.get("imported_at") or _now(),
            thumbnail_path=data.get("thumbnail_path") or "",
            image_format=data.get("image_format") or "",
            sort_order=int(data.get("sort_order") or 0),
            dpi_x=float(data.get("dpi_x") or 0.0),
            dpi_y=float(data.get("dpi_y") or 0.0),
            in_library=bool(int(data.get("in_library", 1) if data.get("in_library") is not None else 1)),
            category_name=data.get("category_name") or "",
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "filename": self.filename,
            "source_path": self.source_path,
            "file_hash": self.file_hash,
            "category_id": self.category_id,
            "original_width": self.original_width,
            "original_height": self.original_height,
            "file_size": self.file_size,
            "imported_at": self.imported_at,
            "thumbnail_path": self.thumbnail_path,
            "image_format": self.image_format,
            "sort_order": self.sort_order,
            "dpi_x": self.dpi_x,
            "dpi_y": self.dpi_y,
            "in_library": self.in_library,
        }
