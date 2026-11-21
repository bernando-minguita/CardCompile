"""Diagnose the project registry, settings and project files on this machine."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.paths import PROJECT_DB_FILENAME, user_data_dir  # noqa: E402


def show_settings(folder: Path) -> None:
    path = folder / "settings.json"
    print(f"\n== settings: {path} (exists={path.is_file()})")
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print("  unreadable:", exc)
        return
    general = data.get("general", {})
    for key in ("default_project_directory", "default_export_directory", "reopen_last_project"):
        print(f"  general.{key} = {general.get(key)!r}")


def show_registry(folder: Path) -> None:
    path = folder / "app_data.db"
    print(f"\n== registry: {path} (exists={path.is_file()})")
    if not path.is_file():
        return
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM known_projects ORDER BY last_opened_at DESC"
        ).fetchall()
        print(f"  known_projects: {len(rows)}")
        for row in rows:
            db = Path(row["db_path"])
            print(
                f"   - {row['name']!r} opened={row['last_opened_at']} "
                f"exists={db.is_file()} db={row['db_path']}"
            )
    except Exception as exc:
        print("  error:", exc)
    finally:
        connection.close()


def show_marker(folder: Path) -> None:
    path = folder / "recovery.json"
    print(f"\n== recovery marker: {path} (exists={path.is_file()})")
    if path.is_file():
        print("  ", path.read_text(encoding="utf-8").replace("\n", " "))


def inspect_project(db_path: Path) -> None:
    print(f"\n== project db: {db_path}")
    if not db_path.is_file():
        print("  MISSING")
        return
    print(f"  size: {db_path.stat().st_size} bytes")
    print(
        "  side files:",
        sorted(p.name for p in db_path.parent.glob("project.db*")),
    )
    try:
        connection = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        print("  tables:", tables)
        if "projects" in tables:
            rows = connection.execute("SELECT * FROM projects").fetchall()
            print(f"  projects rows: {len(rows)}")
            for row in rows:
                print(
                    f"   - id={row['id'][:8]} name={row['name']!r} "
                    f"folder={row['project_folder']}"
                )
        for table in ("categories", "documents", "pdf_pages", "page_items"):
            if table in tables:
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table}: {count}")
        if "schema_version" in tables:
            print(
                "  schema:",
                [r[0] for r in connection.execute("SELECT version FROM schema_version")],
            )
        connection.close()
    except Exception as exc:
        print("  OPEN FAILED:", exc)


def walk_projects_folder(folder: Path) -> None:
    print(f"\n== project folders under {folder} (exists={folder.is_dir()})")
    if not folder.is_dir():
        return
    for child in sorted(folder.iterdir()):
        if not child.is_dir():
            continue
        files = sorted(p.name for p in child.glob("*"))
        print(f"  {child.name}: {files}")


def main() -> int:
    data_folder = user_data_dir()
    print("user data folder:", data_folder)
    show_settings(data_folder)
    show_marker(data_folder)
    show_registry(data_folder)

    # Optional: inspect explicit project folders passed on the command line.
    if len(sys.argv) > 1:
        for raw in sys.argv[1:]:
            folder = Path(raw)
            walk_projects_folder(folder)
            if (folder / PROJECT_DB_FILENAME).is_file():
                inspect_project(folder / PROJECT_DB_FILENAME)
            elif folder.is_dir():
                for child in sorted(folder.iterdir()):
                    db = child / PROJECT_DB_FILENAME
                    if db.is_file():
                        inspect_project(db)
        return 0

    # Also look for settings next to the app and in the dist folder.
    show_settings(ROOT)
    show_settings(ROOT / "dist" / "CardCompile")

    candidates = [ROOT / "projects", ROOT / "dist" / "CardCompile" / "projects"]
    settings_file = data_folder / "settings.json"
    if settings_file.is_file():
        try:
            general = json.loads(settings_file.read_text(encoding="utf-8")).get("general", {})
            configured = general.get("default_project_directory")
            if configured:
                candidates.append(Path(configured))
        except Exception:
            pass
    for folder in candidates:
        walk_projects_folder(folder)
        if folder.is_dir():
            for child in sorted(folder.iterdir()):
                db = child / PROJECT_DB_FILENAME
                if db.is_file():
                    inspect_project(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
