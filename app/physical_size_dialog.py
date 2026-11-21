"""Dialog that gives placed images a real-world size in millimetres.

Useful when a card was cropped out of a scan: the file often claims 300 dpi while
it only holds ~100 dpi worth of pixels, so "original size" prints it far too
small.  Choosing a preset here sets the size the printed card must have.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

#: ``(label, width_mm, height_mm)`` - ``(0, 0)`` means "custom".
CARD_PRESETS: tuple[tuple[str, float, float], ...] = (
    ("Saudi Iqama / Driving licence / Istimara - ID-1 card (85.6 x 54 mm)", 85.6, 54.0),
    ("Passport data page (88 x 125 mm)", 88.0, 125.0),
    ("Passport photo (40 x 60 mm)", 40.0, 60.0),
    ("A6 card (105 x 74 mm)", 105.0, 74.0),
    ("Custom", 0.0, 0.0),
)

DEFAULT_CARD_MM = (85.6, 54.0)


class PhysicalSizeDialog(QDialog):
    """Ask for the printed width/height of the selected images."""

    def __init__(
        self,
        parent=None,
        width_mm: float = DEFAULT_CARD_MM[0],
        height_mm: float = DEFAULT_CARD_MM[1],
        count: int = 1,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set physical size")
        self.setModal(True)
        self._count = count
        self._build_ui()
        self._select_matching_preset(width_mm, height_mm)
        self.width_spin.setValue(max(5.0, float(width_mm)))
        self.height_spin.setValue(max(5.0, float(height_mm)))

    # ---------------------------------------------------------------- build
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        subject = (
            "1 image" if self._count == 1 else f"{self._count} images"
        )
        intro = QLabel(
            f"Printed size for {subject}. The image is scaled on the page, the "
            "source file is never modified."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.preset_combo = QComboBox()
        for label, _w, _h in CARD_PRESETS:
            self.preset_combo.addItem(label)
        form.addRow("Preset", self.preset_combo)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(5.0, 2000.0)
        self.width_spin.setDecimals(1)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setSuffix(" mm")
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(5.0, 2000.0)
        self.height_spin.setDecimals(1)
        self.height_spin.setSingleStep(0.5)
        self.height_spin.setSuffix(" mm")
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        layout.addLayout(form)

        self.keep_aspect_check = QCheckBox("Keep the image aspect ratio")
        # Off by default: a preset names an exact size (an ID-1 card is
        # 85.6 x 54 mm), so the height must be honoured.  Leaving this checked
        # discarded the height and turned a portrait scan into a portrait card,
        # which made the preset look broken.
        self.keep_aspect_check.setChecked(False)
        self.keep_aspect_check.setToolTip(
            "Off: the width and height are used exactly as entered, which is "
            "what a card preset needs. On: the width drives the size and the "
            "height follows the image proportions, so the picture is never "
            "stretched."
        )
        layout.addWidget(self.keep_aspect_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)

    # --------------------------------------------------------------- helpers
    def _select_matching_preset(self, width_mm: float, height_mm: float) -> None:
        for index, (_label, preset_w, preset_h) in enumerate(CARD_PRESETS):
            if abs(preset_w - width_mm) < 0.15 and abs(preset_h - height_mm) < 0.15:
                self.preset_combo.blockSignals(True)
                self.preset_combo.setCurrentIndex(index)
                self.preset_combo.blockSignals(False)
                return
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(len(CARD_PRESETS) - 1)
        self.preset_combo.blockSignals(False)

    def _on_preset_changed(self, index: int) -> None:
        _label, width_mm, height_mm = CARD_PRESETS[max(0, index)]
        if width_mm <= 0 or height_mm <= 0:
            return
        self.width_spin.setValue(width_mm)
        self.height_spin.setValue(height_mm)

    def size_mm(self) -> tuple[float, float]:
        return (self.width_spin.value(), self.height_spin.value())

    def keep_aspect(self) -> bool:
        return self.keep_aspect_check.isChecked()
