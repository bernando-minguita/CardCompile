"""Paste a bitmap from the system clipboard into the Image Library.

Ctrl+V used to mean "paste the page objects copied inside the application"
only: the clipboard was a Python list of item rows.  A screenshot or a card
copied out of another program lives on the system clipboard as a bitmap and
never reached the project at all.

A bitmap has no path, and the project stores source images by path (never as
blobs), so the pasted picture is written to a real PNG file first, imported
through the normal import pipeline and returned as a ``Document``.  The file is
kept outside the project folder so renaming or copying a project never touches
it, exactly like any other imported scan.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.import_service import ImportService
from core.logging_setup import get_logger
from core.paths import documents_root
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS

LOGGER = get_logger("clipboard")

#: Folder (inside the user's Documents) that collects pasted bitmaps.
PASTE_FOLDER_NAME = "Pasted images"


def paste_folder() -> Path:
    """Folder where clipboard bitmaps are written, created on demand."""
    folder = documents_root() / PASTE_FOLDER_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_clipboard_image(image, folder: Path | str | None = None) -> Path | None:
    """Write a ``QImage`` from the system clipboard to a PNG and return the path.

    Returns ``None`` for an empty clipboard or a write failure, so the caller
    can fall back to the existing "paste copied page objects" behaviour.
    """
    if image is None or image.isNull():
        return None
    target_dir = Path(folder) if folder is not None else paste_folder()
    target_dir.mkdir(parents=True, exist_ok=True)
    # Microseconds keep two quick pastes from overwriting each other; the
    # format is readable in Explorer so a pasted card can be found again.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = target_dir / f"pasted-{stamp}.png"
    if not image.save(str(path), "PNG"):
        LOGGER.warning("Could not save the clipboard image to %s", path)
        return None
    return path


def import_clipboard_image(
    workspace,
    image,
    folder: Path | str | None = None,
    category_id: str | None = None,
):
    """Save *image*, import it into the project library and return the ``Document``.

    The import goes through :class:`ImportService`, so the pasted picture gets
    the same hash, size, DPI and thumbnail handling as a file picked with
    *Import documents*.  Nothing is placed on a page here - the caller decides
    where the new library image goes.
    """
    path = save_clipboard_image(image, folder)
    if path is None:
        return None
    service = ImportService(workspace)
    previews = service.analyze([path], list(SUPPORTED_IMAGE_EXTENSIONS))
    if not previews:
        LOGGER.warning("The pasted image could not be analysed: %s", path)
        return None
    summary = service.commit(previews, category_id)
    if not summary.imported_ids:
        LOGGER.warning("The pasted image was not imported: %s", path)
        return None
    return workspace.database.documents.get(summary.imported_ids[0])
