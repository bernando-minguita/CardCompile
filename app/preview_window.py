"""PDF preview window that renders the produced PDF with PyMuPDF."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.export_dialog import open_path
from core.preview_service import PreviewError, PreviewService

THUMB_DPI = 42
VIEW_DPI = 110


class PreviewWindow(QWidget):
    """Shows the actual exported PDF (not the editor canvas)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Without an explicit window flag a QWidget with a parent is embedded as
        # a *child* of the main window - it then appears transparent, uncentred
        # and on top of the toolbars. Force a real top-level window.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowTitle("PDF preview")
        self.setMinimumSize(900, 680)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#F4F5F7"))
        self.setPalette(palette)
        self._service: PreviewService | None = None
        self._page_count = 0
        self._pages_info: list = []
        self._current = 0
        self._pixmap = QPixmap()
        self._zoom = 1.0
        self._centred = False
        self._build_ui()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._centred:
            self._centred = True
            parent = self.parentWidget()
            if parent is not None:
                geometry = parent.frameGeometry()
                self.resize(
                    max(self.minimumWidth(), int(geometry.width() * 0.8)),
                    max(self.minimumHeight(), int(geometry.height() * 0.85)),
                )
                self.move(
                    geometry.center().x() - self.width() // 2,
                    geometry.center().y() - self.height() // 2,
                )

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        toolbar = QHBoxLayout()
        self.first_button = QPushButton("|<")
        self.prev_button = QPushButton("<")
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.next_button = QPushButton(">")
        self.last_button = QPushButton(">|")
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(
            ["Fit page", "Fit width", "50%", "75%", "100%", "150%", "200%"]
        )
        self.open_button = QPushButton("Open PDF")
        self.close_button = QPushButton("Return to editing")
        self.close_button.setObjectName("Primary")
        for widget in (
            self.first_button,
            self.prev_button,
            self.page_spin,
            self.next_button,
            self.last_button,
        ):
            toolbar.addWidget(widget)
        toolbar.addSpacing(12)
        toolbar.addWidget(self.zoom_combo)
        toolbar.addStretch(1)
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.close_button)
        outer.addLayout(toolbar)

        self.info_label = QLabel("No PDF loaded.")
        self.info_label.setObjectName("Muted")
        outer.addWidget(self.info_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setIconSize(QSize(110, 150))
        self.thumbnail_list.setMaximumWidth(190)
        self.thumbnail_list.currentRowChanged.connect(self._on_thumbnail_selected)
        splitter.addWidget(self.thumbnail_list)

        self.scroll = QScrollArea()
        # The label is sized to the rendered page (see _apply_zoom), so the page
        # keeps its aspect ratio and stays centred on a neutral backdrop instead
        # of being stretched to the whole viewport. Both widgets are styled in
        # app.qss (#PreviewScroll / #PreviewPage) because a local setStyleSheet()
        # leaks into child tooltips and blanks them.
        self.scroll.setObjectName("PreviewScroll")
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label = QLabel("Nothing to preview")
        self.page_label.setObjectName("PreviewPage")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumSize(1, 1)
        self.page_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.scroll.setWidget(self.page_label)
        splitter.addWidget(self.scroll)
        splitter.setSizes([180, 780])
        outer.addWidget(splitter, 1)

        self.first_button.clicked.connect(lambda: self.go_to_page(0))
        self.prev_button.clicked.connect(lambda: self.go_to_page(self._current - 1))
        self.next_button.clicked.connect(lambda: self.go_to_page(self._current + 1))
        self.last_button.clicked.connect(lambda: self.go_to_page(self._page_count - 1))
        self.page_spin.valueChanged.connect(self._on_spin_changed)
        self.zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        self.open_button.clicked.connect(self._open_pdf)
        self.close_button.clicked.connect(self.close)

    def _on_spin_changed(self, value: int) -> None:
        self.go_to_page(value - 1)

    # ------------------------------------------------------------------ loading
    def load_pdf(self, path: str | Path, start_page: int = 0) -> bool:
        try:
            self._service = PreviewService(path)
            self._page_count = self._service.page_count()
            self._pages_info = self._service.pages_info()
        except PreviewError as exc:
            self.info_label.setText(str(exc))
            return False
        self.setWindowTitle(f"PDF preview - {Path(path).name}")
        self.page_spin.setRange(1, max(1, self._page_count))
        self._fill_thumbnails()
        self.go_to_page(max(0, min(start_page, self._page_count - 1)))
        # The viewport is only final after the window is shown - fit again then,
        # otherwise the page is clipped/scaled wrongly on the first display.
        QTimer.singleShot(0, self._refit)
        return True

    def _refit(self) -> None:
        if self.zoom_combo.currentText() in ("Fit page", "Fit width"):
            self._on_zoom_changed()

    def _fill_thumbnails(self) -> None:
        self.thumbnail_list.clear()
        if self._service is None:
            return
        for info in self._pages_info:
            item = QListWidgetItem(f"Page {info.index + 1}")
            try:
                data = self._service.render_page_png(info.index, dpi=THUMB_DPI)
                pixmap = QPixmap()
                pixmap.loadFromData(data, "PNG")
                if not pixmap.isNull():
                    item.setIcon(QIcon(pixmap))
            except PreviewError:
                pass
            self.thumbnail_list.addItem(item)

    def go_to_page(self, index: int) -> None:
        if self._service is None or self._page_count == 0:
            return
        index = max(0, min(self._page_count - 1, index))
        self._current = index
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)
        self.thumbnail_list.blockSignals(True)
        self.thumbnail_list.setCurrentRow(index)
        self.thumbnail_list.blockSignals(False)
        self._render_current()

    def _render_current(self) -> None:
        if self._service is None:
            return
        try:
            data = self._service.render_page_png(self._current, dpi=VIEW_DPI)
        except PreviewError as exc:
            self.page_label.setText(str(exc))
            return
        self._pixmap = QPixmap()
        self._pixmap.loadFromData(data, "PNG")
        info = (
            self._pages_info[self._current]
            if self._current < len(self._pages_info)
            else None
        )
        size_text = (
            f"{info.width_mm:.0f} x {info.height_mm:.0f} mm" if info else "unknown size"
        )
        self.info_label.setText(
            f"Page {self._current + 1} of {self._page_count} - {size_text} - "
            f"zoom {self._zoom * 100:.0f}%"
        )
        if self.zoom_combo.currentText() in ("Fit page", "Fit width"):
            self._on_zoom_changed()
        else:
            self._apply_zoom()

    def _on_thumbnail_selected(self, row: int) -> None:
        if row >= 0 and row != self._current:
            self.go_to_page(row)

    def _on_zoom_changed(self) -> None:
        text = self.zoom_combo.currentText()
        if text == "Fit page":
            self._fit_page()
            return
        if text == "Fit width":
            self._fit_width()
            return
        try:
            self._zoom = float(text.strip("%")) / 100.0
        except ValueError:
            self._zoom = 1.0
        self._apply_zoom()

    def _fit_page(self) -> None:
        if self._pixmap.isNull():
            return
        available = self.scroll.viewport().size()
        if available.width() < 120 or available.height() < 120:
            # not laid out yet - try again once the window has a size
            QTimer.singleShot(0, self._refit)
            return
        self._zoom = max(
            0.05,
            min(
                (available.width() - 18) / max(1, self._pixmap.width()),
                (available.height() - 18) / max(1, self._pixmap.height()),
            ),
        )
        self._apply_zoom()

    def _fit_width(self) -> None:
        if self._pixmap.isNull():
            return
        available = self.scroll.viewport().size()
        if available.width() < 120 or available.height() < 120:
            QTimer.singleShot(0, self._refit)
            return
        self._zoom = max(0.05, (available.width() - 18) / max(1, self._pixmap.width()))
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if self._pixmap.isNull():
            self.page_label.clear()
            self.page_label.setText("Nothing to preview")
            self.page_label.resize(420, 300)
            return
        width = max(1, int(self._pixmap.width() * self._zoom))
        height = max(1, int(self._pixmap.height() * self._zoom))
        scaled = self._pixmap.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.page_label.setPixmap(scaled)
        self.page_label.resize(scaled.size())
        self.info_label.setText(
            f"Page {self._current + 1} of {self._page_count} - zoom "
            f"{self._zoom * 100:.0f}% - {scaled.width()} x {scaled.height()} px"
        )

    def _open_pdf(self) -> None:
        if self._service is not None:
            open_path(str(self._service.path))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.zoom_combo.currentText() == "Fit page":
            self._fit_page()

