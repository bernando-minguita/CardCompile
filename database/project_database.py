"""Aggregate that groups every repository of one project database."""

from __future__ import annotations

from pathlib import Path

from database.connection import (
    MIN_PROJECT_SCHEMA_VERSION,
    PROJECT_MIGRATIONS,
    Database,
)
from database.repositories import (
    CategoryRepository,
    DocumentRepository,
    ExportRepository,
    PageItemRepository,
    PageLabelRepository,
    PageRepository,
    ProjectRepository,
)


class ProjectDatabase:
    """Opens a project database file and exposes its repositories."""

    def __init__(self, path: Path | str) -> None:
        self.db = Database(path)
        try:
            # The project schema owns the additive migrations and the version
            # floor; the registry database (AppDatabase) must not inherit them.
            self.db.init_schema(
                migrations=PROJECT_MIGRATIONS,
                min_version=MIN_PROJECT_SCHEMA_VERSION,
            )
        except BaseException:
            # Never leave a half-initialised file locked for the rest of the
            # session: that blocked repairing or deleting the project.
            self.db.close()
            raise
        self.projects = ProjectRepository(self.db)
        self.categories = CategoryRepository(self.db)
        self.documents = DocumentRepository(self.db)
        self.pages = PageRepository(self.db)
        self.items = PageItemRepository(self.db)
        self.labels = PageLabelRepository(self.db)
        self.exports = ExportRepository(self.db)

    @property
    def path(self) -> Path:
        return self.db.path

    def close(self) -> None:
        self.db.close()

    def checkpoint(self) -> None:
        """Flush the WAL so the file on disk is complete after a crash.

        ``synchronous = FULL`` (the default) already guarantees that a commit
        is durable, so this is about keeping ``project.db`` *self-contained*:
        the sidecar files must be empty before the user copies or backs up the
        database on its own.

        ``TRUNCATE`` needs exclusive access: when another connection is still
        reading (a second window, a validation pass) it would block for the full
        busy timeout, so the wait is bounded and the checkpoint degrades to
        ``PASSIVE`` instead of stalling the UI.
        """
        try:
            connection = self.db.connection
        except Exception:  # pragma: no cover - already closed
            return
        try:
            connection.execute("PRAGMA busy_timeout = 2500")
            try:
                row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if row is not None and int(row[0]) != 0:
                    connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                connection.execute("PRAGMA busy_timeout = 20000")
        except Exception:  # pragma: no cover - best effort
            pass
