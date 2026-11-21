"""Text label drawn on the page canvas."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QGraphicsObject

from app.theme import ACCENT, BORDER_STRONG
from models.page_label import (
    LABEL_PADDING_MM,
    PageLabel,
    canonical_font_family,
    font_ascender,
    font_descender,
)

PADDING_MM = LABEL_PADDING_MM

#: Labels are laid out with a font this many times larger and the painter is
#: scaled down while drawing.  Scene units are millimetres, so a font sized for
#: the scene only has ~4 px per em and Qt snapped every glyph advance to a whole
#: millimetre; laying out at 16x gives fractional advances that match the PDF.
TEXT_SUPERSAMPLE = 16


def logical_dpi() -> float:
    """Vertical logical DPI Qt uses to convert point sizes into scene units."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 96.0
    dpi = float(screen.logicalDotsPerInchY())
    return dpi if dpi > 1.0 else 96.0


class PageTextItem(QGraphicsObject):
    """A label that can be moved with the mouse and edited by double click."""

    interaction_started = Signal(str)
    interaction_finished = Signal(str)
    geometry_changed = Signal(str)
    context_requested = Signal(str, QPoint)
    double_clicked = Signal(str)

    def __init__(self, model: PageLabel, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._drag_origin: QPointF | None = None
        self._start_pos: tuple[float, float] = (model.position_x, model.position_y)
        self.setFlags(
            QGraphicsObject.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsObject.GraphicsItemFlag.ItemIsFocusable
            | QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(1000 + model.z_index)
        self.setPos(model.position_x, model.position_y)

    # ------------------------------------------------------------------ model
    @property
    def model(self) -> PageLabel:
        return self._model

    @property
    def item_id(self) -> str:
        return self._model.id

    def sync_from_model(self) -> None:
        self.prepareGeometryChange()
        self.setPos(self._model.position_x, self._model.position_y)
        # ``paint`` applies the rotation itself, so the item transform must stay
        # identity.  Calling ``setRotation`` here rotated a refreshed label a
        # second time (a 90 degree label rendered upside down after any edit).
        self.setRotation(0.0)
        self.setZValue(1000 + self._model.z_index)
        self.update()

    # --------------------------------------------------------------- geometry
    def _font(self, scale: float = 1.0) -> QFont:
        # Use the family Qt can resolve (Arial/Times New Roman/Courier New), which
        # is the same face the PDF writer maps to base-14.
        font = QFont(canonical_font_family(self._model.font_family))
        # The font size is in points and the scene is in millimetres.  Qt turns a
        # point size into scene units with the screen's logical DPI, so a plain
        # ``font_size * 25.4 / 72`` only gave the intended millimetres on a
        # 72 DPI device - on 96 DPI labels drew 4/3 too large.
        font.setPointSizeF(
            max(0.5, self._model.font_size * 25.4 / logical_dpi() * scale)
        )
        # Grid-fitting would snap the (scaled) advances to whole pixels; the
        # untouched outlines keep them proportional to the glyph widths.
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        font.setBold(bool(self._model.bold))
        font.setItalic(bool(self._model.italic))
        return font

    def _text_width(self) -> float:
        """Width of the widest line in mm, measured at the supersampled size."""
        metrics = QFontMetricsF(self._font(TEXT_SUPERSAMPLE))
        lines = self._model.text.splitlines() or [""]
        return max(metrics.horizontalAdvance(line) for line in lines) / TEXT_SUPERSAMPLE

    def _em_mm(self) -> float:
        """One em of the label font in millimetres."""
        return self._model.font_size * 25.4 / 72.0

    def _line_height(self) -> float:
        """Line height from the shared font metrics, not from Qt's own ascent.

        The canvas box and the PDF box must agree, including the point a rotated
        label pivots on, so both use ``font_ascender``/``font_descender``.
        """
        family = self._model.font_family
        return (font_ascender(family) + font_descender(family)) * self._em_mm()

    def _size(self) -> tuple[float, float]:
        lines = self._model.text.splitlines() or [""]
        return (
            max(2.0, self._text_width() + PADDING_MM * 2),
            max(1.5, self._line_height() * len(lines) + PADDING_MM * 2),
        )

    def boundingRect(self) -> QRectF:
        width, height = self._size()
        return QRectF(0.0, 0.0, width, height)

    def shape(self):
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.save()
        rect = self.boundingRect()
        painter.translate(rect.width() / 2.0, rect.height() / 2.0)
        painter.rotate(self._model.rotation)
        painter.translate(-rect.width() / 2.0, -rect.height() / 2.0)
        selected = self.isSelected()
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(255, 255, 255, 110)))
            painter.drawRect(rect)
            pen = QPen(QColor(ACCENT))
            pen.setCosmetic(True)
            pen.setWidthF(1.4)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
        # Lay the text out in a supersampled space so the advances are fractions
        # of a millimetre instead of whole ones; the painter scales it back down.
        scale = TEXT_SUPERSAMPLE
        painter.scale(1.0 / scale, 1.0 / scale)
        painter.setFont(self._font(scale))
        painter.setPen(QPen(QColor(self._model.color or "#000000")))
        # Each line is drawn on its own baseline, spaced by the same line height
        # the PDF writer uses.  Letting Qt lay the block out gave it its own line
        # spacing (~0.93 em), so a second line printed ~1 mm higher than the
        # canvas showed it; there is also no word wrap here because the box is
        # sized from ``text.splitlines()``.
        line_height = self._line_height()
        # ``drawText(QPointF, ...)`` takes the baseline, so the first line sits one
        # shared ascender below the padded top edge.
        first_baseline = PADDING_MM + font_ascender(self._model.font_family) * self._em_mm()
        for index, line in enumerate(self._model.text.splitlines() or [""]):
            if not line:
                continue
            painter.drawText(
                QPointF(
                    PADDING_MM * scale,
                    (first_baseline + index * line_height) * scale,
                ),
                line,
            )
        painter.restore()

    # ----------------------------------------------------------------- events
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.scenePos()
            self._start_pos = (self._model.position_x, self._model.position_y)
            self.interaction_started.emit(self.item_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None:
            delta = event.scenePos() - self._drag_origin
            self._model.position_x = round(self._start_pos[0] + delta.x(), 3)
            self._model.position_y = round(self._start_pos[1] + delta.y(), 3)
            self.setPos(self._model.position_x, self._model.position_y)
            self.geometry_changed.emit(self.item_id)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_origin is not None:
            self._drag_origin = None
            self.interaction_finished.emit(self.item_id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit(self.item_id)
        event.accept()

    def contextMenuEvent(self, event) -> None:
        self.context_requested.emit(self.item_id, event.screenPos())
        event.accept()
