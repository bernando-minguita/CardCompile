"""Automatic grid layout engine.

Layout maths is done in millimetres against the physical page, never in screen
pixels.  The engine computes cell rectangles, then a fitted rectangle and an
optional crop for every document, and writes the resulting items through the
layout service so every object stays individually editable afterwards.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Sequence

from core.layout_service import fit_size, rotated_size
from core.logging_setup import get_logger
from core.page_overflow_engine import PageOverflowEngine, best_grid_for, grid_preset
from models.document import Document
from models.page_item import PageItem
from models.pdf_page import PdfPage

LOGGER = get_logger("autolayout")

#: Smallest cell the engine will place (mm); below this a cell is unusable.
MIN_CELL_MM = 5.0


class LayoutError(ValueError):
    """Raised when the requested grid cannot fit inside the printable area."""

PRESET_CUSTOM = "custom"
PRESET_LABELS = {
    "1": "One document per page",
    "2v": "Two documents vertically",
    "2h": "Two documents horizontally",
    "4": "Four documents (2 x 2)",
    "6": "Six documents (3 x 2)",
    "fit": "Fit selected documents on one page",
    PRESET_CUSTOM: "Custom rows / columns",
}

PRESET_GRIDS = {
    "1": (1, 1),
    "2v": (2, 1),
    "2h": (1, 2),
    "4": (2, 2),
    "6": (3, 2),
}


@dataclass(slots=True)
class LayoutOptions:
    rows: int = 2
    columns: int = 2
    h_spacing_mm: float = 4.0
    v_spacing_mm: float = 4.0
    fit_mode: str = "contain"
    align: str = "center"  # start | center | end (inside the cell)
    order: str = "row"  # row | column
    use_page_margins: bool = True
    margins: tuple[float, float, float, float] | None = None
    auto_overflow: bool = True
    preset: str = PRESET_CUSTOM

    def normalized(self) -> "LayoutOptions":
        rows, columns = grid_preset(self.rows, self.columns)
        self.rows, self.columns = rows, columns
        self.h_spacing_mm = max(0.0, min(50.0, float(self.h_spacing_mm)))
        self.v_spacing_mm = max(0.0, min(50.0, float(self.v_spacing_mm)))
        if self.fit_mode not in ("contain", "cover", "stretch"):
            self.fit_mode = "contain"
        if self.align not in ("start", "center", "end"):
            self.align = "center"
        if self.order not in ("row", "column"):
            self.order = "row"
        return self

    @property
    def capacity(self) -> int:
        return max(1, self.rows * self.columns)


@dataclass(slots=True)
class CellSpec:
    """Computed placement for one document inside a grid cell."""

    x: float
    y: float
    width: float
    height: float
    crop: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass(slots=True)
class LayoutResult:
    item_ids: list[str] = field(default_factory=list)
    page_ids: list[str] = field(default_factory=list)
    created_pages: int = 0
    unassigned_documents: list[str] = field(default_factory=list)

    @property
    def placed(self) -> int:
        return len(self.item_ids)


def margins_rect(
    page: PdfPage, options: LayoutOptions
) -> tuple[float, float, float, float]:
    """Printable area in mm, either the page margins or custom layout margins."""
    if options.use_page_margins or options.margins is None:
        return page.printable_rect()
    top, bottom, left, right = options.margins
    if left + right >= page.width_mm or top + bottom >= page.height_mm:
        raise LayoutError(
            f"The layout margins (left {left:.1f} mm, right {right:.1f} mm, top "
            f"{top:.1f} mm, bottom {bottom:.1f} mm) leave no printable area on a "
            f"{page.width_mm:.0f} x {page.height_mm:.0f} mm page."
        )
    return (
        left,
        top,
        page.width_mm - left - right,
        page.height_mm - top - bottom,
    )


def validate_layout(page: PdfPage, options: LayoutOptions) -> None:
    """Refuse a grid whose cells would fall outside the printable area.

    ``cell_rects`` used to clamp every cell to 5 mm and then step past the page
    edge, so documents were placed where neither the canvas page nor the PDF
    contains them - they only surfaced later as "image lies outside the page"
    validation warnings.
    """
    _ox, _oy, area_w, area_h = margins_rect(page, options)
    for axis, count, spacing, available in (
        ("width", options.columns, options.h_spacing_mm, area_w),
        ("height", options.rows, options.v_spacing_mm, area_h),
    ):
        needed = count * MIN_CELL_MM + (count - 1) * spacing
        if needed > available + 1e-6:
            what = "columns" if axis == "width" else "rows"
            raise LayoutError(
                f"A {options.rows} x {options.columns} grid with {spacing:.1f} mm "
                f"spacing does not fit: {count} {what} need {needed:.1f} mm of "
                f"{axis} but the printable area is {available:.1f} mm {axis}. "
                f"Reduce the {what}, the spacing or the margins."
            )


def cell_rects(
    page: PdfPage, options: LayoutOptions
) -> list[tuple[float, float, float, float]]:
    """Return every grid cell rectangle in reading order."""
    validate_layout(page, options)
    ox, oy, area_w, area_h = margins_rect(page, options)
    rows = options.rows
    columns = options.columns
    cell_w = (area_w - (columns - 1) * options.h_spacing_mm) / columns
    cell_h = (area_h - (rows - 1) * options.v_spacing_mm) / rows
    cell_w = max(5.0, cell_w)
    cell_h = max(5.0, cell_h)

    cells: list[tuple[float, float, float, float]] = []
    for index in range(rows * columns):
        if options.order == "column":
            column = index // rows
            row = index % rows
        else:
            row = index // columns
            column = index % columns
        x = ox + column * (cell_w + options.h_spacing_mm)
        y = oy + row * (cell_h + options.v_spacing_mm)
        cells.append((round(x, 3), round(y, 3), round(cell_w, 3), round(cell_h, 3)))
    return cells


def is_quarter_turn(rotation: float) -> bool:
    """True for 90/270 degree rotations (the visible box swaps its sides)."""
    return abs((abs(float(rotation)) % 180.0) - 90.0) < 0.01


def _quad_extent(
    quad: tuple[float, float, float, float, float, float, float, float],
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    """Pixel extent of the rectangle a quad warps to.

    Mirrors :func:`core.image_service.apply_perspective`, which sizes its output
    from the average of opposite edge lengths.
    """
    if image_width <= 0 or image_height <= 0:
        return (1.0, 1.0)
    x0, y0, x1, y1, x2, y2, x3, y3 = quad
    points = [
        (x0 * image_width, y0 * image_height),
        (x1 * image_width, y1 * image_height),
        (x2 * image_width, y2 * image_height),
        (x3 * image_width, y3 * image_height),
    ]
    top = math.dist(points[0], points[1])
    bottom = math.dist(points[3], points[2])
    left = math.dist(points[0], points[3])
    right = math.dist(points[1], points[2])
    return (max(1.0, (top + bottom) / 2.0), max(1.0, (left + right) / 2.0))


def crop_for_aspect(
    source_width: float, source_height: float, target_aspect: float
) -> tuple[float, float, float, float]:
    """Normalised (left, top, right, bottom) crop that matches *target_aspect*."""
    if source_width <= 0 or source_height <= 0 or target_aspect <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    source_aspect = source_width / source_height
    if abs(source_aspect - target_aspect) < 1e-6:
        return (0.0, 0.0, 0.0, 0.0)
    if source_aspect > target_aspect:
        # Too wide: trim left/right equally.
        visible = target_aspect / source_aspect
        trim = (1.0 - visible) / 2.0
        return (trim, 0.0, trim, 0.0)
    visible = source_aspect / target_aspect
    trim = (1.0 - visible) / 2.0
    return (0.0, trim, 0.0, trim)


def compute_cell(
    document: Document,
    cell: tuple[float, float, float, float],
    options: LayoutOptions,
    existing_crop: tuple[float, float, float, float] | None = None,
    quad: tuple[float, float, float, float, float, float, float, float] | None = None,
) -> CellSpec:
    """Place one document inside one cell according to the fit mode."""
    cell_x, cell_y, cell_w, cell_h = cell
    base_crop = existing_crop or (0.0, 0.0, 0.0, 0.0)
    src_w = max(1, document.original_width)
    src_h = max(1, document.original_height)
    # Account for an existing user crop when computing the visible aspect.
    visible_w = max(1.0, (1.0 - base_crop[0] - base_crop[2]) * src_w)
    visible_h = max(1.0, (1.0 - base_crop[1] - base_crop[3]) * src_h)
    # A perspective correction changes the shape the item renders to, so it
    # overrides the raw scan proportions.  Without this a straightened card was
    # fitted to its cell using the very distortion the correction removed.
    identity = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
    if quad is not None and any(abs(a - b) > 0.0005 for a, b in zip(quad, identity)):
        visible_w, visible_h = _quad_extent(quad, src_w, src_h)

    if options.fit_mode == "stretch":
        return CellSpec(
            x=round(cell_x, 3),
            y=round(cell_y, 3),
            width=round(cell_w, 3),
            height=round(cell_h, 3),
            crop=base_crop,
        )

    if options.fit_mode == "cover":
        extra = crop_for_aspect(visible_w, visible_h, cell_w / cell_h)
        span_x = 1.0 - base_crop[0] - base_crop[2]
        span_y = 1.0 - base_crop[1] - base_crop[3]
        left = base_crop[0] + extra[0] * span_x
        right = base_crop[2] + extra[2] * span_x
        top = base_crop[1] + extra[1] * span_y
        bottom = base_crop[3] + extra[3] * span_y
        return CellSpec(
            x=round(cell_x, 3),
            y=round(cell_y, 3),
            width=round(cell_w, 3),
            height=round(cell_h, 3),
            crop=(round(left, 6), round(top, 6), round(right, 6), round(bottom, 6)),
        )

    width, height = fit_size(visible_w, visible_h, cell_w, cell_h, "contain")
    if options.align == "start":
        x, y = cell_x, cell_y
    elif options.align == "end":
        x, y = cell_x + (cell_w - width), cell_y + (cell_h - height)
    else:
        x = cell_x + (cell_w - width) / 2.0
        y = cell_y + (cell_h - height) / 2.0
    return CellSpec(
        x=round(x, 3), y=round(y, 3), width=round(width, 3), height=round(height, 3)
    )


class AutoLayoutEngine:
    """Applies grid arrangements to a page (and the pages that follow)."""

    def __init__(self, layout_service) -> None:
        self._layout = layout_service
        self._overflow = PageOverflowEngine(layout_service)

    # ------------------------------------------------------------------ presets
    @staticmethod
    def options_for_preset(
        preset: str, base: LayoutOptions | None = None
    ) -> LayoutOptions:
        options = base or LayoutOptions()
        if preset in PRESET_GRIDS:
            options.rows, options.columns = PRESET_GRIDS[preset]
        options.preset = preset
        return options.normalized()

    @staticmethod
    def fit_all_options(
        document_count: int, page: PdfPage, base: LayoutOptions | None = None
    ) -> LayoutOptions:
        options = base or LayoutOptions()
        portrait = page.height_mm >= page.width_mm
        options.rows, options.columns = best_grid_for(document_count, portrait)
        options.preset = "fit"
        return options.normalized()

    # -------------------------------------------------------------------- plans
    def plan_page_items(
        self, documents: Sequence[Document], page: PdfPage, options: LayoutOptions
    ) -> list[tuple[Document, CellSpec]]:
        """Compute placements for up to ``capacity`` documents (no DB writes)."""
        cells = cell_rects(page, options)
        plan: list[tuple[Document, CellSpec]] = []
        for document, cell in zip(documents, cells):
            plan.append((document, compute_cell(document, cell, options)))
        return plan

    def arrange_items(
        self,
        items: Sequence[PageItem],
        page: PdfPage,
        options: LayoutOptions,
    ) -> list[PageItem]:
        """Re-fit *items* that already live on *page* into the grid.

        This is the "Arrange current page" operation: it only moves and resizes
        the page objects it is given.  Nothing is created or deleted, the image
        library is never touched, and the caller can turn the before/after
        snapshot into one undo step.
        """
        options = options.normalized()
        ordered = sorted(items, key=lambda item: (item.z_index, item.id))
        if not ordered:
            return []
        cells = cell_rects(page, options)
        documents = self._layout.documents()
        arranged: list[PageItem] = []
        for index, item in enumerate(ordered):
            # More objects than cells: the extras are left exactly where they
            # are.  Re-using the last cell stacked them all on top of each
            # other, which looked like missing images and made the top one
            # impossible to reach.
            if index >= len(cells):
                break
            cell = cells[index]
            document = documents.get(item.document_id)
            if document is None:
                cell_x, cell_y, cell_w, cell_h = cell
                item.position_x = round(cell_x, 3)
                item.position_y = round(cell_y, 3)
                item.width = round(cell_w, 3)
                item.height = round(cell_h, 3)
            else:
                crop = (
                    item.crop_left,
                    item.crop_top,
                    item.crop_right,
                    item.crop_bottom,
                )
                self._fit_item_into_cell(item, document, cell, options, crop)
            arranged.append(item)
        return arranged

    def _fit_item_into_cell(
        self,
        item: PageItem,
        document: Document,
        cell: tuple[float, float, float, float],
        options: LayoutOptions,
        crop: tuple[float, float, float, float],
    ) -> None:
        """Place *item* in *cell* using its **displayed** bounds.

        ``compute_cell`` works from the unrotated frame, so a 90 degree image was
        fitted as landscape inside a portrait cell and then rotated - its visible
        box was taller than the cell and ran past the page edge.  A quarter turn
        is fitted against a swapped cell, and any remaining rotation is handled by
        shrinking the frame until its rotated bounding box fits, so the visible
        image always stays inside its cell.
        """
        cell_x, cell_y, cell_w, cell_h = cell
        rotation = float(item.rotation)
        quarter = is_quarter_turn(rotation)
        fit_cell = (cell_x, cell_y, cell_h, cell_w) if quarter else cell
        spec = compute_cell(
            document, fit_cell, options, existing_crop=crop, quad=item.quad()
        )
        frame_w, frame_h = spec.width, spec.height

        # The frame is drawn rotated, so its visible box is its bounding box.
        display_w, display_h = rotated_size(frame_w, frame_h, rotation)
        if display_w > cell_w + 1e-6 or display_h > cell_h + 1e-6:
            scale = min(cell_w / display_w, cell_h / display_h)
            frame_w = max(1.0, frame_w * scale)
            frame_h = max(1.0, frame_h * scale)
            display_w, display_h = rotated_size(frame_w, frame_h, rotation)

        if options.align == "start":
            box_x, box_y = cell_x, cell_y
        elif options.align == "end":
            box_x = cell_x + (cell_w - display_w)
            box_y = cell_y + (cell_h - display_h)
        else:
            box_x = cell_x + (cell_w - display_w) / 2.0
            box_y = cell_y + (cell_h - display_h) / 2.0

        # Rotation is around the frame centre, so centre the frame on the box.
        item.position_x = round(box_x + (display_w - frame_w) / 2.0, 3)
        item.position_y = round(box_y + (display_h - frame_h) / 2.0, 3)
        item.width = round(frame_w, 3)
        item.height = round(frame_h, 3)
        if options.fit_mode == "cover":
            (
                item.crop_left,
                item.crop_top,
                item.crop_right,
                item.crop_bottom,
            ) = spec.crop
        item.aspect_locked = options.fit_mode != "stretch"
        item.fit_mode = options.fit_mode

    def apply(
        self,
        documents: Sequence[Document],
        start_page_id: str,
        options: LayoutOptions,
        replace_existing: bool = True,
    ) -> LayoutResult:
        """Lay documents out on *start_page_id* and any required follow-up page."""
        options = options.normalized()
        result = LayoutResult()
        documents = [
            doc for doc in documents if doc.original_width and doc.original_height
        ]
        start_page = self._layout.page(start_page_id)
        if not documents or start_page is None:
            return result
        # Validate before anything is written (overflow pages included).
        validate_layout(start_page, options)

        plan = self._overflow.plan(
            start_page_id=start_page_id,
            document_count=len(documents),
            capacity=options.capacity,
            auto_overflow=options.auto_overflow,
            document_ids=[doc.id for doc in documents],
        )
        result.created_pages = plan.created_pages
        result.page_ids = list(plan.page_ids)

        if replace_existing:
            for page_id in plan.page_ids:
                self._layout.clear_page(page_id)

        remaining = list(documents)
        for page_id in plan.page_ids:
            page = self._layout.page(page_id)
            if page is None:
                continue
            chunk = remaining[: options.capacity]
            remaining = remaining[options.capacity :]
            if not chunk:
                break
            items: list[PageItem] = []
            for index, (document, spec) in enumerate(
                self.plan_page_items(chunk, page, options)
            ):
                items.append(
                    PageItem(
                        id=uuid.uuid4().hex,
                        page_id=page.id,
                        document_id=document.id,
                        position_x=spec.x,
                        position_y=spec.y,
                        width=spec.width,
                        height=spec.height,
                        rotation=0.0,
                        crop_left=spec.crop[0],
                        crop_top=spec.crop[1],
                        crop_right=spec.crop[2],
                        crop_bottom=spec.crop[3],
                        aspect_locked=options.fit_mode != "stretch",
                        fit_mode=options.fit_mode,
                        z_index=index + 1,
                        is_locked=False,
                    )
                )
            self._layout.insert_items(items)
            result.item_ids.extend(item.id for item in items)

        if remaining:
            result.unassigned_documents = [doc.id for doc in remaining]
        LOGGER.info(
            "Applied layout '%s': %s items on %s page(s), %s unassigned",
            options.preset,
            result.placed,
            len(result.page_ids),
            len(result.unassigned_documents),
        )
        return result
