"""Main window: navigation, menus, toolbar, status bar and project lifecycle."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.about_dialog import AboutDialog
from app.context import AppContext
from app.dashboard import Dashboard
from app.editor_workspace import EditorWorkspace
from app.export_dialog import ExportDialog, open_path
from app.preview_window import PreviewWindow
from app.project_dialog import ProjectDialog
from app.projects_page import ProjectsPage
from app.reconnect_dialog import ReconnectDialog
from app.settings_dialog import SettingsDialog
from core.logging_setup import get_logger
from core.paths import (
    APP_NAME,
    PROJECT_DB_FILENAME,
    documents_root,
    resources_dir,
)
from core.project_service import ProjectServiceError, ProjectWorkspace
from core.recovery_service import clear_marker, pending_recovery, write_marker

LOGGER = get_logger("main_window")

NAV_DASHBOARD = 0
NAV_PROJECTS = 1
NAV_EDITOR = 2
#: Settings is an *action* row in the sidebar (it opens a dialog, not a page).
NAV_SETTINGS = 3

#: Sidebar entries. Only pages belong here: "New project" is an action and lives
#: in the toolbar, the File menu (Ctrl+N), the Dashboard and the Projects page -
#: listing it in the sidebar as well made it look like a fourth page.
NAV_LABELS = ("Dashboard", "Projects", "Editor", "Settings")

#: Delay before a failed autosave is retried (the file may be briefly locked).
AUTOSAVE_RETRY_MS = 30_000

#: Preferred startup size (the editor is designed for this width).
STARTUP_SIZE = QSize(1500, 950)


def _clamp(value: int, low: int, high: int) -> int:
    """Keep *value* between *low* and *high* (the floor wins if they overlap)."""
    return max(low, min(value, high))


class MainWindow(QMainWindow):
    """Top level window of CardCompile."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.workspace: ProjectWorkspace | None = None
        self.dirty = False
        self._preview_window: PreviewWindow | None = None
        self._preview_temp: str = ""
        #: True while a "dirty" recovery marker is on disk, so the marker is not
        #: rewritten on every property change.
        self._marker_dirty = False

        self.setWindowTitle(APP_NAME)
        icon_path = resources_dir() / "icons" / "app.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(STARTUP_SIZE)
        # 1100 px squeezed the layout toolbar (Arrange/Align/Fit page were
        # truncated to 40 px).  1280 x 720 is the smallest size measured with
        # every editor control at its full size.
        self.setMinimumSize(1280, 720)
        #: The window is centred once, when it is first shown (the frame size is
        #: only known after the platform has added the title bar and borders).
        self._centred = False

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self._autosave)

        self._build_central()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
        self._connect_pages()
        self.refresh_all()
        self._select_nav(NAV_DASHBOARD)

    # --------------------------------------------------------------------- UI
    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._centred:
            self._centred = True
            self.center_on_screen()

    def center_on_screen(self) -> None:
        """Put the window in the middle of the screen.

        The *available* geometry already excludes the taskbar, so the window is
        never centred behind it. The position is clamped to the top-left corner
        when the desktop is smaller than the window (small laptop screen, high
        display scaling): the window keeps its size - the editor needs the room -
        but its title bar and menus always stay reachable.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:  # pragma: no cover - headless session
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(
            _clamp(frame.left(), available.left(), available.right() - frame.width() + 1),
            _clamp(frame.top(), available.top(), available.bottom() - frame.height() + 1),
        )

    def _build_central(self) -> None:
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("Sidebar")
        self.nav.setFixedWidth(190)
        # Sidebar styling lives in app.qss (QListWidget#Sidebar). A local style
        # sheet would leak into the tooltips of its items and make them blank.
        for label in NAV_LABELS:
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(170, 40))
            self.nav.addItem(item)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.dashboard = Dashboard(self.context)
        self.projects_page = ProjectsPage(self.context)
        self.editor_workspace = EditorWorkspace(self.context)
        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.projects_page)
        self.stack.addWidget(self.editor_workspace)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

    def _connect_pages(self) -> None:
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.dashboard.new_project_requested.connect(self.new_project)
        self.dashboard.open_project_requested.connect(self.open_project_dialog)
        self.dashboard.open_recent_requested.connect(self.open_recent_project)
        self.dashboard.import_requested.connect(self.import_documents)
        self.dashboard.open_export_requested.connect(self._open_export_path)
        self.projects_page.open_requested.connect(self.open_recent_project)
        self.projects_page.locate_requested.connect(self.locate_project)
        self.projects_page.create_requested.connect(self.new_project)
        self.editor_workspace.status_message.connect(self.show_status_message)
        self.editor_workspace.content_changed.connect(self._on_content_changed)
        self.editor_workspace.request_preview.connect(self.preview_pdf)
        self.editor_workspace.documents_changed.connect(self._refresh_counts)

    def _make_action(self, text: str, slot, shortcut=None) -> QAction:
        item = QAction(text, self)
        if shortcut is not None:
            item.setShortcut(shortcut)
        item.triggered.connect(slot)
        return item

    def _build_actions(self) -> None:
        a = self._make_action
        self.action_new = a("New project", self.new_project, QKeySequence("Ctrl+N"))
        self.action_open = a("Open project", self.open_project_dialog, QKeySequence("Ctrl+O"))
        self.action_save = a("Save", self.save_project, QKeySequence("Ctrl+S"))
        self.action_save_as = a("Save as...", self.save_project_as, QKeySequence("Ctrl+Shift+S"))
        self.action_close = a("Close project", self.close_project)
        self.action_import = a("Import documents", self.import_documents)
        self.action_reconnect = a("Reconnect missing files...", self.reconnect_missing)
        self.action_rename = a("Rename project...", self.rename_project)
        self.action_settings = a("Settings...", self.open_settings)
        self.action_quit = a("Exit", self.close, QKeySequence("Ctrl+Q"))

        self.action_undo = a("Undo", self.undo, QKeySequence("Ctrl+Z"))
        self.action_redo = a("Redo", self.redo, QKeySequence("Ctrl+Y"))
        self.action_cut = a("Cut", self.cut, QKeySequence("Ctrl+X"))
        self.action_copy = a("Copy", self.copy, QKeySequence("Ctrl+C"))
        self.action_paste = a("Paste", self.paste, QKeySequence("Ctrl+V"))
        self.action_duplicate = a("Duplicate", self.duplicate, QKeySequence("Ctrl+D"))
        self.action_select_all = a("Select all", self.select_all, QKeySequence("Ctrl+A"))
        self.action_delete = a("Delete", self.delete_selection, QKeySequence("Del"))
        self.action_lock = a("Lock / unlock", self.toggle_lock)

        self._project_actions = [
            self.action_save,
            self.action_save_as,
            self.action_close,
            self.action_import,
            self.action_reconnect,
            self.action_rename,
            self.action_undo,
            self.action_redo,
            self.action_cut,
            self.action_copy,
            self.action_paste,
            self.action_duplicate,
            self.action_select_all,
            self.action_delete,
            self.action_lock,
        ]

        # Crop, reset crop, original/card/physical size and the layer order live
        # in the canvas context menu only.  "Make it card size" keeps its
        # documented Ctrl+Shift+K: an orphan QAction has no shortcut context, so
        # the shortcut is owned by the window instead.
        self.shortcut_card_size = QShortcut(QKeySequence("Ctrl+Shift+K"), self)
        self.shortcut_card_size.activated.connect(self.make_card_size)
        self.action_place_original = a(
            "Place new images at original size", self.toggle_place_original_size
        )
        self.action_place_original.setCheckable(True)
        self.action_place_original.setChecked(
            bool(self.context.settings.get("layout", "place_original_size", True))
        )
        self.action_rotate_180 = a("Rotate 180 degrees", lambda: self.rotate(180))
        self.action_rotate_custom = a("Custom rotation...", self.rotate_custom)
        self.action_add_text = a("Add text...", self.add_text, QKeySequence("Ctrl+T"))
        self.action_perspective = a(
            "Perspective Correction...",
            lambda: self.editor and self.editor.open_perspective_dialog(),
            QKeySequence("Ctrl+Shift+C"),
        )
        self.action_adjust = a(
            "Adjust lighting / tone...",
            lambda: self.editor and self.editor.open_perspective_dialog(),
        )

        self.action_align_left = a("Align left", lambda: self.align("left"))
        self.action_align_center = a("Align horizontal centre", lambda: self.align("center"))
        self.action_align_right = a("Align right", lambda: self.align("right"))
        self.action_align_top = a("Align top", lambda: self.align("top"))
        self.action_align_middle = a("Align vertical middle", lambda: self.align("middle"))
        self.action_align_bottom = a("Align bottom", lambda: self.align("bottom"))
        self.action_center_page = a("Center on page", lambda: self.align("page_center"))
        self.action_distribute_h = a(
            "Distribute horizontally", lambda: self.distribute("horizontal")
        )
        self.action_distribute_v = a(
            "Distribute vertically", lambda: self.distribute("vertical")
        )

        self.action_add_page = a("Add page", self.add_page)
        self.action_duplicate_page = a("Duplicate page", self.duplicate_page)
        self.action_delete_page = a("Delete page", self.delete_page)
        self.action_clear_page = a("Clear page", self.clear_page)
        self.action_move_page_up = a("Move page up", lambda: self.move_page(-1))
        self.action_move_page_down = a("Move page down", lambda: self.move_page(1))
        self.action_landscape = a("Toggle orientation", self.toggle_orientation)
        # "Arrange" is split by scope: page objects vs. selected library images.
        self.action_fit_all = a(
            "Fit all placed images on this page", lambda: self.apply_preset("fit")
        )
        self.action_arrange_selected = a(
            "Arrange selected page objects",
            lambda: self.editor and self.editor.arrange_selected_items("fit"),
        )
        self.action_arrange_v = a(
            "Two images vertically (this page)", lambda: self.apply_preset("2v")
        )
        self.action_arrange_h = a(
            "Two images horizontally (this page)", lambda: self.apply_preset("2h")
        )
        self.action_arrange_4 = a(
            "Four images 2x2 (this page)", lambda: self.apply_preset("4")
        )
        self.action_arrange_6 = a(
            "Six images 3x2 (this page)", lambda: self.apply_preset("6")
        )
        self.action_layout_dialog = a("Arrange current page (custom)...", self.open_layout_dialog)
        self.action_add_selected_page = a(
            "Add selected images to current page", self.add_selected_to_current_page
        )
        self.action_add_selected_new = a(
            "Add selected images to new pages", self.add_selected_to_new_pages
        )
        self.action_add_selected_custom = a(
            "Selected: custom grid on new pages...",
            self.add_selected_to_new_pages_custom,
        )

        self.action_export = a("Export...", self.export, QKeySequence("Ctrl+E"))
        self.action_preview = a("Preview PDF", self.preview_pdf)
        # These four add the *library selection* to newly created pages.
        self.action_layout_1 = a(
            "Selected: one image per new page", lambda: self.add_selected_to_new_pages("1")
        )
        self.action_layout_2v = a(
            "Selected: two images vertically per page",
            lambda: self.add_selected_to_new_pages("2v"),
        )
        self.action_layout_2h = a(
            "Selected: two images horizontally per page",
            lambda: self.add_selected_to_new_pages("2h"),
        )
        self.action_layout_4 = a(
            "Selected: four images 2x2 per page",
            lambda: self.add_selected_to_new_pages("4"),
        )
        self.action_layout_6 = a(
            "Selected: six images 3x2 per page",
            lambda: self.add_selected_to_new_pages("6"),
        )

        # All actions above this point (except New/Open/Settings/Quit) require a project.
        self._project_actions.extend([
            self.action_add_page,
            self.action_duplicate_page,
            self.action_delete_page,
            self.action_clear_page,
            self.action_move_page_up,
            self.action_move_page_down,
            self.action_landscape,
            self.action_fit_all,
            self.action_arrange_selected,
            self.action_arrange_v,
            self.action_arrange_h,
            self.action_arrange_4,
            self.action_arrange_6,
            self.action_layout_dialog,
            self.action_add_selected_page,
            self.action_add_selected_new,
            self.action_add_selected_custom,
            self.action_export,
            self.action_preview,
            self.action_layout_1,
            self.action_layout_2v,
            self.action_layout_2h,
            self.action_layout_4,
            self.action_layout_6,
            self.action_rotate_180,
            self.action_rotate_custom,
            self.action_add_text,
            self.action_place_original,
        ])
        # Alignment and distribution act on the canvas as well, so they belong to
        # the Editor-only group below.  Note that these are *separate* QActions
        # from EditorWorkspace.align_actions (the align button's own menu), so
        # they are not selection-gated today - clicking one with fewer than two
        # objects selected simply does nothing.

        #: Actions that need the Editor page to be the *visible* page, not just a
        #: project to be open.  They all act on the canvas of the current page, so
        #: offering them from the Dashboard or the Projects page invited a click
        #: that silently changed a project the user was not looking at - "Add
        #: page" created a page nobody could see.  _update_action_states() gates
        #: these on the active page.
        self._editor_actions = [
            self.action_add_page,
            self.action_duplicate_page,
            self.action_delete_page,
            self.action_clear_page,
            self.action_move_page_up,
            self.action_move_page_down,
            self.action_landscape,
            self.action_fit_all,
            self.action_arrange_selected,
            self.action_arrange_v,
            self.action_arrange_h,
            self.action_arrange_4,
            self.action_arrange_6,
            self.action_layout_dialog,
            self.action_add_selected_page,
            self.action_add_selected_new,
            self.action_add_selected_custom,
            self.action_layout_1,
            self.action_layout_2v,
            self.action_layout_2h,
            self.action_layout_4,
            self.action_layout_6,
            self.action_rotate_180,
            self.action_rotate_custom,
            self.action_add_text,
            self.action_perspective,
            self.action_adjust,
            self.action_align_left,
            self.action_align_center,
            self.action_align_right,
            self.action_align_top,
            self.action_align_middle,
            self.action_align_bottom,
            self.action_center_page,
            self.action_distribute_h,
            self.action_distribute_v,
        ]

        self.action_zoom_in = a("Zoom in", self.zoom_in, QKeySequence("Ctrl++"))
        self.action_zoom_out = a("Zoom out", self.zoom_out, QKeySequence("Ctrl+-"))
        self.action_zoom_fit = a("Fit page", self.zoom_fit_page, QKeySequence("Ctrl+0"))
        self.action_zoom_fit_width = a("Fit width", self.zoom_fit_width)
        self.action_zoom_50 = a("50%", lambda: self.set_zoom(0.5))
        self.action_zoom_75 = a("75%", lambda: self.set_zoom(0.75))
        self.action_zoom_100 = a("100%", lambda: self.set_zoom(1.0))
        self.action_zoom_150 = a("150%", lambda: self.set_zoom(1.5))
        self.action_zoom_200 = a("200%", lambda: self.set_zoom(2.0))
        self.action_toggle_grid = a("Show grid and snap", self.toggle_grid)
        self.action_toggle_grid.setCheckable(True)
        self.action_toggle_grid.setChecked(
            bool(self.context.settings.get("layout", "snap_to_grid", True))
        )

        # Zoom, grid and export act on the canvas of the visible page too, so
        # they join the Editor-only group once they exist.
        self._editor_actions.extend(
            [
                self.action_zoom_in,
                self.action_zoom_out,
                self.action_zoom_fit,
                self.action_zoom_fit_width,
                self.action_zoom_50,
                self.action_zoom_75,
                self.action_zoom_100,
                self.action_zoom_150,
                self.action_zoom_200,
                self.action_toggle_grid,
                self.action_export,
                self.action_preview,
            ]
        )

        self.action_about = a("About", self.show_about)
        self.action_guide = a("User guide", self.show_guide)

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        for item in (
            self.action_new,
            self.action_open,
            None,
            self.action_save,
            self.action_save_as,
            self.action_rename,
            self.action_close,
            None,
            self.action_import,
            self.action_reconnect,
            self.action_export,
            None,
            self.action_settings,
            self.action_quit,
        ):
            file_menu.addSeparator() if item is None else file_menu.addAction(item)

        edit_menu = menu_bar.addMenu("&Edit")
        for item in (
            self.action_undo,
            self.action_redo,
            None,
            self.action_cut,
            self.action_copy,
            self.action_paste,
            self.action_duplicate,
            self.action_delete,
            None,
            self.action_select_all,
            self.action_lock,
        ):
            edit_menu.addSeparator() if item is None else edit_menu.addAction(item)

        view_menu = menu_bar.addMenu("&View")
        for item in (
            self.action_zoom_fit,
            self.action_zoom_fit_width,
            self.action_zoom_50,
            self.action_zoom_75,
            self.action_zoom_100,
            self.action_zoom_150,
            self.action_zoom_200,
            self.action_zoom_in,
            self.action_zoom_out,
            None,
            self.action_toggle_grid,
            self.action_preview,
        ):
            view_menu.addSeparator() if item is None else view_menu.addAction(item)

        arrange_menu = menu_bar.addMenu("&Arrange")
        for item in (
            self.action_align_left,
            self.action_align_center,
            self.action_align_right,
            self.action_align_top,
            self.action_align_middle,
            self.action_align_bottom,
            self.action_center_page,
            None,
            self.action_distribute_h,
            self.action_distribute_v,
            None,
            self.action_rotate_180,
            self.action_rotate_custom,
            None,
            self.action_add_text,
            self.action_perspective,
            self.action_adjust,
            None,
            self.action_place_original,
            None,
            self.action_fit_all,
            self.action_arrange_selected,
            self.action_arrange_v,
            self.action_arrange_h,
            self.action_arrange_4,
            self.action_arrange_6,
            self.action_layout_dialog,
            None,
            self.action_add_selected_page,
            self.action_layout_1,
            self.action_layout_2v,
            self.action_layout_2h,
            self.action_layout_4,
            self.action_layout_6,
            self.action_add_selected_new,
            self.action_add_selected_custom,
        ):
            arrange_menu.addSeparator() if item is None else arrange_menu.addAction(item)

        page_menu = menu_bar.addMenu("&Page")
        for item in (
            self.action_add_page,
            self.action_duplicate_page,
            self.action_delete_page,
            self.action_clear_page,
            None,
            self.action_move_page_up,
            self.action_move_page_down,
            self.action_landscape,
        ):
            page_menu.addSeparator() if item is None else page_menu.addAction(item)

        export_menu = menu_bar.addMenu("E&xport")
        export_menu.addAction(self.action_export)
        export_menu.addAction(self.action_preview)

        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction(self.action_guide)
        help_menu.addAction(self.action_about)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(toolbar)
        toolbar.addAction(self.action_new)
        toolbar.addAction(self.action_open)
        toolbar.addSeparator()
        # Save / import / export / preview / undo / redo only make sense with a
        # project open, and their toolbar buttons must disappear while it is
        # closed.  Hiding the *shared* actions is not possible - the menu entries
        # must stay visible - and hiding the toolbar's widgets does not survive
        # show(): QToolBar re-shows every widget backed by a visible action,
        # whether it was added with addAction() or a QToolButton (both verified
        # empirically).  The toolbar therefore gets its own *shadow* actions,
        # bound to the shared ones: their visibility is ours alone, and a
        # changed() hook keeps text, tooltip and enabled state in step with the
        # shared action (undo/redo enable themselves when the stack changes).
        self._toolbar_shadow_actions: dict[QAction, QAction] = {}
        for action in (
            self.action_save,
            self.action_import,
            self.action_export,
            self.action_preview,
            self.action_undo,
            self.action_redo,
        ):
            shadow = QAction(action.text(), toolbar)
            shadow.setToolTip(action.toolTip())
            shadow.setStatusTip(action.statusTip())
            shadow.triggered.connect(action.trigger)
            action.changed.connect(
                lambda shared=action, mirror=shadow: self._sync_toolbar_action(
                    shared, mirror
                )
            )
            self._toolbar_shadow_actions[action] = shadow
            toolbar.addAction(shadow)
        toolbar.addSeparator()
        self.main_toolbar = toolbar
        self._update_action_states()

    @staticmethod
    def _sync_toolbar_action(shared: QAction, shadow: QAction) -> None:
        """Mirror a shared action's state onto its toolbar-only twin."""
        shadow.setText(shared.text())
        shadow.setToolTip(shared.toolTip())
        shadow.setStatusTip(shared.statusTip())
        shadow.setEnabled(shared.isEnabled())

    def _update_action_states(self) -> None:
        """Enable actions according to what is open *and* what is on screen.

        A project being open is not enough: the canvas actions act on the page
        the Editor is showing, so they stay disabled while the Dashboard or the
        Projects page is the visible page.  Navigation therefore calls this too.
        """
        has_project = self.workspace is not None
        editor_visible = has_project and self._editor_visible()
        for shadow in self._toolbar_shadow_actions.values():
            shadow.setVisible(has_project)
        # Hide the separators around the hidden group so no empty bars remain.
        for action in self.main_toolbar.actions():
            if action.isSeparator():
                action.setVisible(has_project)
        # Project-wide actions (save, import, undo, clipboard, ...) only need an
        # open project; they work from any page.
        for action in getattr(self, "_project_actions", []):
            action.setEnabled(has_project)
        # Canvas actions additionally need the Editor page to be visible.
        for action in getattr(self, "_editor_actions", []):
            action.setEnabled(editor_visible)

    def _editor_visible(self) -> bool:
        """True while the Editor page is the page the user is actually looking at."""
        return self.stack.currentWidget() is self.editor_workspace

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.status_project = QLabel("No project")
        self.status_save = QLabel("Ready")
        self.status_documents = QLabel("0 imported | 0 placed")
        self.status_pages = QLabel("0 pages")
        self.status_zoom = QLabel("100%")
        for widget in (
            self.status_project,
            self.status_save,
            self.status_documents,
            self.status_pages,
            self.status_zoom,
        ):
            bar.addPermanentWidget(widget)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setVisible(False)
        bar.addPermanentWidget(self.progress)

    # -------------------------------------------------------------- navigation
    def _on_nav_changed(self, row: int) -> None:
        if row == NAV_SETTINGS:
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(NAV_EDITOR if self.workspace else NAV_DASHBOARD)
            self.nav.blockSignals(False)
            self.open_settings()
            return
        if row == NAV_EDITOR and not self.workspace:
            self._open_untitled_editor()
            return
        self.stack.setCurrentIndex(row)
        if row == NAV_DASHBOARD:
            self.dashboard.refresh()
        elif row == NAV_PROJECTS:
            self.projects_page.refresh()
        # The canvas actions follow the visible page, so switching pages has to
        # re-evaluate them (they are disabled while the Editor is not shown).
        self._update_action_states()

    def _select_nav(self, row: int) -> None:
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(row)
        self.nav.blockSignals(False)
        self.stack.setCurrentIndex(row)
        self._update_action_states()

    # -------------------------------------------------------- project lifecycle
    def new_project(self) -> None:
        dialog = ProjectDialog(
            self.context.settings,
            str(self.context.projects.default_project_folder()),
            self.context.projects,
            parent=self,
        )
        if not dialog.exec():
            return
        values = dialog.values()
        try:
            workspace = self.context.projects.create_project(
                name=values["name"],
                description=values["description"],
                folder=values["folder"] or None,
                page_size=values["page_size"],
                orientation=values["orientation"],
                custom_width_mm=values["custom_width_mm"],
                custom_height_mm=values["custom_height_mm"],
                margin_mm=values["margin_mm"],
            )
        except ProjectServiceError as exc:
            QMessageBox.warning(self, "New project", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("Could not create the project")
            QMessageBox.critical(self, "New project", f"The project could not be created:\n{exc}")
            return
        self._attach_workspace(workspace)
        self.show_status_message(f"Project '{workspace.name}' created")

    def open_project_dialog(self) -> None:
        start = str(self.context.projects.default_project_folder())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open a CardCompile project",
            start,
            f"Project database ({PROJECT_DB_FILENAME});;SQLite database (*.db);;All files (*.*)",
        )
        if path:
            self._open_db_path(Path(path))

    def open_recent_project(self, project_id: str) -> None:
        known = self.context.projects.known_project(project_id)
        try:
            workspace = self.context.projects.open_recent(project_id)
        except ProjectServiceError as exc:
            if known is not None and not known.exists:
                self._offer_missing_project(known)
            else:
                QMessageBox.warning(self, "Open project", str(exc))
                self.projects_page.refresh()
                self.dashboard.refresh()
            return
        self._attach_workspace(workspace)

    # -------------------------------------------------- missing project recovery
    def _offer_missing_project(self, known) -> None:
        """Let the user find a moved project instead of hitting a dead end."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Project file missing")
        box.setText(f"The project '{known.name}' could not be found.")
        box.setInformativeText(
            f"{known.db_path}\n\n"
            "The project folder was moved, renamed or deleted. You can point the "
            "application at the project.db in its new location."
        )
        locate = box.addButton("Locate project file...", QMessageBox.ButtonRole.AcceptRole)
        remove = box.addButton(
            "Remove from list", QMessageBox.ButtonRole.DestructiveRole
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is locate:
            self._locate_project(known)
        elif clicked is remove:
            self.context.projects.forget_project(known.id)
            self.show_status_message(f"Removed '{known.name}' from the list")
            self.projects_page.refresh()
            self.dashboard.refresh()

    def _locate_project(self, known) -> None:
        start = str(Path(known.db_path).parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Locate the project file of '{known.name}'",
            start,
            f"Project database ({PROJECT_DB_FILENAME});;SQLite database (*.db);;All files (*.*)",
        )
        if not path:
            return
        try:
            workspace = self.context.projects.relink_project(known.id, path)
        except ProjectServiceError as exc:
            QMessageBox.warning(self, "Locate project", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("Could not relink the project")
            QMessageBox.critical(self, "Locate project", str(exc))
            return
        self._attach_workspace(workspace)
        self.show_status_message(f"Project '{workspace.name}' reopened")

    def locate_project(self, project_id: str) -> None:
        """Called by the projects page 'Locate...' button."""
        known = self.context.projects.known_project(project_id)
        if known is None:
            self.show_status_message("This project is not in the list")
            return
        self._locate_project(known)

    def _save_settings(self) -> bool:
        """Persist the settings and tell the user when that is impossible."""
        if self.context.settings.save():
            return True
        self.show_status_message(
            "Settings could not be written - check the permissions of "
            f"{self.context.settings.path}"
        )
        return False

    def warn_about_unsafe_project_folder(self) -> None:
        """Warn (once) when projects are stored inside the application folder."""
        unsafe = self.context.projects.unsafe_project_folder()
        if unsafe is None:
            return
        safe = documents_root() / "Projects"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Project folder inside the application folder")
        box.setText("Projects are currently saved in a folder that an update or "
                    "rebuild of the application deletes:")
        box.setInformativeText(
            f"{unsafe}\n\n"
            f"The safe location is:\n{safe}\n\n"
            "Switching to the safe folder does not move existing projects - use "
            "File > Open project to open one from the old folder once."
        )
        use_safe = box.addButton("Use the safe folder", QMessageBox.ButtonRole.AcceptRole)
        keep = box.addButton(
            "Keep my folder (don't ask again)", QMessageBox.ButtonRole.DestructiveRole
        )
        box.setDefaultButton(use_safe)
        box.exec()
        clicked = box.clickedButton()
        if clicked is use_safe:
            self.context.settings.update_section(
                "general",
                {
                    "default_project_directory": str(safe),
                    "allow_unsafe_project_folder": False,
                },
            )
            self._save_settings()
            self.show_status_message(f"New projects will be saved in {safe}")
            LOGGER.info("Switched the project folder to %s", safe)
        elif clicked is keep:
            self.context.settings.set("general", "allow_unsafe_project_folder", True)
            self._save_settings()
            LOGGER.warning("User kept the unsafe project folder %s", unsafe)
            self.show_status_message(
                "Keeping the current project folder - it may be deleted by an update"
            )

    def startup_prompts(self) -> None:
        """Run the startup questions one after another (never overlapping)."""
        try:
            self.warn_about_unsafe_project_folder()
        except Exception:  # pragma: no cover - a prompt must never block startup
            LOGGER.exception("Project folder warning failed")
        self.offer_recovery()

    def _open_db_path(self, db_path: Path) -> None:
        try:
            workspace = self.context.projects.open_project(db_path)
        except ProjectServiceError as exc:
            QMessageBox.warning(self, "Open project", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("Could not open the project")
            QMessageBox.critical(self, "Open project", f"The project could not be opened:\n{exc}")
            return
        self._attach_workspace(workspace)

    # ------------------------------------------------------------- workspace glue
    def _open_untitled_editor(self) -> None:
        """Open a scratch (untitled) session so editing can start immediately.

        The workspace is a real project (database, thumbnails, undo history)
        stored in a throwaway ``.untitled`` folder.  It is never registered in
        the application database, so it never appears in the recent-projects
        list.  ``save_project``/``save_project_as`` promotes it to a normal,
        named project the first time the user saves.
        """
        try:
            workspace = self.context.projects.create_untitled_workspace()
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("Could not start an untitled editing session")
            QMessageBox.critical(
                self, "New editing session", f"Could not start editing:\n{exc}"
            )
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(NAV_DASHBOARD)
            self.nav.blockSignals(False)
            self.stack.setCurrentWidget(self.dashboard)
            self._update_action_states()
            return
        self._attach_workspace(workspace)
        self.show_status_message("Untitled editing session - use Save to name it")

    def _attach_workspace(self, workspace: ProjectWorkspace) -> None:
        if self.workspace is not None and self.workspace is not workspace:
            try:
                self.workspace.close()
            except Exception:  # pragma: no cover - defensive
                LOGGER.debug("Previous workspace close failed")
        self.workspace = workspace
        self.context.set_workspace(workspace)
        self.editor_workspace.load_workspace(workspace)
        self.projects_page.set_active_workspace(workspace)
        self.dirty = False
        self.status_save.setText("Saved")
        self._update_action_states()
        self._select_nav(NAV_EDITOR)
        self._refresh_counts()
        self.status_project.setText(f"Project: {workspace.name}")
        self.setWindowTitle(f"{APP_NAME} - {workspace.name}")
        write_marker(str(workspace.db_path), workspace.name, dirty=False)
        self._marker_dirty = False
        self.dashboard.refresh()
        self.projects_page.refresh()

    def close_project(self) -> None:
        if self.workspace is None:
            return
        self._autosave()
        name = self.workspace.name
        clear_marker()
        self._marker_dirty = False
        try:
            self.workspace.close()
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Error while closing the project")
        # A scratch (untitled) session lives in a throwaway folder: remove it
        # entirely so nothing is left behind when the user closes without saving.
        if self.workspace.untitled:
            try:
                self.context.projects.discard_untitled_workspace(self.workspace)
            except Exception:  # pragma: no cover - defensive
                LOGGER.exception("Error while discarding the untitled workspace")
        self.workspace = None
        self.context.clear_workspace()
        self.editor_workspace.close_workspace()
        self.projects_page.set_active_workspace(None)
        self.dirty = False
        self.status_project.setText("No project")
        self.status_save.setText("Ready")
        self.status_documents.setText("0 imported | 0 placed")
        self.status_pages.setText("0 pages")
        self.setWindowTitle(APP_NAME)
        self._update_action_states()
        self._select_nav(NAV_DASHBOARD)
        self.dashboard.refresh()
        self.projects_page.refresh()
        self.show_status_message(f"Project '{name}' closed")

    def save_project(self, confirm: bool = True) -> bool:
        if self.workspace is None:
            self.show_status_message("There is no open project to save")
            return False
        # An untitled (scratch) session has no name or folder yet: promote it
        # through Save As instead of checkpointing in place.
        if self.workspace.untitled:
            return self.save_project_as()
        try:
            self.workspace.touch()
            self.workspace.checkpoint()
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.critical(
                self, "Save project", f"The project could not be saved:\n{exc}"
            )
            return False
        self.dirty = False
        self.status_save.setText("Saved")
        write_marker(str(self.workspace.db_path), self.workspace.name, dirty=False)
        self._marker_dirty = False
        self.show_status_message("Project saved")
        if confirm:
            QMessageBox.information(
                self,
                "Project saved",
                f"The project '{self.workspace.name}' was saved.\n\n"
                f"{self.workspace.db_path}\n\n"
                "All changes are written to the project database immediately, so "
                "nothing is lost if the application closes unexpectedly.",
            )
        return True

    def save_project_as(self) -> bool:
        if self.workspace is None:
            self.show_status_message("There is no open project to save")
            return False
        default_name = self.workspace.name
        if self.workspace.untitled:
            default_name = "Untitled"
        new_name, ok = QInputDialog.getText(
            self,
            "Save project as",
            "New project name",
            text=default_name,
        )
        if not ok or not new_name.strip():
            return False
        try:
            workspace = self.context.projects.duplicate_project(
                self.workspace, new_name.strip()
            )
        except ProjectServiceError as exc:
            QMessageBox.warning(self, "Save as", str(exc))
            return False
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("Save as failed")
            QMessageBox.critical(self, "Save as", str(exc))
            return False
        self._attach_workspace(workspace)
        self.show_status_message(f"Project saved as '{workspace.name}'")
        return True

    def rename_project(self) -> None:
        if self.workspace is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename project", "Project name", text=self.workspace.name
        )
        if not ok or not new_name.strip():
            return
        try:
            self.context.projects.rename_project(self.workspace, new_name.strip())
        except ProjectServiceError as exc:
            QMessageBox.warning(self, "Rename project", str(exc))
            return
        self.status_project.setText(f"Project: {self.workspace.name}")
        self.setWindowTitle(f"{APP_NAME} - {self.workspace.name}")
        self.dashboard.refresh()
        self.projects_page.refresh()
        self.show_status_message("Project renamed")

    def import_documents(self) -> None:
        if self.workspace is None:
            QMessageBox.information(
                self, "Import documents", "Open or create a project before importing."
            )
            return
        if self.stack.currentWidget() is not self.editor_workspace:
            self._select_nav(NAV_EDITOR)
        self.editor_workspace.open_import_dialog()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.context.settings, self.workspace, self)
        if dialog.exec():
            if self.workspace is not None:
                self.editor_workspace.refresh_all()
            self.show_status_message("Settings saved")

    def reconnect_missing(self) -> None:
        if self.workspace is None:
            QMessageBox.information(
                self,
                "Reconnect missing files",
                "Open a project first.",
            )
            return
        dialog = ReconnectDialog(self.workspace, self)
        dialog.exec()
        if self.editor is not None:
            self.editor_workspace.organizer.reload()
            self.editor.reload_current_page()
        if dialog.reconnected:
            self.show_status_message(
                f"Reconnected {dialog.reconnected} missing source file(s)"
            )
            self._on_content_changed()

    def _on_content_changed(self) -> None:
        self.dirty = True
        self.status_save.setText("Unsaved changes")
        self._refresh_counts()
        if self.workspace is not None and not self._marker_dirty:
            write_marker(str(self.workspace.db_path), self.workspace.name, dirty=True)
            self._marker_dirty = True
        self._schedule_autosave()

    def _schedule_autosave(self) -> None:
        """(Re-)arm the autosave timer with the configured interval."""
        interval = int(
            self.context.settings.get("general", "autosave_interval_seconds", 60)
        )
        if interval <= 0:
            # 0 disables autosave completely.
            self.autosave_timer.stop()
            return
        self.autosave_timer.start(min(interval, 3600) * 1000)

    def _autosave(self) -> None:
        if self.workspace is None or not self.dirty:
            return
        try:
            self.workspace.touch()
            self.workspace.checkpoint()
            self.dirty = False
            self.status_save.setText("Saved")
            write_marker(str(self.workspace.db_path), self.workspace.name, dirty=False)
            self._marker_dirty = False
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Auto-save failed")
            self.status_save.setText("Auto-save failed - retrying")
            # The single-shot timer only re-armed on the next edit, so a transient
            # failure (file briefly locked) left the work unsaved - retry instead.
            self.autosave_timer.start(AUTOSAVE_RETRY_MS)

    def _refresh_counts(self) -> None:
        if self.workspace is None:
            self.status_documents.setText("0 imported | 0 placed")
            self.status_pages.setText("0 pages")
            return
        counts = self.editor.layout.library_counts() if self.editor is not None else {
            "imported": self.workspace.document_service.document_count(),
            "placed": 0,
        }
        pages = self.workspace.document_service.page_count()
        self.status_documents.setText(
            f"{counts['imported']} imported | {counts['placed']} placed"
        )
        self.status_documents.setToolTip(
            f"{counts['imported']} image(s) in the Image Library, "
            f"{counts['placed']} used on a page"
        )
        self.status_pages.setText(f"{pages} pages")

    def refresh_all(self) -> None:
        self.dashboard.refresh()
        self.projects_page.refresh()
        self.status_zoom.setText(self.editor_workspace.zoom_value_label.text())
        self.action_place_original.setChecked(
            bool(self.context.settings.get("layout", "place_original_size", True))
        )
        self.editor_workspace.sync_original_size_toggle()

    # ------------------------------------------------------------ editor actions
    @property
    def editor(self):
        return self.editor_workspace.editor

    def _require_editor(self) -> bool:
        if self.editor is None:
            self.show_status_message("Open a project to edit pages")
            return False
        return True

    def undo(self) -> None:
        if self._require_editor():
            self.editor.undo_stack.undo()

    def redo(self) -> None:
        if self._require_editor():
            self.editor.undo_stack.redo()

    def cut(self) -> None:
        if self._require_editor():
            self.editor.cut_selection()

    def copy(self) -> None:
        if self._require_editor():
            self.editor.copy_selection()

    def paste(self) -> None:
        if not self._require_editor():
            return
        if self.editor.paste_clipboard():
            return
        self._paste_clipboard_image()

    def _paste_clipboard_image(self) -> None:
        """Ctrl+V with a bitmap: import it into the library and place it.

        A screenshot (Snipping Tool) or a card copied out of another program
        sits on the system clipboard as an image, not as the page objects this
        application copies.  It is saved as a PNG, imported through the normal
        pipeline and placed on the current page.
        """
        from core.clipboard_paste import import_clipboard_image

        image = QApplication.clipboard().image()
        if image.isNull():
            self.show_status_message("Nothing to paste")
            return
        if self.workspace is None:
            return
        organizer = self.editor_workspace.organizer
        category_id = organizer.category_filter.currentData()
        document = import_clipboard_image(
            self.workspace, image, category_id=category_id
        )
        if document is None:
            self.show_status_message("The pasted image could not be imported")
            return
        self.editor.add_documents([document])
        organizer.reload()
        self._refresh_counts()
        self.show_status_message(
            f'Pasted "{document.filename}" into the library and the page'
        )

    def duplicate(self) -> None:
        if self._require_editor():
            self.editor.duplicate_selection()

    def _canvas_has_focus(self) -> bool:
        """False only while the organizer list or its search box has the focus.

        Delete / Ctrl+A must act on the document cards there - everywhere else
        (canvas, toolbars, menus, panels) they apply to the page elements.
        """
        widget = QApplication.focusWidget()
        if widget is None or self.editor is None:
            return True
        try:
            if widget is self.editor or self.editor.isAncestorOf(widget):
                return True
            organizer = self.editor_workspace.organizer
            if widget is organizer.list or organizer.list.isAncestorOf(widget):
                return False
            if widget is organizer.search_edit or organizer.search_edit.isAncestorOf(widget):
                return False
        except RuntimeError:  # pragma: no cover - widget already destroyed
            return True
        return True

    def delete_selection(self) -> None:
        if not self._require_editor():
            return
        if not self._canvas_has_focus():
            self.show_status_message(
                "Click on the page first - Del only deletes page elements"
            )
            return
        self.editor.delete_selection()

    def select_all(self) -> None:
        if not self._require_editor():
            return
        if not self._canvas_has_focus():
            # The organizer has its own selection (Ctrl+A selects cards there).
            self.show_status_message("Select all applies to the page elements")
            return
        self.editor.select_all()

    def toggle_lock(self) -> None:
        if self._require_editor():
            self.editor.toggle_lock_selection()

    def make_card_size(self) -> None:
        """Give the selected images the printed size of a real ID-1 card."""
        if self._require_editor():
            self.editor.set_selection_card_size()

    def add_text(self) -> None:
        """Add a label / text box to the current page."""
        if self._require_editor():
            self.editor.add_label()

    def toggle_place_original_size(self) -> None:
        enabled = self.action_place_original.isChecked()
        self.context.settings.set("layout", "place_original_size", enabled)
        self._save_settings()
        self.editor_workspace.sync_original_size_toggle()
        self.show_status_message(
            "New images keep their original physical size"
            if enabled
            else "New images are fitted to the printable area"
        )

    def rotate(self, degrees: float) -> None:
        if self._require_editor():
            self.editor.rotate_selection(degrees)

    def rotate_custom(self) -> None:
        if not self._require_editor():
            return
        value, ok = QInputDialog.getDouble(
            self, "Custom rotation", "Angle (degrees)", 0.0, -180.0, 180.0, 1
        )
        if ok:
            self.editor.set_rotation(value)

    def align(self, mode: str) -> None:
        if not self._require_editor():
            return
        QTimer.singleShot(0, lambda: self._run_align(mode))

    def _run_align(self, mode: str) -> None:
        if self.editor is None:
            return
        self.editor.align_selection(mode)
        self.editor_workspace._update_align_actions()

    def distribute(self, axis: str) -> None:
        if not self._require_editor():
            return
        QTimer.singleShot(0, lambda: self._run_distribute(axis))

    def _run_distribute(self, axis: str) -> None:
        if self.editor is None:
            return
        self.editor.distribute_selection(axis)
        self.editor_workspace._update_align_actions()

    def add_page(self) -> None:
        if self._require_editor():
            self.editor.add_page()

    def duplicate_page(self) -> None:
        if self._require_editor():
            self.editor.duplicate_current_page()

    def delete_page(self) -> None:
        if self._require_editor():
            self.editor.delete_current_page()

    def clear_page(self) -> None:
        if self._require_editor():
            self.editor.clear_current_page()

    def move_page(self, delta: int) -> None:
        if self._require_editor():
            self.editor.move_current_page(delta)

    def toggle_orientation(self) -> None:
        if not self._require_editor():
            return
        self.editor.toggle_current_page_orientation()

    def apply_preset(self, preset: str) -> None:
        """Arrange the images already placed on the active page."""
        if not self._require_editor():
            return
        # Queued: never rebuild the page inside the menu's input-synchronous
        # call (Windows raises 0x8001010d for COM work there and Qt aborts).
        QTimer.singleShot(0, lambda: self._run_arrange(preset))

    def _run_arrange(self, preset: str) -> None:
        if self.editor is None:
            return
        self.editor.arrange_current_page(preset)
        self._refresh_counts()

    def add_selected_to_current_page(self) -> None:
        """Place the Image Library selection on the active page."""
        if not self._require_editor():
            return
        QTimer.singleShot(0, self._run_add_selected_to_page)

    def _run_add_selected_to_page(self) -> None:
        self.editor_workspace._run_add_to_page(
            self.editor_workspace.organizer.selected_ids()
        )
        self._refresh_counts()

    def add_selected_to_new_pages(self, preset: str = "fit") -> None:
        """Place the Image Library selection on freshly created pages."""
        if not self._require_editor():
            return
        QTimer.singleShot(0, lambda: self._run_add_selected_to_new_pages(preset))

    def _run_add_selected_to_new_pages(self, preset: str) -> None:
        self.editor_workspace._run_add_to_new_pages(preset)
        self._refresh_counts()

    def add_selected_to_new_pages_custom(self) -> None:
        """Same as above but with a custom rows/columns dialog."""
        if not self._require_editor():
            return
        QTimer.singleShot(0, self._run_add_selected_to_new_pages_custom)

    def _run_add_selected_to_new_pages_custom(self) -> None:
        self.editor_workspace._run_add_custom()
        self._refresh_counts()

    def open_layout_dialog(self) -> None:
        self.editor_workspace.open_layout_dialog()

    def zoom_in(self) -> None:
        if self._require_editor():
            self.editor.zoom_in()

    def zoom_out(self) -> None:
        if self._require_editor():
            self.editor.zoom_out()

    def zoom_fit_page(self) -> None:
        if self._require_editor():
            self.editor.zoom_fit_page()

    def zoom_fit_width(self) -> None:
        if self._require_editor():
            self.editor.zoom_fit_width()

    def set_zoom(self, zoom: float) -> None:
        if self._require_editor():
            self.editor.set_zoom(zoom)

    def toggle_grid(self) -> None:
        enabled = self.action_toggle_grid.isChecked()
        self.context.settings.set("layout", "snap_to_grid", enabled)
        self._save_settings()
        if self.editor is not None:
            self.editor.update_scene_settings()

    # ---------------------------------------------------------- export / preview
    def export(self) -> None:
        if not self._require_editor():
            return
        dialog = ExportDialog(
            self.editor.layout,
            self.context.settings,
            self.editor.current_page_id,
            self,
        )
        # The export worker reads the project database on its own thread through
        # the shared connection.  Pausing autosave keeps a write commit from
        # happening in the middle of that read (and the timer is re-armed below).
        self.autosave_timer.stop()
        try:
            exported = dialog.exec()
        finally:
            if self.dirty:
                self._schedule_autosave()
        if exported:
            if dialog.exported_paths:
                self.context.last_export_path = dialog.exported_paths[0]
            self.dashboard.refresh()
            fmt = dialog.format_combo.currentText().lower()
            self.show_status_message(f"{fmt.upper()} exported and validated")
            if dialog.exported_paths:
                answer = QMessageBox.question(
                    self,
                    "Export finished",
                    f"The {fmt.upper()} file was created successfully.\n\nOpen it now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    open_path(dialog.exported_paths[0])

    def _new_preview_path(self) -> str:
        """Reserve a temporary PDF path for the preview (handle already closed)."""
        from core.pdf_service import create_temp_pdf_path

        path = create_temp_pdf_path("dpb_preview_")
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - fall back to using the file as is
            LOGGER.debug("Could not pre-delete the preview file %s", path)
        return str(path)

    def preview_pdf(self) -> None:
        if not self._require_editor():
            return
        if not self.editor.layout.pages():
            QMessageBox.information(self, "Preview", "The project has no pages yet.")
            return
        temp_name = self._new_preview_path()
        pages_total = max(1, len(self.editor.layout.pages()))
        self.progress.setRange(0, pages_total)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)

        def report(index: int, total: int) -> None:
            # Keep the window painting instead of freezing until the export ends.
            self.progress.setMaximum(max(1, total))
            self.progress.setValue(index)
            QApplication.processEvents()

        try:
            from core.pdf_service import ExportOptions, PdfService

            options = ExportOptions(
                quality=str(self.context.settings.get("pdf", "default_dpi", "High")),
                compress=True,
                progress=report,
            )
            PdfService(self.editor.layout).export_project(temp_name, options)
        except Exception as exc:
            Path(temp_name).unlink(missing_ok=True)
            LOGGER.exception("Preview export failed")
            QMessageBox.critical(
                self, "Preview", f"The preview could not be created:\n{exc}"
            )
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.progress.setVisible(False)

        if self._preview_window is None:
            self._preview_window = PreviewWindow(self)
            self._preview_window.destroyed.connect(self._cleanup_preview)
        if self._preview_temp and self._preview_temp != temp_name:
            Path(self._preview_temp).unlink(missing_ok=True)
        self._preview_temp = temp_name
        start_page = 0
        if self.editor is not None:
            pages = self.editor.layout.pages()
            current_id = self.editor.current_page_id
            for index, page in enumerate(pages):
                if page.id == current_id:
                    start_page = index
                    break
        if not self._preview_window.load_pdf(temp_name, start_page=start_page):
            QMessageBox.warning(self, "Preview", "The generated PDF could not be opened.")
            return
        self._preview_window.show()
        self._preview_window.raise_()
        self._preview_window.activateWindow()

    def _cleanup_preview(self, *_args) -> None:
        self._preview_window = None
        if self._preview_temp:
            Path(self._preview_temp).unlink(missing_ok=True)
            self._preview_temp = ""

    def _open_export_path(self, path: str) -> None:
        if path and Path(path).is_file():
            open_path(path)
        else:
            QMessageBox.information(
                self, "Open PDF", "The exported file is no longer available."
            )

    def show_status_message(self, message: str, timeout: int = 6000) -> None:
        self.statusBar().showMessage(message, timeout)

    def show_about(self) -> None:
        dialog = AboutDialog(self)
        dialog.exec()

    def show_guide(self) -> None:
        QMessageBox.information(
            self,
            "User guide",
            "1. Create a project (File > New project) and choose the page size.\n\n"
            "2. Import scans with File > Import documents - files, folders or drag "
            "and drop. Originals are never modified.\n\n"
            "3. Drag documents from the organizer onto the page or select them and "
            "click 'Add to page'. Use the automatic layout presets for quick grids.\n\n"
            "4. Edit freely: drag to move, corner handles to resize, the round handle "
            "to rotate, the Crop button for an interactive crop. Everything can be "
            "undone with Ctrl+Z.\n\n"
            "5. Add pages and change paper size or orientation in the Page tab of the "
            "properties panel.\n\n"
            "6. Export with Ctrl+E. Choose quality, an optional password and the scope. "
            "The PDF is validated automatically and can be opened afterwards.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.workspace is not None and self.dirty:
            answer = QMessageBox.question(
                self,
                "Unsaved changes",
                "The project has unsaved changes.\nSave them before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                if not self.save_project(confirm=False):
                    # The failure was already reported: keep the window (and the
                    # recovery marker) so the work is not silently thrown away.
                    event.ignore()
                    return
            elif self.workspace.untitled:
                # Discarding a scratch session: delete its throwaway folder.
                try:
                    self.context.projects.discard_untitled_workspace(
                        self.workspace
                    )
                except Exception:  # pragma: no cover - defensive
                    LOGGER.exception(
                        "Error while discarding the untitled workspace"
                    )
                self.workspace = None
                self.context.clear_workspace()
                self.editor_workspace.close_workspace()
                self.projects_page.set_active_workspace(None)
                self.dirty = False
                self.status_project.setText("No project")
                self.status_save.setText("Ready")
                self.status_documents.setText("0 imported | 0 placed")
                self.status_pages.setText("0 pages")
                self.setWindowTitle(APP_NAME)
                self._update_action_states()
                self._select_nav(NAV_DASHBOARD)
                self.dashboard.refresh()
                self.projects_page.refresh()
        if self.workspace is not None:
            try:
                self.workspace.close()
            except Exception:  # pragma: no cover - defensive
                LOGGER.debug("Workspace close on exit failed")
        clear_marker()
        self._cleanup_preview()
        super().closeEvent(event)

    # ------------------------------------------------------------------ recovery
    def offer_recovery(self) -> None:
        """Offer to reopen a project left open in a previous session."""
        marker = pending_recovery()
        if marker and not bool(marker.get("dirty")):
            # Nothing was unsaved: no recovery needed, fall back to the normal
            # "reopen the last project" question.
            clear_marker()
            marker = None
        if not marker:
            if bool(self.context.settings.get("general", "reopen_last_project", True)):
                last = self.context.projects.last_project()
                if last is not None and last.exists:
                    answer = QMessageBox.question(
                        self,
                        "Reopen last project",
                        f"Reopen the last project '{last.name}'?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes,
                    )
                    if answer == QMessageBox.StandardButton.Yes:
                        self.open_recent_project(last.id)
            return
        answer = QMessageBox.question(
            self,
            "Recover project",
            f"The project '{marker.get('project_name', '')}' was still open when the "
            "application closed unexpectedly.\n\nReopen it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._open_db_path(Path(str(marker.get("db_path", ""))))
        else:
            clear_marker()








