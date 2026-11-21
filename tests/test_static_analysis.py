"""Static guard: no module may reference an undefined name.

``QMessageBox`` was used three times in ``app/import_dialog.py`` without being
imported, so clicking *Import* committed the files and then died with a
``NameError`` - a bug no runtime test noticed because it lives in a dialog.
``pyflakes`` finds this class of mistake statically; only "undefined name"
messages fail here (unused imports and local variables stay warnings).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Directories that ship with the application.
PACKAGES = ("app", "core", "database", "graphics", "models", "tools")


def test_no_undefined_names():
    api = pytest.importorskip("pyflakes.api")
    reporter_module = pytest.importorskip("pyflakes.reporter")

    class Collector(reporter_module.Reporter):
        def __init__(self) -> None:
            super().__init__(io.StringIO(), io.StringIO())
            self.messages: list[str] = []

        def flake(self, message) -> None:  # noqa: D102 - pyflakes API
            self.messages.append(str(message))

        def unexpectedError(self, filename, msg) -> None:  # noqa: N802 - API
            self.messages.append(f"{filename}: {msg}")

        def syntaxError(  # noqa: N802 - API
            self, filename, msg, lineno, offset, text
        ) -> None:
            self.messages.append(f"{filename}:{lineno}: {msg}")

    collector = Collector()
    checked = 0
    for package in PACKAGES:
        path = ROOT / package
        if not path.is_dir():
            continue
        checked += len(list(path.rglob("*.py")))
        api.checkRecursive([str(path)], collector)

    assert checked > 0, "no source files were analysed"
    undefined = [message for message in collector.messages if "undefined name" in message]
    assert not undefined, (
        "These names are used but never defined or imported - each one is a "
        "NameError waiting for the right click:\n" + "\n".join(undefined)
    )


def test_logger_name_matches_the_application_name():
    """The logger must not carry a name from before a rename.

    ``core/logging_setup.py`` hard-coded ``"document_pdf_builder"``, which
    survived two product renames, so every log line still announced the old
    name at startup.  The name is now derived from ``APP_NAME``.
    """
    from core.logging_setup import LOGGER_NAME, get_logger
    from core.paths import APP_NAME

    assert LOGGER_NAME == APP_NAME
    assert get_logger("images").name == f"{APP_NAME}.images"
    assert "document_pdf_builder" not in LOGGER_NAME

