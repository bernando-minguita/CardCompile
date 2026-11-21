"""Help > About: what the application does, the version and who built it.

The creator credits are shown as rich text because the LinkedIn profile must be
a real, clickable link (*QLabel* with ``openExternalLinks``) - the plain
``QMessageBox.about`` body only ever allowed copying the URL by hand.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QVBoxLayout,
)

from core.paths import (
    APP_AUTHOR,
    APP_AUTHOR_LINKEDIN,
    APP_NAME,
    APP_VERSION,
)

TECH_STACK = "Python 3.14 - PySide6 - Pillow - PyMuPDF - SQLite"


def credits_html() -> str:
    """Rich text with the creator name and the clickable LinkedIn profile."""
    return (
        "<p>Created by: <b>{author}</b><br>"
        'LinkedIn profile: <a href="{link}">{link}</a></p>'
    ).format(author=APP_AUTHOR, link=APP_AUTHOR_LINKEDIN)


class AboutDialog(QDialog):
    """Version, purpose, privacy note, technology stack and creator credits."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setModal(True)
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        title.setWordWrap(True)
        self.version_label = QLabel(f"Version {APP_VERSION}")
        self.version_label.setObjectName("Muted")
        layout.addWidget(title)
        layout.addWidget(self.version_label)

        purpose = QLabel(
            "Arrange scanned Iqama, Driving License, Istimara, Passport and other "
            "documents on PDF pages and export one combined PDF."
        )
        purpose.setWordWrap(True)
        layout.addWidget(purpose)

        privacy = QLabel(
            "All processing happens locally on this computer. No cloud services, "
            "no telemetry and no automatic data extraction are used."
        )
        privacy.setObjectName("Muted")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        stack = QLabel(TECH_STACK)
        stack.setObjectName("Muted")
        stack.setWordWrap(True)
        layout.addWidget(stack)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        # openExternalLinks opens the profile in the default browser; without it
        # the URL would only be selectable text.
        self.credits_label = QLabel(credits_html())
        self.credits_label.setObjectName("Credits")
        self.credits_label.setWordWrap(True)
        self.credits_label.setTextFormat(Qt.TextFormat.RichText)
        self.credits_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.credits_label.setOpenExternalLinks(True)
        layout.addWidget(self.credits_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Close")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
