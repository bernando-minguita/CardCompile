"""SQLite connection wrapper with current-schema initialization.

Every project owns an isolated database file.  The wrapper keeps SQL out of the
UI layer: services talk to repositories, repositories talk to :class:`Database`.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from core.logging_setup import get_logger

LOGGER = get_logger("database")
CURRENT_SCHEMA_VERSION = 8

#: Additive migrations for the *project* schema, keyed by the version that
#: introduced them.  ``init_schema`` applies every entry newer than the version
#: stored in the file, so an existing project is upgraded in place instead of
#: being rejected.  ``ALTER TABLE ADD COLUMN`` is additive and keeps every row,
#: which is what the project's safety rules require.
#:
#: Each entry is ``(table, column, declaration)``; the declaration must carry a
#: DEFAULT so existing rows get a usable value without a table rebuild.
#:
#: NOTE: this table describes the *project* schema only.  The application
#: registry (``AppDatabase``) has its own, unrelated version numbering and must
#: pass ``migrations=None`` - applying this floor to it would refuse a perfectly
#: valid registry (it sits at version 1).
PROJECT_MIGRATIONS: dict[int, tuple[tuple[str, str, str], ...]] = {
    7: (
        ("page_items", "quad_tl_x", "REAL NOT NULL DEFAULT 0.0"),
        ("page_items", "quad_tl_y", "REAL NOT NULL DEFAULT 0.0"),
        ("page_items", "quad_tr_x", "REAL NOT NULL DEFAULT 1.0"),
        ("page_items", "quad_tr_y", "REAL NOT NULL DEFAULT 0.0"),
        ("page_items", "quad_br_x", "REAL NOT NULL DEFAULT 1.0"),
        ("page_items", "quad_br_y", "REAL NOT NULL DEFAULT 1.0"),
        ("page_items", "quad_bl_x", "REAL NOT NULL DEFAULT 0.0"),
        ("page_items", "quad_bl_y", "REAL NOT NULL DEFAULT 1.0"),
        ("page_items", "adjust_brightness", "REAL NOT NULL DEFAULT 1.0"),
        ("page_items", "adjust_contrast", "REAL NOT NULL DEFAULT 1.0"),
        ("page_items", "adjust_saturation", "REAL NOT NULL DEFAULT 1.0"),
        ("page_items", "adjust_sharpness", "REAL NOT NULL DEFAULT 1.0"),
        ("page_items", "adjust_shadow", "INTEGER NOT NULL DEFAULT 0"),
    ),
    8: (
        ("export_history", "format", "TEXT NOT NULL DEFAULT 'PDF'"),
    ),
}

#: Oldest *project* schema this build can bring forward.  ``PROJECT_MIGRATIONS``
#: describes only the columns added after this version, so anything older cannot
#: be upgraded correctly (the intermediate changes are unknown) and is refused
#: instead of being silently modified.
MIN_PROJECT_SCHEMA_VERSION = 6


def _load_schema() -> str:
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    return schema_path.read_text(encoding="utf-8")


class DatabaseError(RuntimeError):
    """Raised when a database operation cannot be completed."""


class Database:
    """Thin, thread-safe wrapper around a single SQLite file."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._tx_depth = 0

    # ---------------------------------------------------------------- lifecycle
    @property
    def path(self) -> Path:
        return self._path

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._open()
        assert self._connection is not None
        return self._connection

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    def _open(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(
                    str(self._path),
                    timeout=20.0,
                    isolation_level=None,
                    check_same_thread=False,
                )
            except sqlite3.Error as exc:
                raise DatabaseError(
                    f"Cannot open database {self._path}: {exc}"
                ) from exc
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            # FULL: a commit is on the platter before it returns.  The write
            # volume of this application is small (a few rows per edit), and
            # NORMAL keeps recent commits only in the -wal sidecar, which is
            # lost when a user copies/backs up project.db on its own.
            connection.execute("PRAGMA synchronous = FULL")
            self._connection = connection
            LOGGER.debug("Opened database %s", self._path)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                finally:
                    self._connection = None
                    self._tx_depth = 0
                    LOGGER.debug("Closed database %s", self._path)

    # ------------------------------------------------------------ transactions
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements inside a single transaction (nested safe).

        Nested blocks use ``SAVEPOINT``: an exception raised inside an inner
        block rolls back only that block's work instead of being committed by the
        enclosing transaction.
        """
        connection = self.connection
        with self._lock:
            outermost = self._tx_depth == 0
            savepoint = f"dpb_sp_{self._tx_depth}"
            if outermost:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute(f"SAVEPOINT {savepoint}")
            self._tx_depth += 1
            try:
                yield connection
            except BaseException:
                self._tx_depth -= 1
                try:
                    if outermost:
                        connection.rollback()
                    else:
                        connection.execute(f"ROLLBACK TO {savepoint}")
                        connection.execute(f"RELEASE {savepoint}")
                except sqlite3.Error:  # pragma: no cover - connection already dead
                    LOGGER.debug("Rollback of savepoint %s failed", savepoint)
                raise
            else:
                self._tx_depth -= 1
                if outermost:
                    connection.commit()
                else:
                    connection.execute(f"RELEASE {savepoint}")

    # ---------------------------------------------------------------- queries
    def execute(
        self, sql: str, params: Sequence[Any] | dict[str, Any] = ()
    ) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, params)
        except sqlite3.Error as exc:
            LOGGER.error("SQL error: %s", exc)
            raise DatabaseError(str(exc)) from exc

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        try:
            self.connection.executemany(sql, rows)
        except sqlite3.Error as exc:
            LOGGER.error("SQL error: %s", exc)
            raise DatabaseError(str(exc)) from exc

    def query_all(
        self, sql: str, params: Sequence[Any] | dict[str, Any] = ()
    ) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def query_one(
        self, sql: str, params: Sequence[Any] | dict[str, Any] = ()
    ) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def scalar(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
        default: Any = None,
    ) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value


    def __enter__(self) -> "Database":
        self.connection
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ initialization
    def init_schema(
        self,
        schema_sql: str | None = None,
        schema_version: int = CURRENT_SCHEMA_VERSION,
        migrations: dict[int, tuple[tuple[str, str, str], ...]] | None = None,
        min_version: int = 0,
    ) -> None:
        """Create the database, or upgrade an older one in place.

        A file written by an older version of the application is migrated with
        additive ``ALTER TABLE ADD COLUMN`` statements instead of being rejected:
        refusing to open it would lock the user out of every existing project the
        moment a column is added.  A file from a *newer* version is still refused,
        because this build cannot know what those columns mean.

        ``migrations`` and ``min_version`` belong to the schema being opened and
        default to "no migrations, no floor" because not every database in the
        application shares the project's version numbering: the registry
        (``AppDatabase``) is at version 1 and must not be measured against the
        project's floor.  Applying one global floor to every database refused a
        perfectly valid registry at startup.
        """
        current_version = (
            self.schema_version() if self.table_exists("schema_version") else 0
        )
        if current_version > schema_version:
            raise DatabaseError(
                f"Database schema version {current_version} is newer than this "
                f"application supports ({schema_version}); open the project with "
                "the newer version of the application."
            )
        if current_version and current_version < min_version:
            # Only the columns added after ``min_version`` are described in
            # ``migrations``; an older file is missing changes this build does
            # not know about, so upgrading it would produce a half-migrated
            # database.  Refuse it untouched instead.
            raise DatabaseError(
                f"Database schema version {current_version} is not supported; "
                f"this application can upgrade version {min_version} and later "
                f"(expected {schema_version})."
            )
        if schema_sql is None:
            schema_sql = _load_schema()

        with self.transaction() as connection:
            statement_lines: list[str] = []
            for line in schema_sql.splitlines():
                statement_lines.append(line)
                statement = "\n".join(statement_lines)
                if sqlite3.complete_statement(statement):
                    if statement.strip():
                        connection.execute(statement)
                    statement_lines.clear()
            remaining = "\n".join(statement_lines).strip()
            if remaining:
                connection.execute(remaining)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
            )
            if not current_version:
                connection.execute(
                    "INSERT INTO schema_version (version, applied_at) "
                    "VALUES (?, datetime('now'))",
                    (schema_version,),
                )
            elif current_version < schema_version:
                self._apply_additive_columns(
                    connection, current_version, schema_version, migrations or {}
                )
                connection.execute(
                    "INSERT INTO schema_version (version, applied_at) "
                    "VALUES (?, datetime('now'))",
                    (schema_version,),
                )
                LOGGER.info(
                    "Upgraded database %s from schema %s to %s",
                    self._path,
                    current_version,
                    schema_version,
                )

    @staticmethod
    def _apply_additive_columns(
        connection: sqlite3.Connection,
        from_version: int,
        to_version: int,
        migrations: dict[int, tuple[tuple[str, str, str], ...]],
    ) -> None:
        """Add every column introduced after *from_version* (idempotent).

        ``CREATE TABLE IF NOT EXISTS`` in the schema already gives a *fresh*
        database every column; this only runs for an existing file.  Each
        ``ALTER TABLE`` is guarded by a ``PRAGMA table_info`` check so a partially
        migrated file (an interrupted upgrade) can be finished safely.
        """
        for version in sorted(migrations):
            if version <= from_version or version > to_version:
                continue
            for table, column, declaration in migrations[version]:
                existing = {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if not existing:  # pragma: no cover - table missing
                    continue
                if column in existing:
                    continue
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
                )

    def schema_version(self) -> int:
        try:
            value = self.scalar("SELECT MAX(version) FROM schema_version", default=0)
        except DatabaseError:
            return 0
        return int(value or 0)

    def table_exists(self, name: str) -> bool:
        row = self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return row is not None

    def vacuum(self) -> None:
        """Compact the database file (best effort, no callers today).

        The connection stays open: closing it here used to leave every caller
        holding a dead handle for no reason - ``VACUUM`` works on a live one.
        """
        try:
            self.connection.execute("VACUUM")
        except sqlite3.Error as exc:  # pragma: no cover - best effort
            LOGGER.warning("VACUUM failed: %s", exc)
