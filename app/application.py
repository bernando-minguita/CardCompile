"""Application bootstrap: logging, theme, services, main window and error handling."""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import traceback

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app.context import AppContext
from app.main_window import MainWindow
from app.theme import apply_theme
from core.logging_setup import get_logger, setup_logging
from core.paths import (
    APP_NAME,
    APP_SLUG,
    APP_VERSION,
    ORG_NAME,
    resources_dir,
    logs_dir,
    migrate_legacy_locations,
)
from core.project_service import ProjectService
from core.settings_service import SettingsService
from database.app_database import AppDatabase

LOGGER = get_logger("application")

#: Kept alive for the lifetime of the process - faulthandler writes to it.
_CRASH_STREAM = None


def _install_crash_logging() -> None:
    """Write a native traceback to ``logs/crash.log`` when the process dies.

    A crash inside the Qt/C++ layer (or a hard abort) never reaches
    ``sys.excepthook``: the process simply disappears and the application log
    ends mid-sentence.  ``faulthandler`` records the C stack for those cases so
    the next report can be diagnosed instead of guessed at.
    """
    global _CRASH_STREAM
    try:
        path = logs_dir() / "crash.log"
        _CRASH_STREAM = open(path, "a", buffering=1, encoding="utf-8")
    except OSError:  # pragma: no cover - unwritable install location
        return
    _CRASH_STREAM.write(
        f"\n=== {APP_NAME} {APP_VERSION} starting (pid {os.getpid()}) ===\n"
    )
    _CRASH_STREAM.flush()
    faulthandler.enable(file=_CRASH_STREAM, all_threads=True)


def _log_thread_exception(args) -> None:
    """Worker threads used to die silently when something went wrong."""
    if args.exc_type is SystemExit:
        return
    LOGGER.error(
        "Unhandled exception in thread %s: %s",
        getattr(args.thread, "name", "?"),
        "".join(
            traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback
            )
        ),
    )


def _install_exception_hook() -> None:
    """Show a friendly dialog for unexpected errors and log the traceback."""

    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        LOGGER.critical(
            "Unhandled exception: %s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        QMessageBox.critical(
            None,
            f"{APP_NAME} - unexpected error",
            "An unexpected error occurred.\n\n"
            f"{exc_type.__name__}: {exc_value}\n\n"
            "The details were written to logs/application.log. The original "
            "scanned files are never modified by this application.",
        )

    sys.excepthook = hook
    threading.excepthook = _log_thread_exception


def build_context(settings: SettingsService) -> AppContext:
    app_db = AppDatabase()
    projects = ProjectService(app_db, settings)
    return AppContext(settings=settings, app_db=app_db, projects=projects)


def _migrate_legacy_locations() -> None:
    """Move data from a pre-rename install, logging what happened.

    Runs before logging is configured, so failures are silent by design: a
    migration that cannot complete must never stop the application from
    starting.  The folders are simply left where they are in that case.
    """
    try:
        migrated = migrate_legacy_locations()
    except Exception:  # pragma: no cover - migration must never block startup
        return
    if not migrated:
        return
    try:
        LOGGER.info("Migrated data from %s", ", ".join(migrated))
    except Exception:  # pragma: no cover - logging not ready yet
        pass


def run_application(argv: list[str]) -> int:
    # Move data written under the previous application name before anything
    # reads a path: logging, settings and the project registry all live inside
    # the folders this migrates.
    _migrate_legacy_locations()
    setup_logging()
    _install_crash_logging()
    LOGGER.info("Starting %s %s", APP_NAME, APP_VERSION)

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, False)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setDesktopFileName(APP_SLUG)
    icon_path = resources_dir() / "icons" / "app.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_theme(app)
    _install_exception_hook()

    settings = SettingsService()
    settings.load()
    context = build_context(settings)
    window = MainWindow(context)
    window.show()

    # Startup questions run one after another, never as overlapping dialogs.
    QTimer.singleShot(150, window.startup_prompts)

    exit_code = app.exec()
    LOGGER.info("Exiting with code %s", exit_code)
    try:
        context.app_db.close()
    except Exception:  # pragma: no cover - defensive
        LOGGER.debug("Application database close failed")
    return exit_code
