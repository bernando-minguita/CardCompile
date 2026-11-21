"""Regression tests for the fourth review pass: the duplicated "New project".

The sidebar is a page switcher (Dashboard, Projects, Editor) plus the Settings
action, but it also carried a "New project" row that ran the *action* - so the
command appeared twice (toolbar + sidebar) and the sidebar highlighted a "page"
that does not exist.
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

    settings = SettingsService(tmp_path / "nav_settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp_path / "projects"))
    settings.set("general", "default_export_directory", str(tmp_path / "exports"))
    app_db = AppDatabase(tmp_path / "nav_app.db")
    projects = ProjectService(app_db, settings)
    context = AppContext(settings=settings, app_db=app_db, projects=projects)
    window = MainWindow(context)
    workspace = projects.create_project("Navigation Project")
    window._attach_workspace(workspace)
    yield window
    window.close_project()
    window.setParent(None)
    window.deleteLater()
    app_db.close()


def test_sidebar_lists_pages_without_a_new_project_row(window):
    from app.main_window import NAV_LABELS

    labels = [window.nav.item(row).text() for row in range(window.nav.count())]
    assert labels == list(NAV_LABELS)
    assert "New project" not in labels, "the action is listed as a page again"
    assert len(labels) == window.stack.count() + 1, "every nav row must map to a page"


def test_new_project_is_still_reachable_without_the_sidebar_row(window):
    action = window.action_new
    assert action.text() == "New project"
    assert action.shortcut().toString() == "Ctrl+N"
    # The two pages that offer the workflow carry their own button.
    assert window.dashboard.new_button.text() == "New project"
    assert window.projects_page.new_button.text() == "New project"


def test_settings_row_opens_the_dialog_and_keeps_the_current_page(window, monkeypatch):
    from app.main_window import NAV_EDITOR, NAV_SETTINGS

    opened: list[bool] = []
    monkeypatch.setattr(window, "open_settings", lambda: opened.append(True))

    window._select_nav(NAV_EDITOR)
    window.nav.setCurrentRow(NAV_SETTINGS)

    assert opened == [True], "the Settings row did not open the dialog"
    assert window.nav.currentRow() == NAV_EDITOR, "the sidebar jumped to a fake page"
    assert window.stack.currentWidget() is window.editor_workspace
