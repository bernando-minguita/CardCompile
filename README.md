# CardCompile

**Compile scan cards into one PDF file.**

CardCompile is a Windows desktop application (Python 3.14 + PySide6).
Import scanned documents - **Iqama**, **Driving License**, **Istimara / Vehicle
Registration**, **Passport**, contracts, certificates or anything else you scan -
arrange them on PDF pages, and export one tidy PDF ready to print or send.

Your scans are never moved or modified, and every move, crop, rotation and label
stays editable and undoable right up to the export.

### What that looks like in practice

1. **Import** - files, one folder, or several folders at once.
2. **Organise** - every scan listed in the Image Library, with search and
   categories.
3. **Correct** - fix a phone photo: drag the four corners of a skewed card back
   to a true rectangle, and even out lighting, brightness, contrast, saturation
   and sharpness.
4. **Lay out** - place scans on real page sizes; move, crop, rotate, label.
5. **Export** - one PDF, in the quality you choose.

Importing only fills the library - nothing is placed on a page until you do it.

### Correcting phone photos

A card photographed by hand is rarely a true rectangle. Right-click a placed
image and choose **Perspective Correction...** (or press Ctrl+Shift+C). It is a
small, focused dialog - it straightens a skewed photo and evens out its
lighting, nothing more:

* **Left side** - the photo shown large, with four labelled corner handles
  (TL/TR/BR/BL). Drag a corner, or press 1-4 to pick one and nudge it with the
  arrow keys (Shift = 10 px). Scroll to zoom, then drag the photo to move around
  it - panning never changes the zoom level.
* **Right side** - a live preview of the straightened card. Tick *Before /
  after* and drag the split line to compare the skew with the result.
* **Lighting and tone** tab - brightness, contrast, saturation, sharpness and
  *Even out uneven lighting* for a hand shadow across the card. **Auto enhance**
  estimates sensible values from the image histogram.

Nothing is written until **Apply** is pressed, so Cancel is free. The corner
quad and the tone multipliers are stored as **settings**, never as pixels: the
original scan is untouched, the whole session is a single undo step (Ctrl+Z),
and everything is re-applied at full resolution when the PDF is exported.

**Reset perspective** / **Reset adjustments** in the same menu clear either half
without opening the dialog.


### Private by design

Everything runs locally. No cloud services, no telemetry, no OCR, no personal
data sent anywhere. Original scans are never modified - a project stores only
their path, checksum, size, resolution and a thumbnail.

---

## 1. Installation

Requirements:

* Windows 10 or Windows 11 (64 bit)
* Python 3.14 installed (`py -3.14 --version` must work)

Install the dependencies into the default `site-packages` of that interpreter:

```bat
py -3.14 -m pip install -r requirements.txt
```

Packages and the versions the application was verified with:

| Package | Version | Purpose |
|---|---|---|
| PySide6 | 6.12.0 | Desktop UI (Qt 6, abi3 wheel works on 3.14) |
| Pillow | 12.3.0 | Image decoding, EXIF orientation, thumbnails, cropping |
| PyMuPDF | 2.0.0 | PDF rendering, export and validation |

## 2. Start the application

```bat
py -3.14 main.py
```

or double-click `run_dev.bat`.

## 3. Build the Windows executable

```bat
build_exe.bat
```

The script installs PyInstaller and builds a **folder based** distribution
(most reliable for Qt plugins):

```
dist\CardCompile\CardCompile.exe
```

The executable does not require Python on the target machine. Project files,
scanned images and user data are never bundled - only the application itself.

The specification file (`CardCompile.spec`) bundles `resources\` and
`database\schema.sql`, and excludes tooling and optional scientific/ML packages
(pytest, scipy, pandas, torch, onnxruntime, ...) that PyMuPDF's hooks would
otherwise pull in. That keeps the distribution small and the build fast.

Verified build on this machine (Python 3.14.7, PyInstaller 6.22.3):

```
dist\CardCompile\CardCompile.exe     9.2 MB (launcher)
dist\CardCompile\                         242.0 MB total (Qt runtime included)
```

Starting the packaged executable writes
`dist\CardCompile\logs\application.log` with
`Starting CardCompile 1.0.0`, which confirms Qt, Pillow, PyMuPDF and
SQLite load correctly outside of Python.

## 4. Where data is stored

| What | Location |
|---|---|
| Application and logs | `<application folder>\logs\application.log` |
| Default project folder | `Documents\CardCompile\Projects\<project name>\` |
| Project database | `<project folder>\project.db` (SQLite) |
| Project thumbnails | `<project folder>\thumbnails\` |
| Default export folder | `Documents\CardCompile\Exports\` |
| Pasted images (Ctrl+V) | `Documents\CardCompile\Pasted images\` |
| User settings (JSON) | `%APPDATA%\CardCompile\settings.json` |
| Project registry | `%APPDATA%\CardCompile\app_data.db` |
| Recovery marker | `%APPDATA%\CardCompile\recovery.json` |

Projects and exports live **outside the application folder** on purpose: updating
or rebuilding the application replaces that folder, and files inside it would be
deleted. Both locations can be changed in *Settings > General*; the application
warns if the chosen folder is inside the application folder.

**Original scans are never copied into the project and never modified.** The
database stores the absolute path, the SHA-256 hash, the pixel size, the file
size, the **resolution (DPI)**, the format and the import date of every source
file. All edits (crop, rotation, position, size) are stored as instructions only.

Because the DPI is stored, a card that was cropped out of a scan and saved as
its own image is placed on the page at its real size (for example an ID-1 card
of 85.6 x 54 mm) instead of being stretched to the margins.

## 5. Feature overview

* Project management: create, open, save, save as, rename, close, recent
  projects, project information, SQLite database initialized from the current schema.
* Import: single files, multiple files, whole folders, several folders, drag and
  drop. Background analysis with progress and cancellation, duplicate detection
  via SHA-256, invalid file reporting, thumbnails, EXIF orientation.
* **Paste from the clipboard**: Ctrl+V places an image copied in another program
  (a Snipping Tool snip, a card copied from a viewer) on the current page. It is
  saved as a PNG in `Documents\CardCompile\Pasted images\`, imported
  into the Image Library and placed - undoable like any other placement.
  Ctrl+V still pastes the objects copied inside the application; the clipboard
  bitmap is used when nothing was copied there.
* **Image Library vs. PDF pages**: importing an image only adds it to the
  Image Library - it never creates pages and never places anything. The library
  panel shows `imported | placed | selected`, search (name, category, file type),
  category filter, six sort orders, a placement badge per card (`not placed`,
  `page 3`, `4 pages (1, 3, 5, 7)`), multi selection (click / Ctrl / Shift,
  "Select all shown") and the actions Import, Add to page, New pages, Preview,
  Category and Remove. Removing a library image asks for confirmation when it is
  placed and never touches the original file.
* Library (source images) and canvas (page objects) selections are independent:
  selecting in the panel never selects objects on the page and vice versa.
* Organizer features: category per document (Iqama, Driving License, Istimara,
  Passport, Other and custom categories), duplicate, replace image, full size
  preview, missing file detection and reconnection.
* **Original size placement**: cropped card exports are placed at their real
  physical size (pixels / image DPI) instead of being stretched to the printable
  area. Toggle **1:1** at the start of the layout toolbar, the
  **Keep original size** checkbox in the Image Library, or *Arrange >
  Place new images at original size*; a fallback DPI is used for files without
  resolution metadata, and "Restore original size" fixes images already on the
  page. The import dialog shows the resulting physical size.
* **True card size**: *Make it card size (85.6 x 54 mm)* in the page canvas
  context menu (right-click an image) or **Ctrl+Shift+K**, and *Set physical
  size...* with presets for ID-1 cards (Iqama, driving licence, Istimara),
  passport data pages and passport photos - for crops whose DPI tag does not
  match their pixels. Centre-preserving, page-clamped and undoable.
* **Text labels**: *Text* in the toolbar / *Arrange > Add text...* / Ctrl+T adds a
  caption or note with font family, size, bold/italic and colour. Labels are
  exported as real PDF text and can be moved, edited and deleted with undo.
* Layout editor: A4/A3/Letter/Legal/Custom pages, portrait/landscape, margins,
  grid, rulers, zoom (fit page, fit width, 10-800 % in 1.25x steps), drag, resize with aspect
  lock, rotation (90/180/custom angle), interactive crop, multi selection,
  **rotation-aware group alignment** (edges, centres, middles, equal-gap
  distribution, page centring - rotated cards align by their visible outline),
  layer order, locking, numeric property editing, context menu, clipboard.
* Automatic layout, split by scope: **Arrange on Page > Current page (placed
  images)** re-arranges only the objects on the active page (2 vertical,
  2 horizontal, 2x2, 3x2, fit all placed, arrange selected objects, custom rows
  and columns); **Selected library images** adds only the images selected in the
  library, to the current page or to automatically created new pages
  (one/two/four/six per page). Contain/cover/stretch fitting, spacing and
  overflow with automatic page creation. Images that are only in the library are
  never included.
* Pages: add, duplicate, delete, reorder, clear, change size/orientation, move
  images between pages, page navigator with thumbnails.
* Undo/redo for every edit, debounced auto-save, recovery marker after a crash.
* Export: whole project, current page, one PDF per page or only the documents of
  one category; Draft/Standard/High/Original quality, compression, optional
  password, metadata; atomic write, automatic validation and "open PDF"
  afterwards.
* PDF preview: renders the real generated PDF (PyMuPDF) with thumbnails, page
  navigation and zoom.

## 6. Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+N | New project |
| Ctrl+O | Open project |
| Ctrl+S | Save |
| Ctrl+Shift+S | Save as |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Ctrl+X / Ctrl+C / Ctrl+V | Cut / Copy / Paste (Ctrl+V also accepts a clipboard image) |
| Ctrl+D | Duplicate |
| Ctrl+A | Select all |
| Del | Delete selection |
| Ctrl+E | Export PDF |
| Ctrl+ + / Ctrl+ - | Zoom in / out |
| Ctrl+0 | Fit page |
| Ctrl+Mouse wheel | Zoom |
| Middle mouse drag / Space | Pan |

## 7. Tests

```bat
py -3.14 -m pytest
```

The suite covers imports (JPG/PNG/TIFF/BMP/WEBP, invalid files, duplicates, EXIF,
missing files), image processing (crop, resize, DPI, original preservation),
layout maths and grid behaviour, page overflow, project persistence, PDF export
(A4, landscape, multi page, quality, password, metadata, validation), settings
validation and recovery markers.

Additional headless UI smoke tests:

```bat
set QT_QPA_PLATFORM=offscreen
py -3.14 tests\qt_smoke.py
py -3.14 tests\ui_smoke.py
```

Continuous integration: `.github/workflows/tests.yml` runs the whole suite and
the UI smoke test on Windows (Python 3.14) for every push to `main` and every
pull request. `.github/workflows/build-windows.yml` builds the PyInstaller
distribution (with a boot check) on demand or for a `v*` tag and uploads it as
a workflow artefact.

## 8. Project structure

```
main.py                  entry point
app/                     PySide6 windows, dialogs, editor widgets, theme
core/                    services: projects, imports, images, layout, PDF, preview
models/                  dataclasses mirroring the database tables
database/                connection wrapper, schema.sql, migrations, repositories
graphics/                QGraphicsScene / view / image item / crop overlay / rulers
commands/                QUndoCommand implementations
resources/               style sheet and icons
tests/                   pytest suite and headless smoke tests
tools/                   development helpers
docs/                    architecture notes and the user guide
.github/workflows/       CI: tests, Windows build
LICENSE                  MIT
```

Nothing generated is committed: `build/`, `dist/` (the executable), `logs/`,
`projects/`, `exports/`, databases and every cache directory are ignored by
`.gitignore`, so a clone never contains personal scans or build output.

### Documentation

* [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) - the complete workflow, from
  creating a project to exporting the PDF.
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) - how the code is organised and
  how the runtime flow works.

## 9. Troubleshooting

* **"a dependency is missing"** - install the requirements with
  `py -3.14 -m pip install -r requirements.txt`.
* **Projects are not listed** - the registry lives in
  `%APPDATA%\CardCompile\app_data.db`; use *Projects > Show folder* to
  confirm that the project folder still exists. If you updated from a build made
  before the rename (the application used to be called *Image to PDF Builder*),
  the old `Documents\Image to PDF Builder` and `%APPDATA%\ImageToPDFBuilder`
  folders are migrated automatically on the first start.
* **A source image is missing** - the organizer marks the card in red and the
  canvas shows "Missing source image"; use *Replace image...* or
  *Locate missing file...*. A replacement is validated (and its checksum
  compared) before it is accepted.
* **Logs** - `logs\application.log` (rotating, no document content is logged).
* **The built `.exe` disappears or is blocked** - the PyInstaller executable is
  not code-signed, so antivirus products (ESET, Defender, ...) sometimes
  quarantine a freshly built `dist\CardCompile\CardCompile.exe`
  within minutes of the build. Check the antivirus quarantine/protection
  history, restore the file and add an exclusion for the project folder (or for
  `dist\`). The original sources are never affected.


## 10. Known limitations

* Perspective correction is manual: the four corners are dragged onto the card in
  the Perspective Correction, and the quad is then mapped back to a true rectangle.
  Automatic card detection is not implemented, because a wrong automatic crop
  would silently ruin the card while a manual corner takes seconds and is always
  undoable.
* Rotation angles that are not multiples of 90 degrees are produced by rotating
  the pixels before insertion, so the rotated bounding box gets a white
  background outside the image itself.
* PDF password protection uses AES-256 through PyMuPDF; the password is used for
  that export only and is never stored in the project.
* "Save as" copies the complete project folder (database and thumbnails); the
  original scans are referenced by path and are not duplicated.
* Folder-based PyInstaller builds are recommended; single-file builds work but
  start slower because Qt plugins are unpacked at runtime.