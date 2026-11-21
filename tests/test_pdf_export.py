"""PDF export tests: page sizes, multi page, placement, quality, validation."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from core.auto_layout_engine import AutoLayoutEngine, LayoutOptions
from core.import_service import ImportService
from core.layout_service import LayoutService
from core.pdf_service import ExportOptions, PdfService, mm_to_pt
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS
from core.validation_service import ValidationService
from tests.conftest import make_image

A4_WIDTH_PT = mm_to_pt(210.0)
A4_HEIGHT_PT = mm_to_pt(297.0)


@pytest.fixture()
def layout(workspace):
    return LayoutService(workspace)


def _documents(workspace, count=1, size=(800, 600)):
    paths = []
    for index in range(count):
        path = workspace.project.folder / "sources" / f"pdf_doc_{index:02d}.jpg"
        paths.append(make_image(path, size=size, fmt="JPEG", text=f"DOC {index}"))
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    return workspace.database.documents.list_for_project(workspace.id)


def test_export_single_a4_page(layout, workspace, tmp_path):
    documents = _documents(workspace, 1)
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=1, columns=1))

    output = tmp_path / "out" / "single.pdf"
    result = PdfService(layout).export_project(output, ExportOptions(quality="High"))
    assert result.page_count == 1
    assert output.is_file()

    document = pymupdf.open(output)
    try:
        assert document.page_count == 1
        rect = document[0].rect
        assert rect.width == pytest.approx(A4_WIDTH_PT, abs=1.0)
        assert rect.height == pytest.approx(A4_HEIGHT_PT, abs=1.0)
        assert len(document[0].get_images(full=True)) == 1
    finally:
        document.close()


def test_export_landscape(layout, workspace, tmp_path):
    documents = _documents(workspace, 1)
    page = layout.pages()[0]
    layout.update_page(page, orientation="landscape")
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=1, columns=1))

    output = tmp_path / "landscape.pdf"
    PdfService(layout).export_project(output)
    document = pymupdf.open(output)
    try:
        rect = document[0].rect
        assert rect.width == pytest.approx(A4_HEIGHT_PT, abs=1.0)
        assert rect.height == pytest.approx(A4_WIDTH_PT, abs=1.0)
    finally:
        document.close()


def test_export_multiple_pages_with_overflow(layout, workspace, tmp_path):
    documents = _documents(workspace, 5, size=(600, 400))
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(
        documents, page.id, LayoutOptions(rows=2, columns=2, auto_overflow=True)
    )

    output = tmp_path / "multipage.pdf"
    result = PdfService(layout).export_project(output)
    assert result.page_count == 2

    document = pymupdf.open(output)
    try:
        assert document.page_count == 2
        assert len(document[0].get_images(full=True)) == 4
        assert len(document[1].get_images(full=True)) == 1
    finally:
        document.close()


def test_export_selected_pages(layout, workspace, tmp_path):
    documents = _documents(workspace, 2)
    first = layout.pages()[0]
    second = layout.add_page(after_page_id=first.id)
    AutoLayoutEngine(layout).apply(documents[:1], first.id, LayoutOptions(rows=1, columns=1))
    AutoLayoutEngine(layout).apply(documents[1:], second.id, LayoutOptions(rows=1, columns=1))

    output = tmp_path / "page_two.pdf"
    result = PdfService(layout).export_pages([second.id], output)
    assert result.page_count == 1
    document = pymupdf.open(output)
    try:
        assert document.page_count == 1
    finally:
        document.close()


def test_export_separate_files(layout, workspace, tmp_path):
    documents = _documents(workspace, 2)
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=2, columns=1))
    second = layout.add_page(after_page_id=page.id)

    result = PdfService(layout).export_pages_separately(
        [page.id, second.id], tmp_path / "parts", "doc"
    )
    assert len(result.output_paths) == 2
    assert all(Path(path).is_file() for path in result.output_paths)


def test_export_quality_affects_size(layout, workspace, tmp_path):
    documents = _documents(workspace, 1, size=(2400, 1800))
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=1, columns=1))
    service = PdfService(layout)

    draft = tmp_path / "draft.pdf"
    high = tmp_path / "high.pdf"
    service.export_project(draft, ExportOptions(quality="Draft"))
    service.export_project(high, ExportOptions(quality="High"))
    assert draft.stat().st_size > 0 and high.stat().st_size > 0
    assert high.stat().st_size >= draft.stat().st_size


def test_export_validation_passes(layout, workspace, tmp_path):
    documents = _documents(workspace, 2)
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=2, columns=1))
    output = tmp_path / "validated.pdf"
    PdfService(layout).export_project(output)

    report = ValidationService().validate_pdf(
        output,
        expected_pages=1,
        expected_sizes_mm=[(page.width_mm, page.height_mm)],
    )
    assert report.ok, report.summary()
    assert report.warnings == []


def test_validation_detects_wrong_page_count(layout, workspace, tmp_path):
    _documents(workspace, 1)
    output = tmp_path / "wrong.pdf"
    PdfService(layout).export_project(output)
    report = ValidationService().validate_pdf(output, expected_pages=3)
    assert report.ok is False


def test_password_protected_export(layout, workspace, tmp_path):
    documents = _documents(workspace, 1)
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=1, columns=1))
    output = tmp_path / "secured.pdf"
    PdfService(layout).export_project(
        output, ExportOptions(user_password="secret", owner_password="owner")
    )
    document = pymupdf.open(output)
    try:
        assert document.needs_pass
        assert document.authenticate("secret")
    finally:
        document.close()


def test_export_metadata(layout, workspace, tmp_path):
    documents = _documents(workspace, 1)
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=1, columns=1))
    output = tmp_path / "metadata.pdf"
    PdfService(layout).export_project(output)
    document = pymupdf.open(output)
    try:
        assert document.metadata.get("title") == "Test Project"
        assert "CardCompile" in document.metadata.get("creator", "")
    finally:
        document.close()


def test_export_category_only(layout, workspace, tmp_path):
    """Only documents of the selected category are rendered."""
    documents = _documents(workspace, 2)
    categories = workspace.database.categories.list_for_project(workspace.id)
    passport = next(c for c in categories if c.name == "Passport")
    workspace.database.documents.set_category(documents[0].id, passport.id)

    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=2, columns=1))

    output = tmp_path / "passport_only.pdf"
    result = PdfService(layout).export_project(
        output, ExportOptions(quality="Standard"), category_id=passport.id
    )
    assert result.page_count == 1
    document = pymupdf.open(output)
    try:
        assert document.page_count == 1
        assert len(document[0].get_images(full=True)) == 1
    finally:
        document.close()


def test_export_unknown_category_raises(layout, workspace, tmp_path):
    documents = _documents(workspace, 1)
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(documents, page.id, LayoutOptions(rows=1, columns=1))
    with pytest.raises(Exception):
        PdfService(layout).export_project(
            tmp_path / "empty.pdf", ExportOptions(), category_id="does-not-exist"
        )


def test_z_order_matches_the_canvas(layout, workspace, tmp_path):
    """The topmost image on the canvas must also be on top in the PDF.

    Two solid colour cards overlap exactly; the one with the higher z_index has
    to win, in both the database read order and the rendered PDF.
    """
    folder = workspace.project.folder / "sources"
    red = make_image(folder / "red.jpg", (300, 200), "JPEG", color=(220, 30, 30))
    blue = make_image(folder / "blue.jpg", (300, 200), "JPEG", color=(30, 30, 220))
    service = ImportService(workspace)
    previews = service.analyze([red, blue], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    service.commit(previews, category.id)
    documents = {doc.filename: doc for doc in layout.documents().values()}

    page = layout.pages()[0]
    bottom = layout.create_item(documents["blue.jpg"], page, width=80.0, height=50.0)
    bottom.position_x, bottom.position_y = 40.0, 40.0
    bottom.z_index = 1
    layout.insert_item(bottom)
    top = layout.create_item(documents["red.jpg"], page, width=80.0, height=50.0)
    top.position_x, top.position_y = 40.0, 40.0
    top.z_index = 2
    layout.insert_item(top)

    # 1) the reading order used by the exporter is ascending z_index
    order = [item.id for item in layout.items(page.id)]
    assert order == [bottom.id, top.id]

    output = tmp_path / "zorder.pdf"
    PdfService(layout).export_project(output, ExportOptions(quality="Standard"))

    pdf = pymupdf.open(output)
    try:
        pixmap = pdf[0].get_pixmap(dpi=72)
        mm = 72.0 / 25.4
        x = int((40.0 + 40.0) * mm)  # centre of the overlapping cards
        y = int((40.0 + 25.0) * mm)
        red_px = pixmap.pixel(x, y)
        assert red_px[0] > 150 and red_px[2] < 120, (
            f"the image with the higher z_index must be on top, got {red_px}"
        )
    finally:
        pdf.close()


def test_rotation_is_rendered(layout, workspace, tmp_path):
    documents = _documents(workspace, 1)
    page = layout.pages()[0]
    item = layout.insert_item(
        layout.create_item(documents[0], page, width=80, height=40)
    )
    item.rotation = 90.0
    layout.save_item(item)

    output = tmp_path / "rotated.pdf"
    PdfService(layout).export_project(output)
    document = pymupdf.open(output)
    try:
        assert len(document[0].get_images(full=True)) == 1
        bbox = document[0].get_image_bbox(document[0].get_images(full=True)[0])
        assert bbox.height > bbox.width, "a 90 degree rotation swaps the bounds"
    finally:
        document.close()
