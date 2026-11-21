"""Project creation helpers: folder naming, page building, project setup."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from core.paper import page_dimensions
from core.paths import PROJECT_DB_FILENAME
from models.pdf_page import PdfPage
from models.project import Project

LOGGER = get_logger("projects")

_INVALID_FOLDER_CHARS = re.compile(r'[<>:\"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


PROBLEM_MISSING = "missing"
PROBLEM_EMPTY = "empty"
PROBLEM_FOREIGN = "foreign"
PROBLEM_UNREADABLE = "unreadable"


def sanitize_folder_name(name: str) -> str:
    """Turn a project name into a safe Windows folder name."""
    cleaned = _INVALID_FOLDER_CHARS.sub("_", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "Project"
    if cleaned.lower() in _RESERVED_NAMES:
        cleaned = f"{cleaned}_project"
    return cleaned[:80]


def build_page(
    project: Project,
    page_size: str,
    orientation: str,
    margin_mm: float = 10.0,
    custom_width_mm: float | None = None,
    custom_height_mm: float | None = None,
    page_number: int = 1,
) -> PdfPage:
    """Create a :class:`PdfPage` with the requested paper size and margins."""
    width, height = page_dimensions(
        page_size, orientation, custom_width_mm, custom_height_mm
    )
    page = PdfPage(
        id=str(uuid.uuid4()),
        project_id=project.id,
        page_number=page_number,
        paper_size=page_size,
        orientation=orientation,
        width_mm=width,
        height_mm=height,
        margin_top=margin_mm,
        margin_bottom=margin_mm,
        margin_left=margin_mm,
        margin_right=margin_mm,
    )
    page.clamp_margins()
    return page