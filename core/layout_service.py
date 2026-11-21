"""Layout operations shared by the editor UI, the grid engine and the exporter.

All methods work in millimetres and persist immediately through the project
repositories.  Undo/redo is implemented by the command classes which snapshot
item dictionaries and call back into this service.
"""

from __future__ import annotations

import math
import uuid
from typing import Iterable, Sequence

from core.logging_setup import get_logger
from core.paper import page_dimensions
from core.project_service import build_page
from models.document import Document
from models.page_item import PageItem
from models.pdf_page import PdfPage

LOGGER = get_logger("layout")

#: ISO/IEC 7810 ID-1 long edge in millimetres - the physical width of a Saudi
#: Iqama, driving licence, Istimara or bank card.  A card cropped out of a scan
#: usually keeps the scanner's ``300 dpi`` tag even when the crop is only
#: ~100 dpi, which would print it at roughly a third of its real size; placing
#: an "original size" item smaller than this edge is snapped up to it.
ID1_LONG_EDGE_MM = 85.6

ALIGN_MODES = (
    "left",
    "center",
    "right",
    "top",
    "middle",
    "bottom",
    "page_center_h",
    "page_center_v",
    "page_center",
)


def fit_size(
    source_width: float,
    source_height: float,
    box_width: float,
    box_height: float,
    mode: str = "contain",
) -> tuple[float, float]:
    """Return the item size (mm) that fits a source into a box.

    ``contain`` keeps the aspect ratio inside the box, ``cover`` keeps the
    aspect ratio but fills the box (the excess is cropped), ``stretch`` fills the
    box without keeping the aspect ratio.
    """
    if source_width <= 0 or source_height <= 0:
        return box_width, box_height
    if mode == "stretch":
        return max(1.0, box_width), max(1.0, box_height)
    source_aspect = source_width / source_height
    box_aspect = box_width / box_height
    if mode == "cover":
        if source_aspect > box_aspect:
            height = box_height
            width = height * source_aspect
        else:
            width = box_width
            height = width / source_aspect
        return max(1.0, width), max(1.0, height)
    # contain
    if source_aspect > box_aspect:
        width = box_width
        height = width / source_aspect
    else:
        height = box_height
        width = height * source_aspect
    return max(1.0, width), max(1.0, height)


def rotated_size(width: float, height: float, rotation: float) -> tuple[float, float]:
    """Axis-aligned size of a rectangle rotated *rotation* degrees around its centre."""
    if not rotation:
        return width, height
    radians = math.radians(rotation)
    cos_a = abs(math.cos(radians))
    sin_a = abs(math.sin(radians))
    return width * cos_a + height * sin_a, width * sin_a + height * cos_a


def local_to_page(item, x: float, y: float) -> tuple[float, float]:
    """Page coordinates of the item-local point ``(x, y)``.

    The canvas rotates an item around its centre, so a local point does not map
    to ``position + (x, y)`` once the item is rotated.  Cropping uses this to
    keep the visible content anchored where it was on the page.
    """
    center_x = item.position_x + item.width / 2.0
    center_y = item.position_y + item.height / 2.0
    offset_x = x - item.width / 2.0
    offset_y = y - item.height / 2.0
    if item.rotation:
        radians = math.radians(item.rotation)
        cos_a, sin_a = math.cos(radians), math.sin(radians)
        offset_x, offset_y = (
            offset_x * cos_a - offset_y * sin_a,
            offset_x * sin_a + offset_y * cos_a,
        )
    return (center_x + offset_x, center_y + offset_y)


def position_for_local_point(
    width: float,
    height: float,
    rotation: float,
    x: float,
    y: float,
    target_x: float,
    target_y: float,
) -> tuple[float, float]:
    """Top-left position that puts local ``(x, y)`` at page ``(target_x, target_y)``.

    Inverse of :func:`local_to_page` for an item with the given size and rotation.
    """
    offset_x = x - width / 2.0
    offset_y = y - height / 2.0
    if rotation:
        radians = math.radians(rotation)
        cos_a, sin_a = math.cos(radians), math.sin(radians)
        offset_x, offset_y = (
            offset_x * cos_a - offset_y * sin_a,
            offset_x * sin_a + offset_y * cos_a,
        )
    return (
        target_x - width / 2.0 - offset_x,
        target_y - height / 2.0 - offset_y,
    )


def visual_bounds(item) -> tuple[float, float, float, float]:
    """(left, top, width, height) of an item as it appears on the page.

    Rotation happens around the item centre, so a rotated card occupies a
    different box than its unrotated frame.  Group alignment and distribution
    must use these bounds - otherwise "align to top" lines up the invisible
    frames of rotated images and the visible edges stay staggered.
    """
    width, height = rotated_size(item.width, item.height, item.rotation)
    center_x = item.position_x + item.width / 2.0
    center_y = item.position_y + item.height / 2.0
    return (
        center_x - width / 2.0,
        center_y - height / 2.0,
        width,
        height,
    )


def quad_aspect(
    quad: tuple[float, float, float, float, float, float, float, float],
    image_width: int,
    image_height: int,
) -> float:
    """Aspect ratio (width / height) of the rectangle a quad warps to.

    Mirrors :func:`core.image_service.apply_perspective`, which sizes its output
    from the average of opposite edge lengths.  Without this the layout maths
    used the *uncorrected* proportions, so a card straightened from a skewed
    photo was placed with the very distortion the correction removed.
    """
    if image_width <= 0 or image_height <= 0:
        return 1.0
    x0, y0, x1, y1, x2, y2, x3, y3 = quad
    # Corners are fractions of the image, so scale to pixels before measuring.
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
    width = (top + bottom) / 2.0
    height = (left + right) / 2.0
    if width <= 0 or height <= 0:
        return 1.0
    return width / height


def document_aspect(
    document: Document,
    crop: tuple[float, float, float, float] | None = None,
    quad: tuple[float, float, float, float, float, float, float, float] | None = None,
) -> float:
    """Aspect ratio (width / height) of a document, crop and quad aware.

    ``quad`` wins when it is not the identity: it describes the shape the item
    actually renders to, which is what placement and snapping must use.
    """
    identity = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
    if quad is not None and any(abs(a - b) > 0.0005 for a, b in zip(quad, identity)):
        # The quad is expressed against the original image, so the crop does not
        # change the shape it describes.
        return quad_aspect(quad, document.original_width, document.original_height)
    width = max(1, document.original_width)
    height = max(1, document.original_height)
    if crop:
        left, top, right, bottom = crop
        width = max(1.0, (right - left) * width)
        height = max(1.0, (bottom - top) * height)
    return width / height


def natural_size_mm(document: Document, fallback_dpi: float = 300.0) -> tuple[float, float]:
    """Physical size of the scan in millimetres (uses the file's DPI)."""
    return document.natural_size_mm(fallback_dpi)


def fit_natural_size(
    natural_width: float,
    natural_height: float,
    box_width: float,
    box_height: float,
) -> tuple[float, float]:
    """Keep the natural size but shrink it when it does not fit the page area."""
    if natural_width <= 0 or natural_height <= 0:
        return (box_width, box_height)
    scale = 1.0
    if natural_width > box_width or natural_height > box_height:
        scale = min(box_width / natural_width, box_height / natural_height)
    return (natural_width * scale, natural_height * scale)


def original_size_for_crop(
    document: Document,
    crop_box: tuple[float, float, float, float] | None = None,
    fallback_dpi: float = 300.0,
    quad: tuple[float, float, float, float, float, float, float, float] | None = None,
) -> tuple[float, float]:
    """Physical size of the visible part of a document (crop and quad aware).

    With a perspective correction the printed shape is the *warped* rectangle,
    not the raw scan, so the quad decides the proportions and the crop decides
    how many pixels survive.  Ignoring the quad here sized a straightened card
    with the original distortion.
    """
    dpi = document.effective_dpi(fallback_dpi)
    if document.original_width <= 0 or document.original_height <= 0:
        return (0.0, 0.0)
    left, top, right, bottom = crop_box or (0.0, 0.0, 1.0, 1.0)
    visible_w = max(1.0, (right - left) * document.original_width)
    visible_h = max(1.0, (bottom - top) * document.original_height)
    identity = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
    has_quad = quad is not None and any(
        abs(a - b) > 0.0005 for a, b in zip(quad, identity)
    )
    if has_quad:
        # The warped output keeps the quad's own edge lengths, so its pixel size
        # is the quad's extent - not the crop rectangle's.
        x0, y0, x1, y1, x2, y2, x3, y3 = quad
        points = [
            (x0 * document.original_width, y0 * document.original_height),
            (x1 * document.original_width, y1 * document.original_height),
            (x2 * document.original_width, y2 * document.original_height),
            (x3 * document.original_width, y3 * document.original_height),
        ]
        warped_w = (math.dist(points[0], points[1]) + math.dist(points[3], points[2])) / 2.0
        warped_h = (math.dist(points[0], points[3]) + math.dist(points[1], points[2])) / 2.0
        visible_w = max(1.0, warped_w)
        visible_h = max(1.0, warped_h)
    width_mm = visible_w / dpi * 25.4
    return (round(width_mm, 2), round(width_mm * visible_h / visible_w, 2))


def clamp_rect_to_page(
    x: float, y: float, width: float, height: float, page: PdfPage
) -> tuple[float, float]:
    """Keep an item inside the page bounds where possible."""
    x = max(-width + 5.0, min(x, page.width_mm - 5.0))
    y = max(-height + 5.0, min(y, page.height_mm - 5.0))
    return round(x, 3), round(y, 3)


class LayoutService:
    """Page and page-item operations for one open project."""

    def __init__(self, workspace) -> None:
        self._workspace = workspace

    # --------------------------------------------------------------- shortcuts
    @property
    def workspace(self):
        return self._workspace

    @property
    def project(self):
        return self._workspace.project

    @property
    def db(self):
        return self._workspace.database

    # -------------------------------------------------------------------- reads
    def pages(self) -> list[PdfPage]:
        return self.db.pages.list_for_project(self.project.id)

    def page(self, page_id: str) -> PdfPage | None:
        return self.db.pages.get(page_id)

    def items(self, page_id: str) -> list[PageItem]:
        return self.db.items.list_for_page(page_id)

    def all_items(self) -> list[PageItem]:
        return self.db.items.list_for_project(self.project.id)

    def placed_document_ids(self) -> set[str]:
        """Source images that are used on at least one page.

        This is the *only* definition of "used in the PDF": an imported image
        that has never been added to a page is not part of any layout or export.
        """
        return {item.document_id for item in self.all_items()}

    def library_counts(self) -> dict[str, int]:
        """Imported vs placed source images (Image Library and status bar)."""
        # Hidden documents (removed from the library while still placed) keep
        # their rows for the canvas, but they are not library entries.
        documents = self.documents()
        imported = sum(1 for doc in documents.values() if doc.in_library)
        # Placed images include documents removed from the library: the canvas
        # still shows them, so "0 imported | 3 placed" is the honest reading.
        placed = len(self.placed_document_ids())
        return {
            "imported": imported,
            "placed": placed,
            "unused": max(0, imported - placed),
        }

    def documents(self) -> dict[str, Document]:
        return {doc.id: doc for doc in self.db.documents.list_for_project(self.project.id)}

    def item_counts(self) -> dict[str, int]:
        return self.db.items.counts_by_page(self.project.id)

    # ------------------------------------------------------------- item creation
    def create_item(
        self,
        document: Document,
        page: PdfPage,
        *,
        fit_mode: str | None = None,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
        z_index: int | None = None,
        original_size: bool = False,
        fallback_dpi: float = 300.0,
        min_long_edge_mm: float = 0.0,
    ) -> PageItem:
        """Build (but do not save) a page item for *document* on *page*.

        With ``original_size=True`` the item keeps the physical size of the
        scan (pixels / DPI) instead of being stretched to the printable area -
        this is what pre-cropped card exports need.  The size is only reduced
        when the natural size would not fit on the page.

        ``min_long_edge_mm`` guards against scans whose resolution tag lies
        (a card cropped from a 100 dpi scan usually still carries ``300 dpi``,
        which would place a 85.6 mm card at 30 mm): when the natural size is
        smaller than that edge the item is scaled up to it, keeping the aspect
        ratio and never exceeding the printable area.
        """
        crop = (0.0, 0.0, 1.0, 1.0)
        aspect = document_aspect(document, crop)
        px, py, pw, ph = page.printable_rect()
        scaled_down = False
        enlarged = False
        if width is None or height is None:
            if original_size:
                natural_w, natural_h = natural_size_mm(document, fallback_dpi)
                width, height = fit_natural_size(natural_w, natural_h, pw, ph)
                scaled_down = (
                    natural_w > 0 and width < natural_w - 0.01
                )
                if (
                    not scaled_down
                    and min_long_edge_mm > 0
                    and max(width, height) + 0.01 < min_long_edge_mm
                ):
                    factor = min_long_edge_mm / max(width, height)
                    candidate_w = width * factor
                    candidate_h = height * factor
                    if candidate_w <= pw + 0.01 and candidate_h <= ph + 0.01:
                        width, height = candidate_w, candidate_h
                        enlarged = True
            else:
                target_w = pw
                target_h = target_w / aspect
                if target_h > ph:
                    target_h = ph
                    target_w = target_h * aspect
                width, height = target_w, target_h
        if x is None:
            x = px + (pw - width) / 2.0
        if y is None:
            y = py + (ph - height) / 2.0
        if z_index is None:
            z_index = self.db.items.max_z_index(page.id) + 1
        item = PageItem(
            id=str(uuid.uuid4()),
            page_id=page.id,
            document_id=document.id,
            position_x=round(x, 3),
            position_y=round(y, 3),
            width=round(width, 3),
            height=round(height, 3),
            rotation=0.0,
            aspect_locked=True,
            fit_mode=fit_mode or "contain",
            z_index=z_index,
            was_scaled_to_fit=scaled_down,
            was_enlarged=enlarged,
        )
        return item

    def insert_item(self, item: PageItem) -> PageItem:
        return self.db.items.insert(item)

    def insert_items(self, items: Sequence[PageItem]) -> list[PageItem]:
        for item in items:
            self.db.items.insert(item)
        return list(items)

    # ------------------------------------------------------------- item updates
    def save_item(self, item: PageItem) -> None:
        self.db.items.update(item)

    def save_items(self, items: Sequence[PageItem]) -> None:
        self.db.items.update_many(list(items))

    def delete_item(self, item_id: str) -> None:
        self.db.items.delete(item_id)

    def delete_items(self, item_ids: Iterable[str]) -> None:
        for item_id in item_ids:
            self.db.items.delete(item_id)

    def duplicate_item(self, item: PageItem, page_id: str | None = None) -> PageItem:
        """Create a copy of *item*, optionally on another page."""
        target_page_id = page_id or item.page_id
        page = self.db.pages.get(target_page_id)
        if page is None:
            raise LookupError(
                f"Cannot duplicate: page {target_page_id!r} no longer exists."
            )
        clone = item.clone(new_id=str(uuid.uuid4()), page_id=target_page_id)
        clone.z_index = self.db.items.max_z_index(target_page_id) + 1
        if page_id and page_id != item.page_id:
            # Fitting the copy onto the other page: clamp on both sides, an item
            # larger than the page would otherwise be placed at a negative
            # coordinate (partly outside the page, handles unreachable).
            clone.position_x = min(
                max(0.0, clone.position_x), max(0.0, page.width_mm - item.width)
            )
            clone.position_y = min(
                max(0.0, clone.position_y), max(0.0, page.height_mm - clone.height)
            )
        else:
            clone.position_x = round(clone.position_x + 6.0, 3)
            clone.position_y = round(clone.position_y + 6.0, 3)
        return self.db.items.insert(clone)

    def move_item_to_page(self, item: PageItem, page_id: str) -> PageItem:
        page = self.db.pages.get(page_id)
        if page is None:
            # The page can disappear (e.g. deleted while an operation is
            # pending); writing the item would violate the foreign key.
            LOGGER.warning("move_item_to_page: page %s not found - skipped", page_id)
            return item
        item.page_id = page_id
        item.z_index = self.db.items.max_z_index(page_id) + 1
        if page is not None:
            item.position_x = min(item.position_x, max(0.0, page.width_mm - item.width))
            item.position_y = min(item.position_y, max(0.0, page.height_mm - item.height))
        self.db.items.update(item)
        return item

    # ---------------------------------------------------------------- z ordering
    def _resequence_z(self, page_id: str) -> None:
        items = self.db.items.list_for_page(page_id)
        self.db.items.set_z_orders([item.id for item in items])

    def bring_to_front(self, item: PageItem) -> None:
        items = self.db.items.list_for_page(item.page_id)
        ordered = [i.id for i in items if i.id != item.id] + [item.id]
        self.db.items.set_z_orders(ordered)

    def send_to_back(self, item: PageItem) -> None:
        items = self.db.items.list_for_page(item.page_id)
        ordered = [item.id] + [i.id for i in items if i.id != item.id]
        self.db.items.set_z_orders(ordered)

    def bring_forward(self, item: PageItem) -> None:
        items = self.db.items.list_for_page(item.page_id)
        ids = [i.id for i in items]
        if item.id in ids:
            index = ids.index(item.id)
            if index < len(ids) - 1:
                ids[index], ids[index + 1] = ids[index + 1], ids[index]
                self.db.items.set_z_orders(ids)

    def send_backward(self, item: PageItem) -> None:
        items = self.db.items.list_for_page(item.page_id)
        ids = [i.id for i in items]
        if item.id in ids:
            index = ids.index(item.id)
            if index > 0:
                ids[index], ids[index - 1] = ids[index - 1], ids[index]
                self.db.items.set_z_orders(ids)

    # ------------------------------------------------------------------ align
    def align_items(
        self, items: Sequence[PageItem], mode: str, page: PdfPage
    ) -> list[PageItem]:
        """Align items to each other (2+) or to the page (1 or page modes).

        All edges are the *visible* edges: an item rotated by 90 degrees is
        aligned by the outline the user sees, not by its unrotated frame.
        """
        targets = list(items)
        if not targets:
            return []
        px, py, pw, ph = page.printable_rect()
        bounds = {item.id: visual_bounds(item) for item in targets}
        left = min(b[0] for b in bounds.values())
        right = max(b[0] + b[2] for b in bounds.values())
        top = min(b[1] for b in bounds.values())
        bottom = max(b[1] + b[3] for b in bounds.values())

        for item in targets:
            item_left, item_top, item_width, item_height = bounds[item.id]
            center_x = item_left + item_width / 2.0
            center_y = item_top + item_height / 2.0
            if mode == "left":
                center_x = left + item_width / 2.0
            elif mode == "right":
                center_x = right - item_width / 2.0
            elif mode == "center":
                center_x = left + (right - left) / 2.0
            elif mode == "top":
                center_y = top + item_height / 2.0
            elif mode == "bottom":
                center_y = bottom - item_height / 2.0
            elif mode == "middle":
                center_y = top + (bottom - top) / 2.0
            elif mode == "page_center_h":
                center_x = px + pw / 2.0
            elif mode == "page_center_v":
                center_y = py + ph / 2.0
            elif mode == "page_center":
                center_x = px + pw / 2.0
                center_y = py + ph / 2.0
            item.position_x = round(center_x - item.width / 2.0, 3)
            item.position_y = round(center_y - item.height / 2.0, 3)

        self.save_items(targets)
        return targets

    def distribute_items(
        self, items: Sequence[PageItem], axis: str = "horizontal"
    ) -> list[PageItem]:
        """Space items evenly between the first and last item on *axis*.

        Uses the visible bounds, so rotated items get equal *visual* gaps.
        """
        targets = list(items)
        if len(targets) < 3:
            return targets
        if axis == "vertical":
            targets.sort(key=lambda item: visual_bounds(item)[1])
            first = visual_bounds(targets[0])
            last = visual_bounds(targets[-1])
            start = first[1]
            end = last[1] + last[3]
            total = sum(visual_bounds(item)[3] for item in targets)
            gap = (end - start - total) / (len(targets) - 1)
            cursor = start
            for item in targets:
                _, item_top, _, item_height = visual_bounds(item)
                center_y = cursor + item_height / 2.0
                item.position_y = round(center_y - item.height / 2.0, 3)
                cursor += item_height + gap
        else:
            targets.sort(key=lambda item: visual_bounds(item)[0])
            first = visual_bounds(targets[0])
            last = visual_bounds(targets[-1])
            start = first[0]
            end = last[0] + last[2]
            total = sum(visual_bounds(item)[2] for item in targets)
            gap = (end - start - total) / (len(targets) - 1)
            cursor = start
            for item in targets:
                item_left, _, item_width, _ = visual_bounds(item)
                center_x = cursor + item_width / 2.0
                item.position_x = round(center_x - item.width / 2.0, 3)
                cursor += item_width + gap
        self.save_items(targets)
        return targets

    # ------------------------------------------------------------------- pages
    def clear_page(self, page_id: str) -> None:
        self.db.items.delete_for_page(page_id)

    def add_page(
        self,
        after_page_id: str | None = None,
        copy_from_page_id: str | None = None,
        paper_size: str | None = None,
        orientation: str | None = None,
    ) -> PdfPage:
        """Add a blank page, optionally cloned from an existing page."""
        pages = self.pages()
        # A free number: ``len(pages) + 1`` can collide with an existing page
        # (e.g. pages 1 and 3), which the unique index would reject.
        next_number = self.db.pages.next_page_number(self.project.id)
        source = (
            self.db.pages.get(copy_from_page_id)
            if copy_from_page_id
            else self.db.pages.get(after_page_id or "")
        )
        if source is not None:
            page = PdfPage(
                id=str(uuid.uuid4()),
                project_id=self.project.id,
                page_number=next_number,
                paper_size=paper_size or source.paper_size,
                orientation=orientation or source.orientation,
                width_mm=source.width_mm,
                height_mm=source.height_mm,
                margin_top=source.margin_top,
                margin_bottom=source.margin_bottom,
                margin_left=source.margin_left,
                margin_right=source.margin_right,
                background_color=source.background_color,
            )
            if paper_size or orientation:
                page.apply_paper(
                    paper_size or source.paper_size,
                    orientation or source.orientation,
                )
        else:
            page = build_page(
                self.project,
                paper_size or self.project.default_page_size,
                orientation or self.project.default_orientation,
                margin_mm=10.0,
                page_number=next_number,
            )
        self.db.pages.insert(page)

        ordered = [p.id for p in self.pages()]
        ordered.remove(page.id)
        insert_at = len(ordered)
        if after_page_id:
            candidates = [p.id for p in pages]
            if after_page_id in candidates:
                insert_at = candidates.index(after_page_id) + 1
        ordered.insert(min(insert_at, len(ordered)), page.id)
        self.db.pages.resequence(self.project.id, ordered)
        self.reload_page_numbers()
        return self.db.pages.get(page.id) or page

    def duplicate_page(self, page_id: str) -> PdfPage | None:
        source = self.db.pages.get(page_id)
        if source is None:
            return None
        new_page = self.add_page(after_page_id=page_id, copy_from_page_id=page_id)
        for item in self.db.items.list_for_page(page_id):
            clone = item.clone(new_id=str(uuid.uuid4()), page_id=new_page.id)
            self.db.items.insert(clone)
        self.db.labels.copy_page(page_id, new_page.id)
        return new_page

    def delete_page(self, page_id: str) -> bool:
        pages = self.pages()
        if len(pages) <= 1:
            return False
        self.db.items.delete_for_page(page_id)
        self.db.labels.delete_for_page(page_id)
        self.db.pages.delete(page_id)
        remaining = [p.id for p in self.pages()]
        self.db.pages.resequence(self.project.id, remaining)
        self.reload_page_numbers()
        return True

    def move_page(self, page_id: str, new_index: int) -> None:
        ordered = [p.id for p in self.pages()]
        if page_id not in ordered:
            return
        ordered.remove(page_id)
        new_index = max(0, min(new_index, len(ordered)))
        ordered.insert(new_index, page_id)
        self.db.pages.resequence(self.project.id, ordered)
        self.reload_page_numbers()

    def reload_page_numbers(self) -> None:
        """In-memory refresh helper (models are re-read from the database)."""
        # Pages are always re-read from the database, nothing cached here.

    def update_page(
        self,
        page: PdfPage,
        *,
        paper_size: str | None = None,
        orientation: str | None = None,
        margins: tuple[float, float, float, float] | None = None,
        background_color: str | None = None,
        custom_width_mm: float | None = None,
        custom_height_mm: float | None = None,
    ) -> PdfPage:
        if paper_size or orientation:
            page.apply_paper(
                paper_size or page.paper_size,
                orientation or page.orientation,
                custom_width_mm,
                custom_height_mm,
            )
        if margins is not None:
            page.margin_top, page.margin_bottom, page.margin_left, page.margin_right = margins
            page.clamp_margins()
        if background_color:
            page.background_color = background_color
        self.db.pages.update(page)
        return page
