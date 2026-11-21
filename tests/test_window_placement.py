"""Startup placement: the main window is centred on the desktop.

``MainWindow`` used to be resized to 1500 x 950 and left where the window
manager put it. It now centres itself on the screen's *available* area (taskbar
excluded) when it is first shown, without changing its size - the editor is laid
out for that width. On a desktop smaller than the window the position is
clamped to the top-left corner so the title bar and menus stay reachable.
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
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    pyqt_available = False


class _FakeScreen:
    """Minimal screen stub: only the geometry queries the window uses."""

    def __init__(self, rect: "QRect") -> None:
        self._rect = rect

    def availableGeometry(self) -> "QRect":  # noqa: N802 - Qt API
        return self._rect

    def geometry(self) -> "QRect":
        return self._rect


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

    settings = SettingsService(tmp_path / "placement_settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp_path / "projects"))
    settings.set("general", "default_export_directory", str(tmp_path / "exports"))
    app_db = AppDatabase(tmp_path / "placement_app.db")
    projects = ProjectService(app_db, settings)
    context = AppContext(settings=settings, app_db=app_db, projects=projects)
    win = MainWindow(context)
    yield win
    win.hide()
    win.setParent(None)
    win.deleteLater()
    app_db.close()


def test_window_is_centred_on_a_large_screen(window):
    from app.main_window import STARTUP_SIZE

    screen = _FakeScreen(QRect(0, 0, 1920, 1080))
    window.screen = lambda: screen  # instance attribute shadows QWidget.screen()
    window.center_on_screen()

    frame = window.frameGeometry()
    assert (frame.width(), frame.height()) == (
        STARTUP_SIZE.width(),
        STARTUP_SIZE.height(),
    ), "centring must not change the size the editor is designed for"
    assert frame.left() == (1920 - frame.width()) // 2
    assert frame.top() == (1080 - frame.height()) // 2
    assert screen.availableGeometry().center() == frame.center()


def test_taskbar_area_is_respected(window):
    # 1366 x 768 laptop with a 40 px taskbar: the work area is 1366 x 728, so a
    # centred 1500 x 950 window would be off screen - it must stick to the corner.
    screen = _FakeScreen(QRect(0, 0, 1366, 728))
    window.screen = lambda: screen
    window.center_on_screen()

    frame = window.frameGeometry()
    assert frame.left() == 0, "the left edge must not be off screen"
    assert frame.top() == 0, "the title bar must not be above the screen"
    assert frame.width() == window.width(), "the window keeps its size"


def test_window_stays_reachable_on_a_small_screen(window):
    screen = _FakeScreen(QRect(0, 0, 1024, 600))
    window.screen = lambda: screen
    window.center_on_screen()

    frame = window.frameGeometry()
    assert frame.left() >= 0 and frame.top() >= 0
    assert frame.width() >= window.minimumWidth()
    assert frame.height() >= window.minimumHeight()


def test_showing_the_window_centres_it_only_once(window, qt_app):
    screen = _FakeScreen(QRect(0, 0, 1920, 1080))
    window.screen = lambda: screen
    window.show()
    qt_app.processEvents()
    centred = window.pos()
    assert centred.x() > 0 and centred.y() > 0

    # The user moves the window; hiding and showing it again must not undo that.
    window.move(QPoint(40, 30))
    window.hide()
    window.show()
    qt_app.processEvents()
    assert window.pos() == QPoint(40, 30)


def test_clamp_keeps_a_window_on_the_screen():
    from app.main_window import _clamp

    assert _clamp(50, 0, 100) == 50
    assert _clamp(-500, 0, 100) == 0
    assert _clamp(500, 0, 100) == 100
    # Window larger than the screen: the floor (screen edge) wins.
    assert _clamp(-50, 0, -200) == 0


def test_minimum_size_keeps_the_editor_toolbar_unclipped(window):
    """1100 x 700 squeezed Arrange/Align/Fit page to 40 px (real Windows fonts)."""
    assert window.minimumWidth() >= 1280
    assert window.minimumHeight() >= 720
