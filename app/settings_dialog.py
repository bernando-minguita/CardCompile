"""Application settings dialog (general, import, PDF, layout, categories)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.paths import is_inside_app_folder
from core.settings_service import (
    DPI_PRESETS,
    DUPLICATE_BEHAVIOURS,
    FIT_MODES,
    ORIENTATIONS,
    PAGE_SIZES,
    SettingsService,
)


class SettingsDialog(QDialog):
    """Edit and persist the JSON application settings."""

    def __init__(self, settings: SettingsService, workspace=None, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.workspace = workspace
        self.setWindowTitle("Settings")
        self.setMinimumSize(620, 560)
        self._build_ui()
        self._load_values()

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general_tab(), "General")
        self.tabs.addTab(self._build_import_tab(), "Import")
        self.tabs.addTab(self._build_pdf_tab(), "PDF")
        self.tabs.addTab(self._build_layout_tab(), "Layout")
        if self.workspace is not None:
            self.tabs.addTab(self._build_categories_tab(), "Categories")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save settings")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        project_row = QHBoxLayout()
        self.project_dir_edit = QLineEdit()
        project_browse = QPushButton("Browse...")
        project_browse.clicked.connect(lambda: self._browse(self.project_dir_edit))
        project_row.addWidget(self.project_dir_edit, 1)
        project_row.addWidget(project_browse)
        form.addRow("Default project directory", project_row)

        export_row = QHBoxLayout()
        self.export_dir_edit = QLineEdit()
        export_browse = QPushButton("Browse...")
        export_browse.clicked.connect(lambda: self._browse(self.export_dir_edit))
        export_row.addWidget(self.export_dir_edit, 1)
        export_row.addWidget(export_browse)
        form.addRow("Default export directory", export_row)

        self.theme_label = QLabel("Light (professional light theme)")
        form.addRow("Theme", self.theme_label)

        self.recent_spin = QSpinBox()
        self.recent_spin.setRange(3, 50)
        form.addRow("Recent projects shown", self.recent_spin)

        self.autosave_spin = QSpinBox()
        self.autosave_spin.setRange(0, 3600)
        self.autosave_spin.setSuffix(" s")
        self.autosave_spin.setSpecialValueText("Off")
        self.autosave_spin.setToolTip(
            "How often unsaved changes are flushed to the project database. "
            "0 disables autosave (changes are still written immediately)."
        )
        form.addRow("Auto-save interval", self.autosave_spin)

        self.confirm_delete_check = QCheckBox("Ask before deleting anything")
        form.addRow(self.confirm_delete_check)

        self.reopen_check = QCheckBox("Offer to reopen the last project on start")
        form.addRow(self.reopen_check)
        return page

    def _build_import_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.default_category_edit = QLineEdit()
        form.addRow("Default document category", self.default_category_edit)

        self.thumbnail_spin = QSpinBox()
        self.thumbnail_spin.setRange(64, 512)
        self.thumbnail_spin.setSuffix(" px")
        form.addRow("Thumbnail size", self.thumbnail_spin)

        self.duplicate_combo = QComboBox()
        for value in DUPLICATE_BEHAVIOURS:
            self.duplicate_combo.addItem(value, value)
        form.addRow("Duplicate detection", self.duplicate_combo)

        self.extensions_edit = QLineEdit()
        self.extensions_edit.setToolTip("Comma separated list, e.g. .jpg,.png")
        form.addRow("Supported formats", self.extensions_edit)
        return page

    def _build_pdf_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.pdf_paper_combo = QComboBox()
        self.pdf_paper_combo.addItems(PAGE_SIZES)
        form.addRow("Default page size", self.pdf_paper_combo)

        self.pdf_orientation_combo = QComboBox()
        self.pdf_orientation_combo.addItems(ORIENTATIONS)
        form.addRow("Default orientation", self.pdf_orientation_combo)

        self.pdf_margin_spin = QDoubleSpinBox()
        self.pdf_margin_spin.setRange(0.0, 50.0)
        self.pdf_margin_spin.setDecimals(1)
        self.pdf_margin_spin.setSuffix(" mm")
        form.addRow("Default margins", self.pdf_margin_spin)

        self.pdf_dpi_combo = QComboBox()
        self.pdf_dpi_combo.addItems(DPI_PRESETS)
        form.addRow("Default export quality", self.pdf_dpi_combo)

        self.pdf_compression_check = QCheckBox("Compress the exported PDF")
        form.addRow(self.pdf_compression_check)

        self.pdf_naming_edit = QLineEdit()
        self.pdf_naming_edit.setToolTip(
            "Placeholders: {project} and {date}, e.g. {project}_{date}"
        )
        form.addRow("Output file name pattern", self.pdf_naming_edit)
        return page

    def _build_layout_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(FIT_MODES)
        form.addRow("Default image fitting", self.fit_combo)

        self.snap_check = QCheckBox("Snap images to the grid")
        form.addRow(self.snap_check)

        self.grid_spin = QDoubleSpinBox()
        self.grid_spin.setRange(1.0, 50.0)
        self.grid_spin.setDecimals(1)
        self.grid_spin.setSuffix(" mm")
        form.addRow("Grid spacing", self.grid_spin)

        self.guides_check = QCheckBox("Show alignment guides and margins")
        form.addRow(self.guides_check)

        self.overflow_check = QCheckBox("Create extra pages automatically when needed")
        form.addRow(self.overflow_check)

        self.original_size_check = QCheckBox(
            "Place new images at their original size (from the image DPI)"
        )
        self.original_size_check.setToolTip(
            "When enabled, a cropped card export keeps its real physical size "
            "instead of being stretched to the printable area."
        )
        form.addRow(self.original_size_check)

        self.original_size_dpi = QSpinBox()
        self.original_size_dpi.setRange(72, 1200)
        self.original_size_dpi.setSuffix(" dpi")
        self.original_size_dpi.setToolTip(
            "Fallback resolution used when the image file carries no DPI information."
        )
        form.addRow("Assumed DPI when unknown", self.original_size_dpi)

        self.snap_small_cards_check = QCheckBox(
            "Give cards their real size when the file DPI is wrong"
        )
        self.snap_small_cards_check.setToolTip(
            "A card cropped from a scan usually keeps the scanner's 300 dpi tag, "
            "so \"original size\" would print it at about 30 x 19 mm. When this is "
            "on, an image that would be placed smaller than an ID-1 card "
            "(85.6 mm) is scaled up to that size, keeping its proportions."
        )
        form.addRow(self.snap_small_cards_check)

        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 8)
        self.columns_spin = QSpinBox()
        self.columns_spin.setRange(1, 8)
        form.addRow("Default rows", self.rows_spin)
        form.addRow("Default columns", self.columns_spin)
        return page

    def _build_categories_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        info = QLabel(
            "Document categories belong to the open project. Changes are applied "
            "immediately."
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.category_list = QListWidget()
        layout.addWidget(self.category_list, 1)

        buttons = QHBoxLayout()
        add_button = QPushButton("Add category")
        rename_button = QPushButton("Rename")
        delete_button = QPushButton("Delete")
        buttons.addWidget(add_button)
        buttons.addWidget(rename_button)
        buttons.addWidget(delete_button)
        layout.addLayout(buttons)

        add_button.clicked.connect(self._add_category)
        rename_button.clicked.connect(self._rename_category)
        delete_button.clicked.connect(self._delete_category)
        self._reload_categories()
        return page

    # ------------------------------------------------------------------ helpers
    def _browse(self, target: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder", target.text())
        if folder:
            target.setText(folder)

    def _reload_categories(self) -> None:
        if self.workspace is None or not hasattr(self, "category_list"):
            return
        self.category_list.clear()
        for category in self.workspace.document_service.categories():
            self.category_list.addItem(f"{category.name}  ({category.id[:8]})")
            item = self.category_list.item(self.category_list.count() - 1)
            item.setData(32, category.id)

    def _add_category(self) -> None:
        if self.workspace is None:
            return
        name, ok = QInputDialog.getText(self, "New category", "Category name")
        if not ok or not name.strip():
            return
        self.workspace.document_service.create_category(name.strip())
        self._reload_categories()

    def _selected_category_id(self) -> str | None:
        item = self.category_list.currentItem()
        return item.data(32) if item is not None else None

    def _rename_category(self) -> None:
        category_id = self._selected_category_id()
        if category_id is None or self.workspace is None:
            return
        category = self.workspace.document_service.get_category(category_id)
        if category is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename category", "Category name", text=category.name
        )
        if ok and name.strip():
            self.workspace.document_service.rename_category(category_id, name.strip())
            self._reload_categories()

    def _delete_category(self) -> None:
        category_id = self._selected_category_id()
        if category_id is None or self.workspace is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete category",
            "Delete this category? Documents keep existing but become uncategorised.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.workspace.document_service.delete_category(category_id)
            self._reload_categories()

    # --------------------------------------------------------------- load / save
    def _load_values(self) -> None:
        get = self.settings.get
        self.project_dir_edit.setText(str(get("general", "default_project_directory", "")))
        self.export_dir_edit.setText(str(get("general", "default_export_directory", "")))
        self.recent_spin.setValue(int(get("general", "recent_project_count", 10)))
        self.autosave_spin.setValue(int(get("general", "autosave_interval_seconds", 60)))
        self.confirm_delete_check.setChecked(
            bool(get("general", "confirm_before_delete", True))
        )
        self.reopen_check.setChecked(bool(get("general", "reopen_last_project", True)))

        self.default_category_edit.setText(str(get("import", "default_category", "Other")))
        self.thumbnail_spin.setValue(int(get("import", "thumbnail_size", 160)))
        index = self.duplicate_combo.findData(get("import", "duplicate_behavior", "ask"))
        self.duplicate_combo.setCurrentIndex(max(0, index))
        self.extensions_edit.setText(
            ", ".join(self.settings.supported_extensions())
        )

        self.pdf_paper_combo.setCurrentText(str(get("pdf", "default_page_size", "A4")))
        self.pdf_orientation_combo.setCurrentText(
            str(get("pdf", "default_orientation", "portrait"))
        )
        self.pdf_margin_spin.setValue(float(get("pdf", "default_margin_mm", 10.0)))
        self.pdf_dpi_combo.setCurrentText(str(get("pdf", "default_dpi", "High")))
        self.pdf_compression_check.setChecked(
            bool(get("pdf", "default_compression", True))
        )
        self.pdf_naming_edit.setText(str(get("pdf", "output_naming_pattern", "{project}_{date}")))

        self.fit_combo.setCurrentText(str(get("layout", "default_fit_mode", "contain")))
        self.snap_check.setChecked(bool(get("layout", "snap_to_grid", True)))
        self.grid_spin.setValue(float(get("layout", "grid_spacing_mm", 5.0)))
        self.guides_check.setChecked(bool(get("layout", "show_alignment_guides", True)))
        self.overflow_check.setChecked(bool(get("layout", "auto_overflow", True)))
        self.rows_spin.setValue(int(get("layout", "default_rows", 2)))
        self.columns_spin.setValue(int(get("layout", "default_columns", 2)))
        self.original_size_check.setChecked(
            bool(get("layout", "place_original_size", True))
        )
        self.original_size_dpi.setValue(int(get("layout", "original_size_dpi", 300)))
        self.snap_small_cards_check.setChecked(
            bool(get("layout", "snap_small_cards", True))
        )

    def _save(self) -> None:
        for path_field in (self.project_dir_edit, self.export_dir_edit):
            text = path_field.text().strip()
            if text:
                try:
                    Path(text).mkdir(parents=True, exist_ok=True)
                except OSError:
                    QMessageBox.warning(
                        self,
                        "Invalid folder",
                        f"The folder cannot be created:\n{text}",
                    )
                    return
        allow_unsafe = False
        for path_field, label in (
            (self.project_dir_edit, "projects"),
            (self.export_dir_edit, "exports"),
        ):
            text = path_field.text().strip()
            if not text or not is_inside_app_folder(text):
                continue
            answer = QMessageBox.warning(
                self,
                "Folder inside the application folder",
                f"{label.capitalize()} would be stored in:\n{text}\n\n"
                "That folder is deleted when the application is updated or "
                "rebuilt, which loses those files.\n\nUse it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            if path_field is self.project_dir_edit:
                allow_unsafe = True
        self.settings.update_section(
            "general",
            {
                "default_project_directory": self.project_dir_edit.text().strip(),
                "default_export_directory": self.export_dir_edit.text().strip(),
                "recent_project_count": self.recent_spin.value(),
                "autosave_interval_seconds": self.autosave_spin.value(),
                "confirm_before_delete": self.confirm_delete_check.isChecked(),
                "reopen_last_project": self.reopen_check.isChecked(),
                "allow_unsafe_project_folder": allow_unsafe,
            },
        )
        extensions = [
            part.strip() for part in self.extensions_edit.text().split(",") if part.strip()
        ]
        self.settings.update_section(
            "import",
            {
                "default_category": self.default_category_edit.text().strip() or "Other",
                "thumbnail_size": self.thumbnail_spin.value(),
                "duplicate_behavior": self.duplicate_combo.currentData(),
                "supported_extensions": extensions,
            },
        )
        self.settings.update_section(
            "pdf",
            {
                "default_page_size": self.pdf_paper_combo.currentText(),
                "default_orientation": self.pdf_orientation_combo.currentText(),
                "default_margin_mm": self.pdf_margin_spin.value(),
                "default_dpi": self.pdf_dpi_combo.currentText(),
                "default_compression": self.pdf_compression_check.isChecked(),
                "output_naming_pattern": self.pdf_naming_edit.text().strip()
                or "{project}_{date}",
            },
        )
        self.settings.update_section(
            "layout",
            {
                "default_fit_mode": self.fit_combo.currentText(),
                "snap_to_grid": self.snap_check.isChecked(),
                "grid_spacing_mm": self.grid_spin.value(),
                "show_alignment_guides": self.guides_check.isChecked(),
                "auto_overflow": self.overflow_check.isChecked(),
                "default_rows": self.rows_spin.value(),
                "default_columns": self.columns_spin.value(),
                "place_original_size": self.original_size_check.isChecked(),
                "original_size_dpi": self.original_size_dpi.value(),
                "snap_small_cards": self.snap_small_cards_check.isChecked(),
            },
        )
        self.settings.replace(self.settings.data)
        if not self.settings.save():
            QMessageBox.warning(
                self,
                "Settings not saved",
                "The settings file could not be written:\n"
                f"{self.settings.path}\n\n{self.settings.last_error}\n\n"
                "Your choices are kept for this session but are lost when the "
                "application closes.",
            )
            return
        # Validation never silently swaps a typed folder for the default without
        # telling the user.
        replaced: list[str] = []
        for field_widget, label, key in (
            (self.project_dir_edit, "Projects", "default_project_directory"),
            (self.export_dir_edit, "Exports", "default_export_directory"),
        ):
            typed = field_widget.text().strip()
            stored = str(self.settings.get("general", key, ""))
            if typed and typed != stored:
                replaced.append(f"{label}: '{typed}'  ->  '{stored}'")
        if replaced:
            QMessageBox.warning(
                self,
                "Folder not usable",
                "These folders cannot be used and were replaced by the default "
                "folder:\n\n" + "\n".join(replaced),
            )
        self.accept()


