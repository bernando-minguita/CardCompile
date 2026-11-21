"""Editor workspace: organizer, page navigator, canvas and properties panel."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.context import AppContext
from app.import_dialog import ImportDialog
from app.layout_dialog import LayoutDialog
from app.layout_editor import LayoutEditor
from app.organizer_widget import OrganizerWidget
from app.page_manager import PageNavigator
from app.properties_panel import PropertiesPanel
from core.auto_layout_engine import AutoLayoutEngine, LayoutOptions
from core.logging_setup import get_logger

LOGGER = get_logger("workspace")


class EditorWorkspace(QWidget):
    """The main editing surface for one open project."""

    status_message = Signal(str)
    documents_changed = Signal()
    content_changed = Signal()
    request_preview = Signal()

    def __init__(self, context: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self.workspace = None
        self.editor: LayoutEditor | None = None
        self._build_ui()

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        self.organizer = OrganizerWidget()
        self.organizer.setMinimumWidth(250)
        self.splitter.addWidget(self.organizer)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self._build_layout_toolbar())

        self.inner_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.navigator = PageNavigator()
        # 150 px clipped the page caption to "A4 Po..."/"3 ima...": the 84 px
        # thumbnail plus a readable caption needs ~200 px.
        self.navigator.setMinimumWidth(200)
        self.navigator.setMaximumWidth(280)
        self.inner_splitter.addWidget(self.navigator)
        self.editor_holder = QWidget()
        self.editor_holder_layout = QVBoxLayout(self.editor_holder)
        self.editor_holder_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_label = QLabel(
            "Start editing - import or paste images, arrange them on the page, "
            "then Save to name your project."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setObjectName("Muted")
        self.editor_holder_layout.addWidget(self.empty_label)
        self.inner_splitter.addWidget(self.editor_holder)
        self.inner_splitter.setSizes([170, 800])
        center_layout.addWidget(self.inner_splitter, 1)
        self.splitter.addWidget(center)

        self.properties = PropertiesPanel()
        self.properties.setMinimumWidth(290)
        self.splitter.addWidget(self.properties)
        self.splitter.setSizes([270, 760, 330])
        layout.addWidget(self.splitter, 1)

        self.organizer.request_import.connect(self.open_import_dialog)
        self.organizer.request_add_to_page.connect(self._add_documents_to_page)
        self.organizer.request_add_to_new_pages.connect(
            self._add_documents_to_new_pages
        )
        self.organizer.documents_changed.connect(self._on_documents_changed)
        self.organizer.status_message.connect(self.status_message.emit)
        self.organizer.original_size_changed.connect(self._on_original_size_toggled)

    def _build_layout_toolbar(self) -> QWidget:
        bar = QWidget()
        # Styling lives in resources/styles/app.qss (QWidget#LayoutToolbar).
        # A local setStyleSheet() here is inherited by the widgets' tooltips and
        # made them render as a white box with white text (unreadable).
        bar.setObjectName("LayoutToolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(3)

        self.original_size_toggle = QToolButton()
        self.original_size_toggle.setText("1:1")
        self.original_size_toggle.setCheckable(True)
        self.original_size_toggle.setToolTip(
            "Keep original size when adding images: place them at their real "
            "physical size (pixels / image DPI) instead of stretching them to the "
            "printable area"
        )
        self.original_size_toggle.setChecked(
            bool(self.context.settings.get("layout", "place_original_size", True))
        )
        self.original_size_toggle.toggled.connect(self._on_original_size_toggled)
        layout.addWidget(self.original_size_toggle)
        layout.addSpacing(8)

        # Compact menu.  The two scopes are separated on purpose: "Current page"
        # only re-arranges page objects, "Selected library images" only adds the
        # images the user selected in the Image Library.
        self.layout_button = QToolButton()
        # "Arrange on Page" was elided to "Arrange on P..." at the default
        # 1500 px window; the tooltip carries the full description.
        self.layout_button.setText("Arrange")
        self.layout_button.setObjectName("MenuToolButton")
        self.layout_button.setToolTip(
            "Arrange the images already placed on this page, or add the images "
            "selected in the Image Library"
        )
        self.layout_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout_menu = QMenu(self.layout_button)
        self.preset_actions: dict[str, object] = {}

        page_menu = layout_menu.addMenu("Current page (placed images)")
        page_presets = [
            ("2v", "Two images vertically"),
            ("2h", "Two images horizontally"),
            ("4", "Four images (2x2)"),
            ("6", "Six images (3x2)"),
            ("fit", "Fit all placed images"),
        ]
        for key, label in page_presets:
            action = page_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, p=key: self._arrange_page(p)
            )
            self.preset_actions[key] = action
        page_menu.addSeparator()
        self.arrange_selected_action = page_menu.addAction("Arrange selected objects")
        self.arrange_selected_action.setToolTip(
            "Arrange only the images selected on the page canvas"
        )
        self.arrange_selected_action.triggered.connect(self._arrange_selected)
        custom_action = page_menu.addAction("Custom rows / columns...")
        custom_action.triggered.connect(self.open_layout_dialog)
        self.preset_actions["custom"] = custom_action

        library_menu = layout_menu.addMenu("Selected library images")
        self.add_selected_action = library_menu.addAction("Add to current page")
        self.add_selected_action.setToolTip(
            "Place only the images selected in the Image Library on the active page"
        )
        self.add_selected_action.triggered.connect(self._add_library_selection_to_page)
        library_menu.addSeparator()
        for key, label in [
            ("fit", "Add to new pages (fit on one page each run)"),
            ("1", "Add one image per new page"),
            ("2v", "Add two images vertically per new page"),
            ("2h", "Add two images horizontally per new page"),
            ("4", "Add four images (2x2) per new page"),
            ("6", "Add six images (3x2) per new page"),
        ]:
            action = library_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, p=key: self._add_library_selection_to_new_pages(p)
            )
            self.preset_actions[f"add_{key}"] = action
        library_menu.addSeparator()
        custom_library = library_menu.addAction(
            "Add to new pages (custom rows / columns)..."
        )
        custom_library.triggered.connect(self.add_library_selection_custom)
        self.preset_actions["add_custom"] = custom_library

        self.layout_button.setMenu(layout_menu)
        layout.addWidget(self.layout_button)

        # Group alignment for the images selected on the canvas.  Kept compact
        # (a dropdown) so the toolbar keeps fitting narrow windows.
        self.align_button = QToolButton()
        self.align_button.setText("Align")
        self.align_button.setObjectName("MenuToolButton")
        self.align_button.setToolTip(
            "Group alignment for the selected images: select two or more images "
            "on the page, then choose an edge. Rotated images are aligned by "
            "their visible outline."
        )
        self.align_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        align_menu = QMenu(self.align_button)
        self.align_actions: dict[str, object] = {}
        for key, label in (
            ("left", "Align left edges"),
            ("center", "Align centres (vertical)"),
            ("right", "Align right edges"),
            ("top", "Align top edges"),
            ("middle", "Align middles (horizontal)"),
            ("bottom", "Align bottom edges"),
        ):
            action = align_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, m=key: self._align(m))
            self.align_actions[key] = action
        align_menu.addSeparator()
        for key, label in (
            ("horizontal", "Distribute horizontally"),
            ("vertical", "Distribute vertically"),
        ):
            action = align_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, m=key: self._distribute(m)
            )
            self.align_actions[f"distribute_{key}"] = action
        align_menu.addSeparator()
        for key, label in (
            ("page_center_h", "Centre horizontally on the page"),
            ("page_center_v", "Centre vertically on the page"),
            ("page_center", "Centre on the page"),
        ):
            action = align_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, m=key: self._align(m))
            self.align_actions[key] = action
        self.align_button.setMenu(align_menu)
        self.align_button.setEnabled(False)
        layout.addWidget(self.align_button)
        self._update_align_actions()

        self.add_text_button = QToolButton()
        self.add_text_button.setText("Text")
        self.add_text_button.setToolTip("Add a label / text box to the page (Ctrl+T)")
        self.add_text_button.setAutoRaise(True)
        self.add_text_button.clicked.connect(self._add_text)
        layout.addWidget(self.add_text_button)

        layout.addStretch(1)

        self.fit_button = QToolButton()
        self.fit_button.setText("Fit page")
        self.fit_button.setAutoRaise(True)
        self.fit_button.clicked.connect(self._fit_page)
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setText("-")
        self.zoom_out_button.setAutoRaise(True)
        self.zoom_out_button.clicked.connect(self._zoom_out)
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setText("+")
        self.zoom_in_button.setAutoRaise(True)
        self.zoom_in_button.clicked.connect(self._zoom_in)
        self.zoom_value_label = QLabel("100%")
        self.zoom_value_label.setObjectName("Muted")
        self.zoom_value_label.setMinimumWidth(38)
        for widget in (
            self.fit_button,
            self.zoom_out_button,
            self.zoom_value_label,
            self.zoom_in_button,
        ):
            layout.addWidget(widget)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setToolTip("Preview the PDF before exporting it")
        self.preview_button.clicked.connect(self.request_preview.emit)
        layout.addWidget(self.preview_button)
        self._set_toolbar_enabled(False)
        return bar

    def _set_toolbar_enabled(self, enabled: bool) -> None:
        self.layout_button.setEnabled(enabled)
        for action in self.preset_actions.values():
            action.setEnabled(enabled)
        self.align_button.setEnabled(False if not enabled else bool(self.editor))
        if enabled:
            self._update_align_actions()
        self.original_size_toggle.setEnabled(True)
        self.add_text_button.setEnabled(enabled)
        self.fit_button.setEnabled(enabled)
        self.zoom_in_button.setEnabled(enabled)
        self.zoom_out_button.setEnabled(enabled)
        self.preview_button.setEnabled(enabled)

    # ---------------------------------------------------------------- lifecycle
    def load_workspace(self, workspace) -> None:
        """Attach an open project to the editing surface."""
        self.close_workspace()
        self.workspace = workspace
        self.organizer.set_fallback_dpi(
            float(self.context.settings.get("layout", "original_size_dpi", 300))
        )
        self.editor = LayoutEditor(workspace, self.context.settings, self)
        self.editor_holder_layout.addWidget(self.editor)
        self.empty_label.setVisible(False)
        self.organizer.set_workspace(workspace)
        self.navigator.attach_editor(self.editor)
        self.properties.attach_editor(self.editor)
        self.editor.status_message.connect(self.status_message.emit)
        self.editor.items_changed.connect(self.content_changed.emit)
        self.editor.pages_changed.connect(self.content_changed.emit)
        self.editor.zoom_changed.connect(self._on_zoom_changed)
        self.editor.selection_changed.connect(self._update_align_actions)
        self._set_toolbar_enabled(True)
        self.sync_original_size_toggle()
        # Fit once the widget really has a size - fitting now would give ~4 %.
        self.editor.request_zoom_fit()

    def close_workspace(self) -> None:
        if self.editor is not None:
            self.properties.attach_editor(None)
            self.navigator.attach_editor(None)
            self.editor.setParent(None)
            self.editor.deleteLater()
            self.editor = None
        self.workspace = None
        self.organizer.set_workspace(None)
        self.empty_label.setVisible(True)
        self._set_toolbar_enabled(False)
        self._update_align_actions()

    def refresh_all(self) -> None:
        if self.editor is not None:
            self.editor.update_scene_settings()
            self.editor.reload_pages()
            self.properties.refresh()
        self.sync_original_size_toggle()
        self.organizer.set_fallback_dpi(
            float(self.context.settings.get("layout", "original_size_dpi", 300))
        )
        self.organizer.set_workspace(self.workspace)
        self.organizer.refresh_list()

    def _on_documents_changed(self) -> None:
        """The Image Library changed: the undo history may reference it.

        Removing a library record deletes its page placements (foreign key
        cascade), so older undo entries would try to re-insert page items whose
        ``document_id`` no longer exists - an FK error inside the undo slot that
        left QUndoStack parked on a broken entry.  The removal dialog already
        says it cannot be undone, so the history is dropped here.
        """
        if self.editor is not None:
            had_history = self.editor.undo_stack.count() > 0
            self.editor.undo_stack.clear()
            self.editor._interaction_snapshot = {}
            self.editor.reload_current_page()
            if had_history:
                self.status_message.emit(
                    "Undo history cleared - the Image Library changed"
                )
        self.documents_changed.emit()
        self.content_changed.emit()

    def _on_zoom_changed(self, zoom: float) -> None:
        self.zoom_value_label.setText(f"{zoom * 100:.0f}%")

    # ------------------------------------------------------------------ queuing
    def _defer(self, callback, *args) -> None:
        """Run *callback* after the current input-synchronous call has returned.

        Windows refuses COM/OLE calls (message boxes, file dialogs, drag & drop,
        clipboard) made while it is still dispatching an input-synchronous
        message - such as the mouse release that activates a menu action - with
        ``RPC_E_CANTCALLOUT_ININPUTSYNCCALL`` (0x8001010d).  Qt turns that into a
        C++ exception and the process disappears without any dialog ("the window
        just vanished").  Queuing the real work to the next event-loop iteration
        keeps our dialogs, scene rebuilds and database writes out of that window.
        """
        QTimer.singleShot(0, lambda: callback(*args))

    def _on_original_size_toggled(self, checked: bool) -> None:
        if self.editor is not None:
            self.editor.set_place_original_size(checked)
        else:
            self.context.settings.set("layout", "place_original_size", bool(checked))
            self.context.settings.save()
        self.sync_original_size_toggle()

    def sync_original_size_toggle(self) -> None:
        """Keep the toolbar toggle and the organizer checkbox in sync."""
        value = bool(
            self.context.settings.get("layout", "place_original_size", True)
        )
        if self.original_size_toggle.isChecked() != value:
            self.original_size_toggle.blockSignals(True)
            self.original_size_toggle.setChecked(value)
            self.original_size_toggle.blockSignals(False)
        self.organizer.set_original_size(value)

    # ------------------------------------------------------------------ actions
    def _apply_preset(self, preset: str) -> None:
        if self.editor is None:
            return
        self.editor.apply_layout_preset(preset)

    def _layout_dialog_options(self):
        """Ask for rows/columns/spacing/... and return LayoutOptions or None."""
        base = LayoutOptions(
            rows=int(self.context.settings.get("layout", "default_rows", 2)),
            columns=int(self.context.settings.get("layout", "default_columns", 2)),
            h_spacing_mm=float(self.context.settings.get("layout", "grid_spacing_mm", 5.0)),
            v_spacing_mm=float(self.context.settings.get("layout", "grid_spacing_mm", 5.0)),
            fit_mode=str(self.context.settings.get("layout", "default_fit_mode", "contain")),
            auto_overflow=bool(self.context.settings.get("layout", "auto_overflow", True)),
        )
        dialog = LayoutDialog(base, self)
        if not dialog.exec():
            return None
        return dialog.options()

    def open_layout_dialog(self) -> None:
        """Custom grid for the images placed on the active page."""
        self._defer(self._run_layout_dialog)

    def _run_layout_dialog(self) -> None:
        if self.editor is None:
            return
        page = self.editor.current_page()
        if page is None:
            return
        options = self._layout_dialog_options()
        if options is None:
            return
        self.editor.arrange_current_page(options=options)
        self.organizer.refresh_placement_status()
        self.content_changed.emit()

    def open_import_dialog(self) -> None:
        if self.workspace is None:
            QMessageBox.information(
                self, "Import", "Open or create a project before importing documents."
            )
            return
        dialog = ImportDialog(self.workspace, self.context.settings, self)
        dialog.imported.connect(self._on_imported)
        dialog.exec()
        self.organizer.set_workspace(self.workspace)

    def _on_imported(self, document_ids: list) -> None:
        """Imported images become library records only.

        They are *not* placed on a page and no page is created: the user picks
        the images in the Image Library and adds them explicitly.
        """
        self.organizer.refresh_categories()
        self.organizer.reload()
        count = len(document_ids)
        self.status_message.emit(
            f"Imported {count} image(s) into the Image Library - select the ones "
            "you need and use 'Add to page'"
        )
        self.documents_changed.emit()
        self.content_changed.emit()

    def _selected_library_documents(self) -> list:
        """Source images currently selected in the Image Library."""
        if self.editor is None:
            return []
        library = self.editor.layout.documents()
        return [
            library[document_id]
            for document_id in self.organizer.selected_ids()
            if document_id in library
        ]

    def _usable_selected_documents(self) -> list:
        """Library selection whose source files still exist, with feedback.

        Filtering missing files silently produced the misleading "select the
        images you want to add first" message while the library selection was
        still visible - the user could not tell why nothing happened.
        """
        documents = self._selected_library_documents()
        if not documents:
            self.status_message.emit(
                "Select the images in the Image Library you want to add first"
            )
            return []
        missing = [doc.filename for doc in documents if not doc.exists]
        usable = [doc for doc in documents if doc.exists]
        if missing:
            QMessageBox.warning(
                self,
                "Missing source image",
                "These images cannot be added because their source file is "
                "missing:\n\n" + "\n".join(missing[:8])
                + "\n\nUse 'Reconnect missing files...' to locate them.",
            )
            if not usable:
                self.status_message.emit(
                    "The selected image(s) cannot be added: the source files are "
                    "missing"
                )
        return usable

    def _add_documents_to_page(self, document_ids: list) -> None:
        if self.editor is None or not document_ids:
            return
        documents = [
            doc
            for doc in self.editor.layout.documents().values()
            if doc.id in set(document_ids)
        ]
        missing = [doc.filename for doc in documents if not doc.exists]
        if missing:
            QMessageBox.warning(
                self,
                "Missing source image",
                "These documents cannot be placed because their source file is "
                "missing:\n\n" + "\n".join(missing[:8]),
            )
        usable = [doc for doc in documents if doc.exists]
        if usable:
            placed = self.editor.add_documents(usable)
            self.organizer.refresh_placement_status()
            self.content_changed.emit()
            self.status_message.emit(
                f"Added {placed} selected image(s) to page "
                f"{self.editor.current_page().page_number if self.editor.current_page() else '?'}"
            )

    def _add_documents_to_new_pages(self, document_ids: list) -> None:
        if self.editor is None or not document_ids:
            return
        library = self.editor.layout.documents()
        documents = [library[d] for d in document_ids if d in library and library[d].exists]
        if not documents:
            return
        self.editor.add_documents_to_new_pages(documents, preset="fit")
        self.organizer.refresh_placement_status()
        self.content_changed.emit()

    def _add_library_selection_to_page(self) -> None:
        self._defer(self._run_add_to_page, self.organizer.selected_ids())

    def _run_add_to_page(self, document_ids: list) -> None:
        self._add_documents_to_page(document_ids)

    def _add_library_selection_to_new_pages(self, preset: str) -> None:
        self._defer(self._run_add_to_new_pages, preset)

    def _run_add_to_new_pages(self, preset: str) -> None:
        if self.editor is None:
            return
        documents = self._usable_selected_documents()
        if not documents:
            return
        self.editor.add_documents_to_new_pages(documents, preset=preset)
        self.organizer.refresh_placement_status()
        self.content_changed.emit()

    def add_library_selection_custom(self) -> None:
        """Add the selected library images to new pages with a custom grid."""
        self._defer(self._run_add_custom)

    def _run_add_custom(self) -> None:
        if self.editor is None:
            return
        documents = self._usable_selected_documents()
        if not documents:
            return
        options = self._layout_dialog_options()
        if options is None:
            return
        self.editor.add_documents_to_new_pages(documents, options=options)
        self.organizer.refresh_placement_status()
        self.content_changed.emit()

    def _arrange_page(self, preset: str) -> None:
        self._defer(self._run_arrange_page, preset)

    def _run_arrange_page(self, preset: str) -> None:
        if self.editor is None:
            return
        self.editor.arrange_current_page(preset)
        self.organizer.refresh_placement_status()
        self.content_changed.emit()

    # ------------------------------------------------------------------ align
    def _align(self, mode: str) -> None:
        self._defer(self._run_align, mode)

    def _run_align(self, mode: str) -> None:
        if self.editor is None:
            return
        self.editor.align_selection(mode)
        self._update_align_actions()
        self.content_changed.emit()

    def _distribute(self, axis: str) -> None:
        self._defer(self._run_distribute, axis)

    def _run_distribute(self, axis: str) -> None:
        if self.editor is None:
            return
        self.editor.distribute_selection(axis)
        self._update_align_actions()
        self.content_changed.emit()

    def _update_align_actions(self) -> None:
        """Enable alignment for 2+ selected images, distribution for 3+."""
        count = len(self.editor.selected_ids()) if self.editor is not None else 0
        page_modes = ("page_center_h", "page_center_v", "page_center")
        for key, action in self.align_actions.items():
            if key.startswith("distribute_"):
                action.setEnabled(count >= 3)
            elif key in page_modes:
                action.setEnabled(count >= 1)
            else:
                action.setEnabled(count >= 2)
        if self.editor is None:
            self.align_button.setEnabled(False)
            return
        self.align_button.setEnabled(count >= 1)
        if count >= 2:
            self.align_button.setToolTip(
                f"Group alignment for the {count} selected images - rotated "
                "images are aligned by their visible outline"
            )
        elif count == 1:
            self.align_button.setToolTip(
                "Only page centring is available for a single image - select two "
                "or more images to align them to each other"
            )
        else:
            self.align_button.setToolTip(
                "Select two or more images on the page to align them"
            )

    def _arrange_selected(self) -> None:
        if self.editor is None:
            return
        self.editor.arrange_selected_items("fit")
        self.organizer.refresh_placement_status()
        self.content_changed.emit()

    def _undo(self) -> None:
        if self.editor is not None:
            self.editor.undo_stack.undo()

    def _redo(self) -> None:
        if self.editor is not None:
            self.editor.undo_stack.redo()

    def _fit_page(self) -> None:
        if self.editor is not None:
            self.editor.zoom_fit_page()

    def _add_text(self) -> None:
        if self.editor is not None:
            self.editor.add_label()

    def _zoom_in(self) -> None:
        if self.editor is not None:
            self.editor.zoom_in()

    def _zoom_out(self) -> None:
        if self.editor is not None:
            self.editor.zoom_out()


