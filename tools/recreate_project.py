"""Recreate a project from a folder of card images (used to restore a lost one).

Creates a real project through the application services, imports every card at
its real physical size and lays them out 2 x 2 on the first page.

Usage:
    py -3.14 tools\\recreate_project.py "H-1352_RAMESHWAR" "H-1352 RAMESHWAR"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.import_service import ImportService  # noqa: E402
from core.layout_service import LayoutService  # noqa: E402
from core.paths import PROJECT_DB_FILENAME  # noqa: E402
from core.project_service import ProjectService  # noqa: E402
from core.settings_service import (  # noqa: E402
    SUPPORTED_IMAGE_EXTENSIONS,
    SettingsService,
)
from database.app_database import AppDatabase  # noqa: E402

EXTS = tuple(SUPPORTED_IMAGE_EXTENSIONS)


def card_files(folder: Path) -> list[Path]:
    files = [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in EXTS
    ]
    # Prefer the "*_print" copies: they carry the corrected DPI, so placing them
    # at original size gives the true physical card size.
    printed = [path for path in files if path.stem.endswith("_print")]
    if printed:
        return printed
    return [path for path in files if not path.stem.endswith("_print")]


def main() -> int:
    args = [value for value in sys.argv[1:] if not value.startswith("--")]
    replace = "--replace" in sys.argv
    if not args:
        print(__doc__)
        return 2
    folder = Path(args[0]).resolve()
    name = args[1] if len(args) > 1 else folder.name
    if not folder.is_dir():
        print("folder not found:", folder)
        return 1

    settings = SettingsService()
    settings.load()
    app_db = AppDatabase()
    projects = ProjectService(app_db, settings)
    try:
        if replace:
            for known in projects.known_projects():
                if known.name == name:
                    workspace = projects.open_project(known.db_path)
                    print("replacing existing project:", workspace.project.folder)
                    projects.delete_project_files(workspace)
        workspace = projects.create_project(name, "Restored from " + folder.name)
        try:
            sources = card_files(folder)
            if not sources:
                print("no images in", folder)
                return 1
            importer = ImportService(workspace)
            previews = importer.analyze(sources, list(EXTS))
            category = workspace.database.categories.list_for_project(workspace.id)[0]
            summary = importer.commit(previews, category.id)
            print(f"imported {summary.imported_count} of {len(sources)} file(s)")

            layout = LayoutService(workspace)
            page = layout.pages()[0]
            documents = list(layout.documents().values())
            columns = 2
            for index, document in enumerate(documents):
                row, column = divmod(index, columns)
                item = layout.create_item(
                    document, page, original_size=True, fallback_dpi=300
                )
                x = 15.0 + column * 92.0
                y = 20.0 + row * 62.0
                item.position_x = min(x, max(0.0, page.width_mm - item.width))
                item.position_y = min(y, max(0.0, page.height_mm - item.height))
                layout.insert_item(item)
            workspace.touch()
            workspace.database.checkpoint()
            print("project folder:", workspace.project.folder)
            print("database      :", workspace.project.folder / PROJECT_DB_FILENAME)
            for item in layout.items(page.id):
                print(
                    f"  card {item.position_x:6.1f},{item.position_y:6.1f} mm "
                    f"{item.width:5.1f} x {item.height:5.1f} mm"
                )
        finally:
            workspace.close()
    finally:
        app_db.close()
    print("done - open it from the dashboard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
