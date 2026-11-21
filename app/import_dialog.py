"""Import dialog: file/folder selection, background analysis and progress."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QItemSelectionModel, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
)

from app.theme import DANGER, SUCCESS
from app.workers import ImportWorker
from core.import_service import (
    STATUS_DUPLICATE,
    STATUS_INVALID,
    STATUS_READY,
    ImportPreview,
    ImportService,
)
from core.logging_setup import get_logger
from core.settings_service import SettingsService

LOGGER = get_logger("import_dialog")

COLUMNS = (
    "Preview",
    "File name",
    "Dimensions",
    "Physical size",
    "Size",
    "Format",
    "Category",
    "Status",
)


class FolderSelectionDialog(QDialog):
    """Selects one or more folders at once.

    ``QFileDialog`` in ``Directory`` mode only ever returns the single folder
    that is current when its OK button is pressed, no matter which selection
    mode its views use - so "Add several folders..." could only ever add one.
    A plain directory tree with extended selection returns every highlighted
    folder reliably.
    """

    def __init__(self, parent=None, start_dir: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select one or more folders")
        self.setMinimumSize(720, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel(
            "Ctrl+click (or Shift+click) to select several folders. "
            "Double-click a folder to open it."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.model = QFileSystemModel(self)
        self.model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives
        )
        self.model.setRootPath("")

        self.tree = QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tree.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        for column in range(1, self.model.columnCount()):
            self.tree.setColumnHidden(column, True)
        layout.addWidget(self.tree, 1)

        start = Path(start_dir) if start_dir else Path.home()
        if start.exists():
            index = self.model.index(str(start))
            if index.isValid():
                # Show the folder without selecting it: the dialog opens with an
                # empty selection so only the folders the user picks are added.
                self.tree.selectionModel().setCurrentIndex(
                    index, QItemSelectionModel.SelectionFlag.NoUpdate
                )
                self.tree.expand(index)
                self.tree.scrollTo(index)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.add_button = QPushButton("Add selected folders")
        self.add_button.setObjectName("Primary")
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.add_button)
        layout.addLayout(footer)

        self.cancel_button.clicked.connect(self.reject)
        self.add_button.clicked.connect(self.accept)
        self.tree.selectionModel().selectionChanged.connect(self._sync_button)
        self._sync_button()

    def _sync_button(self) -> None:
        count = len(self.selected_folders())
        self.add_button.setEnabled(count > 0)
        self.add_button.setText(
            "Add selected folders" if count != 1 else "Add selected folder"
        )

    def selected_folders(self) -> list[str]:
        selection = self.tree.selectionModel()
        if selection is None:
            return []
        folders: list[str] = []
        for index in selection.selectedRows(0):
            path = self.model.filePath(index)
            if path and path not in folders:
                folders.append(path)
        return folders


class ImportDialog(QDialog):
    """Selects image files/folders, validates them and imports documents."""

    imported = Signal(list)

    def __init__(self, workspace, settings: SettingsService, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.settings = settings
        self.service = ImportService(workspace)
        self._worker: ImportWorker | None = None
        self._previews: list[ImportPreview] = []
        self._paths: list[str] = []

        self.setWindowTitle("Import scanned documents")
        self.setMinimumSize(940, 620)
        self.setAcceptDrops(True)
        self._build_ui()

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel(
            "Add scanned documents (JPG, PNG, TIFF, BMP, WEBP). "
            "The original files stay where they are - nothing is modified."
        )
        header.setObjectName("Muted")
        header.setWordWrap(True)
        layout.addWidget(header)

        buttons = QHBoxLayout()
        self.files_button = QPushButton("Add files...")
        self.folder_button = QPushButton("Add folder...")
        self.folders_button = QPushButton("Add several folders...")
        self.clear_button = QPushButton("Clear list")
        buttons.addWidget(self.files_button)
        buttons.addWidget(self.folder_button)
        buttons.addWidget(self.folders_button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        layout.addLayout(buttons)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setIconSize(QSize(48, 48))
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header_view.resizeSection(0, 58)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, len(COLUMNS)):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        options = QHBoxLayout()
        options.addWidget(QLabel("Category"))
        self.category_combo = QComboBox()
        for category in workspace_categories(self.workspace):
            self.category_combo.addItem(category.name, category.id)
        options.addWidget(self.category_combo)
        options.addSpacing(16)
        options.addWidget(QLabel("Duplicates"))
        self.duplicate_combo = QComboBox()
        self.duplicate_combo.addItem("Ask / import anyway", "ask")
        self.duplicate_combo.addItem("Skip duplicates", "skip")
        self.duplicate_combo.addItem("Always import", "import")
        default_policy = str(self.settings.get("import", "duplicate_behavior", "ask"))
        index = self.duplicate_combo.findData(default_policy)
        self.duplicate_combo.setCurrentIndex(max(0, index))
        options.addWidget(self.duplicate_combo)
        options.addStretch(1)
        layout.addLayout(options)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("No files selected yet. Drag files here or use the buttons.")
        self.status_label.setObjectName("Muted")
        layout.addWidget(self.status_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.import_button = QPushButton("Import")
        self.import_button.setObjectName("Primary")
        self.import_button.setEnabled(False)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.import_button)
        layout.addLayout(footer)

        self.files_button.clicked.connect(self._pick_files)
        self.folder_button.clicked.connect(self._pick_folder)
        self.folders_button.clicked.connect(self._pick_folders)
        self.clear_button.clicked.connect(self._clear)
        self.cancel_button.clicked.connect(self._cancel)
        self.import_button.clicked.connect(self._commit)
        self.duplicate_combo.currentIndexChanged.connect(self._on_policy_changed)

    # ------------------------------------------------------------------ picking
    def _patterns(self) -> str:
        extensions = self.settings.supported_extensions()
        joined = " ".join(f"*{ext}" for ext in extensions)
        return f"Images ({joined})"

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select scanned documents", str(Path.home()), self._patterns()
        )
        if paths:
            self.add_paths(paths)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select a folder with scanned documents", str(Path.home())
        )
        if folder:
            self.add_paths([folder])

    def _pick_folders(self) -> None:
        dialog = FolderSelectionDialog(self, str(Path.home()))
        if dialog.exec():
            folders = dialog.selected_folders()
            if folders:
                self.add_paths(folders)

    def add_paths(self, paths: list[str]) -> None:
        added = False
        for path in paths:
            if path not in self._paths:
                self._paths.append(path)
                added = True
        if added:
            self._start_analysis()

    def _clear(self) -> None:
        self._paths = []
        self._previews = []
        self.table.setRowCount(0)
        self.import_button.setEnabled(False)
        self.status_label.setText("No files selected yet.")

    # ----------------------------------------------------------------- analysis
    def _start_analysis(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            # A new run was requested (files added / policy changed) while the
            # previous analysis is still going: stop it instead of silently
            # ignoring the request.
            self._worker.cancel()
            self._worker.wait(2000)
            if self._worker.isRunning():
                self.status_label.setText(
                    "The previous analysis is still stopping - try again in a moment."
                )
                return
        if self._worker is not None:
            # A previous worker may have finished right as the new run starts;
            # disconnect it so its queued signals cannot clobber the new results.
            for signal, slot in (
                (self._worker.progress, self._on_progress),
                (self._worker.completed, self._on_analysis_done),
                (self._worker.cancelled, self._on_analysis_cancelled),
                (self._worker.failed, self._on_analysis_failed),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        extensions = self.settings.supported_extensions()
        hashes = self.service.existing_hashes()
        policy = self.duplicate_combo.currentData() or "ask"
        self._worker = ImportWorker(
            self.service, self._paths, extensions, policy, hashes, self
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_analysis_done)
        self._worker.cancelled.connect(self._on_analysis_cancelled)
        self._worker.failed.connect(self._on_analysis_failed)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.import_button.setEnabled(False)
        self.status_label.setText("Analysing files...")
        self._worker.start()

    def _on_progress(self, index: int, total: int, name: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(index)
        self.status_label.setText(f"Analysing {index} of {total}: {name}")

    def _on_analysis_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.status_label.setText(f"Import analysis failed: {message}")
        self.import_button.setEnabled(False)

    def _on_analysis_cancelled(self) -> None:
        """Called when the analysis thread is interrupted by the user.

        Clears any partial results that may have accumulated during the loop.
        The import button stays disabled — the user must start a fresh analysis.
        """
        self._previews = []
        self.table.setRowCount(0)
        self.progress.setVisible(False)
        self.import_button.setEnabled(False)
        self.status_label.setText("Analysis cancelled. Please start a new import.")

    def _on_analysis_done(self, previews: list) -> None:
        self._previews = list(previews)
        self.progress.setVisible(False)
        self._fill_table()
        valid = sum(1 for p in previews if p.status != STATUS_INVALID)
        duplicates = sum(1 for p in previews if p.status == STATUS_DUPLICATE)
        invalid = sum(1 for p in previews if p.status == STATUS_INVALID)
        self.status_label.setText(
            f"{len(previews)} file(s): {valid} ready, {duplicates} duplicate(s), "
            f"{invalid} invalid."
        )
        self.import_button.setEnabled(valid > 0)

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self._previews))
        selected_category = self.category_combo.currentText() or "-"
        fallback_dpi = float(self.settings.get("layout", "original_size_dpi", 300))
        for row, preview in enumerate(self._previews):
            thumb = QTableWidgetItem()
            if preview.thumbnail_png:
                pixmap = QPixmap()
                pixmap.loadFromData(preview.thumbnail_png, "PNG")
                if not pixmap.isNull():
                    thumb.setIcon(
                        QIcon(
                            pixmap.scaled(
                                48,
                                48,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        )
                    )
            self.table.setItem(row, 0, thumb)
            self.table.setItem(row, 1, QTableWidgetItem(preview.filename))
            self.table.setItem(row, 2, QTableWidgetItem(preview.dimensions))
            width_mm, height_mm = preview.natural_size_mm(fallback_dpi)
            physical = (
                f"{width_mm:.1f} x {height_mm:.1f} mm"
                if width_mm > 0
                else "-"
            )
            physical_item = QTableWidgetItem(physical)
            physical_item.setToolTip(
                f"{preview.dpi_x:.0f} dpi from the file"
                if preview.dpi_x > 1.0
                else f"No DPI in the file - assuming {fallback_dpi:.0f} dpi"
            )
            self.table.setItem(row, 3, physical_item)
            self.table.setItem(row, 4, QTableWidgetItem(preview.human_size()))
            self.table.setItem(row, 5, QTableWidgetItem(preview.image_format))
            self.table.setItem(row, 6, QTableWidgetItem(selected_category))
            status_item = QTableWidgetItem(self._status_text(preview))
            if preview.status == STATUS_INVALID:
                status_item.setForeground(QColor(DANGER))
            elif preview.status == STATUS_DUPLICATE:
                status_item.setForeground(QColor("#B54708"))
            elif preview.status == STATUS_READY:
                status_item.setForeground(QColor(SUCCESS))
            status_item.setToolTip(preview.message)
            self.table.setItem(row, 7, status_item)

    @staticmethod
    def _status_text(preview: ImportPreview) -> str:
        if preview.status == STATUS_INVALID:
            return "Invalid"
        if preview.status == STATUS_DUPLICATE:
            return "Duplicate"
        return "Ready"

    def _on_policy_changed(self) -> None:
        if self._paths:
            self._start_analysis()

    # -------------------------------------------------------------------- commit
    def _commit(self) -> None:
        if not self._previews:
            return
        category_id = self.category_combo.currentData()
        policy = self.duplicate_combo.currentData() or "ask"
        duplicates = [
            preview
            for preview in self._previews
            if preview.status == STATUS_DUPLICATE
        ]
        if policy == "ask" and duplicates:
            answer = QMessageBox.question(
                self,
                "Duplicate files",
                f"{len(duplicates)} file(s) are already in this project.\n\n"
                "Import them anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                policy = "skip"
        thumbnail_size = int(self.settings.get("import", "thumbnail_size", 160))
        try:
            summary = self.service.commit(
                self._previews,
                category_id,
                thumbnail_size=thumbnail_size,
                include_duplicates=policy != "skip",
            )
        except Exception as exc:  # deleted source file, database error, ...
            LOGGER.exception("Import failed")
            QMessageBox.critical(
                self,
                "Import failed",
                f"The files could not be imported:\n{exc}\n\n"
                "Nothing was added to the project.",
            )
            self.status_label.setText("Import failed.")
            return
        self.imported.emit(summary.imported_ids)
        if summary.imported_count == 0:
            self.status_label.setText(
                "Nothing was imported. Check the duplicate policy and the file list."
            )
            return
        # Concise, explicit result so the user knows nothing was placed yet.
        lines = [
            "Import completed",
            "",
            f"- {summary.imported_count} image(s) imported",
            f"- {summary.duplicates} duplicate(s) skipped",
            f"- {summary.invalid} unsupported or unreadable file(s) skipped",
            "- 0 errors",
            "",
            f"All {summary.imported_count} imported image(s) are now in the Image "
            "Library. Nothing has been added to the PDF pages yet - select the "
            "images you need and use 'Add to page'.",
        ]
        LOGGER.info(
            "Import completed: %s imported, %s duplicates, %s invalid, %s skipped",
            summary.imported_count,
            summary.duplicates,
            summary.invalid,
            summary.skipped,
        )
        QMessageBox.information(self, "Import completed", "\n".join(lines))
        self.accept()

    def _cancel(self) -> None:
        """Stop the analysis thread before closing the dialog.

        ``wait()`` can time out (a huge TIFF is still being hashed); the worker
        must then be kept alive, otherwise closing the dialog destroys a running
        QThread and the application aborts on shutdown.
        """
        self._stop_worker()
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("Cancelling... please wait.")
            self.cancel_button.setEnabled(False)
            return
        self.reject()

    def _stop_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)

    def reject(self) -> None:  # noqa: N802 - Qt API
        self._stop_worker()
        if self._worker is not None and self._worker.isRunning():
            # Do not let Qt destroy the dialog while its thread still runs.
            self.status_label.setText("Cancelling... please wait.")
            self.cancel_button.setEnabled(False)
            return
        super().reject()

    # -------------------------------------------------------------- drag & drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths: list[str] = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        self._stop_worker()
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("Cancelling... please wait.")
            event.ignore()
            return
        super().closeEvent(event)




def workspace_categories(workspace):
    return workspace.database.categories.list_for_project(workspace.id)
