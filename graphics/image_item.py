"""Interactive canvas item for one placed document image.

Scene units are millimetres (1 unit == 1 mm).  The item owns a
:class:`models.page_item.PageItem` and edits it in place; the editor decides
when to push an undo command.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

from app.theme import ACCENT, BORDER_STRONG, DANGER, TEXT_MUTED, WARNING
from models.page_item import PageItem

HANDLE_PX = 10.0
ROTATE_OFFSET_PX = 26.0
MIN_SIZE_MM = 5.0
HANDLES = ("tl", "t", "tr", "r", "br", "b", "bl", "l")
CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor,
    "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor,
    "bl": Qt.CursorShape.SizeBDiagCursor,
    "t": Qt.CursorShape.SizeVerCursor,
    "b": Qt.CursorShape.SizeVerCursor,
    "l": Qt.CursorShape.SizeHorCursor,
    "r": Qt.CursorShape.SizeHorCursor,
    "rotate": Qt.CursorShape.CrossCursor,
}

MODE_IDLE = "idle"
MODE_MOVE = "move"
MODE_RESIZE = "resize"
MODE_ROTATE = "rotate"


@dataclass(slots=True)
class DragStart:
    rect: tuple[float, float, float, float]
    rotation: float
    handle: str
    scene_pos: QPointF
    positions: dict[str, tuple[float, float]]


class PageImageItem(QGraphicsObject):
    """One image on the page canvas with move/resize/rotate handles."""

    interaction_started = Signal(str)
    interaction_finished = Signal(str)
    geometry_changed = Signal(str)
    context_requested = Signal(str, QPoint)
    double_clicked = Signal(str)

    def __init__(
        self,
        model: PageItem,
        pixmap: QPixmap,
        *,
        document_name: str = "",
        missing: bool = False,
    ) -> None:
        super().__init__()
        self._model = model
        self._pixmap = pixmap
        self._document_name = document_name
        self._missing = missing
        self._mode = MODE_IDLE
        self._drag: DragStart | None = None
        self._hover_handle: str | None = None
        self._crop_overlay = None
        #: True when ``_pixmap`` already contains the item's crop and correction,
        #: so painting must not crop it a second time.
        self._pixmap_is_prepared = False
        #: Single-entry cache for :meth:`_pixmap_for_paint`; keyed by the source
        #: rectangle and the on-screen size so a zoom or crop invalidates it.
        self._scaled_cache: tuple[tuple, QPixmap] | None = None


        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setPos(model.position_x, model.position_y)
        self.setRotation(model.rotation)
        self.setZValue(model.z_index)
        self.setTransformOriginPoint(model.width / 2.0, model.height / 2.0)
        self.setToolTip(
            f"{document_name}\n{model.width:.1f} x {model.height:.1f} mm"
            if document_name
            else f"{model.width:.1f} x {model.height:.1f} mm"
        )

    # ------------------------------------------------------------------ model
    @property
    def model(self) -> PageItem:
        return self._model

    @property
    def item_id(self) -> str:
        return self._model.id

    @property
    def is_missing(self) -> bool:
        return self._missing

    def set_missing(self, missing: bool) -> None:
        self._missing = missing
        self.update()

    def set_pixmap(self, pixmap: QPixmap, *, prepared: bool = False) -> None:
        """Replace the pixmap.

        ``prepared=True`` means the pixmap already has the item's crop (and any
        correction) baked in, so :meth:`_source_rect` must paint all of it
        instead of cropping a second time.
        """
        self._pixmap = pixmap
        self._pixmap_is_prepared = prepared
        self._scaled_cache = None
        self.update()

    def sync_from_model(self) -> None:
        """Refresh the visual state after the model was changed elsewhere."""
        self.prepareGeometryChange()
        self.setTransformOriginPoint(self._model.width / 2.0, self._model.height / 2.0)
        self.setPos(self._model.position_x, self._model.position_y)
        self.setRotation(self._model.rotation)
        self.setZValue(self._model.z_index)
        self.update()

    def set_crop_overlay(self, overlay) -> None:
        self._crop_overlay = overlay

    @property
    def crop_overlay(self):
        return self._crop_overlay

    # --------------------------------------------------------------- geometry
    def _rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._model.width, self._model.height)

    def _pixmap_for_paint(self, painter: QPainter, source: QRectF, target: QRectF) -> QPixmap:
        """Return a pixmap pre-scaled to the on-screen size with area averaging.

        ``drawPixmap`` with ``SmoothPixmapTransform`` resamples bilinearly, which
        *undersamples* on large minification: a fine pattern collapses into
        moire instead of averaging to its true tone.  ``scaled(...,
        SmoothTransformation)`` area-averages (measured: identical to a box
        filter), so pre-scaling here removes that aliasing.  The result is
        cached for one source/size pair because ``paint`` runs on every repaint.

        A pixmap that is not being minified at all is drawn directly: enlarging
        it here would only blur it, and Qt's smooth transform is the better
        upscaler.
        """
        if source.width() <= 0.0 or source.height() <= 0.0:
            return self._pixmap
        # The painter's world transform is millimetres -> device pixels, so it
        # already includes both the zoom level and the device pixel ratio.  Use
        # the length of the first basis vector: unlike m11() alone this stays
        # correct when the item itself is rotated (m11 becomes scale*cos).
        transform = painter.worldTransform()
        scale = math.hypot(transform.m11(), transform.m12())
        if scale <= 0.0:
            return self._pixmap
        target_w = max(1, int(round(target.width() * scale)))
        target_h = max(1, int(round(target.height() * scale)))
        # Only skip when nothing is being minified.  Using ``or`` here would
        # disable the fix whenever a single axis happened to be enlarged, which
        # is exactly the common case of an image whose aspect differs from the
        # placed rectangle.
        if target_w >= source.width() and target_h >= source.height():
            return self._pixmap

        key = (
            round(source.x(), 3),
            round(source.y(), 3),
            round(source.width(), 3),
            round(source.height(), 3),
            target_w,
            target_h,
        )
        cached = self._scaled_cache
        if cached is not None and cached[0] == key:
            return cached[1]

        cropped = self._pixmap.copy(
            QRect(
                int(source.x()),
                int(source.y()),
                max(1, int(source.width())),
                max(1, int(source.height())),
            )
        )
        scaled = cropped.scaled(
            target_w,
            target_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._scaled_cache = (key, scaled)
        return scaled

    def _scale(self) -> float:
        """Current pixels-per-millimetre of the view (for constant UI sizes)."""
        scene = self.scene()
        if scene is not None and scene.views():
            return max(0.05, scene.views()[0].transform().m11())
        return 3.78

    def _handle_mm(self) -> float:
        """Handle size in millimetres, never a large share of a small card.

        ``HANDLE_PX / scale`` is correct at normal zoom, but at 100 % the view
        maps one millimetre to one pixel, so an 8 px handle became an 8 mm one
        with a 17 mm hit radius - wider than the half of a 19 mm tall card, which
        made a click in the middle grab the top/bottom handle and resize the
        image instead of moving it.
        """
        size = HANDLE_PX / self._scale()
        smallest_side = max(1.0, min(self._model.width, self._model.height))
        return min(size, smallest_side * 0.15)

    def boundingRect(self) -> QRectF:
        pad = self._handle_mm() * 2.5
        rect = self._rect().adjusted(-pad, -pad, pad, pad)
        return rect

    def shape(self):
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addRect(self._rect())
        # The rotation handle sits *above* the image, so it must be part of the
        # hit area - otherwise Qt never delivers the press to the item and the
        # handle cannot be grabbed.
        if not self._model.is_locked:
            radius = max(self._handle_mm() * 2.0, 4.0)
            rect = self._rect()
            center = QPointF(rect.center().x(), rect.top() - self._rotate_offset())
            path.addEllipse(center, radius, radius)
        return path

    def _rotate_offset(self) -> float:
        return self._handle_mm() * 1.6

    def _handle_points(self) -> dict[str, QPointF]:
        rect = self._rect()
        size = self._handle_mm()
        offset = size * 1.6
        points = {
            "tl": rect.topLeft(),
            "t": QPointF(rect.center().x(), rect.top()),
            "tr": rect.topRight(),
            "r": QPointF(rect.right(), rect.center().y()),
            "br": rect.bottomRight(),
            "b": QPointF(rect.center().x(), rect.bottom()),
            "bl": rect.bottomLeft(),
            "l": QPointF(rect.left(), rect.center().y()),
            "rotate": QPointF(rect.center().x(), rect.top() - offset),
        }
        return points

    def handle_at(self, pos: QPointF) -> str | None:
        if self._model.is_locked:
            return None
        radius = self._handle_mm()
        points = self._handle_points()
        rotate_point = points["rotate"]
        if (pos - rotate_point).manhattanLength() <= radius * 1.5:
            return "rotate"
        for name, point in points.items():
            if name == "rotate":
                continue
            if (pos - point).manhattanLength() <= radius * 3.2:
                return name
        return None

    # ------------------------------------------------------------------ paint
    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        rect = self._rect()
        painter.fillRect(rect, QBrush(QColor("#FFFFFF")))

        if self._missing or self._pixmap.isNull():
            painter.fillRect(rect, QBrush(QColor("#FEF3F2")))
            painter.setPen(QPen(QColor(DANGER), 0.6))
            painter.drawRect(rect)
            font = QFont()
            font.setPointSizeF(max(2.5, min(5.0, rect.height() / 8.0)))
            painter.setFont(font)
            painter.setPen(QPen(QColor(DANGER)))
            painter.drawText(
                rect.adjusted(1.5, 1.5, -1.5, -1.5),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "Missing source image",
            )
            return

        source = self._source_rect()
        pixmap = self._pixmap_for_paint(painter, source, rect)
        if pixmap is self._pixmap:
            painter.drawPixmap(rect, pixmap, source)
        else:
            # Already scaled to the on-screen size: draw it 1:1 so no second
            # resample blurs the area-averaged edge we just produced.
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))

        selected = self.isSelected()
        pen = QPen(QColor(ACCENT if selected else BORDER_STRONG))
        pen.setCosmetic(True)
        pen.setWidthF(1.6 if selected else 1.0)
        if selected or self._is_hovered():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            painter.drawRect(rect)

        if self._model.rotation:
            self._paint_rotation_badge(painter, rect)

        if selected and not self._model.is_locked:
            self._paint_handles(painter)
        if self._model.is_locked:
            self._paint_lock_badge(painter, rect)
        if self._crop_overlay is not None:
            painter.setPen(QPen(QColor(WARNING), 1.0))

    def _is_hovered(self) -> bool:
        return self._hover_handle is not None

    def _source_rect(self) -> QRectF:
        pixmap_w = float(self._pixmap.width())
        pixmap_h = float(self._pixmap.height())
        if self._pixmap_is_prepared:
            # The crop (and correction) is already baked into the pixmap, so
            # painting the whole thing is exactly right.  Cropping again here
            # was the double-crop that made a cropped item show the wrong area.
            return QRectF(0.0, 0.0, pixmap_w, pixmap_h)
        left, top, right, bottom = self._model.crop_box()
        return QRectF(
            left * pixmap_w,
            top * pixmap_h,
            max(1.0, (right - left) * pixmap_w),
            max(1.0, (bottom - top) * pixmap_h),
        )

    def _paint_handles(self, painter: QPainter) -> None:
        size = self._handle_mm()
        painter.setPen(QPen(QColor(ACCENT), 1.0))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        for name, point in self._handle_points().items():
            if name == "rotate":
                painter.drawEllipse(point, size * 0.75, size * 0.75)
                continue
            painter.drawRect(
                QRectF(
                    point.x() - size / 2.0,
                    point.y() - size / 2.0,
                    size,
                    size,
                )
            )

    def _paint_rotation_badge(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont()
        font.setPointSizeF(max(2.0, min(3.6, rect.width() / 22.0)))
        painter.setFont(font)
        painter.setPen(QPen(QColor(TEXT_MUTED)))
        painter.drawText(
            rect.adjusted(1.0, 1.0, -1.0, -1.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            f"{self._model.rotation:.0f} deg",
        )

    def _paint_lock_badge(self, painter: QPainter, rect: QRectF) -> None:
        size = min(rect.width(), rect.height()) * 0.12
        if size < 1.5:
            return
        badge = QRectF(rect.left() + 1.0, rect.top() + 1.0, size, size)
        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.setPen(QPen(QColor(TEXT_MUTED), 0.4))
        painter.drawEllipse(badge)
        painter.drawLine(
            badge.center().x(), badge.top() + size * 0.45,
            badge.center().x(), badge.bottom() - size * 0.25,
        )

    # -------------------------------------------------------------- interaction
    def hoverMoveEvent(self, event) -> None:
        handle = self.handle_at(event.pos())
        self._hover_handle = handle
        if handle:
            self.setCursor(CURSORS.get(handle, Qt.CursorShape.ArrowCursor))
        elif self._model.is_locked:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hover_handle = None
        self.unsetCursor()
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        handle = self.handle_at(event.pos()) or ""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+click toggles this image in and out of the selection.  The
            # toggle has to happen here: QGraphicsItem's own release handler
            # would toggle it a second time and silently undo the selection.
            self.setSelected(not self.isSelected())
            if not self.isSelected():
                event.accept()
                return
        else:
            if not self.isSelected() and self.scene() is not None:
                self.scene().clearSelection()
            self.setSelected(True)

        if handle == "rotate":
            self._mode = MODE_ROTATE
        elif handle:
            self._mode = MODE_RESIZE
        else:
            self._mode = MODE_MOVE
        if self._model.is_locked:
            self._mode = MODE_IDLE

        positions: dict[str, tuple[float, float]] = {}
        if self._mode == MODE_MOVE and self.scene() is not None:
            for other in self.scene().selectedItems():
                if isinstance(other, PageImageItem):
                    positions[other.item_id] = (
                        other.model.position_x,
                        other.model.position_y,
                    )
        if not positions:
            positions[self.item_id] = (self._model.position_x, self._model.position_y)

        self._drag = DragStart(
            rect=(
                self._model.position_x,
                self._model.position_y,
                self._model.width,
                self._model.height,
            ),
            rotation=self._model.rotation,
            handle=handle,
            scene_pos=event.scenePos(),
            positions=positions,
        )
        if self._mode != MODE_IDLE:
            self.interaction_started.emit(self.item_id)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._mode == MODE_IDLE or self._drag is None:
            super().mouseMoveEvent(event)
            return
        if self._mode == MODE_MOVE:
            self._drag_move(event.scenePos())
        elif self._mode == MODE_RESIZE:
            self._drag_resize(event.scenePos(), event.modifiers())
        elif self._mode == MODE_ROTATE:
            self._drag_rotate(event.scenePos(), event.modifiers())
        self.geometry_changed.emit(self.item_id)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        finished = self._mode != MODE_IDLE
        self._mode = MODE_IDLE
        self._drag = None
        if finished:
            self.interaction_finished.emit(self.item_id)
        # Deliberately not calling the base implementation: QGraphicsItem pairs
        # its press/release selection logic itself and would toggle a Ctrl+click
        # (already handled on press) back off on release.
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        """Double click opens the full size preview of the source image."""
        self.double_clicked.emit(self.item_id)
        event.accept()

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.item_id, event.screenPos())
        event.accept()

    # --------------------------------------------------------------- drag maths
    def _sibling(self, item_id: str):
        scene = self.scene()
        if scene is None:
            return None
        for item in scene.items():
            if isinstance(item, PageImageItem) and item.item_id == item_id:
                return item
        return None

    def _rotate_point(self, x: float, y: float, degrees: float) -> tuple[float, float]:
        radians = math.radians(degrees)
        cos_a, sin_a = math.cos(radians), math.sin(radians)
        return x * cos_a - y * sin_a, x * sin_a + y * cos_a

    def _drag_move(self, scene_pos: QPointF) -> None:
        assert self._drag is not None
        dx = scene_pos.x() - self._drag.scene_pos.x()
        dy = scene_pos.y() - self._drag.scene_pos.y()
        scene = self.scene()
        if scene is not None and hasattr(scene, "snap_move"):
            dx, dy = scene.snap_move(self, dx, dy)
        for item_id, (start_x, start_y) in self._drag.positions.items():
            target = self if item_id == self.item_id else self._sibling(item_id)
            if target is None:
                continue
            target.model.position_x = round(start_x + dx, 3)
            target.model.position_y = round(start_y + dy, 3)
            target.setPos(target.model.position_x, target.model.position_y)
            target.update()

    def _drag_resize(self, scene_pos: QPointF, modifiers) -> None:
        assert self._drag is not None
        start_x, start_y, start_w, start_h = self._drag.rect
        handle = self._drag.handle
        if not handle:
            return
        center_x = start_x + start_w / 2.0
        center_y = start_y + start_h / 2.0
        local_x, local_y = self._rotate_point(
            scene_pos.x() - center_x, scene_pos.y() - center_y, -self._drag.rotation
        )
        px = local_x + start_w / 2.0
        py = local_y + start_h / 2.0

        new_w, new_h = start_w, start_h
        anchor_x, anchor_y = "left", "top"
        if handle in ("br", "tr", "bl", "tl"):
            new_w = px if handle in ("br", "tr") else start_w - px
            new_h = py if handle in ("br", "bl") else start_h - py
            anchor_x = "left" if handle in ("br", "tr") else "right"
            anchor_y = "top" if handle in ("br", "bl") else "bottom"
        elif handle in ("r", "l"):
            new_w = px if handle == "r" else start_w - px
            anchor_x = "left" if handle == "r" else "right"
            anchor_y = "center"
        elif handle in ("t", "b"):
            new_h = py if handle == "b" else start_h - py
            anchor_x = "center"
            anchor_y = "top" if handle == "b" else "bottom"

        new_w = max(MIN_SIZE_MM, new_w)
        new_h = max(MIN_SIZE_MM, new_h)

        keep_ratio = self._model.aspect_locked and not (
            modifiers & Qt.KeyboardModifier.ShiftModifier
        )
        if keep_ratio and start_w > 0 and start_h > 0:
            ratio = start_w / start_h
            if handle in ("r", "l", "tl", "tr", "bl", "br"):
                new_h = new_w / ratio
            else:
                new_w = new_h * ratio
            new_w = max(MIN_SIZE_MM, new_w)
            new_h = max(MIN_SIZE_MM, new_h)

        def anchor_local(width: float, height: float) -> tuple[float, float]:
            ax = width if anchor_x == "right" else (width / 2.0 if anchor_x == "center" else 0.0)
            ay = height if anchor_y == "bottom" else (height / 2.0 if anchor_y == "center" else 0.0)
            return ax, ay

        start_anchor = anchor_local(start_w, start_h)
        new_anchor = anchor_local(new_w, new_h)
        rx, ry = self._rotate_point(
            start_anchor[0] - start_w / 2.0,
            start_anchor[1] - start_h / 2.0,
            self._drag.rotation,
        )
        anchor_scene_x = center_x + rx
        anchor_scene_y = center_y + ry
        rx2, ry2 = self._rotate_point(
            new_anchor[0] - new_w / 2.0,
            new_anchor[1] - new_h / 2.0,
            self._drag.rotation,
        )
        new_x = anchor_scene_x - rx2 - new_w / 2.0
        new_y = anchor_scene_y - ry2 - new_h / 2.0

        self.prepareGeometryChange()
        self._model.set_rect(new_x, new_y, new_w, new_h)
        self.setTransformOriginPoint(new_w / 2.0, new_h / 2.0)
        self.setPos(new_x, new_y)
        self.update()

    def _drag_rotate(self, scene_pos: QPointF, modifiers) -> None:
        assert self._drag is not None
        start_x, start_y, start_w, start_h = self._drag.rect
        center_x = start_x + start_w / 2.0
        center_y = start_y + start_h / 2.0
        angle = math.degrees(math.atan2(scene_pos.y() - center_y, scene_pos.x() - center_x))
        rotation = angle + 90.0
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            rotation = round(rotation / 15.0) * 15.0
        else:
            for snap in (0.0, 90.0, 180.0, -90.0, -180.0):
                if abs(rotation - snap) < 3.0:
                    rotation = snap
                    break
        # Normalise into (-180, 180] so dragging across the Â±180 line does not
        # jump by a full turn.
        rotation = (rotation + 180.0) % 360.0 - 180.0
        self._model.rotation = round(rotation, 2)
        self.setRotation(self._model.rotation)
        self.update()


