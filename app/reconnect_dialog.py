"""Reconnect missing source images by scanning a folder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.document_service import DocumentService

COL_DOCUMENT = 0
COL_CANDIDATE = 1
COL_CHECKSUM = 2
COL_DIMENSIONS = 3


class ReconnectDialog(QDialog):
    """Find replacements for missing files and re-link them after verification."""

    def __init__(self, workspace, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.service = DocumentService(workspace)
        self._candidates: list = []
        #: Candidates offered for each table row (one row per document).
        self._rows: list[list] = []
        self.reconnected = 0
        self.setWindowTitle("Reconnect missing images")
        self.setMinimumSize(760, 520)
        self._build_ui()
        self._refresh_missing()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        info = QLabel(
            "Choose the folder that contains the original scans. Files are matched "
            "by name and verified by checksum before they are reconnected. When a "
            "name appears more than once you pick the file for each document."
        )
        info.setWordWrap(True)
        info.setObjectName("Muted")
        layout.addWidget(info)

        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Folder with the original scans")
        browse = QPushButton("Browse...")
        scan = QPushButton("Scan folder")
        row.addWidget(self.folder_edit, 1)
        row.addWidget(browse)
        row.addWidget(scan)
        layout.addLayout(row)

        self.missing_label = QLabel("")
        self.missing_label.setObjectName("Muted")
        layout.addWidget(self.missing_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Document", "Candidate file", "Checksum", "Dimensions"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.allow_mismatch = QCheckBox(
            "Allow files whose checksum differs from the original (not recommended)"
        )
        layout.addWidget(self.allow_mismatch)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close
        )
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setText("Reconnect")
        apply_button.setObjectName("Primary")
        # Apply buttons live in ApplyRole and never emit accepted()/rejected();
        # connect the button directly or clicking it is a silent no-op.
        apply_button.clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        browse.clicked.connect(self._browse)
        scan.clicked.connect(self._scan)
        self.allow_mismatch.toggled.connect(self._rebuild_table)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder with the originals")
        if folder:
            self.folder_edit.setText(folder)
            self._scan()

    def _refresh_missing(self) -> None:
        missing = self.service.missing_documents()
        self.missing_label.setText(
            f"{len(missing)} document(s) currently have a missing source file."
        )

    def _scan(self) -> None:
        folder = self.folder_edit.text().strip()
        if not folder or not Path(folder).is_dir():
            QMessageBox.warning(self, "Scan folder", "Choose a valid folder first.")
            return
        self._candidates = self.service.scan_folder_for_missing(folder)
        if not self._candidates:
            QMessageBox.information(
                self,
                "Scan folder",
                "No matching files were found in that folder.\n"
                "The files must keep their original file names.",
            )
        self._rebuild_table()

    # ------------------------------------------------------------------- table
    def _visible_candidates(self, candidates: list) -> list:
        """Candidates the current filter allows, nearest match first."""
        allowed = [
            candidate
            for candidate in candidates
            if candidate.width > 0
            and candidate.height > 0
            and (candidate.hash_matches or self.allow_mismatch.isChecked())
        ]
        allowed.sort(key=lambda item: (not item.hash_matches, item.candidate_path.lower()))
        return allowed

    def _documents_with_candidates(self) -> list[tuple]:
        """One entry per missing document, with every usable candidate for it.

        A folder can hold several files with the same name (a backup copy, a
        duplicate in another sub-folder).  They used to appear as separate rows
        and every one of them was applied in turn, so the last file silently won.
        Grouping keeps one row per document and turns the duplicates into a
        choice.
        """
        grouped: dict[str, tuple] = {}
        for candidate in self._candidates:
            document = candidate.document
            if document.id not in grouped:
                grouped[document.id] = (document, [])
            grouped[document.id][1].append(candidate)

        rows: list[tuple] = []
        for document, candidates in grouped.values():
            visible = self._visible_candidates(candidates)
            if visible:
                rows.append((document, visible))
        return rows

    def _selected_candidate(self, row: int):
        candidates = self._rows[row] if 0 <= row < len(self._rows) else []
        if not candidates:
            return None
        widget = self.table.cellWidget(row, COL_CANDIDATE)
        index = widget.currentIndex() if isinstance(widget, QComboBox) else 0
        return candidates[max(0, min(index, len(candidates) - 1))]

    def _update_row(self, row: int) -> None:
        candidate = self._selected_candidate(row)
        if candidate is None:
            return
        status = "Match" if candidate.hash_matches else "Different file"
        self.table.setItem(row, COL_CHECKSUM, QTableWidgetItem(status))
        self.table.setItem(
            row,
            COL_DIMENSIONS,
            QTableWidgetItem(f"{candidate.width} x {candidate.height} px"),
        )

    def _rebuild_table(self) -> None:
        rows = self._documents_with_candidates()
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        self._rows = [candidates for _document, candidates in rows]
        for row, (document, candidates) in enumerate(rows):
            self.table.setItem(row, COL_DOCUMENT, QTableWidgetItem(document.filename))
            if len(candidates) > 1:
                combo = QComboBox()
                for candidate in candidates:
                    label = candidate.candidate_path
                    if not candidate.hash_matches:
                        label += "   (different file)"
                    combo.addItem(label)
                combo.currentIndexChanged.connect(
                    lambda _index, row=row: self._update_row(row)
                )
                self.table.setCellWidget(row, COL_CANDIDATE, combo)
            else:
                self.table.setItem(
                    row,
                    COL_CANDIDATE,
                    QTableWidgetItem(candidates[0].candidate_path),
                )
            self._update_row(row)

    # ------------------------------------------------------------------- apply
    def _apply(self) -> None:
        if not self._rows:
            QMessageBox.information(
                self, "Reconnect", "Nothing to reconnect - scan a folder first."
            )
            return
        reconnected = 0
        for row in range(len(self._rows)):
            candidate = self._selected_candidate(row)
            if candidate is None:
                continue
            # One row per document, so each document is reconnected exactly once -
            # to the file selected for it.
            if self.service.reconnect(candidate, update_hash=not candidate.hash_matches):
                reconnected += 1
        self.reconnected = reconnected
        self._refresh_missing()
        self.status_label.setText(f"Reconnected {reconnected} document(s).")
        QMessageBox.information(
            self,
            "Reconnect",
            f"{reconnected} document(s) were reconnected to their source files.",
        )
        if self.folder_edit.text().strip():
            self._candidates = self.service.scan_folder_for_missing(
                self.folder_edit.text().strip()
            )
        self._rebuild_table()
