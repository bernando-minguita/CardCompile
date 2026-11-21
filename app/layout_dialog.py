"""Dialog for the automatic grid layout options."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from core.auto_layout_engine import PRESET_GRIDS, LayoutOptions


class LayoutDialog(QDialog):
    """Configure rows, columns, spacing, fitting and overflow behaviour."""

    def __init__(self, options: LayoutOptions, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Automatic layout")
        self.setMinimumWidth(420)
        self._options = options
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Documents are arranged on the current page. Manual editing is still "
            "possible afterwards - every image stays an independent object."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        form = QFormLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Custom rows / columns", "custom")
        self.preset_combo.addItem("One document per page", "1")
        self.preset_combo.addItem("Two documents vertically", "2v")
        self.preset_combo.addItem("Two documents horizontally", "2h")
        self.preset_combo.addItem("Four documents (2 x 2)", "4")
        self.preset_combo.addItem("Six documents (3 x 2)", "6")
        form.addRow("Preset", self.preset_combo)

        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 8)
        self.columns_spin = QSpinBox()
        self.columns_spin.setRange(1, 8)
        form.addRow("Rows", self.rows_spin)
        form.addRow("Columns", self.columns_spin)

        self.h_spacing = QDoubleSpinBox()
        self.v_spacing = QDoubleSpinBox()
        for spin in (self.h_spacing, self.v_spacing):
            spin.setRange(0.0, 50.0)
            spin.setDecimals(1)
            spin.setSuffix(" mm")
        form.addRow("Horizontal spacing", self.h_spacing)
        form.addRow("Vertical spacing", self.v_spacing)

        self.fit_combo = QComboBox()
        self.fit_combo.addItem("Contain (keep whole image)", "contain")
        self.fit_combo.addItem("Cover (fill, crop excess)", "cover")
        self.fit_combo.addItem("Stretch (fills, may distort)", "stretch")
        form.addRow("Image fitting", self.fit_combo)

        self.align_combo = QComboBox()
        self.align_combo.addItem("Centered in the cell", "center")
        self.align_combo.addItem("Top left of the cell", "start")
        self.align_combo.addItem("Bottom right of the cell", "end")
        form.addRow("Alignment", self.align_combo)

        self.order_combo = QComboBox()
        self.order_combo.addItem("Fill rows first", "row")
        self.order_combo.addItem("Fill columns first", "column")
        form.addRow("Document order", self.order_combo)

        self.overflow_check = QCheckBox("Create extra pages when needed")
        self.overflow_check.setChecked(self._options.auto_overflow)
        form.addRow(self.overflow_check)

        self.stretch_warning = QLabel("")
        self.stretch_warning.setObjectName("Muted")
        self.stretch_warning.setWordWrap(True)
        form.addRow(self.stretch_warning)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.fit_combo.currentIndexChanged.connect(self._on_fit_changed)
        self._apply_options(self._options)

    def _apply_options(self, options: LayoutOptions) -> None:
        self.rows_spin.setValue(options.rows)
        self.columns_spin.setValue(options.columns)
        self.h_spacing.setValue(options.h_spacing_mm)
        self.v_spacing.setValue(options.v_spacing_mm)
        index = self.fit_combo.findData(options.fit_mode)
        self.fit_combo.setCurrentIndex(max(0, index))
        index = self.align_combo.findData(options.align)
        self.align_combo.setCurrentIndex(max(0, index))
        index = self.order_combo.findData(options.order)
        self.order_combo.setCurrentIndex(max(0, index))
        self.overflow_check.setChecked(options.auto_overflow)
        self._on_fit_changed()

    def _on_preset_changed(self) -> None:
        preset = self.preset_combo.currentData()
        if preset in PRESET_GRIDS:
            rows, columns = PRESET_GRIDS[preset]
            self.rows_spin.setValue(rows)
            self.columns_spin.setValue(columns)
        self.rows_spin.setEnabled(preset not in PRESET_GRIDS)
        self.columns_spin.setEnabled(preset not in PRESET_GRIDS)

    def _on_fit_changed(self) -> None:
        if self.fit_combo.currentData() == "stretch":
            self.stretch_warning.setText(
                "Warning: Stretch changes the aspect ratio of the documents, which "
                "can make identity documents harder to read."
            )
        else:
            self.stretch_warning.setText("")

    # ------------------------------------------------------------------- result
    def options(self) -> LayoutOptions:
        return LayoutOptions(
            rows=self.rows_spin.value(),
            columns=self.columns_spin.value(),
            h_spacing_mm=self.h_spacing.value(),
            v_spacing_mm=self.v_spacing.value(),
            fit_mode=self.fit_combo.currentData() or "contain",
            align=self.align_combo.currentData() or "center",
            order=self.order_combo.currentData() or "row",
            auto_overflow=self.overflow_check.isChecked(),
            preset=self.preset_combo.currentData() or "custom",
        ).normalized()
