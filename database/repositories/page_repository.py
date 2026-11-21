"""PDF page row access."""

from __future__ import annotations

from database.repositories.base import BaseRepository
from core.logging_setup import get_logger
from models.pdf_page import PdfPage

LOGGER = get_logger("pages")


class PageRepository(BaseRepository):
    """CRUD operations for ``pdf_pages``."""

    def list_for_project(self, project_id: str) -> list[PdfPage]:
        rows = self._all(
            "SELECT * FROM pdf_pages WHERE project_id = ? ORDER BY page_number",
            (project_id,),
        )
        return [PdfPage.from_row(row) for row in rows]

    def get(self, page_id: str) -> PdfPage | None:
        row = self._one("SELECT * FROM pdf_pages WHERE id = ?", (page_id,))
        return PdfPage.from_row(row) if row else None

    def count(self, project_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COUNT(*) FROM pdf_pages WHERE project_id = ?",
                (project_id,),
                default=0,
            )
        )

    def next_page_number(self, project_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COALESCE(MAX(page_number), 0) + 1 FROM pdf_pages "
                "WHERE project_id = ?",
                (project_id,),
                default=1,
            )
        )

    def insert(self, page: PdfPage) -> PdfPage:
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO pdf_pages (id, project_id, page_number, paper_size, "
                "orientation, width_mm, height_mm, margin_top, margin_bottom, "
                "margin_left, margin_right, background_color) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    page.id,
                    page.project_id,
                    page.page_number,
                    page.paper_size,
                    page.orientation,
                    page.width_mm,
                    page.height_mm,
                    page.margin_top,
                    page.margin_bottom,
                    page.margin_left,
                    page.margin_right,
                    page.background_color,
                ),
            )
        return page

    def update(self, page: PdfPage) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE pdf_pages SET page_number = ?, paper_size = ?, "
                "orientation = ?, width_mm = ?, height_mm = ?, margin_top = ?, "
                "margin_bottom = ?, margin_left = ?, margin_right = ?, "
                "background_color = ? WHERE id = ?",
                (
                    page.page_number,
                    page.paper_size,
                    page.orientation,
                    page.width_mm,
                    page.height_mm,
                    page.margin_top,
                    page.margin_bottom,
                    page.margin_left,
                    page.margin_right,
                    page.background_color,
                    page.id,
                ),
            )

    def delete(self, page_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM pdf_pages WHERE id = ?", (page_id,))

    def delete_all(self, project_id: str) -> None:
        """Remove every page of a project (used when restoring a snapshot)."""
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM pdf_pages WHERE project_id = ?", (project_id,))

    def resequence(self, project_id: str, ordered_ids: list[str]) -> None:
        """Assign page numbers 1..n following *ordered_ids*.

        The numbers are moved out of the way first (negative offsets derived from
        ``rowid``) so the unique index on ``(project_id, page_number)`` can never
        collide while a page is reordered.  Pages the caller left out are appended
        in their previous order: otherwise they would keep their negative number
        and silently disappear from every ordered query.
        """
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE pdf_pages SET page_number = -(1000000 + rowid) "
                "WHERE project_id = ?",
                (project_id,),
            )
            rows = connection.execute(
                "SELECT id FROM pdf_pages WHERE project_id = ? ORDER BY rowid",
                (project_id,),
            ).fetchall()
            sequence = list(ordered_ids)
            seen = set(sequence)
            leftover = [row["id"] for row in rows if row["id"] not in seen]
            if leftover:
                LOGGER.warning(
                    "%s page(s) were missing from the new order; appended at the end",
                    len(leftover),
                )
                sequence.extend(leftover)
            connection.executemany(
                "UPDATE pdf_pages SET page_number = ? WHERE id = ? AND project_id = ?",
                [
                    (index + 1, page_id, project_id)
                    for index, page_id in enumerate(sequence)
                ],
            )
