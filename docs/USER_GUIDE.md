# CardCompile - user guide

**Compile scan cards into one PDF file.**

This guide follows one complete job from start to finish: create a project,
import your scans, arrange them on pages, and export the PDF.

---

## 1. Start the application

* Development: `py -3.14 main.py` or `run_dev.bat`
* Packaged: `dist\CardCompile\CardCompile.exe`

The dashboard opens first. It shows your recent projects, the last exported PDFs
and the application version. The window opens centred on the screen and keeps its
usual size; on a display that is smaller than the window it starts at the
top-left corner so the title bar and menus stay reachable. Maximise it for more
room, and it will stay wherever you move it.

The sidebar on the left switches between the three pages - **Dashboard**,
**Projects** and **Editor** - and holds the **Settings** entry. Actions such as
**New project** or **Open project** are not pages: they sit in the toolbar, in the
**File** menu and on the Dashboard/Projects pages.

## 2. Create a project

1. Click **New project** (or Ctrl+N).
2. Enter a project name (required). A folder name is proposed automatically -
   you can choose another empty folder instead.
3. Optional: description, page size (A4/A3/Letter/Legal/Custom), orientation and
   page margins.
4. Click **Create project**.

The project gets its own folder containing `project.db` (SQLite),
`thumbnails\` and an empty `documents\` folder. Existing folders are never
overwritten.

By default projects are stored in `Documents\CardCompile\Projects`, so
they survive updates and reinstalls of the application. Never keep projects
inside the application folder (`dist\CardCompile`): that folder is
replaced on every update or rebuild. The application warns you when the
configured folder is unsafe and offers to switch.

### Reopen, move or repair a project

* Open a project from the dashboard (double-click), the **Projects** list, or
  *File > Open project* (`project.db`).
* Moved a project folder somewhere else? Open the **Projects** page, select the
  entry (marked *missing* in red) and click **Locate...** - pick the `project.db`
  in its new location and the entry is re-linked.
* *Remove from list* deletes only the entry, never the files.
* A crashed session offers to reopen the project on the next start; layouts are
  written to the database continuously, so nothing is lost even without saving.


## 3. Import scanned images

1. Open the project (double-click a recent project on the dashboard).
2. In the editor press **Import...** in the Image Library panel (or File > Import
   documents, or drag files/folders onto the import dialog).
3. Choose **Add files...**, **Add folder...** or **Add several folders...**.
   **Add several folders...** opens a folder tree that starts at your home
   folder. Ctrl+click or Shift+click to highlight two or more folders at once
   (double-click opens a folder, and folders can be picked from any depth),
   then click **Add selected folders**. Everything highlighted is added to the
   list in one go. The button starts greyed out and turns on as soon as you
   pick a folder, so the starting folder itself is never added by accident.
4. The files are analysed in the background: size, dimensions, format,
   checksum, duplicate status. Invalid files are marked in red.
5. Choose the target **Category** and how duplicates should be handled
   (ask / skip / always import).
6. Click **Import** and read the result
   (*"50 images imported, 3 duplicates skipped, 2 unsupported files skipped"*).
   Files that are already in the project are reported before anything is written:
   answer the duplicate question to import them again or to skip them.

**Importing does not put anything on a PDF page.** The images go into the Image
Library and wait there until you select the ones you need. The page count, the
canvas and the PDF stay unchanged.

The original files stay exactly where they are - the application only stores
their path, checksum, size and a thumbnail.

## 4. The Image Library

The Image Library panel lists every imported source image of the project - never
page objects. Its header counts them:

`50 imported | 6 placed | 3 selected`

* **Imported** - all images in the library.
* **Placed** - distinct images used on at least one page (an image placed on
  three pages counts once).
* **Selected** - the images currently selected in the panel.

Each card shows the thumbnail, file name, category, pixel size, file size and a
placement badge: *not placed*, *page 3* or *4 pages (1, 3, 5, 7)*. Hover for the
full file name, resolution and path.

* **Search** matches the file name, the category and the file type; **filter** by
  category; **sort** by import order, file name (A-Z / Z-A), category, file size
  or import date.
* Select with click, Ctrl+click, Shift+click, **Select all shown** (right-click
  menu) or Ctrl+A; *Clear selection* is in the same menu.
* Buttons: **Import...**, **Add to page**, **New pages**, **Preview**,
  **Category**, **Remove**. Add/Preview/Category/Remove are disabled until
  something is selected and explain why in their tooltip.
* Right-click for: add to current page, add to new pages, open full size,
  duplicate image, replace image, locate missing file, remove from the library.
* Removing an image never deletes the original file. If the image is placed you
  are told exactly how many placements (and which pages) will be removed.
* A missing source file is shown in red; use *Replace image...* to point the
  project at the file again. The replacement is validated and its checksum is
  compared with the original before it is accepted.

**Library selection and canvas selection are separate.** Selecting cards never
selects objects on the page, and clicking an object on the page never changes the
library selection.

## 5. Arrange images on the page

### Adding images
Select the cards you need and click **Add to page** (only the selected images are
added; existing page objects stay where they are), or **New pages** to place them
on freshly created pages. You can also **drag cards from the Image Library
straight onto the page** - the image is placed where you drop it. Images never
land on top of each other: if the spot is taken, the nearest free slot is used.

When a project is opened the page is automatically fitted to the window (about
68 % for A4). Use Ctrl+0 to fit again after zooming, Ctrl+mouse wheel to zoom and
the middle mouse button (or space bar) to pan.

### Original size (pre-cropped cards)
Images are added at their **real physical size**, calculated from the resolution
stored in the file (pixels / DPI). This is what you want when a card has already
been cropped out of a scan and exported as its own image - it is not resized.

* The **1:1** button is the first control in the toolbar above the canvas. The
  same switch exists as the **Keep original size** checkbox under the Image
  Library actions and as *Arrange > Place new images at original size*. All three
  stay in sync and the choice is remembered between sessions.
* If a file has no DPI information, the DPI configured in *Settings > Layout >
  Assumed DPI when unknown* is used (default 300).
* An image larger than the printable area is only reduced as much as needed to
  fit; the status bar reports when this happens.
* To restore an image that is already on the page, right-click it and choose
  *Restore original size* from the canvas context menu. The crop is respected,
  so the visible part keeps its physical size.
* The import dialog lists the resulting physical size of every file before it is
  imported, and the organizer tooltip shows it too.

### Print a card at its real size
"Original size" can only be as accurate as the information inside the file. A
card cropped from a 100 dpi scan is often saved with a 300 dpi tag, so it would
print at about 30 x 19 mm instead of 85.6 x 54 mm.

**The application now handles this automatically.** When *place at original
size* would make an image smaller than an ID-1 card (85.6 mm long edge) it is
scaled up to that size, keeping its proportions, and the status bar says so -
for example *"4 image(s) were placed at card size (85.6 mm) because the file
resolution would have made them tiny"*. The result is a normal card on the
page instead of a 30 mm sliver.

* Switch it off in *Settings > Layout*: **Give cards their real size when the
  file DPI is wrong** (`snap_small_cards`). With it off, placement follows the
  file's DPI tag exactly.
* The automatic size is only a starting point - adjust any image afterwards:

* **Make it card size (85.6 x 54 mm)** - *Arrange* menu, the image context menu
  or **Ctrl+Shift+K**. One click sets the selected image(s) to the exact size of
  a Saudi Iqama, driving licence or Istimara (ISO/IEC 7810 ID-1 card).
* **Set physical size...** - same menus. Pick a preset (ID-1 card, passport data
  page 88 x 125 mm, passport photo 40 x 60 mm, A6) or type the width and height
  in millimetres. *Keep the image aspect ratio* is on by default so nothing is
  stretched.

Both keep the centre of the image, reduce the size if it would not fit on the
page, and can be undone with Ctrl+Z. The source file is never modified.

Need the files themselves to report the right size (for other programs)? Run
`py -3.14 tools\set_card_size.py <folder> --preset auto`. It writes
`*_print.jpg` copies with the corrected resolution tag, no resampling.

### Editing
| Action | How |
|---|---|
| Move | drag the image |
| Resize | drag one of the 8 square handles |
| Resize without aspect lock | uncheck "Keep aspect ratio" or hold Shift while dragging |
| Type an exact size | Properties panel: **Width** or **Height** - with "Keep aspect ratio" on, the other box follows instantly |
| Rotate | drag the round handle above the image (Shift = 15 degree steps) |
| Rotate numerically | Properties panel: type or step the **Rotation** angle - applied live |
| Add text | **Text** in the layout toolbar, *Arrange > Add text...* or Ctrl+T |
| Edit text | double-click the label (or right-click > Edit text) |
| Crop | select the image, right-click > **Crop...**, drag the edges, Enter/Esc |
| Numeric values | Properties panel: X, Y, width, height, rotation, fit mode |
| Move a group numerically | with 2+ images selected, **X** and **Y** show the top-left corner of the whole group: typing a value shifts every selected image by the same amount, so the spacing stays intact (one Ctrl+Z undoes the whole move) |
| Layer order | Properties panel buttons or right-click > Bring/Send |
| Lock | Properties panel checkbox or right-click > Lock |
| Delete | Del |
| Duplicate | Ctrl+D |
| Undo / Redo | Ctrl+Z / Ctrl+Y |

### Cropping
Cropping **trims the frame**: while you drag an edge or corner you see the
rectangle you are cutting to (the rest is dimmed); when you release the mouse the
frame shrinks to exactly that rectangle, the remaining picture keeps its
proportions and stays where it was on the page - nothing is stretched, and the
size you get is the cropped size (the dimmed part is not included). The status bar
shows the result (*"Cropped to 62.1 x 42.8 mm"*).

* Releasing the mouse (or **Enter**) applies the crop, **Esc** cancels it without
  changing anything, **Ctrl+Z** undoes it.
* *Reset crop* in the canvas context menu (right-click the image) removes the
  crop and grows the frame back around the whole image.
* After cropping, *Make it card size (85.6 x 54 mm)* or *Set physical size...*
  give the trimmed picture an exact printed size, and *Restore original size*
  uses the resolution of the visible part.
* Double-click an image to preview it: the preview shows the **cropped** part you
  see on the page (the title and info line say *"(cropped)"*). The organizer's
  document preview still shows the complete original scan.
| Open the original scan | double-click the image |

### Text labels

Use **Text** in the layout toolbar, *Arrange > Add text...* or **Ctrl+T** to put a
caption, note or heading directly on the page:

* Type the text, pick the font (Helvetica, Times-Roman, Courier), the size in
  points, bold/italic and the colour.
* Drag the label to move it, **double-click** it to change the text, **Del** to
  remove it, Ctrl+Z to undo.
* Labels are exported as real PDF text (selectable and searchable), not as an
  image, and are copied together with the page when you duplicate it.

Zoom with Ctrl+Mouse wheel, fit the page with Ctrl+0, pan with the middle mouse
button or the space bar.

### Group alignment and distribution
Select two or more images on the page (the last one you click keeps the handles,
all of them are aligned), then use **Align** in the toolbar or *Arrange*:

| Command | Effect |
|---|---|
| Align left / right edges | all selected images share the leftmost / rightmost visible edge |
| Align top / bottom edges | all share the topmost / bottommost visible edge |
| Align centres (vertical) | one common vertical centre line |
| Align middles (horizontal) | one common horizontal centre line |
| Distribute horizontally / vertically | three or more images, equal gaps |
| Centre horizontally / vertically / both on the page | uses the printable area |

The **Align** menu is disabled until the selection allows it - 2+ images for
edges, 3+ for distribution, 1+ for page centring - and the status bar confirms
the result, e.g. *"Aligned 3 image(s) by their top edges"*.

Alignment works on the **visible outline**, so images that were rotated (90°, or
any angle) and cropped line up exactly as you see them - not by their invisible
unrotated frames. The whole operation is one undo step (Ctrl+Z).

### Automatic layout
The **Arrange on Page** button above the canvas has two clearly separated scopes:

**Current page (placed images)** - re-arranges only the objects that are already
on the active page: two vertically, two horizontally, four (2x2), six (3x2),
**Fit all placed images**, **Arrange selected objects** (only the objects
selected on the canvas) and **Custom rows / columns...** (rows, columns, spacing,
fitting mode, alignment, order and overflow). Images that only exist in the
Image Library are never pulled in; pages other than the active one are never
touched.

**Selected library images** - adds only the images selected in the Image Library:
*Add to current page*, or *Add to new pages* with one, two (vertical or
horizontal), four or six images per page. New pages are created as needed and the
existing pages stay exactly as they were.

* **Contain** keeps the whole image visible (default).
* **Cover** fills the cell and crops the excess.
* **Stretch** distorts the image - the dialog warns you before applying it.

When more images are selected than the grid can hold, extra pages are created
automatically. If automatic overflow is disabled you are warned and the
remaining images are listed instead of being silently dropped.

Every image stays individually editable after an automatic layout, and the whole
operation is one undo step (Ctrl+Z).

## 6. Manage pages

The **Pages** strip lists every PDF page in its final order with a miniature of
its *actual* content: images appear with the rotation, crop and stacking they
have on the canvas, so the preview always matches the page.

* **Page 1 / A4 Portrait / 3 images** - the number counts the images placed on
  that page (hover for the paper size in millimetres).
* Click a page to switch to it; right-click a thumbnail for that page's
  actions (add, duplicate, clear, delete, move up/down).
* Duplicating a page copies its objects (and labels) with all placement
  properties; the same source image may be used on several pages.
* Clearing or deleting a page removes its objects only - the images stay in the
  Image Library.

The page navigator's right-click menu acts on the page you clicked:

* **Add page** adds a page after the current one.
* **Duplicate page** duplicates the current page including its images.
* **Clear page** empties the page but keeps it - the images stay in the Image
  Library.
* **Delete page** removes the current page (at least one page is always kept).
* **Move page up** / **Move page down** reorder pages.
* **Toggle orientation** switches the page between portrait and landscape.
* The **Page** tab in the properties panel changes paper size, orientation and
  margins.
* Right-click an image > **Move to page** to move it to another page or to a new
  page.

## 7. Export the PDF

1. Press Ctrl+E (or Export > Export PDF).
2. Choose the output folder and file name, the scope (whole project, current
   page, one PDF per page, or only the documents of one category), the quality
   (Draft 150 DPI, Standard 200 DPI, High 300 DPI, Original) and whether to
   compress.
3. Optional: password protection (AES-256). The password is used for this export
   only and is never stored.
4. Click **Export**. The PDF is written to a temporary file, atomically renamed,
   then reopened and validated: page count, page dimensions, blank pages,
   renderability and missing images. The page sizes are compared with the pages
   that were actually exported, so mixing A4 and A3 pages is fine.

* *One PDF per page* writes `<name>_page_001.pdf`, `<name>_page_002.pdf`, ... into
  the folder you picked and asks before overwriting existing files of that name.

After a successful export you can open the PDF with your default viewer.

## 8. Preview the PDF

**Preview PDF** renders the real PDF (not the editor canvas) into a temporary
file and shows it with page thumbnails, navigation and zoom. The temporary file
is deleted when the preview window is closed.

## 9. Saving and recovery

* The database is written continuously; **Ctrl+S** additionally flushes it to
  disk and updates the save indicator.
* A debounced auto-save runs after edits.
* If the application closes unexpectedly, the next start offers to reopen the
  project that was open.
* **Save as** (Ctrl+Shift+S) copies the whole project into a new folder with a
  new identity, so both projects remain independent.

## 10. Settings

* **General**: default project/export folders, recent projects count, auto-save
  interval, confirmation before deleting, reopen last project.
* **Import**: default category, thumbnail size, duplicate behaviour, supported
  formats.
* **PDF**: default page size, orientation, margins, quality, compression, output
  file name pattern (`{project}` and `{date}` placeholders).
* **Layout**: default fitting, snap to grid, grid spacing, alignment guides,
  automatic overflow, default rows/columns.
* **Categories**: add, rename and delete the document categories of the open
  project.

Settings are stored as JSON in `%APPDATA%\CardCompile\settings.json`. A
corrupted file is backed up and replaced with safe defaults.

## 11. If something goes wrong

* `logs\application.log` (next to the program) records every start, project
  open, layout run and export. It ends with `Exiting with code 0` on a normal
  shutdown - if that line is missing, the program did not close itself.
* `logs\crash.log` appears only after a hard crash inside the graphics library
  (the case where the window disappears without an error message). Send this
  file with the report; it contains the technical stack of the failure, never
  the content of your scans.
* Everything you placed is written to the project database immediately, so a
  crash never costs more than the last drag. After a crash the start-up asks
  whether the last project should be reopened.

### "The window vanished without any message"

That is a Windows-level failure, not a normal error: the program was killed
while the operating system was dispatching a click or menu selection
(`RPC_E_CANTCALLOUT_ININPUTSYNCCALL`, code `0x8001010d` in `crash.log`).

Since 1.4.2 the app queues its toolbar/menu actions to the next event-loop turn,
so no dialog, drag operation or scene rebuild runs while Windows is still
handling that click. If you still see it, please send `logs\crash.log` - the
line starting with `Windows fatal exception:` names the fault.

### "My projects disappeared after updating"

The application was renamed from **Image to PDF Builder** to **CardCompile**. On
the first start after the update the old folders are moved automatically:

* `Documents\Image to PDF Builder\` → `Documents\CardCompile\`
* `%APPDATA%\ImageToPDFBuilder\` → `%APPDATA%\CardCompile\`

Projects, exports, pasted images, settings and the project list all come across,
and the stored project paths are re-pointed at the new folder. Nothing is
deleted: if a file already exists under the new name it is kept and the old copy
is left in place.

If the move could not complete (a folder was locked by a backup tool or an
antivirus scan), close anything holding the folder and start the application
again - the migration simply runs again. You can also move the folder by hand;
the names above are the only thing that changed.

### "The project is empty" or "(empty - database copied without -wal?)"

A project folder contains `project.db` plus two sidecar files
(`project.db-wal`, `project.db-shm`) while the application runs. If you copy or
back up **only** `project.db` while the app was still running (or right after a
crash), the newest changes are missing and the database opens as an empty file.

Since 1.4.4 the database is written with `synchronous = FULL`, the Projects list
labels such a file honestly, and *Open* explains what to restore. To be safe,
back up the **whole project folder** (or close the project first - closing
flushes the sidecars).

### "New project" refuses a folder

A folder inside the application folder (`dist\CardCompile\...`) is deleted
by the next update or rebuild, so the dialog refuses it and offers the default
project folder. If you really want it, choose such a folder in
*Settings > General* and confirm the warning ("Use it anyway?"); that opt-in then
also applies to new projects. A folder typed by hand into `settings.json` is
ignored the same way as long as the opt-in is off.

### "This project is open in the editor" when deleting

A project that is currently open cannot be deleted from the Projects list: the
folder (database, `documents\`, `thumbnails\`) cannot be removed while the editor
keeps writing to it. Close the project first (*File > Close project*) and delete
it afterwards - nothing is deleted until you do.

### Auto-save keeps saying "Auto-save failed - retrying"

The project database could not be written (folder removed, drive unplugged, file
locked by a backup tool). The app retries every 30 seconds while there are
unsaved changes; **Ctrl+S** retries immediately and shows the exact error.
Nothing is lost while the window stays open, and closing asks before discarding.

### A reconnected image still looks wrong

Reconnecting refreshes the cached thumbnail of that entry. If the thumbnail file
itself cannot be rebuilt (unreadable replacement), the cache is cleared so the
organizer and the page strip use the real file. Use **Reload** on the organizer
(or reopen the project) if a card was drawn before the reconnect.

## 12. Privacy

All processing is local. There is no cloud upload, no third-party storage, no
external API, no OCR service and no telemetry. Logs contain technical
information only - never the content of scanned documents.

## 13. About and credits

**Help > About** shows the application version, what the application does, the
privacy note, the technology stack and the creator credits:

* Created by: **Bernando Jr Minguita**
* LinkedIn profile: <https://www.linkedin.com/in/bernando-jr-minguita> (a real
  link - click it to open the profile in your default browser)
