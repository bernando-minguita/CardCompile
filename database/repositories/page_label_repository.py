"""Page label (text box) row access."""

from __future__ import annotations

from models.page_label import PageLabel

from database.repositories.base import BaseRepository


class PageLabelRepository(BaseRepository):
    """CRUD operations for text labels placed on pages."""

    def list_for_page(self, page_id: str) -> list[PageLabel]:
        rows = self._all(
            "SELECT * FROM page_labels WHERE page_id = ? ORDER BY z_index, id",
            (page_id,),
        )
        return [PageLabel.from_row(row) for row in rows]

    def list_for_project(self, project_id: str) -> list[PageLabel]:
        rows = self._all(
            "SELECT l.* FROM page_labels l JOIN pdf_pages p ON p.id = l.page_id "
            "WHERE p.project_id = ? ORDER BY p.page_number, l.z_index, l.id",
            (project_id,),
        )
        return [PageLabel.from_row(row) for row in rows]

    def get(self, label_id: str) -> PageLabel | None:
        row = self._one("SELECT * FROM page_labels WHERE id = ?", (label_id,))
        return PageLabel.from_row(row) if row else None

    def max_z_index(self, page_id: str) -> int:
        return int(
            self._scalar(
                "SELECT COALESCE(MAX(z_index), 0) FROM page_labels WHERE page_id = ?",
                (page_id,),
                default=0,
            )
        )

    def insert(self, label: PageLabel) -> PageLabel:
        data = label.to_dict()
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO page_labels (id, page_id, text, position_x, position_y, "
                "font_family, font_size, bold, italic, color, rotation, z_index) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["id"],
                    data["page_id"],
                    data["text"],
                    data["position_x"],
                    data["position_y"],
                    data["font_family"],
                    data["font_size"],
                    data["bold"],
                    data["italic"],
                    data["color"],
                    data["rotation"],
                    data["z_index"],
                ),
            )
        return label

    def update(self, label: PageLabel) -> None:
        data = label.to_dict()
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE page_labels SET page_id = ?, text = ?, position_x = ?, "
                "position_y = ?, font_family = ?, font_size = ?, bold = ?, "
                "italic = ?, color = ?, rotation = ?, z_index = ? WHERE id = ?",
                (
                    data["page_id"],
                    data["text"],
                    data["position_x"],
                    data["position_y"],
                    data["font_family"],
                    data["font_size"],
                    data["bold"],
                    data["italic"],
                    data["color"],
                    data["rotation"],
                    data["z_index"],
                    data["id"],
                ),
            )

    def delete(self, label_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM page_labels WHERE id = ?", (label_id,))

    def delete_for_page(self, page_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM page_labels WHERE page_id = ?", (page_id,))

    def delete_for_project(self, project_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "DELETE FROM page_labels WHERE page_id IN "
                "(SELECT id FROM pdf_pages WHERE project_id = ?)",
                (project_id,),
            )

    def copy_page(self, source_page_id: str, target_page_id: str) -> None:
        """Duplicate the labels of a page (used when a page is duplicated)."""
        with self.db.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM page_labels WHERE page_id = ?", (source_page_id,)
            ).fetchall()
            for row in rows:
                label = PageLabel.from_row(row)
                label.id = PageLabel().id
                label.page_id = target_page_id
                data = label.to_dict()
                connection.execute(
                    "INSERT INTO page_labels (id, page_id, text, position_x, "
                    "position_y, font_family, font_size, bold, italic, color, "
                    "rotation, z_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        data["id"],
                        data["page_id"],
                        data["text"],
                        data["position_x"],
                        data["position_y"],
                        data["font_family"],
                        data["font_size"],
                        data["bold"],
                        data["italic"],
                        data["color"],
                        data["rotation"],
                        data["z_index"],
                    ),
                )
