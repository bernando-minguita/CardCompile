"""Project lifecycle: create, open, rename, close and inspect projects.

Every project is an isolated folder containing ``project.db``.  Creating a
project never overwrites an existing folder, and opening one verifies the
database before the workspace is handed to the UI.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.logging_setup import get_logger
from core.paper import page_dimensions
from core.paths import (
    PROJECT_DB_FILENAME,
    documents_root,
    is_inside_app_folder,
    projects_dir,
)
from core.settings_service import SettingsService
from database.app_database import AppDatabase, KnownProject
from database.project_database import ProjectDatabase
from models.pdf_page import PdfPage
from models.project import Project

LOGGER = get_logger("projects")

_INVALID_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


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
    import sqlite3

    if not path.is_file():
        return PROBLEM_MISSING
    try:
        # ``as_uri()`` percent-encodes the path.  Formatting the raw path into a
        # URI broke every folder containing ``#`` (SQLite treats it as the start
        # of a fragment and opened a different, empty file), so a perfectly valid
        # project was reported as "empty" and could not be reopened.
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
    """Read-only check that *path* really is a project database.

    ``ProjectDatabase`` initializes the current schema on construction, so a
    foreign SQLite file must be rejected *before* it is opened for writing.
    """
    return project_file_problem(path) == ""


def empty_project_message(path: Path | str) -> str:
    """Explain a project database that opens but contains no tables."""
    return (
        f"The project database is empty:\n{path}\n\n"
        "SQLite keeps very recent changes in a sidecar file next to the "
        "database (project.db-wal) until they are flushed. Copying or backing up "
        "project.db *alone* leaves that file behind, and everything that was "
        "still in it is gone.\n\n"
        "To restore the project:\n"
        f"  - copy 'project.db', 'project.db-wal' and 'project.db-shm' together, or\n"
        f"  - restore the backup of the whole folder "
        f"'{path.parent.name}'.\n\n"
        "The original scans are never touched by this application."
    )


class ProjectServiceError(RuntimeError):
    """User facing project error (invalid name, missing file, ...)."""


def missing_project_message(path: Path | str) -> str:
    """Explain a missing ``project.db`` and what the user can do about it."""
    return (
        f"The project file was not found:\n{path}\n\n"
        "The folder was moved, renamed or deleted (an update or rebuild of the "
        "application only removes files inside the application folder).\n\n"
        "Use 'Locate project file...' to point at the project.db of the moved "
        "project, or remove it from the list."
    )


def sanitize_folder_name(name: str) -> str:
    """Turn a project name into a safe Windows folder name."""
    cleaned = _INVALID_FOLDER_CHARS.sub("_", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "Project"
    if cleaned.lower() in _RESERVED_NAMES:
        cleaned = f"{cleaned}_project"
    return cleaned[:80]


@dataclass(slots=True)
class ProjectSummary:
    """Lightweight statistics shown on the dashboard."""

    db_path: str
    name: str
    description: str
    project_folder: str
    document_count: int
    page_count: int
    updated_at: str
    valid: bool = True
    #: Why the project cannot be used ("" while everything is fine).
    problem: str = ""


def build_page(
    project: Project,
    page_size: str,
    orientation: str,
    margin_mm: float = 10.0,
    custom_width_mm: float | None = None,
    custom_height_mm: float | None = None,
    page_number: int = 1,
) -> PdfPage:
    """Create a :class:`PdfPage` with the requested paper size and margins."""
    width, height = page_dimensions(
        page_size, orientation, custom_width_mm, custom_height_mm
    )
    page = PdfPage(
        id=str(uuid.uuid4()),
        project_id=project.id,
        page_number=page_number,
        paper_size=page_size,
        orientation=orientation,
        width_mm=width,
        height_mm=height,
        margin_top=margin_mm,
        margin_bottom=margin_mm,
        margin_left=margin_mm,
        margin_right=margin_mm,
    )
    page.clamp_margins()
    return page


class ProjectWorkspace:
    """An open project together with its database and repositories.

    An *untitled* workspace is a real project stored in a throwaway folder so
    the user can edit, import and arrange documents before deciding on a name.
    It is never registered in the application database and is deleted when it
    is closed without being saved.  ``save_project_as`` promotes it to a normal
    project the moment the user picks a real name and folder.
    """

    def __init__(
        self, project: Project, database: ProjectDatabase, untitled: bool = False
    ) -> None:
        self.project = project
        self.database = database
        self.untitled = bool(untitled)

    @property
    def id(self) -> str:
        return self.project.id

    @property
    def name(self) -> str:
        return self.project.name

    @property
    def db_path(self) -> Path:
        return self.database.path

    def close(self) -> None:
        # ``checkpoint()`` would re-open a closed database (the ``connection``
        # property opens lazily), so it is skipped once the database is already
        # closed.  This lets ``close()`` be called more than once safely, which
        # happens when an untitled workspace is discarded after being closed.
        try:
            if self.database.db.is_open:
                self.database.checkpoint()
        finally:
            self.database.close()

    def touch(self) -> None:
        self.project.touch()
        self.database.projects.update(self.project)


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
    for name in ("documents", "thumbnails"):
        shutil.rmtree(folder / name, ignore_errors=True)
    for name in (PROJECT_DB_FILENAME, f"{PROJECT_DB_FILENAME}-wal", f"{PROJECT_DB_FILENAME}-shm"):
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - locked file
            LOGGER.warning("Could not remove %s", folder / name)


class ProjectService:
    """Creates, opens and manages project files."""

    def __init__(self, app_db: AppDatabase, settings: SettingsService) -> None:
        self._app_db = app_db
        self._settings = settings

    # ------------------------------------------------------------------ queries
    @property
    def app_db(self) -> AppDatabase:
        return self._app_db

    def known_projects(self, limit: int | None = None) -> list[KnownProject]:
        return self._app_db.list_projects(limit)

    def recent_projects(self) -> list[KnownProject]:
        limit = int(self._settings.get("general", "recent_project_count", 10))
        if limit <= 0:
            return []  # 0 = keep the recent list hidden
        return self._app_db.list_projects(limit)

    def last_project(self) -> KnownProject | None:
        return self._app_db.last_project()

    def count_projects(self) -> int:
        return self._app_db.count_projects()

    # ------------------------------------------------------------------- create
    def configured_project_folder(self) -> Path | None:
        """The project folder from the settings, without any safety net."""
        folder = self._settings.get("general", "default_project_directory")
        return Path(str(folder)) if folder else None

    def unsafe_project_folder(self) -> Path | None:
        """Return the configured folder when it sits inside the app folder.

        A rebuild/reinstall deletes the application folder, so storing projects
        there loses them.  The user can accept the risk once
        (``general.allow_unsafe_project_folder``), after which this returns
        ``None`` and no warning is shown again.
        """
        if bool(self._settings.get("general", "allow_unsafe_project_folder", False)):
            return None
        configured = self.configured_project_folder()
        if configured is not None and is_inside_app_folder(configured):
            return configured
        return None

    def default_project_folder(self) -> Path:
        configured = self.configured_project_folder()
        if configured is not None and is_inside_app_folder(configured):
            if not bool(
                self._settings.get("general", "allow_unsafe_project_folder", False)
            ):
                LOGGER.warning(
                    "Ignoring project folder %s - it is inside the application "
                    "folder and would be deleted by an update. Using %s instead.",
                    configured,
                    documents_root() / "Projects",
                )
                configured = None
        base = configured if configured is not None else projects_dir()
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            base = projects_dir()
        return base

    def unique_project_folder(self, name: str, base: Path | None = None) -> Path:
        root = Path(base) if base else self.default_project_folder()
        root.mkdir(parents=True, exist_ok=True)
        stem = sanitize_folder_name(name)
        candidate = root / stem
        counter = 2
        while candidate.is_dir() and any(candidate.iterdir()):
            candidate = root / f"{stem} ({counter})"
            counter += 1
        return candidate

    def create_project(
        self,
        name: str,
        description: str = "",
        folder: str | Path | None = None,
        page_size: str = "A4",
        orientation: str = "portrait",
        custom_width_mm: float | None = None,
        custom_height_mm: float | None = None,
        margin_mm: float = 10.0,
    ) -> ProjectWorkspace:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ProjectServiceError("A project name is required.")

        created_here = False
        if folder:
            target = Path(folder)
            if target.suffix.lower() == ".db":
                target = target.parent
            # A rebuild/update replaces the application folder, so a project
            # stored inside it would be deleted.  The Settings dialog offers the
            # same warning; per-project picks must not bypass it.
            if is_inside_app_folder(target) and not bool(
                self._settings.get("general", "allow_unsafe_project_folder", False)
            ):
                raise ProjectServiceError(
                    f"The folder '{target}' is inside the application folder.\n\n"
                    "An update or rebuild of CardCompile replaces that "
                    "folder, which would delete the project.\n\n"
                    f"Use the default project folder instead:\n"
                    f"{self.default_project_folder()}"
                )
            if target.exists() and not target.is_dir():
                raise ProjectServiceError(
                    f"'{target}' is not a folder. Choose an empty folder "
                    "to store the project in."
                )
            if target.is_dir() and any(target.iterdir()):
                raise ProjectServiceError(
                    f"The folder '{target}' is not empty. Choose an empty folder "
                    "or a new one to avoid overwriting existing files."
                )
            target.mkdir(parents=True, exist_ok=True)
        else:
            created_here = True
            target = self.unique_project_folder(clean_name)

        now = datetime.now().isoformat(timespec="seconds")
        project = Project(
            id=str(uuid.uuid4()),
            name=clean_name,
            description=(description or "").strip(),
            project_folder=str(target),
            default_page_size=page_size,
            default_orientation=orientation,
            created_at=now,
            updated_at=now,
        )
        project.ensure_folders()

        workspace = ProjectWorkspace(project, ProjectDatabase(project.db_path))
        try:
            workspace.database.projects.insert(project)
            workspace.database.categories.ensure_defaults(project.id)
            first_page = build_page(
                project,
                page_size,
                orientation,
                margin_mm,
                custom_width_mm,
                custom_height_mm,
            )
            workspace.database.pages.insert(first_page)
            self._app_db.register_project(project)
        except Exception:
            workspace.close()
            # Leaving a half-built project behind made the folder look valid and
            # the next attempt silently became "Name (2)".
            _remove_project_artifacts(target)
            if created_here:
                try:
                    target.rmdir()
                except OSError:
                    shutil.rmtree(target, ignore_errors=True)
            LOGGER.exception("Project creation failed for %s", target)
            raise
        LOGGER.info("Created project '%s' at %s", project.name, target)
        return workspace

    # ------------------------------------------------------------------ untitled
    def _untitled_root(self) -> Path:
        """Folder that holds the throwaway untitled projects."""
        return self.default_project_folder() / ".untitled"

    def create_untitled_workspace(self) -> ProjectWorkspace:
        """Open a scratch project so the user can edit without saving first.

        The workspace is a *real* project (database, documents and thumbnails
        folders) stored in a throwaway ``.untitled`` folder, but it is never
        registered in the application database.  ``save_project_as`` (or Save,
        which asks for a name) promotes it to a normal project; closing it
        without saving deletes the folder.
        """
        root = self._untitled_root()
        target = root / str(uuid.uuid4())
        now = datetime.now().isoformat(timespec="seconds")
        project = Project(
            id=str(uuid.uuid4()),
            name="Untitled",
            project_folder=str(target),
            created_at=now,
            updated_at=now,
        )
        project.ensure_folders()
        workspace = ProjectWorkspace(project, ProjectDatabase(project.db_path), untitled=True)
        try:
            workspace.database.projects.insert(project)
            workspace.database.categories.ensure_defaults(project.id)
            first_page = build_page(project, "A4", "portrait")
            workspace.database.pages.insert(first_page)
        except Exception:
            workspace.close()
            shutil.rmtree(target, ignore_errors=True)
            LOGGER.exception("Untitled workspace could not be created")
            raise
        LOGGER.info("Started an untitled workspace in %s", target)
        return workspace

    def discard_untitled_workspace(self, workspace: ProjectWorkspace) -> None:
        """Close an unsaved untitled workspace and delete its throwaway folder."""
        if not workspace.untitled:
            return
        folder = workspace.project.folder
        try:
            workspace.close()
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Error while closing the untitled workspace")
        shutil.rmtree(folder, ignore_errors=True)
        LOGGER.info("Discarded the untitled workspace in %s", folder)

    # --------------------------------------------------------------------- open
    def open_project(self, db_path: str | Path) -> ProjectWorkspace:
        path = Path(db_path)
        if path.is_dir():
            path = path / PROJECT_DB_FILENAME
        problem = project_file_problem(path)
        if problem == PROBLEM_MISSING:
            raise ProjectServiceError(missing_project_message(path))
        if problem == PROBLEM_EMPTY:
            raise ProjectServiceError(empty_project_message(path))
        if problem:
            raise ProjectServiceError(
                f"This file is not a CardCompile project:\n{path}"
            )
        database = ProjectDatabase(path)
        try:
            project = database.projects.first()
            if project is None:
                raise ProjectServiceError(
                    "This file does not contain a CardCompile project."
                )
            database.categories.ensure_defaults(project.id)
            self._app_db.register_project(project)
        except Exception:
            database.close()
            raise
        LOGGER.info("Opened project '%s'", project.name)
        return ProjectWorkspace(project, database)

    def open_recent(self, project_id: str) -> ProjectWorkspace:
        known = self._app_db.get_project(project_id)
        if known is None:
            raise ProjectServiceError("This project is no longer in the recent list.")
        return self.open_project(known.db_path)

    def known_project(self, project_id: str):
        """Registry entry for *project_id* (``None`` when unknown)."""
        return self._app_db.get_project(project_id)

    def relink_project(
        self, project_id: str, db_path: str | Path
    ) -> ProjectWorkspace:
        """Point a registry entry at a ``project.db`` that was moved.

        The database stores its own folder, so it is re-keyed to the new location
        before the entry is registered again.
        """
        path = Path(db_path)
        if path.is_dir():
            path = path / PROJECT_DB_FILENAME
        problem = project_file_problem(path)
        if problem == PROBLEM_MISSING:
            raise ProjectServiceError(missing_project_message(path))
        if problem == PROBLEM_EMPTY:
            raise ProjectServiceError(empty_project_message(path))
        if problem:
            raise ProjectServiceError(
                f"This file is not a CardCompile project:\n{path}"
            )
        database = ProjectDatabase(path)
        try:
            project = database.projects.first()
            if project is None:
                raise ProjectServiceError(
                    "This file does not contain a CardCompile project."
                )
            old_folder = project.folder
            new_folder = path.parent
            if project.project_folder != str(new_folder):
                # Folder update and thumbnail rebase must commit together: if
                # the rebase ran in its own transaction and failed after the
                # folder was stored, a retry would see the new folder already
                # saved and skip the rebase for good - leaving every preview
                # pointing into the old folder.  ``update()`` opens a nested
                # savepoint inside this transaction, so everything is atomic.
                with database.db.transaction() as connection:
                    project.project_folder = str(new_folder)
                    project.touch()
                    database.projects.update(project)
                    # Thumbnails are stored as absolute paths: after a move they
                    # must follow the project, otherwise every preview points at
                    # a file that no longer exists.
                    if old_folder != new_folder:
                        _rebase_thumbnail_paths(connection, old_folder, new_folder)
        finally:
            database.close()
        existing = self._app_db.find_by_db_path(str(path))
        if existing is not None and existing.id != project.id:
            # The same file is already registered under another id.
            self._app_db.forget_project(existing.id)
        self._app_db.forget_project(project_id)
        self._app_db.register_project(project)
        LOGGER.info("Relinked project '%s' to %s", project.name, path)
        return self.open_project(path)

    # ------------------------------------------------------------------- manage
    def rename_project(self, workspace: ProjectWorkspace, new_name: str) -> None:
        clean = (new_name or "").strip()
        if not clean:
            raise ProjectServiceError("The project name cannot be empty.")
        workspace.project.name = clean
        workspace.touch()
        self._app_db.rename_project(workspace.id, clean)
        LOGGER.info("Renamed project to '%s'", clean)

    def update_project_defaults(
        self,
        workspace: ProjectWorkspace,
        page_size: str,
        orientation: str,
        description: str | None = None,
    ) -> None:
        workspace.project.default_page_size = page_size
        workspace.project.default_orientation = orientation
        if description is not None:
            workspace.project.description = description
        workspace.touch()

    def forget_project(self, project_id: str) -> None:
        """Remove a project from the recent list without touching the files."""
        self._app_db.forget_project(project_id)

    def rename_known_project(self, project_id: str, new_name: str) -> None:
        clean = (new_name or "").strip()
        if not clean:
            raise ProjectServiceError("The project name cannot be empty.")
        known = self._app_db.get_project(project_id)
        if known is None:
            raise ProjectServiceError("This project is not in the registry.")
        database = ProjectDatabase(known.db_path)
        try:
            project = database.projects.first()
            if project is not None:
                project.name = clean
                project.touch()
                database.projects.update(project)
        finally:
            database.close()
        self._app_db.rename_project(project_id, clean)
        LOGGER.info("Renamed stored project %s to '%s'", project_id[:8], clean)

    def delete_project_files(self, workspace: ProjectWorkspace) -> None:
        """Delete the project folder after explicit user confirmation.

        Only the artefacts this application created are removed (database,
        ``documents/``, ``thumbnails/``).  Anything else the user keeps in that
        folder - including sibling projects - is left alone; the folder itself is
        removed only when it ends up empty.
        """
        folder = workspace.project.folder
        if not (folder / PROJECT_DB_FILENAME).is_file():
            raise ProjectServiceError(
                f"This folder does not contain a project any more:\n{folder}\n\n"
                "Nothing was deleted."
            )
        try:
            resolved_folder = folder.resolve()
        except OSError:  # pragma: no cover - unreadable path
            resolved_folder = folder
        nested = [
            known
            for known in self._app_db.list_projects()
            if known.id != workspace.id
            and resolved_folder in Path(known.db_path).resolve().parents
        ]
        if nested:
            names = ", ".join(known.name for known in nested)
            raise ProjectServiceError(
                f"Other projects are stored inside this folder ({names}).\n\n"
                "Move them out first - deleting this folder would destroy them."
            )
        workspace.close()
        self._app_db.forget_project(workspace.id)
        _remove_project_artifacts(folder)
        try:
            folder.rmdir()
        except OSError:
            LOGGER.info(
                "Project data deleted; %s kept because it still contains other files",
                folder,
            )
        else:
            LOGGER.warning("Deleted project folder %s", folder)

    @staticmethod
    def _partial_folder(target: Path) -> Path:
        """Staging folder beside *target*; a drive root cannot provide one."""
        try:
            return target.with_name(f"{target.name}.partial")
        except ValueError as exc:  # drive or share root: no name to prefix
            raise ProjectServiceError(
                f"'{target}' cannot hold a copy of the project.\n\n"
                "Choose a normal folder inside it."
            ) from exc

    def duplicate_project(
        self, workspace: ProjectWorkspace, new_name: str, folder: str | Path | None = None
    ) -> ProjectWorkspace:
        """Save the project under a new name (``Save as``).

        The whole project folder is copied and re-keyed to a new project id so
        both projects stay completely independent.
        """
        clean = (new_name or "").strip()
        if not clean:
            raise ProjectServiceError("The project name cannot be empty.")
        source_folder = workspace.project.folder
        workspace.database.checkpoint()
        if folder:
            target = Path(folder)
            if target.exists() and not target.is_dir():
                raise ProjectServiceError(f"'{target}' is not a folder.")
            if target.anchor and target == Path(target.anchor):
                # A drive root ("D:\", "\\server\share\") has no name, so it can
                # never hold the "<name>.partial" staging folder used below.
                raise ProjectServiceError(
                    f"'{target}' is a drive or share root.\n\n"
                    "Choose a folder inside it instead - the copy is built in a "
                    "temporary sub-folder beside the final one."
                )
            if source_folder in target.parents or target == source_folder:
                raise ProjectServiceError(
                    "The copy cannot be placed inside the original project folder."
                )
            if target.is_dir() and any(target.iterdir()):
                raise ProjectServiceError(
                    f"The folder '{target}' is not empty."
                )
        else:
            target = self.unique_project_folder(clean)
        # ------------------------------------------------------------------ PROMOTION (Untitled)
        if workspace.untitled:
            try:
                shutil.move(source_folder, target)
            except Exception as exc:
                raise ProjectServiceError(f"Could not move untitled project: {exc}") from exc

            db_path = target / PROJECT_DB_FILENAME
            database = ProjectDatabase(db_path)
            try:
                new_id = str(uuid.uuid4())
                now = datetime.now().isoformat(timespec="seconds")
                with database.db.transaction() as connection:
                    connection.execute("PRAGMA defer_foreign_keys = ON")
                    connection.execute(
                        "UPDATE projects SET id = ?, name = ?, project_folder = ?, "
                        "updated_at = ?",
                        (new_id, clean, str(target), now),
                    )
                    for table in ("categories", "documents", "pdf_pages", "export_history"):
                        connection.execute(
                            f"UPDATE {table} SET project_id = ?", (new_id,)
                        )
                    connection.execute("DELETE FROM export_history")
                    _rebase_thumbnail_paths(connection, source_folder, target)
                project = database.projects.first()
                database.checkpoint()
            except Exception:
                raise
            finally:
                database.close()

            self._app_db.register_project(project)
            LOGGER.info("Promoted untitled project to '%s' in %s", clean, target)
            return self.open_project(project.db_path)

        # ------------------------------------------------------------------ COPY (Normal)
        # Build the copy beside its final name and rename it only when it is
        # complete: an interrupted Save-as then leaves no half-initialised folder
        # (and no second folder sharing the original project id).
        partial = self._partial_folder(target)
        shutil.rmtree(partial, ignore_errors=True)
        try:
            shutil.copytree(source_folder, partial)
        except OSError as exc:
            shutil.rmtree(partial, ignore_errors=True)
            raise ProjectServiceError(f"The project could not be copied: {exc}") from exc

        new_id = str(uuid.uuid4())
        now = datetime.now().isoformat(timespec="seconds")
        try:
            database = ProjectDatabase(partial / PROJECT_DB_FILENAME)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        # Any failure from here on (re-key, checkpoint, rename) must remove the
        # staging folder: a leftover "<name>.partial" held a full copy of the
        # project still carrying the *original* project id, which looked like a
        # half-saved project on disk and could be picked up by mistake.
        try:
            try:
                with database.db.transaction() as connection:
                    # Foreign keys are checked at commit time so the project id can
                    # be re-keyed together with every child row.
                    connection.execute("PRAGMA defer_foreign_keys = ON")
                    connection.execute(
                        "UPDATE projects SET id = ?, name = ?, project_folder = ?, "
                        "updated_at = ?",
                        (new_id, clean, str(target), now),
                    )
                    for table in ("categories", "documents", "pdf_pages", "export_history"):
                        connection.execute(
                            f"UPDATE {table} SET project_id = ?", (new_id,)
                        )
                    connection.execute("DELETE FROM export_history")
                    _rebase_thumbnail_paths(connection, source_folder, target)
                project = database.projects.first()
                database.checkpoint()
            finally:
                database.close()
            if project is None:
                raise ProjectServiceError("The copied project could not be opened.")
            if target.exists():
                target.rmdir()  # the empty folder the user picked
            partial.rename(target)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        self._app_db.register_project(project)
        LOGGER.info("Saved project as '%s' in %s", clean, target)
        return self.open_project(project.db_path)


    # -------------------------------------------------------------------- stats
    def project_summary(self, db_path: str | Path) -> ProjectSummary:
        path = Path(db_path)
        problem = project_file_problem(path)
        summary = ProjectSummary(
            db_path=str(path),
            name=path.parent.name,
            description="",
            project_folder=str(path.parent),
            document_count=0,
            page_count=0,
            updated_at="",
            valid=False,
            problem=problem or PROBLEM_UNREADABLE,
        )
        if problem:
            if problem != PROBLEM_MISSING:
                LOGGER.warning(
                    "Project database unusable (%s), skipping: %s", problem, path
                )
            return summary
        try:
            database = ProjectDatabase(path)
        except Exception:
            LOGGER.exception("Could not open project %s", path)
            return summary
        try:
            project = database.projects.first()
            if project is None:
                return summary
            summary.name = project.name
            summary.description = project.description
            summary.project_folder = project.project_folder
            summary.updated_at = project.updated_at
            summary.document_count = database.documents.count(project.id)
            summary.page_count = database.pages.count(project.id)
            summary.valid = True
        except Exception:  # pragma: no cover - corrupt project
            LOGGER.exception("Could not read project summary for %s", path)
        finally:
            database.close()
        return summary
