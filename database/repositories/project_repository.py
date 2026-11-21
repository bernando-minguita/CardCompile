"""Project row access."""

from __future__ import annotations

from datetime import datetime

from database.repositories.base import BaseRepository
from models.project import Project


class ProjectRepository(BaseRepository):
    """CRUD operations for the ``projects`` table (one row per project DB)."""

    def insert(self, project: Project) -> Project:
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO projects (id, name, description, project_folder, "
                "default_page_size, default_orientation, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project.id,
                    project.name,
                    project.description,
                    project.project_folder,
                    project.default_page_size,
                    project.default_orientation,
                    project.created_at,
                    project.updated_at,
                ),
            )
        return project

    def update(self, project: Project) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE projects SET name = ?, description = ?, project_folder = ?, "
                "default_page_size = ?, default_orientation = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    project.name,
                    project.description,
                    project.project_folder,
                    project.default_page_size,
                    project.default_orientation,
                    project.updated_at,
                    project.id,
                ),
            )

    def save(self, project: Project) -> None:
        """Insert or update depending on whether the row already exists."""
        if self.get(project.id) is None:
            self.insert(project)
        else:
            self.update(project)

    def get(self, project_id: str) -> Project | None:
        row = self._one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return Project.from_row(row) if row else None

    def first(self) -> Project | None:
        row = self._one("SELECT * FROM projects ORDER BY created_at LIMIT 1")
        return Project.from_row(row) if row else None

    def touch(self, project_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), project_id),
            )

    def delete(self, project_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
