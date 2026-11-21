"""Global application database: the registry of known projects.

The project list lives outside the individual project databases so the
dashboard can show recent projects without opening every project file.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.paths import APP_NAME, LEGACY_APP_NAMES, app_database_path
from database.connection import Database
from models.project import Project

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS known_projects (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    db_path        TEXT NOT NULL UNIQUE,
    project_folder TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    last_opened_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_known_projects_opened ON known_projects(last_opened_at);
CREATE TABLE IF NOT EXISTS recent_exports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    output_path  TEXT NOT NULL,
    page_count   INTEGER NOT NULL DEFAULT 0,
    exported_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recent_exports_time ON recent_exports(exported_at);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rebase_legacy_path(
    db_path: Path, documents_root: Path, new_root: Path
) -> Path | None:
    """Re-anchor *db_path* on *new_root* when it sits under a legacy folder.

    ``Documents\\Image to PDF Builder\\Projects\\Job\\project.db`` becomes
    ``Documents\\CardCompile\\Projects\\Job\\project.db``: only the application
    folder name changed, the part below it is preserved.  Returns ``None`` when
    the path does not belong to any known legacy folder.
    """
    for legacy_name in LEGACY_APP_NAMES:
        legacy_root = documents_root / legacy_name
        try:
            relative = db_path.relative_to(legacy_root)
        except ValueError:
            continue
        return new_root / relative
    return None


@dataclass(slots=True)
class KnownProject:
    id: str
    name: str
    db_path: str
    project_folder: str
    created_at: str
    last_opened_at: str

    @property
    def exists(self) -> bool:
        try:
            return Path(self.db_path).is_file()
        except OSError:
            return False

    @property
    def folder(self) -> Path:
        return Path(self.project_folder)

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> "KnownProject":
        data = dict(row)
        return cls(
            id=data["id"],
            name=data["name"],
            db_path=data["db_path"],
            project_folder=data["project_folder"],
            created_at=data["created_at"],
            last_opened_at=data["last_opened_at"],
        )


class AppDatabase:
    """Registry of projects plus the recently exported PDF files."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.db = Database(path or app_database_path())
        self.db.init_schema(APP_SCHEMA, schema_version=1)
        self.repair_legacy_paths()

    def repair_legacy_paths(self) -> int:
        """Rewrite registry rows that still point at a pre-rename folder.

        ``known_projects`` stores absolute paths.  When the application folder was
        renamed, those paths keep the old name and every project would show up as
        *missing* even though the files were migrated.  Each row is re-anchored on
        the new documents root by keeping the part of the path below the old
        application folder.  Returns the number of rows that were repaired.
        """
        documents_root = Path.home() / "Documents"
        new_root = documents_root / APP_NAME
        repaired = 0
        for project in self.list_projects():
            db_path = Path(project.db_path)
            if db_path.is_file():
                continue
            rebased = _rebase_legacy_path(db_path, documents_root, new_root)
            if rebased is None or not rebased.is_file():
                continue
            folder = rebased.parent
            with self.db.transaction() as connection:
                connection.execute(
                    "UPDATE known_projects SET db_path = ?, project_folder = ? "
                    "WHERE id = ?",
                    (str(rebased), str(folder), project.id),
                )
            repaired += 1
        return repaired

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ projects
    def register_project(self, project: Project) -> None:
        now = _now()
        db_path = str(project.db_path)
        with self.db.transaction() as connection:
            # The same file may already be registered under another id (project
            # folder replaced, database restored from a backup). ``db_path`` is
            # UNIQUE, so drop the stale row first instead of failing the open.
            connection.execute(
                "DELETE FROM known_projects WHERE db_path = ? AND id <> ?",
                (db_path, project.id),
            )
            connection.execute(
                "INSERT INTO known_projects (id, name, db_path, project_folder, "
                "created_at, last_opened_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "db_path = excluded.db_path, "
                "project_folder = excluded.project_folder, "
                "last_opened_at = excluded.last_opened_at",
                (
                    project.id,
                    project.name,
                    str(project.db_path),
                    str(project.folder),
                    project.created_at,
                    now,
                ),
            )

    def touch_project(self, project_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE known_projects SET last_opened_at = ? WHERE id = ?",
                (_now(), project_id),
            )

    def rename_project(self, project_id: str, name: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE known_projects SET name = ? WHERE id = ?", (name, project_id)
            )

    def forget_project(self, project_id: str) -> None:
        """Remove the project from the registry (files stay on disk)."""
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM known_projects WHERE id = ?", (project_id,))

    def list_projects(self, limit: int | None = None) -> list[KnownProject]:
        sql = "SELECT * FROM known_projects ORDER BY last_opened_at DESC, name"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        return [KnownProject.from_row(row) for row in self.db.query_all(sql, params)]

    def get_project(self, project_id: str) -> KnownProject | None:
        row = self.db.query_one(
            "SELECT * FROM known_projects WHERE id = ?", (project_id,)
        )
        return KnownProject.from_row(row) if row else None

    def find_by_db_path(self, db_path: str) -> KnownProject | None:
        row = self.db.query_one(
            "SELECT * FROM known_projects WHERE db_path = ?", (str(db_path),)
        )
        return KnownProject.from_row(row) if row else None

    def count_projects(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM known_projects", default=0))

    def last_project(self) -> KnownProject | None:
        row = self.db.query_one(
            "SELECT * FROM known_projects ORDER BY last_opened_at DESC LIMIT 1"
        )
        return KnownProject.from_row(row) if row else None

    def prune_missing(self) -> int:
        """Drop registry entries whose database file disappeared."""
        removed = 0
        for project in self.list_projects():
            if not project.exists:
                self.forget_project(project.id)
                removed += 1
        return removed

    # ------------------------------------------------------------------- exports
    def add_recent_export(
        self, project_name: str, output_path: str, page_count: int
    ) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO recent_exports (project_name, output_path, "
                "page_count, exported_at) VALUES (?, ?, ?, ?)",
                (project_name, str(output_path), int(page_count), _now()),
            )
            connection.execute(
                "DELETE FROM recent_exports WHERE id NOT IN ("
                "SELECT id FROM recent_exports ORDER BY exported_at DESC LIMIT 50)"
            )

    def recent_exports(self, limit: int = 10) -> list[dict]:
        rows = self.db.query_all(
            "SELECT project_name, output_path, page_count, exported_at "
            "FROM recent_exports ORDER BY exported_at DESC LIMIT ?",
            (int(limit),),
        )
        return [dict(row) for row in rows]
