"""Regression tests for the import dialog's commit path.

Clicking **Import** raised ``NameError: name 'QMessageBox' is not defined``:
the dialog used ``QMessageBox`` in three places (duplicate question, failure
report, completion note) but the import list only pulled in the widgets it drew
with. The images *were* committed before the crash, so the user saw a success
report replaced by an "unexpected error" dialog.

These tests drive ``ImportDialog._commit()`` directly - the path the rest of the
suite never touched, because the other import tests call ``ImportService`` and
skip the dialog.
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

from core.import_service import ImportService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS  # noqa: E402
from tests.conftest import make_image  # noqa: E402

pyqt_available = True
try:  # pragma: no cover - environment guard
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
except Exception:  # pragma: no cover
    pyqt_available = False


@pytest.fixture(scope="module")
def qt_app():
    if not pyqt_available:  # pragma: no cover
        pytest.skip("PySide6 is not available")
    app = QApplication.instance() or QApplication([])
    yield app


class _StubMessageBox:
    """Stands in for the modal dialogs and remembers what was shown."""

    StandardButton = QMessageBox.StandardButton
    answer = QMessageBox.StandardButton.Yes
    calls: list[tuple[str, str]] = []

    @classmethod
    def _record(cls, kind: str, args: tuple) -> None:
        title = args[1] if len(args) > 1 else ""
        cls.calls.append((kind, str(title)))

    @classmethod
    def question(cls, *args, **kwargs):
        cls._record("question", args)
        return cls.answer

    @classmethod
    def information(cls, *args, **kwargs):
        cls._record("information", args)
        return QMessageBox.StandardButton.Ok

    @classmethod
    def critical(cls, *args, **kwargs):
        cls._record("critical", args)
        return QMessageBox.StandardButton.Ok

    @classmethod
    def kinds(cls) -> list[str]:
        return [kind for kind, _title in cls.calls]


@pytest.fixture()
def dialog(qt_app, workspace, settings, monkeypatch, tmp_path):
    from app.import_dialog import ImportDialog

    _StubMessageBox.calls = []
    _StubMessageBox.answer = QMessageBox.StandardButton.Yes
    monkeypatch.setattr("app.import_dialog.QMessageBox", _StubMessageBox)

    source = make_image(tmp_path / "sources" / "scan_01.jpg", size=(600, 950), fmt="JPEG")
    dialog = ImportDialog(workspace, settings)
    yield dialog, source
    dialog.setParent(None)
    dialog.deleteLater()


def _preview(dialog, source, *, duplicates: bool = False):
    """Fill the dialog's list the way the analysis worker would."""
    service = ImportService(dialog.workspace)
    hashes = service.existing_hashes() if duplicates else None
    previews = service.analyze(
        [source],
        list(SUPPORTED_IMAGE_EXTENSIONS),
        "ask",
        existing_hashes=hashes,
    )
    dialog._previews = previews
    dialog.import_button.setEnabled(True)
    return previews


def test_import_completes_without_a_name_error(dialog, workspace):
    dialog, source = dialog
    _preview(dialog, source)
    imported: list[list[str]] = []
    dialog.imported.connect(imported.append)

    dialog._commit()  # used to raise NameError('QMessageBox')

    assert "information" in _StubMessageBox.kinds(), "no completion note was shown"
    assert "question" not in _StubMessageBox.kinds(), "nothing was duplicated"
    assert imported and len(imported[0]) == 1
    assert dialog.result() == QDialog.DialogCode.Accepted
    documents = workspace.database.documents.list_for_project(workspace.id)
    assert len(documents) == 1


def test_duplicate_question_can_skip_the_files(dialog, workspace):
    dialog, source = dialog
    _preview(dialog, source)
    dialog._commit()  # first import: the file is now in the project

    previews = _preview(dialog, source, duplicates=True)
    assert previews[0].status == "duplicate", "the second pass must flag the file"
    _StubMessageBox.calls = []
    _StubMessageBox.answer = QMessageBox.StandardButton.No

    dialog._commit()

    assert "question" in _StubMessageBox.kinds(), "the user was not asked"
    assert "critical" not in _StubMessageBox.kinds()
    assert len(workspace.database.documents.list_for_project(workspace.id)) == 1
    assert "nothing was imported" in dialog.status_label.text().lower()


def test_worker_delivers_analysis_results(dialog, tmp_path):
    """Regression: ``ImportWorker.run`` tested ``isInterruptionRequested`` as a
    bound method (always true), so every folder/file import was reported as
    "Analysis cancelled" and the Import button stayed disabled."""
    from PySide6.QtCore import QEventLoop

    from app.workers import ImportWorker

    dialog, _source = dialog
    folder = tmp_path / "samples"
    for index in range(3):
        make_image(
            folder / f"scan_{index}.jpg",
            (320, 240),
            "JPEG",
            color=(240, 240 - index * 20, 250 - index * 20),
            text=f"scan {index}",
        )

    worker = ImportWorker(
        ImportService(dialog.workspace),
        [str(folder)],
        list(SUPPORTED_IMAGE_EXTENSIONS),
        "ask",
        ImportService(dialog.workspace).existing_hashes(),
    )
    results: list[list] = []
    cancelled: list[bool] = []
    worker.completed.connect(results.append)
    worker.cancelled.connect(lambda: cancelled.append(True))

    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    worker.start()
    loop.exec()

    assert not cancelled, "a normal analysis was reported as cancelled"
    assert results and len(results[0]) == 3
    assert all(preview.status == "ready" for preview in results[0])


def test_folder_picker_returns_every_selected_folder(qt_app, tmp_path):
    """Regression: "Add several folders..." used ``QFileDialog`` in
    ``Directory`` mode, which only accepts the current folder, so only one
    folder could ever be selected. The dedicated picker must return the whole
    selection."""
    from PySide6.QtCore import QItemSelectionModel

    from app.import_dialog import FolderSelectionDialog

    root = tmp_path / "library"
    for name in ("iqama", "license", "passport"):
        (root / name).mkdir(parents=True)

    picker = FolderSelectionDialog(None, str(root))
    try:
        picker.show()
        qt_app.processEvents()

        assert picker.selected_folders() == [], "the dialog started pre-selected"
        assert not picker.add_button.isEnabled()

        selection = picker.tree.selectionModel()
        for name in ("iqama", "license"):
            index = picker.model.index(str(root / name))
            assert index.isValid()
            selection.select(
                index,
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        qt_app.processEvents()

        chosen = [Path(path).name for path in picker.selected_folders()]
        assert chosen == ["iqama", "license"]
        assert picker.add_button.isEnabled()
    finally:
        picker.close()
        picker.deleteLater()


def test_add_several_folders_adds_all_picked_folders(dialog, monkeypatch, tmp_path):
    """The button must feed every picked folder into the import list, not just
    one."""
    from PySide6.QtCore import QEventLoop

    from app import import_dialog as import_dialog_module

    dialog, _source = dialog
    folders = [tmp_path / "iqama", tmp_path / "passport"]
    for folder in folders:
        folder.mkdir()

    class _Picker:
        def __init__(self, parent=None, start_dir=None) -> None:
            self.start_dir = start_dir

        def exec(self) -> int:
            return 1

        def selected_folders(self) -> list[str]:
            return [str(folder) for folder in folders]

    monkeypatch.setattr(import_dialog_module, "FolderSelectionDialog", _Picker)

    dialog._pick_folders()

    assert dialog._paths == [str(folder) for folder in folders]

    loop = QEventLoop()
    dialog._worker.finished.connect(loop.quit)
    loop.exec()


def test_commit_failure_is_reported_and_changes_nothing(dialog, workspace):
    dialog, source = dialog
    _preview(dialog, source)

    def _boom(*args, **kwargs):
        raise OSError("the thumbnails folder is read-only")

    dialog.service.commit = _boom  # type: ignore[method-assign]

    dialog._commit()  # must not raise

    assert "critical" in _StubMessageBox.kinds(), "the failure was not reported"
    assert "import failed" in dialog.status_label.text().lower()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert workspace.database.documents.list_for_project(workspace.id) == []
