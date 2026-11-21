-- CardCompile - project database schema (version 8)
-- All geometry columns are millimetres; crop values are normalised fractions.

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    project_folder      TEXT NOT NULL,
    default_page_size   TEXT NOT NULL DEFAULT 'A4',
    default_orientation TEXT NOT NULL DEFAULT 'portrait',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_categories_project ON categories(project_id, sort_order);

CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    source_path     TEXT NOT NULL,
    file_hash       TEXT NOT NULL,
    category_id     TEXT REFERENCES categories(id) ON DELETE SET NULL,
    original_width  INTEGER NOT NULL DEFAULT 0,
    original_height INTEGER NOT NULL DEFAULT 0,
    file_size       INTEGER NOT NULL DEFAULT 0,
    dpi_x           REAL NOT NULL DEFAULT 0.0,
    dpi_y           REAL NOT NULL DEFAULT 0.0,
    imported_at     TEXT NOT NULL,
    thumbnail_path  TEXT NOT NULL DEFAULT '',
    image_format    TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    -- 1 = shown in the Image Library.  A placed document is hidden instead of
    -- deleted so the canvas keeps its own image reference (and the PDF keeps
    -- exporting it); re-importing the same file makes it visible again.
    in_library      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(project_id, file_hash);
CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category_id);

CREATE TABLE IF NOT EXISTS pdf_pages (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    page_number      INTEGER NOT NULL,
    paper_size       TEXT NOT NULL DEFAULT 'A4',
    orientation      TEXT NOT NULL DEFAULT 'portrait',
    width_mm         REAL NOT NULL DEFAULT 210.0,
    height_mm        REAL NOT NULL DEFAULT 297.0,
    margin_top       REAL NOT NULL DEFAULT 10.0,
    margin_bottom    REAL NOT NULL DEFAULT 10.0,
    margin_left      REAL NOT NULL DEFAULT 10.0,
    margin_right     REAL NOT NULL DEFAULT 10.0,
    background_color TEXT NOT NULL DEFAULT '#FFFFFF'
);
CREATE INDEX IF NOT EXISTS idx_pages_project ON pdf_pages(project_id, page_number);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pages_project_number_unique
    ON pdf_pages(project_id, page_number);

CREATE TABLE IF NOT EXISTS page_labels (
    id          TEXT PRIMARY KEY,
    page_id     TEXT NOT NULL REFERENCES pdf_pages(id) ON DELETE CASCADE,
    text        TEXT NOT NULL DEFAULT 'Text',
    position_x  REAL NOT NULL DEFAULT 10.0,
    position_y  REAL NOT NULL DEFAULT 10.0,
    font_family TEXT NOT NULL DEFAULT 'Arial',
    font_size   REAL NOT NULL DEFAULT 12.0,
    bold        INTEGER NOT NULL DEFAULT 0,
    italic      INTEGER NOT NULL DEFAULT 0,
    color       TEXT NOT NULL DEFAULT '#000000',
    rotation    REAL NOT NULL DEFAULT 0.0,
    z_index     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_labels_page ON page_labels(page_id, z_index);

CREATE TABLE IF NOT EXISTS page_items (
    id            TEXT PRIMARY KEY,
    page_id       TEXT NOT NULL REFERENCES pdf_pages(id) ON DELETE CASCADE,
    document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position_x    REAL NOT NULL DEFAULT 0.0,
    position_y    REAL NOT NULL DEFAULT 0.0,
    width         REAL NOT NULL DEFAULT 50.0,
    height        REAL NOT NULL DEFAULT 50.0,
    rotation      REAL NOT NULL DEFAULT 0.0,
    crop_left     REAL NOT NULL DEFAULT 0.0,
    crop_top      REAL NOT NULL DEFAULT 0.0,
    crop_right    REAL NOT NULL DEFAULT 0.0,
    crop_bottom   REAL NOT NULL DEFAULT 0.0,
    aspect_locked INTEGER NOT NULL DEFAULT 1,
    fit_mode      TEXT NOT NULL DEFAULT 'contain',
    z_index       INTEGER NOT NULL DEFAULT 0,
    is_locked     INTEGER NOT NULL DEFAULT 0,
    -- Perspective correction: the four source corners as normalised
    -- fractions (0..1) of the image, in the order top-left, top-right,
    -- bottom-right, bottom-left.  The default is the identity quad (the whole
    -- image), so a project created before v7 needs no backfill beyond the
    -- column defaults.  A phone photo of a card that is skewed or slanted is
    -- mapped from this quad onto a true rectangle at render time.
    quad_tl_x     REAL NOT NULL DEFAULT 0.0,
    quad_tl_y     REAL NOT NULL DEFAULT 0.0,
    quad_tr_x     REAL NOT NULL DEFAULT 1.0,
    quad_tr_y     REAL NOT NULL DEFAULT 0.0,
    quad_br_x     REAL NOT NULL DEFAULT 1.0,
    quad_br_y     REAL NOT NULL DEFAULT 1.0,
    quad_bl_x     REAL NOT NULL DEFAULT 0.0,
    quad_bl_y     REAL NOT NULL DEFAULT 1.0,
    -- Non-destructive tone correction.  Stored as parameters, never as
    -- pixels: the original scan is untouched and every value is reversible.
    -- 1.0 is the neutral value for the four multipliers.
    adjust_brightness REAL NOT NULL DEFAULT 1.0,
    adjust_contrast   REAL NOT NULL DEFAULT 1.0,
    adjust_saturation REAL NOT NULL DEFAULT 1.0,
    adjust_sharpness  REAL NOT NULL DEFAULT 1.0,
    -- 0/1: flatten uneven lighting (a hand shadow across the card).
    adjust_shadow     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_page ON page_items(page_id, z_index);
CREATE INDEX IF NOT EXISTS idx_items_document ON page_items(document_id);
CREATE INDEX IF NOT EXISTS idx_items_document_page ON page_items(document_id, page_id);

CREATE TABLE IF NOT EXISTS export_history (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    output_path TEXT NOT NULL,
    page_count  INTEGER NOT NULL DEFAULT 0,
    quality     TEXT NOT NULL DEFAULT 'High',
    exported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_export_project ON export_history(project_id, exported_at);
