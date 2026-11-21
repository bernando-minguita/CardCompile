"""Reconnect dialog: same-named candidates must be a choice, not a last-write-wins.

A scan folder can contain several files with the same name (a backup copy, a
duplicate in another sub-folder).  The dialog used to list one row per file and
reconnect *every* row, so the last file silently replaced the others and the
"Reconnected N document(s)" count counted candidates instead of documents.
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

try:  # pragma: no cover - environment guard
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    pytest.skip("PySide6 is not available", allow_module_level=True)

from core.document_service import DocumentService  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS  # noqa: E402
from tests.conftest import make_image  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def messages(monkeypatch):
    """Silence the modal boxes and collect their texts."""
    calls: list[str] = []

    class _Boxes:
        @staticmethod
        def information(parent=None, title="", text="", *args, **kwargs):
            calls.append(str(text))

        @staticmethod
        def warning(parent=None, title="", text="", *args, **kwargs):
            calls.append(str(text))

    monkeypatch.setattr("app.reconnect_dialog.QMessageBox", _Boxes)
    return calls


def _import_one(workspace, path: Path):
    service = ImportService(workspace)
    previews = service.analyze([path], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)[0]


def _make_missing(workspace, tmp_path: Path):
    source = make_image(tmp_path / "sources" / "scan.jpg", size=(600, 400), fmt="JPEG")
    document = _import_one(workspace, source)
    original = source.read_bytes()
    source.unlink()
    return document, original


def _open_dialog(workspace, folder: Path):
    from app.reconnect_dialog import ReconnectDialog

    dialog = ReconnectDialog(workspace)
    dialog.folder_edit.setText(str(folder))
    dialog._scan()
    return dialog


def test_same_named_copies_are_listed_once_and_reconnected_once(
    workspace, qt_app, tmp_path, messages
):
    document, original = _make_missing(workspace, tmp_path)
    folder = tmp_path / "backups"
    first = folder / "a" / document.filename
    second = folder / "b" / document.filename
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original)

    service = DocumentService(workspace)
    candidates = service.scan_folder_for_missing(folder)
    assert len(candidates) == 2, "both copies must stay available to choose from"
    assert all(candidate.hash_matches for candidate in candidates)

    dialog = _open_dialog(workspace, folder)
    try:
        assert dialog.table.rowCount() == 1, (
            f"one document produced {dialog.table.rowCount()} rows"
        )

        dialog._apply()

        assert dialog.reconnected == 1, (
            f"reported {dialog.reconnected} documents for a single document"
        )
        reloaded = workspace.database.documents.get(document.id)
        assert reloaded.source_path == str(first), (
            f"reconnected to {reloaded.source_path} instead of the chosen {first}"
        )
    finally:
        dialog.setParent(None)
        dialog.deleteLater()


def test_reconnecting_one_image_leaves_the_other_untouched(workspace, tmp_path, messages):
    """Two documents with the same file name must not be re-pointed together."""
    first_source = make_image(
        tmp_path / "one" / "scan.jpg", (600, 400), "JPEG", color=(200, 30, 30)
    )
    second_source = make_image(
        tmp_path / "two" / "scan.jpg", (300, 300), "JPEG", color=(30, 30, 200)
    )
    importer = ImportService(workspace)
    previews = importer.analyze(
        [first_source, second_source], list(SUPPORTED_IMAGE_EXTENSIONS)
    )
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    summary = importer.commit(previews, category.id)
    first = workspace.database.documents.get(summary.imported_ids[0])
    second = workspace.database.documents.get(summary.imported_ids[1])
    first_bytes = first_source.read_bytes()
    second_path = second.source_path
    second_hash = second.file_hash
    first_source.unlink()
    second_source.unlink()

    replacement = tmp_path / "backup" / "scan.jpg"
    replacement.parent.mkdir(parents=True, exist_ok=True)
    replacement.write_bytes(first_bytes)

    service = DocumentService(workspace)
    candidate = next(
        item
        for item in service.scan_folder_for_missing(tmp_path / "backup")
        if item.document.id == first.id
    )
    assert candidate.hash_matches is True
    assert service.reconnect(candidate, update_hash=False) is True

    reloaded_first = workspace.database.documents.get(first.id)
    reloaded_second = workspace.database.documents.get(second.id)
    assert reloaded_first.source_path == str(replacement)
    assert reloaded_second.source_path == second_path, "the other document moved"
    assert reloaded_second.file_hash == second_hash
    assert reloaded_second.exists is False

    wrong = service.validate_replacement(reloaded_second, replacement)
    assert wrong.hash_matches is False
    assert "Different file" in wrong.message


def test_the_candidate_can_be_chosen(workspace, qt_app, tmp_path, messages):
    document, original = _make_missing(workspace, tmp_path)
    folder = tmp_path / "backups"
    matching = folder / "a" / document.filename
    matching.parent.mkdir(parents=True, exist_ok=True)
    matching.write_bytes(original)
    different = make_image(
        folder / "b" / document.filename, size=(300, 300), fmt="JPEG"
    )

    dialog = _open_dialog(workspace, folder)
    try:
        assert dialog.table.rowCount() == 1
        assert dialog.table.cellWidget(0, 1) is None, (
            "a single allowed candidate needs no picker"
        )

        dialog.allow_mismatch.setChecked(True)  # rebuilds the table
        combo = dialog.table.cellWidget(0, 1)
        assert combo is not None, "two candidates must offer a choice"
        assert combo.count() == 2
        assert combo.currentIndex() == 0, "the checksum match must be preselected"

        combo.setCurrentIndex(1)
        dialog._apply()

        assert dialog.reconnected == 1
        reloaded = workspace.database.documents.get(document.id)
        assert reloaded.source_path == str(different), (
            f"the chosen file was ignored: {reloaded.source_path}"
        )
    finally:
        dialog.setParent(None)
        dialog.deleteLater()
