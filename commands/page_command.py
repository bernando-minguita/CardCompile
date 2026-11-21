"""Page structure commands: add, delete, duplicate, reorder and page properties.

Page operations can move or destroy many items at once, so the commands
snapshot the complete page/item state and restore it verbatim on undo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from commands.base import AppliedCommand, document_exists
from core.logging_setup import get_logger
from models.page_item import PageItem
from models.page_label import PageLabel
from models.pdf_page import PdfPage

LOGGER = get_logger("commands")


@dataclass(slots=True)
class LayoutState:
    pages: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    labels: list[dict[str, Any]] = field(default_factory=list)

    @property
    def page_ids(self) -> list[str]:
        return [page["id"] for page in self.pages]


def capture_state(layout) -> LayoutState:
    """Read the complete page/item state of a project."""
    pages = [page.to_dict() for page in layout.pages()]
    items = [item.to_dict() for item in layout.all_items()]
    labels = [
        label.to_dict() for label in layout.db.labels.list_for_project(layout.project.id)
    ]
    return LayoutState(pages=pages, items=items, labels=labels)


def restore_state(layout, state: LayoutState) -> None:
    """Rewrite the project pages/items from *state* inside one transaction.

    Page-item rows whose source image is no longer in the Image Library are
    skipped: their ``document_id`` foreign key would fail, which used to abort
    the whole restore (leaving the undo stack stuck on a broken entry).
    """
    database = layout.db
    with database.db.transaction():
        # Delete labels explicitly: page deletion cascades them away, so they
        # must be re-inserted after the pages or they would be lost for good.
        database.labels.delete_for_project(layout.project.id)
        database.items.delete_for_project(layout.project.id)
        database.pages.delete_all(layout.project.id)
        for page_row in state.pages:
            database.pages.insert(PdfPage.from_row(page_row))
        for item_row in state.items:
            if not document_exists(layout, item_row["document_id"]):
                LOGGER.warning(
                    "Skipping item %s while restoring: its image is no longer in "
                    "the library",
                    item_row["id"],
                )
                continue
            database.items.insert(PageItem.from_row(item_row))
        for label_row in state.labels:
            database.labels.insert(PageLabel.from_row(label_row))


class PageStateCommand(AppliedCommand):
    """Undoable structural page change."""

    def __init__(self, editor, label: str, before: LayoutState, after: LayoutState) -> None:
        super().__init__(label)
        self._editor = editor
        self._before = before
        self._after = after

    def redo_action(self) -> None:
        restore_state(self._editor.layout, self._after)
        self._editor.reload_pages()

    def undo_action(self) -> None:
        restore_state(self._editor.layout, self._before)
        self._editor.reload_pages()


class PagePropertiesCommand(AppliedCommand):
    """Paper size, orientation, margins and background of one page."""

    def __init__(
        self,
        editor,
        before: dict[str, Any],
        after: dict[str, Any],
        label: str = "Change page settings",
    ) -> None:
        super().__init__(label)
        self._editor = editor
        self._before = dict(before)
        self._after = dict(after)

    def _apply(self, data: dict[str, Any]) -> None:
        layout = self._editor.layout
        page = layout.db.pages.get(data["id"])
        if page is None:
            return
        for field_name, value in data.items():
            if field_name != "id" and hasattr(page, field_name):
                setattr(page, field_name, value)
        layout.db.pages.update(page)
        self._editor.reload_pages()

    def redo_action(self) -> None:
        self._apply(self._after)

    def undo_action(self) -> None:
        self._apply(self._before)
