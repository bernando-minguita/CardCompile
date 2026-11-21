"""Image correction: perspective warp, tone adjustments and schema migration.

Covers the 1.5.0 additions:

* a skewed phone photo can be mapped back to a true rectangle;
* tone corrections are stored as parameters and never touch the source file;
* the parameters survive a save/reload round trip and are undoable;
* a project database written by an older version is upgraded in place instead
  of being refused (which used to lock the user out of every existing project).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from core.image_service import (  # noqa: E402
    apply_adjustments,
    apply_perspective,
    apply_sharpness,
    correct_image,
    flatten_lighting,
    perspective_coefficients,
)
from database.connection import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    MIN_PROJECT_SCHEMA_VERSION,
    PROJECT_MIGRATIONS,
    Database,
    DatabaseError,
)
from models.page_item import PageItem  # noqa: E402

IDENTITY = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
SKEWED = (0.06, 0.05, 0.94, 0.0, 1.0, 0.95, 0.0, 1.0)


def _open_project_db(path: Path) -> Database:
    """Open *path* exactly the way ``ProjectDatabase`` does."""
    db = Database(path)
    db.init_schema(migrations=PROJECT_MIGRATIONS, min_version=MIN_PROJECT_SCHEMA_VERSION)
    return db


def _sample(size=(400, 300), color=(200, 190, 180)) -> Image.Image:
    return Image.new("RGB", size, color)


# ------------------------------------------------------------------ geometry
def _corner_marked_image(quad, size=(400, 300)):
    """A grey photo of a white card whose corners are uniquely coloured.

    The colours make the warp *verifiable*: if the transform is right, the
    output corners must be red (TL), green (TR), blue (BR) and yellow (BL).
    Asserting only the output *size* let a completely broken transform pass,
    which is exactly how the normalised-coordinates bug shipped.
    """
    from PIL import ImageDraw

    width, height = size
    image = Image.new("RGB", size, (128, 128, 128))
    draw = ImageDraw.Draw(image)
    points = [(quad[i] * width, quad[i + 1] * height) for i in range(0, 8, 2)]
    draw.polygon(points, fill=(255, 255, 255))
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    for (x, y), colour in zip(points, colours):
        draw.ellipse([x - 16, y - 16, x + 16, y + 16], fill=colour)
    return image


def test_identity_quad_produces_no_change():
    image = _sample()
    result = apply_perspective(image, IDENTITY)
    assert result.size == image.size


def test_skewed_quad_is_warped_to_a_rectangle():
    image = _sample()
    result = apply_perspective(image, SKEWED)
    assert result.mode == "RGB"
    assert result.width > 0 and result.height > 0
    assert result.size != image.size


def test_warp_recovers_the_card_corners_in_the_right_order():
    """The four output corners must be the four marked source corners.

    This is the regression test for the transform that produced a black
    diagonal smear: the coefficients were solved in normalised 0..1 space, so
    every output pixel sampled a point far outside the image.  Checking the
    corner colours proves the mapping is in *pixel* space and correctly
    oriented.
    """
    quad = (0.10, 0.15, 0.90, 0.05, 0.95, 0.90, 0.05, 0.85)
    image = _corner_marked_image(quad)
    result = apply_perspective(image, quad)
    width, height = result.size

    def near(actual, expected, tolerance=60):
        return all(abs(a - b) <= tolerance for a, b in zip(actual, expected))

    assert near(result.getpixel((4, 4)), (255, 0, 0)), "top-left is not red"
    assert near(result.getpixel((width - 5, 4)), (0, 255, 0)), "top-right is not green"
    assert near(
        result.getpixel((width - 5, height - 5)), (0, 0, 255)
    ), "bottom-right is not blue"
    assert near(result.getpixel((4, height - 5)), (255, 255, 0)), "bottom-left is not yellow"


def test_warp_never_produces_an_all_black_result():
    """A black output means every sample fell outside the image."""
    quad = (0.10, 0.15, 0.90, 0.05, 0.95, 0.90, 0.05, 0.85)
    image = _corner_marked_image(quad)
    result = apply_perspective(image, quad)
    pixels = list(result.resize((40, 30)).getdata())
    dark = sum(1 for r, g, b in pixels if r < 12 and g < 12 and b < 12)
    assert dark < len(pixels) // 4, f"{dark}/{len(pixels)} output pixels are black"


def test_warp_keeps_the_card_interior_white():
    """The card body must survive the warp, not just its corners."""
    quad = (0.10, 0.15, 0.90, 0.05, 0.95, 0.90, 0.05, 0.85)
    image = _corner_marked_image(quad)
    result = apply_perspective(image, quad)
    width, height = result.size
    centre = result.getpixel((width // 2, height // 2))
    assert all(channel > 200 for channel in centre), f"centre is {centre}, not white"


def test_degenerate_quad_is_refused_instead_of_crashing():
    """A collapsed corner has no unique solution; the image must come back."""
    image = _sample()
    collapsed = (0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
    assert perspective_coefficients(collapsed, 100, 100) is None
    assert apply_perspective(image, collapsed).size == image.size


def test_coefficients_map_output_corners_onto_the_quad_in_pixels():
    """The solved coefficients must be expressed in pixel coordinates."""
    quad = (0.10, 0.15, 0.90, 0.05, 0.95, 0.90, 0.05, 0.85)
    source_w, source_h = 400, 300
    target_w, target_h = 341, 233
    coefficients = perspective_coefficients(
        quad, source_w, source_h, target_w, target_h
    )
    assert coefficients is not None
    a, b, c, d, e, f, g, h = coefficients

    def map_point(u, v):
        denominator = g * u + h * v + 1.0
        return (
            (a * u + b * v + c) / denominator,
            (d * u + e * v + f) / denominator,
        )

    expected = [
        (quad[0] * source_w, quad[1] * source_h),
        (quad[2] * source_w, quad[3] * source_h),
        (quad[4] * source_w, quad[5] * source_h),
        (quad[6] * source_w, quad[7] * source_h),
    ]
    corners = [
        (0.0, 0.0),
        (target_w - 1.0, 0.0),
        (target_w - 1.0, target_h - 1.0),
        (0.0, target_h - 1.0),
    ]
    for (u, v), (ex, ey) in zip(corners, expected):
        sx, sy = map_point(u, v)
        assert sx == pytest.approx(ex, abs=1.0), f"x off: {sx} vs {ex}"
        assert sy == pytest.approx(ey, abs=1.0), f"y off: {sy} vs {ey}"


def test_coefficients_default_the_target_to_the_source_size():
    """Without an explicit target the output matches the source resolution."""
    quad = (0.10, 0.15, 0.90, 0.05, 0.95, 0.90, 0.05, 0.85)
    default = perspective_coefficients(quad, 400, 300)
    explicit = perspective_coefficients(quad, 400, 300, 400, 300)
    assert default == explicit


def test_coefficients_refuse_a_zero_sized_source():
    quad = (0.10, 0.15, 0.90, 0.05, 0.95, 0.90, 0.05, 0.85)
    assert perspective_coefficients(quad, 0, 300) is None
    assert perspective_coefficients(quad, 400, 0) is None


def test_perspective_coefficients_are_eight_finite_numbers():
    coefficients = perspective_coefficients(SKEWED, 400, 300)
    assert coefficients is not None
    assert len(coefficients) == 8
    assert all(abs(value) < 1e9 for value in coefficients)


def test_quad_is_clamped_to_the_image():
    item = PageItem(id="i", page_id="p", document_id="d")
    item.set_quad((-0.5, 2.0, 1.5, -1.0, 1.0, 1.0, 0.0, 1.0))
    assert all(0.0 <= value <= 1.0 for value in item.quad())


# --------------------------------------------------------------------- tone
def test_neutral_adjustments_leave_the_image_untouched():
    image = _sample()
    result = apply_adjustments(image)
    assert result.size == image.size
    assert list(result.getpixel((10, 10))) == list(image.getpixel((10, 10)))


def test_brightness_changes_the_pixels():
    image = _sample(color=(100, 100, 100))
    brighter = apply_adjustments(image, brightness=1.6)
    assert brighter.getpixel((10, 10))[0] > image.getpixel((10, 10))[0]


def test_shadow_flattening_evens_out_a_gradient():
    """A dark-to-light gradient is a stand-in for a shadow across the card."""
    image = Image.new("RGB", (240, 120))
    for x in range(240):
        shade = 60 + int(150 * x / 239)
        for y in range(120):
            image.putpixel((x, y), (shade, shade, shade))
    result = flatten_lighting(image)
    left = result.getpixel((5, 60))[0]
    right = result.getpixel((234, 60))[0]
    # The gradient must be much flatter than the original 60 -> 210 ramp.
    assert abs(right - left) < abs(210 - 60) / 2


def test_sharpening_is_a_no_op_at_neutral():
    image = _sample()
    assert apply_sharpness(image, 1.0) is image


def test_correct_image_applies_perspective_before_tone():
    image = _sample()
    result = correct_image(image, quad=SKEWED, brightness=1.2, sharpness=1.4)
    assert result.mode == "RGB"
    assert result.size != image.size


def test_adjustments_are_clamped_to_a_usable_range():
    """A corrupt value must not blow up the render."""
    image = _sample()
    for hostile in (-100.0, 0.0, 1e9, float("nan")):
        result = apply_adjustments(image, brightness=hostile)
        assert result.size == image.size


# -------------------------------------------------------------------- model
def test_new_item_is_neutral():
    item = PageItem(id="i", page_id="p", document_id="d")
    assert not item.has_perspective()
    assert not item.has_adjustments()
    assert not item.has_correction()


def test_correction_key_changes_with_every_parameter():
    item = PageItem(id="i", page_id="p", document_id="d")
    base = item.correction_key()
    item.adjust_contrast = 1.3
    assert item.correction_key() != base
    item.reset_adjustments()
    item.set_quad(SKEWED)
    assert item.correction_key() != base


def test_reset_helpers_restore_neutral_state():
    item = PageItem(id="i", page_id="p", document_id="d")
    item.set_quad(SKEWED)
    item.adjust_brightness = 2.0
    item.adjust_shadow = True
    assert item.has_correction()
    item.reset_quad()
    item.reset_adjustments()
    assert not item.has_correction()


def test_row_round_trip_keeps_the_correction():
    item = PageItem(id="i", page_id="p", document_id="d")
    item.set_quad(SKEWED)
    item.adjust_brightness = 1.25
    item.adjust_shadow = True
    restored = PageItem.from_row(item.to_dict())
    assert restored.quad() == item.quad()
    assert restored.adjust_brightness == pytest.approx(1.25)
    assert restored.adjust_shadow is True


def test_from_row_tolerates_a_row_without_the_new_columns():
    """An old row (or a partial SELECT) must still build a usable item."""
    item = PageItem.from_row({"id": "i", "page_id": "p", "document_id": "d"})
    assert item.quad() == IDENTITY
    assert not item.has_correction()


def test_geometry_fields_cover_the_correction():
    """Undo restores whatever GEOMETRY_FIELDS snapshots."""
    from commands.base import GEOMETRY_FIELDS

    for field in (
        "quad_tl_x",
        "quad_br_y",
        "adjust_brightness",
        "adjust_contrast",
        "adjust_saturation",
        "adjust_sharpness",
        "adjust_shadow",
    ):
        assert field in GEOMETRY_FIELDS, f"{field} is missing from GEOMETRY_FIELDS"


# ------------------------------------------------------------------ schema
def test_fresh_database_has_the_correction_columns(tmp_path):
    db = _open_project_db(tmp_path / "fresh.db")
    try:
        columns = {row[1] for row in db.query_all("PRAGMA table_info(page_items)")}
        assert db.schema_version() == CURRENT_SCHEMA_VERSION
        for column in (
            "quad_tl_x",
            "quad_br_y",
            "adjust_brightness",
            "adjust_shadow",
        ):
            assert column in columns
    finally:
        db.close()


def _make_v6_database(path: Path) -> None:
    """Build a database that looks exactly like a 1.4.x project."""
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);
            INSERT INTO schema_version VALUES (6, datetime('now'));
            CREATE TABLE page_items (
                id TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                position_x REAL NOT NULL DEFAULT 0.0,
                position_y REAL NOT NULL DEFAULT 0.0,
                width REAL NOT NULL DEFAULT 50.0,
                height REAL NOT NULL DEFAULT 50.0,
                rotation REAL NOT NULL DEFAULT 0.0,
                crop_left REAL NOT NULL DEFAULT 0.0,
                crop_top REAL NOT NULL DEFAULT 0.0,
                crop_right REAL NOT NULL DEFAULT 0.0,
                crop_bottom REAL NOT NULL DEFAULT 0.0,
                aspect_locked INTEGER NOT NULL DEFAULT 1,
                fit_mode TEXT NOT NULL DEFAULT 'contain',
                z_index INTEGER NOT NULL DEFAULT 0,
                is_locked INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO page_items (id, page_id, document_id, width, height)
            VALUES ('existing', 'page', 'doc', 85.6, 54.0);
            """
        )
    finally:
        connection.close()


def test_old_project_is_upgraded_in_place(tmp_path):
    """A 1.4.x project must open, keep its rows and gain the new columns."""
    path = tmp_path / "old.db"
    _make_v6_database(path)

    db = _open_project_db(path)
    try:
        assert db.schema_version() == CURRENT_SCHEMA_VERSION
        row = db.query_one("SELECT * FROM page_items WHERE id = 'existing'")
        assert row is not None
        # The row survived and got the identity quad from the column defaults.
        item = PageItem.from_row(row)
        assert item.width == pytest.approx(85.6)
        assert item.quad() == IDENTITY
        assert not item.has_correction()
    finally:
        db.close()


def test_upgrade_is_idempotent(tmp_path):
    path = tmp_path / "old.db"
    _make_v6_database(path)
    for _ in range(2):
        db = _open_project_db(path)
        try:
            assert db.schema_version() == CURRENT_SCHEMA_VERSION
        finally:
            db.close()


def test_newer_database_is_still_refused(tmp_path):
    """A file from a future version cannot be understood, so it must not open."""
    path = tmp_path / "future.db"
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            "CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);"
            f"INSERT INTO schema_version VALUES ({CURRENT_SCHEMA_VERSION + 5}, datetime('now'));"
        )
    finally:
        connection.close()

    db = Database(path)
    try:
        with pytest.raises(DatabaseError):
            db.init_schema(
                migrations=PROJECT_MIGRATIONS,
                min_version=MIN_PROJECT_SCHEMA_VERSION,
            )
    finally:
        db.close()


def test_too_old_database_is_refused_untouched(tmp_path):
    """A schema older than the floor is missing changes this build cannot apply.

    Only the columns added after ``MIN_PROJECT_SCHEMA_VERSION`` are described,
    so "upgrading" an older file would leave it half-migrated.  It must be
    refused without being modified.
    """
    path = tmp_path / "ancient.db"
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            "CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);"
            f"INSERT INTO schema_version VALUES ({MIN_PROJECT_SCHEMA_VERSION - 1}, 'earlier');"
        )
    finally:
        connection.close()

    db = Database(path)
    try:
        with pytest.raises(DatabaseError):
            db.init_schema(
                migrations=PROJECT_MIGRATIONS,
                min_version=MIN_PROJECT_SCHEMA_VERSION,
            )
        # Nothing was written: the version is unchanged and no table was added.
        assert db.schema_version() == MIN_PROJECT_SCHEMA_VERSION - 1
        assert not db.table_exists("projects")
    finally:
        db.close()


def test_registry_database_is_not_measured_against_the_project_floor(tmp_path):
    """The app registry has its own version numbering and must keep opening.

    ``AppDatabase`` opens its registry with ``schema_version=1``.  A single
    global "minimum version" applied to every database refused that registry at
    startup ("Database schema version 1 is not supported; this application can
    upgrade version 6 and later"), which broke the application on launch.
    """
    from database.app_database import AppDatabase

    # A registry that already exists at version 1, exactly like a real install.
    path = tmp_path / "app_data.db"
    first = AppDatabase(path)
    first.close()
    assert path.is_file()

    # Reopening it must not raise, and the version must stay at 1.
    reopened = AppDatabase(path)
    try:
        assert reopened.db.schema_version() == 1
        assert reopened.db.table_exists("known_projects")
    finally:
        reopened.close()


def test_registry_and_project_versions_are_independent(tmp_path):
    """Both databases open side by side, each with its own version."""
    from database.app_database import AppDatabase

    registry = AppDatabase(tmp_path / "app_data.db")
    project = _open_project_db(tmp_path / "project.db")
    try:
        assert registry.db.schema_version() == 1
        assert project.schema_version() == CURRENT_SCHEMA_VERSION
    finally:
        project.close()
        registry.close()


# -------------------------------------------------------------- dialog helpers
def test_slider_mapping_round_trips():
    """Every multiplier survives a trip through the slider position."""
    from app.perspective_dialog import slider_to_value, value_to_slider

    for value in (0.2, 0.35, 0.5, 1.0, 1.5, 2.2, 3.0):
        position = value_to_slider(value)
        assert 0 <= position <= 200
        assert slider_to_value(position) == pytest.approx(value, abs=0.02)


def test_slider_mapping_is_neutral_at_the_centre():
    from app.perspective_dialog import (
        SLIDER_NEUTRAL,
        slider_to_value,
        value_to_slider,
    )

    assert slider_to_value(SLIDER_NEUTRAL) == pytest.approx(1.0)
    assert value_to_slider(1.0) == SLIDER_NEUTRAL


def test_slider_mapping_rejects_hostile_input():
    """A NaN or a string must fall back to neutral, not crash the dialog."""
    from app.perspective_dialog import SLIDER_NEUTRAL, value_to_slider

    assert value_to_slider(float("nan")) == SLIDER_NEUTRAL
    assert value_to_slider("not a number") == SLIDER_NEUTRAL
    assert value_to_slider(None) == SLIDER_NEUTRAL


def test_identity_quad_is_usable_and_collapsed_quad_is_not():
    """The dialog must refuse to preview a quad the warp would reject."""
    from app.perspective_dialog import IDENTITY_QUAD, quad_is_usable

    assert quad_is_usable(IDENTITY_QUAD, 400, 300)
    collapsed = (0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
    assert not quad_is_usable(collapsed, 400, 300)


def test_concave_quad_is_refused():
    """A corner dragged inside the shape is not a document outline."""
    from app.perspective_dialog import quad_is_usable

    concave = (0.0, 0.0, 1.0, 0.0, 0.5, 0.5, 0.0, 1.0)
    assert not quad_is_usable(concave, 400, 300)


def test_tiny_quad_is_refused():
    """A quad smaller than a few pixels cannot produce a usable card."""
    from app.perspective_dialog import quad_is_usable

    tiny = (0.50, 0.50, 0.51, 0.50, 0.51, 0.51, 0.50, 0.51)
    assert not quad_is_usable(tiny, 400, 300)


def test_quad_is_usable_rejects_bad_dimensions():
    from app.perspective_dialog import IDENTITY_QUAD, quad_is_usable

    assert not quad_is_usable(IDENTITY_QUAD, 0, 0)
    assert not quad_is_usable((0.0, 0.0), 400, 300)


# ------------------------------------------------------------------- worker
def test_render_correction_returns_a_warped_preview(tmp_path):
    """The worker's task function builds a corrected image from a path."""
    from app.workers import render_correction

    source = tmp_path / "card.png"
    _sample((600, 400)).save(source)
    result = render_correction(
        {
            "path": str(source),
            "quad": list(SKEWED),
            "max_side": 400,
            "brightness": 1.2,
            "contrast": 1.1,
            "saturation": 1.0,
            "sharpness": 1.0,
            "shadow": False,
        }
    )
    assert result["image"].width > 0
    assert result["image"].height > 0
    # The preview is capped at max_side, so a large scan never reaches the UI.
    assert max(result["size"]) <= 400


def test_render_correction_honours_neutral_parameters(tmp_path):
    """With no quad and neutral tone values the preview matches the source."""
    from app.workers import render_correction

    source = tmp_path / "plain.png"
    _sample((300, 200)).save(source)
    result = render_correction({"path": str(source), "max_side": 0})
    assert result["size"] == (300, 200)


def test_render_correction_reports_a_missing_file(tmp_path):
    """A deleted source must raise, so the dialog can show an error."""
    from app.workers import render_correction

    with pytest.raises(Exception):
        render_correction({"path": str(tmp_path / "gone.png"), "max_side": 200})

