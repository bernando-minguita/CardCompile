"""PDF generation with PyMuPDF - UI independent.

Coordinates: page geometry is stored in millimetres; PDF uses points
(1 mm = 72/25.4 pt).  Our scene has the origin at the *top left* with Y growing
downwards, while PDF has the origin at the *bottom left* with Y growing
upwards, so every item is converted with ``y_pdf = page_height - y - height``.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import pymupdf

from core.image_service import dpi_for_preset, render_pdf_image_bytes
from core.logging_setup import get_logger
from core.paths import MM_TO_PT, PT_TO_MM
from models.page_item import PageItem
from models.page_label import (
    LABEL_PADDING_MM,
    canonical_font_family,
    font_ascender,
    font_descender,
)
from models.pdf_page import PdfPage

LOGGER = get_logger("pdf")

QUALITY_PRESETS = ("Draft", "Standard", "High", "Original")


class PdfExportError(RuntimeError):
    """Raised when a PDF cannot be produced or validated."""


class PdfExportCancelled(PdfExportError):
    """Raised when the user cancels an export.

    Separate from a real failure so the interface can report a cancellation (and
    the files that were already written) instead of an error box.
    """


@dataclass(slots=True)
class ExportOptions:
    """User selected export settings."""

    quality: str = "High"
    compress: bool = True
    user_password: str = ""
    owner_password: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    separate_files: bool = False
    cancel_check: Callable[[], bool] | None = None
    progress: Callable[[int, int], None] | None = None


@dataclass(slots=True)
class ExportResult:
    output_paths: list[str] = field(default_factory=list)
    page_count: int = 0
    skipped_items: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def primary_path(self) -> str:
        return self.output_paths[0] if self.output_paths else ""


def mm_to_pt(value: float) -> float:
    return float(value) * MM_TO_PT


def item_pdf_rect(item: PageItem, page: PdfPage) -> pymupdf.Rect:
    """Convert a millimetre item rectangle into a PDF point rectangle.

    Millimetres and PyMuPDF points share the same origin (top-left, y growing
    downwards), so the rectangle maps 1:1. Do **not** flip y through
    ``page.height_mm``: that mirrors the page vertically.
    """
    x0 = mm_to_pt(item.position_x)
    y0 = mm_to_pt(item.position_y)
    x1 = mm_to_pt(item.position_x + item.width)
    y1 = mm_to_pt(item.position_y + item.height)
    return pymupdf.Rect(x0, y0, x1, y1)


def rotated_bounds(width: float, height: float, angle_deg: float) -> tuple[float, float]:
    """Bounding box of a rotated rectangle (mm)."""
    import math

    radians = math.radians(angle_deg)
    cos_a = abs(math.cos(radians))
    sin_a = abs(math.sin(radians))
    return (
        width * cos_a + height * sin_a,
        width * sin_a + height * cos_a,
    )


def create_temp_pdf_path(prefix: str = "dpb_export_") -> Path:
    """Reserve a writable temporary PDF path that can be deleted on Windows.

    ``tempfile.mkstemp`` returns an open handle; it has to be closed before the
    file can be replaced or removed, otherwise Windows reports WinError 32.
    """
    handle, name = tempfile.mkstemp(prefix=prefix, suffix=".pdf")
    os.close(handle)
    return Path(name)


class PdfService:
    """Renders project pages into a validated PDF document."""

    def __init__(self, layout_service) -> None:
        self._layout = layout_service

    # ---------------------------------------------------------------- helpers
    @property
    def workspace(self):
        return self._layout.workspace

    def _document_map(self) -> dict:
        return {doc.id: doc for doc in self._layout.db.documents.list_for_project(self.workspace.project.id)}

    def _item_stream(
        self,
        item: PageItem,
        document,
        dpi: int | None,
        quality: int = 92,
        background: str = "",
    ) -> tuple[bytes, float, float]:
        """Render one item to image bytes and its final millimetre size."""
        crop = item.crop_pixels(document.original_width, document.original_height)
        rotation = float(item.rotation) % 360.0
        bounds_w, bounds_h = rotated_bounds(item.width, item.height, rotation)

        if abs(rotation) < 0.01:
            data = render_pdf_image_bytes(
                document.source_path,
                crop,
                item.width,
                item.height,
                dpi,
                quality,
                correction=item.correction_key() if item.has_correction() else None,
            )
            return data, item.width, item.height

        from core.image_service import (
            apply_sharpness,
            correct_image,
            cropped_image,
            encode_jpeg,
            resize_for_dpi,
            rotate_image,
        )

        # The corners exposed by the rotation must show the page colour, like the
        # canvas does; a hard-coded white fill left pale triangles on coloured
        # pages.
        fill = tuple(
            int(round(channel * 255.0))
            for channel in (self._rgb(background) if background else (1.0, 1.0, 1.0))
        )
        image = cropped_image(document.source_path, crop)
        if item.has_correction():
            # Full resolution here: this is the exported PDF, so the warp and
            # the tone pass must run on every source pixel, not on a preview.
            # Sharpening is deferred until after the downscale below.
            image = correct_image(
                image,
                quad=item.quad() if item.has_perspective() else None,
                brightness=item.adjust_brightness,
                contrast=item.adjust_contrast,
                saturation=item.adjust_saturation,
                sharpness=item.adjust_sharpness,
                shadow=item.adjust_shadow,
                sharpen=False,
            )
        image = rotate_image(image, rotation, fill=fill)
        image = resize_for_dpi(image, bounds_w, bounds_h, dpi)
        if item.has_correction():
            # Sharpen last: sharpening before the resize wastes the effect and
            # can alias the downscaled result.
            image = apply_sharpness(image, item.adjust_sharpness)
        return encode_jpeg(image, quality), bounds_w, bounds_h

    def _insert_item(self, page_doc, page: PdfPage, item: PageItem, document, dpi, quality) -> None:
        try:
            data, width_mm, height_mm = self._item_stream(
                item, document, dpi, quality, page.background_color
            )
        except Exception as exc:
            raise PdfExportError(f"Cannot render '{document.filename}': {exc}") from exc

        center_x = item.position_x + item.width / 2.0
        center_y = item.position_y + item.height / 2.0
        left = center_x - width_mm / 2.0
        top = center_y - height_mm / 2.0
        # PyMuPDF uses the same origin as the canvas (top-left, y grows
        # downwards), so the millimetre rectangle maps 1:1. Flipping y here
        # mirrored every page vertically (the image placed at the top appeared
        # at the bottom of the exported PDF).
        rect = pymupdf.Rect(
            mm_to_pt(left),
            mm_to_pt(top),
            mm_to_pt(left + width_mm),
            mm_to_pt(top + height_mm),
        )
        page_doc.insert_image(
            rect,
            stream=data,
            keep_proportion=False,
            overlay=True,
            rotate=0,
        )

    #: Base-14 fonts understood by PyMuPDF (no font files to embed).  Keyed by
    #: the canonical family the canvas draws, so both sides use the same face.
    _FONTS = {
        ("Arial", False, False): "helv",
        ("Arial", True, False): "hebo",
        ("Arial", False, True): "heit",
        ("Arial", True, True): "hebi",
        ("Times New Roman", False, False): "tiro",
        ("Times New Roman", True, False): "tibo",
        ("Times New Roman", False, True): "tiit",
        ("Times New Roman", True, True): "tibi",
        ("Courier New", False, False): "cour",
        ("Courier New", True, False): "cobo",
        ("Courier New", False, True): "coit",
        ("Courier New", True, True): "cobi",
    }

    @classmethod
    def _font_name(cls, label) -> str:
        # Legacy base-14 names map onto the same Windows family the canvas uses.
        family = canonical_font_family(label.font_family)
        mapped = cls._FONTS.get((family, bool(label.bold), bool(label.italic)))
        if mapped:
            return mapped
        # Unknown family: fall back to Arial (Helvetica) with the requested style.
        return cls._FONTS[("Arial", bool(label.bold), bool(label.italic))]

    #: Scripts base-14 fonts cannot draw.  PyMuPDF's own text writer has no
    #: shaping, so Arabic came out as isolated dots; HTML layout shapes it.
    _SHAPED_RANGES = (
        (0x0590, 0x05FF),  # Hebrew
        (0x0600, 0x06FF),  # Arabic
        (0x0750, 0x077F),  # Arabic supplement
        (0x08A0, 0x08FF),  # Arabic extended-A
        (0xFB50, 0xFDFF),  # Arabic presentation forms-A
        (0xFE70, 0xFEFF),  # Arabic presentation forms-B
    )

    @classmethod
    def _needs_shaping(cls, text: str) -> bool:
        """True when the text needs complex-script shaping (Arabic/Hebrew)."""
        for char in text:
            code = ord(char)
            if any(start <= code <= end for start, end in cls._SHAPED_RANGES):
                return True
        return False

    def _shaped_label_source(self, label, lines):
        """Lay the label out with PyMuPDF's HTML engine (shapes Arabic).

        Returns ``(temp_document, glyph_rect, first_baseline, text_width_mm)`` or
        ``None`` when the HTML layout produced no text.
        """
        import html as html_module

        size_pt = max(2.0, float(label.font_size))
        rtl = any(
            ord(char) in range(0x0590, 0x0800) or ord(char) in range(0xFB50, 0xFF00)
            for char in (label.text or "")
        )
        body = "<br>".join(html_module.escape(line) for line in lines)
        css = (
            f"font-size:{size_pt}pt; font-family:'{canonical_font_family(label.font_family)}';"
            f" color:{label.color}; line-height:1.0; margin:0; padding:0;"
            f" direction:{'rtl' if rtl else 'ltr'}; text-align:{'right' if rtl else 'left'};"
        )
        document = pymupdf.open()
        page = document.new_page(width=1200, height=400)
        try:
            page.insert_htmlbox(pymupdf.Rect(0, 0, 1180, 380), f'<div style="{css}">{body}</div>')
            blocks = [
                block
                for block in page.get_text("dict")["blocks"]
                if block.get("type") == 0 and block.get("lines")
            ]
            if not blocks:
                document.close()
                return None
            rect = pymupdf.Rect()
            baseline = None
            for block in blocks:
                rect |= pymupdf.Rect(block["bbox"])
                if baseline is None:
                    spans = block["lines"][0].get("spans") or []
                    if spans:
                        baseline = float(spans[0]["origin"][1])
            if rect.is_empty or baseline is None:
                document.close()
                return None
            return document, rect, baseline, rect.width * PT_TO_MM
        except Exception:  # pragma: no cover - HTML engine problems
            document.close()
            return None

    def _insert_shaped_label(self, page_doc, page: PdfPage, label, lines) -> bool:
        """Draw a shaped (Arabic/Hebrew) label; ``False`` to use the text path."""
        source = self._shaped_label_source(label, lines)
        if source is None:
            return False
        temp_doc, glyphs, baseline, text_width_mm = source
        try:
            padding = LABEL_PADDING_MM
            size_mm = max(2.0, float(label.font_size)) * PT_TO_MM
            line_height_mm = (
                font_ascender(label.font_family) + font_descender(label.font_family)
            ) * size_mm
            box_w = text_width_mm + 2 * padding
            box_h = line_height_mm * len(lines) + 2 * padding
            center = pymupdf.Point(
                mm_to_pt(label.position_x + box_w / 2.0),
                mm_to_pt(label.position_y + box_h / 2.0),
            )
            rotation = float(label.rotation) % 360.0
            quarter = round(rotation / 90.0) * 90
            exact = abs(rotation - quarter) < 0.01

            if exact and abs(rotation) < 0.01:
                # Baseline-aligned like the standard path.
                baseline_mm = (
                    label.position_y
                    + padding
                    + font_ascender(label.font_family) * size_mm
                )
                left = mm_to_pt(label.position_x + padding)
                top = mm_to_pt(baseline_mm) - (baseline - glyphs.y0)
                rect = pymupdf.Rect(left, top, left + glyphs.width, top + glyphs.height)
                page_doc.show_pdf_page(rect, temp_doc, 0, clip=glyphs)
                return True

            if exact:
                w, h = glyphs.width, glyphs.height
                if int(quarter) % 180 == 90:
                    w, h = h, w
                rect = pymupdf.Rect(
                    center.x - w / 2.0,
                    center.y - h / 2.0,
                    center.x + w / 2.0,
                    center.y + h / 2.0,
                )
                page_doc.show_pdf_page(
                    rect, temp_doc, 0, clip=glyphs, rotate=int(quarter) % 360
                )
                return True

            # Arbitrary angle: rasterise the shaped text and rotate the image.
            from PIL import Image

            pixmap = temp_doc[0].get_pixmap(
                dpi=300, clip=glyphs, alpha=True
            )
            image = Image.frombytes(
                "RGBA", (pixmap.width, pixmap.height), pixmap.samples
            )
            rotated = image.rotate(
                -rotation, resample=Image.Resampling.BICUBIC, expand=True,
                fillcolor=(0, 0, 0, 0),
            )
            import io as _io

            buffer = _io.BytesIO()
            rotated.save(buffer, format="PNG")
            w_pt = rotated.width / 300.0 * 72.0
            h_pt = rotated.height / 300.0 * 72.0
            rect = pymupdf.Rect(
                center.x - w_pt / 2.0,
                center.y - h_pt / 2.0,
                center.x + w_pt / 2.0,
                center.y + h_pt / 2.0,
            )
            page_doc.insert_image(
                rect, stream=buffer.getvalue(), keep_proportion=False, overlay=True
            )
            return True
        finally:
            try:
                temp_doc.close()
            except Exception:  # pragma: no cover
                pass

    def _insert_label(self, page_doc, page: PdfPage, label) -> None:
        """Draw a text label as real PDF text, matching the canvas exactly.

        The canvas draws the text inside a box that starts at
        ``(position_x, position_y)`` plus ``LABEL_PADDING_MM`` and rotates that
        box around its centre.  The PDF therefore uses the same box: the text
        starts at the padded top-left corner and its baseline sits one ascender
        below it (Qt's ``AlignTop`` placement), and any rotation pivots on the
        centre of the box.  PyMuPDF's ``insert_text`` only supports
        0/90/180/270 deg, so arbitrary angles go through
        ``TextWriter.write_text`` with a ``morph`` tuple.
        """
        text = (label.text or "").strip()
        if not text:
            return
        # One baseline per line, like the canvas: PyMuPDF's own multi-line layout
        # spaces lines by its own metric, which drifted from the editor.
        lines = text.splitlines() or [text]
        if self._needs_shaping(text) and self._insert_shaped_label(
            page_doc, page, label, lines
        ):
            return
        size_pt = max(2.0, float(label.font_size))
        font_name = self._font_name(label)
        color = self._rgb(label.color)
        font_obj = pymupdf.Font(fontname=font_name)
        # Ascender/descender as fractions of the font size, shared with the canvas.
        # ``Font.ascender`` is PyMuPDF's *bounding box* ascent (1.075 for Helvetica)
        # while the canvas uses Qt's typographic ascent, which put exported text
        # ~0.8 mm lower than the editor showed it.
        ascender = font_ascender(label.font_family)
        descender = font_descender(label.font_family)
        line_height = (ascender + descender) * size_pt
        padding = mm_to_pt(LABEL_PADDING_MM)
        left = mm_to_pt(label.position_x)
        top = mm_to_pt(label.position_y)
        first_baseline = top + padding + ascender * size_pt
        rotation = float(label.rotation) % 360.0
        if abs(rotation) < 0.01:
            try:
                for index, line in enumerate(lines):
                    if not line:
                        continue
                    page_doc.insert_text(
                        pymupdf.Point(left + padding, first_baseline + index * line_height),
                        line,
                        fontname=font_name,
                        fontsize=size_pt,
                        color=color,
                        overlay=True,
                    )
            except Exception as exc:  # pragma: no cover - font problems
                LOGGER.warning("Could not draw label %s: %s", label.id, exc)
            return

        # Arbitrary rotation: use TextWriter with a morph around the centre of
        # the label box (the canvas rotates around that centre too, so pivoting
        # on the top-left corner displaced every rotated label).
        #
        # The morph must be the ``(pivot, matrix)`` tuple: hand-composing the
        # matrix from empty ``pymupdf.Matrix()`` objects silently produced the
        # all-zeros matrix (an empty matrix is not the identity in PyMuPDF), so
        # ``write_text`` applied no transform and every rotated label came out
        # horizontal.  A negative angle rotates clockwise, matching Qt's
        # ``QPainter.rotate`` on the canvas.
        text_width = max(
            float(font_obj.text_length(line, fontsize=size_pt)) for line in lines
        )
        pivot = pymupdf.Point(
            left + padding + text_width / 2.0,
            top + padding + line_height * len(lines) / 2.0,
        )
        writer = pymupdf.TextWriter(page_doc.rect, color=color)
        for index, line in enumerate(lines):
            if not line:
                continue
            writer.append(
                pymupdf.Point(left + padding, first_baseline + index * line_height),
                line,
                font=font_obj,
                fontsize=size_pt,
            )
        try:
            writer.write_text(
                page_doc,
                morph=(pivot, pymupdf.Matrix(-rotation)),
                overlay=True,
            )
        except Exception as exc:  # pragma: no cover - font problems
            LOGGER.warning("Could not draw rotated label %s: %s", label.id, exc)

    # ------------------------------------------------------------------- export
    def export_pages(
        self,
        page_ids: Sequence[str],
        output_path: str | Path,
        options: ExportOptions | None = None,
        category_id: str | None = None,
    ) -> ExportResult:
        """Render the given pages into a single PDF file."""
        options = options or ExportOptions()
        documents = self._document_map()
        pages = [self._layout.page(page_id) for page_id in page_ids]
        pages = [page for page in pages if page is not None]
        if not pages:
            raise PdfExportError("There are no pages to export.")

        result = ExportResult()
        dpi = dpi_for_preset(options.quality)
        quality = 92 if options.quality in ("High", "Original") else 85

        entries: list[tuple[PdfPage, list[PageItem]]] = []
        for page in pages:
            items = self._layout.items(page.id)
            if category_id:
                items = [
                    item
                    for item in items
                    if documents.get(item.document_id) is not None
                    and documents[item.document_id].category_id == category_id
                ]
                if not items:
                    continue
            entries.append((page, items))
        if not entries:
            raise PdfExportError(
                "No pages contain documents of the selected category."
            )

        document = pymupdf.open()
        try:
            for index, (page, page_items) in enumerate(entries):
                if options.cancel_check is not None and options.cancel_check():
                    raise PdfExportCancelled("Export cancelled.")
                if options.progress is not None:
                    options.progress(index, len(entries))
                pdf_page = document.new_page(
                    width=mm_to_pt(page.width_mm), height=mm_to_pt(page.height_mm)
                )
                if page.background_color.upper() not in ("#FFFFFF", "WHITE"):
                    pdf_page.draw_rect(
                        self._background_rect(page),
                        color=None,
                        fill=self._rgb(page.background_color),
                        overlay=False,
                    )
                for item in page_items:
                    doc = documents.get(item.document_id)
                    if doc is None or not doc.exists:
                        result.skipped_items.append(item.id)
                        LOGGER.warning("Skipping item %s - source image missing", item.id)
                        continue
                    self._insert_item(pdf_page, page, item, doc, dpi, quality)
                for label in self._layout.db.labels.list_for_page(page.id):
                    self._insert_label(pdf_page, page, label)
                result.page_count = index + 1

            self._apply_metadata(document, options)
            self._save_atomically(document, Path(output_path), options)
        finally:
            try:
                document.close()
            except Exception:  # pragma: no cover - already closed on success
                pass

        result.output_paths.append(str(output_path))
        LOGGER.info("Exported %s page(s) to %s", result.page_count, output_path)
        return result

    def export_project(
        self,
        output_path: str | Path,
        options: ExportOptions | None = None,
        category_id: str | None = None,
    ) -> ExportResult:
        page_ids = [page.id for page in self._layout.pages()]
        return self.export_pages(page_ids, output_path, options, category_id)

    def export_pages_separately(
        self,
        page_ids: Sequence[str],
        output_folder: str | Path,
        base_name: str,
        options: ExportOptions | None = None,
    ) -> ExportResult:
        """Export each page to its own PDF file inside *output_folder*."""
        options = options or ExportOptions()
        folder = Path(output_folder)
        folder.mkdir(parents=True, exist_ok=True)
        combined = ExportResult()
        for index, page_id in enumerate(page_ids, start=1):
            target = folder / f"{base_name}_page_{index:03d}.pdf"
            try:
                single = self.export_pages([page_id], target, options)
            except PdfExportCancelled:
                # Keep the pages that were already written and report them as a
                # cancellation instead of losing the partial result to an error.
                combined.cancelled = True
                return combined
            combined.output_paths.extend(single.output_paths)
            combined.skipped_items.extend(single.skipped_items)
            combined.page_count += single.page_count
        return combined

    # ---------------------------------------------------------------- internals
    @staticmethod
    def _background_rect(page: PdfPage) -> pymupdf.Rect:
        return pymupdf.Rect(0, 0, mm_to_pt(page.width_mm), mm_to_pt(page.height_mm))

    @staticmethod
    def _rgb(color: str) -> tuple[float, float, float]:
        text = (color or "#FFFFFF").lstrip("#")
        if len(text) == 3:
            text = "".join(char * 2 for char in text)
        elif len(text) == 8:
            # #RRGGBBAA - the alpha channel is not used by the PDF writer.
            text = text[:6]
        elif len(text) == 4:
            text = "".join(char * 2 for char in text[:3])
        try:
            return (
                int(text[0:2], 16) / 255.0,
                int(text[2:4], 16) / 255.0,
                int(text[4:6], 16) / 255.0,
            )
        except (ValueError, IndexError):
            return 1.0, 1.0, 1.0

    def _apply_metadata(self, document, options: ExportOptions) -> None:
        from core.paths import APP_NAME, APP_VERSION

        project = self.workspace.project
        metadata = {
            "title": project.name,
            "author": APP_NAME,
            "creator": f"{APP_NAME} {APP_VERSION}",
            "producer": f"{APP_NAME} {APP_VERSION}",
            "subject": project.description or "Scanned documents",
            "keywords": "documents, scans, pdf builder",
        }
        metadata.update({key: str(value) for key, value in options.metadata.items() if value})
        try:
            document.set_metadata(metadata)
        except Exception:  # pragma: no cover - metadata is best effort
            LOGGER.debug("Could not set PDF metadata")

    def _save_atomically(self, document, target: Path, options: ExportOptions) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            prefix=".dpb_export_", suffix=".pdf", dir=str(target.parent)
        )
        os.close(handle)
        temp_path = Path(temp_name)
        try:
            save_kwargs: dict = {
                "garbage": 3 if options.compress else 1,
                "deflate": bool(options.compress),
                "clean": bool(options.compress),
            }
            if options.user_password or options.owner_password:
                save_kwargs.update(
                    {
                        "encryption": pymupdf.PDF_ENCRYPT_AES_256,
                        "owner_pw": options.owner_password or options.user_password,
                        "user_pw": options.user_password,
                        "permissions": pymupdf.PDF_PERM_PRINT
                        | pymupdf.PDF_PERM_COPY,
                    }
                )
            document.save(str(temp_path), **save_kwargs)
            document.close()
            os.replace(temp_path, target)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------ export history
    def record_export(self, output_path: str, page_count: int, quality: str) -> None:
        try:
            self._layout.db.exports.insert(
                self.workspace.project.id, output_path, page_count, quality
            )
        except Exception:  # pragma: no cover - history is not critical
            LOGGER.warning("Could not record export history for %s", output_path)
