"""Layout engine tests: grid maths, fit modes, alignment, overflow, assignment."""

from __future__ import annotations

import pytest

from core.auto_layout_engine import (
    AutoLayoutEngine,
    LayoutOptions,
    cell_rects,
    compute_cell,
    crop_for_aspect,
)
from core.import_service import ImportService
from core.layout_service import LayoutService
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS
from tests.conftest import make_image


def _import_documents(workspace, paths):
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)


@pytest.fixture()
def layout(workspace):
    return LayoutService(workspace)


@pytest.fixture()
def source_factory(workspace):
    counter = {"value": 0}

    def _factory(size=(800, 400), fmt="JPEG"):
        counter["value"] += 1
        path = (
            workspace.project.folder
            / "sources"
            / f"doc_{counter['value']:03d}.jpg"
        )
        return make_image(path, size=size, fmt=fmt)

    return _factory


def test_a4_page_geometry(workspace, layout):
    pages = layout.pages()
    assert len(pages) == 1
    page = pages[0]
    assert (page.width_mm, page.height_mm) == (210.0, 297.0)
    assert page.printable_rect() == (10.0, 10.0, 190.0, 277.0)


def test_grid_cells_do_not_overlap(workspace, layout):
    page = layout.pages()[0]
    options = LayoutOptions(rows=2, columns=2).normalized()
    cells = cell_rects(page, options)
    assert len(cells) == 4
    for index, first in enumerate(cells):
        for second in cells[index + 1 :]:
            overlap_x = min(first[0] + first[2], second[0] + second[2]) - max(
                first[0], second[0]
            )
            overlap_y = min(first[1] + first[3], second[1] + second[3]) - max(
                first[1], second[1]
            )
            assert overlap_x <= 0.001 or overlap_y <= 0.001


def test_grid_stays_inside_margins(workspace, layout):
    page = layout.pages()[0]
    options = LayoutOptions(
        rows=3, columns=2, h_spacing_mm=5, v_spacing_mm=5
    ).normalized()
    for x, y, width, height in cell_rects(page, options):
        assert x >= page.margin_left - 0.001
        assert y >= page.margin_top - 0.001
        assert x + width <= page.width_mm - page.margin_right + 0.001
        assert y + height <= page.height_mm - page.margin_bottom + 0.001


def test_fit_modes_respect_aspect_and_cell(workspace, layout, source_factory):
    page = layout.pages()[0]
    document = _import_documents(workspace, [source_factory((800, 400))])[0]
    cell = (20.0, 20.0, 100.0, 100.0)

    contain = compute_cell(document, cell, LayoutOptions(fit_mode="contain"))
    assert contain.width <= 100.0 and contain.height <= 100.0
    assert abs((contain.width / contain.height) - 2.0) < 0.01

    cover = compute_cell(document, cell, LayoutOptions(fit_mode="cover"))
    assert (cover.width, cover.height) == (100.0, 100.0)
    assert cover.crop[0] > 0 or cover.crop[2] > 0, "cover must crop the excess"

    stretch = compute_cell(document, cell, LayoutOptions(fit_mode="stretch"))
    assert (stretch.width, stretch.height) == (100.0, 100.0)
    assert stretch.crop == (0.0, 0.0, 0.0, 0.0)


def test_apply_grid_places_documents(workspace, layout, source_factory):
    paths = [source_factory(fmt=fmt) for fmt in ("JPEG", "PNG", "TIFF", "BMP")]
    documents = _import_documents(workspace, paths)
    page = layout.pages()[0]
    engine = AutoLayoutEngine(layout)
    result = engine.apply(documents, page.id, AutoLayoutEngine.options_for_preset("4"))

    assert result.placed == 4
    items = layout.items(page.id)
    assert len(items) == 4
    for item in items:
        assert item.width > 0 and item.height > 0
        assert item.position_x >= 0 and item.position_y >= 0
    assert len({item.id for item in items}) == 4


def test_apply_grid_six_documents(workspace, layout, source_factory):
    documents = _import_documents(workspace, [source_factory() for _ in range(6)])
    page = layout.pages()[0]
    engine = AutoLayoutEngine(layout)
    result = engine.apply(documents, page.id, AutoLayoutEngine.options_for_preset("6"))
    assert result.placed == 6
    assert result.created_pages == 0


def test_alignment_modes(workspace, layout, source_factory):
    documents = _import_documents(workspace, [source_factory() for _ in range(3)])
    page = layout.pages()[0]
    items = []
    for index, document in enumerate(documents):
        item = layout.create_item(document, page)
        item.position_x = 15.0 + index * 30
        item.position_y = 20.0 + index * 10
        items.append(layout.insert_item(item))

    layout.align_items(items, "left", page)
    assert len({round(item.position_x, 3) for item in items}) == 1

    layout.align_items(items, "page_center", page)
    for item in items:
        expected = (page.width_mm - item.width) / 2.0
        assert abs(item.position_x - expected) < 0.01


def test_distribution(workspace, layout, source_factory):
    documents = _import_documents(workspace, [source_factory() for _ in range(3)])
    page = layout.pages()[0]
    items = []
    for index, document in enumerate(documents):
        item = layout.create_item(document, page, width=40, height=30)
        item.position_x = 10.0 + index * 55
        items.append(layout.insert_item(item))
    layout.distribute_items(items, "horizontal")
    ordered = sorted(items, key=lambda i: i.position_x)
    gap_one = ordered[1].position_x - ordered[0].right
    gap_two = ordered[2].position_x - ordered[1].right
    assert abs(gap_one - gap_two) < 0.01


def test_z_order_operations(workspace, layout, source_factory):
    documents = _import_documents(workspace, [source_factory() for _ in range(2)])
    page = layout.pages()[0]
    first = layout.insert_item(layout.create_item(documents[0], page))
    layout.insert_item(layout.create_item(documents[1], page))
    layout.bring_to_front(first)
    assert layout.items(page.id)[-1].id == first.id
    layout.send_to_back(first)
    assert layout.items(page.id)[0].id == first.id


def test_duplicate_and_move_between_pages(workspace, layout, source_factory):
    document = _import_documents(workspace, [source_factory()])[0]
    page = layout.pages()[0]
    item = layout.insert_item(layout.create_item(document, page))
    clone = layout.duplicate_item(item)
    assert clone.id != item.id
    assert len(layout.items(page.id)) == 2

    second_page = layout.add_page(after_page_id=page.id)
    layout.move_item_to_page(clone, second_page.id)
    assert layout.items(page.id)[0].id == item.id
    assert len(layout.items(second_page.id)) == 1


def _arranged_rows(layout, page, items, options, engine=None):
    """Arrange, persist and return the stored rows (arrange does not save)."""
    engine = engine or AutoLayoutEngine(layout)
    arranged = engine.arrange_items(items, page, options)
    layout.save_items(arranged)
    return arranged, {item.id: item for item in layout.items(page.id)}


def test_arrange_leaves_extras_when_objects_exceed_cells(
    workspace, layout, source_factory
):
    """A page with more objects than grid cells must not stack the extras in the
    last cell: they used to be copied onto cell 4 one after another."""
    documents = _import_documents(workspace, [source_factory() for _ in range(6)])
    page = layout.pages()[0]
    items = [
        layout.insert_item(layout.create_item(document, page, original_size=False))
        for document in documents
    ]
    extras_before = [(item.position_x, item.position_y) for item in items[4:]]

    arranged, stored = _arranged_rows(
        layout, page, items, LayoutOptions(rows=2, columns=2).normalized()
    )

    assert len(arranged) == 4, "only the available cells may be filled"
    positions = {
        (stored[item.id].position_x, stored[item.id].position_y) for item in arranged
    }
    assert len(positions) == 4, "two images received the same cell"
    assert [
        (stored[item.id].position_x, stored[item.id].position_y) for item in items[4:]
    ] == extras_before, "images that did not fit were moved anyway"


def test_arrange_keeps_a_rotated_image_inside_its_cell(
    workspace, layout, source_factory
):
    """A 90 degree image must be fitted by its visible (swapped) bounds.

    The engine fitted the unrotated frame into the cell, so the rotated box was
    taller than the cell and ran past the page edge.
    """
    from core.layout_service import visual_bounds

    documents = _import_documents(
        workspace, [source_factory((800, 400)), source_factory((800, 400))]
    )
    page = layout.pages()[0]
    items = [layout.create_item(doc, page, original_size=False) for doc in documents]
    items[1].rotation = 90.0
    items = [layout.insert_item(item) for item in items]
    options = LayoutOptions(rows=2, columns=1).normalized()

    arranged, stored = _arranged_rows(layout, page, items, options)

    cells = cell_rects(page, options)
    for item, cell in zip(arranged, cells):
        rect = visual_bounds(stored[item.id])
        left, top, width, height = rect
        cell_x, cell_y, cell_w, cell_h = cell
        assert left >= cell_x - 0.01 and top >= cell_y - 0.01
        assert left + width <= cell_x + cell_w + 0.01
        assert top + height <= cell_y + cell_h + 0.01
        assert 0 <= left and left + width <= page.width_mm + 0.01
        assert 0 <= top and top + height <= page.height_mm + 0.01

    rotated = stored[items[1].id]
    left, top, visible_w, visible_h = visual_bounds(rotated)
    assert visible_w < visible_h, "a rotated 2:1 image is portrait on the page"
    assert visible_w / visible_h == pytest.approx(0.5, rel=0.01)


@pytest.mark.parametrize("rotation", [0.0, 45.0, 90.0, 180.0, 270.0])
def test_arrange_keeps_any_rotation_inside_the_cell(
    workspace, layout, source_factory, rotation
):
    from core.layout_service import visual_bounds

    document = _import_documents(workspace, [source_factory((800, 400))])[0]
    page = layout.pages()[0]
    item = layout.create_item(document, page, original_size=False)
    item.rotation = rotation
    item = layout.insert_item(item)
    options = LayoutOptions(rows=1, columns=1).normalized()

    _arranged, stored = _arranged_rows(layout, page, [item], options)

    left, top, width, height = visual_bounds(stored[item.id])
    cell_x, cell_y, cell_w, cell_h = cell_rects(page, options)[0]
    assert left >= cell_x - 0.01 and top >= cell_y - 0.01
    assert left + width <= cell_x + cell_w + 0.01
    assert top + height <= cell_y + cell_h + 0.01
    assert width > 1 and height > 1, "the image vanished"


def test_arrange_mixed_aspects_keep_their_ratio(workspace, layout, source_factory):
    sizes = [(800, 200), (200, 800), (600, 600), (1600, 300), (300, 1600), (900, 600)]
    documents = _import_documents(
        workspace, [source_factory(size) for size in sizes]
    )
    page = layout.pages()[0]
    items = [
        layout.insert_item(layout.create_item(document, page, original_size=False))
        for document in documents
    ]

    arranged, stored = _arranged_rows(
        layout, page, items, LayoutOptions(rows=3, columns=2).normalized()
    )

    assert len(arranged) == 6
    for document, item in zip(documents, arranged):
        stored_item = stored[item.id]
        expected = document.original_width / document.original_height
        assert stored_item.width / stored_item.height == pytest.approx(
            expected, rel=0.01
        ), "a mixed-size image was distorted"
        assert stored_item.width <= page.width_mm
        assert stored_item.height <= page.height_mm
        for other in arranged:
            if other.id == item.id:
                continue
            other_row = stored[other.id]
            overlap_x = min(
                stored_item.position_x + stored_item.width,
                other_row.position_x + other_row.width,
            ) - max(stored_item.position_x, other_row.position_x)
            overlap_y = min(
                stored_item.position_y + stored_item.height,
                other_row.position_y + other_row.height,
            ) - max(stored_item.position_y, other_row.position_y)
            assert overlap_x <= 0.01 or overlap_y <= 0.01, "images overlap"


def test_arrange_uses_the_visible_crop(workspace, layout, source_factory):
    """A cropped image is fitted by its visible region, not the whole scan."""
    document = _import_documents(workspace, [source_factory((800, 400))])[0]
    page = layout.pages()[0]
    item = layout.create_item(document, page, original_size=False)
    # Trim to the middle 50 % on every side: 400 x 200 visible -> aspect 2.0.
    item.crop_left = item.crop_right = 0.25
    item.crop_top = item.crop_bottom = 0.25
    item = layout.insert_item(item)

    arranged, stored = _arranged_rows(
        layout, page, [item], LayoutOptions(rows=1, columns=1).normalized()
    )

    row = stored[item.id]
    assert row.width / row.height == pytest.approx(2.0, rel=0.02)
    assert row.width <= page.printable_rect()[2] + 0.01
    assert row.height <= page.printable_rect()[3] + 0.01


def test_repeated_arrange_is_idempotent(workspace, layout, source_factory):
    documents = _import_documents(workspace, [source_factory() for _ in range(4)])
    page = layout.pages()[0]
    items = [
        layout.insert_item(layout.create_item(document, page, original_size=False))
        for document in documents
    ]
    engine = AutoLayoutEngine(layout)
    options = LayoutOptions(rows=2, columns=2, fit_mode="cover").normalized()

    snapshots = []
    for _ in range(3):
        _arranged_rows(layout, page, items, options, engine)
        snapshots.append(
            sorted(
                (
                    item.id,
                    round(item.position_x, 3),
                    round(item.position_y, 3),
                    round(item.width, 3),
                    round(item.height, 3),
                    round(item.crop_left, 5),
                    round(item.crop_top, 5),
                    round(item.crop_right, 5),
                    round(item.crop_bottom, 5),
                )
                for item in layout.items(page.id)
            )
        )

    assert snapshots[0] == snapshots[1] == snapshots[2], (
        "each run changed sizes or positions"
    )


def test_crop_for_aspect_values():
    crop = crop_for_aspect(1000, 500, 1.0)
    assert pytest.approx(crop[0], abs=1e-6) == 0.25
    assert crop[1] == 0.0
    vertical = crop_for_aspect(500, 1000, 1.0)
    assert vertical[0] == 0.0
    assert pytest.approx(vertical[1], abs=1e-6) == 0.25
