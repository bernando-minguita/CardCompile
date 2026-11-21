"""PDF page model."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from core.paper import describe_page_size, normalize_orientation, page_dimensions


@dataclass(slots=True)
class PdfPage:
    id: str
    project_id: str
    page_number: int
    paper_size: str = "A4"
    orientation: str = "portrait"
    width_mm: float = 210.0
    height_mm: float = 297.0
    margin_top: float = 10.0
    margin_bottom: float = 10.0
    margin_left: float = 10.0
    margin_right: float = 10.0
    background_color: str = "#FFFFFF"

    # ------------------------------------------------------------------ geometry
    def printable_rect(self) -> tuple[float, float, float, float]:
        """Return ``(x, y, width, height)`` of the printable area in mm."""
        return (
            self.margin_left,
            self.margin_top,
            max(1.0, self.width_mm - self.margin_left - self.margin_right),
            max(1.0, self.height_mm - self.margin_top - self.margin_bottom),
        )

    def apply_paper(
        self,
        paper_size: str,
        orientation: str,
        custom_width_mm: float | None = None,
        custom_height_mm: float | None = None,
    ) -> None:
        """Update paper size/orientation and recompute the millimetre size."""
        width, height = page_dimensions(
            paper_size, orientation, custom_width_mm, custom_height_mm
        )
        self.paper_size = paper_size
        self.orientation = normalize_orientation(orientation)
        self.width_mm = width
        self.height_mm = height
        self.clamp_margins()

    def clamp_margins(self) -> None:
        """Keep margins inside the page so a printable area always exists."""
        max_h = max(0.0, self.height_mm / 2.0 - 5.0)
        max_w = max(0.0, self.width_mm / 2.0 - 5.0)
        self.margin_top = min(max(0.0, self.margin_top), max_h)
        self.margin_bottom = min(max(0.0, self.margin_bottom), max_h)
        self.margin_left = min(max(0.0, self.margin_left), max_w)
        self.margin_right = min(max(0.0, self.margin_right), max_w)

    def describe(self) -> str:
        return describe_page_size(
            self.paper_size, self.orientation, self.width_mm, self.height_mm
        )

    # --------------------------------------------------------------- factories
    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> "PdfPage":
        data = dict(row)
        page = cls(
            id=data["id"],
            project_id=data["project_id"],
            page_number=int(data.get("page_number") or 1),
            paper_size=data.get("paper_size") or "A4",
            orientation=data.get("orientation") or "portrait",
            width_mm=float(data.get("width_mm") or 210.0),
            height_mm=float(data.get("height_mm") or 297.0),
            margin_top=float(data.get("margin_top") or 0.0),
            margin_bottom=float(data.get("margin_bottom") or 0.0),
            margin_left=float(data.get("margin_left") or 0.0),
            margin_right=float(data.get("margin_right") or 0.0),
            background_color=data.get("background_color") or "#FFFFFF",
        )
        return page

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "page_number": self.page_number,
            "paper_size": self.paper_size,
            "orientation": self.orientation,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "margin_top": self.margin_top,
            "margin_bottom": self.margin_bottom,
            "margin_left": self.margin_left,
            "margin_right": self.margin_right,
            "background_color": self.background_color,
        }
