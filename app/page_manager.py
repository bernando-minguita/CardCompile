"""Page navigator: thumbnail strip with a page management context menu."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from graphics.pixmap_cache import load_pixmap, load_thumbnail_pixmap
from app.theme import ACCENT, BORDER, TEXT_MUTED

THUMB_WIDTH = 84


def _item_pixmap(document) -> QPixmap:
    """Best available picture for one placed image (thumbnail, else the file)."""
    if document is None:
        return QPixmap()
    if document.thumbnail_path:
        thumbnail = load_thumbnail_pixmap(document.thumbnail_path, max_side=160)
        if not thumbnail.isNull():
            return thumbnail
    if getattr(document, "exists", False):
        return load_pixmap(document.source_path, max_side=200)
    return QPixmap()


def _source_rect(item, pixmap: QPixmap) -> QRectF:
    """Part of *pixmap* the item shows, honouring its crop fractions."""
    width = float(pixmap.width())
    height = float(pixmap.height())
    left = max(0.0, min(0.95, item.crop_left)) * width
    top = max(0.0, min(0.95, item.crop_top)) * height
    visible_w = max(1.0, (1.0 - item.crop_left - item.crop_right) * width)
    visible_h = max(1.0, (1.0 - item.crop_top - item.crop_bottom) * height)
    return QRectF(left, top, visible_w, visible_h)


def render_page_thumbnail(page, items, documents=None, width: int = THUMB_WIDTH) -> QPixmap:
    """Draw a miniature of the page exactly as the canvas shows it.

    *documents* maps ``document_id`` -> Document.  Each placed image is drawn
    with its own **rotation** (around the frame centre, like the editor) and its
    **crop**, so a page with rotated cards no longer previews as upright cards.
    """
    height = max(24, int(width * page.height_mm / max(1.0, page.width_mm)))
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(page.background_color or "#FFFFFF"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    scale = width / max(1.0, page.width_mm)

    painter.setPen(QPen(QColor(BORDER), 1))
    painter.drawRect(0, 0, width - 1, height - 1)

    # Painter's order = z order, so overlapping cards preview like on the page.
    for item in sorted(items, key=lambda entry: (entry.z_index, entry.id)):
        rect = QRectF(
            item.position_x * scale,
            item.position_y * scale,
            max(1.0, item.width * scale),
            max(1.0, item.height * scale),
        )
        document = (documents or {}).get(item.document_id)
        picture = _item_pixmap(document)

        painter.save()
        if item.rotation:
            centre = rect.center()
            painter.translate(centre)
            painter.rotate(item.rotation)
            painter.translate(-centre)
        painter.setClipRect(rect)
        if not picture.isNull():
            painter.drawPixmap(rect, picture, _source_rect(item, picture))
        else:
            painter.setBrush(QBrush(QColor("#DCE7F8")))
            painter.setPen(QPen(QColor(ACCENT), 0.6))
            painter.drawRect(rect)
        painter.restore()
    painter.end()
    return pixmap


class PageNavigator(QWidget):
    """Lists the pages of the project and lets the user manage them."""

    page_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._editor = None
        self._items: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Pages")
        title.setObjectName("SectionTitle")
        self.count_label = QLabel("0")
        self.count_label.setObjectName("Muted")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB_WIDTH, 120))
        self.list.setSpacing(4)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.currentRowChanged.connect(self._on_row_changed)
        # Page actions live in a right-click menu instead of the button strip
        # that used to sit under the list.
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list, 1)

    # ------------------------------------------------------------------ wiring
    def attach_editor(self, editor) -> None:
        self._editor = editor
        if editor is None:
            self.list.clear()
            return
        editor.pages_changed.connect(self.rebuild)
        editor.items_changed.connect(self.update_counts)
        editor.page_changed.connect(self._highlight_current)
        self.rebuild()

    def rebuild(self) -> None:
        if self._editor is None:
            return
        pages = self._editor.pages()
        documents = self._editor.layout.documents()
        self.list.blockSignals(True)
        self.list.clear()
        self._items = []
        for page in pages:
            items = self._editor.layout.items(page.id)
            orientation = "Landscape" if page.orientation == "landscape" else "Portrait"
            count = len(items)
            label = (
                f"Page {page.page_number}\n{page.paper_size} {orientation}\n"
                f"{count} image{'s' if count != 1 else ''}"
            )
            entry = QListWidgetItem(
                QIcon(render_page_thumbnail(page, items, documents)), label
            )
            entry.setData(Qt.ItemDataRole.UserRole, page.id)
            entry.setToolTip(
                f"Page {page.page_number}: {page.paper_size} {orientation}, "
                f"{page.width_mm:.0f} x {page.height_mm:.0f} mm, {count} placed "
                "image(s) - these are the objects that will be exported"
            )
            self.list.addItem(entry)
            self._items.append(page.id)
        self.list.blockSignals(False)
        self.count_label.setText(str(len(pages)))
        self._highlight_current()

    def update_counts(self) -> None:
        self.rebuild()

    def _highlight_current(self, *_args) -> None:
        if self._editor is None:
            return
        current = self._editor.current_page_id
        if current not in self._items:
            return
        row = self._items.index(current)
        if self.list.currentRow() != row:
            self.list.blockSignals(True)
            self.list.setCurrentRow(row)
            self.list.blockSignals(False)

    def _on_row_changed(self, row: int) -> None:
        if self._editor is None or row < 0 or row >= len(self._items):
            return
        self._editor.set_current_page(self._items[row])

    # -------------------------------------------------------------- context menu
    def _build_page_menu(self) -> QMenu:
        """Right-click menu for the page list.

        Every entry calls the same editor method the *Page* menu uses, so the
        two menus cannot drift apart (and the work stays undoable).
        """
        menu = QMenu(self)
        if self._editor is None:
            return menu
        row = self.list.currentRow()
        pages = len(self._items)

        menu.addAction("Add page", self._editor.add_page)
        menu.addAction("Duplicate page", self._editor.duplicate_current_page)
        menu.addAction("Clear page", self._editor.clear_current_page)
        delete_action = menu.addAction("Delete page", self._editor.delete_current_page)
        # The editor refuses to remove the last page with a modal dialog;
        # disabling the entry avoids opening that dialog from a context menu.
        delete_action.setEnabled(pages > 1)
        menu.addSeparator()
        up_action = menu.addAction(
            "Move page up", lambda: self._editor.move_current_page(-1)
        )
        down_action = menu.addAction(
            "Move page down", lambda: self._editor.move_current_page(1)
        )
        up_action.setEnabled(row > 0)
        down_action.setEnabled(0 <= row < pages - 1)
        menu.addSeparator()
        menu.addAction(
            "Toggle orientation", self._editor.toggle_current_page_orientation
        )
        return menu

    def _show_context_menu(self, position) -> None:
        if self._editor is None:
            return
        item = self.list.itemAt(position)
        if item is not None:
            # Act on the page under the cursor, like the canvas menus do.
            self.list.setCurrentItem(item)
        menu = self._build_page_menu()
        try:
            menu.exec(self.list.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

