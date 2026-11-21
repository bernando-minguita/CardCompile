"""Headless Qt smoke test for the editor, properties panel and page navigator.

Run with:  set QT_QPA_PLATFORM=offscreen && py -3.14 tests/qt_smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.layout_editor import LayoutEditor  # noqa: E402
from app.page_manager import PageNavigator  # noqa: E402
from app.properties_panel import PropertiesPanel  # noqa: E402
from app.theme import apply_theme  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.project_service import ProjectService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService  # noqa: E402
from database.app_database import AppDatabase  # noqa: E402
from tests.conftest import make_image  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)

    tmp = Path(tempfile.mkdtemp(prefix="dpb_qt_"))
    settings = SettingsService(tmp / "settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp / "projects"))
    app_db = AppDatabase(tmp / "app_data.db")
    service = ProjectService(app_db, settings)
    workspace = service.create_project("Qt Smoke", page_size="A4")

    paths = [
        make_image(tmp / "sources" / f"doc{i}.jpg", size=(800 + i * 40, 600)) for i in range(3)
    ]
    importer = ImportService(workspace)
    previews = importer.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    summary = importer.commit(previews, category.id)
    print("imported:", summary.imported_count)

    editor = LayoutEditor(workspace, settings)
    editor.resize(1000, 800)
    navigator = PageNavigator()
    navigator.attach_editor(editor)
    properties = PropertiesPanel()
    properties.attach_editor(editor)
    app.processEvents()

    documents = list(editor.layout.documents().values())
    added = editor.add_documents(documents[:2])
    print("added to page:", added, "scene items:", len(editor.scene.image_items()))
    assert len(editor.scene.image_items()) == 2

    editor.select_all()
    print("selected:", len(editor.selected_models()))
    properties.refresh()
    print("panel X:", properties.x_spin.value(), "W:", properties.w_spin.value())

    editor.rotate_selection(90)
    print("rotation after 90:", editor.selected_models()[0].rotation)
    assert editor.selected_models()[0].rotation == 90.0

    editor.apply_item_geometry(
        editor.selected_models()[0].id, {"position_x": 20.0, "position_y": 30.0}
    )
    moved = editor.layout.db.items.get(editor.selected_models()[0].id)
    print("moved to:", moved.position_x, moved.position_y)

    editor.undo_stack.undo()
    restored = editor.layout.db.items.get(moved.id)
    print("after undo x:", restored.position_x)
    editor.undo_stack.redo()

    editor.align_selection("page_center_v")
    editor.apply_layout_preset("4")
    print("layout items on page 1:", len(editor.layout.items(editor.current_page_id)))

    editor.add_page()
    print("pages after add:", len(editor.pages()))
    editor.duplicate_current_page()
    print("pages after duplicate:", len(editor.pages()))
    editor.delete_current_page()
    print("pages after delete:", len(editor.pages()))

    editor.zoom_fit_page()
    print("zoom:", round(editor.zoom(), 3))
    navigator.rebuild()
    print("navigator rows:", navigator.list.count())
    assert navigator.list.count() == len(editor.pages())

    editor.select_all()
    editor.copy_selection()
    editor.paste_clipboard()
    print("items after paste:", len(editor.layout.items(editor.current_page_id)))

    editor.undo_stack.undo()
    editor.undo_stack.undo()
    editor.undo_stack.undo()
    print("undo chain ok, pages:", len(editor.pages()))

    if editor.scene.image_items():
        editor.scene.clearSelection()
        first = editor.scene.image_items()[0]
        first.setSelected(True)
        started = editor.scene.start_crop(first.item_id)
        print("crop started:", started)
        editor.scene.exit_crop_mode()

    workspace.close()
    app_db.close()
    print("QT-SMOKE-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
