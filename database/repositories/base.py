"""Repository base class - all SQL lives in the ``database.repositories`` package."""

from __future__ import annotations

from database.connection import Database


class BaseRepository:
    """Common helpers shared by every repository."""

    def __init__(self, database: Database) -> None:
        self.db = database

    # Convenience passthroughs so subclasses stay short and readable.
    def _all(self, sql: str, params=()):
        return self.db.query_all(sql, params)

    def _one(self, sql: str, params=()):
        return self.db.query_one(sql, params)

    def _run(self, sql: str, params=()):
        return self.db.execute(sql, params)

    def _scalar(self, sql: str, params=(), default=None):
        return self.db.scalar(sql, params, default)
