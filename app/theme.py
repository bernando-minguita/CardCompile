"""Light theme: palette constants and the application style sheet."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
ACCENT_SOFT = "#E8F0FE"
SURFACE = "#FFFFFF"
CANVAS = "#F4F5F7"
BORDER = "#DCE0E6"
BORDER_STRONG = "#C3C9D2"
TEXT = "#1F2933"
TEXT_MUTED = "#667085"
SUCCESS = "#12805C"
WARNING = "#B54708"
DANGER = "#B42318"

STYLE_SHEET = f"""
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI", "Inter", Arial, sans-serif;
    font-size: 10pt;
}}
QMainWindow, QDialog {{
    background: {CANVAS};
}}
QFrame#Panel, QWidget#Panel {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QWidget#Sidebar {{
    background: {SURFACE};
    border-right: 1px solid {BORDER};
}}
QListWidget#Sidebar {{
    border: none;
    background: {SURFACE};
    padding-top: 10px;
}}
QListWidget#Sidebar::item {{
    padding: 11px 14px;
    border-radius: 6px;
    margin: 2px 8px;
}}
QListWidget#Sidebar::item:selected {{
    background: {ACCENT_SOFT};
    color: {ACCENT_HOVER};
    font-weight: 600;
}}
QWidget#LayoutToolbar {{
    background: {SURFACE};
    border-bottom: 1px solid {BORDER};
}}
QWidget#RulerCorner {{
    background: #FBFCFD;
    border: 1px solid {BORDER};
}}
QScrollArea#PreviewScroll {{
    background: #DDE0E5;
    border: 1px solid #C6CAD2;
    border-radius: 6px;
}}
QLabel#PreviewPage {{
    background: {SURFACE};
    color: {TEXT_MUTED};
    border: 1px solid #C6CAD2;
}}
QLabel#Title {{
    font-size: 17pt;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#Subtitle {{
    font-size: 9pt;
    color: {TEXT_MUTED};
}}
QLabel#SectionTitle {{
    font-size: 10pt;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#Metric {{
    font-size: 22pt;
    font-weight: 600;
    color: {ACCENT};
}}
QLabel#Muted {{
    color: {TEXT_MUTED};
}}
QToolBar {{
    background: {SURFACE};
    border-bottom: 1px solid {BORDER};
    spacing: 4px;
    padding: 3px 6px;
}}
QToolBar QToolButton {{
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 9px;
    color: {TEXT};
}}
QToolBar QToolButton:hover {{
    background: {ACCENT_SOFT};
    border-color: {BORDER};
}}
QToolBar QToolButton:checked {{
    background: {ACCENT_SOFT};
    border-color: {ACCENT};
}}
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    padding: 6px 14px;
    min-height: 18px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
    color: {ACCENT_HOVER};
}}
QPushButton:pressed {{
    background: {ACCENT_SOFT};
}}
QPushButton:disabled {{
    color: #9AA3AF;
    border-color: {BORDER};
    background: #FAFBFC;
}}
QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
    color: #FFFFFF;
}}
QPushButton#Danger {{
    color: {DANGER};
    border-color: #F3C0BB;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 7px;
    selection-background-color: {ACCENT};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    width: 18px;
    border: none;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    outline: none;
}}
QListWidget, QTreeWidget, QTableWidget, QTableView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    alternate-background-color: #FAFBFC;
    gridline-color: #EDEFF3;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    outline: none;
}}
QListWidget::item, QTreeWidget::item, QTableWidget::item {{
    padding: 4px 6px;
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QHeaderView::section {{
    background: #F7F8FA;
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid #EDEFF3;
    padding: 6px;
    font-weight: 600;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: {SURFACE};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    border: 1px solid transparent;
    padding: 7px 14px;
    margin-right: 2px;
    color: {TEXT_MUTED};
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
    background: {SURFACE};
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QStatusBar {{
    background: {SURFACE};
    border-top: 1px solid {BORDER};
    color: {TEXT_MUTED};
}}
QStatusBar QLabel {{
    color: {TEXT_MUTED};
    padding: 0 8px;
}}
QSplitter::handle {{
    background: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #C6CCD6;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #A8B0BD;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #C6CCD6;
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0;
}}
QProgressBar {{
    background: #EDEFF3;
    border: none;
    border-radius: 5px;
    text-align: center;
    height: 8px;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}
QMenuBar {{
    background: {SURFACE};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item {{
    padding: 6px 10px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background: {ACCENT_SOFT};
    border-radius: 5px;
}}
QMenu {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 26px 6px 22px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 8px;
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    background: {SURFACE};
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT_MUTED};
}}
QCheckBox, QRadioButton {{
    spacing: 7px;
}}
QToolTip {{
    background: #1F2933;
    color: #FFFFFF;
    border: 1px solid #0B1220;
    padding: 5px 7px;
    border-radius: 5px;
}}
QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background: #F7F8FA;
    padding: 6px;
    border-bottom: 1px solid {BORDER};
}}
"""


def build_palette() -> QPalette:
    """Explicit light palette so the OS dark mode never leaks in."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(CANVAS))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#FAFBFC"))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1F2933"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    palette.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    return palette


def _load_stylesheet() -> str:
    """Load the QSS from ``resources/styles/app.qss`` when available."""
    try:
        from core.paths import resources_dir

        for candidate in (
            resources_dir() / "styles" / "app.qss",
            resources_dir() / "app.qss",
        ):
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
    except Exception:  # pragma: no cover - fallback to the inline sheet
        pass
    return STYLE_SHEET


def apply_theme(app: QApplication) -> None:
    """Apply the Fusion style, the light palette and the style sheet."""
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(_load_stylesheet())
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

