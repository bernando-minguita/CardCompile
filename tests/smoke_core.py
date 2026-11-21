"""Smoke test: project creation, persistence and reopening without the GUI."""

import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.paths import PROJECT_DB_FILENAME  # noqa: E402
from core.project_service import ProjectService  # noqa: E402
from core.settings_service import SettingsService  # noqa: E402
from database.app_database import AppDatabase  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp(prefix="dpb_smoke_"))
settings = SettingsService(tmp / "settings.json")
settings.load()
settings.set("general", "default_project_directory", str(tmp / "projects"))
app_db = AppDatabase(tmp / "app_data.db")
service = ProjectService(app_db, settings)

ws = service.create_project("Smoke Project", "desc", page_size="A4", orientation="portrait")
print("project id:", ws.id[:8], "folder:", ws.project.folder.name)
print("db exists:", ws.db_path.is_file(), "schema:", ws.database.db.schema_version())
cats = ws.database.categories.list_for_project(ws.id)
print("categories:", [c.name for c in cats])
pages = ws.database.pages.list_for_project(ws.id)
print("pages:", [(p.page_number, p.width_mm, p.height_mm) for p in pages])
ws.database.pages.list_for_project(ws.id)
second = service.create_project("Smoke Project", "duplicate name")
print("folder collision handled:", second.project.folder.name)
ws.touch()
ws.close()
second.close()

reopened = service.open_project(tmp / "projects" / "Smoke Project" / PROJECT_DB_FILENAME)
print("reopened name:", reopened.name)
print("reopened pages:", reopened.database.pages.count(reopened.id))
summary = service.project_summary(reopened.db_path)
print("summary:", summary.name, summary.document_count, summary.page_count, summary.valid)
reopened.close()

print("registered projects:", [p.name for p in service.recent_projects()])
app_db.close()
shutil.rmtree(tmp, ignore_errors=True)
print("SMOKE-OK")
