"""Page item (placed image) row access."""

from __future__ import annotations

from database.repositories.base import BaseRepository
from models.page_item import PageItem


class PageItemRepository(BaseRepository):
    """CRUD operations for images placed on pages."""

    def list_for_page(self, page_id: str) -> list[PageItem]:
        rows = self._all(
            "SELECT * FROM page_items WHERE page_id = ? ORDER BY z_index, id",
            (page_id,),
        )
        return [PageItem.from_row(row) for row in rows]

    def list_for_project(self, project_id: str) -> list[PageItem]:
        rows = self._all(
            "SELECT i.* FROM page_items i JOIN pdf_pages p ON p.id = i.page_id "
            "WHERE p.project_id = ? ORDER BY p.page_number, i.z_index, i.id",
            (project_id,),
        )
        return [PageItem.from_row(row) for row in rows]

    def get(self, item_id: str) -> PageItem | None:
        row = self._one("SELECT * FROM page_items WHERE id = ?", (item_id,))
        return PageItem.from_row(row) if row else None

    def count_for_page(self, page_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COUNT(*) FROM page_items WHERE page_id = ?",
                (page_id,),
                default=0,
            )
        )

    def counts_by_page(self, project_id: str) -> dict[str, int]:
        rows = self._all(
            "SELECT i.page_id, COUNT(*) AS total FROM page_items i "
            "JOIN pdf_pages p ON p.id = i.page_id WHERE p.project_id = ? "
            "GROUP BY i.page_id",
            (project_id,),
        )
        return {row["page_id"]: int(row["total"]) for row in rows}

    def max_z_index(self, page_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COALESCE(MAX(z_index), 0) FROM page_items WHERE page_id = ?",
                (page_id,),
                default=0,
            )
        )

    #: Columns written by :meth:`insert` and :meth:`update`, in binding order.
    #: ``page_items`` gained the quad/adjustment columns in schema 7, so the
    #: statement is built from this tuple instead of being spelled out twice.
    _WRITE_COLUMNS = (
        "page_id",
        "document_id",
        "position_x",
        "position_y",
        "width",
        "height",
        "rotation",
        "crop_left",
        "crop_top",
        "crop_right",
        "crop_bottom",
        "aspect_locked",
        "fit_mode",
        "z_index",
        "is_locked",
        "quad_tl_x",
        "quad_tl_y",
        "quad_tr_x",
        "quad_tr_y",
        "quad_br_x",
        "quad_br_y",
        "quad_bl_x",
        "quad_bl_y",
        "adjust_brightness",
        "adjust_contrast",
        "adjust_saturation",
        "adjust_sharpness",
        "adjust_shadow",
    )

    @classmethod
    def _values(cls, item: PageItem) -> tuple:
        """Bind values in :attr:`_WRITE_COLUMNS` order."""
        return (
            item.page_id,
            item.document_id,
            item.position_x,
            item.position_y,
            item.width,
            item.height,
            item.rotation,
            item.crop_left,
            item.crop_top,
            item.crop_right,
            item.crop_bottom,
            int(item.aspect_locked),
            item.fit_mode,
            item.z_index,
            int(item.is_locked),
            item.quad_tl_x,
            item.quad_tl_y,
            item.quad_tr_x,
            item.quad_tr_y,
            item.quad_br_x,
            item.quad_br_y,
            item.quad_bl_x,
            item.quad_bl_y,
            item.adjust_brightness,
            item.adjust_contrast,
            item.adjust_saturation,
            item.adjust_sharpness,
            int(item.adjust_shadow),
        )

    def insert(self, item: PageItem) -> PageItem:
        columns = ", ".join(("id", *self._WRITE_COLUMNS))
        placeholders = ", ".join("?" for _ in range(len(self._WRITE_COLUMNS) + 1))
        with self.db.transaction() as connection:
            connection.execute(
                f"INSERT INTO page_items ({columns}) VALUES ({placeholders})",
                (item.id, *self._values(item)),
            )
        return item

    def update(self, item: PageItem) -> None:
        assignments = ", ".join(f"{column} = ?" for column in self._WRITE_COLUMNS)
        with self.db.transaction() as connection:
            connection.execute(
                f"UPDATE page_items SET {assignments} WHERE id = ?",
                (*self._values(item), item.id),
            )

    def update_many(self, items: list[PageItem]) -> None:
        if not items:
            return
        assignments = ", ".join(f"{column} = ?" for column in self._WRITE_COLUMNS)
        with self.db.transaction() as connection:
            connection.executemany(
                f"UPDATE page_items SET {assignments} WHERE id = ?",
                [(*self._values(item), item.id) for item in items],
            )

    def delete(self, item_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM page_items WHERE id = ?", (item_id,))

    def delete_for_page(self, page_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM page_items WHERE page_id = ?", (page_id,))

    def delete_for_project(self, project_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "DELETE FROM page_items WHERE page_id IN "
                "(SELECT id FROM pdf_pages WHERE project_id = ?)",
                (project_id,),
            )

    def set_z_orders(self, ordered_ids: list[str]) -> None:
        with self.db.transaction() as connection:
            connection.executemany(
                "UPDATE page_items SET z_index = ? WHERE id = ?",
                [(index + 1, item_id) for index, item_id in enumerate(ordered_ids)],
            )
