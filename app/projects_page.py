"""Projects page: every known project with open/rename/remove actions."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.context import AppContext
from core.project_service import ProjectSummary


def _format_time(value: str) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return value


class ProjectsPage(QWidget):
    """Table of all projects registered on this machine."""

    open_requested = Signal(str)
    create_requested = Signal()
    locate_requested = Signal(str)

    def __init__(self, context: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self._rows: list[str] = []
        self._active_workspace = None
        # Summaries are expensive (each one opens a database), so they are
        # cached against the file stamp and the search box is debounced.
        self._summary_cache: dict[str, tuple[int, int, ProjectSummary]] = {}
        self._summary_calls = 0
        self._build_ui()

    def set_active_workspace(self, workspace) -> None:
        """Tell the page which project is open so it can reuse its connection."""
        self._active_workspace = workspace

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.refresh)

        title = QLabel("Projects")
        title.setObjectName("Title")
        layout.addWidget(title)

        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search projects...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        self.new_button = QPushButton("New project")
        self.new_button.setObjectName("Primary")
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.new_button)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Project", "Documents", "Pages", "Last opened", "Folder"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._open_selected)
        header = self.table.horizontalHeader()
        for index in range(4):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.locate_button = QPushButton("Locate...")
        self.locate_button.setToolTip(
            "Find a project after its folder was moved or renamed"
        )
        self.rename_button = QPushButton("Rename")
        self.folder_button = QPushButton("Show folder")
        self.forget_button = QPushButton("Remove from list")
        self.delete_button = QPushButton("Delete project files")
        self.delete_button.setObjectName("Danger")
        actions.addWidget(self.open_button)
        actions.addWidget(self.locate_button)
        actions.addWidget(self.rename_button)
        actions.addWidget(self.folder_button)
        actions.addStretch(1)
        actions.addWidget(self.forget_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)

        self.new_button.clicked.connect(self.create_requested.emit)
        self.open_button.clicked.connect(self._open_selected)
        self.locate_button.clicked.connect(self._locate_selected)
        self.rename_button.clicked.connect(self._rename_selected)
        self.folder_button.clicked.connect(self._show_folder)
        self.forget_button.clicked.connect(self._forget_selected)
        self.delete_button.clicked.connect(self._delete_selected)

    # -------------------------------------------------------------------- data
    def _on_search_changed(self, _text: str) -> None:
        """Debounce: listing must not re-read databases on every keystroke."""
        self._search_timer.start()

    def _live_summary(self, project) -> ProjectSummary:
        """Summary of the project that is open right now (no second connection)."""
        workspace = self._active_workspace
        live = getattr(workspace, "project", None)
        return ProjectSummary(
            db_path=project.db_path,
            name=live.name if live is not None else project.name,
            description=live.description if live is not None else "",
            project_folder=live.project_folder if live is not None else project.project_folder,
            document_count=workspace.document_service.document_count(),
            page_count=workspace.document_service.page_count(),
            updated_at=live.updated_at if live is not None else "",
            valid=True,
            problem="",
        )

    def summary_for(self, project) -> ProjectSummary:
        """Cached summary; re-read only when the database file changed."""
        workspace = self._active_workspace
        if (
            workspace is not None
            and getattr(workspace, "project", None) is not None
            and workspace.project.id == project.id
            and workspace.project.db_path and Path(workspace.project.db_path) == Path(project.db_path)
        ):
            return self._live_summary(project)
        try:
            stat = Path(project.db_path).stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        cached = self._summary_cache.get(project.db_path)
        if cached is not None and stamp is not None and cached[:2] == stamp:
            return cached[2]
        self._summary_calls += 1
        summary = self.context.projects.project_summary(project.db_path)
        if stamp is not None:
            self._summary_cache[project.db_path] = (stamp[0], stamp[1], summary)
        return summary

    def _status_suffix(self, summary: ProjectSummary, project) -> str:
        """Short honest label instead of a blanket "(missing)"."""
        problem = summary.problem
        if problem == "empty":
            return "   (empty - database copied without -wal?)"
        if problem == "foreign":
            return "   (not a project)"
        if problem in ("missing", "unreadable") or not project.exists:
            return "   (missing)"
        return "   (unreadable)"

    def _problem_tooltip(self, summary: ProjectSummary, project) -> str:
        problem = summary.problem
        if problem == "empty":
            return (
                "This project database contains no tables:\n"
                + project.db_path
                + "\n\nIt was most likely copied or backed up without the "
                "project.db-wal file, which still held the recent changes.\n\n"
                "Restore the backup of the whole project folder, or copy "
                "project.db together with project.db-wal and project.db-shm."
            )
        if problem == "foreign":
            return (
                "This file is a SQLite database, but it is not a Document PDF "
                "Builder project:\n" + project.db_path
            )
        return (
            "The project file is missing or unreadable:\n"
            + project.db_path
            + "\n\nSelect the row and press 'Locate...' to point at the "
            "project.db in its new location."
        )

    def refresh(self) -> None:
        query = self.search_edit.text().strip().lower()
        projects = self.context.projects.known_projects()
        visible = [
            project
            for project in projects
            if not query
            or query in project.name.lower()
            or query in project.project_folder.lower()
        ]
        self.table.setRowCount(len(visible))
        self._rows = []
        for row, project in enumerate(visible):
            summary = self.summary_for(project)
            missing = not project.exists or not summary.valid
            name_item = QTableWidgetItem(
                (summary.name if summary.valid else project.name)
                + (self._status_suffix(summary, project) if missing else "")
            )
            name_item.setData(Qt.ItemDataRole.UserRole, project.id)
            if missing:
                name_item.setToolTip(self._problem_tooltip(summary, project))
                name_item.setForeground(QBrush(QColor("#B3261E")))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(summary.document_count)))
            self.table.setItem(row, 2, QTableWidgetItem(str(summary.page_count)))
            self.table.setItem(
                row, 3, QTableWidgetItem(_format_time(project.last_opened_at))
            )
            self.table.setItem(row, 4, QTableWidgetItem(project.project_folder))
            self._rows.append(project.id)

    def selected_project_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    # ----------------------------------------------------------------- actions
    def _open_selected(self) -> None:
        project_id = self.selected_project_id()
        if project_id:
            self.open_requested.emit(project_id)
        else:
            QMessageBox.information(
                self,
                "Open project",
                "Select a project in the list first (or double-click it).",
            )

    def _locate_selected(self) -> None:
        project_id = self.selected_project_id()
        if project_id:
            self.locate_requested.emit(project_id)
        else:
            QMessageBox.information(
                self,
                "Locate project",
                "Select the project that should be re-linked first.",
            )

    def _rename_selected(self) -> None:
        project_id = self.selected_project_id()
        if project_id is None:
            return
        known = self.context.app_db.get_project(project_id)
        row = self.table.currentRow()
        current = self.table.item(row, 0)
        # The visible label may carry the "   (missing)" marker - never put that
        # suffix into the project name.
        default_name = known.name if known is not None else (
            current.text() if current else ""
        )
        new_name, ok = QInputDialog.getText(
            self,
            "Rename project",
            "Project name",
            text=default_name,
        )
        if not ok or not new_name.strip():
            return
        clean = new_name.replace("   (missing)", "").strip()
        if not clean:
            return
        try:
            self.context.projects.rename_known_project(project_id, clean)
        except Exception as exc:
            QMessageBox.warning(self, "Rename project", str(exc))
            return
        self.refresh()

    def _show_folder(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        folder = self.table.item(row, 4)
        if folder is None:
            return
        try:
            os.startfile(folder.text())  # type: ignore[attr-defined]
        except Exception:
            QMessageBox.information(self, "Project folder", folder.text())

    def _forget_selected(self) -> None:
        project_id = self.selected_project_id()
        if project_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove from list",
            "Remove this project from the recent list?\nThe files on disk are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.context.projects.forget_project(project_id)
            self.refresh()

    def _delete_selected(self) -> None:
        project_id = self.selected_project_id()
        if project_id is None:
            return
        known = self.context.app_db.get_project(project_id)
        if known is None:
            return
        answer = QMessageBox.warning(
            self,
            "Delete project",
            f"Delete the project '{known.name}' and its folder?\n\n"
            f"{known.project_folder}\n\n"
            "The original scanned images are NOT deleted - only the project "
            "database and its thumbnails.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        active = self._active_workspace
        if (
            active is not None
            and getattr(active, "project", None) is not None
            and active.project.id == project_id
        ):
            # Deleting a live project removed documents/ and thumbnails/ while the
            # editor still held the database open: the second connection saw half
            # the files and the running session kept writing to a deleted file.
            QMessageBox.information(
                self,
                "Project is open",
                f"The project '{known.name}' is open in the editor.\n\n"
                "Close it first (File > Close project) and then delete it - "
                "an open project is never deleted.",
            )
            return
        try:
            workspace = self.context.projects.open_project(known.db_path)
            self.context.projects.delete_project_files(workspace)
        except Exception as exc:
            QMessageBox.warning(self, "Delete project", str(exc))
        self.refresh()

