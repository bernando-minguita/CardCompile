"""Shared application context passed to the windows and pages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.project_service import ProjectService
from core.settings_service import SettingsService
from database.app_database import AppDatabase


@dataclass
class AppContext:
    """Services shared by the whole application."""

    settings: SettingsService
    app_db: AppDatabase
    projects: ProjectService
    workspace: object | None = None
    last_export_path: str = ""
    startup_notice: str = ""
    pending_files: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    def set_workspace(self, workspace) -> None:
        self.workspace = workspace

    def clear_workspace(self) -> None:
        self.workspace = None

    @property
    def has_project(self) -> bool:
        return self.workspace is not None

    def export_directory(self) -> Path:
        text = self.settings.get("general", "default_export_directory", "")
        return Path(text) if text else Path.cwd()
