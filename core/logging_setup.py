"""Centralised logging configuration.

Technical information is written to ``logs/application.log``.  Scanned document
contents, image data and personal information are never logged.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from core.paths import APP_NAME, logs_dir

_CONFIGURED = False
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"

#: Root logger name.  Derived from ``APP_NAME`` instead of being spelled out:
#: a hard-coded ``"document_pdf_builder"`` survived two renames and every log
#: line still carried the old product name.
LOGGER_NAME = APP_NAME


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root logging once and return the application logger."""
    global _CONFIGURED
    logger = logging.getLogger(LOGGER_NAME)
    if _CONFIGURED:
        return logger

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    try:
        log_path = logs_dir() / "application.log"
        file_handler = RotatingFileHandler(
            log_path, maxBytes=4 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - unwritable install location
        print("Warning: could not create the log file", file=sys.stderr)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    logging.getLogger("PIL").setLevel(logging.WARNING)
    _CONFIGURED = True
    logger.info("Logging initialised")
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
