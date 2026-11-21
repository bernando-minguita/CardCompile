"""Interactive crop overlay drawn on top of a page image item."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

from app.theme import ACCENT, TEXT
from core.layout_service import local_to_page, position_for_local_point
from graphics.image_item import MIN_SIZE_MM, PageImageItem

HANDLE_PX = 9.0
MIN_CROP_FRACTION = 0.05


class CropOverlay(QGraphicsObject):
    """Crop rectangle with draggable edges, corners and a dimmed surround."""

    crop_changed = Signal(str)
    crop_finished = Signal(str)
    crop_cancelled = Signal(str)

    def __init__(self, parent_item: PageImageItem) -> None:
        super().__init__(parent_item)
        self._item = parent_item
        self._mode: str | None = None
        # Snapshot the crop right away so Esc can restore it even when the
        # user cancels without ever pressing the left mouse button.
        model = parent_item.model
        self._start = (
            model.crop_left,
            model.crop_top,
            model.crop_right,
            model.crop_bottom,
        )
        self._start_box = (
            model.position_x,
            model.position_y,
            model.width,
            model.height,
        )
        #: Pending crop rectangle in item coordinates (millimetres).  The drag
        #: only moves this rectangle: the item is not resized while dragging, so
        #: the view never has to erase the previous shape (that caused the
        #: "shadow" trail).  The trim is applied once, on release.
        self._frame = QRectF(0.0, 0.0, model.width, model.height)
        self._pending = QRectF(self._frame)
        self.setZValue(50)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setAcceptHoverEvents(True)

    def _apply_frame(self) -> None:
        """Trim the frame to the cropped region instead of stretching into it.

        The image is painted by stretching the visible source region into the
        item rectangle, so keeping the rectangle while removing part of the
        source squashed the picture.  Here the rectangle follows the crop at the
        same millimetres-per-source-pixel scale, anchored on the visible top-left
        corner: the content keeps its proportions and stays where it was.

        The anchor is moved in *page* space (through the item's rotation), so a
        rotated image no longer jumps sideways when it is cropped.
        """
        model = self._item.model
        width0, height0 = self._start_box[2], self._start_box[3]
        left0, top0, right0, bottom0 = self._start
        visible0_w = max(0.01, 1.0 - left0 - right0)
        visible0_h = max(0.01, 1.0 - top0 - bottom0)

        left, top, right, bottom = model.crop_box()
        visible_w = max(0.01, right - left)
        visible_h = max(0.01, bottom - top)

        width = max(MIN_SIZE_MM, width0 * visible_w / visible0_w)
        height = max(MIN_SIZE_MM, height0 * visible_h / visible0_h)
        # The frame *is* the visible region: the source point that sat at the old
        # visible corner must stay at the same page position.  In the old frame
        # it is offset by the trimmed fraction of the old visible span.
        anchor_x = (left - left0) / visible0_w * width0
        anchor_y = (top - top0) / visible0_h * height0
        target_x, target_y = local_to_page(model, anchor_x, anchor_y)

        model.width = round(width, 3)
        model.height = round(height, 3)
        x, y = position_for_local_point(
            width, height, model.rotation, 0.0, 0.0, target_x, target_y
        )
        model.position_x = round(x, 3)
        model.position_y = round(y, 3)

    # ---------------------------------------------------------------- geometry
    def _scale(self) -> float:
        scene = self.scene()
        if scene is not None and scene.views():
            return max(0.05, scene.views()[0].transform().m11())
        return 3.78

    def _handle_mm(self) -> float:
        return HANDLE_PX / self._scale()

    def _item_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._item.model.width, self._item.model.height)

    def _crop_rect(self) -> QRectF:
        """The rectangle the user is dragging (equal to the frame at first)."""
        return QRectF(self._pending)

    def pending_size_mm(self) -> tuple[float, float]:
        """Size the item will have when the pending crop is applied."""
        return self._pending.width(), self._pending.height()

    def _compose_crop(self) -> None:
        """Turn the pending rectangle into source fractions and trim the item."""
        model = self._item.model
        frame = self._frame
        if frame.width() <= 0.0 or frame.height() <= 0.0:
            return
        fx0 = (self._pending.left() - frame.left()) / frame.width()
        fx1 = (self._pending.right() - frame.left()) / frame.width()
        fy0 = (self._pending.top() - frame.top()) / frame.height()
        fy1 = (self._pending.bottom() - frame.top()) / frame.height()
        left0, top0, right0, bottom0 = self._start
        span_x = max(0.01, 1.0 - left0 - right0)
        span_y = max(0.01, 1.0 - top0 - bottom0)
        left = left0 + fx0 * span_x
        right_edge = left0 + fx1 * span_x
        top = top0 + fy0 * span_y
        bottom_edge = top0 + fy1 * span_y
        model.crop_left = round(min(max(0.0, left), 0.98), 5)
        model.crop_right = round(min(max(0.0, 1.0 - right_edge), 0.98), 5)
        model.crop_top = round(min(max(0.0, top), 0.98), 5)
        model.crop_bottom = round(min(max(0.0, 1.0 - bottom_edge), 0.98), 5)
        self._apply_frame()

    def boundingRect(self) -> QRectF:
        pad = self._handle_mm() * 2.0
        return self._frame.adjusted(-pad, -pad, pad, pad)

    # ------------------------------------------------------------------ paint
    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        full = self._frame
        crop = self._crop_rect()

        shade = QColor(15, 23, 42, 110)
        painter.fillRect(QRectF(full.left(), full.top(), full.width(), crop.top() - full.top()), shade)
        painter.fillRect(
            QRectF(full.left(), crop.bottom(), full.width(), full.bottom() - crop.bottom()),
            shade,
        )
        painter.fillRect(QRectF(full.left(), crop.top(), crop.left() - full.left(), crop.height()), shade)
        painter.fillRect(
            QRectF(crop.right(), crop.top(), full.right() - crop.right(), crop.height()),
            shade,
        )

        pen = QPen(QColor(ACCENT), 1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(crop)

        # rule of thirds
        thin = QPen(QColor(255, 255, 255, 170), 0.8)
        thin.setCosmetic(True)
        painter.setPen(thin)
        for index in (1, 2):
            x = crop.left() + crop.width() * index / 3.0
            y = crop.top() + crop.height() * index / 3.0
            painter.drawLine(x, crop.top(), x, crop.bottom())
            painter.drawLine(crop.left(), y, crop.right(), y)

        size = self._handle_mm()
        painter.setPen(QPen(QColor(ACCENT), 1.0))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        for point in self._handle_points(crop).values():
            painter.drawRect(
                QRectF(point.x() - size / 2, point.y() - size / 2, size, size)
            )

        font_size = max(2.2, min(4.0, full.height() / 14.0))
        from PySide6.QtGui import QFont

        font = QFont()
        font.setPointSizeF(font_size)
        painter.setFont(font)
        painter.setPen(QPen(QColor(TEXT)))
        painter.drawText(
            crop.adjusted(1.0, 1.0, -1.0, -1.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            "Crop: drag edges, Enter to apply, Esc to cancel",
        )

    def _handle_points(self, crop: QRectF) -> dict[str, object]:
        return {
            "tl": crop.topLeft(),
            "tr": crop.topRight(),
            "bl": crop.bottomLeft(),
            "br": crop.bottomRight(),
            "t": crop.topLeft() + (crop.topRight() - crop.topLeft()) / 2.0,
            "b": crop.bottomLeft() + (crop.bottomRight() - crop.bottomLeft()) / 2.0,
            "l": crop.topLeft() + (crop.bottomLeft() - crop.topLeft()) / 2.0,
            "r": crop.topRight() + (crop.bottomRight() - crop.topRight()) / 2.0,
        }

    # -------------------------------------------------------------- interaction
    def _hit_test(self, pos) -> str | None:
        radius = self._handle_mm() * 1.6
        for name, point in self._handle_points(self._crop_rect()).items():
            if abs(pos.x() - point.x()) <= radius and abs(pos.y() - point.y()) <= radius:
                return name
        crop = self._crop_rect()
        if crop.contains(pos):
            return "inside"
        return None

    def hoverMoveEvent(self, event) -> None:
        name = self._hit_test(event.pos())
        cursors = {
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
            "t": Qt.CursorShape.SizeVerCursor,
            "b": Qt.CursorShape.SizeVerCursor,
            "l": Qt.CursorShape.SizeHorCursor,
            "r": Qt.CursorShape.SizeHorCursor,
        }
        self.setCursor(cursors.get(name or "", Qt.CursorShape.CrossCursor))
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._mode = self._hit_test(event.pos())
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._mode is None or self._mode == "inside":
            event.ignore()
            return
        frame = self._frame
        pos = event.pos()
        x = min(max(pos.x(), frame.left()), frame.right())
        y = min(max(pos.y(), frame.top()), frame.bottom())
        min_w = max(MIN_SIZE_MM, frame.width() * MIN_CROP_FRACTION)
        min_h = max(MIN_SIZE_MM, frame.height() * MIN_CROP_FRACTION)

        pending = QRectF(self._pending)
        if self._mode in ("l", "tl", "bl"):
            pending.setLeft(min(x, pending.right() - min_w))
        if self._mode in ("r", "tr", "br"):
            pending.setRight(max(x, pending.left() + min_w))
        if self._mode in ("t", "tl", "tr"):
            pending.setTop(min(y, pending.bottom() - min_h))
        if self._mode in ("b", "bl", "br"):
            pending.setBottom(max(y, pending.top() + min_h))

        if pending != self._pending:
            self._pending = pending
            # Only the overlay repaints: the image itself is not touched while
            # dragging, so no ghost/shadow is left behind.
            self.update()
            self.crop_changed.emit(self._item.item_id)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        mode = self._mode
        self._mode = None
        if mode is not None and mode != "inside":
            self._apply_pending()
        event.accept()

    def _apply_pending(self) -> None:
        """Apply the pending rectangle (also used by Enter) and leave crop mode."""
        if self._pending != self._frame:
            self._compose_crop()
            self._item.sync_from_model()
        self.crop_finished.emit(self._item.item_id)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # The tooltip promises "Enter to apply": apply and leave crop mode.
            self._apply_pending()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            # Nothing was changed while dragging, so cancelling is free.
            self.crop_cancelled.emit(self._item.item_id)
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

