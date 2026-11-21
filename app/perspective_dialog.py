"""Perspective Correction: a dedicated dialog for perspective and lighting fixes.

The inline four-corner overlay on the page canvas was hard to use: the card is
drawn at its *placed* size (often only 54 mm tall), so the corners were a few
pixels apart and the surrounding page competed for attention.  This dialog gives
the correction its own workspace:

* the source photo is shown large, with four labelled corner handles;
* a live preview shows the straightened result next to the original;
* lighting and tone sliders sit beside it and preview as they move;
* nothing is written until the user presses Apply.

Everything here is a *parameter*.  The source scan is only ever read, and the
quad plus the tone multipliers are stored on the page item, so every correction
stays reversible.

Rendering runs on :class:`app.workers.CorrectionWorker` so dragging a corner
never blocks the interface.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.theme import ACCENT, BORDER, CANVAS, SURFACE, TEXT_MUTED
from app.workers import CorrectionWorker
from core.image_service import ADJUST_MAX, ADJUST_MIN, NEUTRAL

#: Corner roles, in the order the model stores them.
CORNERS = ("tl", "tr", "br", "bl")
CORNER_LABELS = {"tl": "TL", "tr": "TR", "br": "BR", "bl": "BL"}
IDENTITY_QUAD = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)

HANDLE_RADIUS = 9.0
#: A quad smaller than this (in source pixels) cannot be warped meaningfully.
MIN_QUAD_AREA = 400.0
MIN_QUAD_SIDE = 12.0

SLIDER_MIN = 0
SLIDER_MAX = 200
SLIDER_NEUTRAL = 100


def slider_to_value(position: int) -> float:
    """Map a slider position onto a tone multiplier in ``[ADJUST_MIN, ADJUST_MAX]``."""
    position = max(SLIDER_MIN, min(SLIDER_MAX, int(position)))
    if position <= SLIDER_NEUTRAL:
        span = SLIDER_NEUTRAL - SLIDER_MIN
        return NEUTRAL - (SLIDER_NEUTRAL - position) / span * (NEUTRAL - ADJUST_MIN)
    span = SLIDER_MAX - SLIDER_NEUTRAL
    return NEUTRAL + (position - SLIDER_NEUTRAL) / span * (ADJUST_MAX - NEUTRAL)


def value_to_slider(value: float) -> int:
    """Inverse of :func:`slider_to_value`."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return SLIDER_NEUTRAL
    if number != number:  # NaN
        return SLIDER_NEUTRAL
    number = max(ADJUST_MIN, min(ADJUST_MAX, number))
    if number <= NEUTRAL:
        span = NEUTRAL - ADJUST_MIN
        return int(round(SLIDER_NEUTRAL - (NEUTRAL - number) / span * SLIDER_NEUTRAL))
    span = ADJUST_MAX - NEUTRAL
    return int(round(SLIDER_NEUTRAL + (number - NEUTRAL) / span * SLIDER_NEUTRAL))


def quad_is_usable(quad: tuple[float, ...], width: int, height: int) -> bool:
    """True when *quad* is convex, non-degenerate and large enough to warp.

    Mirrors the checks in :func:`core.image_service.perspective_coefficients`
    so the dialog never offers to apply a warp that would be refused at render
    time (which looked like "Apply did nothing").
    """
    if len(quad) != 8 or width <= 0 or height <= 0:
        return False
    points = [
        (quad[index] * width, quad[index + 1] * height) for index in range(0, 8, 2)
    ]
    area = 0.0
    for index in range(4):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % 4]
        area += x1 * y2 - x2 * y1
    if abs(area) / 2.0 < MIN_QUAD_AREA:
        return False
    # Convexity: every cross product must share the same sign.
    sign = 0
    for index in range(4):
        ax, ay = points[index]
        bx, by = points[(index + 1) % 4]
        cx, cy = points[(index + 2) % 4]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) < 1e-6:
            return False
        current = 1 if cross > 0 else -1
        if sign == 0:
            sign = current
        elif current != sign:
            return False
    for index in range(4):
        ax, ay = points[index]
        bx, by = points[(index + 1) % 4]
        if ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 < MIN_QUAD_SIDE:
            return False
    return True


class CornerHandle(QGraphicsObject):
    """One draggable corner, drawn at a constant on-screen size.

    ``ItemIgnoresTransformations`` keeps the handle the same number of pixels
    across zoom levels, so a corner is always grabbable even when the photo is
    zoomed out to fit.
    """

    moved = Signal(str)
    released = Signal(str)

    def __init__(self, role: str, parent=None) -> None:
        super().__init__(parent)
        self.role = role
        self._bounds = QRectF(0.0, 0.0, 1.0, 1.0)
        self._guard = False
        self._press_position: QPointF | None = None
        self._hovered = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(10)
        self.setToolTip(
            f"{CORNER_LABELS[role]} corner - drag, or nudge with the arrow keys"
        )

    # -------------------------------------------------------------- geometry
    def boundingRect(self) -> QRectF:
        radius = HANDLE_RADIUS + 4.0
        return QRectF(-radius, -radius, radius * 2, radius * 2)

    def set_bounds(self, rect: QRectF) -> None:
        """Limit the handle to the image rectangle (scene coordinates)."""
        self._bounds = QRectF(rect)

    def set_position_quietly(self, point: QPointF) -> None:
        """Move without emitting ``moved`` (used when loading a quad)."""
        self._guard = True
        self.setPos(point)
        self._guard = False

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            point = QPointF(value)
            bounds = self._bounds
            point.setX(min(max(point.x(), bounds.left()), bounds.right()))
            point.setY(min(max(point.y(), bounds.top()), bounds.bottom()))
            return point
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._guard
        ):
            self.moved.emit(self.role)
        return super().itemChange(change, value)

    # ----------------------------------------------------------- interaction
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self._guard:
            return
        if self._press_position is not None and self.pos() == self._press_position:
            # A click without movement must not push an undo entry.
            self._press_position = None
            return
        self._press_position = None
        self.released.emit(self.role)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    # -------------------------------------------------------------- painting
    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = HANDLE_RADIUS + (1.5 if self._hovered else 0.0)
        painter.setPen(QPen(QColor("#FFFFFF"), 2.4))
        painter.setBrush(QColor(ACCENT if not self._hovered else "#1D4ED8"))
        painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
        font = QFont(painter.font())
        font.setPixelSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(
            QRectF(-radius, -radius, radius * 2, radius * 2),
            Qt.AlignmentFlag.AlignCenter,
            CORNER_LABELS[self.role],
        )


class SourceCanvas(QGraphicsView):
    """The source photo with four draggable corner handles.

    The quad is kept in *normalised* coordinates (0..1) so it survives a change
    of photo resolution, and the handles are positioned in scene pixels.  Arrow
    keys nudge the focused corner by one pixel (Shift = ten), which is the only
    practical way to place a corner precisely on a low-resolution photo.
    """

    quad_changed = Signal(tuple)
    quad_committed = Signal(tuple)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SourceCanvas")
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(CANVAS)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._polygon: QGraphicsPolygonItem | None = None
        self._handles: dict[str, CornerHandle] = {}
        self._image_size = (0, 0)
        self._active = "tl"
        self._loading = False
        #: Drag-to-pan state.  Only the scrollbars move, so the zoom level is
        #: never touched while the user looks around a zoomed-in photo.
        self._panning = False
        self._pan_origin: QPointF | None = None
        #: True once the user zoomed manually, so a resize keeps their zoom
        #: instead of snapping back to "fit".
        self._user_zoomed = False

    # ------------------------------------------------------------------ data
    def has_image(self) -> bool:
        return self._pixmap_item is not None

    def image_size(self) -> tuple[int, int]:
        return self._image_size

    def set_image(self, pixmap: QPixmap, quad: tuple[float, ...]) -> None:
        """Show *pixmap* and place the handles for *quad* (normalised)."""
        self._loading = True
        try:
            self._scene.clear()
            self._handles = {}
            self._polygon = None
            self._image_size = (pixmap.width(), pixmap.height())
            self._pixmap_item = self._scene.addPixmap(pixmap)
            self._pixmap_item.setZValue(0)
            self._pixmap_item.setTransformationMode(
                Qt.TransformationMode.SmoothTransformation
            )
            rect = QRectF(0.0, 0.0, pixmap.width(), pixmap.height())
            self.setSceneRect(rect)

            self._polygon = QGraphicsPolygonItem()
            pen = QPen(QColor(ACCENT))
            pen.setWidthF(1.6)
            pen.setCosmetic(True)
            self._polygon.setPen(pen)
            self._polygon.setBrush(QBrush(QColor(37, 99, 235, 34)))
            self._polygon.setZValue(5)
            self._scene.addItem(self._polygon)

            for role in CORNERS:
                handle = CornerHandle(role)
                handle.set_bounds(rect)
                handle.moved.connect(self._on_handle_moved)
                handle.released.connect(self._on_handle_released)
                self._scene.addItem(handle)
                self._handles[role] = handle
            self.set_quad(quad)
            self.fit()
        finally:
            self._loading = False

    def set_quad(self, quad: tuple[float, ...]) -> None:
        """Move the handles to *quad* without emitting change signals."""
        if not self._handles:
            return
        width, height = self._image_size
        self._loading = True
        try:
            for index, role in enumerate(CORNERS):
                self._handles[role].set_position_quietly(
                    QPointF(quad[index * 2] * width, quad[index * 2 + 1] * height)
                )
            self._update_polygon()
        finally:
            self._loading = False

    def quad(self) -> tuple[float, ...]:
        """The current corners as normalised fractions."""
        width, height = self._image_size
        if width <= 0 or height <= 0:
            return IDENTITY_QUAD
        values: list[float] = []
        for role in CORNERS:
            point = self._handles[role].pos()
            values.append(min(1.0, max(0.0, point.x() / width)))
            values.append(min(1.0, max(0.0, point.y() / height)))
        return tuple(values)

    # -------------------------------------------------------------- geometry
    def _update_polygon(self) -> None:
        if self._polygon is None:
            return
        self._polygon.setPolygon(
            QPolygonF([self._handles[role].pos() for role in CORNERS])
        )

    def fit(self) -> None:
        """Scale the photo to fill the viewport, keeping its aspect ratio."""
        if self._pixmap_item is None:
            return
        rect = self.sceneRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        viewport = self.viewport().rect()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        scale = min(viewport.width() / rect.width(), viewport.height() / rect.height())
        self.resetTransform()
        self.scale(scale, scale)
        self.centerOn(rect.center())
        self._user_zoomed = False

    def zoom(self) -> float:
        """Current view scale (1.0 == one scene pixel per screen pixel)."""
        return self.transform().m11()

    def is_zoomed_in(self) -> bool:
        """True when the photo is larger than the viewport, so panning helps."""
        rect = self.sceneRect()
        if rect.width() <= 0:
            return False
        viewport = self.viewport().rect()
        return (
            rect.width() * self.zoom() > viewport.width() + 1
            or rect.height() * self.zoom() > viewport.height() + 1
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # A resize must not throw away the user's zoom; only refit when they
        # have not zoomed in themselves.
        if not self._user_zoomed:
            self.fit()

    def wheelEvent(self, event) -> None:
        """Zoom with the wheel, anchored under the pointer."""
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        target = self.zoom() * factor
        if 0.02 <= target <= 40.0:
            self.scale(factor, factor)
            self._user_zoomed = True
            self._update_cursor()
        event.accept()

    # ------------------------------------------------------------------ panning
    def _can_pan(self) -> bool:
        return self._pixmap_item is not None and self.is_zoomed_in()

    def _update_cursor(self) -> None:
        """Show an open hand when the photo can be dragged around."""
        if self._panning:
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._can_pan():
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.viewport().unsetCursor()

    def mousePressEvent(self, event) -> None:
        """Start panning on a left-drag over empty space or with the middle button.

        The corner handles keep priority: they are child items, so a press on a
        handle is delivered to the handle and never reaches this method.
        """
        on_handle = any(
            handle.contains(handle.mapFromScene(self.mapToScene(event.position().toPoint())))
            for handle in self._handles.values()
        )
        startable = event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and not on_handle
            and self._can_pan()
        )
        if startable:
            self._panning = True
            self._pan_origin = event.position()
            self._update_cursor()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_origin is not None:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
            # Moving the scrollbars pans the view without changing the zoom.
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning:
            self._panning = False
            self._pan_origin = None
            self._update_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._panning:
            self.viewport().unsetCursor()
        super().leaveEvent(event)

    # ----------------------------------------------------------- interaction
    def _on_handle_moved(self, role: str) -> None:
        self._active = role
        self._update_polygon()
        if not self._loading:
            self.quad_changed.emit(self.quad())

    def _on_handle_released(self, role: str) -> None:
        self._active = role
        if not self._loading:
            self.quad_committed.emit(self.quad())

    def keyPressEvent(self, event) -> None:
        if not self._handles:
            super().keyPressEvent(event)
            return
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        deltas = {
            Qt.Key.Key_Left: (-step, 0.0),
            Qt.Key.Key_Right: (step, 0.0),
            Qt.Key.Key_Up: (0.0, -step),
            Qt.Key.Key_Down: (0.0, step),
        }
        if event.key() in deltas:
            dx, dy = deltas[event.key()]
            handle = self._handles[self._active]
            handle.setPos(handle.pos() + QPointF(dx, dy))
            self._update_polygon()
            self.quad_changed.emit(self.quad())
            self.quad_committed.emit(self.quad())
            event.accept()
            return
        if event.key() in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4):
            index = event.key() - Qt.Key.Key_1
            self._active = CORNERS[index]
            event.accept()
            return
        super().keyPressEvent(event)



class PreviewPane(QWidget):
    """Shows the corrected result, optionally split against the original.

    The split handle is the fastest way to judge a correction: dragging it
    across the card shows the skew on one side and the straightened result on
    the other, which a full-frame toggle cannot do.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._after: QPixmap | None = None
        self._before: QPixmap | None = None
        self._compare = False
        self._split = 0.5
        self._dragging = False
        self._empty_text = "The corrected card will appear here."
        self.setMinimumSize(260, 200)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ data
    def set_after(self, pixmap: QPixmap | None) -> None:
        self._after = pixmap if pixmap is not None and not pixmap.isNull() else None
        self.update()

    def set_before(self, pixmap: QPixmap | None) -> None:
        self._before = pixmap if pixmap is not None and not pixmap.isNull() else None
        self.update()

    def set_compare(self, enabled: bool) -> None:
        self._compare = bool(enabled)
        self.update()

    def is_comparing(self) -> bool:
        return self._compare

    def clear(self) -> None:
        self._after = None
        self._before = None
        self.update()

    def has_image(self) -> bool:
        return self._after is not None

    # -------------------------------------------------------------- painting
    def _target_rect(self) -> QRectF:
        area = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        pixmap = self._after or self._before
        if pixmap is None or area.width() <= 0 or area.height() <= 0:
            return area
        ratio = min(area.width() / pixmap.width(), area.height() / pixmap.height())
        width = pixmap.width() * ratio
        height = pixmap.height() * ratio
        return QRectF(
            area.center().x() - width / 2.0,
            area.center().y() - height / 2.0,
            width,
            height,
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor(CANVAS))

        rect = self._target_rect()
        if self._after is None and self._before is None:
            pen = QPen(QColor(BORDER))
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(12, 12, -12, -12), 10, 10
            )
            painter.setPen(QColor(TEXT_MUTED))
            font = QFont(painter.font())
            font.setPixelSize(13)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
                self._empty_text,
            )
            painter.end()
            return

        if self._compare and self._before is not None and self._after is not None:
            split_x = rect.left() + rect.width() * self._split
            painter.save()
            painter.setClipRect(
                QRectF(rect.left(), rect.top(), split_x - rect.left(), rect.height())
            )
            painter.drawPixmap(rect, self._before, QRectF(self._before.rect()))
            painter.restore()
            painter.save()
            painter.setClipRect(
                QRectF(split_x, rect.top(), rect.right() - split_x, rect.height())
            )
            painter.drawPixmap(rect, self._after, QRectF(self._after.rect()))
            painter.restore()

            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.drawLine(
                QPointF(split_x, rect.top()), QPointF(split_x, rect.bottom())
            )
            handle = QRectF(split_x - 13, rect.center().y() - 16, 26, 32)
            painter.setBrush(QColor(SURFACE))
            painter.setPen(QPen(QColor(ACCENT), 1.5))
            painter.drawRoundedRect(handle, 7, 7)
            painter.setPen(QPen(QColor(ACCENT), 2))
            centre = rect.center().y()
            painter.drawLine(
                QPointF(split_x - 5, centre - 3), QPointF(split_x - 5, centre + 3)
            )
            painter.drawLine(
                QPointF(split_x + 5, centre - 3), QPointF(split_x + 5, centre + 3)
            )
            self._draw_tag(painter, rect.topLeft() + QPointF(10, 10), "BEFORE")
            self._draw_tag(painter, rect.topRight() + QPointF(-64, 10), "AFTER")
        else:
            pixmap = self._after or self._before
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))

        if self._after is not None:
            painter.setPen(QColor(TEXT_MUTED))
            font = QFont(painter.font())
            font.setPixelSize(10)
            painter.setFont(font)
            painter.drawText(
                rect.adjusted(6, 0, -6, -6),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                f"{self._after.width()} x {self._after.height()} px",
            )
        painter.end()

    @staticmethod
    def _draw_tag(painter: QPainter, top_left: QPointF, text: str) -> None:
        rect = QRectF(top_left.x(), top_left.y(), 54, 20)
        painter.setBrush(QColor(15, 23, 42, 170))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor("#FFFFFF"))
        font = QFont(painter.font())
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    # ----------------------------------------------------------------- mouse
    def mousePressEvent(self, event) -> None:
        if self._compare and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_split(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._update_split(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        super().mouseReleaseEvent(event)

    def _update_split(self, x: float) -> None:
        rect = self._target_rect()
        if rect.width() <= 0:
            return
        self._split = max(0.02, min(0.98, (x - rect.left()) / rect.width()))
        self.update()


class PerspectiveDialog(QDialog):
    """Straighten a photographed card and even out its lighting.

    Deliberately not called a "studio": it does one focused job.  The dialog is
    given the source path plus the item's current parameters; it edits a *copy*
    of them and only writes back when the user accepts.  That keeps Cancel free
    (there is nothing to undo) and means the caller can push a single undo entry
    for the whole session.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Perspective Correction")
        self.setModal(True)
        self.resize(1180, 760)
        self.setMinimumSize(900, 600)

        self._source_path: str | None = None
        self._source_size = (0, 0)
        self._quad: tuple[float, ...] = IDENTITY_QUAD
        self._token = 0

        self._worker = CorrectionWorker(self)
        self._worker.rendered.connect(self._on_rendered)
        self._worker.failed.connect(self._on_render_failed)
        self._worker.start()

        self._build_ui()

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Perspective Correction")
        title.setObjectName("Title")
        subtitle = QLabel(
            "Drag the four corners onto the card's real corners to straighten a "
            "skewed photo, then check the result."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.reset_corners_button = QPushButton("Reset corners")
        self.reset_corners_button.clicked.connect(self.reset_corners)
        header.addWidget(self.reset_corners_button)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_source_pane())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([620, 480])
        root.addWidget(splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        root.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText("Apply")
        self.apply_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_source_pane(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        hint = QLabel(
            "Drag a corner, or press 1-4 to pick one and nudge with the arrow "
            "keys (Shift = 10 px). Scroll to zoom, then drag the photo to move "
            "around it."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.canvas = SourceCanvas()
        self.canvas.quad_changed.connect(self._on_quad_changed)
        self.canvas.quad_committed.connect(self._on_quad_committed)
        layout.addWidget(self.canvas, 1)
        return frame

    def _build_workspace(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_preview_tab(), "Corrected result")
        self.tabs.addTab(self._build_lighting_tab(), "Lighting and tone")
        layout.addWidget(self.tabs, 1)
        return frame

    def _build_preview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        row = QHBoxLayout()
        self.compare_check = QCheckBox("Before / after")
        self.compare_check.toggled.connect(self._on_compare_toggled)
        self.size_label = QLabel("")
        self.size_label.setObjectName("Muted")
        row.addWidget(self.compare_check)
        row.addStretch(1)
        row.addWidget(self.size_label)
        layout.addLayout(row)
        self.preview = PreviewPane()
        layout.addWidget(self.preview, 1)
        return tab

    def _build_lighting_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        intro = QLabel(
            "Stored as settings, never as pixels: the original scan is untouched "
            "and every value can be reset."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._sliders: dict[str, QSlider] = {}
        self._value_labels: dict[str, QLabel] = {}
        for key, label in (
            ("brightness", "Brightness"),
            ("contrast", "Contrast"),
            ("saturation", "Saturation"),
            ("sharpness", "Sharpness"),
        ):
            layout.addWidget(self._build_slider_row(key, label))

        self.shadow_check = QCheckBox("Even out uneven lighting (remove shadows)")
        self.shadow_check.setToolTip(
            "Flattens a hand shadow or a lamp falling on one side of the card"
        )
        self.shadow_check.toggled.connect(self._on_lighting_changed)
        layout.addWidget(self.shadow_check)

        buttons = QHBoxLayout()
        self.auto_button = QPushButton("Auto enhance")
        self.auto_button.setToolTip("Estimate sensible values from the image histogram")
        self.auto_button.clicked.connect(self.auto_enhance)
        self.reset_lighting_button = QPushButton("Reset")
        self.reset_lighting_button.clicked.connect(self.reset_lighting)
        buttons.addWidget(self.auto_button)
        buttons.addWidget(self.reset_lighting_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return tab

    def _build_slider_row(self, key: str, label: str) -> QWidget:
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        top = QHBoxLayout()
        name = QLabel(label)
        value = QLabel("0")
        value.setObjectName("Muted")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(name)
        top.addStretch(1)
        top.addWidget(value)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(SLIDER_MIN, SLIDER_MAX)
        slider.setValue(SLIDER_NEUTRAL)
        slider.valueChanged.connect(lambda _value, k=key: self._on_lighting_changed(k))
        column.addLayout(top)
        column.addWidget(slider)
        self._sliders[key] = slider
        self._value_labels[key] = value
        return row

    # ------------------------------------------------------------------ data
    def set_source(self, path: str, quad: tuple[float, ...], values: dict) -> None:
        """Load the photo and the item's current correction parameters."""
        self._source_path = str(path)
        pixmap = QPixmap(self._source_path)
        if pixmap.isNull():
            self.status_label.setText("The source image could not be opened.")
            return
        # The canvas shows a downscaled copy: a 6000x4000 photo as a scene
        # pixmap would cost ~90 MB of graphics memory for no visible benefit.
        if max(pixmap.width(), pixmap.height()) > 1600:
            pixmap = pixmap.scaled(
                1600,
                1600,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._source_size = (pixmap.width(), pixmap.height())
        self._quad = tuple(quad)
        self.canvas.set_image(pixmap, self._quad)
        self.preview.set_before(pixmap)
        self.set_values(values)
        self.apply_button.setEnabled(True)
        self._schedule_render()

    def set_values(self, values: dict) -> None:
        """Load tone multipliers without triggering a re-render per slider."""
        for key, slider in self._sliders.items():
            slider.blockSignals(True)
            slider.setValue(value_to_slider(values.get(key, NEUTRAL)))
            slider.blockSignals(False)
            self._update_value_label(key)
        self.shadow_check.blockSignals(True)
        self.shadow_check.setChecked(bool(values.get("shadow", False)))
        self.shadow_check.blockSignals(False)

    def values(self) -> dict:
        """The tone parameters currently shown in the dialog."""
        return {
            "brightness": slider_to_value(self._sliders["brightness"].value()),
            "contrast": slider_to_value(self._sliders["contrast"].value()),
            "saturation": slider_to_value(self._sliders["saturation"].value()),
            "sharpness": slider_to_value(self._sliders["sharpness"].value()),
            "shadow": self.shadow_check.isChecked(),
        }

    def quad(self) -> tuple[float, ...]:
        return tuple(self._quad)

    def _update_value_label(self, key: str) -> None:
        position = self._sliders[key].value() - SLIDER_NEUTRAL
        self._value_labels[key].setText(f"{position:+d}" if position else "0")

    # --------------------------------------------------------------- handlers
    def _on_quad_changed(self, quad: tuple) -> None:
        """Preview while dragging, but only if the quad can actually be warped."""
        self._quad = tuple(quad)
        if not quad_is_usable(self._quad, *self._source_size):
            self.status_label.setText(
                "Corners must form a convex shape - adjust them to continue."
            )
            return
        self.status_label.setText("")
        self._schedule_render()

    def _on_quad_committed(self, quad: tuple) -> None:
        self._on_quad_changed(quad)

    def _on_lighting_changed(self, *_args) -> None:
        for key in self._sliders:
            self._update_value_label(key)
        self._schedule_render()

    def _on_compare_toggled(self, enabled: bool) -> None:
        self.preview.set_compare(bool(enabled))

    def reset_corners(self) -> None:
        """Put the corners back on the full image."""
        self._quad = IDENTITY_QUAD
        self.canvas.set_quad(IDENTITY_QUAD)
        self.status_label.setText("Corners reset to the full image.")
        self._schedule_render()

    def reset_lighting(self) -> None:
        self.set_values({})
        self._on_lighting_changed()

    def auto_enhance(self) -> None:
        """Estimate lighting values from the source histogram.

        Deliberately conservative: it corrects exposure and a colour cast
        without pretending to restore detail that is not there.
        """
        if self._source_path is None:
            return
        try:
            from PIL import Image, ImageStat

            with Image.open(self._source_path) as handle:
                sample = handle.convert("RGB")
                sample.thumbnail((320, 320))
            stat = ImageStat.Stat(sample)
            mean = sum(stat.mean) / 3.0
            spread = sum(stat.stddev) / 3.0
        except (OSError, ValueError) as exc:
            self.status_label.setText(f"Could not analyse the image: {exc}")
            return

        brightness = NEUTRAL
        if mean < 90:
            brightness = min(1.35, 1.0 + (110.0 - mean) / 260.0)
        elif mean > 190:
            brightness = max(0.72, 1.0 - (mean - 180.0) / 260.0)
        contrast = NEUTRAL
        if spread < 42:
            contrast = min(1.45, 1.0 + (48.0 - spread) / 110.0)
        self.set_values(
            {
                "brightness": brightness,
                "contrast": contrast,
                "saturation": NEUTRAL,
                "sharpness": 1.18 if spread < 55 else NEUTRAL,
                "shadow": mean < 150,
            }
        )
        self._on_lighting_changed()
        self.status_label.setText("Auto enhance applied - adjust the sliders to taste.")

    # --------------------------------------------------------------- rendering
    def _schedule_render(self) -> None:
        """Ask the worker for a fresh preview (newest request wins)."""
        if self._source_path is None:
            return
        self._token += 1
        payload = {
            "path": self._source_path,
            "quad": list(self._quad),
            "max_side": 1100,
            **self.values(),
        }
        self.status_label.setText("Rendering preview...")
        self._worker.submit(self._token, payload)

    def _on_rendered(self, token: int, result: dict) -> None:
        if token != self._token:
            return  # a newer request has superseded this one
        from graphics.pixmap_cache import pil_to_qpixmap

        self.preview.set_after(pil_to_qpixmap(result["image"]))
        width, height = result["size"]
        self.size_label.setText(f"{width} x {height} px")
        self.status_label.setText("")

    def _on_render_failed(self, token: int, message: str) -> None:
        if token != self._token:
            return
        self.status_label.setText(f"Preview failed: {message}")

    # --------------------------------------------------------------- lifetime
    def done(self, result: int) -> None:
        """Stop the worker before the dialog goes away."""
        self._worker.stop()
        self._worker.wait(2000)
        super().done(result)








