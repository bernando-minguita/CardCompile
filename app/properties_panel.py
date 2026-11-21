"""Properties panel: numeric geometry, rotation and page settings."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.paper import available_paper_sizes, normalize_paper_size
from models.page_item import PageItem


def _mm_spin(minimum: float = -1000.0, maximum: float = 1000.0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(2)
    spin.setSingleStep(1.0)
    spin.setSuffix(" mm")
    spin.setKeyboardTracking(False)
    return spin


class PropertiesPanel(QWidget):
    """Editing surface for the selected image and the current page."""

    request_status = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._editor = None
        self._updating = False
        self._last_had_selection = False
        self._build_ui()

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_item_tab(), "Image")
        self.tabs.addTab(self._build_page_tab(), "Page")
        outer.addWidget(self.tabs)

        self.hint = QLabel("Select an image on the page to edit its properties.")
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

    def _build_item_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        self.item_title = QLabel("No selection")
        self.item_title.setObjectName("SectionTitle")
        self.item_title.setWordWrap(True)
        layout.addWidget(self.item_title)

        geometry = QGroupBox("Geometry")
        form = QFormLayout(geometry)
        self.x_spin = _mm_spin()
        self.y_spin = _mm_spin()
        self.w_spin = _mm_spin(5.0, 3000.0)
        self.h_spin = _mm_spin(5.0, 3000.0)
        form.addRow("X position", self.x_spin)
        form.addRow("Y position", self.y_spin)
        form.addRow("Width", self.w_spin)
        form.addRow("Height", self.h_spin)
        self.aspect_check = QCheckBox("Keep aspect ratio")
        form.addRow(self.aspect_check)
        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-180.0, 180.0)
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.setSuffix(" deg")
        self.rotation_spin.setKeyboardTracking(False)
        form.addRow("Rotation", self.rotation_spin)
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["contain", "cover", "stretch"])
        form.addRow("Fit mode", self.fit_combo)
        layout.addWidget(geometry)

        layer_box = QGroupBox("Layer and locking")
        layer_layout = QVBoxLayout(layer_box)
        self.lock_check = QCheckBox("Lock position and size")
        layer_layout.addWidget(self.lock_check)
        buttons = QHBoxLayout()
        self.backward_button = QToolButton()
        self.backward_button.setText("Send -")
        self.forward_button = QToolButton()
        self.forward_button.setText("Bring +")
        self.front_button = QToolButton()
        self.front_button.setText("Front")
        self.back_button = QToolButton()
        self.back_button.setText("Back")
        for button in (
            self.backward_button,
            self.forward_button,
            self.front_button,
            self.back_button,
        ):
            buttons.addWidget(button)
        layer_layout.addLayout(buttons)
        layout.addWidget(layer_box)
        layout.addStretch(1)

        for spin in (self.w_spin, self.h_spin, self.rotation_spin):
            spin.editingFinished.connect(self._apply_item_changes)
        # X/Y move the whole selection, so they are applied on every change: an
        # arrow step lands immediately and typing follows Enter, like width and
        # height.  ``setKeyboardTracking(False)`` keeps the value stable while
        # the user is still typing.
        self.x_spin.valueChanged.connect(lambda _value: self._apply_position_change())
        self.y_spin.valueChanged.connect(lambda _value: self._apply_position_change())
        # Rotation must follow the spinner live (arrow buttons / Enter), not only
        # when the field loses focus. Only the angle is pushed, so the other
        # fields cannot drift.
        self.rotation_spin.valueChanged.connect(self._apply_rotation)
        # With the aspect lock on the two size fields drive each other: the
        # partner is recomputed from the current ratio *before* the pair is
        # pushed, otherwise the stale value in the other box cancelled the edit.
        # Arrow steps apply immediately, exactly like the rotation box.
        self.w_spin.valueChanged.connect(lambda _value: self._on_size_changed("width"))
        self.h_spin.valueChanged.connect(
            lambda _value: self._on_size_changed("height")
        )
        self.aspect_check.toggled.connect(self._on_aspect_toggled)
        self.fit_combo.currentIndexChanged.connect(self._apply_item_changes)
        self.lock_check.toggled.connect(self._on_lock_toggled)
        self.front_button.clicked.connect(self._on_front_clicked)
        self.back_button.clicked.connect(self._on_back_clicked)
        self.forward_button.clicked.connect(self._on_forward_clicked)
        self.backward_button.clicked.connect(self._on_backward_clicked)
        return container

    def _on_front_clicked(self) -> None:
        if self._editor is not None:
            self._editor.bring_to_front()

    def _on_back_clicked(self) -> None:
        if self._editor is not None:
            self._editor.send_to_back()

    def _on_forward_clicked(self) -> None:
        if self._editor is not None:
            self._editor.bring_forward()

    def _on_backward_clicked(self) -> None:
        if self._editor is not None:
            self._editor.send_backward()

    def _build_page_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        self.page_title = QLabel("Page")
        self.page_title.setObjectName("SectionTitle")
        layout.addWidget(self.page_title)

        paper_box = QGroupBox("Paper")
        form = QFormLayout(paper_box)
        self.paper_combo = QComboBox()
        self.paper_combo.addItems(available_paper_sizes())
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["portrait", "landscape"])
        self.custom_width = _mm_spin(50.0, 2000.0)
        self.custom_height = _mm_spin(50.0, 2000.0)
        form.addRow("Paper size", self.paper_combo)
        form.addRow("Orientation", self.orientation_combo)
        form.addRow("Custom width", self.custom_width)
        form.addRow("Custom height", self.custom_height)
        layout.addWidget(paper_box)

        margin_box = QGroupBox("Margins")
        margin_form = QFormLayout(margin_box)
        self.margin_top = _mm_spin(0.0, 100.0)
        self.margin_bottom = _mm_spin(0.0, 100.0)
        self.margin_left = _mm_spin(0.0, 100.0)
        self.margin_right = _mm_spin(0.0, 100.0)
        margin_form.addRow("Top", self.margin_top)
        margin_form.addRow("Bottom", self.margin_bottom)
        margin_form.addRow("Left", self.margin_left)
        margin_form.addRow("Right", self.margin_right)
        layout.addWidget(margin_box)

        actions = QHBoxLayout()
        # Short labels: the three buttons share the 290 px panel and
        # "Duplicate page"/"Delete page" were elided to "plicate pa"/"elete pag"
        # even on a 1920 px screen.  The tooltips keep the full action names.
        self.add_page_button = QPushButton("Add page")
        self.add_page_button.setToolTip("Add a new page after the current page")
        self.duplicate_page_button = QPushButton("Duplicate")
        self.duplicate_page_button.setToolTip("Duplicate the current page")
        self.delete_page_button = QPushButton("Delete")
        self.delete_page_button.setToolTip("Delete the current page")
        self.delete_page_button.setObjectName("Danger")
        actions.addWidget(self.add_page_button)
        actions.addWidget(self.duplicate_page_button)
        actions.addWidget(self.delete_page_button)
        layout.addLayout(actions)
        layout.addStretch(1)

        self.paper_combo.currentTextChanged.connect(self._on_paper_changed)
        self.orientation_combo.currentTextChanged.connect(self._apply_page_changes)
        for spin in (self.custom_width, self.custom_height):
            spin.editingFinished.connect(self._apply_page_changes)
        for spin in (self.margin_top, self.margin_bottom, self.margin_left, self.margin_right):
            spin.editingFinished.connect(self._apply_page_changes)
        self.add_page_button.clicked.connect(lambda: self._editor and self._editor.add_page())
        self.duplicate_page_button.clicked.connect(
            lambda: self._editor and self._editor.duplicate_current_page()
        )
        self.delete_page_button.clicked.connect(
            lambda: self._editor and self._editor.delete_current_page()
        )
        return container

    # ------------------------------------------------------------------ wiring
    def attach_editor(self, editor) -> None:
        self._editor = editor
        if editor is None:
            return
        editor.selection_changed.connect(self.refresh)
        editor.selection_changed.connect(self._sync_tab_to_selection)
        editor.pages_changed.connect(self.refresh)
        editor.page_changed.connect(lambda _page_id: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        if self._editor is None:
            return
        self._updating = True
        try:
            self._refresh_item()
            self._refresh_page()
        finally:
            self._updating = False

    def _refresh_item(self) -> None:
        models: list[PageItem] = self._editor.selected_models() if self._editor else []
        enabled = bool(models)
        for widget in (
            self.x_spin,
            self.y_spin,
            self.w_spin,
            self.h_spin,
            self.rotation_spin,
            self.aspect_check,
            self.fit_combo,
            self.lock_check,
            self.front_button,
            self.back_button,
            self.forward_button,
            self.backward_button,
        ):
            widget.setEnabled(enabled)
        if not models:
            self.item_title.setText("No selection")
            self.hint.setText("Select an image on the page to edit its properties.")
            return

        model = models[0]
        # With several images selected the position fields show the top-left
        # corner of the group (the union bounding box), not one arbitrary card.
        unlocked = [item for item in models if not item.is_locked] or models
        self.item_title.setText(
            f"{len(models)} images selected" if len(models) > 1 else "Selected image"
        )
        self.hint.setText(
            "Drag to move, use the corner handles to resize and the round handle to rotate."
        )
        self.x_spin.setValue(min(item.position_x for item in unlocked))
        self.y_spin.setValue(min(item.position_y for item in unlocked))
        self.w_spin.setValue(model.width)
        self.h_spin.setValue(model.height)
        self.rotation_spin.setValue(model.rotation)
        self.aspect_check.setChecked(model.aspect_locked)
        index = self.fit_combo.findText(model.fit_mode)
        self.fit_combo.setCurrentIndex(max(0, index))
        self.lock_check.setChecked(model.is_locked)

    def _refresh_page(self) -> None:
        if self._editor is None:
            return
        page = self._editor.current_page()
        if page is None:
            self.page_title.setText("No page")
            return
        self.page_title.setText(f"Page {page.page_number} - {page.describe()}")
        index = self.paper_combo.findText(normalize_paper_size(page.paper_size))
        self.paper_combo.setCurrentIndex(max(0, index))
        self.orientation_combo.setCurrentText(page.orientation)
        self.custom_width.setValue(page.width_mm)
        self.custom_height.setValue(page.height_mm)
        self.margin_top.setValue(page.margin_top)
        self.margin_bottom.setValue(page.margin_bottom)
        self.margin_left.setValue(page.margin_left)
        self.margin_right.setValue(page.margin_right)
        is_custom = page.paper_size == "Custom"
        self.custom_width.setEnabled(is_custom)
        self.custom_height.setEnabled(is_custom)

    # ------------------------------------------------------------------ callbacks
    def _apply_item_changes(self) -> None:
        if self._updating or self._editor is None:
            return
        models = self._editor.selected_models()
        if not models:
            return
        item_id = models[0].id
        # Position is handled by ``_apply_position_change``: it moves the whole
        # selection instead of just the first card.
        self._editor.apply_item_geometry(
            item_id,
            {
                "width": self.w_spin.value(),
                "height": self.h_spin.value(),
                "rotation": self.rotation_spin.value(),
                "fit_mode": self.fit_combo.currentText(),
            },
        )

    def _apply_position_change(self) -> None:
        """Move the selection so its top-left corner sits at the typed X/Y.

        The delta is applied to every unlocked selected image, so the spacing
        between the cards is preserved, and the editor clamps it so the group
        stops at the page edge together.
        """
        if self._updating or self._editor is None:
            return
        models = self._editor.selected_models()
        if not models:
            return
        unlocked = [item for item in models if not item.is_locked] or models
        current_x = min(item.position_x for item in unlocked)
        current_y = min(item.position_y for item in unlocked)
        dx = round(self.x_spin.value() - current_x, 3)
        dy = round(self.y_spin.value() - current_y, 3)
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return
        self._editor.move_selected_items(dx, dy)
        # A clamped move (page edge) leaves the group short of the typed value:
        # show where it actually stopped, including the new group minimum.
        self.refresh()

    def _apply_rotation(self, value: float) -> None:
        """Live rotation from the properties spinner."""
        if self._updating or self._editor is None:
            return
        models = self._editor.selected_models()
        if not models:
            return
        self._editor.apply_item_geometry(models[0].id, {"rotation": float(value)})

    def _on_size_changed(self, source: str) -> None:
        """Width/height changed: link the partner, then apply immediately.

        ``setKeyboardTracking(False)`` means typing only fires this on Enter/Tab
        (as ``editingFinished``), while the arrow buttons fire it per step - the
        same live behaviour the rotation spinner has.
        """
        self._link_size_field(source)
        self._apply_item_changes()

    def _link_size_field(self, source: str) -> None:
        """Keep width and height in step while *Keep aspect ratio* is on.

        The panel sends both numbers to the editor, so a partner field still
        showing the old value would cancel the edit (widening an image looked
        like it did nothing). Recomputing it here also means the second value is
        visible in the panel straight away.
        """
        if self._updating or self._editor is None:
            return
        if not self.aspect_check.isChecked():
            return
        models = self._editor.selected_models()
        if not models:
            return
        model = models[0]
        if model.width <= 0 or model.height <= 0:
            return
        ratio = model.width / model.height
        if source == "width":
            driver, partner = self.w_spin, self.h_spin
            target = driver.value() / ratio
        else:
            driver, partner = self.h_spin, self.w_spin
            target = driver.value() * ratio
        target = max(partner.minimum(), min(partner.maximum(), target))
        if abs(partner.value() - target) < 0.005:
            return
        previous = partner.blockSignals(True)
        try:
            partner.setValue(target)
        finally:
            partner.blockSignals(previous)

    def _on_aspect_toggled(self, checked: bool) -> None:
        if self._updating or self._editor is None:
            return
        self._editor.set_aspect_locked(checked)

    def _on_lock_toggled(self, checked: bool) -> None:
        if self._updating or self._editor is None:
            return
        self._editor.set_locked(checked)

    def _sync_tab_to_selection(self) -> None:
        """Image tab with a canvas selection, Page tab when nothing is selected."""
        if self._editor is None:
            return
        has_selection = bool(self._editor.selected_models())
        if has_selection == self._last_had_selection:
            return
        self._last_had_selection = has_selection
        self.tabs.setCurrentIndex(0 if has_selection else 1)

    def _on_paper_changed(self, paper: str) -> None:
        if self._updating or self._editor is None:
            return
        is_custom = paper == "Custom"
        self.custom_width.setEnabled(is_custom)
        self.custom_height.setEnabled(is_custom)
        self._apply_page_changes()

    def _apply_page_changes(self) -> None:
        if self._updating or self._editor is None:
            return
        page = self._editor.current_page()
        if page is None:
            return
        margins = (
            self.margin_top.value(),
            self.margin_bottom.value(),
            self.margin_left.value(),
            self.margin_right.value(),
        )
        self._editor.update_current_page(
            paper_size=self.paper_combo.currentText(),
            orientation=self.orientation_combo.currentText(),
            margins=margins,
            custom_width_mm=self.custom_width.value(),
            custom_height_mm=self.custom_height.value(),
        )



