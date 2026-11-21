"""Create / edit project dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from core.paper import available_paper_sizes, normalize_paper_size
from core.settings_service import SettingsService


class ProjectDialog(QDialog):
    """Collects the settings required to create (or edit) a project."""

    def __init__(
        self,
        settings: SettingsService,
        default_directory: str,
        project_service=None,
        edit_mode: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.project_service = project_service
        self.default_directory = default_directory
        self.edit_mode = edit_mode
        self._suggested_folder = ""
        self.setWindowTitle("Project settings" if edit_mode else "New project")
        self.setMinimumWidth(520)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Project details" if self.edit_mode else "Create a new project")
        title.setObjectName("Title")
        layout.addWidget(title)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("For example: Family documents 2026")
        self.name_edit.textChanged.connect(self._on_name_changed)
        form.addRow("Project name *", self.name_edit)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlaceholderText("Optional notes about this project")
        self.description_edit.setFixedHeight(70)
        form.addRow("Description", self.description_edit)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(
            "Leave empty to create a folder automatically"
        )
        self.browse_button = QPushButton("Browse...")
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_button)
        form.addRow("Project folder", folder_row)

        self.paper_combo = QComboBox()
        self.paper_combo.addItems(available_paper_sizes())
        self.paper_combo.setCurrentText(
            normalize_paper_size(str(self.settings.get("pdf", "default_page_size", "A4")))
        )
        self.paper_combo.currentTextChanged.connect(self._on_paper_changed)
        form.addRow("Default page size", self.paper_combo)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["portrait", "landscape"])
        self.orientation_combo.setCurrentText(
            str(self.settings.get("pdf", "default_orientation", "portrait"))
        )
        form.addRow("Default orientation", self.orientation_combo)

        self.custom_width = QDoubleSpinBox()
        self.custom_height = QDoubleSpinBox()
        for spin in (self.custom_width, self.custom_height):
            spin.setRange(50.0, 2000.0)
            spin.setDecimals(1)
            spin.setSuffix(" mm")
        self.custom_width.setValue(210.0)
        self.custom_height.setValue(297.0)
        form.addRow("Custom width", self.custom_width)
        form.addRow("Custom height", self.custom_height)

        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0.0, 50.0)
        self.margin_spin.setDecimals(1)
        self.margin_spin.setSuffix(" mm")
        self.margin_spin.setValue(
            float(self.settings.get("pdf", "default_margin_mm", 10.0))
        )
        form.addRow("Page margins", self.margin_spin)
        layout.addLayout(form)

        self.info_label = QLabel("")
        self.info_label.setObjectName("Muted")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Save" if self.edit_mode else "Create project")
        self.ok_button.setObjectName("Primary")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.browse_button.clicked.connect(self._browse)
        self._on_paper_changed(self.paper_combo.currentText())
        if self.edit_mode:
            self.folder_edit.setEnabled(False)
            self.paper_combo.setEnabled(False)
            self.orientation_combo.setEnabled(False)
            self.margin_spin.setEnabled(False)

    # ------------------------------------------------------------------ helpers
    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the project folder", self.default_directory
        )
        if folder:
            self.folder_edit.setText(folder)
            self._update_info()

    def _on_paper_changed(self, paper: str) -> None:
        is_custom = paper == "Custom"
        self.custom_width.setEnabled(is_custom)
        self.custom_height.setEnabled(is_custom)

    def _on_name_changed(self, name: str) -> None:
        if not self.edit_mode and not self.folder_edit.text().strip():
            self._suggested_folder = self._suggest(name)
        self._update_info()

    def _suggest(self, name: str) -> str:
        if self.project_service is None or not name.strip():
            return ""
        try:
            return str(self.project_service.unique_project_folder(name.strip()))
        except Exception:
            return ""

    def _update_info(self) -> None:
        if not self.name_edit.text().strip():
            self.info_label.setText("A project name is required.")
            return
        if self.edit_mode:
            self.info_label.setText("")
            return
        folder = self.folder_edit.text().strip() or self._suggested_folder
        self.info_label.setText(
            f"The project will be created in:\n{folder}" if folder else ""
        )

    # ------------------------------------------------------------------- result
    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Project name", "Please enter a project name.")
            return
        folder_text = self.folder_edit.text().strip()
        if folder_text and not self.edit_mode:
            path = Path(folder_text)
            if path.exists() and any(path.iterdir()):
                QMessageBox.warning(
                    self,
                    "Folder not empty",
                    "The selected folder is not empty.\n"
                    "Choose an empty folder or leave the field blank so a new "
                    "folder is created automatically.",
                )
                return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip(),
            "folder": self.folder_edit.text().strip(),
            "page_size": self.paper_combo.currentText(),
            "orientation": self.orientation_combo.currentText(),
            "custom_width_mm": self.custom_width.value(),
            "custom_height_mm": self.custom_height.value(),
            "margin_mm": self.margin_spin.value(),
        }

    def load_project(self, project) -> None:
        """Fill the form when editing an existing project."""
        self.name_edit.setText(project.name)
        self.description_edit.setPlainText(project.description)
        self.folder_edit.setText(project.project_folder)
        self.paper_combo.setCurrentText(normalize_paper_size(project.default_page_size))
        self.orientation_combo.setCurrentText(project.default_orientation)
        self._update_info()

