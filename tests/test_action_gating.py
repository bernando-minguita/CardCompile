"""Menu and toolbar actions follow the page that is actually visible.

``MainWindow._update_project_toolbar`` used to enable every project action as
soon as a project was open, without looking at which page was on screen.  With a
project open and the Dashboard (or the Projects page) showing, *Add page*,
*Delete page*, *Export PDF*, the zoom entries and the rotation entries were all
enabled: clicking one silently changed a project the user was not looking at -
"Add page" created a page nobody could see.

The canvas actions are now gated on the Editor page being the visible page, and
navigation re-evaluates them.  Project-wide actions (save, import, undo, the
clipboard) still only need an open project, because they work from any page.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.project_service import ProjectService  # noqa: E402
from core.settings_service import SettingsService  # noqa: E402
from database.app_database import AppDatabase  # noqa: E402

pyqt_available = True
try:  # pragma: no cover - environment guard
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    pyqt_available = False


@pytest.fixture(scope="module")
def qt_app():
    if not pyqt_available:  # pragma: no cover
        pytest.skip("PySide6 is not available")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qt_app, tmp_path):
    from app.context import AppContext
    from app.main_window import MainWindow

    settings = SettingsService(tmp_path / "settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp_path / "projects"))
    app_db = AppDatabase(tmp_path / "app_data.db")
    window = MainWindow(
        AppContext(
            settings=settings,
            app_db=app_db,
            projects=ProjectService(app_db, settings),
        )
    )
    try:
        yield window
    finally:
        window.setParent(None)
        window.deleteLater()
        app_db.close()


def _open_project(window) -> None:
    workspace = window.context.projects.create_project("Gating Project")
    window._attach_workspace(workspace)


def test_canvas_actions_are_disabled_while_the_editor_is_hidden(window):
    """A project open on the Dashboard must not offer canvas actions."""
    from app.main_window import NAV_DASHBOARD, NAV_EDITOR

    _open_project(window)
    # Opening a project lands on the Editor, where the canvas actions belong.
    assert window.stack.currentWidget() is window.editor_workspace
    assert window.action_add_page.isEnabled()

    window._select_nav(NAV_DASHBOARD)
    assert window.stack.currentWidget() is window.dashboard

    gated = (
        window.action_add_page,
        window.action_duplicate_page,
        window.action_delete_page,
        window.action_clear_page,
        window.action_landscape,
        window.action_rotate_180,
        window.action_add_text,
        window.action_zoom_fit,
        window.action_zoom_in,
        window.action_toggle_grid,
        window.action_export,
        window.action_preview,
    )
    still_enabled = [action.text() for action in gated if action.isEnabled()]
    assert not still_enabled, (
        "these canvas actions stayed enabled while the Editor was hidden: "
        f"{still_enabled}"
    )

    # Back on the Editor they come back.
    window._select_nav(NAV_EDITOR)
    assert window.action_add_page.isEnabled()
    assert window.action_export.isEnabled()


def test_project_actions_stay_available_from_any_page(window):
    """Save / import / clipboard work on the project, not on the visible page."""
    from app.main_window import NAV_DASHBOARD

    _open_project(window)
    window._select_nav(NAV_DASHBOARD)

    for action in (
        window.action_save,
        window.action_save_as,
        window.action_import,
        window.action_close,
        window.action_undo,
        window.action_copy,
        window.action_paste,
    ):
        assert action.isEnabled(), f"{action.text()} should work from the Dashboard"


def test_nothing_is_enabled_without_a_project(window):
    """No project: the toolbar is hidden and the project actions are disabled."""
    from app.main_window import NAV_DASHBOARD

    window._select_nav(NAV_DASHBOARD)
    assert window.workspace is None

    for action in window._project_actions + window._editor_actions:
        assert not action.isEnabled(), f"{action.text()} is enabled without a project"

    for shadow in window._toolbar_shadow_actions.values():
        assert not shadow.isVisible()


def test_alignment_actions_follow_the_visible_page(window):
    """Align/distribute are canvas actions, so they need the Editor on screen.

    They are separate QActions from ``EditorWorkspace.align_actions`` (the align
    button's own menu), so they are not selection-gated: with the Editor visible
    they stay enabled and a click with too few objects selected is a no-op.
    """
    from app.main_window import NAV_DASHBOARD

    _open_project(window)
    assert window.editor is not None

    alignment = (
        window.action_align_left,
        window.action_align_center,
        window.action_align_right,
        window.action_align_top,
        window.action_align_middle,
        window.action_align_bottom,
        window.action_center_page,
        window.action_distribute_h,
        window.action_distribute_v,
    )

    # Editor visible: available (the handler itself tolerates a small selection).
    for action in alignment:
        assert action.isEnabled(), f"{action.text()} should be available in the Editor"

    # Dashboard visible: not offered, because they act on a canvas nobody sees.
    window._select_nav(NAV_DASHBOARD)
    still_enabled = [action.text() for action in alignment if action.isEnabled()]
    assert not still_enabled, (
        "these alignment actions stayed enabled while the Editor was hidden: "
        f"{still_enabled}"
    )
