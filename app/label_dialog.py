"""Dialog to create or edit a text label (text, font, size, colour)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from models.page_label import (
    DEFAULT_FONT_FAMILY,
    FONT_CHOICES,
    PageLabel,
    canonical_font_family,
)


class LabelDialog(QDialog):
    """Ask for the text and appearance of a label."""

    def __init__(self, parent=None, label: PageLabel | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit text" if label else "Add text")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._color = label.color if label else "#000000"
        self._build_ui()
        if label is not None:
            self.set_from_label(label)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Text shown on the page")
        self.text_edit.setMinimumHeight(80)
        form.addRow("Text", self.text_edit)

        self.font_combo = QComboBox()
        self.font_combo.addItems(list(FONT_CHOICES))
        self.font_combo.setEditable(True)
        form.addRow("Font", self.font_combo)

        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(4.0, 144.0)
        self.size_spin.setDecimals(1)
        self.size_spin.setSingleStep(1.0)
        self.size_spin.setValue(12.0)
        self.size_spin.setSuffix(" pt")
        form.addRow("Size", self.size_spin)

        style_row = QHBoxLayout()
        self.bold_check = QCheckBox("Bold")
        self.italic_check = QCheckBox("Italic")
        style_row.addWidget(self.bold_check)
        style_row.addWidget(self.italic_check)
        style_row.addStretch(1)
        form.addRow("Style", style_row)

        color_row = QHBoxLayout()
        self.color_button = QPushButton("Colour...")
        self.color_button.clicked.connect(self._pick_color)
        self.color_value = QLabel(self._color)
        color_row.addWidget(self.color_button)
        color_row.addWidget(self.color_value)
        color_row.addStretch(1)
        form.addRow("Colour", color_row)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ helpers
    def _pick_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Text colour")
        if chosen.isValid():
            self._color = chosen.name()
            self.color_value.setText(self._color)

    def set_from_label(self, label: PageLabel) -> None:
        self.text_edit.setPlainText(label.text)
        # Older labels store "Helvetica"/"Times-Roman"; show the family the
        # canvas actually draws so the dialog does not claim a different face.
        family = canonical_font_family(label.font_family)
        index = self.font_combo.findText(family)
        if index >= 0:
            self.font_combo.setCurrentIndex(index)
        else:
            self.font_combo.setEditText(family)
        self.size_spin.setValue(float(label.font_size))
        self.bold_check.setChecked(bool(label.bold))
        self.italic_check.setChecked(bool(label.italic))
        self._color = label.color or "#000000"
        self.color_value.setText(self._color)

    # -------------------------------------------------------------------- data
    def values(self) -> dict:
        text = self.text_edit.toPlainText()
        if not text.strip():
            text = "Text"
        font_name = self.font_combo.currentText().strip() or DEFAULT_FONT_FAMILY
        return {
            "text": text,
            "font_family": font_name,
            "font_size": float(self.size_spin.value()),
            "bold": self.bold_check.isChecked(),
            "italic": self.italic_check.isChecked(),
            "color": self._color,
        }

    def preview_font(self) -> QFont:
        font = QFont(canonical_font_family(self.font_combo.currentText()))
        font.setPointSizeF(self.size_spin.value())
        font.setBold(self.bold_check.isChecked())
        font.setItalic(self.italic_check.isChecked())
        return font
