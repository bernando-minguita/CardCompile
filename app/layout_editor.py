"""Interactive PDF layout editor: canvas, rulers, undo stack and page handling."""

from __future__ import annotations

import uuid
from typing import Iterable, Sequence

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.physical_size_dialog import PhysicalSizeDialog
from app.label_dialog import LabelDialog
from app.theme import ACCENT, TEXT_MUTED
from commands import (
    AddItemsCommand,
    AddLabelsCommand,
    CropCommand,
    DeleteItemsCommand,
    DeleteLabelsCommand,
    GeometryCommand,
    LabelEditCommand,
    LabelMoveCommand,
    LayoutCommand,
    LayoutState,
    PagePropertiesCommand,
    PageStateCommand,
    capture_state,
    item_row,
    item_snapshot,
)
from core.auto_layout_engine import (
    AutoLayoutEngine,
    LayoutError,
    LayoutOptions,
    validate_layout,
)
from core.layout_service import (
    ID1_LONG_EDGE_MM,
    LayoutService,
    fit_natural_size,
    local_to_page,
    original_size_for_crop,
    position_for_local_point,
    rotated_size,
)
from core.logging_setup import get_logger
from graphics.guides import Ruler
from graphics.image_item import PageImageItem
from graphics.page_scene import PageScene
from graphics.page_view import PageView
from models.document import Document
from models.page_item import PageItem
from models.page_label import PageLabel
from models.pdf_page import PdfPage

LOGGER = get_logger("editor")

#: ISO/IEC 7810 ID-1 - Saudi Iqama, driving licence, Istimara and bank cards.
CARD_SIZE_MM = (ID1_LONG_EDGE_MM, 54.0)


def menu_position(value) -> QPoint:
    """Convert a signal position into the QPoint required by ``QMenu.exec``."""
    if isinstance(value, QPoint):
        return value
    return QPoint(int(round(value.x())), int(round(value.y())))


class LayoutEditor(QWidget):
    """The page canvas with rulers, zoom controls and undo support."""

    zoom_changed = Signal(float)
    selection_changed = Signal()
    page_changed = Signal(str)
    pages_changed = Signal()
    items_changed = Signal()
    status_message = Signal(str)

    def __init__(self, workspace, settings, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.layout = LayoutService(workspace)
        self.settings = settings
        self.undo_stack = QUndoStack(self)
        self._current_page_id: str = ""
        self._clipboard: list[dict] = []
        self._interaction_snapshot: dict[str, dict] = {}
        self._reloading = False
        self._reload_pending = False

        self.scene = PageScene(self)
        self.view = PageView(self.scene, self)
        self.h_ruler = Ruler(self.view, Qt.Orientation.Horizontal)
        self.v_ruler = Ruler(self.view, Qt.Orientation.Vertical)
        self._build_ui()
        self._connect_signals()
        self.update_scene_settings()
        self.reload_pages()

    # -------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        corner = QWidget()
        corner.setObjectName("RulerCorner")
        corner.setFixedSize(22, 22)
        layout.addWidget(corner, 0, 0)
        layout.addWidget(self.h_ruler, 0, 1)
        layout.addWidget(self.v_ruler, 1, 0)
        layout.addWidget(self.view, 1, 1)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("Muted")
        self.page_label = QLabel("")
        self.page_label.setObjectName("Muted")

    def _connect_signals(self) -> None:
        self.view.zoom_changed.connect(self._on_zoom_changed)
        self.scene.selection_changed.connect(self._on_selection_changed)
        self.scene.geometry_edited.connect(self._on_geometry_edited)
        self.scene.context_menu_requested.connect(self._show_item_menu)
        self.scene.double_click_requested.connect(self._on_item_double_clicked)
        self.scene.label_double_clicked.connect(self.edit_label)
        self.scene.label_context_requested.connect(self._show_label_menu)
        self.scene.crop_finished.connect(self._on_crop_finished)
        self.scene.crop_cancelled.connect(self._on_crop_cancelled)
        self.scene.interaction_started.connect(self._capture_geometry)
        self.scene.interaction_finished.connect(self._on_interaction_finished)
        self.view.documents_dropped.connect(self._on_documents_dropped)
        # Note: undo/redo refresh is handled inside the command classes and
        # every push site emits items_changed itself - connecting cleanChanged
        # here would rebuild all page thumbnails twice per edit.

    def _on_interaction_finished(self, _item_id: str) -> None:
        self._commit_geometry()

    # ------------------------------------------------------------------ pages
    def pages(self) -> list[PdfPage]:
        return self.layout.pages()

    def current_page(self) -> PdfPage | None:
        if not self._current_page_id:
            return None
        return self.layout.page(self._current_page_id)

    @property
    def current_page_id(self) -> str:
        return self._current_page_id

    def set_current_page(self, page_id: str) -> None:
        if page_id == self._current_page_id:
            return
        self._current_page_id = page_id
        self.reload_current_page()
        self.page_changed.emit(page_id)

    def reload_pages(self, keep_selection: bool = False) -> None:
        """Refresh the page list, the canvas and dependent widgets."""
        pages = self.pages()
        if not pages:
            self._current_page_id = ""
            self.scene.set_page(PdfPage(id="", project_id=self.workspace.id, page_number=1))
            self.scene.clear()
            self.pages_changed.emit()
            return

        ids = [page.id for page in pages]
        if self._current_page_id not in ids:
            self._current_page_id = ids[0]
        page = self.layout.page(self._current_page_id)
        if page is None:
            return
        self.scene.set_page(page)
        self.reload_current_page()
        self.pages_changed.emit()

    def reload_current_page(self) -> None:
        """Rebuild the canvas from the database.

        Re-entrant calls are queued instead of running inside the running
        rebuild: a signal emitted by ``load_items()`` (selection, geometry)
        must never clear the scene while it is being repopulated, otherwise
        objects stay behind in the view - the "extra images" that appeared
        before a crash.
        """
        if self._reloading:
            self._reload_pending = True
            return
        page = self.current_page()
        if page is None:
            return
        self._reloading = True
        try:
            documents = self.layout.documents()
            items = self.layout.items(page.id)
            labels = self.layout.db.labels.list_for_page(page.id)
            self.scene.load_items(items, documents, labels)
            self.page_label.setText(page.describe())
        finally:
            self._reloading = False
        self._on_selection_changed()
        if self._reload_pending:
            self._reload_pending = False
            self.reload_current_page()

    def after_command(self) -> None:
        """Called by commands after they restored a state."""
        self._on_selection_changed()
        self.items_changed.emit()

    # -------------------------------------------------------------------- zoom
    def zoom_in(self) -> None:
        self.view.zoom_in()

    def zoom_out(self) -> None:
        self.view.zoom_out()

    def zoom_fit_page(self) -> None:
        self.view.zoom_fit_page()

    def request_zoom_fit(self) -> None:
        """Fit the page now, or as soon as the view is laid out."""
        self.view.request_fit()

    def zoom_fit_width(self) -> None:
        self.view.zoom_fit_width()

    def set_zoom(self, zoom: float) -> None:
        self.view.apply_zoom(zoom)

    def zoom(self) -> float:
        return self.view.zoom

    def update_scene_settings(self) -> None:
        self.scene.configure(
            show_grid=bool(self.settings.get("layout", "snap_to_grid", True)),
            snap_to_grid=bool(self.settings.get("layout", "snap_to_grid", True)),
            grid_spacing=float(self.settings.get("layout", "grid_spacing_mm", 5.0)),
            show_guides=bool(self.settings.get("layout", "show_alignment_guides", True)),
        )

    def _on_zoom_changed(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100:.0f}%")
        self.zoom_changed.emit(zoom)

    def _on_selection_changed(self) -> None:
        self.selection_changed.emit()

    def selected_models(self) -> list[PageItem]:
        return [item.model for item in self.scene.selected_image_items()]

    def selected_ids(self) -> list[str]:
        return [item.item_id for item in self.scene.selected_image_items()]

    def selected_label_ids(self) -> list[str]:
        return [item.item_id for item in self.scene.selected_label_items()]

    def select_all(self) -> None:
        """Select every image and label on the page (skipping stale items)."""
        self.scene.clearSelection()
        for item in [*self.scene.image_items(), *self.scene.label_items()]:
            try:
                item.setSelected(True)
            except RuntimeError:  # pragma: no cover - item already destroyed
                continue

    # -------------------------------------------------------- geometry commands
    def _on_geometry_edited(self, _item_id: str) -> None:
        self.selection_changed.emit()

    def _capture_geometry(self, item_id: str) -> None:
        ids = set(self.selected_ids())
        ids.update(self.selected_label_ids())
        ids.add(item_id)
        snapshot: dict[str, dict] = {}
        for current in ids:
            model = self.layout.db.items.get(current)
            if model is not None:
                snapshot[current] = item_snapshot(model)
                continue
            label = self.layout.db.labels.get(current)
            if label is not None:
                snapshot[current] = {
                    "position_x": label.position_x,
                    "position_y": label.position_y,
                }
        self._interaction_snapshot = snapshot

    def _commit_geometry(self) -> None:
        """Persist the geometry a drag/resize/rotate produced.

        The scene item holds the *live* model; reading the database here would
        only return the old values, so the move was never stored and the next
        page reload jumped the image back.
        """
        if not self._interaction_snapshot:
            return
        before = self._interaction_snapshot
        self._interaction_snapshot = {}
        after: dict[str, dict] = {}
        changed_ids: list[str] = []
        label_moves: list[tuple[str, dict, dict]] = []
        for item_id in before:
            label_graphic = self.scene.label_by_id(item_id)
            if label_graphic is not None:
                label_model = label_graphic.model
                values = {
                    "position_x": label_model.position_x,
                    "position_y": label_model.position_y,
                }
                if before[item_id] != values:
                    label_moves.append((item_id, before[item_id], values))
                continue
            graphic = self.scene.item_by_id(item_id)
            model = graphic.model if graphic is not None else self.layout.db.items.get(item_id)
            if model is None:
                continue
            self.layout.save_item(model)
            after[item_id] = item_snapshot(model)
            if before[item_id] != after[item_id]:
                changed_ids.append(item_id)
        for label_id, old_values, new_values in label_moves:
            self.save_label_position(label_id, old_values, new_values)
        if not changed_ids:
            return
        sample_before = before[changed_ids[0]]
        sample_after = after[changed_ids[0]]
        label = "Edit image"
        if any(
            sample_before[field] != sample_after[field]
            for field in ("crop_left", "crop_top", "crop_right", "crop_bottom")
        ):
            label = "Crop image"
        elif sample_before["rotation"] != sample_after["rotation"]:
            label = "Rotate image"
        elif (
            sample_before["width"] != sample_after["width"]
            or sample_before["height"] != sample_after["height"]
        ):
            label = "Resize image"
        elif (
            sample_before["position_x"] != sample_after["position_x"]
            or sample_before["position_y"] != sample_after["position_y"]
        ):
            label = "Move image"
        self.undo_stack.push(GeometryCommand(self, label, before, after))
        self.items_changed.emit()

    # ------------------------------------------------------------- add / remove
    def find_free_position(
        self,
        page: PdfPage,
        width: float,
        height: float,
        existing: Sequence[PageItem],
        near: tuple[float, float] | None = None,
        step: float = 5.0,
    ) -> tuple[float, float]:
        """Top-left corner where a *width* x *height* image does not overlap.

        Images used to be dropped exactly on top of each other in the page
        centre; new ones now cascade into the first free slot (preferring the
        area around *near*, e.g. the drop position).
        """
        left, top, printable_w, printable_h = page.printable_rect()
        max_x = max(0.0, page.width_mm - width)
        max_y = max(0.0, page.height_mm - height)
        limit_x = max(left, min(left + printable_w - width, max_x))
        limit_y = max(top, min(top + printable_h - height, max_y))

        def overlaps(x: float, y: float) -> bool:
            for other in existing:
                if (
                    x < other.position_x + other.width
                    and x + width > other.position_x
                    and y < other.position_y + other.height
                    and y + height > other.position_y
                ):
                    return True
            return False

        if near is not None:
            x = min(max(0.0, near[0] - width / 2.0), limit_x)
            y = min(max(0.0, near[1] - height / 2.0), limit_y)
            if not overlaps(x, y):
                return x, y
            # try a small spiral around the drop point before scanning the page
            for radius in range(1, 7):
                for dx, dy in ((radius, 0), (0, radius), (radius, radius), (-radius, 0), (0, -radius)):
                    cx = min(max(0.0, x + dx * step * 2), limit_x)
                    cy = min(max(0.0, y + dy * step * 2), limit_y)
                    if not overlaps(cx, cy):
                        return cx, cy

        y = top
        while y <= limit_y:
            x = left
            while x <= limit_x:
                if not overlaps(x, y):
                    return x, y
                x += step
            y += step
        return (page.width_mm - width) / 2.0, (page.height_mm - height) / 2.0

    def _on_documents_dropped(self, document_ids: list, point) -> None:
        """A card was dragged from the organizer onto the page."""
        documents = self.layout.documents()
        selected = [documents[doc_id] for doc_id in document_ids if doc_id in documents]
        if not selected:
            self.status_message.emit("The dropped documents are not part of this project")
            return
        self.add_documents(selected, position=(point.x(), point.y()))

    # ---------------------------------------------------------------- labels
    def create_label(
        self,
        values: dict | None = None,
        position: tuple[float, float] | None = None,
    ) -> str:
        """Insert a label without asking (used by the dialog and by tests)."""
        page = self.current_page()
        if page is None:
            self.status_message.emit("Open a project to add text")
            return ""
        label = PageLabel(page_id=page.id)
        for key, value in (values or {}).items():
            if hasattr(label, key):
                setattr(label, key, value)
        left, top, printable_w, printable_h = page.printable_rect()
        if position is None:
            x, y = left + 5.0, top + 5.0
        else:
            x, y = position
        label.position_x = round(min(max(0.0, x), page.width_mm - 10.0), 3)
        label.position_y = round(min(max(0.0, y), page.height_mm - 5.0), 3)
        label.z_index = self.layout.db.labels.max_z_index(page.id) + 1
        self.layout.db.labels.insert(label)
        self.undo_stack.push(AddLabelsCommand(self, [label.to_dict()], "Add text"))
        self.reload_current_page()
        self.items_changed.emit()
        self.status_message.emit("Text added to the page")
        return label.id

    def add_label(self, position: tuple[float, float] | None = None) -> str:
        """Open the text dialog and place a label on the current page."""
        page = self.current_page()
        if page is None:
            self.status_message.emit("Open a project to add text")
            return ""
        dialog = LabelDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        return self.create_label(dialog.values(), position)

    def edit_label(self, label_id: str) -> None:
        label = self.layout.db.labels.get(label_id)
        if label is None:
            return
        dialog = LabelDialog(self, label)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        before = {
            "text": label.text,
            "font_family": label.font_family,
            "font_size": label.font_size,
            "bold": label.bold,
            "italic": label.italic,
            "color": label.color,
        }
        for key, value in dialog.values().items():
            setattr(label, key, value)
        self.layout.db.labels.update(label)
        after = {
            "text": label.text,
            "font_family": label.font_family,
            "font_size": label.font_size,
            "bold": label.bold,
            "italic": label.italic,
            "color": label.color,
        }
        if before != after:
            self.undo_stack.push(LabelEditCommand(self, label.id, before, after))
        self.scene.refresh_label(label)
        self.items_changed.emit()
        self.status_message.emit("Text updated")

    def delete_label(self, label_id: str) -> None:
        label = self.layout.db.labels.get(label_id)
        if label is None:
            return
        rows = [label.to_dict()]
        self.layout.db.labels.delete(label_id)
        self.undo_stack.push(DeleteLabelsCommand(self, rows, "Delete text"))
        self.reload_current_page()
        self.items_changed.emit()
        self.status_message.emit("Text deleted")

    def save_label_position(self, label_id: str, before: dict, after: dict) -> None:
        label = self.layout.db.labels.get(label_id)
        if label is None:
            return
        for key, value in after.items():
            setattr(label, key, value)
        self.layout.db.labels.update(label)
        if before != after:
            self.undo_stack.push(LabelMoveCommand(self, label_id, before, after))
        self.items_changed.emit()

    def _show_label_menu(self, label_id: str, global_pos) -> None:
        menu = QMenu(self)
        menu.addAction("Edit text...", lambda: self.edit_label(label_id))
        menu.addAction("Delete text", lambda: self.delete_label(label_id))
        menu.exec(menu_position(global_pos))

    def add_documents(
        self,
        documents: Sequence[Document],
        page_id: str | None = None,
        *,
        fit_mode: str | None = None,
        original_size: bool | None = None,
        position: tuple[float, float] | None = None,
    ) -> int:
        """Place documents on the current page (or *page_id*).

        By default the placement mode from the settings is used: images keep
        their physical size (pixels / DPI) so cropped card exports are not
        stretched, unless the user switched that off.

        *position* (millimetres) is the drop point for drag & drop; without it
        every image gets the first free slot on the page so nothing is hidden
        underneath another image.
        """
        if not documents:
            return 0
        target_id = page_id or self._current_page_id
        page = self.layout.page(target_id)
        if page is None:
            return 0
        fit = fit_mode or str(self.settings.get("layout", "default_fit_mode", "contain"))
        if original_size is None:
            original_size = bool(
                self.settings.get("layout", "place_original_size", True)
            )
        fallback_dpi = float(self.settings.get("layout", "original_size_dpi", 300))
        # Cards cropped from a scan keep the scanner's DPI tag, so "original
        # size" would place a 85.6 mm card at ~30 mm.  Unless the user turned
        # this off, such images are scaled up to the ID-1 long edge.
        min_long_edge = 0.0
        if original_size and bool(self.settings.get("layout", "snap_small_cards", True)):
            min_long_edge = ID1_LONG_EDGE_MM
        rows: list[dict] = []
        scaled = 0
        enlarged = 0
        placed = list(self.layout.items(page.id))
        for document in documents:
            item = self.layout.create_item(
                document,
                page,
                fit_mode=fit,
                original_size=original_size,
                fallback_dpi=fallback_dpi,
                min_long_edge_mm=min_long_edge,
            )
            if item.was_scaled_to_fit:
                scaled += 1
            if item.was_enlarged:
                enlarged += 1
            x, y = self.find_free_position(
                page, item.width, item.height, placed, near=position
            )
            item.position_x = round(x, 3)
            item.position_y = round(y, 3)
            placed.append(item)
            self.layout.insert_item(item)
            rows.append(item_row(item))
        self.undo_stack.push(
            AddItemsCommand(
                self,
                rows,
                "Add image" if len(rows) == 1 else f"Add {len(rows)} images",
            )
        )
        if target_id == self._current_page_id:
            self.reload_current_page()
        self.items_changed.emit()
        mode = "at original size" if original_size else "fitted to the page"
        message = f"Added {len(rows)} image(s) {mode}"
        if scaled:
            message += (
                f" - {scaled} image(s) were reduced to fit the printable area"
            )
        if enlarged:
            message += (
                f" - {enlarged} image(s) were placed at card size ({ID1_LONG_EDGE_MM:.1f} mm)"
                " because the file resolution would have made them tiny"
                " (right-click > Set physical size... to change)"
            )
        self.status_message.emit(message)
        return len(rows)

    def place_original_size(self) -> bool:
        return bool(self.settings.get("layout", "place_original_size", True))

    def set_place_original_size(self, enabled: bool) -> None:
        self.settings.set("layout", "place_original_size", bool(enabled))
        self.settings.save()
        self.status_message.emit(
            "New images will keep their original physical size"
            if enabled
            else "New images will be fitted to the printable area"
        )

    def reset_selection_to_original_size(self) -> None:
        """Resize the selected images to 100 % of their visible (cropped) pixels.

        The size comes from the cropped region (visible pixels / DPI), so an
        image keeps the scale it had and a half-frame crop is never grown back to
        the full scan; only a size that would overflow the printable area is
        reduced, uniformly on both axes.
        """
        models = self.selected_models()
        page = self.current_page()
        if not models or page is None:
            self.status_message.emit("Select an image first")
            return
        documents = self.layout.documents()
        fallback_dpi = float(self.settings.get("layout", "original_size_dpi", 300))
        _, _, printable_w, printable_h = page.printable_rect()
        before: dict[str, dict] = {}
        after: dict[str, dict] = {}
        changed = 0
        for model in models:
            document = documents.get(model.document_id)
            if document is None:
                continue
            before[model.id] = item_snapshot(model)
            natural_w, natural_h = original_size_for_crop(
                document, model.crop_box(), fallback_dpi, model.quad()
            )
            if natural_w <= 0:
                before.pop(model.id, None)
                continue
            # No ``snap_small_cards`` enlargement here.  That rule belongs to
            # *placing* a file whose DPI tag lies; restoring must give exactly
            # 100 % of the visible (cropped) region.  Scaling a small crop up to
            # an ID-1 long edge turned a 42.8 mm half-frame into an 85.6 mm one,
            # so a cropped image could never be restored to its real scale.
            width, height = fit_natural_size(natural_w, natural_h, printable_w, printable_h)
            # A rotated image occupies its rotated bounding box, not the frame:
            # restoring a tall scan turned 90 degrees used to produce a 260 mm
            # wide image on a 210 mm page.  Shrink until the *visible* box fits.
            display_w, display_h = rotated_size(width, height, model.rotation)
            if display_w > printable_w + 0.01 or display_h > printable_h + 0.01:
                scale = min(printable_w / display_w, printable_h / display_h)
                width *= scale
                height *= scale
                display_w, display_h = rotated_size(width, height, model.rotation)
            center_x = model.position_x + model.width / 2.0
            center_y = model.position_y + model.height / 2.0
            # Keep the restored image on the page; the centre is preserved unless
            # the new visible box would hang over an edge.
            visible_left = min(
                max(0.0, center_x - display_w / 2.0),
                max(0.0, page.width_mm - display_w),
            )
            visible_top = min(
                max(0.0, center_y - display_h / 2.0),
                max(0.0, page.height_mm - display_h),
            )
            center_x = visible_left + display_w / 2.0
            center_y = visible_top + display_h / 2.0
            model.width = round(width, 3)
            model.height = round(height, 3)
            model.position_x = round(center_x - width / 2.0, 3)
            model.position_y = round(center_y - height / 2.0, 3)
            self.layout.save_item(model)
            after[model.id] = item_snapshot(model)
            if before[model.id] != after[model.id]:
                changed += 1
        if not before:
            return
        self._push_geometry("Restore original size", before, after)
        self._refresh_selected()
        if changed:
            self.status_message.emit(
                f"{changed} image(s) restored to their original physical size"
            )
        else:
            self.status_message.emit("The selection already uses its original size")

    def set_selection_physical_size(
        self,
        width_mm: float,
        height_mm: float,
        *,
        keep_aspect: bool = True,
    ) -> int:
        """Give the selected images a real-world printed size.

        Crops made from a 100 dpi scan often carry a 300 dpi tag, so "original
        size" prints them far too small.  This sets the size the printed image
        must have (for example a 85.6 x 54 mm Iqama card) and keeps the centre.

        ``keep_aspect`` derives the height from the item's *current* box ratio
        and therefore **discards** ``height_mm``.  That is right when the user
        only knows the width, but it is wrong for a named preset: asking for an
        ID-1 card (85.6 x 54) on a portrait scan used to return 85.6 x 137 mm,
        i.e. still portrait.  Callers that know both dimensions - every preset -
        must pass ``keep_aspect=False``.
        """
        models = self.selected_models()
        page = self.current_page()
        if not models or page is None:
            self.status_message.emit("Select an image first")
            return 0
        target_w = max(5.0, float(width_mm))
        target_h = max(5.0, float(height_mm))
        before: dict[str, dict] = {}
        after: dict[str, dict] = {}
        changed = 0
        capped = 0
        for model in models:
            if model.is_locked:
                continue
            before[model.id] = item_snapshot(model)
            width = target_w
            height = target_h
            if keep_aspect and model.width > 0 and model.height > 0:
                height = target_w * (model.height / model.width)
            # A physical size can never be larger than the page itself.
            scale = 1.0
            if width > page.width_mm:
                scale = min(scale, page.width_mm / width)
            if height > page.height_mm:
                scale = min(scale, page.height_mm / height)
            if scale < 1.0:
                width *= scale
                height *= scale
                capped += 1
            center_x, center_y = model.center
            x = center_x - width / 2.0
            y = center_y - height / 2.0
            x = min(max(0.0, x), max(0.0, page.width_mm - width))
            y = min(max(0.0, y), max(0.0, page.height_mm - height))
            model.set_rect(x, y, width, height)
            self.layout.save_item(model)
            after[model.id] = item_snapshot(model)
            if before[model.id] != after[model.id]:
                changed += 1
        if not before:
            self.status_message.emit("The selected images are locked")
            return 0
        self._push_geometry("Set physical size", before, after)
        self._refresh_selected()
        if changed:
            message = f"{changed} image(s) set to {target_w:.1f} x {target_h:.1f} mm"
            if capped:
                message += " - reduced to stay inside the page"
            self.status_message.emit(message)
        else:
            self.status_message.emit("The selection already has that size")
        return changed

    def set_selection_card_size(self) -> int:
        """One click: make the selection the size of a real ID-1 card.

        ``keep_aspect=False`` is deliberate.  The card size is a *known* pair of
        dimensions, so the height must be honoured; deriving it from the scan's
        own ratio (the old behaviour) turned a portrait photo into a portrait
        card and the action appeared to do nothing.
        """
        return self.set_selection_physical_size(*CARD_SIZE_MM, keep_aspect=False)

    def open_physical_size_dialog(self) -> int:
        models = self.selected_models()
        if not models:
            self.status_message.emit("Select an image first")
            return 0
        start_w, start_h = CARD_SIZE_MM
        page = self.current_page()
        if page is not None:
            _, _, printable_w, printable_h = page.printable_rect()
            if start_w > printable_w or start_h > printable_h:
                scale = min(printable_w / start_w, printable_h / start_h)
                start_w, start_h = start_w * scale, start_h * scale
        dialog = PhysicalSizeDialog(
            self,
            width_mm=start_w,
            height_mm=start_h,
            count=len(models),
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return 0
            width_mm, height_mm = dialog.size_mm()
            keep_aspect = dialog.keep_aspect()
        finally:
            dialog.deleteLater()
        return self.set_selection_physical_size(
            width_mm, height_mm, keep_aspect=keep_aspect
        )

    def delete_selection(self) -> None:
        selected = self.selected_models()
        label_ids = self.selected_label_ids()
        if not selected and not label_ids:
            return
        models = [model for model in selected if not model.is_locked]
        locked = len(selected) - len(models)
        if locked:
            self.status_message.emit(
                f"{locked} locked image(s) were kept - unlock them to delete"
            )
        if not models and not label_ids:
            return
        removed = 0
        if models:
            rows = [item_row(item) for item in models]
            self.layout.delete_items([item.id for item in models])
            self.undo_stack.push(
                DeleteItemsCommand(
                    self,
                    rows,
                    "Delete image" if len(rows) == 1 else f"Delete {len(rows)} images",
                )
            )
            removed += len(rows)
        if label_ids:
            label_rows = []
            for label_id in label_ids:
                label = self.layout.db.labels.get(label_id)
                if label is not None:
                    label_rows.append(label.to_dict())
                    self.layout.db.labels.delete(label_id)
            if label_rows:
                self.undo_stack.push(
                    DeleteLabelsCommand(
                        self,
                        label_rows,
                        "Delete text" if len(label_rows) == 1 else "Delete texts",
                    )
                )
                removed += len(label_rows)
        self.reload_current_page()
        self.items_changed.emit()
        self.status_message.emit(f"Deleted {removed} element(s)")

    def duplicate_selection(self) -> None:
        clones: list[dict] = []
        for model in self.selected_models():
            clone = self.layout.duplicate_item(model)
            clones.append(item_row(clone))
        if not clones:
            return
        self.undo_stack.push(AddItemsCommand(self, clones, "Duplicate image"))
        self.reload_current_page()
        for model in self.layout.items(self._current_page_id):
            if model.id in {row["id"] for row in clones}:
                graphic = self.scene.item_by_id(model.id)
                if graphic is not None:
                    graphic.setSelected(True)
        self.items_changed.emit()
        self.status_message.emit(f"Duplicated {len(clones)} image(s)")

    # ------------------------------------------------------------- clipboard
    def copy_selection(self) -> None:
        models = self.selected_models()
        if not models:
            return
        self._clipboard = [item_row(item) for item in models]
        self.status_message.emit(f"Copied {len(self._clipboard)} image(s)")

    def cut_selection(self) -> None:
        self.copy_selection()
        self.delete_selection()

    def paste_clipboard(self) -> bool:
        """Paste the page objects copied in this application.

        Returns ``False`` when there is nothing to paste, so Ctrl+V can fall
        through to a bitmap on the system clipboard (see
        ``MainWindow._paste_clipboard_image``).
        """
        if not self._clipboard:
            return False
        page = self.current_page()
        if page is None:
            return False
        rows: list[dict] = []
        for row in self._clipboard:
            clone = PageItem.from_row(row)
            clone.id = uuid.uuid4().hex
            clone.page_id = page.id
            # Keep the pasted copy on the page even when the item is larger
            # than the page (the plain min() would produce negative positions).
            clone.position_x = min(
                max(0.0, clone.position_x + 6.0), max(0.0, page.width_mm - clone.width)
            )
            clone.position_y = min(
                max(0.0, clone.position_y + 6.0), max(0.0, page.height_mm - clone.height)
            )
            clone.z_index = self.layout.db.items.max_z_index(page.id) + 1
            self.layout.insert_item(clone)
            rows.append(item_row(clone))
        self.undo_stack.push(AddItemsCommand(self, rows, "Paste image"))
        self.reload_current_page()
        self.items_changed.emit()
        self.status_message.emit(f"Pasted {len(rows)} image(s)")
        return True

    # --------------------------------------------------------------- transforms
    def rotate_selection(self, delta: float | None = None, absolute: float | None = None) -> None:
        models = self.selected_models()
        if not models:
            return
        before: dict[str, dict] = {}
        after: dict[str, dict] = {}
        for model in models:
            before[model.id] = item_snapshot(model)
            value = absolute if absolute is not None else model.rotation + (delta or 0.0)
            while value > 180.0:
                value -= 360.0
            while value < -180.0:
                value += 360.0
            model.rotation = round(value, 2)
            self.layout.save_item(model)
            after[model.id] = item_snapshot(model)
        self._push_geometry("Rotate image", before, after)
        self._refresh_selected()

    def set_rotation(self, degrees: float) -> None:
        self.rotate_selection(absolute=degrees)

    def set_aspect_locked(self, locked: bool) -> None:
        models = self.selected_models()
        if not models:
            return
        before = {model.id: item_snapshot(model) for model in models}
        for model in models:
            model.aspect_locked = locked
            self.layout.save_item(model)
        after = {model.id: item_snapshot(model) for model in models}
        self._push_geometry("Aspect ratio lock", before, after)
        self._refresh_selected()

    def toggle_lock_selection(self) -> None:
        models = self.selected_models()
        if not models:
            return
        before: dict[str, dict] = {}
        after: dict[str, dict] = {}
        for model in models:
            before[model.id] = item_snapshot(model)
            model.is_locked = not model.is_locked
            self.layout.save_item(model)
            after[model.id] = item_snapshot(model)
        self._push_geometry("Lock image", before, after)
        self._refresh_selected()

    def set_locked(self, locked: bool) -> None:
        models = self.selected_models()
        before: dict[str, dict] = {}
        after: dict[str, dict] = {}
        for model in models:
            before[model.id] = item_snapshot(model)
            model.is_locked = locked
            self.layout.save_item(model)
            after[model.id] = item_snapshot(model)
        if before:
            self._push_geometry("Lock image" if locked else "Unlock image", before, after)
        self._refresh_selected()

    def _push_geometry(self, label: str, before: dict, after: dict) -> None:
        if before != after:
            self.undo_stack.push(GeometryCommand(self, label, before, after))
            self.items_changed.emit()

    def _refresh_selected(self) -> None:
        for model in self.selected_models():
            self.scene.refresh_item(model)
        self.selection_changed.emit()
        self.items_changed.emit()

    # ------------------------------------------------------------------ z order
    def bring_forward(self) -> None:
        for model in self.selected_models():
            self.layout.bring_forward(model)
        self.reload_current_page()
        self.items_changed.emit()

    def send_backward(self) -> None:
        for model in self.selected_models():
            self.layout.send_backward(model)
        self.reload_current_page()
        self.items_changed.emit()

    def bring_to_front(self) -> None:
        for model in self.selected_models():
            self.layout.bring_to_front(model)
        self.reload_current_page()
        self.items_changed.emit()

    def send_to_back(self) -> None:
        for model in self.selected_models():
            self.layout.send_to_back(model)
        self.reload_current_page()
        self.items_changed.emit()

    # ------------------------------------------------------- align / distribute
    ALIGN_LABELS = {
        "left": "left edges",
        "center": "centre line (vertical)",
        "right": "right edges",
        "top": "top edges",
        "middle": "middle line (horizontal)",
        "bottom": "bottom edges",
        "page_center_h": "horizontally on the page",
        "page_center_v": "vertically on the page",
        "page_center": "on the page centre",
    }

    def align_selection(self, mode: str) -> None:
        """Align the selected images (by their visible, rotation-aware edges)."""
        models = self.selected_models()
        page = self.current_page()
        if not models or page is None:
            return
        if len(models) < 2 and not mode.startswith("page_"):
            self.status_message.emit(
                "Select at least two images on the page to align them (click, "
                "then Ctrl+click the others)"
            )
            return
        before = {model.id: item_snapshot(model) for model in models}
        self.layout.align_items(models, mode, page)
        after = {model.id: item_snapshot(model) for model in models}
        self._push_geometry("Align images", before, after)
        self._refresh_selected()
        self.status_message.emit(
            f"Aligned {len(models)} image(s) by their {self.ALIGN_LABELS.get(mode, mode)}"
        )

    def distribute_selection(self, axis: str = "horizontal") -> None:
        models = self.selected_models()
        if len(models) < 3:
            self.status_message.emit(
                "Select at least three images to distribute them"
            )
            return
        before = {model.id: item_snapshot(model) for model in models}
        self.layout.distribute_items(models, axis)
        after = {model.id: item_snapshot(model) for model in models}
        self._push_geometry("Distribute images", before, after)
        self._refresh_selected()
        direction = "horizontally" if axis == "horizontal" else "vertically"
        self.status_message.emit(
            f"Distributed {len(models)} image(s) {direction} with equal gaps"
        )

    # ---------------------------------------------------------- numeric editing
    def move_selected_items(self, dx: float, dy: float) -> bool:
        """Shift every unlocked selected image by ``(dx, dy)`` as one undo step.

        The delta is clamped so the *group* stops at the page edge together: one
        card reaching the boundary must not leave the others behind or pile them
        up against the edge.  Locked images keep their place.
        """
        page = self.current_page()
        if page is None:
            return False
        models = [model for model in self.selected_models() if not model.is_locked]
        if not models:
            return False
        if dx > 0:
            dx = min(dx, max(0.0, page.width_mm - max(m.position_x + m.width for m in models)))
        else:
            dx = max(dx, min(0.0, -min(m.position_x for m in models)))
        if dy > 0:
            dy = min(dy, max(0.0, page.height_mm - max(m.position_y + m.height for m in models)))
        else:
            dy = max(dy, min(0.0, -min(m.position_y for m in models)))
        dx = round(dx, 3)
        dy = round(dy, 3)
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return False
        before = {model.id: item_snapshot(model) for model in models}
        for model in models:
            model.position_x = round(model.position_x + dx, 3)
            model.position_y = round(model.position_y + dy, 3)
            self.layout.save_item(model)
        after = {model.id: item_snapshot(model) for model in models}
        if before == after:
            return False
        # One command for the whole selection; ``merge`` lets a run of spinbox
        # steps collapse into a single Ctrl+Z entry.
        self.undo_stack.push(
            GeometryCommand(self, "Move image", before, after, merge=True)
        )
        self.items_changed.emit()
        self._refresh_selected()
        return True

    def apply_item_geometry(
        self, item_id: str, values: dict[str, float | bool | str]
    ) -> None:
        """Called by the properties panel with validated numbers.

        Width and height are resolved together *before* anything is written:
        with the aspect lock on, only the dimension the user actually changed may
        drive the edit.  The panel always sends both numbers, and the partner box
        still held the old value, so writing the pair one after the other applied
        the new width and then immediately put the old width back - widening an
        image in the properties panel appeared to do nothing.
        """
        model = self.layout.db.items.get(item_id)
        if model is None:
            return
        values = dict(values)
        before = {model.id: item_snapshot(model)}
        old_ratio = model.width / model.height if model.height else 0.0
        wanted_width = values.pop("width", None)
        wanted_height = values.pop("height", None)
        if wanted_width is not None or wanted_height is not None:
            target_w = float(wanted_width) if wanted_width is not None else None
            target_h = float(wanted_height) if wanted_height is not None else None
            width_changed = target_w is not None and abs(target_w - model.width) > 1e-6
            height_changed = (
                target_h is not None and abs(target_h - model.height) > 1e-6
            )
            if model.aspect_locked and old_ratio > 0:
                if width_changed:
                    target_h = target_w / old_ratio
                elif height_changed:
                    target_w = target_h * old_ratio
            if target_w is not None:
                model.width = round(max(5.0, target_w), 3)
            if target_h is not None:
                model.height = round(max(5.0, target_h), 3)
        for field_name, value in values.items():
            if field_name in ("position_x", "position_y"):
                setattr(model, field_name, round(float(value), 3))
            elif field_name == "rotation":
                model.rotation = round(max(-180.0, min(180.0, float(value))), 2)
            elif field_name in (
                "crop_left",
                "crop_top",
                "crop_right",
                "crop_bottom",
            ):
                setattr(model, field_name, round(max(0.0, min(0.9, float(value))), 5))
            elif field_name == "aspect_locked":
                model.aspect_locked = bool(value)
            elif field_name == "fit_mode":
                model.fit_mode = str(value)
        self.layout.save_item(model)
        after = {model.id: item_snapshot(model)}
        self._push_geometry("Change image properties", before, after)
        self.scene.refresh_item(model)
        self.selection_changed.emit()

    # -------------------------------------------------------------------- crop
    def start_crop(self) -> None:
        ids = self.selected_ids()
        if not ids:
            self.status_message.emit("Select an image to crop")
            return
        item_id = ids[0]
        model = self.layout.db.items.get(item_id)
        if model is not None:
            self._crop_before = {model.id: item_snapshot(model)}
        if self.scene.start_crop(item_id):
            self.status_message.emit("Drag the crop edges, then press Enter or Esc")

    def _on_crop_finished(self, item_id: str) -> None:
        # The scene item holds the *live* model: re-reading the row here returns
        # the crop values from before the drag, which silently threw the crop
        # away (the image snapped back on the next page reload).
        graphic = self.scene.image_item(item_id)
        model = (
            graphic.model
            if graphic is not None
            else self.layout.db.items.get(item_id)
        )
        if model is None:
            return
        self.layout.save_item(model)
        before = getattr(self, "_crop_before", None)
        after = {model.id: item_snapshot(model)}
        changed = bool(before) and before != after
        if changed:
            self._push_geometry("Crop image", before, after)
        self._crop_before = None
        if graphic is not None:
            graphic.sync_from_model()
        self.scene.refresh_item(model)
        self.selection_changed.emit()
        if not changed:
            self.status_message.emit("Crop unchanged")
        elif model.has_crop():
            self.status_message.emit(
                f"Cropped to {model.width:.1f} x {model.height:.1f} mm "
                "(the frame follows the crop, so nothing is stretched)"
            )
        else:
            self.status_message.emit(
                f"Crop removed - {model.width:.1f} x {model.height:.1f} mm"
            )

    def _on_crop_cancelled(self, item_id: str) -> None:
        """Esc: nothing was modified during the drag, so just leave crop mode."""
        graphic = self.scene.image_item(item_id)
        if graphic is not None:
            graphic.sync_from_model()
        self._crop_before = None
        self.status_message.emit("Crop cancelled")
        self.selection_changed.emit()

    def reset_crop(self) -> None:
        """Remove the crop and grow the frame back to the whole image.

        Cropping trims the frame, so resetting has to undo that too - otherwise
        the full image would be stretched into the cropped rectangle.

        An active crop overlay is closed first: it captured the old frame, so it
        would keep drawing a stale rectangle and handles over the restored image.
        """
        self.scene.exit_crop_mode()
        self._crop_before = None
        models = self.selected_models()
        before = {model.id: item_snapshot(model) for model in models}
        for model in models:
            left, top, right, bottom = model.crop_box()
            visible_w = max(0.01, right - left)
            visible_h = max(0.01, bottom - top)
            # Anchor the visible top-left corner in page space *before* the frame
            # grows, so a rotated image does not slide when the crop is removed.
            target_x, target_y = local_to_page(model, 0.0, 0.0)
            width = model.width / visible_w
            height = model.height / visible_h
            # Grow the frame back around the visible region so the content keeps
            # its scale and stays where it is; the removed part reappears outside.
            model.crop_left = model.crop_top = model.crop_right = model.crop_bottom = 0.0
            model.width = round(width, 3)
            model.height = round(height, 3)
            x, y = position_for_local_point(
                width,
                height,
                model.rotation,
                left * width,
                top * height,
                target_x,
                target_y,
            )
            model.position_x = round(x, 3)
            model.position_y = round(y, 3)
            self.layout.save_item(model)
        after = {model.id: item_snapshot(model) for model in models}
        self._push_geometry("Reset crop", before, after)
        self._refresh_selected()

    # ------------------------------------------------------- perspective dialog
    def open_perspective_dialog(self) -> None:
        """Open the dedicated correction dialog for the selected image(s).

        Perspective and lighting are edited together in one workspace; nothing is
        written until the user accepts.  A single undo command covers the whole
        session, and Cancel restores the original parameters.
        """
        models = self.selected_models()
        if not models:
            self.status_message.emit("Select an image to correct")
            return
        first = models[0]
        document = self.layout.db.documents.get(first.document_id)
        # ``Document.source_path`` is a plain string; the model exposes an
        # ``exists`` property (and a ``source`` Path) for this check.  Calling
        # ``.exists()`` on the string raised AttributeError and the Correction
        # dialog never opened.
        if document is None or not document.exists:
            self.status_message.emit("The source image is missing")
            return

        from app.perspective_dialog import PerspectiveDialog

        before = {model.id: item_snapshot(model) for model in models}
        dialog = PerspectiveDialog(parent=self)
        dialog.set_source(
            document.source_path,
            first.quad(),
            {
                "brightness": first.adjust_brightness,
                "contrast": first.adjust_contrast,
                "saturation": first.adjust_saturation,
                "sharpness": first.adjust_sharpness,
                "shadow": first.adjust_shadow,
            },
        )
        accepted = dialog.exec()
        if not accepted:
            self.status_message.emit("Correction cancelled")
            return

        quad = dialog.quad()
        values = dialog.values()
        for model in models:
            model.set_quad(quad)
            model.adjust_brightness = values["brightness"]
            model.adjust_contrast = values["contrast"]
            model.adjust_saturation = values["saturation"]
            model.adjust_sharpness = values["sharpness"]
            model.adjust_shadow = values["shadow"]
            self.layout.save_item(model)
            self.scene.refresh_correction(model.id)
        after = {model.id: item_snapshot(model) for model in models}
        if before != after:
            self._push_geometry("Correct image", before, after)
        self.status_message.emit(
            "Correction applied" if before != after else "Correction unchanged"
        )

    def start_perspective(self) -> None:
        """Legacy entry point: now opens the unified Perspective Correction."""
        self.open_perspective_dialog()

    def open_adjustments_dialog(self) -> None:
        """Legacy entry point: now opens the unified Perspective Correction."""
        self.open_perspective_dialog()

    def reset_perspective(self) -> None:
        """Put the quad back to the identity (the whole image)."""
        self.scene.exit_crop_mode()
        models = self.selected_models()
        if not models:
            return
        before = {model.id: item_snapshot(model) for model in models}
        for model in models:
            model.reset_quad()
            self.layout.save_item(model)
            self.scene.refresh_correction(model.id)
        after = {model.id: item_snapshot(model) for model in models}
        if before != after:
            self._push_geometry("Reset perspective", before, after)
            self._refresh_selected()

    def reset_adjustments(self) -> None:
        """Return every tone correction to neutral."""
        models = self.selected_models()
        if not models:
            return
        before = {model.id: item_snapshot(model) for model in models}
        for model in models:
            model.reset_adjustments()
            self.layout.save_item(model)
            self.scene.refresh_correction(model.id)
        after = {model.id: item_snapshot(model) for model in models}
        if before != after:
            self._push_geometry("Reset adjustments", before, after)
            self.status_message.emit("Adjustments reset")

    def move_selection_to_page(self, page_id: str) -> None:
        models = self.selected_models()
        if not models:
            return
        before_state = capture_state(self.layout)
        for model in models:
            self.layout.move_item_to_page(model, page_id)
        after_state = capture_state(self.layout)
        self.undo_stack.push(PageStateCommand(self, "Move image to page", before_state, after_state))
        self.reload_pages()
        self.set_current_page(page_id)
        self.items_changed.emit()

    # ------------------------------------------------------------------- pages
    def add_page(self, after_current: bool = True) -> None:
        before = capture_state(self.layout)
        page = self.layout.add_page(
            after_page_id=self._current_page_id if after_current else None
        )
        after = capture_state(self.layout)
        self.undo_stack.push(PageStateCommand(self, "Add page", before, after))
        self.reload_pages()
        self.set_current_page(page.id)
        self.items_changed.emit()
        self.status_message.emit(f"Added page {page.page_number}")

    def duplicate_current_page(self) -> None:
        if not self._current_page_id:
            return
        before = capture_state(self.layout)
        page = self.layout.duplicate_page(self._current_page_id)
        after = capture_state(self.layout)
        if page is None:
            return
        self.undo_stack.push(PageStateCommand(self, "Duplicate page", before, after))
        self.reload_pages()
        self.set_current_page(page.id)
        self.items_changed.emit()
        self.status_message.emit("Page duplicated")

    def delete_current_page(self) -> None:
        if not self._current_page_id:
            return
        pages = self.pages()
        if len(pages) <= 1:
            QMessageBox.information(
                self, "Delete page", "A project must keep at least one page."
            )
            return
        before = capture_state(self.layout)
        if not self.layout.delete_page(self._current_page_id):
            return
        after = capture_state(self.layout)
        self.undo_stack.push(PageStateCommand(self, "Delete page", before, after))
        self._current_page_id = ""
        self.reload_pages()
        self.items_changed.emit()
        self.status_message.emit("Page deleted")

    def toggle_current_page_orientation(self) -> None:
        """Swap the active page between portrait and landscape (undoable).

        Shared by the *Page* menu and the page navigator's context menu.
        """
        page = self.current_page()
        if page is None:
            return
        target = "portrait" if page.orientation == "landscape" else "landscape"
        self.update_current_page(orientation=target)

    def move_current_page(self, delta: int) -> None:
        pages = self.pages()
        ids = [page.id for page in pages]
        if self._current_page_id not in ids:
            return
        index = ids.index(self._current_page_id)
        new_index = max(0, min(len(ids) - 1, index + delta))
        if new_index == index:
            return
        before = capture_state(self.layout)
        self.layout.move_page(self._current_page_id, new_index)
        after = capture_state(self.layout)
        self.undo_stack.push(PageStateCommand(self, "Reorder pages", before, after))
        page_id = self._current_page_id
        self.reload_pages()
        self.set_current_page(page_id)
        self.items_changed.emit()

    def clear_current_page(self) -> None:
        if not self._current_page_id:
            return
        before = capture_state(self.layout)
        self.layout.clear_page(self._current_page_id)
        after = capture_state(self.layout)
        self.undo_stack.push(PageStateCommand(self, "Clear page", before, after))
        self.reload_current_page()
        self.items_changed.emit()
        self.status_message.emit("Page cleared")

    def update_current_page(
        self,
        *,
        paper_size: str | None = None,
        orientation: str | None = None,
        margins: tuple[float, float, float, float] | None = None,
        background_color: str | None = None,
        custom_width_mm: float | None = None,
        custom_height_mm: float | None = None,
    ) -> None:
        page = self.current_page()
        if page is None:
            return
        before = page.to_dict()
        self.layout.update_page(
            page,
            paper_size=paper_size,
            orientation=orientation,
            margins=margins,
            background_color=background_color,
            custom_width_mm=custom_width_mm,
            custom_height_mm=custom_height_mm,
        )
        after = page.to_dict()
        if before == after:
            return  # nothing changed - no undo entry, no rebuild
        self.undo_stack.push(PagePropertiesCommand(self, before, after))
        self.reload_pages()
        self.items_changed.emit()

    # ---------------------------------------------------------- automatic layout
    def _layout_base_options(self) -> LayoutOptions:
        spacing = float(self.settings.get("layout", "grid_spacing_mm", 5.0))
        return LayoutOptions(
            h_spacing_mm=spacing,
            v_spacing_mm=spacing,
            fit_mode=str(self.settings.get("layout", "default_fit_mode", "contain")),
            auto_overflow=bool(self.settings.get("layout", "auto_overflow", True)),
        )

    def layout_options_for(
        self, preset: str, page: PdfPage, count: int
    ) -> LayoutOptions:
        """Options for *preset* when *count* objects/images have to fit."""
        base = self._layout_base_options()
        if preset == "fit":
            return AutoLayoutEngine.fit_all_options(max(1, count), page, base)
        if preset == "custom":
            options = AutoLayoutEngine.options_for_preset("custom", base)
            options.rows = int(self.settings.get("layout", "default_rows", 2))
            options.columns = int(self.settings.get("layout", "default_columns", 2))
            return options.normalized()
        return AutoLayoutEngine.options_for_preset(preset, base)

    def _select_items(self, item_ids: Sequence[str]) -> None:
        """Select the given page objects after a reload (canvas selection only)."""
        wanted = set(item_ids)
        self.scene.clearSelection()
        for graphic in self.scene.image_items():
            try:
                if graphic.item_id in wanted:
                    graphic.setSelected(True)
            except RuntimeError:  # pragma: no cover - item already destroyed
                continue

    def arrange_current_page(
        self, preset: str = "fit", options: LayoutOptions | None = None
    ) -> int:
        """Arrange the images that are *already placed* on the active page.

        Only page objects are touched: the Image Library is never read, so an
        image that exists solely in the library can never be pulled onto a page.
        """
        page = self.current_page()
        if page is None:
            return 0
        items = self.layout.items(page.id)
        if not items:
            self.status_message.emit(
                "Nothing to arrange: add images to this page from the Image "
                "Library first"
            )
            return 0
        chosen = options or self.layout_options_for(preset, page, len(items))
        return self._arrange_items(items, page, chosen, "Arrange page")

    def arrange_selected_items(
        self, preset: str = "fit", options: LayoutOptions | None = None
    ) -> int:
        """Arrange only the page objects that are selected on the canvas."""
        page = self.current_page()
        if page is None:
            return 0
        selected = set(self.selected_ids())
        items = [item for item in self.layout.items(page.id) if item.id in selected]
        if not items:
            self.status_message.emit(
                "Select the images on the page you want to arrange (click or "
                "Ctrl+click them on the canvas)"
            )
            return 0
        chosen = options or self.layout_options_for(preset, page, len(items))
        return self._arrange_items(items, page, chosen, "Arrange selected images")

    def _arrange_items(
        self,
        items: Sequence[PageItem],
        page: PdfPage,
        options: LayoutOptions,
        label: str,
    ) -> int:
        before = {item.id: item_snapshot(item) for item in items}
        engine = AutoLayoutEngine(self.layout)
        try:
            arranged = engine.arrange_items(items, page, options)
        except LayoutError as exc:
            # Nothing moved: report why instead of writing off-page geometry.
            self.status_message.emit(f"Layout not applied: {exc}")
            return 0
        if not arranged:
            return 0
        self.layout.save_items(arranged)
        after = {item.id: item_snapshot(item) for item in arranged}
        self._push_geometry(label, before, after)
        self.reload_current_page()
        self._select_items([item.id for item in arranged])
        self.items_changed.emit()
        message = (
            f"Arranged {len(arranged)} placed image(s) on this page "
            f"({options.rows} x {options.columns})"
        )
        if len(arranged) < len(items):
            message += (
                f" - {len(items) - len(arranged)} did not fit the grid and were "
                "left unchanged"
            )
        self.status_message.emit(message)
        return len(arranged)

    def add_documents_to_new_pages(
        self,
        documents: Sequence[Document],
        preset: str = "fit",
        options: LayoutOptions | None = None,
    ) -> int:
        """Add *documents* to brand-new pages, leaving existing pages untouched."""
        docs = [doc for doc in documents if doc.original_width and doc.original_height]
        if not docs:
            self.status_message.emit("Select at least one image in the Image Library")
            return 0
        before = capture_state(self.layout)
        last_page = self.pages()[-1]
        chosen = options or self.layout_options_for(preset, last_page, len(docs))
        try:
            # Validate against the existing page *before* adding an empty one, so a
            # grid that cannot fit leaves the project untouched.
            validate_layout(last_page, chosen)
        except LayoutError as exc:
            self.status_message.emit(f"Layout not applied: {exc}")
            return 0
        first_new = self.layout.add_page(after_page_id=last_page.id)
        engine = AutoLayoutEngine(self.layout)
        result = engine.apply(docs, first_new.id, chosen)
        after = capture_state(self.layout)
        self.undo_stack.push(LayoutCommand(self, before, after))
        self.reload_pages()
        self.set_current_page(first_new.id)
        self.items_changed.emit()
        pages_added = 1 + result.created_pages
        message = (
            f"Added {result.placed} image(s) to {pages_added} new page(s) - the "
            "existing pages were not changed"
        )
        if result.unassigned_documents:
            message += (
                f" ({len(result.unassigned_documents)} could not be placed because "
                "automatic overflow is off)"
            )
        self.status_message.emit(message)
        return result.placed

    def apply_layout_options(
        self,
        options: LayoutOptions,
        documents: Sequence[Document] | None = None,
        page_id: str | None = None,
    ) -> None:
        """Apply grid *options*.

        With *documents* the images are added to new pages; without them the
        options arrange the images **placed on the page**, never the whole
        Image Library.
        """
        if documents is not None:
            self.add_documents_to_new_pages(documents, options=options)
            return
        target = page_id or self._current_page_id
        if not target:
            return
        if target != self._current_page_id:
            self.set_current_page(target)
        page = self.current_page()
        if page is None:
            return
        items = self.layout.items(page.id)
        if not items:
            self.status_message.emit(
                "Nothing to arrange: this page has no placed images yet"
            )
            return
        self._arrange_items(items, page, options, "Arrange page")

    def apply_layout_preset(
        self, preset: str, documents: Sequence[Document] | None = None
    ) -> None:
        """Preset entry point.

        With *documents* only those library images are added (to new pages);
        without them the preset arranges whatever is already on the active page.
        """
        if documents is not None:
            self.add_documents_to_new_pages(documents, preset=preset)
            return
        self.arrange_current_page(preset)

    # ------------------------------------------------------------- context menu
    def _show_item_menu(self, item_id: str, global_pos) -> None:
        menu = self._build_item_menu(item_id)
        if menu is None:
            return
        # QMenu.exec() requires a QPoint - never a QPointF.
        menu.exec(menu_position(global_pos))

    def _build_item_menu(self, item_id: str) -> QMenu | None:
        """Build (but do not show) the context menu for one image."""
        graphic = self.scene.item_by_id(item_id)
        if graphic is None:
            return None
        if not graphic.isSelected():
            self.scene.clearSelection()
            graphic.setSelected(True)
        menu = QMenu(self)
        menu.addAction("Cut", self.cut_selection, QKeySequence.StandardKey.Cut)
        menu.addAction("Copy", self.copy_selection, QKeySequence.StandardKey.Copy)
        menu.addAction("Paste", self.paste_clipboard, QKeySequence.StandardKey.Paste)
        menu.addAction("Duplicate", self.duplicate_selection, QKeySequence("Ctrl+D"))
        menu.addSeparator()
        menu.addAction("Rotate left 90", lambda: self.rotate_selection(-90))
        menu.addAction("Rotate right 90", lambda: self.rotate_selection(90))
        menu.addAction("Crop...", self.start_crop)
        menu.addAction("Reset crop", self.reset_crop)
        menu.addSeparator()
        menu.addAction("Perspective Correction...", self.open_perspective_dialog)
        menu.addAction("Reset perspective", self.reset_perspective)
        menu.addAction("Reset adjustments", self.reset_adjustments)
        menu.addSeparator()
        menu.addAction("Restore original size", self.reset_selection_to_original_size)
        menu.addAction(
            "Make it card size (85.6 x 54 mm)", self.set_selection_card_size
        )
        menu.addAction("Set physical size...", self.open_physical_size_dialog)
        menu.addSeparator()
        menu.addAction("Bring forward", self.bring_forward)
        menu.addAction("Send backward", self.send_backward)
        menu.addAction("Bring to front", self.bring_to_front)
        menu.addAction("Send to back", self.send_to_back)
        menu.addSeparator()
        menu.addAction(
            "Unlock image" if graphic.model.is_locked else "Lock image",
            self.toggle_lock_selection,
        )
        move_menu = menu.addMenu("Move to page")
        for page in self.pages():
            if page.id == self._current_page_id:
                continue
            action = QAction(f"Page {page.page_number}", move_menu)
            action.triggered.connect(
                lambda _checked=False, target=page.id: self.move_selection_to_page(target)
            )
            move_menu.addAction(action)
        move_menu.addAction("New page", self._move_selection_to_new_page)
        duplicate_menu = menu.addMenu("Duplicate to page")
        for page in self.pages():
            if page.id == self._current_page_id:
                continue
            action = QAction(f"Page {page.page_number}", duplicate_menu)
            action.triggered.connect(
                lambda _checked=False, target=page.id: self.duplicate_selection_to_page(target)
            )
            duplicate_menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Delete", self.delete_selection, QKeySequence.StandardKey.Delete)
        return menu

    def _on_item_double_clicked(self, item_id: str) -> None:
        """Double click on an image shows that image (its cropped region)."""
        model = self.layout.db.items.get(item_id)
        if model is None:
            return
        document = self.layout.documents().get(model.document_id)
        if document is None:
            self.status_message.emit("The source document of this image is gone")
            return
        if not document.exists:
            QMessageBox.warning(
                self,
                "Missing source image",
                f"The original file could not be found:\n{document.source_path}\n\n"
                "Use File > Reconnect missing files to link it again.",
            )
            return
        from app.viewer_dialog import ImageViewerDialog

        # Show the visible part of the placed item, not the whole scan: after
        # cropping, the preview has to match what is on the page.
        crop = None
        if model.has_crop():
            crop = model.crop_pixels(document.original_width, document.original_height)
        # The correction must travel too, otherwise a card straightened and
        # resized in the Perspective Correction still opened as the raw A4 scan.
        correction = model.correction_key() if model.has_correction() else None
        ImageViewerDialog(document, self, crop=crop, correction=correction).exec()

    def _move_selection_to_new_page(self) -> None:
        models = self.selected_models()
        if not models:
            return
        before = capture_state(self.layout)
        page = self.layout.add_page(after_page_id=self._current_page_id)
        for model in models:
            self.layout.move_item_to_page(model, page.id)
        after = capture_state(self.layout)
        self.undo_stack.push(
            PageStateCommand(self, "Move image to new page", before, after)
        )
        self.reload_pages()
        self.set_current_page(page.id)
        self.items_changed.emit()

    def duplicate_selection_to_page(self, page_id: str) -> None:
        """Copy the selected images onto another page (explicit duplication)."""
        models = self.selected_models()
        if not models:
            return
        clones: list[dict] = []
        for model in models:
            clone = self.layout.duplicate_item(model, page_id)
            clones.append(item_row(clone))
        self.undo_stack.push(AddItemsCommand(self, clones, "Duplicate to page"))
        self.reload_pages()
        self.set_current_page(page_id)
        self.items_changed.emit()
        self.status_message.emit(f"Duplicated {len(clones)} image(s) onto another page")








