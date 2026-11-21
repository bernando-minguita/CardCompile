"""Page overflow tests: automatic overflow, disabled overflow, page management."""

from __future__ import annotations

import pytest

from core.auto_layout_engine import AutoLayoutEngine, LayoutOptions
from core.import_service import ImportService
from core.layout_service import LayoutService
from core.page_overflow_engine import PageOverflowEngine, best_grid_for
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS
from models.page_item import PageItem
from tests.conftest import make_image


@pytest.fixture()
def layout(workspace):
    return LayoutService(workspace)


def _documents(workspace, count):
    paths = []
    for index in range(count):
        path = workspace.project.folder / "sources" / f"overflow_{index:02d}.jpg"
        paths.append(make_image(path, size=(600 + index, 400), fmt="JPEG"))
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)


def test_overflow_creates_pages(layout, workspace):
    documents = _documents(workspace, 5)
    page = layout.pages()[0]
    engine = AutoLayoutEngine(layout)
    options = LayoutOptions(rows=2, columns=2, auto_overflow=True)
    result = engine.apply(documents, page.id, options)

    assert result.placed == 5
    assert result.created_pages == 1
    assert len(layout.pages()) == 2
    second_page = layout.pages()[1]
    assert len(layout.items(second_page.id)) == 1


def test_overflow_disabled_reports_unassigned(layout, workspace):
    documents = _documents(workspace, 5)
    page = layout.pages()[0]
    engine = AutoLayoutEngine(layout)
    options = LayoutOptions(rows=2, columns=2, auto_overflow=False)
    result = engine.apply(documents, page.id, options)

    assert result.placed == 4
    assert len(result.unassigned_documents) == 1
    assert len(layout.pages()) == 1, "no page is created when overflow is disabled"
    assert result.unassigned_documents[0] == documents[4].id


def test_overflow_plan_reports_unassigned(layout):
    page = layout.pages()[0]
    engine = PageOverflowEngine(layout)
    plan = engine.plan(page.id, document_count=7, capacity=4, auto_overflow=False)
    assert plan.unassigned_slots == 3
    assert plan.created_pages == 0

    plan = engine.plan(page.id, document_count=7, capacity=4, auto_overflow=True)
    assert plan.created_pages == 1
    assert plan.unassigned_slots == 0


def test_repeated_one_per_page_does_not_grow_the_document(layout, workspace):
    """Clicking "One document per page" twice must be idempotent.

    Every run created ``capacity - 1`` fresh pages, so repeated clicks piled up
    pages (4 -> 7 -> 10 ...) and the app had to render dozens of empty pages.
    """
    documents = _documents(workspace, 4)
    start = layout.pages()[0]
    engine = AutoLayoutEngine(layout)
    options = AutoLayoutEngine.options_for_preset("1", LayoutOptions())

    first = engine.apply(documents, start.id, options)
    assert first.placed == 4
    assert first.created_pages == 3
    assert len(layout.pages()) == 4

    for _ in range(4):
        again = engine.apply(documents, start.id, options)
        assert again.placed == 4
        assert again.created_pages == 0, "a repeated layout created pages again"
        assert len(layout.pages()) == 4, "the page count grew on a repeated layout"

    assert [len(layout.items(page.id)) for page in layout.pages()] == [1, 1, 1, 1]
    placed = [
        item.document_id for page in layout.pages() for item in layout.items(page.id)
    ]
    assert sorted(placed) == sorted(doc.id for doc in documents)


def test_overflow_keeps_pages_with_other_documents(layout, workspace):
    """Re-use must never wipe a page that holds a different document."""
    documents = _documents(workspace, 4)
    start = layout.pages()[0]
    engine = AutoLayoutEngine(layout)
    options = AutoLayoutEngine.options_for_preset("1", LayoutOptions())
    engine.apply(documents, start.id, options)

    # A fifth scan placed by hand on page 3, outside this layout run.
    extra = _documents(workspace, 5)[-1]
    third = layout.pages()[2]
    layout.insert_items(
        [
            PageItem(
                id="manual-1",
                page_id=third.id,
                document_id=extra.id,
                position_x=10.0,
                position_y=10.0,
                width=50.0,
                height=70.0,
            )
        ]
    )

    result = engine.apply(documents, start.id, options)

    assert result.placed == 4
    assert len(layout.pages()) == 5
    kept = [
        item
        for page in layout.pages()
        for item in layout.items(page.id)
        if item.document_id == extra.id
    ]
    assert len(kept) == 1, "the manually placed image was lost or duplicated"
    # the four documents end up exactly once on the pages of this run
    on_run_pages = [
        item for page_id in result.page_ids for item in layout.items(page_id)
    ]
    assert sorted(item.document_id for item in on_run_pages) == sorted(
        doc.id for doc in documents
    )


def test_overflow_places_documents_in_order_once_each(layout, workspace):
    """Seven scans, one per page: every page gets one, in order, no duplicates."""
    documents = _documents(workspace, 7)
    start = layout.pages()[0]
    options = AutoLayoutEngine.options_for_preset("1", LayoutOptions())

    result = AutoLayoutEngine(layout).apply(documents, start.id, options)

    pages = layout.pages()
    assert result.placed == 7
    assert len(pages) == 7
    per_page = [len(layout.items(page.id)) for page in pages]
    assert per_page == [1] * 7, f"unexpected page contents: {per_page}"
    placed = [layout.items(page.id)[0].document_id for page in pages]
    assert placed == [document.id for document in documents], "wrong page order"
    assert len(set(placed)) == 7, "a document was duplicated or omitted"
    assert [page.page_number for page in pages] == list(range(1, 8))


def test_page_management(layout, workspace):
    page = layout.pages()[0]
    second = layout.add_page(after_page_id=page.id)
    assert len(layout.pages()) == 2
    assert [p.page_number for p in layout.pages()] == [1, 2]

    duplicate = layout.duplicate_page(page.id)
    assert duplicate is not None
    assert len(layout.pages()) == 3
    assert layout.pages()[1].id == duplicate.id

    assert layout.delete_page(duplicate.id) is True
    assert len(layout.pages()) == 2
    assert [p.page_number for p in layout.pages()] == [1, 2]


def test_last_page_cannot_be_deleted(layout):
    page = layout.pages()[0]
    assert layout.delete_page(page.id) is False
    assert len(layout.pages()) == 1


def test_page_reorder(layout):
    first = layout.pages()[0]
    second = layout.add_page(after_page_id=first.id)
    third = layout.add_page(after_page_id=second.id)
    layout.move_page(third.id, 0)
    assert [p.id for p in layout.pages()] == [third.id, first.id, second.id]
    assert [p.page_number for p in layout.pages()] == [1, 2, 3]


def test_page_orientation_change(layout):
    page = layout.pages()[0]
    layout.update_page(page, orientation="landscape")
    reloaded = layout.page(page.id)
    assert (reloaded.width_mm, reloaded.height_mm) == (297.0, 210.0)


def test_clear_page(layout, workspace):
    documents = _documents(workspace, 2)
    page = layout.pages()[0]
    for document in documents:
        layout.insert_item(layout.create_item(document, page))
    assert len(layout.items(page.id)) == 2
    layout.clear_page(page.id)
    assert layout.items(page.id) == []


def test_best_grid_suggestions():
    assert best_grid_for(1) == (1, 1)
    assert best_grid_for(2, portrait=True) == (2, 1)
    assert best_grid_for(2, portrait=False) == (1, 2)
    assert best_grid_for(4) == (2, 2)
    assert best_grid_for(6) == (3, 2)
    assert best_grid_for(9) == (3, 3)
