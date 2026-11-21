"""Validation of projects and exported PDF files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from core.logging_setup import get_logger
from core.pdf_service import mm_to_pt

LOGGER = get_logger("validation")

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(slots=True)
class ValidationIssue:
    severity: str
    message: str
    context: str = ""


@dataclass(slots=True)
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: str, message: str, context: str = "") -> None:
        self.issues.append(ValidationIssue(severity, message, context))

    def summary(self) -> str:
        if not self.issues:
            return "No problems found."
        return "\n".join(f"{i.severity.upper()}: {i.message}" for i in self.issues)


class ValidationService:
    """Checks the project before export and the produced PDF afterwards."""

    def validate_project(self, layout_service) -> ValidationReport:
        report = ValidationReport()
        pages = layout_service.pages()
        if not pages:
            report.add(SEVERITY_ERROR, "The project has no pages.")
            return report

        documents = layout_service.documents()
        placed_ids = layout_service.placed_document_ids()
        # Only images that are actually placed can break the PDF.  An unused
        # library image with a missing file must not block the export.
        for document_id in placed_ids:
            document = documents.get(document_id)
            if document is None:
                report.add(
                    SEVERITY_ERROR,
                    "A page references an image that is no longer in the Image "
                    "Library.",
                    document_id,
                )
                continue
            if not document.exists:
                report.add(
                    SEVERITY_ERROR,
                    f"Original image is missing: {document.filename} "
                    f"(expected at {document.source_path})",
                    document.source_path,
                )

        unused = len(documents) - len(placed_ids)
        if unused > 0:
            report.add(
                SEVERITY_WARNING,
                f"{unused} imported image(s) are not placed on any page and will "
                "not be included in the PDF.",
            )

        total_items = 0
        for page in pages:
            items = layout_service.items(page.id)
            total_items += len(items)
            for item in items:
                if item.document_id not in documents:
                    report.add(
                        SEVERITY_ERROR,
                        f"Page {page.page_number} references a removed image.",
                        item.id,
                    )
                    continue
                outside = (
                    item.position_x + item.width < 1.0
                    or item.position_y + item.height < 1.0
                    or item.position_x > page.width_mm - 1.0
                    or item.position_y > page.height_mm - 1.0
                )
                if outside:
                    report.add(
                        SEVERITY_WARNING,
                        f"An image on page {page.page_number} lies outside the page.",
                        item.id,
                    )
                if item.width < 5.0 or item.height < 5.0:
                    report.add(
                        SEVERITY_WARNING,
                        f"An image on page {page.page_number} is very small.",
                        item.id,
                    )

        if total_items == 0:
            report.add(SEVERITY_WARNING, "No images have been placed on any page.")
        return report

    # ---------------------------------------------------------------------- pdf
    def validate_pdf(
        self,
        path: str | Path,
        expected_pages: int | None = None,
        expected_sizes_mm: list[tuple[float, float]] | None = None,
        require_images: bool = True,
    ) -> ValidationReport:
        report = ValidationReport()
        pdf_path = Path(path)
        if not pdf_path.is_file():
            report.add(SEVERITY_ERROR, "The PDF file was not created.")
            return report
        if pdf_path.stat().st_size == 0:
            report.add(SEVERITY_ERROR, "The PDF file is empty.")
            return report

        try:
            document = pymupdf.open(pdf_path)
        except Exception as exc:
            report.add(SEVERITY_ERROR, f"The PDF cannot be opened: {exc}")
            return report

        try:
            if document.needs_pass:
                report.add(
                    SEVERITY_WARNING,
                    "The PDF is password protected (validation limited).",
                )
                return report
            if expected_pages is not None and document.page_count != expected_pages:
                report.add(
                    SEVERITY_ERROR,
                    f"Expected {expected_pages} pages but the PDF has "
                    f"{document.page_count}.",
                )
            for index in range(document.page_count):
                page = document[index]
                if expected_sizes_mm and index < len(expected_sizes_mm):
                    width_mm, height_mm = expected_sizes_mm[index]
                    if (
                        abs(page.rect.width - mm_to_pt(width_mm)) > 1.0
                        or abs(page.rect.height - mm_to_pt(height_mm)) > 1.0
                    ):
                        report.add(
                            SEVERITY_ERROR,
                            f"Page {index + 1} has the wrong size "
                            f"({page.rect.width:.1f} x {page.rect.height:.1f} pt).",
                        )
                if require_images and not page.get_images(full=True):
                    report.add(
                        SEVERITY_WARNING,
                        f"Page {index + 1} contains no images (blank page).",
                    )
                try:
                    page.get_pixmap(dpi=36)
                except Exception:
                    report.add(SEVERITY_ERROR, f"Page {index + 1} cannot be rendered.")
        finally:
            document.close()

        if report.ok:
            LOGGER.info("PDF validation passed for %s", pdf_path)
        else:
            LOGGER.warning("PDF validation found %s error(s)", len(report.errors))
        return report
