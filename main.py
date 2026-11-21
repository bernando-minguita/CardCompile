"""CardCompile - application entry point.

Start the application with:

    py -3.14 main.py

Dependencies (PySide6, Pillow, PyMuPDF) are installed in the default
site-packages of the Python 3.14 interpreter - see requirements.txt.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def bootstrap_import_path() -> None:
    """Make the workspace importable.

    Dependencies come from the interpreter's normal site-packages, so no extra
    package directory is added here.
    """
    entry = str(PROJECT_ROOT)
    if entry not in sys.path:
        sys.path.insert(0, entry)


bootstrap_import_path()


def main() -> int:
    try:
        from app.application import run_application
    except ImportError as exc:  # pragma: no cover - dependency guard
        message = (
            "CardCompile could not start because a dependency is "
            "missing.\n\n"
            f"Details: {exc}\n\n"
            "Install the dependencies with:\n"
            "    py -3.14 -m pip install PySide6 Pillow PyMuPDF"
        )
        print(message, file=sys.stderr)
        return 2
    return run_application(sys.argv)


if __name__ == "__main__":
    sys.exit(main())

