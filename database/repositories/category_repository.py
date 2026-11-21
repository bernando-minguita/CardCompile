"""Document category row access."""

from __future__ import annotations

import uuid

from database.repositories.base import BaseRepository
from models.category import DEFAULT_CATEGORIES, Category


class CategoryRepository(BaseRepository):
    """CRUD operations for document categories."""

    def list_for_project(self, project_id: str) -> list[Category]:
        rows = self._all(
            "SELECT * FROM categories WHERE project_id = ? ORDER BY sort_order, name",
            (project_id,),
        )
        return [Category.from_row(row) for row in rows]

    def get(self, category_id: str) -> Category | None:
        row = self._one("SELECT * FROM categories WHERE id = ?", (category_id,))
        return Category.from_row(row) if row else None

    def find_by_name(self, project_id: str, name: str) -> Category | None:
        row = self._one(
            "SELECT * FROM categories WHERE project_id = ? AND lower(name) = lower(?)",
            (project_id, name),
        )
        return Category.from_row(row) if row else None

    def next_sort_order(self, project_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories "
                "WHERE project_id = ?",
                (project_id,),
                default=0,
            )
        )

    def create(self, project_id: str, name: str, sort_order: int | None = None) -> Category:
        order = self.next_sort_order(project_id) if sort_order is None else sort_order
        category = Category(
            id=str(uuid.uuid4()),
            project_id=project_id,
            name=name.strip(),
            sort_order=order,
        )
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO categories (id, project_id, name, sort_order) "
                "VALUES (?, ?, ?, ?)",
                (category.id, category.project_id, category.name, category.sort_order),
            )
        return category

    def rename(self, category_id: str, name: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE categories SET name = ? WHERE id = ?", (name.strip(), category_id)
            )

    def delete(self, category_id: str) -> None:
        """Delete a category; documents keep existing with a NULL category."""
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    def resequence(self, project_id: str, ordered_ids: list[str]) -> None:
        with self.db.transaction() as connection:
            for index, category_id in enumerate(ordered_ids):
                connection.execute(
                    "UPDATE categories SET sort_order = ? WHERE id = ? AND project_id = ?",
                    (index, category_id, project_id),
                )

    def ensure_defaults(self, project_id: str) -> list[Category]:
        """Create the default document categories when none exist."""
        existing = self.list_for_project(project_id)
        if existing:
            return existing
        for name in DEFAULT_CATEGORIES:
            self.create(project_id, name)
        return self.list_for_project(project_id)
