"""The page canvas scene: page background, guides, snapping and item handling."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
)

from app.theme import ACCENT, BORDER, CANVAS, SUCCESS, TEXT_MUTED
from graphics.crop_overlay import CropOverlay
from graphics.image_item import PageImageItem
from graphics.pixmap_cache import load_corrected_pixmap, load_pixmap
from graphics.text_item import PageTextItem
from models.document import Document
from models.page_item import PageItem
from models.pdf_page import PdfPage

SNAP_TOLERANCE_PX = 7.0
PAGE_MARGIN = 12.0


class PageScene(QGraphicsScene):
    """Scene whose units are millimetres, one page at a time."""

    selection_changed = Signal()
    geometry_edited = Signal(str)
    context_menu_requested = Signal(str, QPoint)
    double_click_requested = Signal(str)
    label_context_requested = Signal(str, QPoint)
    label_double_clicked = Signal(str)
    crop_started = Signal(str)
    crop_finished = Signal(str)
    crop_cancelled = Signal(str)
    interaction_started = Signal(str)
    interaction_finished = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page: PdfPage | None = None
        self._items: dict[str, PageImageItem] = {}
        self._labels: dict[str, PageTextItem] = {}
        #: Documents of the page currently loaded (see ``load_items``).
        self._documents: dict[str, Document] = {}
        self._show_grid = True
        self._snap_to_grid = True
        self._grid_spacing = 5.0
        self._show_guides = True
        self._guides: list[tuple[str, float]] = []
        self._crop_item: PageImageItem | None = None
        #: Viewport update mode to restore when crop mode ends.
        self._crop_update_mode = None
        self.selectionChanged.connect(self._emit_selection_changed)

    def _emit_selection_changed(self) -> None:
        self.selection_changed.emit()

    # ------------------------------------------------------------------- setup
    @property
    def page(self) -> PdfPage | None:
        return self._page

    def set_page(self, page: PdfPage) -> None:
        self._page = page
        self.setSceneRect(
            -PAGE_MARGIN,
            -PAGE_MARGIN,
            page.width_mm + PAGE_MARGIN * 2,
            page.height_mm + PAGE_MARGIN * 2,
        )
        self.invalidate(self.sceneRect(), QGraphicsScene.SceneLayer.BackgroundLayer)

    def configure(
        self,
        show_grid: bool = True,
        snap_to_grid: bool = True,
        grid_spacing: float = 5.0,
        show_guides: bool = True,
    ) -> None:
        self._show_grid = show_grid
        self._snap_to_grid = snap_to_grid
        self._grid_spacing = max(1.0, grid_spacing)
        self._show_guides = show_guides
        self.update()

    @property
    def grid_spacing(self) -> float:
        return self._grid_spacing

    # -------------------------------------------------------------- item access
    def image_items(self) -> list[PageImageItem]:
        """Live image items, skipping any wrapper whose C++ object is gone."""
        items: list[PageImageItem] = []
        for item in self.items():
            if not isinstance(item, PageImageItem):
                continue
            try:
                item.item_id  # touching the wrapper detects a deleted C++ object
            except RuntimeError:
                continue
            items.append(item)
        return items

    def item_by_id(self, item_id: str) -> PageImageItem | None:
        return self._items.get(item_id)

    def selected_image_items(self) -> list[PageImageItem]:
        items: list[PageImageItem] = []
        for item in self.selectedItems():
            if not isinstance(item, PageImageItem):
                continue
            try:
                item.item_id
            except RuntimeError:
                continue
            items.append(item)
        return items

    def load_items(
        self,
        models: list[PageItem],
        documents: dict[str, Document],
        labels: list | None = None,
    ) -> None:
        """Rebuild the scene contents from the database models."""
        self.exit_crop_mode()
        #: Kept so ``refresh_correction`` can reload one item's pixels without a
        #: full rebuild (the tone sliders call it on every change).
        self._documents = documents
        # Rebuilding while a signal handler of an item is on the stack (for
        # example "Delete" from that item's context menu) must never destroy the
        # C++ object immediately - that crashes the whole process.  Deferring the
        # deletion with deleteLater() keeps the item alive until the event loop
        # is idle again.
        self.blockSignals(True)
        try:
            for item in self.image_items():
                self.removeItem(item)
                item.deleteLater()
            for label_item in self.label_items():
                self.removeItem(label_item)
                label_item.deleteLater()
            self._items.clear()
            self._labels.clear()
        finally:
            self.blockSignals(False)

        for model in sorted(models, key=lambda m: m.z_index):
            document = documents.get(model.document_id)
            missing = document is None or not document.exists
            pixmap = None
            prepared = False
            if document and not missing:
                if model.has_correction():
                    # Crop and correction are baked into this pixmap, so the
                    # item must paint it whole (see set_pixmap(prepared=True)).
                    pixmap = load_corrected_pixmap(
                        document.source_path,
                        model.correction_key(),
                        crop=model.crop_pixels(
                            document.original_width, document.original_height
                        )
                        if model.has_crop()
                        else None,
                    )
                    prepared = True
                else:
                    pixmap = load_pixmap(document.source_path)
            graphic = PageImageItem(
                model,
                pixmap if pixmap is not None else __import__("PySide6.QtGui", fromlist=["QPixmap"]).QPixmap(),
                document_name=document.filename if document else "Removed document",
                missing=missing,
            )
            if prepared:
                graphic.set_pixmap(graphic._pixmap, prepared=True)
            graphic.context_requested.connect(self.context_menu_requested.emit)
            graphic.double_clicked.connect(self.double_click_requested.emit)
            graphic.interaction_started.connect(self._on_interaction_started)
            graphic.geometry_changed.connect(self.geometry_edited.emit)
            graphic.interaction_finished.connect(self._on_interaction_finished)
            self.addItem(graphic)
            self._items[model.id] = graphic

        for label in labels or []:
            label_item = PageTextItem(label)
            label_item.context_requested.connect(self.label_context_requested.emit)
            label_item.double_clicked.connect(self.label_double_clicked.emit)
            label_item.interaction_started.connect(self._on_interaction_started)
            label_item.geometry_changed.connect(self.geometry_edited.emit)
            label_item.interaction_finished.connect(self._on_interaction_finished)
            self.addItem(label_item)
            self._labels[label.id] = label_item

    def label_items(self) -> list[PageTextItem]:
        items: list[PageTextItem] = []
        for item in self.items():
            if not isinstance(item, PageTextItem):
                continue
            try:
                item.item_id
            except RuntimeError:
                continue
            items.append(item)
        return items

    def selected_label_items(self) -> list[PageTextItem]:
        return [item for item in self.label_items() if item.isSelected()]

    def label_by_id(self, label_id: str) -> PageTextItem | None:
        return self._labels.get(label_id)

    def image_item(self, item_id: str) -> PageImageItem | None:
        """Live image graphic for *item_id* (``None`` when it is gone).

        The graphic holds the model the user is editing, so persistence must use
        it instead of re-reading the row from the database.
        """
        return self._items.get(item_id)

    def refresh_label(self, label) -> None:
        graphic = self._labels.get(label.id)
        if graphic is not None:
            graphic.model.text = label.text
            graphic.model.position_x = label.position_x
            graphic.model.position_y = label.position_y
            graphic.model.font_family = label.font_family
            graphic.model.font_size = label.font_size
            graphic.model.bold = label.bold
            graphic.model.italic = label.italic
            graphic.model.color = label.color
            graphic.model.z_index = label.z_index
            graphic.sync_from_model()
        self.update()

    def refresh_item(self, model: PageItem) -> None:
        graphic = self._items.get(model.id)
        if graphic is not None:
            graphic.model.position_x = model.position_x
            graphic.model.position_y = model.position_y
            graphic.model.width = model.width
            graphic.model.height = model.height
            graphic.model.rotation = model.rotation
            graphic.model.crop_left = model.crop_left
            graphic.model.crop_top = model.crop_top
            graphic.model.crop_right = model.crop_right
            graphic.model.crop_bottom = model.crop_bottom
            graphic.model.z_index = model.z_index
            graphic.model.is_locked = model.is_locked
            graphic.model.aspect_locked = model.aspect_locked
            # The correction travels with the geometry: undo restores a snapshot
            # that includes the quad and the tone values, so they have to be
            # copied here or the pixels would keep the undone correction.
            graphic.model.set_quad(model.quad())
            graphic.model.adjust_brightness = model.adjust_brightness
            graphic.model.adjust_contrast = model.adjust_contrast
            graphic.model.adjust_saturation = model.adjust_saturation
            graphic.model.adjust_sharpness = model.adjust_sharpness
            graphic.model.adjust_shadow = model.adjust_shadow
            graphic.sync_from_model()
            self.refresh_correction(model.id)
        self.update()

    def remove_item(self, item_id: str) -> None:
        graphic = self._items.pop(item_id, None)
        if graphic is not None:
            self.removeItem(graphic)

    # ------------------------------------------------------------------- paint
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QBrush(QColor(CANVAS)))
        if self._page is None:
            return
        page_rect = QRectF(0.0, 0.0, self._page.width_mm, self._page.height_mm)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 26)))
        painter.drawRect(page_rect.translated(1.6, 1.6))

        painter.setBrush(QBrush(QColor(self._page.background_color or "#FFFFFF")))
        painter.setPen(QPen(QColor(BORDER), 0.25))
        painter.drawRect(page_rect)

        if self._show_grid:
            self._paint_grid(painter, page_rect)
        if self._show_guides:
            self._paint_margins(painter, page_rect)

    def _paint_grid(self, painter: QPainter, page_rect: QRectF) -> None:
        step = self._grid_spacing
        pen = QPen(QColor(226, 232, 240), 0.12)
        pen.setCosmetic(True)
        painter.setPen(pen)
        x = step
        while x < page_rect.width():
            painter.drawLine(x, 0.0, x, page_rect.height())
            x += step
        y = step
        while y < page_rect.height():
            painter.drawLine(0.0, y, page_rect.width(), y)
            y += step

    def _paint_margins(self, painter: QPainter, page_rect: QRectF) -> None:
        if self._page is None:
            return
        x, y, width, height = self._page.printable_rect()
        pen = QPen(QColor(SUCCESS), 0.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(x, y, width, height))

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if not self._guides:
            return
        pen = QPen(QColor(ACCENT), 0.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        page_width = self._page.width_mm if self._page else rect.width()
        page_height = self._page.height_mm if self._page else rect.height()
        for orientation, position in self._guides:
            if orientation == "v":
                painter.drawLine(position, 0.0, position, page_height)
            else:
                painter.drawLine(0.0, position, page_width, position)

    # ---------------------------------------------------------------- snapping
    def _scale(self) -> float:
        if self.views():
            return max(0.05, self.views()[0].transform().m11())
        return 3.78

    def snap_move(self, item: PageImageItem, dx: float, dy: float) -> tuple[float, float]:
        """Adjust a drag delta so the item lands on grid/margin/edge guides."""
        if self._page is None:
            return dx, dy
        drag = getattr(item, "_drag", None)
        if drag is None:
            return dx, dy
        start_x, start_y, width, height = drag.rect
        proposed_x = start_x + dx
        proposed_y = start_y + dy
        tolerance = SNAP_TOLERANCE_PX / self._scale()

        x_candidates: list[float] = []
        y_candidates: list[float] = []
        left, top, printable_w, printable_h = self._page.printable_rect()
        right = left + printable_w
        bottom = top + printable_h
        # ``proposed_x``/``proposed_y`` are the item's *left*/*top* corner, so the
        # right and bottom margins have to be shifted back by the item size.
        # Using the raw margin coordinate snapped the left edge onto the right
        # margin and pushed the item off the page (a 50 mm wide card landed at
        # x=200 on a 210 mm page, 40 mm outside the paper).
        x_candidates.extend(
            [left, right - width, self._page.width_mm / 2.0 - width / 2.0]
        )
        y_candidates.extend(
            [top, bottom - height, self._page.height_mm / 2.0 - height / 2.0]
        )

        if self._snap_to_grid:
            step = self._grid_spacing
            x_candidates.append(round(proposed_x / step) * step)
            y_candidates.append(round(proposed_y / step) * step)

        for other in self.image_items():
            if other is item:
                continue
            model = other.model
            x_candidates.extend(
                [
                    model.position_x,
                    model.position_x + model.width,
                    # Align right edges with the other item as well: only the
                    # left-edge variant was offered, so two items could never be
                    # right-aligned by dragging.
                    model.position_x + model.width - width,
                    model.position_x + model.width / 2.0 - width / 2.0,
                ]
            )
            y_candidates.extend(
                [
                    model.position_y,
                    model.position_y + model.height,
                    model.position_y + model.height - height,
                    model.position_y + model.height / 2.0 - height / 2.0,
                ]
            )

        guides: list[tuple[str, float]] = []
        best_x: float | None = None
        for candidate in x_candidates:
            if abs(candidate - proposed_x) <= tolerance:
                if best_x is None or abs(candidate - proposed_x) < abs(best_x - proposed_x):
                    best_x = candidate
        if best_x is not None:
            proposed_x = best_x
            guides.append(("v", best_x))

        best_y: float | None = None
        for candidate in y_candidates:
            if abs(candidate - proposed_y) <= tolerance:
                if best_y is None or abs(candidate - proposed_y) < abs(best_y - proposed_y):
                    best_y = candidate
        if best_y is not None:
            proposed_y = best_y
            guides.append(("h", best_y))

        self._guides = guides
        self.update()
        return proposed_x - start_x, proposed_y - start_y

    def clear_guides(self) -> None:
        if self._guides:
            self._guides = []
            self.update()

    # ------------------------------------------------------------------- links
    def _on_interaction_started(self, item_id: str) -> None:
        self.clear_guides()
        self.interaction_started.emit(item_id)

    def _on_interaction_finished(self, item_id: str) -> None:
        self.clear_guides()
        self.interaction_finished.emit(item_id)

    # -------------------------------------------------------------------- crop
    def start_crop(self, item_id: str) -> bool:
        self.exit_crop_mode()
        graphic = self._items.get(item_id)
        if graphic is None or graphic.is_missing:
            return False
        overlay = CropOverlay(graphic)
        overlay.crop_finished.connect(self._on_crop_done)
        overlay.crop_cancelled.connect(self._on_crop_cancelled)
        graphic.set_crop_overlay(overlay)
        overlay.setFocus()
        self._crop_item = graphic
        # Repaint the whole viewport while cropping: the overlay changes shape on
        # every mouse move and an incremental update would leave a ghost of the
        # previous rectangle behind (the "shadow" seen while dragging).
        self._set_crop_repaint(True)
        self.crop_started.emit(item_id)
        return True

    def _set_crop_repaint(self, active: bool) -> None:
        for view in self.views():
            if not isinstance(view, QGraphicsView):  # pragma: no cover - safety
                continue
            if active:
                if self._crop_update_mode is None:
                    self._crop_update_mode = view.viewportUpdateMode()
                view.setViewportUpdateMode(
                    QGraphicsView.ViewportUpdateMode.FullViewportUpdate
                )
            elif self._crop_update_mode is not None:
                view.setViewportUpdateMode(self._crop_update_mode)
        if not active:
            self._crop_update_mode = None

    def refresh_correction(self, item_id: str) -> bool:
        """Reload one item's pixmap after its correction parameters changed.

        The pixmap cache is keyed by :meth:`PageItem.correction_key`, so the new
        parameters produce a fresh decode; the old entry simply ages out of the
        LRU instead of being mutated.  Returns ``False`` when the item or its
        source image is unavailable.
        """
        graphic = self._items.get(item_id)
        if graphic is None or graphic.is_missing:
            return False
        model = graphic.model
        document = self._documents.get(model.document_id) if self._documents else None
        if document is None:
            return False
        if model.has_correction():
            pixmap = load_corrected_pixmap(
                document.source_path,
                model.correction_key(),
                crop=model.crop_pixels(
                    document.original_width, document.original_height
                )
                if model.has_crop()
                else None,
            )
            if pixmap.isNull():
                return False
            graphic.set_pixmap(pixmap, prepared=True)
            graphic.update()
            return True
        pixmap = load_pixmap(document.source_path)
        if pixmap.isNull():
            return False
        graphic.set_pixmap(pixmap)
        graphic.update()
        return True

    def exit_crop_mode(self) -> None:
        if self._crop_item is None:
            return
        overlay = self._crop_item.crop_overlay
        if overlay is not None:
            self.removeItem(overlay)
            self._crop_item.set_crop_overlay(None)
            self._crop_item.update()
        self._crop_item = None
        self._set_crop_repaint(False)

    # ------------------------------------------------------------ perspective
    def start_perspective(self, item_id: str) -> bool:
        """Deprecated: perspective correction now lives in the Perspective Correction.

        Kept only so an older caller fails softly instead of raising
        ``AttributeError``; the editor routes every correction through
        :meth:`app.layout_editor.LayoutEditor.open_perspective_dialog`.
        """
        return False

    def _on_perspective_done(self, item_id: str) -> None:
        graphic = self._items.get(item_id)
        self.exit_crop_mode()
        # The quad changed, so the pixels must be re-warped from the source.
        self.refresh_correction(item_id)
        self.crop_finished.emit(item_id)
        if graphic is not None:
            graphic.update()

    def _on_perspective_cancelled(self, item_id: str) -> None:
        self.exit_crop_mode()
        self.crop_cancelled.emit(item_id)

    def _on_crop_done(self, item_id: str) -> None:
        graphic = self._items.get(item_id)
        self.exit_crop_mode()
        self.crop_finished.emit(item_id)
        if graphic is not None:
            graphic.update()

    def _on_crop_cancelled(self, item_id: str) -> None:
        graphic = self._items.get(item_id)
        self.exit_crop_mode()
        self.crop_cancelled.emit(item_id)
        if graphic is not None:
            graphic.update()

    @property
    def crop_item_id(self) -> str | None:
        return self._crop_item.item_id if self._crop_item is not None else None

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._crop_item is not None:
            self.exit_crop_mode()
            event.accept()
            return
        super().keyPressEvent(event)
