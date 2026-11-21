"""Millimetre rulers painted next to the page view."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.theme import BORDER, TEXT_MUTED

RULER_SIZE = 22


class Ruler(QWidget):
    """A ruler in millimetres that follows a :class:`PageView`."""

    def __init__(self, view, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(parent)
        self._view = view
        self._orientation = orientation
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(RULER_SIZE)
        else:
            self.setFixedWidth(RULER_SIZE)
        self.setBackgroundRole(self.backgroundRole())
        view.horizontalScrollBar().valueChanged.connect(self.update)
        view.verticalScrollBar().valueChanged.connect(self.update)
        view.zoom_changed.connect(self.update)
        view.resized.connect(self.update)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FBFCFD"))
        painter.setPen(QPen(QColor(BORDER), 1))
        if self._orientation == Qt.Orientation.Horizontal:
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        else:
            painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

        scale = self._view.mm_scale()
        if scale <= 0:
            return

        font = QFont()
        font.setPointSizeF(7.0)
        painter.setFont(font)
        painter.setPen(QPen(QColor(TEXT_MUTED), 1))

        step = self._label_step(scale)
        minor = step / 2.0
        origin = self._view.mapFromScene(QPointF(0.0, 0.0))
        start_mm = -20.0
        end_mm = self._view.page_length_mm() + 20.0

        value = start_mm - (start_mm % minor)
        while value <= end_mm:
            if self._orientation == Qt.Orientation.Horizontal:
                pixel = origin.x() + value * scale
                if 0 <= pixel <= self.width():
                    major = abs(value % step) < 0.001
                    painter.drawLine(
                        int(pixel), self.height() - (12 if major else 6),
                        int(pixel), self.height(),
                    )
                    if major:
                        painter.drawText(int(pixel) + 2, 10, f"{value:.0f}")
            else:
                pixel = origin.y() + value * scale
                if 0 <= pixel <= self.height():
                    major = abs(value % step) < 0.001
                    painter.drawLine(
                        self.width() - (12 if major else 6), int(pixel),
                        self.width(), int(pixel),
                    )
                    if major:
                        painter.save()
                        painter.translate(10, int(pixel) - 2)
                        painter.rotate(-90)
                        painter.drawText(0, 0, f"{value:.0f}")
                        painter.restore()
            value += minor

    @staticmethod
    def _label_step(scale: float) -> float:
        """Pick a readable label interval for the current zoom."""
        for candidate in (5.0, 10.0, 20.0, 50.0, 100.0):
            if candidate * scale >= 45.0:
                return candidate
        return 100.0
