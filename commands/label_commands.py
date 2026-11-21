"""Undo/redo commands for text labels."""

from __future__ import annotations

from typing import Any

from commands.base import AppliedCommand
from models.page_label import PageLabel


class LabelMoveCommand(AppliedCommand):
    """A label was dragged to a new position."""

    def __init__(self, editor, label_id: str, before: dict, after: dict) -> None:
        super().__init__("Move text")
        self._editor = editor
        self._label_id = label_id
        self._before = before
        self._after = after

    def _apply(self, values: dict) -> None:
        label = self._editor.layout.db.labels.get(self._label_id)
        if label is None:
            return
        for key, value in values.items():
            setattr(label, key, value)
        self._editor.layout.db.labels.update(label)
        self._editor.scene.refresh_label(label)

    def undo_action(self) -> None:  # noqa: D102 - Qt API
        self._apply(self._before)

    def redo_action(self) -> None:  # noqa: D102 - Qt API
        self._apply(self._after)


class LabelEditCommand(AppliedCommand):
    """Text, font, size or colour of a label changed."""

    def __init__(self, editor, label_id: str, before: dict, after: dict) -> None:
        super().__init__("Edit text")
        self._editor = editor
        self._label_id = label_id
        self._before = before
        self._after = after

    def _apply(self, values: dict[str, Any]) -> None:
        label = self._editor.layout.db.labels.get(self._label_id)
        if label is None:
            return
        for key, value in values.items():
            setattr(label, key, value)
        self._editor.layout.db.labels.update(label)
        self._editor.scene.refresh_label(label)

    def undo_action(self) -> None:  # noqa: D102 - Qt API
        self._apply(self._before)

    def redo_action(self) -> None:  # noqa: D102 - Qt API
        self._apply(self._after)


class DeleteLabelsCommand(AppliedCommand):
    """One or more labels were deleted."""

    def __init__(self, editor, rows: list[dict], text: str) -> None:
        super().__init__(text)
        self._editor = editor
        self._rows = rows

    def _add_back(self) -> None:
        for row in self._rows:
            label = PageLabel.from_row(row)
            self._editor.layout.db.labels.insert(label)

    def _remove(self) -> None:
        for row in self._rows:
            self._editor.layout.db.labels.delete(row["id"])

    def undo_action(self) -> None:  # noqa: D102 - Qt API
        self._add_back()
        self._editor.reload_current_page()

    def redo_action(self) -> None:  # noqa: D102 - Qt API
        self._remove()
        self._editor.reload_current_page()


class AddLabelsCommand(AppliedCommand):
    """Labels were added (used so the action can be undone)."""

    def __init__(self, editor, rows: list[dict], text: str) -> None:
        super().__init__(text)
        self._editor = editor
        self._rows = rows

    def undo_action(self) -> None:  # noqa: D102 - Qt API
        for row in self._rows:
            self._editor.layout.db.labels.delete(row["id"])
        self._editor.reload_current_page()

    def redo_action(self) -> None:  # noqa: D102 - Qt API
        for row in self._rows:
            self._editor.layout.db.labels.insert(PageLabel.from_row(row))
        self._editor.reload_current_page()
