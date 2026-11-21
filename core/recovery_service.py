"""Crash recovery markers.

Layout changes are saved to the project database continuously; the marker file
only records that the project was open and may contain unsaved UI state, so the
application can offer to reopen it after an unexpected shutdown.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from core.logging_setup import get_logger
from core.paths import user_data_dir

LOGGER = get_logger("recovery")
MARKER_NAME = "recovery.json"


def marker_path() -> Path:
    return user_data_dir() / MARKER_NAME


def write_marker(db_path: str, project_name: str, dirty: bool = True) -> None:
    """Record that a project is open (written atomically).

    A plain ``write_text`` truncates first: a crash inside that window left an
    empty marker, and the next start then silently skipped the crash prompt.
    """
    payload = {
        "db_path": str(db_path),
        "project_name": project_name,
        "dirty": bool(dirty),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    path = marker_path()
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:  # pragma: no cover - marker is best effort
        LOGGER.debug("Could not write the recovery marker")
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def clear_marker() -> None:
    try:
        marker_path().unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        LOGGER.debug("Could not remove the recovery marker")


def _keep_corrupt_marker() -> None:
    """Preserve an unreadable marker instead of deleting the evidence."""
    path = marker_path()
    try:
        path.replace(path.with_name("recovery.corrupt.json"))
    except OSError:  # pragma: no cover
        LOGGER.debug("Could not keep the corrupt recovery marker")
        clear_marker()


def pending_recovery() -> dict | None:
    """Return the marker contents when a project was left open unexpectedly."""
    path = marker_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Recovery marker unreadable - kept as recovery.corrupt.json")
        _keep_corrupt_marker()
        return None
    if not isinstance(data, dict):
        _keep_corrupt_marker()
        return None
    db_path = Path(str(data.get("db_path", "")))
    if not db_path.is_file():
        clear_marker()
        return None
    return data
