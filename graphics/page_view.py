"""Zoomable, pannable page view with millimetre rulers."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsView

from app.dnd import DOCUMENT_MIME

#: Screen pixels per millimetre at 100 % zoom (96 DPI assumed by Qt).
PX_PER_MM = 96.0 / 25.4

ZOOM_PRESETS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)


class PageView(QGraphicsView):
    """Graphics view that maps 1 scene unit to 1 millimetre."""

    zoom_changed = Signal(float)
    resized = Signal()
    documents_dropped = Signal(list, QPointF)

    def __init__(self, scene, parent=None) -> None:
        super().__init__(scene, parent)
        self._zoom = 1.0
        self._panning = False
        self._pan_origin = None
        self._base_scale = PX_PER_MM
        self._fit_pending = False
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Dropping organizer cards onto the page.
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    # -------------------------------------------------------------------- zoom
    @property
    def zoom(self) -> float:
        return self._zoom

    def mm_scale(self) -> float:
        """Current pixels per millimetre."""
        return self.transform().m11()

    def page_length_mm(self) -> float:
        scene = self.scene()
        return scene.page.height_mm if scene and scene.page else 297.0

    def apply_zoom(self, zoom: float, keep_anchor: bool = True) -> None:
        zoom = max(0.1, min(8.0, float(zoom)))
        self._zoom = zoom
        self.resetTransform()
        self.scale(self._base_scale * zoom, self._base_scale * zoom)
        if not keep_anchor:
            self.centerOn(0.0, 0.0)
        self.zoom_changed.emit(self._zoom)

    def zoom_in(self) -> None:
        self.apply_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.apply_zoom(self._zoom / 1.25)

    def zoom_fit_page(self) -> None:
        scene = self.scene()
        if scene is None or scene.page is None:
            return
        viewport = self.viewport()
        if viewport.width() < 120 or viewport.height() < 120:
            # The widget has not been laid out yet - fitting now would produce a
            # useless 2-5 % zoom. Retry as soon as it has a real size.
            self.request_fit()
            return
        page = scene.page
        available_w = max(50, viewport.width() - 24)
        available_h = max(50, viewport.height() - 24)
        scale = min(available_w / page.width_mm, available_h / page.height_mm)
        # Same limits as apply_zoom(): otherwise zoom_out() jumps on the first
        # click because the fit zoom was outside the 10-800 % range.
        zoom = max(0.1, min(8.0, scale / self._base_scale))
        self._zoom = zoom
        self.resetTransform()
        self.scale(self._base_scale * zoom, self._base_scale * zoom)
        self.centerOn(page.width_mm / 2.0, page.height_mm / 2.0)
        self.zoom_changed.emit(self._zoom)

    def request_fit(self) -> None:
        """Fit the page as soon as the view has a usable size."""
        self._fit_pending = True
        self._apply_pending_fit()

    def _apply_pending_fit(self) -> bool:
        if not self._fit_pending:
            return False
        viewport = self.viewport()
        if viewport.width() < 120 or viewport.height() < 120:
            return False
        self._fit_pending = False
        self.zoom_fit_page()
        return True

    def zoom_fit_width(self) -> None:
        scene = self.scene()
        if scene is None or scene.page is None:
            return
        page = scene.page
        viewport = self.viewport()
        if viewport.width() < 120:
            self.request_fit()
            return
        available_w = max(50, viewport.width() - 24)
        scale = available_w / page.width_mm
        zoom = max(0.1, min(8.0, scale / self._base_scale))
        self._zoom = zoom
        self.resetTransform()
        self.scale(self._base_scale * zoom, self._base_scale * zoom)
        self.centerOn(page.width_mm / 2.0, page.height_mm / 2.0)
        self.zoom_changed.emit(self._zoom)

    # ------------------------------------------------------------------ drops
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(DOCUMENT_MIME):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(DOCUMENT_MIME):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat(DOCUMENT_MIME):
            super().dropEvent(event)
            return
        raw = bytes(event.mimeData().data(DOCUMENT_MIME)).decode("utf-8", "ignore")
        ids = [part for part in raw.split("\n") if part]
        # QDropEvent.pos()/position() already give a QPoint - calling toPoint()
        # on it raises AttributeError inside the C++ event handler.
        point = self.mapToScene(event.pos())
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if ids:
            self.documents_dropped.emit(ids, QPointF(point))

    # ------------------------------------------------------------------ events
    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.apply_zoom(self._zoom * factor)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_origin = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_origin is not None:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
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
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._pan_origin = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._apply_pending_fit():
            self.resized.emit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_pending_fit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def scene_center_mm(self) -> tuple[float, float]:
        center = self.mapToScene(self.viewport().rect().center())
        return center.x(), center.y()

    def px_to_mm(self, pixels: float) -> float:
        scale = self.mm_scale()
        return pixels / scale if scale > 0 else 0.0
