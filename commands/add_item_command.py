"""Add image command (documents placed on a page)."""

from __future__ import annotations

from typing import Any, Sequence

from commands.base import AppliedCommand, document_exists
from models.page_item import PageItem


class AddItemsCommand(AppliedCommand):
    """Undo of an "add image" removes the rows, redo inserts them again."""

    def __init__(self, editor, rows: Sequence[dict[str, Any]], label: str = "Add image") -> None:
        super().__init__(label)
        self._editor = editor
        self._rows = [dict(row) for row in rows]

    def redo_action(self) -> None:
        layout = self._editor.layout
        for row in self._rows:
            if layout.db.items.get(row["id"]) is not None:
                continue
            # The image may have been removed from the Image Library meanwhile;
            # re-inserting the row would violate the foreign key.
            if not document_exists(layout, row["document_id"]):
                continue
            layout.db.items.insert(PageItem.from_row(row))
        self._editor.reload_current_page()

    def undo_action(self) -> None:
        layout = self._editor.layout
        for row in self._rows:
            layout.db.items.delete(row["id"])
        self._editor.reload_current_page()
