"""Paper sizes and millimetre/point conversion helpers.

Every measurement stored in the database is expressed in millimetres.
"""

from __future__ import annotations

from core.paths import MM_TO_PT, PT_TO_MM

#: Portrait dimensions in millimetres for the supported paper sizes.
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}

CUSTOM_PAPER = "Custom"
DEFAULT_MARGIN_MM = 10.0
MIN_PAGE_MM = 50.0
MAX_PAGE_MM = 2000.0


def available_paper_sizes() -> list[str]:
    return [*PAPER_SIZES_MM.keys(), CUSTOM_PAPER]


def normalize_orientation(orientation: str | None) -> str:
    text = (orientation or "portrait").strip().lower()
    return "landscape" if text.startswith("land") else "portrait"


def normalize_paper_size(paper_size: str | None) -> str:
    text = (paper_size or "A4").strip()
    for name in PAPER_SIZES_MM:
        if text.lower() == name.lower():
            return name
    return CUSTOM_PAPER if text.lower() == "custom" else "A4"


def page_dimensions(
    paper_size: str,
    orientation: str,
    custom_width_mm: float | None = None,
    custom_height_mm: float | None = None,
) -> tuple[float, float]:
    """Return the page width/height in millimetres for the given settings.

    ``PAPER_SIZES_MM`` stores portrait dimensions; landscape simply swaps the
    two values.  Custom pages are stored exactly as entered for the requested
    orientation.
    """
    paper = normalize_paper_size(paper_size)
    orient = normalize_orientation(orientation)

    if paper == CUSTOM_PAPER:
        width = float(custom_width_mm or PAPER_SIZES_MM["A4"][0])
        height = float(custom_height_mm or PAPER_SIZES_MM["A4"][1])
        width = max(MIN_PAGE_MM, min(MAX_PAGE_MM, width))
        height = max(MIN_PAGE_MM, min(MAX_PAGE_MM, height))
        if orient == "landscape" and height > width:
            width, height = height, width
        elif orient == "portrait" and width > height:
            width, height = height, width
        return round(width, 2), round(height, 2)

    width, height = PAPER_SIZES_MM[paper]
    if orient == "landscape":
        return height, width
    return width, height


def mm_to_pt(value_mm: float) -> float:
    return float(value_mm) * MM_TO_PT


def pt_to_mm(value_pt: float) -> float:
    return float(value_pt) * PT_TO_MM


def describe_page_size(paper_size: str, orientation: str, width_mm: float, height_mm: float) -> str:
    """Human readable one line description used in page navigators."""
    orient = "Landscape" if normalize_orientation(orientation) == "landscape" else "Portrait"
    return f"{paper_size} {orient} - {width_mm:.0f} x {height_mm:.0f} mm"
