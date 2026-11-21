"""Original-size placement: physical size from DPI, cropping and fallbacks."""

from __future__ import annotations

from PIL import Image

from core.image_service import probe_image, read_image_dpi
from core.import_service import ImportService, analyze_file
from core.layout_service import (
    LayoutService,
    fit_natural_size,
    original_size_for_crop,
)
from core.auto_layout_engine import AutoLayoutEngine, LayoutOptions
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS
from models.document import Document
from tests.conftest import make_image


def _jpg_with_dpi(path, size=(600, 400), dpi=(300, 300)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (250, 250, 250))
    image.save(path, format="JPEG", dpi=dpi)
    return path


def _import_one(workspace, path):
    importer = ImportService(workspace)
    previews = importer.analyze([path], list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    summary = importer.commit(previews, category.id)
    return workspace.database.documents.get(summary.imported_ids[0])


def test_probe_reads_dpi(tmp_path):
    path = _jpg_with_dpi(tmp_path / "card.jpg")
    info = probe_image(path)
    assert round(info.dpi_x) == 300
    assert round(info.dpi_y) == 300
    assert info.has_resolution is True


def test_probe_without_dpi_reports_zero(tmp_path):
    path = make_image(tmp_path / "plain.jpg", (400, 300), "JPEG")
    info = probe_image(path)
    assert info.dpi_x == 0.0
    assert info.has_resolution is False


def test_read_image_dpi_rejects_unplausible_values(tmp_path):
    path = tmp_path / "weird.jpg"
    Image.new("RGB", (10, 10)).save(path, format="JPEG", dpi=(1, 1))
    with Image.open(path) as image:
        assert read_image_dpi(image) == (0.0, 0.0)


def test_natural_size_uses_dpi_and_keeps_aspect():
    document = Document(
        id="d1",
        project_id="p1",
        filename="iqama.jpg",
        source_path="C:/scan.jpg",
        file_hash="x",
        original_width=1263,
        original_height=798,
        dpi_x=300,
        dpi_y=300,
    )
    width_mm, height_mm = document.natural_size_mm()
    assert round(width_mm, 1) == 106.9  # 1263 px at 300 dpi
    assert abs((width_mm / height_mm) - (1263 / 798)) < 0.001


def test_natural_size_falls_back_to_configured_dpi():
    document = Document(
        id="d1",
        project_id="p1",
        filename="scan.jpg",
        source_path="C:/scan.jpg",
        file_hash="x",
        original_width=2480,
        original_height=3508,
        dpi_x=0,
    )
    width_mm, height_mm = document.natural_size_mm(fallback_dpi=300)
    assert round(width_mm, 0) == 210
    assert round(height_mm, 0) == 297
    assert "assumed" in document.describe_resolution(300)


def test_fit_natural_size_only_shrinks():
    assert fit_natural_size(50.0, 30.0, 190.0, 277.0) == (50.0, 30.0)
    width, height = fit_natural_size(400.0, 200.0, 190.0, 277.0)
    assert round(width, 2) == 190.0
    assert round(height, 2) == 95.0


def test_original_size_for_crop_uses_visible_region():
    document = Document(
        id="d1",
        project_id="p1",
        filename="card.jpg",
        source_path="C:/card.jpg",
        file_hash="x",
        original_width=1200,
        original_height=800,
        dpi_x=300,
    )
    full = original_size_for_crop(document, (0.0, 0.0, 1.0, 1.0))
    half = original_size_for_crop(document, (0.0, 0.0, 0.5, 1.0))
    assert round(full[0], 1) == 101.6
    assert round(half[0], 1) == 50.8


def test_place_item_at_original_size(workspace, tmp_path):
    source = _jpg_with_dpi(tmp_path / "sources" / "card.jpg", (600, 400), (300, 300))
    document = _import_one(workspace, source)
    assert round(document.dpi_x) == 300

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    item = layout.create_item(document, page, original_size=True)
    # 600 px / 300 dpi = 2 inch = 50.8 mm
    assert round(item.width, 1) == 50.8
    assert round(item.height, 1) == 33.9
    assert item.was_scaled_to_fit is False


def test_place_item_fitted_when_option_disabled(workspace, tmp_path):
    source = _jpg_with_dpi(tmp_path / "sources" / "card2.jpg", (600, 400), (300, 300))
    document = _import_one(workspace, source)

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    item = layout.create_item(document, page, original_size=False)
    assert round(item.width, 1) == 190.0  # fills the printable width


def test_huge_image_is_scaled_down_to_fit(workspace, tmp_path):
    source = _jpg_with_dpi(tmp_path / "sources" / "huge.jpg", (6000, 4000), (300, 300))
    document = _import_one(workspace, source)

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    _, _, printable_w, printable_h = page.printable_rect()
    item = layout.create_item(document, page, original_size=True)
    assert item.was_scaled_to_fit is True
    assert item.width <= printable_w + 0.01
    assert item.height <= printable_h + 0.01
    assert abs((item.width / item.height) - 1.5) < 0.01


def test_grid_layout_still_fits_cells(workspace, tmp_path):
    source = _jpg_with_dpi(tmp_path / "sources" / "grid.jpg", (900, 600), (300, 300))
    _import_one(workspace, source)
    documents = workspace.database.documents.list_for_project(workspace.id)

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    AutoLayoutEngine(layout).apply(
        documents, page.id, LayoutOptions(rows=2, columns=2)
    )
    items = layout.items(page.id)
    assert len(items) == 1
    assert items[0].width <= page.width_mm
    assert items[0].height <= page.height_mm


def test_preview_reports_physical_size(tmp_path):
    path = _jpg_with_dpi(tmp_path / "preview.jpg", (300, 300), (300, 300))
    preview = analyze_file(path, {})
    width_mm, height_mm = preview.natural_size_mm(300)
    assert round(width_mm, 1) == 25.4
    assert round(height_mm, 1) == 25.4

