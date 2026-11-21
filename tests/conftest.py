"""Shared pytest fixtures: temporary projects and real image files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.project_service import ProjectService  # noqa: E402
from core.settings_service import SettingsService  # noqa: E402
from database.app_database import AppDatabase  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    """One ``QApplication`` shared by every Qt test module.

    A module-scoped fixture created a *second* application once the first module
    finished.  Destroying a ``QApplication`` and building another one spins Qt's
    event dispatcher during interpreter shutdown, so the whole test run reached
    100 % and then hung forever with the process burning CPU.  Session scope
    keeps a single application alive for the entire run.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    yield app


def make_image(
    path: Path,
    size: tuple[int, int] = (600, 400),
    fmt: str = "JPEG",
    color: tuple[int, int, int] = (235, 240, 250),
    text: str = "DOC",
) -> Path:
    """Create a real image file with visible content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([4, 4, size[0] - 5, size[1] - 5], outline=(20, 40, 90), width=3)
    draw.text((16, 16), text, fill=(10, 10, 10))
    image.save(path, format=fmt)
    return path


@pytest.fixture()
def settings(tmp_path: Path) -> SettingsService:
    service = SettingsService(tmp_path / "settings.json")
    service.load()
    service.set("general", "default_project_directory", str(tmp_path / "projects"))
    service.set("general", "default_export_directory", str(tmp_path / "exports"))
    return service


@pytest.fixture()
def app_db(tmp_path: Path):
    database = AppDatabase(tmp_path / "app_data.db")
    yield database
    database.close()


@pytest.fixture()
def project_service(app_db, settings) -> ProjectService:
    return ProjectService(app_db, settings)


@pytest.fixture()
def workspace(project_service):
    ws = project_service.create_project("Test Project", "fixture project")
    yield ws
    try:
        ws.close()
    except Exception:
        pass


@pytest.fixture()
def image_factory(tmp_path: Path):
    counter = {"value": 0}

    def _factory(
        fmt: str = "JPEG",
        size: tuple[int, int] = (600, 400),
        name: str | None = None,
        color: tuple[int, int, int] = (235, 240, 250),
    ) -> Path:
        counter["value"] += 1
        extension = {"JPEG": ".jpg", "PNG": ".png", "TIFF": ".tif", "BMP": ".bmp", "WEBP": ".webp"}[fmt]
        filename = name or f"scan_{counter['value']:03d}{extension}"
        return make_image(tmp_path / "sources" / filename, size, fmt, color, filename)
    return _factory


@pytest.fixture()
def exif_rotated_image(tmp_path: Path) -> Path:
    """JPEG tagged with EXIF orientation 6 (rotate 90 degrees clockwise)."""
    path = tmp_path / "sources" / "rotated.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 200), (250, 245, 235))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 120, 60], fill=(200, 30, 30))
    exif = image.getexif()
    exif[274] = 6  # Orientation: rotate 90 CW
    image.save(path, format="JPEG", exif=exif)
    return path
