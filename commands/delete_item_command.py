"""Delete image command."""

from __future__ import annotations

from typing import Any, Sequence

from commands.base import AppliedCommand, document_exists
from core.logging_setup import get_logger
from models.page_item import PageItem

LOGGER = get_logger("commands")


class DeleteItemsCommand(AppliedCommand):
    """Undo of a delete restores the exact rows (ids, geometry and crops)."""

    def __init__(
        self, editor, rows: Sequence[dict[str, Any]], label: str = "Delete image"
    ) -> None:
        super().__init__(label)
        self._editor = editor
        self._rows = [dict(row) for row in rows]

    def redo_action(self) -> None:
        layout = self._editor.layout
        for row in self._rows:
            layout.db.items.delete(row["id"])
        self._editor.reload_current_page()

    def undo_action(self) -> None:
        layout = self._editor.layout
        for row in self._rows:
            if layout.db.items.get(row["id"]) is not None:
                continue
            if not document_exists(layout, row["document_id"]):
                # The image was removed from the Image Library meanwhile; its
                # placement cannot come back (see document_exists()).
                LOGGER.info(
                    "Undo skipped item %s - its image is no longer in the library",
                    row["id"],
                )
                continue
            layout.db.items.insert(PageItem.from_row(row))
        self._editor.reload_current_page()
