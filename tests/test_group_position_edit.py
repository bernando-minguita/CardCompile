"""Properties panel X/Y edits with several images selected."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - environment guard
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover
    pytest.skip("PySide6 is not available", allow_module_level=True)

from core.import_service import ImportService  # noqa: E402
from core.settings_service import SUPPORTED_IMAGE_EXTENSIONS, SettingsService  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def editor(qt_app, workspace, tmp_path):
    from app.layout_editor import LayoutEditor

    sources = []
    for index in range(4):
        path = tmp_path / "sources" / f"card_{index}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (360, 226), (240, 240, 240)).save(path, format="JPEG")
        sources.append(path)
    importer = ImportService(workspace)
    previews = importer.analyze(sources, list(SUPPORTED_IMAGE_EXTENSIONS))
    category = workspace.database.categories.list_for_project(workspace.id)[0]
    importer.commit(previews, category.id)

    settings = SettingsService(workspace.project.folder / "editor_settings.json")
    settings.load()
    settings.set("layout", "snap_small_cards", False)
    editor = LayoutEditor(workspace, settings)
    editor.resize(1000, 800)
    editor.show()
    QApplication.processEvents()
    yield editor
    editor.setParent(None)
    editor.deleteLater()


@pytest.fixture()
def panel(editor):
    from app.properties_panel import PropertiesPanel

    widget = PropertiesPanel()
    widget.attach_editor(editor)
    QApplication.processEvents()
    yield widget
    widget.setParent(None)
    widget.deleteLater()


def _place(editor, positions):
    """Add one image per position, arrange them and select them all."""
    documents = list(editor.layout.documents().values())
    editor.add_documents(documents[: len(positions)])
    QApplication.processEvents()
    graphics = editor.scene.image_items()
    assert len(graphics) == len(positions), "the fixture placed the wrong images"
    models = {}
    for graphic, (x, y) in zip(graphics, positions):
        model = graphic.model
        model.position_x, model.position_y = float(x), float(y)
        editor.layout.save_item(model)
        editor.scene.refresh_item(model)
        models[graphic.item_id] = model
    editor.select_all()
    QApplication.processEvents()
    return models


POSITIONS = [(10.0, 20.0), (70.0, 55.0), (40.0, 110.0)]


def test_panel_shows_the_top_left_corner_of_the_group(editor, panel):
    _place(editor, POSITIONS)

    assert panel.x_spin.value() == pytest.approx(10.0)
    assert panel.y_spin.value() == pytest.approx(20.0)
    assert "3 images" in panel.item_title.text()


def test_typing_x_shifts_every_image_and_keeps_the_spacing(editor, panel):
    models = _place(editor, POSITIONS)
    before = {key: (m.position_x, m.position_y) for key, m in models.items()}

    panel.x_spin.setValue(40.0)
    QApplication.processEvents()

    for key, model in models.items():
        assert model.position_x == pytest.approx(before[key][0] + 30.0)
        assert model.position_y == pytest.approx(before[key][1])
    assert panel.x_spin.value() == pytest.approx(40.0)


def test_group_stops_at_the_page_edge_instead_of_collapsing(editor, panel):
    models = _place(editor, POSITIONS)
    page = editor.current_page()
    gaps = sorted(m.position_x for m in models.values())

    panel.x_spin.setValue(page.width_mm + 500.0)
    QApplication.processEvents()

    right_edges = [m.position_x + m.width for m in models.values()]
    assert max(right_edges) == pytest.approx(page.width_mm, abs=0.02)
    after = sorted(m.position_x for m in models.values())
    assert [b - a for a, b in zip(after, after[1:])] == pytest.approx(
        [b - a for a, b in zip(gaps, gaps[1:])]
    )
    assert panel.x_spin.value() == pytest.approx(min(m.position_x for m in models.values()))


def test_one_undo_reverts_the_whole_group_move(editor, panel):
    models = _place(editor, POSITIONS)
    before = {key: (m.position_x, m.position_y) for key, m in models.items()}

    panel.x_spin.setValue(40.0)
    QApplication.processEvents()
    assert editor.undo_stack.canUndo()

    editor.undo_stack.undo()
    QApplication.processEvents()

    for key, model in models.items():
        assert (model.position_x, model.position_y) == pytest.approx(before[key])
    assert panel.x_spin.value() == pytest.approx(10.0)


def test_arrow_steps_merge_into_a_single_undo_entry(editor, panel):
    _place(editor, POSITIONS)
    start = panel.x_spin.value()
    count = editor.undo_stack.count()

    panel.x_spin.setValue(start + 1.0)
    panel.x_spin.setValue(start + 2.0)
    QApplication.processEvents()

    assert editor.undo_stack.count() == count + 1, "each arrow step became its own undo entry"
    editor.undo_stack.undo()
    QApplication.processEvents()
    assert panel.x_spin.value() == pytest.approx(start)
