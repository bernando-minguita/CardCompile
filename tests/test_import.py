"""Image import tests: formats, validation, duplicates, EXIF, missing files."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.document_service import DocumentService
from core.image_service import ImageValidationError, load_rgb, probe_image
from core.import_service import ImportService, analyze_file, expand_paths
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS


def _import(workspace, paths, include_duplicates=True):
    service = ImportService(workspace)
    previews = service.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    summary = service.commit(
        previews, category.id, include_duplicates=include_duplicates
    )
    return previews, summary


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "TIFF", "BMP", "WEBP"])
def test_import_supported_formats(workspace, image_factory, fmt):
    source = image_factory(fmt=fmt)
    _, summary = _import(workspace, [source])
    assert summary.imported_count == 1
    documents = workspace.database.documents.list_for_project(workspace.id)
    assert len(documents) == 1
    document = documents[0]
    assert document.original_width == 600
    assert document.original_height == 400
    assert document.file_size > 0
    assert Path(document.thumbnail_path).is_file()
    assert document.source_path == str(source)
    assert source.is_file(), "the original file must stay in place"


def test_import_folder_recursive(workspace, image_factory, tmp_path):
    image_factory(fmt="JPEG")
    image_factory(fmt="PNG")
    nested = tmp_path / "sources" / "sub" / "deeper"
    nested.mkdir(parents=True, exist_ok=True)
    from tests.conftest import make_image

    make_image(nested / "nested.jpg", (300, 300))
    files = expand_paths([tmp_path / "sources"], list(SUPPORTED_IMAGE_EXTENSIONS))
    assert len(files) == 3
    _, summary = _import(workspace, [tmp_path / "sources"])
    assert summary.imported_count == 3


def test_invalid_file_is_reported(workspace, tmp_path):
    broken = tmp_path / "sources" / "broken.jpg"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"this is not an image")
    preview = analyze_file(broken, {})
    assert preview.status == "invalid"
    assert preview.message
    _, summary = _import(workspace, [broken])
    assert summary.imported_count == 0
    assert summary.invalid == 1


def test_empty_file_is_rejected(tmp_path):
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(ImageValidationError):
        probe_image(empty)


def test_duplicate_detection(workspace, image_factory):
    source = image_factory(fmt="JPEG")
    duplicates = source.parent / "copy_of_scan.jpg"
    duplicates.write_bytes(source.read_bytes())

    previews, summary = _import(workspace, [source, duplicates])
    assert summary.imported_count == 2, "duplicates are imported when allowed"
    assert any(p.status == "duplicate" for p in previews), "second file flagged"

    previews, summary = _import(workspace, [source], include_duplicates=False)
    assert summary.imported_count == 0
    assert summary.skipped == 1


def test_duplicate_not_deleted_from_disk(workspace, image_factory):
    source = image_factory(fmt="PNG")
    _import(workspace, [source])
    _import(workspace, [source], include_duplicates=False)
    assert source.is_file()


def test_exif_orientation_applied(workspace, exif_rotated_image):
    raw = probe_image(exif_rotated_image)
    assert (raw.width, raw.height) == (200, 400), "EXIF orientation rotates dimensions"
    image = load_rgb(exif_rotated_image)
    assert image.size == (200, 400), "EXIF orientation must rotate the pixels"
    _, summary = _import(workspace, [exif_rotated_image])
    assert summary.imported_count == 1
    document = workspace.database.documents.list_for_project(workspace.id)[0]
    assert (document.original_width, document.original_height) == (200, 400), "Stored dims match rotated orientation"


def test_missing_source_detected(workspace, image_factory):
    source = image_factory(fmt="JPEG")
    _import(workspace, [source])
    source.unlink()
    service = DocumentService(workspace)
    missing = service.missing_documents()
    assert len(missing) == 1
    assert service.verify_hash(missing[0]) is None


def test_reconnect_replacement(workspace, image_factory, tmp_path):
    source = image_factory(fmt="JPEG")
    _import(workspace, [source])
    original_bytes = source.read_bytes()
    source.unlink()
    service = DocumentService(workspace)
    document = service.missing_documents()[0]

    replacement = tmp_path / "restored" / document.filename
    replacement.parent.mkdir(parents=True, exist_ok=True)
    replacement.write_bytes(original_bytes)

    candidate = service.validate_replacement(document, replacement)
    assert candidate.hash_matches is True
    assert service.reconnect(candidate, update_hash=False) is True
    reloaded = workspace.database.documents.get(document.id)
    assert reloaded.source_path == str(replacement)
    assert service.missing_documents() == []


def test_scan_folder_for_missing(workspace, image_factory, tmp_path):
    source = image_factory(fmt="PNG")
    _import(workspace, [source])
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / source.name).write_bytes(source.read_bytes())
    source.unlink()

    service = DocumentService(workspace)
    proposals = service.scan_folder_for_missing(backup)
    assert len(proposals) == 1
    assert proposals[0].hash_matches is True


def test_thumbnail_size_respected(workspace, image_factory):
    from PIL import Image

    source = image_factory(fmt="JPEG", size=(3000, 2000))
    _import(workspace, [source])
    document = workspace.database.documents.list_for_project(workspace.id)[0]
    with Image.open(document.thumbnail_path) as thumbnail:
        assert max(thumbnail.size) <= 240
