"""JSON backed application settings with strict validation.

Corrupted or partially invalid configuration files never prevent the
application from starting: invalid values fall back to the documented
defaults and the bad file is preserved as ``settings.corrupt.json``.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger
from core.paths import exports_dir, is_inside_app_folder, projects_dir, settings_path

LOGGER = get_logger("settings")

SUPPORTED_IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
)

PAGE_SIZES = ("A4", "A3", "Letter", "Legal", "Custom")
ORIENTATIONS = ("portrait", "landscape")
FIT_MODES = ("contain", "cover", "stretch")
DPI_PRESETS = ("Draft", "Standard", "High", "Original")
DUPLICATE_BEHAVIOURS = ("ask", "skip", "import")


def default_settings() -> dict[str, Any]:
    """Return a fresh copy of the built-in defaults."""
    return {
        "general": {
            "default_project_directory": str(projects_dir()),
            "default_export_directory": str(exports_dir()),
            "theme": "light",
            "recent_project_count": 10,
            "autosave_interval_seconds": 60,
            "confirm_before_delete": True,
            "reopen_last_project": True,
            "allow_unsafe_project_folder": False,
        },
        "import": {
            "default_category": "Other",
            "thumbnail_size": 160,
            "duplicate_behavior": "ask",
            "supported_extensions": list(SUPPORTED_IMAGE_EXTENSIONS),
        },
        "pdf": {
            "default_page_size": "A4",
            "default_orientation": "portrait",
            "default_margin_mm": 10.0,
            "default_dpi": "High",
            "default_compression": True,
            "output_naming_pattern": "{project}_{date}",
        },
        "layout": {
            "default_fit_mode": "contain",
            "snap_to_grid": True,
            "grid_spacing_mm": 5.0,
            "show_alignment_guides": True,
            "auto_overflow": True,
            "default_rows": 2,
            "default_columns": 2,
            "place_original_size": True,
            "original_size_dpi": 300,
            "snap_small_cards": True,
        },
    }
def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(minimum, min(maximum, number))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_choice(value: Any, choices: tuple[str, ...], default: str) -> str:
    if isinstance(value, str):
        for choice in choices:
            if value.strip().lower() == choice.lower():
                return choice
    return default


def _as_text(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _as_path(value: Any, default: str) -> str:
    """Validate a folder setting **without touching the filesystem**.

    Validation used to call ``mkdir()``, so merely loading ``settings.json``
    created directories (and blocked the start-up on an unplugged network share).

    Only clearly unusable values are replaced: empty text, unparsable text and a
    path that points at an existing *file*.  A folder that merely is not reachable
    right now - an unplugged drive or a network share that is offline - is kept,
    because resetting it to the default silently lost the user's configuration.
    """
    text = _as_text(value, "")
    if not text:
        return default
    try:
        path = Path(text).expanduser()
    except (OSError, ValueError):  # pragma: no cover - exotic input
        return default
    if path.exists() and not path.is_dir():
        return default  # points at a file
    parent = path.parent
    if parent != path and not parent.is_dir():
        LOGGER.warning(
            "Folder %s is not reachable right now; keeping the setting", path
        )
    return str(path)


def _as_extensions(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return list(SUPPORTED_IMAGE_EXTENSIONS)
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip().lower()
        if not text:
            continue
        if not text.startswith("."):
            text = f".{text}"
        if text not in cleaned:
            cleaned.append(text)
    return cleaned or list(SUPPORTED_IMAGE_EXTENSIONS)



def _keep_unknown(validated: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Keep keys this build does not know about (newer settings file)."""
    unknown = {key: value for key, value in raw.items() if key not in validated}
    return {**unknown, **validated}


def sanitize_settings(raw: Any) -> dict[str, Any]:
    """Merge *raw* over the defaults, dropping every invalid value.

    Unknown keys and whole sections are preserved so a settings file written by a
    newer build survives a load/save cycle in an older one.
    """
    defaults = default_settings()
    if not isinstance(raw, dict):
        return defaults

    general = raw.get("general") if isinstance(raw.get("general"), dict) else {}
    imp = raw.get("import") if isinstance(raw.get("import"), dict) else {}
    pdf = raw.get("pdf") if isinstance(raw.get("pdf"), dict) else {}
    layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
    allow_unsafe = _as_bool(general.get("allow_unsafe_project_folder"), False)

    result: dict[str, Any] = {
        "general": {
            "default_project_directory": _as_path(
                general.get("default_project_directory"),
                defaults["general"]["default_project_directory"],
            ),
            "default_export_directory": _as_path(
                general.get("default_export_directory"),
                defaults["general"]["default_export_directory"],
            ),
            "theme": _as_choice(general.get("theme"), ("light",), "light"),
            "recent_project_count": _as_int(
                general.get("recent_project_count"), 10, 0, 50
            ),
            "autosave_interval_seconds": _as_int(
                general.get("autosave_interval_seconds"), 60, 0, 3600
            ),
            "confirm_before_delete": _as_bool(
                general.get("confirm_before_delete"), True
            ),
            "reopen_last_project": _as_bool(general.get("reopen_last_project"), True),
            "allow_unsafe_project_folder": allow_unsafe,
        },
        "import": {
            "default_category": _as_text(imp.get("default_category"), "Other"),
            "thumbnail_size": _as_int(imp.get("thumbnail_size"), 160, 64, 512),
            "duplicate_behavior": _as_choice(
                imp.get("duplicate_behavior"), DUPLICATE_BEHAVIOURS, "ask"
            ),
            "supported_extensions": _as_extensions(imp.get("supported_extensions")),
        },
        "pdf": {
            "default_page_size": _as_choice(
                pdf.get("default_page_size"), PAGE_SIZES, "A4"
            ),
            "default_orientation": _as_choice(
                pdf.get("default_orientation"), ORIENTATIONS, "portrait"
            ),
            "default_margin_mm": _as_float(
                pdf.get("default_margin_mm"), 10.0, 0.0, 50.0
            ),
            "default_dpi": _as_choice(pdf.get("default_dpi"), DPI_PRESETS, "High"),
            "default_compression": _as_bool(pdf.get("default_compression"), True),
            "output_naming_pattern": _as_text(
                pdf.get("output_naming_pattern"), "{project}_{date}"
            ),
        },
        "layout": {
            "default_fit_mode": _as_choice(
                layout.get("default_fit_mode"), FIT_MODES, "contain"
            ),
            "snap_to_grid": _as_bool(layout.get("snap_to_grid"), True),
            "grid_spacing_mm": _as_float(layout.get("grid_spacing_mm"), 5.0, 1.0, 50.0),
            "show_alignment_guides": _as_bool(
                layout.get("show_alignment_guides"), True
            ),
            "auto_overflow": _as_bool(layout.get("auto_overflow"), True),
            "default_rows": _as_int(layout.get("default_rows"), 2, 1, 8),
            "default_columns": _as_int(layout.get("default_columns"), 2, 1, 8),
            "place_original_size": _as_bool(
                layout.get("place_original_size"), True
            ),
            "original_size_dpi": _as_int(
                layout.get("original_size_dpi"), 300, 72, 1200
            ),
            "snap_small_cards": _as_bool(layout.get("snap_small_cards"), True),
        },
    }
    if not allow_unsafe:
        # A hand-edited settings file must not point projects or exports at the
        # application folder: an update or a rebuild replaces that folder.
        for key in ("default_project_directory", "default_export_directory"):
            current = result["general"][key]
            if is_inside_app_folder(current):
                LOGGER.warning(
                    "Ignoring general.%s '%s': it is inside the application folder",
                    key,
                    current,
                )
                result["general"][key] = defaults["general"][key]
    for name, section in (
        ("general", general),
        ("import", imp),
        ("pdf", pdf),
        ("layout", layout),
    ):
        result[name] = _keep_unknown(result[name], section)
    for name, value in raw.items():
        if name not in result:
            result[name] = value
    return result



class SettingsService:
    """Load, validate, expose and persist application settings."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else settings_path()
        self._data: dict[str, Any] = default_settings()
        self.last_error: str = ""

    # ------------------------------------------------------------------ paths
    @property
    def path(self) -> Path:
        return self._path

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    # --------------------------------------------------------------- lifecycle
    def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            self._data = default_settings()
            self._safe_save()
            return self._data

        try:
            with self._path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Settings file unreadable (%s) - restoring defaults", exc)
            self._backup_corrupt_file()
            self._data = default_settings()
            self._safe_save()
            return self._data

        self._data = sanitize_settings(raw)
        return self._data

    def _safe_save(self) -> None:
        """Save during start-up without ever aborting the launch."""
        self.save()

    def save(self) -> bool:
        """Write the settings file atomically.

        Returns ``False`` when the file cannot be written (read-only profile,
        antivirus lock, full disk) instead of raising: every caller is a UI slot,
        and a failed write must not abort the action that triggered it.
        """
        temporary = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except OSError as exc:
            self.last_error = str(exc)
            LOGGER.warning("Settings could not be written to %s: %s", self._path, exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - locked temp file
                pass
            return False
        self.last_error = ""
        return True

    def _backup_corrupt_file(self) -> None:
        try:
            target = self._path.with_name("settings.corrupt.json")
            shutil.copy2(self._path, target)
        except OSError:  # pragma: no cover
            LOGGER.debug("Could not back up the corrupt settings file")

    # ----------------------------------------------------------------- access
    def section(self, name: str) -> dict[str, Any]:
        return self._data.setdefault(name, {})

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.section(section).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        self.section(section)[key] = value

    def update_section(self, section: str, values: dict[str, Any]) -> None:
        self.section(section).update(values)

    def replace(self, data: dict[str, Any]) -> None:
        """Replace all values with *data* after sanitising it."""
        self._data = sanitize_settings(data)

    def supported_extensions(self) -> list[str]:
        return list(
            self.get("import", "supported_extensions", SUPPORTED_IMAGE_EXTENSIONS)
        )
