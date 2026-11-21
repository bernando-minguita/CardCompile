"""Export history entry model."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(slots=True)
class ExportRecord:
    id: str
    project_id: str
    output_path: str
    page_count: int = 0
    quality: str = "High"
    exported_at: str = field(default_factory=_now)

    @property
    def path(self) -> Path:
        return Path(self.output_path)

    @property
    def exists(self) -> bool:
        try:
            return self.path.is_file()
        except OSError:
            return False

    @property
    def file_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> "ExportRecord":
        data = dict(row)
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            output_path=data["output_path"],
            page_count=int(data.get("page_count") or 0),
            quality=data.get("quality") or "High",
            exported_at=data.get("exported_at") or _now(),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "output_path": self.output_path,
            "page_count": self.page_count,
            "quality": self.quality,
            "exported_at": self.exported_at,
        }
