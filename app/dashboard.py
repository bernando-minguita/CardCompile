"""Dashboard: quick actions, statistics, recent projects and exports."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.context import AppContext
from core.paths import APP_NAME, APP_VERSION


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    heading = QLabel(title)
    heading.setObjectName("SectionTitle")
    layout.addWidget(heading)
    return frame, layout


def _format_timestamp(value: str) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return value


class Dashboard(QWidget):
    """Landing page with the most common actions."""

    new_project_requested = Signal()
    open_project_requested = Signal()
    open_recent_requested = Signal(str)
    import_requested = Signal()
    open_export_requested = Signal(str)

    def __init__(self, context: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        subtitle = QLabel(
            "Combine scanned Iqama, Driving License, Istimara, Passport and other "
            "documents into one professionally formatted PDF - fully offline."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        actions = QHBoxLayout()
        self.new_button = QPushButton("New project")
        self.new_button.setObjectName("Primary")
        self.new_button.setMinimumHeight(38)
        self.open_button = QPushButton("Open project...")
        self.open_button.setMinimumHeight(38)
        self.import_button = QPushButton("Import documents")
        self.import_button.setMinimumHeight(38)
        self.import_button.setEnabled(False)
        self.import_button.setToolTip("Import scans into the current project")
        actions.addWidget(self.new_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.import_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        stats = QHBoxLayout()
        self.projects_card, projects_layout = _card("Projects")
        self.projects_value = QLabel("0")
        self.projects_value.setObjectName("Metric")
        projects_layout.addWidget(self.projects_value)
        projects_layout.addWidget(QLabel("Stored in the project registry"))

        self.exports_card, exports_layout = _card("Recent exports")
        self.exports_value = QLabel("0")
        self.exports_value.setObjectName("Metric")
        exports_layout.addWidget(self.exports_value)
        exports_layout.addWidget(QLabel("PDF files produced recently"))

        self.version_card, version_layout = _card("Application")
        self.version_value = QLabel(APP_VERSION)
        self.version_value.setObjectName("Metric")
        version_layout.addWidget(self.version_value)
        version_layout.addWidget(QLabel("Offline, local processing only"))

        stats.addWidget(self.projects_card)
        stats.addWidget(self.exports_card)
        stats.addWidget(self.version_card)
        layout.addLayout(stats)

        projects_card, projects_layout = _card("Recent projects")
        self.recent_table = self._make_table(
            ["Project", "Last modified", "Documents", "Pages", "Folder"]
        )
        self.recent_table.doubleClicked.connect(self._open_selected_project)
        self.recent_table.setMinimumHeight(190)
        projects_layout.addWidget(self.recent_table)
        self.open_recent_button = QPushButton("Open selected project")
        projects_layout.addWidget(
            self.open_recent_button, alignment=Qt.AlignmentFlag.AlignRight
        )
        layout.addWidget(projects_card)

        exports_card, exports_layout = _card("Recently exported PDFs")
        self.exports_list = self._make_table(["Project", "File", "Exported"])
        self.exports_list.doubleClicked.connect(self._open_selected_export)
        self.exports_list.setMinimumHeight(150)
        exports_layout.addWidget(self.exports_list)
        self.open_export_button = QPushButton("Open PDF")
        exports_layout.addWidget(
            self.open_export_button, alignment=Qt.AlignmentFlag.AlignRight
        )
        layout.addWidget(exports_card)

        info_card, info_layout = _card("How it works")
        steps = QLabel(
            "1. Create a project and choose the default page size.\n"
            "2. Import scans - the original files are never modified.\n"
            "3. Place them on the page, arrange them with the grid presets and edit "
            "position, size, rotation and crop.\n"
            "4. Export a validated PDF at the resolution you need."
        )
        steps.setWordWrap(True)
        info_layout.addWidget(steps)
        layout.addWidget(info_card)

        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        self.new_button.clicked.connect(self.new_project_requested.emit)
        self.open_button.clicked.connect(self.open_project_requested.emit)
        self.import_button.clicked.connect(self.import_requested.emit)
        self.open_recent_button.clicked.connect(self._open_selected_project)
        self.open_export_button.clicked.connect(self._open_selected_export)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = table.horizontalHeader()
        for index in range(len(headers) - 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
        return table

    # ------------------------------------------------------------------- data
    def refresh(self) -> None:
        self.projects_value.setText(str(self.context.projects.count_projects()))
        recent = self.context.projects.recent_projects()
        self.recent_table.setRowCount(len(recent))
        for row, project in enumerate(recent):
            summary = self.context.projects.project_summary(project.db_path)
            missing = not project.exists or not summary.valid
            name_item = QTableWidgetItem(
                (summary.name if summary.valid else project.name)
                + ("   (missing)" if missing else "")
            )
            name_item.setData(Qt.ItemDataRole.UserRole, project.id)
            if missing:
                name_item.setToolTip(
                    "The project file is missing:\n"
                    + project.db_path
                    + "\n\nDouble-click it to locate the project.db, or open "
                    "Projects to remove it from the list."
                )
                name_item.setForeground(QBrush(QColor("#B3261E")))
            self.recent_table.setItem(row, 0, name_item)
            self.recent_table.setItem(
                row, 1, QTableWidgetItem(_format_timestamp(summary.updated_at))
            )
            self.recent_table.setItem(
                row, 2, QTableWidgetItem(str(summary.document_count))
            )
            self.recent_table.setItem(row, 3, QTableWidgetItem(str(summary.page_count)))
            self.recent_table.setItem(
                row, 4, QTableWidgetItem(summary.project_folder)
            )

        exports = self.context.app_db.recent_exports(10)
        self.exports_value.setText(str(len(exports)))
        self.exports_list.setRowCount(len(exports))
        for row, record in enumerate(exports):
            self.exports_list.setItem(
                row, 0, QTableWidgetItem(record.get("project_name", ""))
            )
            file_item = QTableWidgetItem(record.get("output_path", ""))
            file_item.setData(Qt.ItemDataRole.UserRole, record.get("output_path", ""))
            self.exports_list.setItem(row, 1, file_item)
            self.exports_list.setItem(
                row, 2, QTableWidgetItem(_format_timestamp(record.get("exported_at", "")))
            )
        self.import_button.setEnabled(self.context.has_project)

    # ----------------------------------------------------------------- actions
    def _open_selected_project(self) -> None:
        row = self.recent_table.currentRow()
        if row < 0:
            return
        item = self.recent_table.item(row, 0)
        if item is None:
            return
        project_id = item.data(Qt.ItemDataRole.UserRole)
        if project_id:
            self.open_recent_requested.emit(project_id)

    def _open_selected_export(self) -> None:
        row = self.exports_list.currentRow()
        if row < 0:
            return
        item = self.exports_list.item(row, 1)
        if item is not None:
            self.open_export_requested.emit(item.data(Qt.ItemDataRole.UserRole))

