"""Regression tests for card placement with lying DPI tags (1.3.4).

The sample scans in ``sample_images/`` are cropped cards that still carry the
scanner's 300 dpi tag: 360x225 px => 30.5 x 19.1 mm, i.e. a third of a real
ID-1 card.  "Place at original size" used to put them on the page that small.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from core.import_service import ImportService  # noqa: E402
from core.layout_service import ID1_LONG_EDGE_MM, LayoutService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService  # noqa: E402

SAMPLES = ROOT / "sample_images"


def _import(workspace, paths):
    importer = ImportService(workspace)
    previews = importer.analyze(list(paths), list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)
    return {doc.filename: doc for doc in workspace.database.documents.list_for_project(workspace.id)}


def test_sample_cards_are_placed_at_card_size(workspace):
    """A 30 mm card would be unusable on the page - it snaps to ID-1 width."""
    if not SAMPLES.is_dir():  # pragma: no cover - samples are shipped
        pytest.skip("sample images are not available")
    documents = _import(workspace, SAMPLES.glob("*.jpg"))
    layout = LayoutService(workspace)
    page = layout.pages()[0]

    for name, document in documents.items():
        natural_w, natural_h = document.natural_size_mm(300)
        assert max(natural_w, natural_h) < ID1_LONG_EDGE_MM, (
            f"{name} is already at a usable size ({natural_w:.1f} mm)"
        )
        item = layout.create_item(
            document,
            page,
            original_size=True,
            fallback_dpi=300,
            min_long_edge_mm=ID1_LONG_EDGE_MM,
        )
        assert item.was_enlarged is True
        assert max(item.width, item.height) == pytest.approx(ID1_LONG_EDGE_MM, abs=0.2)
        # the aspect ratio is preserved (no distortion)
        assert (item.width / item.height) == pytest.approx(
            document.original_width / document.original_height, rel=0.01
        )
        assert not item.was_scaled_to_fit


def test_snapping_can_be_switched_off(workspace):
    source = ROOT / "sample_images" / "Iqama_7654_P01_03.jpg"
    if not source.is_file():  # pragma: no cover
        pytest.skip("sample images are not available")
    target = workspace.project.folder.parent / "sources" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    document = _import(workspace, [target])[source.name]

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    item = layout.create_item(
        document, page, original_size=True, fallback_dpi=300, min_long_edge_mm=0.0
    )
    assert item.was_enlarged is False
    assert item.width == pytest.approx(30.5, abs=0.2)


def test_correctly_sized_scan_is_not_changed(workspace, tmp_path):
    """A scan whose DPI tag is right must stay at its real physical size."""
    source = tmp_path / "sources" / "real_card.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    # 1011 px at 300 dpi = 85.6 mm - a properly tagged ID-1 card
    Image.new("RGB", (1011, 638), (250, 250, 250)).save(
        source, format="JPEG", dpi=(300, 300)
    )
    document = _import(workspace, [source])[source.name]

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    item = layout.create_item(
        document,
        page,
        original_size=True,
        fallback_dpi=300,
        min_long_edge_mm=ID1_LONG_EDGE_MM,
    )
    assert item.was_enlarged is False
    assert item.width == pytest.approx(85.6, abs=0.2)


def test_large_scan_is_not_enlarged(workspace, tmp_path):
    """Nothing that is already bigger than a card is touched."""
    source = tmp_path / "sources" / "a4_scan.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2480, 3508), (250, 250, 250)).save(
        source, format="JPEG", dpi=(300, 300)
    )
    document = _import(workspace, [source])[source.name]

    layout = LayoutService(workspace)
    page = layout.pages()[0]
    item = layout.create_item(
        document,
        page,
        original_size=True,
        fallback_dpi=300,
        min_long_edge_mm=ID1_LONG_EDGE_MM,
    )
    assert item.was_enlarged is False
    # 210 mm wide does not fit the 190 mm printable width, so it is reduced -
    # but never enlarged and never snapped to the card size.
    assert item.was_scaled_to_fit is True
    assert item.width == pytest.approx(190.0, abs=0.5)


def test_setting_defaults_to_on(workspace):
    settings = SettingsService(workspace.project.folder / "snap_settings.json")
    data = settings.load()
    assert data["layout"]["snap_small_cards"] is True
