"""Filesystem locations and application level constants.

All application directories are created on demand so the program also works
from a freshly extracted folder-based PyInstaller build.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "CardCompile"
APP_SLUG = "CardCompile"
APP_VERSION = "1.6.0"
ORG_NAME = "CardCompile"

#: Names this application used before it was renamed.  The folders below are
#: migrated once on startup so a rename never orphans an existing project.
LEGACY_APP_NAMES = ("Image to PDF Builder",)
LEGACY_APP_SLUGS = ("ImageToPDFBuilder",)

#: Creator credits, shown in Help > About only (no page footer).
APP_AUTHOR = "Bernando Jr Minguita"
APP_AUTHOR_LINKEDIN = "https://www.linkedin.com/in/bernando-jr-minguita"

#: Name of the SQLite file stored inside every project folder.
PROJECT_DB_FILENAME = "project.db"

#: 1 millimetre expressed in PostScript points (1 pt = 1/72 inch).
MM_TO_PT = 72.0 / 25.4
PT_TO_MM = 25.4 / 72.0


def is_frozen() -> bool:
    """True when running from a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Directory that contains the application (``main.py`` or the .exe)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resources_dir() -> Path:
    """Directory holding styles, icons and templates."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", app_root()))
        bundled = base / "resources"
        if bundled.is_dir():
            return bundled
    return app_root() / "resources"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def documents_root() -> Path:
    """Per-user folder for projects and exports.

    Kept outside the application folder on purpose: reinstalling, updating or
    rebuilding the app wipes ``app_root()`` (a PyInstaller build clears
    ``dist\\CardCompile``), which must never delete user projects.
    """
    override = os.environ.get("DPB_USER_DATA_DIR")
    if override:
        return _ensure(Path(override))
    base = Path.home() / "Documents"
    if base.is_dir():
        return _ensure(base / APP_NAME)
    return user_data_dir()


def logs_dir() -> Path:
    return _ensure(app_root() / "logs")


def projects_dir() -> Path:
    """Default folder for new projects (created on demand, not at import)."""
    return documents_root() / "Projects"


def exports_dir() -> Path:
    """Default folder for exported PDFs (created on demand)."""
    return documents_root() / "Exports"


def user_data_dir() -> Path:
    """Per-user configuration directory (``%APPDATA%`` on Windows)."""
    override = os.environ.get("DPB_USER_DATA_DIR")
    if override:
        return _ensure(Path(override))
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        return _ensure(Path(base) / APP_SLUG)
    return _ensure(Path.home() / f".{APP_SLUG.lower()}")


def is_inside_app_folder(path: Path | str) -> bool:
    """True when *path* lives inside the application/build folder.

    Such folders are deleted by an update or a PyInstaller rebuild, so projects
    and exports must not be stored there.
    """
    try:
        candidate = Path(path).expanduser().resolve()
    except OSError:  # pragma: no cover - unreadable path
        return False
    root = app_root().resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def app_database_path() -> Path:
    """Global registry database (known projects, recent exports)."""
    return user_data_dir() / "app_data.db"


# --------------------------------------------------------------------- migration
def _legacy_documents_roots() -> list[Path]:
    """Old ``Documents\\<app name>`` folders, newest naming first."""
    base = Path.home() / "Documents"
    return [base / name for name in LEGACY_APP_NAMES]


def _legacy_user_data_dirs() -> list[Path]:
    """Old ``%APPDATA%\\<slug>`` folders."""
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        return [Path(base) / slug for slug in LEGACY_APP_SLUGS]
    return [Path.home() / f".{slug.lower()}" for slug in LEGACY_APP_SLUGS]


def _merge_tree(source: Path, target: Path) -> bool:
    """Merge *source* into *target* recursively; existing target entries win."""
    moved = False
    for entry in list(source.iterdir()):
        destination = target / entry.name
        if entry.is_dir() and destination.is_dir():
            # Both sides have this folder: descend instead of skipping it, or a
            # nested ``Projects\\<name>`` would never reach the new location.
            moved = _merge_tree(entry, destination) or moved
            try:
                if not any(entry.iterdir()):
                    entry.rmdir()
            except OSError:  # pragma: no cover - locked
                pass
        elif not destination.exists():
            entry.rename(destination)
            moved = True
    return moved


def _merge_move(source: Path, target: Path) -> bool:
    """Move *source* to *target*, merging when the target already exists.

    A plain ``rename`` fails as soon as the new folder was created by an earlier
    launch, so the contents are merged entry by entry - recursively, because the
    interesting data sits several levels down (``Projects\\<name>\\project.db``).
    Existing files in *target* win: they belong to the current version.
    """
    if not source.is_dir():
        return False
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            return True
        moved = _merge_tree(source, target)
        try:
            if not any(source.iterdir()):
                source.rmdir()
        except OSError:  # pragma: no cover - locked
            pass
        return moved
    except OSError:  # pragma: no cover - locked or permission denied
        return False


def migrate_legacy_locations() -> list[str]:
    """Move data written under the old application name into the new folders.

    Renaming the application changes ``documents_root()`` and
    ``user_data_dir()``, which would otherwise make every existing project
    invisible: the dashboard reads ``%APPDATA%\\<slug>\\app_data.db`` and the
    project list would come back empty while the folders still sat under the old
    name.  This runs once per start (it is cheap and idempotent) and returns the
    list of folders that were migrated, for logging.

    The registry database is *not* edited here; ``AppDatabase`` repairs the
    stored paths itself, because the old absolute paths still point at the old
    folder name.
    """
    if os.environ.get("DPB_USER_DATA_DIR"):
        # An explicit override means the caller manages the location.
        return []
    migrated: list[str] = []

    new_documents = Path.home() / "Documents" / APP_NAME
    for legacy in _legacy_documents_roots():
        if legacy == new_documents:
            continue
        if _merge_move(legacy, new_documents):
            migrated.append(str(legacy))

    new_data = user_data_dir()
    for legacy in _legacy_user_data_dirs():
        if legacy == new_data:
            continue
        if _merge_move(legacy, new_data):
            migrated.append(str(legacy))

    return migrated
