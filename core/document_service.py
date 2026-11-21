"""Document organisation: search, filter, sort, removal and file reconnection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from core.image_service import ImageValidationError, compute_sha256, probe_image
from core.import_service import make_preview_png, write_thumbnail
from core.logging_setup import get_logger
from models.document import Document

LOGGER = get_logger("documents")

#: Longest side of a regenerated library thumbnail (same default as the importer).
THUMBNAIL_MAX_SIDE = 240

SORT_NAME = "name"
SORT_CATEGORY = "category"
SORT_IMPORTED = "imported"
SORT_SIZE = "size"
SORT_DATE = "date"


@dataclass(slots=True)
class ReconnectCandidate:
    document: Document
    candidate_path: str
    hash_matches: bool
    width: int = 0
    height: int = 0
    message: str = ""


class DocumentService:
    """Project level document operations used by the organizer panel."""

    def __init__(self, workspace) -> None:
        self._workspace = workspace

    # -------------------------------------------------------------------- reads
    @property
    def workspace(self):
        return self._workspace

    def documents(self) -> list[Document]:
        """Library entries only (placed images removed from it are hidden)."""
        return self._workspace.database.documents.list_for_project(
            self._workspace.project.id, in_library=True
        )

    def search(
        self,
        documents: Sequence[Document],
        query: str = "",
        category_id: str | None = None,
        sort_by: str = SORT_IMPORTED,
        descending: bool = False,
        only_missing: bool = False,
    ) -> list[Document]:
        """Filter and sort documents for the Image Library.

        The query matches the file name *and* the category, so typing "passport"
        finds the passport scans without opening the category filter first.
        """
        text = (query or "").strip().lower()
        result = []
        for document in documents:
            if text:
                haystack = " ".join(
                    (
                        document.filename,
                        document.category_name or "",
                        document.image_format or "",
                    )
                ).lower()
                if text not in haystack:
                    continue
            if category_id and document.category_id != category_id:
                continue
            if only_missing and document.exists:
                continue
            result.append(document)

        keys = {
            SORT_NAME: lambda d: (d.filename.lower(), d.imported_at),
            SORT_CATEGORY: lambda d: (d.category_name.lower(), d.filename.lower()),
            # sort_order is the manual drag order (it starts out as the import
            # order); using it as the primary key keeps drag-reorders sticky.
            SORT_IMPORTED: lambda d: (d.sort_order, d.imported_at),
            SORT_SIZE: lambda d: (d.file_size, d.filename.lower()),
            SORT_DATE: lambda d: (d.imported_at, d.sort_order),
        }
        result.sort(key=keys.get(sort_by, keys[SORT_IMPORTED]), reverse=descending)
        return result

    def placed_count(self) -> int:
        """Images used on a page, including ones hidden from the library."""
        return sum(
            1
            for document in self._workspace.database.documents.list_for_project(
                self._workspace.project.id
            )
            if document.page_numbers
        )

    def missing_documents(self) -> list[Document]:
        return [document for document in self.documents() if not document.exists]

    # ------------------------------------------------------------------ removal
    def remove_documents(self, document_ids: Iterable[str]) -> int:
        """Remove images from the Image Library. Original files are never deleted.

        A document that is still placed on a page is only *hidden*: its row is
        the canvas item's image reference, so deleting it would cascade to every
        placement (the image vanished from the canvas, the saved project and the
        PDF).  Unplaced documents are deleted as before, including their
        thumbnail.  Re-importing the same file makes a hidden document visible
        again.
        """
        ids = list(dict.fromkeys(document_ids))
        if not ids:
            return 0
        repository = self._workspace.database.documents
        known = {
            document.id: document
            for document in repository.list_for_project(self._workspace.project.id)
        }
        placed = [
            document_id
            for document_id in ids
            if document_id in known and known[document_id].page_numbers
        ]
        unplaced = [
            document_id
            for document_id in ids
            if document_id in known and not known[document_id].page_numbers
        ]
        repository.set_in_library(placed, False)
        thumbnails = repository.thumbnail_paths(unplaced)
        for document_id in unplaced:
            repository.delete(document_id)
        removed = len(placed) + len(unplaced)
        if unplaced:
            self._remove_thumbnails(thumbnails)
        if placed:
            LOGGER.info(
                "Hid %s placed document(s) from the library; their page "
                "placements stay",
                len(placed),
            )
        if removed:
            LOGGER.info("Removed %s document(s) from the project", removed)
        return removed

    def _remove_thumbnails(self, paths: Iterable[str]) -> None:
        """Delete cached thumbnails that no longer belong to a document."""
        roots = self._thumbnail_roots()
        for raw in paths:
            if not raw:
                continue
            # A duplicated library entry shares the *same* thumbnail file, so the
            # file is kept as long as another document still points at it.  The
            # exact stored string is compared - a resolved path may differ in case.
            if self._thumbnail_referenced(raw):
                LOGGER.debug("Keeping shared thumbnail %s", raw)
                continue
            try:
                path = Path(raw).resolve()
            except OSError:  # pragma: no cover - unreadable path
                continue
            # Only files this project owns may be removed - never an original.
            if not any(root in path.parents for root in roots):
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - locked file
                LOGGER.debug("Could not remove thumbnail %s", path)

    def _thumbnail_referenced(self, thumbnail_path: str) -> bool:
        """True when a document of this project still uses *thumbnail_path*."""
        if not thumbnail_path:
            return False
        return self._workspace.database.documents.thumbnail_in_use(
            thumbnail_path, self._workspace.project.id
        )

    def duplicate_document(self, document_id: str) -> Document | None:
        """Create a second project entry pointing at the same original file."""
        source = self._workspace.database.documents.get(document_id)
        if source is None:
            return None
        import uuid

        clone = Document(
            id=str(uuid.uuid4()),
            project_id=source.project_id,
            filename=source.filename,
            source_path=source.source_path,
            file_hash=source.file_hash,
            category_id=source.category_id,
            original_width=source.original_width,
            original_height=source.original_height,
            file_size=source.file_size,
            imported_at=source.imported_at,
            thumbnail_path=source.thumbnail_path,
            image_format=source.image_format,
            # Without the resolution the copy would fall back to 300 dpi and a
            # 600 dpi scan would be placed at half its real size.
            dpi_x=source.dpi_x,
            dpi_y=source.dpi_y,
            sort_order=self._workspace.database.documents.next_sort_order(
                source.project_id
            ),
        )
        return self._workspace.database.documents.insert(clone)

    def set_category(self, document_ids: Sequence[str], category_id: str | None) -> None:
        self._workspace.database.documents.set_categories(list(document_ids), category_id)

    def reorder(self, ordered_ids: Sequence[str]) -> None:
        self._workspace.database.documents.set_sort_orders(list(ordered_ids))

    # ------------------------------------------------------------------ missing
    def validate_replacement(
        self, document: Document, candidate_path: str | Path
    ) -> ReconnectCandidate:
        """Check a replacement file before the user is allowed to use it."""
        path = Path(candidate_path)
        candidate = ReconnectCandidate(
            document=document, candidate_path=str(path), hash_matches=False
        )
        try:
            info = probe_image(path)
        except ImageValidationError as exc:
            candidate.message = f"Not a usable image: {exc}"
            return candidate
        candidate.width = info.width
        candidate.height = info.height
        candidate.hash_matches = info.file_hash == document.file_hash
        if candidate.hash_matches:
            candidate.message = "This is the original file (checksum matches)."
        else:
            candidate.message = (
                "Different file: the checksum does not match the original. "
                f"Size {info.width} x {info.height} px."
            )
        return candidate

    def reconnect(self, candidate: ReconnectCandidate, update_hash: bool) -> bool:
        """Point the document at the replacement file.

        ``update_hash`` must be True when the user explicitly approved a file
        whose checksum differs from the original one.
        """
        document = candidate.document
        path = Path(candidate.candidate_path)
        if not path.is_file():
            return False
        try:
            info = probe_image(path)
        except ImageValidationError:
            return False

        document.source_path = str(path)
        document.filename = path.name
        document.original_width = info.width
        document.original_height = info.height
        document.file_size = info.file_size
        document.image_format = info.image_format
        # The replacement may have a different resolution - keep "original size"
        # placement correct by refreshing the stored DPI as well.
        document.dpi_x = info.dpi_x
        document.dpi_y = info.dpi_y
        if update_hash or not document.file_hash:
            document.file_hash = info.file_hash
        self._workspace.database.documents.update(document)
        # The organizer and the page strip prefer the cached thumbnail, so a stale
        # one would keep showing the *old* picture after reconnecting.
        self._refresh_thumbnail(document)
        LOGGER.info("Reconnected document %s to %s", document.id, path)
        return True

    def _refresh_thumbnail(self, document: Document) -> None:
        """Rebuild (or drop) the cached thumbnail after the source file changed."""
        folder = self._workspace.project.thumbs_folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover - read-only project folder
            LOGGER.debug("Thumbnail folder unavailable for %s", document.id)
            return
        png = make_preview_png(Path(document.source_path))
        previous = document.thumbnail_path
        if not png:
            # No preview could be produced: fall back to the reconnected file
            # rather than leaving the previous image in place.
            self._forget_thumbnail(document, previous)
            return
        written = write_thumbnail(
            png, folder / f"{document.id}.png", THUMBNAIL_MAX_SIDE
        )
        if not written.is_file():
            return
        document.thumbnail_path = str(written)
        self._workspace.database.documents.update(document)
        if previous and previous != document.thumbnail_path:
            self._drop_unused_thumbnail(previous)

    def _forget_thumbnail(self, document: Document, previous: str) -> None:
        if not previous:
            return
        document.thumbnail_path = ""
        self._workspace.database.documents.update(document)
        self._drop_unused_thumbnail(previous)

    def _thumbnail_roots(self) -> list[Path]:
        """Folders whose files this project owns (never an original scan)."""
        return [
            self._workspace.project.thumbs_folder.resolve(),
            self._workspace.project.documents_folder.resolve(),
        ]

    def _drop_unused_thumbnail(self, thumbnail_path: str) -> None:
        """Remove a replaced thumbnail unless another entry still shares it."""
        if self._thumbnail_referenced(thumbnail_path):
            return
        try:
            path = Path(thumbnail_path).resolve()
        except OSError:  # pragma: no cover - unreadable path
            return
        if not any(root in path.parents for root in self._thumbnail_roots()):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - locked file
            LOGGER.debug("Could not remove stale thumbnail %s", path)

    def scan_folder_for_missing(
        self, folder: str | Path, recursive: bool = True
    ) -> list[ReconnectCandidate]:
        """Look for files matching the missing documents in *folder*."""
        root = Path(folder)
        if not root.is_dir():
            return []
        missing = self.missing_documents()
        if not missing:
            return []
        by_name: dict[str, list[Path]] = {}
        iterator = root.rglob("*") if recursive else root.glob("*")
        for candidate in iterator:
            if candidate.is_file():
                by_name.setdefault(candidate.name.lower(), []).append(candidate)

        proposals: list[ReconnectCandidate] = []
        for document in missing:
            matches = [
                self.validate_replacement(document, match)
                for match in by_name.get(document.filename.lower(), [])
            ]
            # Verified originals first, so a caller that shows these as a choice
            # preselects the file whose checksum matches.  Every same-named file
            # stays in the list: the caller decides which one (if any) to use.
            matches.sort(
                key=lambda candidate: (
                    not candidate.hash_matches,
                    candidate.candidate_path.lower(),
                )
            )
            proposals.extend(matches)
        return proposals

    # ------------------------------------------------------------------- helpers
    def verify_hash(self, document: Document) -> bool | None:
        """Return True/False for the current file, or None when it is missing."""
        if not document.exists:
            return None
        try:
            return compute_sha256(document.source_path) == document.file_hash
        except ImageValidationError:
            return False

    def category_names(self) -> dict[str | None, str]:
        categories = self._workspace.database.categories.list_for_project(
            self._workspace.project.id
        )
        mapping: dict[str | None, str] = {category.id: category.name for category in categories}
        mapping[None] = "Uncategorised"
        return mapping

    def categories(self) -> list:
        """Return all categories for the current project, ordered by sort_order."""
        return self._workspace.database.categories.list_for_project(
            self._workspace.project.id
        )

    def document_count(self) -> int:
        """Return the total number of documents (including hidden ones)."""
        return self._workspace.database.documents.count(self._workspace.project.id)

    def page_count(self) -> int:
        """Return the total number of pages in the current project."""
        return self._workspace.database.pages.count(self._workspace.project.id)

    # ------------------------------------------------------------------ categories
    def create_category(self, name: str) -> object:
        """Create a new category for the current project."""
        from models.category import Category
        import uuid
        category = Category(
            id=str(uuid.uuid4()),
            project_id=self._workspace.project.id,
            name=name.strip(),
        )
        self._workspace.database.categories.insert(category)
        return category

    def get_category(self, category_id: str) -> object | None:
        """Return a category by id, or None if not found."""
        return self._workspace.database.categories.get(category_id)

    def rename_category(self, category_id: str, name: str) -> None:
        """Rename an existing category."""
        category = self.get_category(category_id)
        if category is not None:
            category.name = name.strip()
            self._workspace.database.categories.update(category)

    def delete_category(self, category_id: str) -> None:
        """Delete a category. Documents keep existing but become uncategorised."""
        self._workspace.database.categories.delete(category_id)
