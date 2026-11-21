"""Document organizer: thumbnail cards, search, filtering and bulk actions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.dnd import DOCUMENT_MIME
from app.theme import DANGER, TEXT_MUTED
from core.document_service import (
    SORT_CATEGORY,
    SORT_DATE,
    SORT_IMPORTED,
    SORT_NAME,
    SORT_SIZE,
    DocumentService,
)
from graphics.pixmap_cache import load_thumbnail_pixmap
from models.document import Document

MISSING_COLOR = QColor("#B42318")
CARD_ICON = QSize(96, 96)


class DocumentListWidget(QListWidget):
    """List widget that can drag its cards onto the layout canvas."""

    order_changed = Signal()

    def dropEvent(self, event) -> None:
        if event.source() is self:
            # Reorder manually instead of calling super(): Qt's InternalMove
            # insertion plus our custom startDrag would duplicate the cards.
            self._reorder_from_drop(event)
            event.accept()
            return
        super().dropEvent(event)
        # Only a drop the list actually accepted (an external drop that the
        # base class handled) changes anything; canvas drops never arrive here.
        if event.isAccepted():
            self.order_changed.emit()

    def dragEnterEvent(self, event) -> None:
        # Accept our own drags regardless of the offered drop action: Qt's
        # InternalMove handling expects MoveAction, but startDrag offers
        # CopyAction so the canvas can receive document copies.
        if event.source() is self:
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.source() is self:
            event.accept()
            return
        super().dragMoveEvent(event)

    def _reorder_from_drop(self, event) -> None:
        """Move the selected cards to the drop position (drag to reorder)."""
        dragged = self.selectedItems()
        if not dragged:
            return
        dragged_rows = sorted(self.row(item) for item in dragged)
        target_item = self.itemAt(event.pos())
        target_row = self.count() if target_item is None else self.row(target_item)
        if (
            target_item is not None
            and self.dropIndicatorPosition()
            == QAbstractItemView.DropIndicatorPosition.BelowItem
        ):
            target_row += 1
        if dragged_rows == list(range(target_row, target_row + len(dragged_rows))):
            return  # dropped back where it was
        # Take bottom-up so the rows of the remaining items do not shift,
        # then re-insert in the original drag order.
        taken = [self.takeItem(row) for row in reversed(dragged_rows)]
        taken.reverse()
        insert_row = target_row - sum(1 for row in dragged_rows if row < target_row)
        for offset, item in enumerate(taken):
            self.insertItem(insert_row + offset, item)
        self.order_changed.emit()

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt API
        """Drag the selected documents with a mime type the canvas understands."""
        items = self.selectedItems()
        if not items:
            return
        document_ids = [
            str(item.data(Qt.ItemDataRole.UserRole))
            for item in items
            if item.data(Qt.ItemDataRole.UserRole)
        ]
        if not document_ids:
            return
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag

        mime = QMimeData()
        mime.setData(DOCUMENT_MIME, "\n".join(document_ids).encode("utf-8"))
        mime.setText(", ".join(item.text() for item in items))
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = items[0].icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(CARD_ICON))
        drag.exec(Qt.DropAction.CopyAction)


class OrganizerWidget(QWidget):
    """Image Library: every imported source image of the project.

    This panel lists *source images*.  It never shows page objects and never
    places anything by itself: the user selects images and uses an Add action.
    """

    request_import = Signal()
    request_add_to_page = Signal(list)
    request_add_to_new_pages = Signal(list)
    documents_changed = Signal()
    status_message = Signal(str)
    original_size_changed = Signal(bool)
    library_selection_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workspace = None
        self._service: DocumentService | None = None
        self._documents: list[Document] = []
        self._visible: list[Document] = []
        self._current_sort = SORT_IMPORTED
        self._sort_descending = False
        self._fallback_dpi = 300.0
        #: Selection tracked by document id, not by list row.  A card that a
        #: search or category filter hides keeps its selection, so returning to
        #: the previous filter shows it selected again.
        self._selected_ids: set[str] = set()
        self._build_ui()

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Image Library")
        title.setObjectName("SectionTitle")
        title.setToolTip(
            "Every image imported into this project. Importing an image does not "
            "put it on a page - select the ones you need and add them."
        )
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        # Counts live on their own wrapped line: a long label next to the title
        # forced the whole panel (and with it the page toolbar) much wider.
        self.count_label = QLabel("0 imported")
        self.count_label.setObjectName("Muted")
        self.count_label.setWordWrap(True)
        layout.addWidget(self.count_label)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search name, category or file type...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh_list)
        layout.addWidget(self.search_edit)

        # Filter and sort on separate rows: side by side they forced the panel
        # (and the splitter) much wider than the canvas budget allows.
        self.category_filter = QComboBox()
        self.category_filter.setToolTip("Show only one category")
        self.category_filter.currentIndexChanged.connect(self.refresh_list)
        layout.addWidget(self.category_filter)
        self.sort_combo = QComboBox()
        self.sort_combo.setToolTip(
            "Sort the Image Library:\n"
            "Import order, Filename A-Z, Filename Z-A, Category, File size "
            "(largest first), Date imported (newest first)"
        )
        self.sort_combo.addItem("Import order", (SORT_IMPORTED, False))
        self.sort_combo.addItem("Name A-Z", (SORT_NAME, False))
        self.sort_combo.addItem("Name Z-A", (SORT_NAME, True))
        self.sort_combo.addItem("Category", (SORT_CATEGORY, False))
        self.sort_combo.addItem("File size", (SORT_SIZE, True))
        self.sort_combo.addItem("Date imported", (SORT_DATE, True))
        # Keep the panel narrow: a long item must not force the whole splitter
        # wider (that used to squeeze the page toolbar).
        self.sort_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.sort_combo.setMinimumContentsLength(9)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        layout.addWidget(self.sort_combo)

        self.list = DocumentListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setIconSize(CARD_ICON)
        self.list.setGridSize(QSize(126, 160))
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setDragEnabled(True)
        # InternalMove lets the user reorder cards by dragging; drags that land
        # outside the list (the canvas) keep working via startDrag's mime data.
        # The row move itself is done manually in dropEvent because startDrag is
        # custom and never triggers Qt's internal move bookkeeping.
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        self.list.itemDoubleClicked.connect(self._on_double_clicked)
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        self.list.order_changed.connect(self._on_order_changed)
        layout.addWidget(self.list, 1)

        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setHorizontalSpacing(6)
        buttons.setVerticalSpacing(4)
        self.import_button = QPushButton("Import...")
        self.import_button.setObjectName("Primary")
        self.import_button.setToolTip("Import images or a whole folder")
        self.add_button = QPushButton("Add to page")
        self.new_pages_button = QPushButton("New pages")
        self.preview_button = QPushButton("Preview")
        self.category_button = QPushButton("Category")
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("Danger")
        buttons.addWidget(self.import_button, 0, 0)
        buttons.addWidget(self.add_button, 0, 1)
        buttons.addWidget(self.new_pages_button, 1, 0)
        buttons.addWidget(self.preview_button, 1, 1)
        buttons.addWidget(self.category_button, 2, 0)
        buttons.addWidget(self.remove_button, 2, 1)
        layout.addLayout(buttons)

        self.original_size_check = QCheckBox("Keep original size")
        self.original_size_check.setChecked(True)
        self.original_size_check.setToolTip(
            "Add images at their real physical size (pixels / image DPI) instead of "
            "stretching them to the printable area.\n"
            "Ideal for cards that were cropped out of a scan.\n"
            "Unchecking stretches new images to the printable area."
        )
        self.original_size_check.toggled.connect(self.original_size_changed.emit)
        layout.addWidget(self.original_size_check)

        self.import_button.clicked.connect(self.request_import.emit)
        self.add_button.clicked.connect(self._add_selected_to_page)
        self.new_pages_button.clicked.connect(self._add_selected_to_new_pages)
        self.preview_button.clicked.connect(self._preview_selected)
        self.category_button.clicked.connect(self._change_category_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self._update_action_states()

    # ------------------------------------------------------------------ wiring
    def set_fallback_dpi(self, dpi: float) -> None:
        """Resolution assumed for documents that carry no DPI information."""
        self._fallback_dpi = float(dpi) if dpi and dpi > 1 else 300.0

    def set_original_size(self, enabled: bool) -> None:
        """Mirror the 'place at original size' setting in the organizer."""
        if self.original_size_check.isChecked() != bool(enabled):
            self.original_size_check.blockSignals(True)
            self.original_size_check.setChecked(bool(enabled))
            self.original_size_check.blockSignals(False)

    def set_workspace(self, workspace) -> None:
        self._workspace = workspace
        self._service = DocumentService(workspace) if workspace else None
        self.refresh_categories()
        self.reload()

    def refresh_categories(self) -> None:
        current = self.category_filter.currentData()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("All categories", None)
        if self._workspace is not None:
            for category in self._workspace.document_service.categories():
                self.category_filter.addItem(category.name, category.id)
        index = self.category_filter.findData(current)
        self.category_filter.setCurrentIndex(max(0, index))
        self.category_filter.blockSignals(False)

    def reload(self) -> None:
        if self._service is None:
            self._documents = []
        else:
            self._documents = self._service.documents()
        self.refresh_list()

    def refresh_list(self) -> None:
        if self._service is None:
            self.list.clear()
            self._visible = []
            self._update_counts()
            return
        query = self.search_edit.text()
        category_id = self.category_filter.currentData()
        documents = self._service.search(
            self._documents,
            query=query,
            category_id=category_id,
            sort_by=self._current_sort,
            descending=self._sort_descending,
        )
        self._visible = documents
        # Drop ids that no longer exist (removed from the library), keep the
        # rest even when the current filter hides them.
        self._selected_ids &= {document.id for document in self._documents}
        selected = set(self._selected_ids)
        fallback_dpi = self._fallback_dpi

        self.list.blockSignals(True)
        self.list.clear()
        for document in documents:
            item = QListWidgetItem(self._card_label(document, document.id in selected))
            item.setData(Qt.ItemDataRole.UserRole, document.id)
            icon = self._card_icon(document)
            if not icon.isNull():
                item.setIcon(icon)
            item.setToolTip(self._card_tooltip(document, fallback_dpi))
            if not document.exists:
                item.setForeground(QBrush(MISSING_COLOR))
            self.list.addItem(item)
            if document.id in selected:
                item.setSelected(True)
        self.list.blockSignals(False)
        self._update_counts()
        self._on_selection_changed()

    def refresh_placement_status(self) -> None:
        """Update the placed/not-placed badges without reloading the thumbnails."""
        if self._service is None:
            return
        self._documents = self._service.documents()
        selected = set(self.selected_ids())
        by_id = {document.id: document for document in self._documents}
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            item = self.list.item(row)
            document_id = item.data(Qt.ItemDataRole.UserRole)
            document = by_id.get(document_id)
            if document is None:
                continue
            item.setText(self._card_label(document, document_id in selected))
            item.setToolTip(self._card_tooltip(document, self._fallback_dpi))
        self.list.blockSignals(False)
        self._update_counts()

    def _update_counts(self) -> None:
        imported = len(self._documents)
        # Count every placed image, including documents removed from the library
        # (their canvas objects still exist and still export).
        placed = (
            self._service.placed_count()
            if self._service is not None
            else sum(1 for document in self._documents if document.page_numbers)
        )
        selected = len(self.selected_ids())
        parts = [f"{imported} imported", f"{placed} placed"]
        if selected:
            parts.append(f"{selected} selected")
        if self._visible and len(self._visible) != imported:
            parts.append(f"{len(self._visible)} shown")
        self.count_label.setText(" | ".join(parts))
        self.count_label.setToolTip(
            f"{imported} image(s) in the library, {placed} used on a page, "
            f"{selected} selected"
        )

    @staticmethod
    def _placement_text(document: Document) -> str:
        pages = document.page_numbers
        if not pages:
            return "Not placed"
        if len(pages) == 1:
            return f"Placed - page {pages[0]}"
        shown = ", ".join(str(page) for page in pages[:3])
        if len(pages) > 3:
            shown += ", ..."
        return f"Placed - {len(pages)} pages ({shown})"

    @classmethod
    def _card_label(cls, document: Document, selected: bool = False) -> str:
        name = document.filename
        if len(name) > 24:
            name = name[:10] + "..." + name[-11:]
        category = document.category_name or "Uncategorised"
        mark = "[x] " if selected else ""
        pages = document.page_numbers
        if not pages:
            placement = "- not placed"
        elif len(pages) == 1:
            placement = f"- page {pages[0]}"
        else:
            placement = f"- {len(pages)} pages"
        missing = " | MISSING" if not document.exists else ""
        return (
            f"{mark}{name}\n{category}\n"
            f"{document.original_width}x{document.original_height} px | "
            f"{document.human_size()}\n{placement}{missing}"
        )

    @staticmethod
    def _card_tooltip(document: Document, fallback_dpi: float) -> str:
        return (
            f"{document.filename}\n"
            f"Category: {document.category_name or 'Uncategorised'}\n"
            f"{document.original_width} x {document.original_height} px | "
            f"{document.human_size()}\n"
            f"Original size: {document.describe_resolution(fallback_dpi)}\n"
            f"{OrganizerWidget._placement_text(document)}\n"
            f"{document.source_path}"
        )

    @staticmethod
    def _card_icon(document: Document) -> QIcon:
        pixmap = load_thumbnail_pixmap(document.thumbnail_path, max_side=200)
        if pixmap.isNull() and document.exists:
            from graphics.pixmap_cache import load_pixmap

            pixmap = load_pixmap(document.source_path, max_side=200)
        if pixmap.isNull():
            empty = QPixmap(CARD_ICON)
            empty.fill(QColor("#FEF3F2"))
            return QIcon(empty)
        return QIcon(
            pixmap.scaled(
                CARD_ICON,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # --------------------------------------------------------------- selection
    def selected_ids(self) -> list[str]:
        """Selected document ids, including ones hidden by a filter."""
        self._selected_ids &= {document.id for document in self._documents}
        return [document.id for document in self._documents if document.id in self._selected_ids]

    def selected_documents(self) -> list[Document]:
        ids = set(self.selected_ids())
        return [doc for doc in self._documents if doc.id in ids]

    def document_by_id(self, document_id: str) -> Document | None:
        for document in self._documents:
            if document.id == document_id:
                return document
        return None

    def select_all_filtered(self) -> None:
        """Add every image that is currently visible (search/filter applied)."""
        self.list.blockSignals(True)
        try:
            for row in range(self.list.count()):
                item = self.list.item(row)
                document_id = item.data(Qt.ItemDataRole.UserRole)
                if document_id:
                    self._selected_ids.add(str(document_id))
                item.setSelected(True)
        finally:
            self.list.blockSignals(False)
        self._on_selection_changed()

    def clear_selection(self) -> None:
        """Deselect everything, including images hidden by the current filter."""
        self._selected_ids.clear()
        self.list.blockSignals(True)
        try:
            self.list.clearSelection()
        finally:
            self.list.blockSignals(False)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        # Merge the visible selection into the id set: rows the user just
        # deselected are dropped, ids hidden by a filter stay selected.
        shown = set()
        for row in range(self.list.count()):
            document_id = self.list.item(row).data(Qt.ItemDataRole.UserRole)
            if document_id:
                shown.add(str(document_id))
        visible_selected = {
            str(item.data(Qt.ItemDataRole.UserRole))
            for item in self.list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        }
        self._selected_ids = (self._selected_ids - shown) | visible_selected
        count = len(self.selected_ids())
        # Keep the "[x]" markers on the cards in step with the selection.
        selected = set(self._selected_ids)
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            item = self.list.item(row)
            document = self.document_by_id(item.data(Qt.ItemDataRole.UserRole))
            if document is not None:
                item.setText(self._card_label(document, document.id in selected))
        self.list.blockSignals(False)
        self._update_counts()
        self._update_action_states(count)
        self.library_selection_changed.emit(count)

    def _update_action_states(self, count: int | None = None) -> None:
        if count is None:
            count = len(self.selected_ids())
        enabled = count > 0
        hint = (
            "Select the images in the Image Library first (click, Ctrl+click or "
            "Shift+click)"
        )
        for button, tooltip in (
            (self.add_button, "Place only the selected images on the active page"),
            (self.new_pages_button, "Add only the selected images, arranged on new pages"),
            (self.preview_button, "Open the selected image full size"),
            (self.category_button, "Change the category of the selected images"),
            (self.remove_button, "Remove the selected images from the library"),
        ):
            button.setEnabled(enabled)
            button.setToolTip(tooltip if enabled else hint)
        if enabled:
            self.add_button.setToolTip(
                f"Place the {count} selected image(s) on the active page"
            )
            self.new_pages_button.setToolTip(
                f"Add the {count} selected image(s), arranged on new pages"
            )

    def _on_sort_changed(self) -> None:
        data = self.sort_combo.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            self._current_sort, self._sort_descending = data[0], bool(data[1])
        else:  # pragma: no cover - defensive
            self._current_sort = data or SORT_IMPORTED
            self._sort_descending = False
        self.refresh_list()

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        document = self.document_by_id(item.data(Qt.ItemDataRole.UserRole))
        if document is not None:
            self._preview(document)

    # ------------------------------------------------------------------ actions
    def _add_selected_to_page(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        self.request_add_to_page.emit(ids)
        self.refresh_placement_status()

    def _add_selected_to_new_pages(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        self.request_add_to_new_pages.emit(ids)
        self.refresh_placement_status()

    def _preview_selected(self) -> None:
        documents = self.selected_documents()
        if documents:
            self._preview(documents[0])

    def _change_category_selected(self) -> None:
        self._change_category(self.selected_documents())

    def _remove_selected(self) -> None:
        documents = self.selected_documents()
        if not documents:
            return
        placed = [doc for doc in documents if doc.page_numbers]
        names = ", ".join(doc.filename for doc in documents[:3])
        if len(documents) > 3:
            names += f" and {len(documents) - 3} more"
        kept = ""
        if placed:
            placements = sum(len(doc.page_numbers) for doc in placed)
            pages = sorted({page for doc in placed for page in doc.page_numbers})
            kept = (
                f"\n\n{len(placed)} of these image(s) are placed "
                f"{placements} time(s) on page(s) "
                f"{', '.join(str(p) for p in pages[:8])}.\n"
                "They stay on the page, in the saved project and in the PDF - "
                "only the library entry is removed. Re-importing the same file "
                "adds the entry back without touching the placed images."
            )
        answer = QMessageBox.question(
            self,
            "Remove from Image Library",
            f"Remove {len(documents)} image(s) from the Image Library?\n\n"
            f"{names}\n\n"
            "The original image files are NOT deleted and no placed image is "
            f"removed from a page.{kept}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self._service.remove_documents([doc.id for doc in documents])
        self.reload()
        self.documents_changed.emit()
        if placed:
            self.status_message.emit(
                f"Removed {removed} image(s) from the Image Library; "
                f"{len(placed)} placed image(s) stay on their pages"
            )
        else:
            self.status_message.emit(
                f"Removed {removed} image(s) from the Image Library"
            )

    def _duplicate(self, document: Document) -> None:
        clone = self._service.duplicate_document(document.id)
        if clone is not None:
            self.reload()
            self.documents_changed.emit()
            self.status_message.emit(f"Duplicated '{document.filename}'")

    def _preview(self, document: Document) -> None:
        from app.viewer_dialog import ImageViewerDialog

        dialog = ImageViewerDialog(document, self)
        dialog.exec()

    def _change_category(self, documents: list[Document]) -> None:
        if self._workspace is None or not documents:
            return
        categories = self._workspace.document_service.categories()
        menu = QMenu(self)
        for category in categories:
            action = menu.addAction(category.name)
            action.triggered.connect(
                lambda _checked=False, cid=category.id: self._assign_category(cid)
            )
        menu.addSeparator()
        menu.addAction("Uncategorised").triggered.connect(
            lambda _checked=False: self._assign_category(None)
        )
        menu.exec(self.mapToGlobal(self.rect().center()))

    def _assign_category(self, category_id: str | None) -> None:
        ids = self.selected_ids()
        if not ids or self._service is None:
            return
        self._service.set_category(ids, category_id)
        self.reload()
        self.documents_changed.emit()
        self.status_message.emit(f"Updated the category of {len(ids)} document(s)")

    def _replace_source(self, document: Document) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose the replacement image",
            str(Path(document.source_path).parent),
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp)",
        )
        if not path:
            return
        candidate = self._service.validate_replacement(document, path)
        if candidate.width == 0:
            QMessageBox.warning(self, "Invalid image", candidate.message)
            return
        message = candidate.message
        if not candidate.hash_matches:
            message += (
                "\n\nThis file is different from the original scan. Replace it anyway?"
            )
        answer = QMessageBox.question(
            self,
            "Replace image",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._service.reconnect(candidate, update_hash=not candidate.hash_matches):
            self.reload()
            self.documents_changed.emit()
            self.status_message.emit(f"Reconnected '{document.filename}'")

    def _locate_missing(self, document: Document) -> None:
        self._replace_source(document)

    # -------------------------------------------------------------- context menu
    def _show_menu(self, position) -> None:
        item = self.list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        document = self.document_by_id(item.data(Qt.ItemDataRole.UserRole))
        if document is None:
            return

        menu = QMenu(self)
        menu.addAction(
            f"Add {len(self.selected_ids())} selected to current page",
            lambda: self._add_selected_to_page(),
        )
        menu.addAction(
            "Add selected to new pages", lambda: self._add_selected_to_new_pages()
        )
        menu.addAction("Open full size", lambda: self._preview(document))
        menu.addSeparator()
        menu.addAction("Select all shown", self.select_all_filtered)
        menu.addAction("Clear selection", self.clear_selection)
        menu.addSeparator()
        category_menu = menu.addMenu("Set category")
        if self._workspace is not None:
            for category in self._workspace.document_service.categories():
                action = category_menu.addAction(category.name)
                action.triggered.connect(
                    lambda _checked=False, cid=category.id: self._assign_category(cid)
                )
            category_menu.addSeparator()
            category_menu.addAction("Uncategorised").triggered.connect(
                lambda _checked=False: self._assign_category(None)
            )
        menu.addAction("Duplicate image", lambda: self._duplicate(document))
        menu.addAction("Replace image...", lambda: self._replace_source(document))
        if not document.exists:
            menu.addAction(
                "Locate missing file...", lambda: self._locate_missing(document)
            )
        menu.addSeparator()
        menu.addAction("Remove from Image Library", self._remove_selected)
        menu.exec(self.list.viewport().mapToGlobal(position))

    # ------------------------------------------------------------------ ordering
    def _on_order_changed(self) -> None:
        if self._service is None or self._current_sort != SORT_IMPORTED:
            return
        ordered = [
            self.list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.list.count())
        ]
        ordered = [item for item in ordered if item]
        if ordered:
            self._service.reorder(ordered)
            self.reload()
            self.documents_changed.emit()
            self.status_message.emit("Document order updated")



