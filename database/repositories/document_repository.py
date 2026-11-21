"""Document row access."""

from __future__ import annotations

from database.repositories.base import BaseRepository
from models.document import Document


class DocumentRepository(BaseRepository):
    """CRUD operations for imported documents."""

    _SELECT = (
        "SELECT d.*, COALESCE(c.name, '') AS category_name "
        "FROM documents d LEFT JOIN categories c ON c.id = d.category_id "
    )

    def list_for_project(
        self, project_id: str, in_library: bool | None = None
    ) -> list[Document]:
        """Documents of a project.

        ``in_library=None`` returns every row (the canvas/PDF need the hidden
        ones too), ``True`` only the visible Image Library entries and ``False``
        only the hidden, still-placed documents.
        """
        query = self._SELECT + "WHERE d.project_id = ?"
        params: list[object] = [project_id]
        if in_library is not None:
            query += " AND d.in_library = ?"
            params.append(1 if in_library else 0)
        query += " ORDER BY d.sort_order, d.imported_at, d.filename"
        rows = self._all(query, tuple(params))
        documents = [Document.from_row(row) for row in rows]
        self._attach_page_numbers(project_id, documents)
        return documents

    def set_in_library(self, document_ids: list[str], in_library: bool) -> None:
        """Show or hide documents in the Image Library.

        Hiding keeps the row (and therefore every placed canvas item) alive.
        """
        if not document_ids:
            return
        with self.db.transaction() as connection:
            connection.executemany(
                "UPDATE documents SET in_library = ? WHERE id = ?",
                [(1 if in_library else 0, document_id) for document_id in document_ids],
            )

    def _attach_page_numbers(self, project_id: str, documents: list[Document]) -> None:
        if not documents:
            return
        rows = self._all(
            "SELECT i.document_id, p.page_number "
            "FROM page_items i JOIN pdf_pages p ON p.id = i.page_id "
            "WHERE p.project_id = ? ORDER BY p.page_number",
            (project_id,),
        )
        mapping: dict[str, list[int]] = {}
        for row in rows:
            mapping.setdefault(row["document_id"], []).append(int(row["page_number"]))
        for document in documents:
            document.page_numbers = sorted(mapping.get(document.id, []))

    def get(self, document_id: str) -> Document | None:
        row = self._one(self._SELECT + "WHERE d.id = ?", (document_id,))
        if not row:
            return None
        document = Document.from_row(row)
        # ``page_numbers`` is transient and must be filled here too: a caller
        # that got a document from ``get()`` used to see it as "not placed" even
        # when it was on a page, which made the library treat it as unused.
        self._attach_page_numbers(document.project_id, [document])
        return document

    def find_by_hash(self, project_id: str, file_hash: str) -> list[Document]:
        rows = self._all(
            self._SELECT + "WHERE d.project_id = ? AND d.file_hash = ?",
            (project_id, file_hash),
        )
        return [Document.from_row(row) for row in rows]

    def existing_hashes(self, project_id: str) -> dict[str, list[str]]:
        """Map hash -> filenames already visible in the Image Library.

        Documents hidden by a library removal are excluded on purpose: importing
        that file again must restore its existing entry (and keep its placed
        items) instead of being reported as a duplicate.
        """
        rows = self._all(
            "SELECT file_hash, filename FROM documents "
            "WHERE project_id = ? AND in_library = 1",
            (project_id,),
        )
        mapping: dict[str, list[str]] = {}
        for row in rows:
            mapping.setdefault(row["file_hash"], []).append(row["filename"])
        return mapping

    def next_sort_order(self, project_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM documents "
                "WHERE project_id = ?",
                (project_id,),
                default=0,
            )
        )


    def insert(self, document: Document) -> Document:
        with self.db.transaction() as connection:
            connection.execute(self._INSERT_SQL, self._insert_params(document))
        return document

    _INSERT_SQL = (
        "INSERT INTO documents (id, project_id, filename, source_path, "
        "file_hash, category_id, original_width, original_height, "
        "file_size, dpi_x, dpi_y, imported_at, thumbnail_path, "
        "image_format, sort_order, in_library) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    @staticmethod
    def _insert_params(document: Document) -> tuple:
        return (
            document.id,
            document.project_id,
            document.filename,
            document.source_path,
            document.file_hash,
            document.category_id,
            document.original_width,
            document.original_height,
            document.file_size,
            document.dpi_x,
            document.dpi_y,
            document.imported_at,
            document.thumbnail_path,
            document.image_format,
            document.sort_order,
            1 if document.in_library else 0,
        )

    def insert_many(self, documents: list[Document]) -> list[Document]:
        """Insert a whole batch in one transaction (all or nothing)."""
        if not documents:
            return documents
        with self.db.transaction() as connection:
            connection.executemany(
                self._INSERT_SQL,
                [self._insert_params(document) for document in documents],
            )
        return documents

    def update(self, document: Document) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE documents SET filename = ?, source_path = ?, file_hash = ?, "
                "category_id = ?, original_width = ?, original_height = ?, "
                "file_size = ?, dpi_x = ?, dpi_y = ?, thumbnail_path = ?, "
                "image_format = ?, sort_order = ? WHERE id = ?",
                (
                    document.filename,
                    document.source_path,
                    document.file_hash,
                    document.category_id,
                    document.original_width,
                    document.original_height,
                    document.file_size,
                    document.dpi_x,
                    document.dpi_y,
                    document.thumbnail_path,
                    document.image_format,
                    document.sort_order,
                    document.id,
                ),
            )

    def set_category(self, document_id: str, category_id: str | None) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE documents SET category_id = ? WHERE id = ?",
                (category_id, document_id),
            )

    def set_categories(self, document_ids: list[str], category_id: str | None) -> None:
        with self.db.transaction() as connection:
            connection.executemany(
                "UPDATE documents SET category_id = ? WHERE id = ?",
                [(category_id, document_id) for document_id in document_ids],
            )

    def set_sort_orders(self, ordered_ids: list[str]) -> None:
        with self.db.transaction() as connection:
            connection.executemany(
                "UPDATE documents SET sort_order = ? WHERE id = ?",
                [(index, document_id) for index, document_id in enumerate(ordered_ids)],
            )

    def delete(self, document_id: str) -> None:
        """Remove the document *from the project* - the file is untouched."""
        with self.db.transaction() as connection:
            connection.execute(
                "DELETE FROM page_items WHERE document_id = ?", (document_id,)
            )
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def thumbnail_paths(self, document_ids: list[str]) -> list[str]:
        """Thumbnail files owned by the given documents (for cleanup)."""
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        rows = self._all(
            f"SELECT thumbnail_path FROM documents WHERE id IN ({placeholders})",
            tuple(document_ids),
        )
        return [row["thumbnail_path"] for row in rows if row["thumbnail_path"]]

    def thumbnail_in_use(self, path: str, project_id: str) -> bool:
        """True when a document still points at the thumbnail *path*.

        Duplicated library entries share one thumbnail file, so the file may only
        be removed after the last document referencing it is gone.
        """
        if not path:
            return False
        total = self._scalar(
            "SELECT COUNT(*) FROM documents "
            "WHERE project_id = ? AND thumbnail_path = ?",
            (project_id, path),
            default=0,
        )
        return bool(total)

    def counts_by_category(self, project_id: str) -> dict[str | None, int]:
        rows = self._all(
            "SELECT category_id, COUNT(*) AS total FROM documents "
            "WHERE project_id = ? GROUP BY category_id",
            (project_id,),
        )
        return {row["category_id"]: int(row["total"]) for row in rows}

    def count(self, project_id: str, in_library: bool | None = None) -> int:
        """Images the project owns.

        Project lists/dashboard count every row (``None``), including documents
        hidden from the library while still placed on a page - otherwise a
        project with three placed images and no library entries reported
        "0 documents" next to its page.  Pass ``in_library=True`` for the library
        figure only.
        """
        query = "SELECT COUNT(*) FROM documents WHERE project_id = ?"
        params: list[object] = [project_id]
        if in_library is not None:
            query += " AND in_library = ?"
            params.append(1 if in_library else 0)
        return int(self._scalar(query, tuple(params), default=0))
