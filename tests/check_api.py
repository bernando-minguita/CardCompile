"""Structural check: verify every repository/service exposes its full API."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.project_service import ProjectService, ProjectWorkspace  # noqa: E402
from core.settings_service import SettingsService  # noqa: E402
from database.app_database import AppDatabase  # noqa: E402
from database.connection import Database  # noqa: E402
from database.project_database import ProjectDatabase  # noqa: E402
from database.repositories import (  # noqa: E402
    CategoryRepository,
    DocumentRepository,
    ExportRepository,
    PageItemRepository,
    PageRepository,
    ProjectRepository,
)

CHECKS = {
    Database: [
        "transaction",
        "execute",
        "executemany",
        "query_all",
        "query_one",
        "scalar",
        "init_schema",
        "schema_version",
        "table_exists",
        "vacuum",
        "close",
        "__enter__",
        "__exit__",
    ],
    SettingsService: [
        "load",
        "save",
        "get",
        "set",
        "update_section",
        "replace",
        "supported_extensions",
        "section",
        "data",
        "path",
    ],
    ProjectRepository: ["insert", "update", "save", "get", "first", "touch", "delete"],
    CategoryRepository: [
        "list_for_project",
        "get",
        "find_by_name",
        "create",
        "rename",
        "delete",
        "resequence",
        "ensure_defaults",
        "next_sort_order",
    ],
    DocumentRepository: [
        "list_for_project",
        "get",
        "find_by_hash",
        "existing_hashes",
        "next_sort_order",
        "insert",
        "update",
        "set_category",
        "set_categories",
        "set_sort_orders",
        "delete",
        "counts_by_category",
        "count",
        "_attach_page_numbers",
    ],
    PageRepository: [
        "list_for_project",
        "get",
        "count",
        "next_page_number",
        "insert",
        "update",
        "delete",
        "resequence",
    ],
    PageItemRepository: [
        "list_for_page",
        "list_for_project",
        "get",
        "count_for_page",
        "counts_by_page",
        "max_z_index",
        "insert",
        "update",
        "update_many",
        "delete",
        "delete_for_page",
        "set_z_orders",
    ],
    ExportRepository: ["insert", "list_for_project", "count", "delete"],
    ProjectDatabase: ["projects", "categories", "documents", "pages", "items", "exports"],
    AppDatabase: [
        "register_project",
        "touch_project",
        "rename_project",
        "forget_project",
        "list_projects",
        "get_project",
        "find_by_db_path",
        "count_projects",
        "last_project",
        "prune_missing",
        "add_recent_export",
        "recent_exports",
    ],
    ProjectService: [
        "known_projects",
        "recent_projects",
        "last_project",
        "default_project_folder",
        "unique_project_folder",
        "create_project",
        "open_project",
        "open_recent",
        "rename_project",
        "update_project_defaults",
        "forget_project",
        "delete_project_files",
        "project_summary",
    ],
    ProjectWorkspace: ["close", "touch", "id", "name", "db_path"],
}

failures = 0
for cls, names in CHECKS.items():
    missing = [name for name in names if not hasattr(cls, name)]
    status = "OK" if not missing else f"MISSING {missing}"
    if missing:
        failures += 1
    print(f"{cls.__name__:24s} {status}")

print("API-CHECK-FAILURES:", failures)
