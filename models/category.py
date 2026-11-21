"""Document category model."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: Categories created for every new project (fully editable in Settings).
DEFAULT_CATEGORIES = (
    "Iqama",
    "Driving License",
    "Istimara",
    "Passport",
    "Other",
)


@dataclass(slots=True)
class Category:
    id: str
    project_id: str
    name: str
    sort_order: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> "Category":
        data = dict(row)
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            name=data["name"],
            sort_order=int(data.get("sort_order") or 0),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "sort_order": self.sort_order,
        }
