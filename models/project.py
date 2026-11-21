"""Project model."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.paths import PROJECT_DB_FILENAME


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(slots=True)
class Project:
    """A CardCompile project stored in its own SQLite database."""

    id: str
    name: str
    project_folder: str
    description: str = ""
    default_page_size: str = "A4"
    default_orientation: str = "portrait"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    # ------------------------------------------------------------------ paths
    @property
    def folder(self) -> Path:
        return Path(self.project_folder)

    @property
    def db_path(self) -> Path:
        return self.folder / PROJECT_DB_FILENAME

    @property
    def documents_folder(self) -> Path:
        return self.folder / "documents"

    @property
    def thumbs_folder(self) -> Path:
        return self.folder / "thumbnails"

    def ensure_folders(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.documents_folder.mkdir(parents=True, exist_ok=True)
        self.thumbs_folder.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- factories
    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> "Project":
        data = dict(row)
        return cls(
            id=data["id"],
            name=data["name"],
            project_folder=data["project_folder"],
            description=data.get("description") or "",
            default_page_size=data.get("default_page_size") or "A4",
            default_orientation=data.get("default_orientation") or "portrait",
            created_at=data.get("created_at") or _now(),
            updated_at=data.get("updated_at") or _now(),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "project_folder": self.project_folder,
            "default_page_size": self.default_page_size,
            "default_orientation": self.default_orientation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def touch(self) -> None:
        self.updated_at = _now()
