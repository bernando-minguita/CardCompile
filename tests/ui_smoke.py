"""End to end headless UI test: window, editor, export and preview.

Run with:  py -3.14 tests/ui_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPoint, QPointF  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSceneMouseEvent,
    QMessageBox,
    QToolButton,
)

from app.context import AppContext  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.preview_window import PreviewWindow  # noqa: E402
from app.theme import apply_theme  # noqa: E402
from core.import_service import ImportService  # noqa: E402
from core.pdf_service import ExportOptions, PdfService  # noqa: E402
from core.project_service import ProjectService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService  # noqa: E402
from core.validation_service import ValidationService  # noqa: E402
from database.app_database import AppDatabase  # noqa: E402
from tests.conftest import make_image  # noqa: E402


def check_project_recovery(window, app, tmp: Path) -> list[str]:
    """A moved project must be re-linkable instead of being a dead end."""
    from core import paths as paths_module

    problems: list[str] = []
    projects = window.context.projects
    workspace = projects.create_project("Moved Project", page_size="A4")
    project_id = workspace.id
    folder = workspace.project.folder
    workspace.close()

    target = tmp / "relocated"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(target))

    offers: list[str] = []
    original = window._offer_missing_project
    window._offer_missing_project = lambda known: offers.append(known.id)  # type: ignore[assignment]
    try:
        window.open_recent_project(project_id)
        app.processEvents()
    finally:
        window._offer_missing_project = original  # type: ignore[assignment]
    print("missing project offered:", offers)
    if offers != [project_id]:
        problems.append("opening a missing project did not offer the locate flow")

    reopened = projects.relink_project(project_id, target / "project.db")
    print("relinked folder:", reopened.project.folder)
    if Path(reopened.project.folder) != target:
        problems.append("relink did not follow the new folder")
    window._attach_workspace(reopened)
    app.processEvents()
    if window.workspace is None or window.workspace.id != project_id:
        problems.append("relinked project was not attached")

    # A project folder inside the application folder must be reported as unsafe.
    configured = window.context.settings.get("general", "default_project_directory")
    window.context.settings.set(
        "general", "default_project_directory", str(paths_module.app_root() / "projects")
    )
    unsafe = projects.unsafe_project_folder()
    resolved = projects.default_project_folder()
    print("unsafe folder detection:", unsafe, "-> falling back to", resolved)
    if unsafe is None:
        problems.append("application folder was not detected as unsafe")
    if paths_module.is_inside_app_folder(resolved):
        problems.append("fallback project folder is still inside the application folder")

    # Startup questions must run in sequence and stop nagging once answered.
    from core.recovery_service import clear_marker

    clear_marker()
    window.context.settings.set("general", "reopen_last_project", False)
    window.context.settings.set("general", "allow_unsafe_project_folder", True)
    if projects.unsafe_project_folder() is not None:
        problems.append("the remembered 'keep my folder' choice was not honoured")
    try:
        window.startup_prompts()  # must return without opening any dialog
        app.processEvents()
        print("startup prompts with no pending questions: sequential, no dialogs")
    except Exception as exc:  # pragma: no cover - defensive
        problems.append(f"startup prompts failed: {exc!r}")
    window.context.settings.set("general", "allow_unsafe_project_folder", False)
    window.context.settings.set("general", "default_project_directory", configured)
    return problems


def check_report_fixes(window, app, tmp: Path) -> list[str]:
    """Rotate handle, live rotation, preview page/fit, save dialog, duplicate."""
    from PySide6.QtCore import QPointF

    problems: list[str] = []
    editor = window.editor
    page_id = editor.current_page_id
    items = editor.layout.items(page_id)
    if not items:
        return ["no items on the page to test with"]
    item = items[0]

    # --- the round rotate handle must be clickable (part of the hit shape)
    graphic = editor.scene.item_by_id(item.id)
    editor.select_all()
    app.processEvents()
    handle_point = QPointF(item.width / 2.0, -graphic._rotate_offset())
    reachable = graphic.shape().contains(handle_point)
    print("rotate handle inside the hit area:", reachable)
    if not reachable:
        problems.append("the rotate handle cannot be clicked (outside the shape)")

    # --- rotation from the properties panel is live
    editor.scene.clearSelection()
    graphic.setSelected(True)
    app.processEvents()
    window.editor_workspace.properties.rotation_spin.setValue(25.0)
    app.processEvents()
    stored = editor.layout.db.items.get(item.id)
    scene_rotation = graphic.rotation()
    print("live rotation:", stored.rotation, "canvas:", round(scene_rotation, 1))
    if abs(stored.rotation - 25.0) > 0.01 or abs(scene_rotation - 25.0) > 0.01:
        problems.append("rotation from the properties panel is not applied live")

    # --- preview opens on the edited page and fits it
    dialogs: list[str] = []
    original_information = QMessageBox.information
    QMessageBox.information = staticmethod(  # type: ignore[assignment]
        lambda _parent, title, text, *a, **k: dialogs.append(f"{title}: {text}")
    )
    try:
        window.preview_pdf()
        app.processEvents()
        preview = window._preview_window
        pages = editor.layout.pages()
        expected = next(
            (i for i, page in enumerate(pages) if page.id == page_id), 0
        )
        print(
            "preview page:", preview._current if preview else None,
            "expected:", expected,
            "zoom:", round(preview._zoom, 2) if preview else None,
        )
        if preview is None:
            problems.append("the preview window was not created")
        else:
            if preview._current != expected:
                problems.append("the preview did not open on the edited page")
            if preview._zoom < 0.25:
                problems.append(f"the preview is not fitted ({preview._zoom:.0%})")
            preview.close()
            app.processEvents()

        # --- saving shows a confirmation
        dialogs.clear()
        window.save_project()
        app.processEvents()
        print("save dialogs:", dialogs)
        if not any("saved" in text.lower() for text in dialogs):
            problems.append("no confirmation is shown after saving")
    finally:
        QMessageBox.information = original_information

    # --- duplicate from the context menu must not crash
    editor.select_all()
    app.processEvents()
    before = len(editor.layout.items(page_id))
    menu = editor._build_item_menu(item.id)
    duplicate = next(a for a in menu.actions() if a.text() == "Duplicate")
    duplicate.trigger()
    app.processEvents()
    after = len(editor.layout.items(page_id))
    print("duplicate via menu:", before, "->", after)
    if after <= before:
        problems.append("the context menu duplicate did nothing")

    # --- a text label can be added and shows up on the canvas
    label_id = editor.create_label({"text": "SMOKE LABEL", "font_size": 14.0})
    app.processEvents()
    print("label created:", bool(label_id), "on canvas:", editor.scene.label_by_id(label_id) is not None)
    if not label_id or editor.scene.label_by_id(label_id) is None:
        problems.append("a text label could not be added to the page")
    return problems


def check_editor_basics(window, app) -> list[str]:
    """Fit zoom, placement, persistence and drops (reported 1.2.3 issues)."""
    problems: list[str] = []
    editor = window.editor

    zoom = editor.zoom()
    print("zoom after load:", round(zoom * 100, 1), "%")
    if zoom < 0.4:
        problems.append(f"the page was not fitted on load ({zoom:.0%})")

    window.add_page()
    app.processEvents()
    docs = list(editor.layout.documents().values())
    editor.add_documents(docs[:2])
    app.processEvents()
    page_id = editor.current_page_id
    items = editor.layout.items(page_id)
    positions = {(round(i.position_x, 1), round(i.position_y, 1)) for i in items}
    print("new image positions:", sorted(positions), "of", len(items), "items")
    if len(positions) < len(items):
        problems.append("new images were stacked on the same spot")

    first = items[0]
    graphic = editor.scene.item_by_id(first.id)
    graphic.interaction_started.emit(first.id)
    graphic.model.position_y = 15.0
    graphic.sync_from_model()
    graphic.interaction_finished.emit(first.id)
    app.processEvents()
    if abs(editor.layout.db.items.get(first.id).position_y - 15.0) > 0.01:
        problems.append("a dragged image was not saved to the project")

    editor.add_documents(docs[2:3])
    app.processEvents()
    after = editor.layout.db.items.get(first.id)
    if abs(after.position_y - 15.0) > 0.01:
        problems.append("a moved image jumped back after adding another one")

    before_count = len(editor.layout.items(page_id))
    editor._on_documents_dropped([docs[0].id], QPointF(70.0, 210.0))
    app.processEvents()
    added = len(editor.layout.items(page_id)) - before_count
    print("items added by a drop:", added)
    if added != 1:
        problems.append("dropping a document on the page added nothing")

    editor.select_all()
    app.processEvents()
    print("selected after select-all:", len(editor.selected_ids()))
    return problems


def check_item_interaction(window, app) -> list[str]:
    """Right click, double click, preview and toolbar must all work (1.1.1)."""
    problems: list[str] = []
    item = window.editor.scene.image_items()[0]

    menu = window.editor._build_item_menu(item.item_id)
    if menu is None:
        problems.append("context menu could not be built")
    else:
        labels = [action.text() for action in menu.actions() if action.text()]
        print("context menu entries:", len(labels))
        if "Restore original size" not in labels:
            problems.append("context menu has no 'Restore original size'")
        menu.deleteLater()

    doubles: list[str] = []
    window.editor.scene.double_click_requested.disconnect()
    item.double_clicked.connect(doubles.append)
    event = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMouseDoubleClick)
    event.setScenePos(QPointF(3.0, 3.0))
    event.setScreenPos(QPoint(3, 3))
    item.mouseDoubleClickEvent(event)
    print("double click signals:", len(doubles))
    if not doubles:
        problems.append("double click did not emit its signal")

    dialogs: list[str] = []
    original_critical = QMessageBox.critical
    original_warning = QMessageBox.warning
    QMessageBox.critical = staticmethod(  # type: ignore[assignment]
        lambda _parent, _title, text, *a, **k: dialogs.append(str(text))
    )
    QMessageBox.warning = staticmethod(  # type: ignore[assignment]
        lambda _parent, _title, text, *a, **k: dialogs.append(str(text))
    )
    try:
        window.preview_pdf()
        app.processEvents()
    finally:
        QMessageBox.critical = original_critical
        QMessageBox.warning = original_warning
    print("preview window:", window._preview_window is not None, "dialogs:", dialogs)
    if dialogs:
        problems.append(f"preview showed an error: {dialogs[0].splitlines()[0]}")
    if window._preview_window is None:
        problems.append("preview window was not created")
    else:
        window._preview_window.close()
        app.processEvents()

    toggle = window.editor_workspace.original_size_toggle
    bar = toggle.parentWidget()
    needed = sum(
        button.sizeHint().width() for button in bar.findChildren(QToolButton)
    )
    print("toolbar needed:", needed, "available:", bar.width())
    if not toggle.isVisible():
        problems.append("the original size toggle is not visible")
    if needed > bar.width():
        problems.append("the layout toolbar overflows")

    # Real card size (85.6 x 54 mm) must be reachable from the menu and work.
    window.select_all()
    window.make_card_size()
    app.processEvents()
    items = window.editor.layout.items(window.editor.current_page_id)
    width = max((item.width for item in items), default=0.0)
    print("card size applied, widest item:", round(width, 2), "mm")
    if abs(width - 85.6) > 0.3:
        problems.append(f"card size action produced {width:.1f} mm instead of 85.6")
    window.undo()
    app.processEvents()
    return problems


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)

    tmp = Path(tempfile.mkdtemp(prefix="dpb_ui_"))
    settings = SettingsService(tmp / "settings.json")
    settings.load()
    settings.set("general", "default_project_directory", str(tmp / "projects"))
    settings.set("general", "default_export_directory", str(tmp / "exports"))
    settings.save()
    app_db = AppDatabase(tmp / "app_data.db")
    projects = ProjectService(app_db, settings)
    context = AppContext(settings=settings, app_db=app_db, projects=projects)

    window = MainWindow(context)
    window.resize(1500, 950)
    window.show()
    app.processEvents()
    print("window created:", window.windowTitle())

    workspace = projects.create_project("UI Smoke", page_size="A4")
    window._attach_workspace(workspace)
    app.processEvents()
    print("editor attached:", window.editor is not None)
    print("status pages:", window.status_pages.text())

    paths = [
        make_image(tmp / "sources" / f"scan_{index}.jpg", size=(900 + index * 30, 650))
        for index in range(4)
    ]
    importer = ImportService(workspace)
    previews = importer.analyze(paths, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    summary = importer.commit(previews, category.id)
    print("imported:", summary.imported_count)

    window.editor_workspace.organizer.set_workspace(workspace)
    window.editor_workspace.organizer.reload()
    app.processEvents()
    print("organizer cards:", window.editor_workspace.organizer.list.count())

    documents = list(window.editor.layout.documents().values())
    window.editor_workspace._add_documents_to_page([doc.id for doc in documents])
    app.processEvents()
    print("items after add:", len(window.editor.layout.items(window.editor.current_page_id)))

    window.apply_preset("4")
    app.processEvents()
    print("items after preset 4:", len(window.editor.layout.items(window.editor.current_page_id)))

    window.select_all()
    window.rotate(90)
    window.align("page_center")
    window.copy()
    window.paste()
    app.processEvents()
    print("items after paste:", len(window.editor.layout.items(window.editor.current_page_id)))
    window.undo()
    window.undo()
    app.processEvents()

    window.add_page()
    app.processEvents()
    print("pages:", window.status_pages.text())
    window.delete_page()
    app.processEvents()

    window.save_project(confirm=False)
    print("save status:", window.status_save.text())

    output = tmp / "exports" / "ui_smoke.pdf"
    result = PdfService(window.editor.layout).export_project(
        output, ExportOptions(quality="Standard")
    )
    print("exported pages:", result.page_count, "exists:", output.is_file())
    report = ValidationService().validate_pdf(
        output, expected_pages=len(window.editor.layout.pages())
    )
    print("validation ok:", report.ok, "warnings:", len(report.warnings))

    preview = PreviewWindow()
    preview.resize(1000, 800)
    loaded = preview.load_pdf(output)
    app.processEvents()
    print("preview loaded:", loaded, "pages:", preview._page_count)
    preview.go_to_page(0)
    preview.close()

    problems = check_editor_basics(window, app)
    problems += check_report_fixes(window, app, tmp)
    problems += check_item_interaction(window, app)
    problems += check_project_recovery(window, app, tmp)
    for problem in problems:
        print("PROBLEM:", problem)

    window.close_project()
    app.processEvents()
    print("project closed, pages label:", window.status_pages.text())

    app_db.close()
    if problems:
        print("UI-SMOKE-FAILED")
        return 1
    print("UI-SMOKE-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
