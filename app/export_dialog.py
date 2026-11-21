"""Export dialog: output location, quality, scope, protection and validation."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from app.workers import ExportWorker
from core.logging_setup import get_logger
from core.pdf_service import ExportOptions, PdfService
from core.settings_service import SettingsService
from core.validation_service import ValidationService

LOGGER = get_logger("export_dialog")

SCOPE_ALL = "all"
SCOPE_CURRENT = "current"
SCOPE_SEPARATE = "separate"
SCOPE_CATEGORY = "category"

#: Characters Windows refuses in a file name (Unicode letters are kept).
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_pdf_file_name(name: str, fallback: str = "document") -> str:
    """Turn any user input into a safe ``*.pdf`` file name.

    Typing ``..\\..\\evil.pdf`` used to escape the chosen export folder, and
    characters such as ``?`` or ``*`` only failed later during the write. The
    name is flattened to a single component; Unicode (Arabic) text is preserved.
    """
    text = (name or "").strip()
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    text = _INVALID_NAME_CHARS.sub("_", text)
    text = text.replace("..", "_")
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or text.strip("_").lower() in _RESERVED_NAMES:
        text = fallback
    return f"{text[:120]}.pdf"


class ExportDialog(QDialog):
    """Collects the export settings and runs the export in a worker thread."""

    def __init__(
        self,
        layout_service,
        settings: SettingsService,
        current_page_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.layout = layout_service
        self.settings = settings
        self.current_page_id = current_page_id
        self.pdf_service = PdfService(layout_service)
        self.worker: ExportWorker | None = None
        self.exported_paths: list[str] = []
        self.validation_report = None

        self.setWindowTitle("Export PDF")
        self.setMinimumWidth(560)
        self._build_ui()

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(
            str(self.settings.get("general", "default_export_directory", ""))
        )
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse_button)
        form.addRow("Output folder", folder_row)

        self.name_edit = QLineEdit(self._default_name())
        form.addRow("File name", self.name_edit)

        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Entire project", SCOPE_ALL)
        self.scope_combo.addItem("Current page only", SCOPE_CURRENT)
        self.scope_combo.addItem("Each page as a separate PDF", SCOPE_SEPARATE)
        self.scope_combo.addItem("Documents of one category", SCOPE_CATEGORY)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        form.addRow("Export", self.scope_combo)

        # Explicit reminder that only page contents are exported.
        self.content_label = QLabel(self._content_summary())
        self.content_label.setObjectName("Muted")
        self.content_label.setWordWrap(True)
        form.addRow("Contents", self.content_label)

        self.category_combo = QComboBox()
        self.category_combo.addItem("All categories", None)
        for category in self.layout.workspace.document_service.categories():
            self.category_combo.addItem(category.name, category.id)
        self.category_combo.setEnabled(False)
        form.addRow("Category", self.category_combo)

        self.quality_combo = QComboBox()
        for preset in ("Draft", "Standard", "High", "Original"):
            self.quality_combo.addItem(preset)
        self.quality_combo.setCurrentText(
            str(self.settings.get("pdf", "default_dpi", "High"))
        )
        form.addRow("Quality", self.quality_combo)

        self.compress_check = QCheckBox("Compress the PDF (smaller file)")
        self.compress_check.setChecked(
            bool(self.settings.get("pdf", "default_compression", True))
        )
        form.addRow(self.compress_check)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("Optional - leave empty for no protection")
        form.addRow("Password", self.password_edit)

        self.password_confirm = QLineEdit()
        self.password_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_confirm.setPlaceholderText("Repeat the password")
        form.addRow("Confirm password", self.password_confirm)

        self.title_edit = QLineEdit()
        form.addRow("PDF title", self.title_edit)
        layout.addLayout(form)

        note = QLabel(
            "Passwords are used for this export only and are never written to the "
            "project settings."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("Ready to export.")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        footer = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("Primary")
        footer.addStretch(1)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.export_button)
        layout.addLayout(footer)

        self.cancel_button.clicked.connect(self._cancel)
        self.export_button.clicked.connect(self._start_export)

    # ------------------------------------------------------------------ helpers
    def _content_summary(self) -> str:
        """Tell the user what the PDF will contain (page objects only)."""
        try:
            counts = self.layout.library_counts()
        except Exception:  # pragma: no cover - defensive
            return "Only the images placed on pages are exported."
        used = counts["placed"]
        unused = counts["unused"]
        text = (
            f"{counts['imported']} imported | {used} used in the PDF | "
            f"{unused} not included"
        )
        if unused:
            text += " (library images that are not on any page are ignored)"
        return text

    def _default_name(self) -> str:
        pattern = str(
            self.settings.get("pdf", "output_naming_pattern", "{project}_{date}")
        )
        project_name = self.layout.workspace.project.name
        safe = "".join(
            ch if ch.isalnum() or ch in (" ", "-", "_") else "_" for ch in project_name
        ).strip()
        try:
            name = pattern.format(
                project=safe or "document", date=datetime.now().strftime("%Y-%m-%d")
            )
        except (KeyError, ValueError):
            name = f"{safe}_{datetime.now().strftime('%Y-%m-%d')}"
        return safe_pdf_file_name(name)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the export folder", self.folder_edit.text()
        )
        if folder:
            self.folder_edit.setText(folder)

    def _on_scope_changed(self) -> None:
        if hasattr(self, "category_combo"):
            self.category_combo.setEnabled(
                self.scope_combo.currentData() == SCOPE_CATEGORY
            )

    def _pages(self) -> list[str]:
        pages = self.layout.pages()
        if self.scope_combo.currentData() == SCOPE_CURRENT and self.current_page_id:
            return [self.current_page_id]
        return [page.id for page in pages]

    def _start_export(self) -> None:
        if not self.layout.pages():
            QMessageBox.warning(self, "Export", "The project has no pages yet.")
            return
        scope = self.scope_combo.currentData()
        if scope == SCOPE_CATEGORY and self.category_combo.currentData() is None:
            # A falsy category id used to disable the filter, so this scope
            # silently exported the entire project.
            QMessageBox.warning(
                self,
                "Export",
                "Choose a category to export, or change the scope to "
                "\"Entire project\".\n\n"
                "\"All categories\" is not a category: with this scope it would "
                "export every page of the project.",
            )
            return
        folder_text = self.folder_edit.text().strip()
        if not folder_text:
            QMessageBox.warning(self, "Export", "Please choose an output folder.")
            return
        folder = Path(folder_text)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Export", f"Cannot use this folder:\n{exc}")
            return

        password = self.password_edit.text()
        if password != self.password_confirm.text():
            QMessageBox.warning(self, "Export", "The two passwords do not match.")
            return

        name = safe_pdf_file_name(self.name_edit.text())
        if scope == SCOPE_SEPARATE:
            # One file per page: the target is the *folder*, so there is no single
            # file name to validate.  Comparing folder.parent with folder here
            # rejected every normal folder and made this scope unusable.
            target = folder
        else:
            target = folder / name
            try:
                if target.resolve().parent != folder.resolve():
                    raise ValueError(target)
            except (OSError, ValueError):
                QMessageBox.warning(
                    self, "Export", "That file name cannot be used for this folder."
                )
                return
        if scope != SCOPE_SEPARATE and target.exists():
            answer = QMessageBox.question(
                self,
                "Overwrite file",
                f"The file already exists:\n{target}\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        elif scope == SCOPE_SEPARATE:
            # Separate exports write one file per page - warn when the folder
            # already contains files of this base name.  The match is done on
            # the literal name, not with ``glob``: a project called
            # "Report [final]" produced the pattern "Report [final]_page_*.pdf",
            # where the brackets open a character class, so the check found
            # nothing and the previous export was overwritten without asking.
            base = name[:-4] if name.lower().endswith(".pdf") else name
            prefix = f"{base}_page_"
            existing = sorted(
                path
                for path in (folder.iterdir() if folder.is_dir() else ())
                if path.name.startswith(prefix)
                and path.name.endswith(".pdf")
                and path.name[len(prefix) : -4].isdigit()
            )
            if existing:
                answer = QMessageBox.question(
                    self,
                    "Overwrite files",
                    f"{len(existing)} exported page file(s) already exist in:\n"
                    f"{folder}\n\nOverwrite them?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

        report = ValidationService().validate_project(self.layout)
        if report.errors:
            QMessageBox.critical(
                self,
                "Cannot export yet",
                "The project has problems that must be fixed first:\n\n"
                + "\n".join(issue.message for issue in report.errors[:8]),
            )
            return

        page_ids = self._pages()
        options = ExportOptions(
            quality=self.quality_combo.currentText(),
            compress=self.compress_check.isChecked(),
            user_password=password,
            owner_password=password,
            metadata={
                "title": self.title_edit.text().strip()
                or self.layout.workspace.project.name
            },
        )
        self.progress.setVisible(True)
        self.progress.setRange(0, max(1, len(page_ids)))
        self.progress.setValue(0)
        self.export_button.setEnabled(False)
        self.status_label.setText("Rendering pages...")

        self.worker = ExportWorker(
            self.pdf_service,
            page_ids,
            str(target),
            options,
            separate=scope == SCOPE_SEPARATE,
            base_name=Path(name).stem,
            category_id=(
                self.category_combo.currentData() if scope == SCOPE_CATEGORY else None
            ),
            parent=self,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.completed.connect(self._on_export_done)
        self.worker.cancelled.connect(self._on_export_cancelled)
        self.worker.failed.connect(self._on_export_failed)
        self.worker.start()

    def _on_progress(self, index: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(index + 1)
        self.status_label.setText(f"Rendering page {index + 1} of {total}...")

    def _on_export_cancelled(self, result) -> None:
        """A cancelled export is not a failure: report the files already written."""
        self.progress.setVisible(False)
        self.export_button.setEnabled(True)
        paths = list(getattr(result, "output_paths", None) or [])
        self.exported_paths = paths
        self.status_label.setText("Export cancelled.")
        if paths:
            QMessageBox.information(
                self,
                "Export cancelled",
                f"The export was cancelled. {len(paths)} file(s) had already been "
                "written and were kept:\n\n" + "\n".join(paths[:10]),
            )
        else:
            QMessageBox.information(
                self,
                "Export cancelled",
                "The export was cancelled. No file was written.",
            )

    def _on_export_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.export_button.setEnabled(True)
        self.status_label.setText("Export failed.")
        QMessageBox.critical(self, "Export failed", message)

    def _on_export_done(self, result) -> None:
        self.progress.setVisible(False)
        self.export_button.setEnabled(True)
        self.exported_paths = list(result.output_paths)
        if not self.exported_paths:
            self.status_label.setText("Nothing was exported.")
            return

        validator = ValidationService()
        exported_ids = self._pages()
        by_id = {page.id: page for page in self.layout.pages()}
        # The sizes must follow the pages that were really exported - using the
        # first pages of the project reported "Page 1 has the wrong size" as soon
        # as the current page (or a later page) had different paper.
        expected_sizes = [
            (by_id[page_id].width_mm, by_id[page_id].height_mm)
            for page_id in exported_ids
            if page_id in by_id
        ]
        reports = []
        if self.scope_combo.currentData() == SCOPE_SEPARATE:
            for index, path in enumerate(self.exported_paths):
                reports.append(
                    validator.validate_pdf(
                        path,
                        expected_pages=1,
                        expected_sizes_mm=expected_sizes[index : index + 1],
                    )
                )
        elif self.scope_combo.currentData() == SCOPE_CATEGORY:
            reports.append(validator.validate_pdf(self.exported_paths[0]))
        else:
            reports.append(
                validator.validate_pdf(
                    self.exported_paths[0],
                    expected_pages=len(exported_ids),
                    expected_sizes_mm=expected_sizes,
                )
            )
        self.validation_report = reports[0] if reports else None

        # A separate export writes one page per file, so the history must not
        # claim the whole project's page count for every entry.  A category
        # export skips pages without matching documents, so the page ids are not
        # the page count either - ``result.page_count`` is the real one.
        if self.scope_combo.currentData() == SCOPE_SEPARATE:
            pages_per_file = 1
        else:
            pages_per_file = getattr(result, "page_count", 0) or len(exported_ids)
        for path in self.exported_paths:
            self.pdf_service.record_export(
                path, pages_per_file, self.quality_combo.currentText()
            )

        messages = [
            f"{issue.severity.upper()}: {issue.message}"
            for report in reports
            for issue in report.issues
        ]
        if result.skipped_items:
            messages.append(
                f"WARNING: {len(result.skipped_items)} image(s) were skipped because "
                "their source file is missing."
            )
        if messages:
            self.status_label.setText("Export finished with warnings.")
            QMessageBox.warning(
                self,
                "Export finished with warnings",
                "The PDF was created but validation reported:\n\n"
                + "\n".join(messages[:10]),
            )
        else:
            self.status_label.setText("Export completed and validated.")
        self.accept()

    def _cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        if self.worker is not None and self.worker.isRunning():
            # Keep the dialog (and its thread) alive instead of destroying a
            # running QThread, which aborts the whole application.
            self.status_label.setText("Cancelling... please wait.")
            self.cancel_button.setEnabled(False)
            return
        self.reject()

    def reject(self) -> None:  # noqa: N802 - Qt API
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        if self.worker is not None and self.worker.isRunning():
            self.status_label.setText("Cancelling... please wait.")
            self.cancel_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)


def open_path(path: str) -> None:
    """Open a file with the Windows default application."""
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - non Windows fallback
        QMessageBox.information(
            None, "Open file", f"Please open the file manually:\n{path}"
        )



