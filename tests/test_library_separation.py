"""Image Library vs. PDF page contents: the separation contract.

These tests pin down the rule that an imported image is *only* a library record
until the user explicitly adds it to a page, and that every layout operation has
an explicit scope (placed page objects or selected library images).
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

from core.auto_layout_engine import AutoLayoutEngine, LayoutOptions  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.layout_service import LayoutService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS  # noqa: E402
from core.validation_service import ValidationService  # noqa: E402
from models.page_item import PageItem  # noqa: E402
from tests.conftest import make_image  # noqa: E402


@pytest.fixture()
def layout(workspace):
    return LayoutService(workspace)


def _import(workspace, count: int):
    paths = []
    for index in range(count):
        path = workspace.project.folder / "sources" / f"lib_{index:02d}.jpg"
        paths.append(make_image(path, size=(800 + index * 10, 520), fmt="JPEG"))
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)


def _place(layout, workspace, documents, page_id=None) -> list[PageItem]:
    page_id = page_id or layout.pages()[0].id
    page = layout.page(page_id)
    items = [
        layout.create_item(document, page, original_size=False)
        for document in documents
    ]
    return layout.insert_items(items)


def test_get_reports_page_numbers_for_a_placed_document(layout, workspace):
    """``documents.get()`` must fill ``page_numbers`` like ``list_for_project()``.

    The field is transient, so a single-row read used to come back looking
    "not placed".  A caller that trusted it would treat a placed image as
    unused and delete it, cascading its placements away.
    """
    documents = _import(workspace, 1)
    page = layout.pages()[0]
    second_page = layout.add_page(after_page_id=page.id)
    _place(layout, workspace, [documents[0]], page_id=page.id)
    _place(layout, workspace, [documents[0]], page_id=second_page.id)

    listed = workspace.database.documents.list_for_project(workspace.id)[0]
    fetched = workspace.database.documents.get(documents[0].id)

    assert fetched is not None
    assert fetched.page_numbers == [1, 2], (
        f"get() reported page_numbers={fetched.page_numbers} for a document "
        f"placed on pages 1 and 2 (list_for_project() says {listed.page_numbers})"
    )
    assert fetched.page_numbers == listed.page_numbers


def test_get_reports_no_pages_for_an_unplaced_document(layout, workspace):
    """The fix must not invent placements: an unused image stays unplaced."""
    documents = _import(workspace, 2)
    _place(layout, workspace, [documents[0]])

    fetched = workspace.database.documents.get(documents[1].id)

    assert fetched is not None
    assert fetched.page_numbers == [], (
        f"an unplaced image reported page_numbers={fetched.page_numbers}"
    )


def test_import_only_fills_the_library(layout, workspace):
    documents = _import(workspace, 5)

    assert len(documents) == 5
    assert len(layout.pages()) == 1, "importing must not create pages"
    assert layout.all_items() == [], "importing must not place images on a page"
    counts = layout.library_counts()
    assert counts["imported"] == 5
    assert counts["placed"] == 0
    assert counts["unused"] == 5


def test_placed_count_counts_distinct_sources(layout, workspace):
    documents = _import(workspace, 4)
    _place(layout, workspace, documents[:2])
    second_page = layout.add_page(after_page_id=layout.pages()[0].id)
    _place(layout, workspace, documents[:1], page_id=second_page.id)

    counts = layout.library_counts()
    assert counts["placed"] == 2, "one image on two pages is still one placed image"
    assert len(layout.all_items()) == 3


def test_layout_never_uses_unplaced_library_images(layout, workspace):
    documents = _import(workspace, 6)
    placed = _place(layout, workspace, documents[:3])
    engine = AutoLayoutEngine(layout)
    options = AutoLayoutEngine.options_for_preset("4", LayoutOptions())

    engine.arrange_items(placed, layout.pages()[0], options)

    assert len(layout.all_items()) == 3, "arranging must not add library images"
    assert layout.placed_document_ids() == {doc.id for doc in documents[:3]}
    unused = {doc.id for doc in documents[3:]}
    assert not (layout.placed_document_ids() & unused)


def test_arrange_keeps_object_identity_and_respects_margins(layout, workspace):
    documents = _import(workspace, 3)
    items = _place(layout, workspace, documents)
    ids = {item.id for item in items}
    page = layout.pages()[0]
    engine = AutoLayoutEngine(layout)

    # A 2 x 2 grid has room for all three objects (2v only has two cells).
    arranged = engine.arrange_items(
        items, page, AutoLayoutEngine.options_for_preset("4", LayoutOptions())
    )

    assert {item.id for item in arranged} == ids, "arrange must not recreate objects"
    left, top, width, height = page.printable_rect()
    for item in arranged:
        assert item.position_x >= left - 0.01
        assert item.position_y >= top - 0.01
        assert item.position_x + item.width <= left + width + 0.01
        assert item.position_y + item.height <= top + height + 0.01
        assert item.aspect_locked, "arranging keeps the aspect ratio locked"
        assert abs(item.width / item.height - 800 / 520) < 0.05


def test_arrange_only_touches_the_given_items(layout, workspace):
    documents = _import(workspace, 4)
    page = layout.pages()[0]
    items = _place(layout, workspace, documents, page_id=page.id)
    other_page = layout.add_page(after_page_id=page.id)
    other_items = _place(layout, workspace, documents[3:], page_id=other_page.id)
    untouched = [(i.position_x, i.position_y, i.width, i.height) for i in other_items]

    AutoLayoutEngine(layout).arrange_items(
        items, page, AutoLayoutEngine.options_for_preset("4", LayoutOptions())
    )

    stored = layout.items(other_page.id)
    assert [
        (i.position_x, i.position_y, i.width, i.height) for i in stored
    ] == untouched, "the other page must not be modified"


def test_removing_a_placement_keeps_the_library_image(layout, workspace):
    documents = _import(workspace, 2)
    items = _place(layout, workspace, documents[:1])

    layout.delete_item(items[0].id)

    assert layout.all_items() == []
    assert len(layout.documents()) == 2, "the library record must survive"
    assert layout.library_counts()["placed"] == 0


def test_clear_and_delete_page_keep_the_library(layout, workspace):
    documents = _import(workspace, 3)
    page = layout.pages()[0]
    _place(layout, workspace, documents)
    second = layout.add_page(after_page_id=page.id)
    _place(layout, workspace, documents[:1], page_id=second.id)

    layout.clear_page(page.id)
    assert len(layout.documents()) == 3
    assert len(layout.all_items()) == 1

    layout.delete_page(second.id)
    assert len(layout.documents()) == 3
    assert layout.all_items() == []


def test_validation_ignores_unused_missing_files(layout, workspace):
    documents = _import(workspace, 2)
    placed, unused = documents
    _place(layout, workspace, [placed])
    Path(unused.source_path).unlink()

    report = ValidationService().validate_project(layout)
    assert report.ok, [issue.message for issue in report.errors]
    messages = " ".join(issue.message for issue in report.warnings)
    assert "not placed on any page" in messages

    # ... but a missing file that *is* placed still blocks the export.
    Path(placed.source_path).unlink()
    report = ValidationService().validate_project(layout)
    assert not report.ok
    error_msgs = " ".join(issue.message for issue in report.errors)
    assert "Original image is missing" in error_msgs
    # The full expected path is included to help the user locate/reconnect it.
    assert (
        f"(expected at {placed.source_path})" in error_msgs
    ), f"Expected path not in message: {error_msgs}"


# --------------------------------------------------------------------- editor
try:  # pragma: no cover - environment guard
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    QApplication = None


@pytest.fixture(scope="module")
def qt_app():
    if QApplication is None:  # pragma: no cover
        pytest.skip("PySide6 is not available")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def editor(qt_app, workspace):
    from app.layout_editor import LayoutEditor
    from core.settings_service import SettingsService

    settings = SettingsService(workspace.project.folder / "test_settings.json")
    settings.load()
    editor = LayoutEditor(workspace, settings)
    _import(workspace, 5)
    editor.reload_pages()
    yield editor
    editor.setParent(None)
    editor.deleteLater()


def test_editor_arrange_uses_only_page_objects(editor):
    library = list(editor.layout.documents().values())
    editor.add_documents(library[:3])
    page = editor.current_page()
    before_ids = {item.id for item in editor.layout.items(page.id)}

    assert editor.arrange_current_page("4") == 3

    after = editor.layout.items(page.id)
    assert {item.id for item in after} == before_ids
    assert len(after) == 3
    assert {item.document_id for item in after} == {doc.id for doc in library[:3]}
    assert len(editor.layout.all_items()) == 3, "library images were pulled in"


def test_editor_arrange_selected_only_moves_the_selection(editor):
    library = list(editor.layout.documents().values())
    editor.add_documents(library[:3])
    page = editor.current_page()
    items = editor.layout.items(page.id)
    first = items[0]
    untouched = (first.position_x, first.position_y, first.width, first.height)

    editor._select_items([items[1].id, items[2].id])
    assert editor.arrange_selected_items("2v") == 2

    stored = {item.id: item for item in editor.layout.items(page.id)}
    assert (
        stored[first.id].position_x,
        stored[first.id].position_y,
        stored[first.id].width,
        stored[first.id].height,
    ) == untouched, "an unselected object was moved"


def test_editor_add_to_current_page_keeps_existing_objects(editor):
    library = list(editor.layout.documents().values())
    editor.add_documents(library[:1])
    page = editor.current_page()
    existing = [item.id for item in editor.layout.items(page.id)]

    editor.add_documents(library[1:3])

    items = editor.layout.items(page.id)
    assert len(items) == 3
    assert set(existing) <= {item.id for item in items}, "existing objects were lost"
    assert len(editor.pages()) == 1, "adding to the page must not create pages"


def test_editor_add_to_new_pages_leaves_existing_pages_alone(editor):
    library = list(editor.layout.documents().values())
    editor.add_documents(library[:1])
    first_page = editor.current_page()
    before = [(i.position_x, i.position_y, i.width, i.height) for i in editor.layout.items(first_page.id)]

    placed = editor.add_documents_to_new_pages(library[1:4], preset="2v")

    assert placed == 3
    assert len(editor.pages()) == 3, "two new pages were expected (2 per page)"
    after = [
        (i.position_x, i.position_y, i.width, i.height)
        for i in editor.layout.items(first_page.id)
    ]
    assert after == before, "the existing page was modified"
    assert editor.current_page().id != first_page.id


def test_editor_undo_restores_the_previous_arrangement(editor):
    library = list(editor.layout.documents().values())
    editor.add_documents(library[:2])
    page = editor.current_page()
    before = [
        (i.position_x, i.position_y, i.width, i.height)
        for i in editor.layout.items(page.id)
    ]

    undo_before = editor.undo_stack.count()
    editor.arrange_current_page("2h")
    assert editor.undo_stack.count() == undo_before + 1, (
        "a layout must be a single undo entry"
    )
    editor.undo_stack.undo()

    after = [
        (i.position_x, i.position_y, i.width, i.height)
        for i in editor.layout.items(page.id)
    ]
    assert after == before


def test_toolbar_actions_run_outside_the_input_call(qt_app, tmp_path):
    """Menu/button actions must be queued, not executed inside the click.

    Windows raises RPC_E_CANTCALLOUT_ININPUTSYNCCALL (0x8001010d) when COM work
    (dialogs, drag & drop) runs while an input-synchronous message is still being
    dispatched - the mouse release that activates a menu action.  Qt then aborts
    the process: "the window vanished without any dialog".  Deferring the real
    work to the next event-loop turn keeps it out of that window.
    """
    from app.context import AppContext
    from app.editor_workspace import EditorWorkspace
    from core.project_service import ProjectService
    from core.settings_service import SettingsService
    from database.app_database import AppDatabase

    settings = SettingsService(tmp_path / "settings.json")
    settings.load()
    app_db = AppDatabase(tmp_path / "app.db")
    workspace_widget = EditorWorkspace(
        AppContext(
            settings=settings,
            app_db=app_db,
            projects=ProjectService(app_db, settings),
        )
    )
    calls: list[int] = []
    try:
        workspace_widget._defer(calls.append, 7)
        assert calls == [], "the callback ran inside the calling stack"
        qt_app.processEvents()
        assert calls == [7], "the queued callback never ran"
    finally:
        workspace_widget.setParent(None)
        workspace_widget.deleteLater()
        qt_app.processEvents()


def test_image_library_panel_builds_and_reports_counts(qt_app, workspace):
    from app.organizer_widget import OrganizerWidget

    _import(workspace, 3)
    panel = OrganizerWidget()
    try:
        panel.set_workspace(workspace)
        panel.refresh_list()
        assert panel.count_label.text().startswith("3 imported")
        assert panel.list.count() == 3
        assert not panel.add_button.isEnabled(), "Add must need a selection"
        assert "Select the images" in panel.add_button.toolTip()

        panel.select_all_filtered()
        assert panel.add_button.isEnabled()
        assert "3 selected" in panel.count_label.text()
        assert panel.list.count() == 3
    finally:
        panel.deleteLater()


def test_library_and_canvas_selections_are_independent(editor, workspace):
    from app.organizer_widget import OrganizerWidget

    panel = OrganizerWidget()
    panel.set_workspace(workspace)
    library = list(editor.layout.documents().values())
    editor.add_documents(library[:2])
    editor.scene.clearSelection()

    received: list[list[str]] = []
    panel.request_add_to_page.connect(received.append)
    try:
        panel.select_all_filtered()
        assert len(panel.selected_ids()) == 5
        assert editor.selected_ids() == [], "library selection leaked to the canvas"

        panel._add_selected_to_page()
        assert received and len(received[0]) == 5, "only selected images are added"
        assert editor.selected_ids() == [], "adding images must not select the canvas"
    finally:
        panel.deleteLater()


# --------------------------------------------------------------------- export
def test_export_contains_only_placed_images(layout, workspace, tmp_path):
    import pymupdf

    from core.pdf_service import PdfService

    documents = _import(workspace, 5)
    _place(layout, workspace, documents[:2])
    output = tmp_path / "placed_only.pdf"

    result = PdfService(layout).export_pages([layout.pages()[0].id], output)

    assert result.page_count == 1
    assert result.skipped_items == []
    with pymupdf.open(output) as pdf:
        listed = pdf[0].get_images()
    assert len(listed) == 2, (
        f"the PDF must contain the 2 placed images only, found {len(listed)}"
    )


def test_fifty_imported_three_placed_export_only_three(
    layout, workspace, tmp_path
):
    """The acceptance scenario: 50 imported, only 3 selected and placed."""
    import pymupdf

    from core.pdf_service import PdfService

    documents = _import(workspace, 50)
    assert len(documents) == 50
    assert layout.all_items() == [], "importing 50 images placed something"
    assert len(layout.pages()) == 1, "importing 50 images created pages"

    chosen = documents[:3]
    _place(layout, workspace, chosen)
    page = layout.pages()[0]
    AutoLayoutEngine(layout).arrange_items(
        layout.items(page.id),
        page,
        AutoLayoutEngine.options_for_preset("4", LayoutOptions()),
    )
    counts = layout.library_counts()
    assert counts == {"imported": 50, "placed": 3, "unused": 47}

    output = tmp_path / "acceptance.pdf"
    PdfService(layout).export_pages([page.id], output)

    with pymupdf.open(output) as pdf:
        exported = len(pdf[0].get_images())
    assert exported == 3, f"the PDF contains {exported} images instead of 3"

    untouched = {doc.id for doc in documents[3:]}
    assert not (untouched & {item.document_id for item in layout.all_items()})
    report = ValidationService().validate_project(layout)
    assert report.ok


def test_library_selection_survives_filtering(qt_app, workspace):
    """Selecting cards, filtering them away and clearing the filter must keep
    the same selection.

    The panel used to derive the selection from the visible rows, so a card
    hidden by a search lost its selection for good and a later "Add to page"
    silently placed fewer images than the user had picked.
    """
    from app.organizer_widget import OrganizerWidget

    _import(workspace, 5)
    panel = OrganizerWidget()
    try:
        panel.set_workspace(workspace)
        for row in (0, 2, 4):
            panel.list.item(row).setSelected(True)
        chosen = set(panel.selected_ids())
        assert len(chosen) == 3

        panel.search_edit.setText("lib_00")  # hides two of the three
        assert panel.list.count() == 1
        assert set(panel.selected_ids()) == chosen, "hidden cards lost their selection"
        assert "3 selected" in panel.count_label.text()

        panel.search_edit.setText("")  # back to the previous view
        assert set(panel.selected_ids()) == chosen, "the selection did not come back"
        rows = [
            panel.list.item(row).isSelected() for row in range(panel.list.count())
        ]
        assert rows == [True, False, True, False, True]

        received: list[list[str]] = []
        panel.request_add_to_page.connect(received.append)
        panel._add_selected_to_page()
        assert received and set(received[0]) == chosen

        panel.clear_selection()
        assert panel.selected_ids() == []
        panel.select_all_filtered()
        assert len(panel.selected_ids()) == 5
    finally:
        panel.deleteLater()


def test_three_selected_of_fifty_are_the_only_ones_placed(qt_app, workspace):
    """Add to page places exactly the selected library images."""
    from PySide6.QtCore import Qt

    from app.context import AppContext
    from app.editor_workspace import EditorWorkspace
    from core.project_service import ProjectService
    from core.settings_service import SettingsService
    from database.app_database import AppDatabase

    documents = _import(workspace, 50)
    settings = SettingsService(workspace.project.folder / "sep_settings.json")
    settings.load()
    app_db = AppDatabase(workspace.project.folder / "sep_app.db")
    widget = EditorWorkspace(
        AppContext(
            settings=settings,
            app_db=app_db,
            projects=ProjectService(app_db, settings),
        )
    )
    try:
        widget.load_workspace(workspace)
        qt_app.processEvents()
        panel = widget.organizer
        wanted = {documents[1].id, documents[7].id, documents[42].id}
        for row in range(panel.list.count()):
            item = panel.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) in wanted:
                item.setSelected(True)
        assert set(panel.selected_ids()) == wanted

        widget._add_documents_to_page(panel.selected_ids())

        items = widget.editor.layout.all_items()
        assert len(items) == 3, f"placed {len(items)} images instead of 3"
        assert {item.document_id for item in items} == wanted
        assert len(widget.editor.layout.pages()) == 1, "adding created pages"
        assert len(widget.editor.layout.documents()) == 50
    finally:
        widget.setParent(None)
        widget.deleteLater()
        app_db.close()


def test_missing_source_is_named_and_not_placed(qt_app, workspace, monkeypatch):
    """A missing file must be reported by name, not placed silently."""
    from app import editor_workspace as editor_workspace_module
    from app.context import AppContext
    from app.editor_workspace import EditorWorkspace
    from core.project_service import ProjectService
    from core.settings_service import SettingsService
    from database.app_database import AppDatabase

    documents = _import(workspace, 2)
    good, missing = documents
    Path(missing.source_path).unlink()

    warnings: list[str] = []
    monkeypatch.setattr(
        editor_workspace_module.QMessageBox,
        "warning",
        lambda _parent, _title, text, *a, **k: warnings.append(text),
        raising=False,
    )

    settings = SettingsService(workspace.project.folder / "missing_settings.json")
    settings.load()
    app_db = AppDatabase(workspace.project.folder / "missing_app.db")
    widget = EditorWorkspace(
        AppContext(
            settings=settings,
            app_db=app_db,
            projects=ProjectService(app_db, settings),
        )
    )
    try:
        widget.load_workspace(workspace)
        qt_app.processEvents()

        widget._add_documents_to_page([good.id, missing.id])

        assert warnings, "the missing file was not reported"
        assert missing.filename in warnings[0]
        placed = widget.editor.layout.all_items()
        assert [item.document_id for item in placed] == [good.id]
    finally:
        widget.setParent(None)
        widget.deleteLater()
        app_db.close()


def test_same_image_can_be_placed_twice_with_independent_properties(layout, workspace):
    documents = _import(workspace, 1)
    page = layout.pages()[0]
    first = layout.insert_item(layout.create_item(documents[0], page, original_size=False))
    second = layout.insert_item(layout.create_item(documents[0], page, original_size=False))
    assert first.id != second.id

    second.width = round(second.width * 0.5, 3)
    second.rotation = 45.0
    second.crop_left = 0.2
    second.position_x = round(second.position_x + 10.0, 3)
    layout.save_item(second)

    stored = {item.id: item for item in layout.items(page.id)}
    assert len(stored) == 2, "the same source may be placed more than once"
    assert stored[first.id].width != stored[second.id].width
    assert stored[first.id].rotation == 0.0 and stored[second.id].rotation == 45.0
    assert stored[first.id].crop_left == 0.0 and stored[second.id].crop_left == 0.2
    assert stored[first.id].position_x != stored[second.id].position_x

    layout.delete_item(first.id)
    assert [item.id for item in layout.items(page.id)] == [second.id]
    assert len(layout.documents()) == 1, "the source image must stay in the library"


def test_removing_a_placed_image_keeps_every_placement(layout, workspace, tmp_path):
    """Deleting a library entry must not touch the placed canvas objects.

    ``page_items.document_id`` cascaded from ``documents``, so removing a placed
    image from the Image Library deleted its placements and the image vanished
    from the canvas, the saved project and the PDF.  A placed document is now
    hidden from the library while its row - the canvas item's image reference -
    stays alive.
    """
    import pymupdf

    from core.document_service import DocumentService
    from core.pdf_service import ExportOptions, PdfService

    documents = _import(workspace, 2)
    page = layout.pages()[0]
    _place(layout, workspace, [documents[0]], page_id=page.id)
    second_page = layout.add_page(after_page_id=page.id)
    _place(layout, workspace, [documents[0]], page_id=second_page.id)
    _place(layout, workspace, [documents[1]], page_id=page.id)
    assert len(layout.all_items()) == 3
    before = {
        item.id: (item.page_id, item.position_x, item.position_y, item.width, item.height)
        for item in layout.all_items()
    }

    service = DocumentService(workspace)
    assert service.remove_documents([documents[0].id]) == 1

    # Every placement, on both pages, is untouched.
    after = {
        item.id: (item.page_id, item.position_x, item.position_y, item.width, item.height)
        for item in layout.all_items()
    }
    assert after == before, "removing a library entry moved or deleted placements"
    assert {item.document_id for item in layout.all_items()} == {
        documents[0].id,
        documents[1].id,
    }

    # The library no longer lists it, but the row still exists for the canvas.
    assert [doc.id for doc in service.documents()] == [documents[1].id]
    assert layout.library_counts() == {"imported": 1, "placed": 2, "unused": 0}
    hidden = workspace.database.documents.get(documents[0].id)
    assert hidden is not None and hidden.in_library is False
    assert Path(documents[0].source_path).is_file(), "the original file was deleted"

    # ... and the PDF still exports both pages with their images.
    output = tmp_path / "hidden_still_exports.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))
    pdf = pymupdf.open(output)
    try:
        assert pdf.page_count == 2
        assert len(pdf[0].get_images(full=True)) == 2
        assert len(pdf[1].get_images(full=True)) == 1
    finally:
        pdf.close()


def test_hidden_placed_image_survives_save_and_reopen(
    layout, workspace, project_service
):
    """Save, close and reopen: the hidden document and its placements are kept."""
    from core.document_service import DocumentService

    documents = _import(workspace, 2)
    page = layout.pages()[0]
    placements = _place(layout, workspace, documents)
    ids = [item.id for item in placements]
    DocumentService(workspace).remove_documents([documents[0].id])

    db_path = workspace.db_path
    workspace.close()
    reopened = project_service.open_project(db_path)
    try:
        stored = LayoutService(reopened).all_items()
        assert sorted(item.id for item in stored) == sorted(ids)
        assert {item.document_id for item in stored} == {
            documents[0].id,
            documents[1].id,
        }
        library = DocumentService(reopened).documents()
        assert [doc.id for doc in library] == [documents[1].id]
        hidden = reopened.database.documents.get(documents[0].id)
        assert hidden is not None and hidden.in_library is False
    finally:
        reopened.close()


def test_reimporting_a_hidden_image_restores_the_same_document(layout, workspace):
    """Re-importing the same file restores its library entry, no duplicate."""
    from core.document_service import DocumentService

    documents = _import(workspace, 2)
    page = layout.pages()[0]
    placements = _place(layout, workspace, documents)
    item_ids = [item.id for item in placements]
    hidden = documents[0]

    service = DocumentService(workspace)
    service.remove_documents([hidden.id])
    assert [doc.id for doc in service.documents()] == [documents[1].id]

    importer = ImportService(workspace)
    previews = importer.analyze(
        [Path(hidden.source_path)], list(SUPPORTED_IMAGE_EXTENSIONS)
    )
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    summary = importer.commit(previews, category.id)

    assert summary.imported_ids == [hidden.id], "re-import created a new document"
    library = service.documents()
    assert {doc.id for doc in library} == {hidden.id, documents[1].id}
    assert len(library) == 2, "the same image must not be listed twice"
    restored = workspace.database.documents.get(hidden.id)
    assert restored is not None and restored.in_library is True
    assert sorted(item.id for item in layout.all_items()) == sorted(item_ids)
    assert {item.document_id for item in layout.all_items()} == {
        hidden.id,
        documents[1].id,
    }


def test_export_keeps_page_order_and_per_page_contents(layout, workspace, tmp_path):
    import pymupdf

    from core.pdf_service import PdfService

    documents = _import(workspace, 4)
    first = layout.pages()[0]
    _place(layout, workspace, documents[:3], page_id=first.id)
    second = layout.add_page(after_page_id=first.id)
    _place(layout, workspace, documents[3:], page_id=second.id)
    output = tmp_path / "two_pages.pdf"

    PdfService(layout).export_pages([first.id, second.id], output)

    with pymupdf.open(output) as pdf:
        assert pdf.page_count == 2
        counts = [len(page.get_images()) for page in pdf]
    assert counts == [3, 1], f"unexpected per-page image counts: {counts}"




def test_project_summary_counts_hidden_images_that_are_still_placed(
    layout, workspace, project_service
):
    """The Projects list showed "0 documents" for a page with three images.

    After a library removal the placed document is hidden, so the visible-only
    count was zero while the page still contained the images.  Project-level
    counts must include every document the project owns.
    """
    from core.document_service import DocumentService

    documents = _import(workspace, 3)
    page = layout.pages()[0]
    _place(layout, workspace, documents, page_id=page.id)
    DocumentService(workspace).remove_documents([doc.id for doc in documents])

    summary = project_service.project_summary(workspace.db_path)
    assert summary.valid
    assert summary.document_count == 3, (
        f"project summary says {summary.document_count} documents for a page "
        "with 3 placed images"
    )
    assert summary.page_count == 1
    # The library itself stays empty, but the project still owns its images.
    assert DocumentService(workspace).documents() == []
    assert workspace.database.documents.count(workspace.id, in_library=True) == 0
