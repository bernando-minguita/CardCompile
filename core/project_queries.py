"""Project query helpers: summaries, listing, and project lookup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ProjectSummary:
    """Lightweight statistics shown on the dashboard."""

    db_path: str
    name: str
    description: str
    project_folder: str
    document_count: int
    page_count: int
    updated_at: str
    valid: bool = True
    #: Why the project cannot be used ("" while everything is fine).
    problem: str = ""