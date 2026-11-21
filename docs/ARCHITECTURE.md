# CardCompile Architecture

## Overview

CardCompile is a desktop application for turning scanned or cropped images into a polished PDF document. The workflow is intentionally simple:

1. Import source images into a project.
2. Organize them by category and metadata.
3. Place images on real-size layout pages.
4. Crop, resize, rotate, label, and align content.
5. Export the final document as a PDF.

The application is built with Python 3.14 and PySide6, and it keeps the original source files untouched by storing references, metadata, and project instructions rather than duplicating the originals.

## High-level structure

```text
main.py
├── app/
│   ├── application.py          # startup bootstrap, logging, app lifecycle
│   ├── dashboard.py            # dashboard/navigation landing screen
│   ├── editor_workspace.py     # main layout editing surface
│   ├── main_window.py          # top-level window, menus, toolbar, status bar
│   ├── projects_page.py        # project registry and file-open workflow
│   ├── export_dialog.py        # PDF export settings and actions
│   ├── preview_window.py       # PDF preview viewer
│   └── ...                     # dialogs, property panels, organizer UI
├── core/
│   ├── import_service.py       # import engine for files/folders
│   ├── layout_service.py       # page layout calculations and geometry
│   ├── pdf_service.py          # PDF generation and validation
│   ├── project_service.py      # project creation, opening, repair, metadata
│   ├── settings_service.py     # persisted settings and validation
│   ├── recovery_service.py     # crash-recovery marker logic
│   └── ...                     # image, document, paper, validation utilities
├── database/
│   ├── app_database.py         # application-level registry of projects
│   ├── project_database.py     # per-project SQLite database wrapper
│   ├── schema.sql              # project schema
│   └── repositories/           # project and document repositories
├── graphics/
│   ├── page_scene.py           # canvas scene for page objects
│   ├── page_view.py            # interactive viewport and zoom/pan logic
│   ├── image_item.py           # image object on the page
│   ├── text_item.py            # label text objects
│   └── ...                     # crop overlays, guides, rendering helpers
├── models/
│   ├── project.py              # project metadata model
│   ├── document.py             # imported source document model
│   ├── page_item.py            # layout item model
│   └── ...                     # category, label, PDF page models
├── commands/
│   ├── move_command.py         # undoable movement actions
│   ├── resize_command.py       # undoable resize actions
│   ├── crop_command.py         # crop interaction logic
│   ├── layout_command.py       # arrangement actions
│   └── ...                     # page, rotation, label, delete commands
├── resources/
│   ├── icons/                  # app and toolbar icons
│   ├── styles/                 # Qt stylesheet themes
│   └── ...
├── tests/
│   └── ...                     # QA coverage for imports, layout, export, UI
├── docs/
│   ├── ARCHITECTURE.md         # this document
│   └── USER_GUIDE.md           # end-to-end user workflow
├── requirements.txt
├── build_exe.bat
├── CardCompile.spec
└── README.md
```

## Runtime flow

### 1. Application bootstrap

`main.py` initializes Python import paths and starts `app.application.run_application`. The bootstrap step:

- configures logging
- creates the application database
- loads user settings
- opens the main window
- triggers startup prompts for recovery or project selection

### 2. Project lifecycle

The project system is centered around `ProjectService` and `ProjectDatabase`.

- projects are created in a dedicated folder outside the app installation directory
- each project stores its own SQLite database (`project.db`)
- project metadata, pages, items, categories, and export history are persisted locally
- recovery markers and autosave logic avoid losing edits after crashes

### 3. Import and document library

The import pipeline reads file lists and folders, validates images, computes metadata (size, DPI, checksum, EXIF), and stores references to each source file.

Important traits:

- the original files remain in place
- imported images are stored in a library, not placed on a page by default
- duplicates are detected using file hashes
- missing files are tracked and can be repaired later

### 4. Layout editor

The editor uses Qt scene/view objects layered over a page system.

- `graphics/page_scene.py` holds page items and selection logic
- `graphics/page_view.py` handles zoom, pan, and interaction
- `commands/` implements undoable changes for move, crop, resize, rotate, and alignment
- `core/layout_service.py` provides geometric calculations for page sizing, arrangement, and original-size placement

This allows an image to remain editable as a real page object while preserving the underlying source file and metadata.

### 5. Export pipeline

When the user exports a PDF:

- the selected page set is assembled
- objects are converted to print-ready PDF content
- PDF metadata, quality, compression, and optional password settings are applied
- the file is written atomically and validated
- the preview window can open the generated PDF for review

`core/pdf_service.py` is responsible for generating the final PDF and validating the output.

## Design principles

### Local-first

Everything is kept on the user machine. The app does not upload scans or project data to any cloud service.

### Non-destructive editing

Source images are never modified. The app keeps the original file path, checksum, size, and DPI data and stores edits as project instructions.

### Editable history

The app supports undo/redo, crop reset, alignment operations, and project autosave. This keeps the editor responsive without sacrificing recoverability.

### Real-page layout

The app distinguishes between source-document metadata and physical page placement. Images can be laid out at their original size or scaled to known card dimensions while preserving aspect ratio and print accuracy.

## Key dependencies

- PySide6: desktop UI and Qt integration
- Pillow: image decoding, metadata, thumbnails, and crop operations
- PyMuPDF: PDF generation, preview rendering, and validation
- SQLite: project storage and application registry

## Notes on extension

The architecture is organized to keep UI, geometry, persistence, and export functions separated. That makes it practical to add new page layouts, new import sources, or new export formats without rewriting the whole app.
