"""Tests for Help > About and the creator credits.

The About box used to be a plain ``QMessageBox.about`` body, so the LinkedIn
profile could only be copied by hand. It is now a small dialog with a real
hyperlink (``QLabel.openExternalLinks``). The credits are shown *only* there:
the dashboard used to repeat the same line as a footer, which was removed on
request, so a regression test now guards against it coming back.
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

from core.paths import (  # noqa: E402
    APP_AUTHOR,
    APP_AUTHOR_LINKEDIN,
    APP_NAME,
    APP_VERSION,
)

pyqt_available = True
try:  # pragma: no cover - environment guard
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel
except Exception:  # pragma: no cover
    pyqt_available = False


@pytest.fixture(scope="module")
def qt_app():
    if not pyqt_available:  # pragma: no cover
        pytest.skip("PySide6 is not available")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def about(qt_app):
    from app.about_dialog import AboutDialog

    dialog = AboutDialog()
    yield dialog
    dialog.setParent(None)
    dialog.deleteLater()


def test_about_names_the_creator_and_the_version(about):
    joined = "\n".join(label.text() for label in about.findChildren(QLabel))
    assert APP_NAME in joined
    assert APP_VERSION in joined, "the About dialog must show the version"
    assert "Created by:" in joined
    assert APP_AUTHOR in joined
    assert "LinkedIn profile:" in joined
    assert APP_AUTHOR_LINKEDIN in joined


def test_linkedin_profile_is_a_clickable_link(about):
    label = about.credits_label
    assert about.windowTitle() == f"About {APP_NAME}"
    assert f'href="{APP_AUTHOR_LINKEDIN}"' in label.text()
    assert label.textFormat() == Qt.TextFormat.RichText
    assert label.openExternalLinks() is True, "the URL must open in the browser"
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByMouse


def test_credits_html_uses_the_shared_constants():
    from app.about_dialog import credits_html

    html = credits_html()
    assert APP_AUTHOR in html
    assert f'href="{APP_AUTHOR_LINKEDIN}"' in html
    # The visible link text is the URL itself, so it can also be copied.
    assert f">{APP_AUTHOR_LINKEDIN}</a>" in html


def test_help_about_opens_the_dialog_with_the_credits(qt_app, tmp_path, monkeypatch):
    from app.about_dialog import AboutDialog
    from app.context import AppContext
    from app.main_window import MainWindow
    from core.project_service import ProjectService
    from core.settings_service import SettingsService
    from database.app_database import AppDatabase

    settings = SettingsService(tmp_path / "about_settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp_path / "projects"))
    settings.set("general", "default_export_directory", str(tmp_path / "exports"))
    app_db = AppDatabase(tmp_path / "about_app.db")
    projects = ProjectService(app_db, settings)
    window = MainWindow(AppContext(settings=settings, app_db=app_db, projects=projects))

    shown: list[AboutDialog] = []

    def _exec(self) -> int:
        shown.append(self)
        return 0

    monkeypatch.setattr(AboutDialog, "exec", _exec)
    try:
        window.show_about()
        assert shown, "Help > About did not open a dialog"
        dialog = shown[0]
        assert APP_AUTHOR in dialog.credits_label.text()
        assert APP_AUTHOR_LINKEDIN in dialog.credits_label.text()
        assert dialog.parent() is window

        # The credits live in Help > About only. The dashboard used to repeat
        # them as a footer; that line was removed, so no page may render it.
        for page in (window.dashboard, window.projects_page, window.editor_workspace):
            for label in page.findChildren(QLabel):
                assert APP_AUTHOR not in label.text(), (
                    "the creator credits must not appear as a page footer"
                )
                assert APP_AUTHOR_LINKEDIN not in label.text()
    finally:
        window.setParent(None)
        window.deleteLater()
        app_db.close()
