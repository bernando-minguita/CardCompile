"""Full size image viewer with zoom controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from core.image_service import (
    ImageValidationError,
    _crop_with_box,
    correct_image,
    load_display_image,
    load_rgb,
    quad_in_crop_space,
)
from graphics.pixmap_cache import pil_to_qpixmap
from models.document import Document


class ImageViewerDialog(QDialog):
    """Shows the scan behind a placed image (read only).

    ``crop`` is the pixel box of the placed item's visible region and
    ``correction`` is :meth:`models.page_item.PageItem.correction_key`, so the
    preview shows exactly what the page and the exported PDF show: the cropped,
    perspective-corrected and tone-adjusted image, not the raw scan.

    Skipping the correction here made the preview disagree with the canvas: a
    card straightened and resized in the Perspective Correction still opened as the
    original A4 scan.
    """

    def __init__(
        self,
        document: Document,
        parent=None,
        crop: tuple[int, int, int, int] | None = None,
        correction: tuple | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.crop = crop
        self.correction = tuple(correction) if correction else None
        self._pixmap = QPixmap()
        self._zoom = 1.0
        suffix = ""
        if self.correction:
            suffix += " (corrected)"
        if crop:
            suffix += " (cropped)"
        self.setWindowTitle(f"{document.filename} - document preview{suffix}")
        self.resize(900, 720)
        self._build_ui()
        self._load()

    def _visible_size(self) -> tuple[int, int]:
        """Pixel size of what is actually shown.

        With a correction the displayed image is the warped result, whose size
        differs from the source, so reporting the source dimensions here made
        the label contradict the picture ("600 x 850 px" above a card).
        """
        if not self._pixmap.isNull():
            return (self._pixmap.width(), self._pixmap.height())
        if self.crop:
            return (
                max(1, self.crop[2] - self.crop[0]),
                max(1, self.crop[3] - self.crop[1]),
            )
        return self.document.original_width, self.document.original_height

    def _describe(self) -> str:
        width, height = self._visible_size()
        text = f"{self.document.filename} - {width} x {height} px"
        source_w = self.document.original_width
        source_h = self.document.original_height
        if self.correction or self.crop:
            text += f" (from {source_w} x {source_h} px)"
        else:
            text += f" - {self.document.human_size()}"
        if self.correction:
            text += " - corrected"
        if self.crop:
            text += " - cropped"
        return text

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.info_label = QLabel(self._describe())
        self.info_label.setObjectName("Muted")
        layout.addWidget(self.info_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label = QLabel("Loading...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.image_label)
        layout.addWidget(self.scroll, 1)

        buttons = QHBoxLayout()
        self.zoom_in_button = QPushButton("Zoom in")
        self.zoom_out_button = QPushButton("Zoom out")
        self.fit_button = QPushButton("Fit")
        self.close_button = QPushButton("Close")
        buttons.addWidget(self.zoom_in_button)
        buttons.addWidget(self.zoom_out_button)
        buttons.addWidget(self.fit_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.zoom_in_button.clicked.connect(lambda: self._apply_zoom(self._zoom * 1.25))
        self.zoom_out_button.clicked.connect(lambda: self._apply_zoom(self._zoom / 1.25))
        self.fit_button.clicked.connect(self._fit)
        self.close_button.clicked.connect(self.accept)

    def _load(self) -> None:
        try:
            image = self._render()
            self._pixmap = pil_to_qpixmap(image)
        except ImageValidationError as exc:
            self.image_label.setText(f"Cannot display this image:\n{exc}")
            return
        # The label was built before the pixmap existed, so it still describes
        # the source dimensions; refresh it now that the real size is known.
        self.info_label.setText(self._describe())
        # Fit once the scroll area has a real size; fitting here uses the
        # not-yet-laid-out viewport and produces a wrong initial zoom.
        QTimer.singleShot(0, self._fit)

    def _render(self):
        """Return the image the page shows: cropped, then corrected.

        The order matters and matches the canvas and the PDF export: the crop
        selects which source pixels exist, and the perspective/tone pass then
        works on those pixels.  The stored quad is in ORIGINAL-image space, so
        it is remapped into the crop before warping.
        """
        if not self.correction:
            return load_display_image(
                self.document.source_path, max_side=2600, crop=self.crop
            )
        image = load_rgb(self.document.source_path)
        crop_box = None
        if self.crop:
            image, crop_box = _crop_with_box(image, self.crop)
        if max(image.size) > 2600:
            image.thumbnail((2600, 2600))
        quad = tuple(self.correction[:8])
        identity = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
        if any(abs(a - b) > 0.0005 for a, b in zip(quad, identity)):
            if crop_box:
                quad = quad_in_crop_space(quad, crop_box)
        else:
            quad = None
        return correct_image(
            image,
            quad=quad,
            brightness=self.correction[8],
            contrast=self.correction[9],
            saturation=self.correction[10],
            sharpness=self.correction[11],
            shadow=bool(self.correction[12]),
        )

    def _fit(self) -> None:
        if self._pixmap.isNull():
            return
        available = self.scroll.viewport().size()
        if available.width() < 120 or available.height() < 120:
            QTimer.singleShot(0, self._fit)
            return
        factor = min(
            (available.width() - 18) / max(1, self._pixmap.width()),
            (available.height() - 18) / max(1, self._pixmap.height()),
        )
        self._apply_zoom(max(0.05, factor))

    def _apply_zoom(self, zoom: float) -> None:
        if self._pixmap.isNull():
            return
        self._zoom = max(0.05, min(6.0, zoom))
        scaled = self._pixmap.scaled(
            int(self._pixmap.width() * self._zoom),
            int(self._pixmap.height() * self._zoom),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.info_label.setText(f"{self._describe()} - zoom {self._zoom * 100:.0f}%")
