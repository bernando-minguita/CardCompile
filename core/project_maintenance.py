"""Project maintenance helpers: file validation, WAL checkpointing, cleanup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.logging_setup import get_logger

LOGGER = get_logger("projects")

PROBLEM_MISSING = "missing"
PROBLEM_EMPTY = "empty"
PROBLEM_FOREIGN = "foreign"
PROBLEM_UNREADABLE = "unreadable"


def project_file_problem(path: Path) -> str:
    """Explain why *path* cannot be used as a project, or ``""`` when it can.

    Distinguishes the cases the user can act on:

    * ``missing``  - there is no file at all (moved/renamed/deleted folder).
    * ``empty``    - a readable SQLite file **without any tables**.  This is
      what a ``project.db`` looks like when it was copied without its ``-wal``
      sidecar after a crash: the committed rows lived in that file.
    * ``foreign``  - a SQLite database that is not ours.
    * ``unreadable`` - the file exists but cannot be opened/queried.
    """
    if not path.is_file():
        return PROBLEM_MISSING
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0)
    except (sqlite3.Error, ValueError):
        return PROBLEM_UNREADABLE
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except sqlite3.Error:
        return PROBLEM_UNREADABLE
    finally:
        connection.close()
    names = {row[0] for row in rows}
    if "projects" in names:
        return ""
    if not names:
        return PROBLEM_EMPTY
    return PROBLEM_FOREIGN


def looks_like_project(path: Path) -> bool:
    """Read-only check that *path* really is a project database."""
    return project_file_problem(path) == ""


def empty_project_message(path: Path | str) -> str:
    """Explain a project database that opens but contains no tables."""
    return (
        f"The project database is empty:\\n{path}\\n\\n"
        "SQLite keeps very recent changes in a sidecar file next to the "
        "database (project.db-wal) until they are flushed. Copying or backing up "
        "project.db *alone* leaves that file behind, and everything that was "
        "still in it is gone.\\n\\n"
        "To restore the project:\\n"
        f"  - copy 'project.db', 'project.db-wal' and 'project.db-shm' together, or\\n"
        f"  - restore the backup of the whole folder "
        f"'{Path(path).parent.name}'.\\n\\n"
        "The original scans are never touched by this application."
    )


def missing_project_message(path: Path | str) -> str:
    """Explain a missing ``project.db`` and what the user can do about it."""
    return (
        f"The project file was not found:\\n{path}\\n\\n"
        "The folder was moved, renamed or deleted (an update or rebuild of the "
        "application only removes files inside the application folder).\\n\\n"
        "Use 'Locate project file...' to point at the project.db of the moved "
        "project, or remove it from the list."
    )


def _rebase_thumbnail_paths(
    connection: sqlite3.Connection, old_folder: Path, new_folder: Path
) -> int:
    """Re-point ``documents.thumbnail_path`` from *old_folder* into *new_folder*.

    Thumbnails are stored as absolute paths.  Copying or moving a project folder
    (``Save as`` / ``Locate project file``) must rewrite them, otherwise the new
    project silently keeps reading files out of the old folder and loses every
    preview once that folder is deleted.  ``source_path`` is deliberately left
    untouched: originals are referenced in place and are never duplicated.
    """
    old_text = str(old_folder)
    updated = 0
    rows = connection.execute("SELECT id, thumbnail_path FROM documents").fetchall()
    for row in rows:
        current = row["thumbnail_path"] or ""
        if not current or not current.lower().startswith(old_text.lower()):
            continue
        try:
            relative = Path(current).relative_to(old_folder)
        except (ValueError, OSError):
            continue
        connection.execute(
            "UPDATE documents SET thumbnail_path = ? WHERE id = ?",
            (str(Path(new_folder) / relative), row["id"]),
        )
        updated += 1
    return updated


def _remove_project_artifacts(folder: Path) -> None:
    """Delete the files this application owns inside a project folder."""
    import shutil

    for name in ("documents", "thumbnails"):
        shutil.rmtree(folder / name, ignore_errors=True)
    for name in (PROJECT_DB_FILENAME, f"{PROJECT_DB_FILENAME}-wal", f"{PROJECT_DB_FILENAME}-shm"):
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - locked file
            LOGGER.warning("Could not remove %s", folder / name)