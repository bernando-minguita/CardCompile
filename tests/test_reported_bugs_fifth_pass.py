"""Regression tests for the fifth review pass (export dialog + duplicate clamp).

1. "Each page as a separate PDF" was refused with *"That file name cannot be used
   for this folder"* before anything was exported: for that scope the target is
   the folder itself, and the sanity check compared ``folder.parent`` with
   ``folder``, so only a drive root ever passed.
2. "Current page only" validated the produced PDF against the size of page 1
   instead of the exported page, so an A3 page 2 exported on its own was
   reported as "Page 1 has the wrong size".
3. A separate export recorded the *total* page count for every file in the
   export history instead of one page per file.
4. ``LayoutService.duplicate_item`` clamped the copy with ``min()`` but not with
   ``max(0, ...)``: duplicating an item that is larger than the target page
   placed it at a negative coordinate, partly off the page.
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
from core.layout_service import LayoutService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS  # noqa: E402
from core.validation_service import ValidationReport  # noqa: E402
from tests.conftest import make_image  # noqa: E402

pyqt_available = True
try:  # pragma: no cover - environment guard
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QMessageBox
except Exception:  # pragma: no cover
    pyqt_available = False


@pytest.fixture(scope="module")
def qt_app():
    if not pyqt_available:  # pragma: no cover
        pytest.skip("PySide6 is not available")
    app = QApplication.instance() or QApplication([])
    yield app


def _import(workspace, count: int = 1):
    paths = [
        make_image(
            workspace.project.folder / "sources" / f"scan_{index}.jpg",
            size=(600, 950),
            fmt="JPEG",
        )
        for index in range(count)
    ]
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)


class _StubMessageBox:
    StandardButton = QMessageBox.StandardButton
    calls: list[tuple[str, str]] = []

    @classmethod
    def warning(cls, parent=None, title="", text="", *args, **kwargs):
        cls.calls.append(("warning", str(text)))
        return QMessageBox.StandardButton.Ok

    @classmethod
    def critical(cls, parent=None, title="", text="", *args, **kwargs):
        cls.calls.append(("critical", str(text)))
        return QMessageBox.StandardButton.Ok

    @classmethod
    def question(cls, *args, **kwargs):
        cls.calls.append(("question", ""))
        return QMessageBox.StandardButton.Yes

    @classmethod
    def texts(cls) -> str:
        return " | ".join(text for _kind, text in cls.calls)


@pytest.fixture()
def export_dialog(qt_app, workspace, settings, monkeypatch, tmp_path):
    from app.export_dialog import ExportDialog

    _StubMessageBox.calls = []
    monkeypatch.setattr("app.export_dialog.QMessageBox", _StubMessageBox)

    layout = LayoutService(workspace)
    first = layout.pages()[0]
    second = layout.add_page(after_page_id=first.id, paper_size="A3")
    dialog = ExportDialog(layout, settings, second.id)
    dialog.folder_edit.setText(str(tmp_path / "out"))
    yield dialog, layout, first, second
    dialog.setParent(None)
    dialog.deleteLater()


class _FakeWorker(QObject):
    progress = Signal(int, int)
    completed = Signal(object)
    cancelled = Signal(object)
    failed = Signal(str)
    instances: list["_FakeWorker"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.started = False
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        self.started = True

    def isRunning(self) -> bool:
        return False

    def cancel(self) -> None:
        pass


class _Result:
    def __init__(self, paths: list[str]) -> None:
        self.output_paths = list(paths)
        self.skipped_items: list[str] = []


def test_each_page_scope_is_accepted(export_dialog, monkeypatch):
    from app.export_dialog import SCOPE_SEPARATE

    dialog, _layout, _first, _second = export_dialog
    _FakeWorker.instances = []
    monkeypatch.setattr("app.export_dialog.ExportWorker", _FakeWorker)

    dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData(SCOPE_SEPARATE))
    dialog._start_export()

    assert "cannot be used" not in _StubMessageBox.texts(), (
        "the separate-page export was blocked by the file name check: "
        + _StubMessageBox.texts()
    )
    assert _FakeWorker.instances and _FakeWorker.instances[0].started, (
        "no export worker was started for the separate-page scope"
    )
    assert _FakeWorker.instances[0].kwargs.get("separate") is True


def test_current_page_scope_validates_its_own_size(export_dialog, monkeypatch):
    from app.export_dialog import SCOPE_CURRENT

    dialog, _layout, first, second = export_dialog
    assert (first.width_mm, first.height_mm) != (second.width_mm, second.height_mm)

    captured: dict = {}

    class _Validator:
        def validate_pdf(self, path, **kwargs):
            captured.update(kwargs)
            return ValidationReport()

    monkeypatch.setattr("app.export_dialog.ValidationService", _Validator)
    dialog.pdf_service.record_export = lambda *args, **kwargs: None
    dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData(SCOPE_CURRENT))

    dialog._on_export_done(_Result(["whatever.pdf"]))

    sizes = captured.get("expected_sizes_mm") or []
    assert sizes == [(second.width_mm, second.height_mm)], (
        f"validated against {sizes} instead of the exported page size"
    )


def test_separate_export_records_one_page_per_file(export_dialog, monkeypatch):
    from app.export_dialog import SCOPE_SEPARATE

    dialog, _layout, _first, _second = export_dialog
    recorded: list[tuple] = []

    class _Validator:
        def validate_pdf(self, path, **kwargs):
            return ValidationReport()

    monkeypatch.setattr("app.export_dialog.ValidationService", _Validator)
    dialog.pdf_service.record_export = lambda *args, **kwargs: recorded.append(args)
    dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData(SCOPE_SEPARATE))

    dialog._on_export_done(_Result(["page_001.pdf", "page_002.pdf"]))

    assert [args[1] for args in recorded] == [1, 1], (
        f"each separate PDF must record one page, got {recorded}"
    )


def test_separate_export_warns_for_bracketed_name(export_dialog, monkeypatch):
    """A name with ``[``/``]`` must still trigger the overwrite warning.

    ``safe_pdf_file_name`` keeps the brackets (they are legal on Windows), but
    they open a character class in a glob pattern.  The old check built
    ``folder.glob(f"{base}_page_*.pdf")``, which matched nothing for
    ``Report [final]``, so the previous export was overwritten without asking.
    """
    from app.export_dialog import SCOPE_SEPARATE

    dialog, _layout, _first, _second = export_dialog
    titles: list[str] = []

    class _Recorder:
        StandardButton = QMessageBox.StandardButton

        @classmethod
        def question(cls, parent=None, title="", text="", *args, **kwargs):
            titles.append(str(title))
            # Answer "No" so the export stops here and no worker is started.
            return QMessageBox.StandardButton.No

        @classmethod
        def warning(cls, *args, **kwargs):
            return QMessageBox.StandardButton.Ok

        @classmethod
        def critical(cls, *args, **kwargs):
            return QMessageBox.StandardButton.Ok

    monkeypatch.setattr("app.export_dialog.QMessageBox", _Recorder)

    out = Path(dialog.folder_edit.text())
    out.mkdir(parents=True, exist_ok=True)
    (out / "Report [final]_page_001.pdf").write_bytes(b"%PDF-1.4 old")

    dialog.name_edit.setText("Report [final]")
    dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData(SCOPE_SEPARATE))
    dialog._start_export()

    assert titles == ["Overwrite files"], (
        "a bracketed project name must still warn before overwriting, got "
        f"{titles}"
    )


def test_duplicate_item_stays_inside_a_small_page(workspace):
    layout = LayoutService(workspace)
    documents = _import(workspace)
    small = layout.add_page(after_page_id=None)
    page = layout.db.pages.get(small.id)
    page.width_mm = 40.0
    page.height_mm = 40.0
    layout.db.pages.update(page)

    source_page = layout.pages()[0]
    item = layout.create_item(documents[0], source_page, original_size=False)
    item.width, item.height = 120.0, 90.0
    layout.save_item(item)

    clone = layout.duplicate_item(item, page_id=small.id)

    assert clone.position_x >= 0.0, f"copy placed off the page at x={clone.position_x}"
    assert clone.position_y >= 0.0, f"copy placed off the page at y={clone.position_y}"

    def texts(cls) -> str:
        return " | ".join(text for _kind, text in cls.calls)


def test_category_scope_refuses_the_all_categories_entry(export_dialog, monkeypatch):
    """\"All categories\" with the category scope must not export everything."""
    from app.export_dialog import SCOPE_CATEGORY

    dialog, _layout, _first, _second = export_dialog
    _FakeWorker.instances = []
    monkeypatch.setattr("app.export_dialog.ExportWorker", _FakeWorker)
    dialog.scope_combo.setCurrentIndex(
        dialog.scope_combo.findData(SCOPE_CATEGORY)
    )
    dialog.category_combo.setCurrentIndex(0)  # "All categories" (data None)

    dialog._start_export()

    assert "Choose a category" in _StubMessageBox.texts(), (
        "the export was not refused: " + _StubMessageBox.texts()
    )
    assert not _FakeWorker.instances, (
        "a worker was started for a whole-project export disguised as a category"
    )


def test_category_scope_records_the_real_page_count(export_dialog, monkeypatch):
    """A category export skips pages, so the history must use the real count."""
    from app.export_dialog import SCOPE_CATEGORY

    dialog, _layout, _first, _second = export_dialog
    recorded: list[tuple] = []

    class _Validator:
        def validate_pdf(self, path, **kwargs):
            return ValidationReport()

    class _CategoryResult:
        output_paths = ["category.pdf"]
        skipped_items: list[str] = []
        page_count = 2  # only 2 of the project's pages had matching documents

    monkeypatch.setattr("app.export_dialog.ValidationService", _Validator)
    dialog.pdf_service.record_export = lambda *args, **kwargs: recorded.append(args)
    dialog.scope_combo.setCurrentIndex(
        dialog.scope_combo.findData(SCOPE_CATEGORY)
    )
    dialog.category_combo.setCurrentIndex(1)  # the project's only category

    dialog._on_export_done(_CategoryResult())

    assert [args[1] for args in recorded] == [2], (
        f"history recorded the wrong page count: {recorded}"
    )
