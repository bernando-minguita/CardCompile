"""Image import pipeline: scanning, validation, duplicate detection, hashing.

The heavy work (decode + hash + thumbnail) runs in a background thread; the
dialog then commits the accepted results to the database on the main thread.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageOps

from core.image_service import (
    ImageValidationError,
    compute_sha256,
    is_supported_extension,
    probe_image,
)
from core.logging_setup import get_logger
from models.document import Document

LOGGER = get_logger("import")

PREVIEW_MAX_SIDE = 160

STATUS_READY = "ready"
STATUS_DUPLICATE = "duplicate"
STATUS_INVALID = "invalid"
STATUS_IMPORTED = "imported"
STATUS_SKIPPED = "skipped"


@dataclass(slots=True)
class ImportPreview:
    """One analysed file shown in the import dialog."""

    path: str
    filename: str
    file_size: int = 0
    width: int = 0
    height: int = 0
    image_format: str = ""
    file_hash: str = ""
    dpi_x: float = 0.0
    dpi_y: float = 0.0
    status: str = STATUS_READY
    message: str = ""
    duplicate_of: list[str] = field(default_factory=list)
    thumbnail_png: bytes = b""
    category_id: str | None = None
    imported: bool = False

    @property
    def usable(self) -> bool:
        return self.status in (STATUS_READY, STATUS_DUPLICATE)

    @property
    def dimensions(self) -> str:
        return f"{self.width} x {self.height} px" if self.width else "-"

    def natural_size_mm(self, fallback_dpi: float = 300.0) -> tuple[float, float]:
        dpi = self.dpi_x if self.dpi_x > 1.0 else float(fallback_dpi or 300.0)
        if self.width <= 0 or self.height <= 0:
            return (0.0, 0.0)
        width_mm = self.width / dpi * 25.4
        return (round(width_mm, 2), round(width_mm * self.height / self.width, 2))

    def human_size(self) -> str:
        size = float(self.file_size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


@dataclass(slots=True)
class ImportSummary:
    previews: list[ImportPreview] = field(default_factory=list)
    imported_ids: list[str] = field(default_factory=list)
    duplicates: int = 0
    invalid: int = 0
    skipped: int = 0
    cancelled: bool = False

    @property
    def imported_count(self) -> int:
        return len(self.imported_ids)


def expand_paths(
    paths: Iterable[str | Path], extensions: Sequence[str], recursive: bool = True
) -> list[Path]:
    """Expand files and folders into a unique, sorted list of image files."""
    collected: dict[str, Path] = {}
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            for candidate in iterator:
                if candidate.is_file() and is_supported_extension(candidate, list(extensions)):
                    collected.setdefault(str(candidate.resolve()).lower(), candidate)
        elif path.is_file() and is_supported_extension(path, list(extensions)):
            collected.setdefault(str(path.resolve()).lower(), path)
    return sorted(collected.values(), key=lambda item: (str(item.parent).lower(), item.name.lower()))


def make_preview_png(path: Path, max_side: int = PREVIEW_MAX_SIDE) -> bytes:
    """Small PNG preview used in the import table (never touches the source)."""
    try:
        with Image.open(path) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # pragma: no cover - preview is optional
        return b""


def write_thumbnail(png_bytes: bytes, target: Path, max_side: int = 240) -> Path:
    """Store a normalised JPEG thumbnail for the organizer."""
    try:
        with Image.open(io.BytesIO(png_bytes)) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            jpeg_target = target.with_suffix(".jpg")
            image.save(jpeg_target, format="JPEG", quality=85, optimize=True)
            return jpeg_target
    except Exception:  # pragma: no cover - defensive
        LOGGER.warning("Thumbnail could not be written for %s", target.name)
        return target


def analyze_file(
    path: Path,
    existing_hashes: dict[str, list[str]],
    duplicate_policy: str = "ask",
) -> ImportPreview:
    """Validate one file, hash it and flag duplicates."""
    preview = ImportPreview(path=str(path), filename=path.name)
    try:
        info = probe_image(path)
    except ImageValidationError as exc:
        preview.status = STATUS_INVALID
        preview.message = str(exc)
        try:
            preview.file_size = path.stat().st_size
        except OSError:
            preview.file_size = 0
        return preview

    preview.width = info.width
    preview.height = info.height
    preview.image_format = info.image_format
    preview.file_size = info.file_size
    preview.file_hash = info.file_hash
    preview.dpi_x = info.dpi_x
    preview.dpi_y = info.dpi_y
    preview.thumbnail_png = make_preview_png(path)

    duplicates = existing_hashes.get(info.file_hash, [])
    if duplicates:
        preview.status = STATUS_DUPLICATE
        preview.duplicate_of = list(duplicates)
        preview.message = "Already imported as: " + ", ".join(duplicates[:3])
        if duplicate_policy == "skip":
            preview.message += " (will be skipped)"
    return preview


class ImportService:
    """Analyses and commits document imports for an open project."""

    def __init__(self, workspace) -> None:
        self._workspace = workspace

    # ------------------------------------------------------------------ analyse
    def existing_hashes(self) -> dict[str, list[str]]:
        return self._workspace.database.documents.existing_hashes(
            self._workspace.project.id
        )

    def analyze(
        self,
        paths: Sequence[str | Path],
        extensions: Sequence[str],
        duplicate_policy: str = "ask",
        progress: Callable[[int, int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        recursive: bool = True,
        existing_hashes: dict[str, list[str]] | None = None,
    ) -> list[ImportPreview]:
        """Analyse every candidate file. Safe to call from a worker thread.

        Pass ``existing_hashes`` when running off the UI thread so the database
        is never touched from a background thread.
        """
        files = expand_paths(paths, extensions, recursive=recursive)
        hashes = dict(existing_hashes) if existing_hashes is not None else self.existing_hashes()
        previews: list[ImportPreview] = []
        total = len(files)
        for index, file_path in enumerate(files, start=1):
            if is_cancelled is not None and is_cancelled():
                break
            if progress is not None:
                progress(index, total, file_path.name)
            preview = analyze_file(file_path, hashes, duplicate_policy)
            previews.append(preview)
            # Also flag duplicates inside the same batch.
            if preview.file_hash:
                hashes.setdefault(preview.file_hash, []).append(preview.filename)
        return previews

    # ------------------------------------------------------------------- commit
    def commit(
        self,
        previews: Sequence[ImportPreview],
        category_id: str | None,
        thumbnail_size: int = 240,
        include_duplicates: bool = True,
    ) -> ImportSummary:
        """Insert the accepted previews into the project database.

        The whole batch is committed in a single transaction: a failure part-way
        through used to leave some files imported, the rest missing and their
        thumbnails orphaned on disk.
        """
        summary = ImportSummary(previews=list(previews))
        workspace = self._workspace
        project = workspace.project
        repository = workspace.database.documents
        next_order = repository.next_sort_order(project.id)
        thumb_folder = project.thumbs_folder
        thumb_folder.mkdir(parents=True, exist_ok=True)

        # A document that was removed from the library while it was still placed
        # is kept as a hidden row.  Importing the same file again must restore
        # that row instead of creating a duplicate library entry - the canvas
        # items keep pointing at it.
        hidden_by_hash: dict[str, Document] = {}
        for existing in repository.list_for_project(project.id, in_library=False):
            hidden_by_hash.setdefault(existing.file_hash, existing)

        documents: list[Document] = []
        accepted: list[ImportPreview] = []
        restored: list[tuple[ImportPreview, Document]] = []
        written_thumbs: list[Path] = []
        for preview in previews:
            if preview.status == STATUS_INVALID:
                summary.invalid += 1
                continue
            if preview.status == STATUS_DUPLICATE and not include_duplicates:
                summary.duplicates += 1
                summary.skipped += 1
                preview.status = STATUS_SKIPPED
                continue

            hidden = (
                hidden_by_hash.pop(preview.file_hash, None)
                if preview.file_hash
                else None
            )
            if hidden is not None:
                if hidden.source_path != preview.path:
                    hidden.source_path = preview.path
                    hidden.filename = preview.filename
                    hidden.file_size = preview.file_size
                    hidden.original_width = preview.width
                    hidden.original_height = preview.height
                    hidden.dpi_x = preview.dpi_x
                    hidden.dpi_y = preview.dpi_y
                    hidden.image_format = preview.image_format
                    repository.update(hidden)
                restored.append((preview, hidden))
                continue

            document_id = str(uuid.uuid4())
            thumbnail_path = ""
            if preview.thumbnail_png:
                written = self._write_thumbnail(
                    preview.thumbnail_png, thumb_folder / f"{document_id}.png",
                    thumbnail_size,
                )
                if written.is_file():
                    thumbnail_path = str(written)
                    written_thumbs.append(written)

            documents.append(Document(
                id=document_id,
                project_id=project.id,
                filename=preview.filename,
                source_path=preview.path,
                file_hash=preview.file_hash,
                category_id=category_id,
                original_width=preview.width,
                original_height=preview.height,
                file_size=preview.file_size,
                imported_at=datetime.now().isoformat(timespec="seconds"),
                thumbnail_path=thumbnail_path,
                image_format=preview.image_format,
                sort_order=next_order,
                dpi_x=preview.dpi_x,
                dpi_y=preview.dpi_y,
            ))
            accepted.append(preview)
            next_order += 1

        try:
            repository.insert_many(documents)
        except BaseException:
            for path in written_thumbs:
                try:
                    path.unlink(missing_ok=True)
                except OSError:  # pragma: no cover - best effort
                    LOGGER.debug("Could not remove orphan thumbnail %s", path)
            raise

        if restored:
            repository.set_in_library(
                [document.id for _preview, document in restored], True
            )

        for preview, document in [*zip(accepted, documents), *restored]:
            preview.imported = True
            summary.imported_ids.append(document.id)
            if preview.duplicate_of:
                summary.duplicates += 1

        project.touch()
        workspace.database.projects.update(project)
        LOGGER.info(
            "Imported %s documents (%s duplicates, %s invalid)",
            summary.imported_count,
            summary.duplicates,
            summary.invalid,
        )
        return summary

    @staticmethod
    def _write_thumbnail(png_bytes: bytes, target: Path, max_side: int) -> Path:
        return write_thumbnail(png_bytes, target, max_side)
