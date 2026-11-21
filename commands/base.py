"""Undo/redo commands that keep the database and the canvas in sync.

Commands are *applied before* they are pushed onto the stack (the user already
performed the action), so the first ``redo()`` call is skipped.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtGui import QUndoCommand

from models.page_item import PageItem

GEOMETRY_FIELDS = (
    "position_x",
    "position_y",
    "width",
    "height",
    "rotation",
    "crop_left",
    "crop_top",
    "crop_right",
    "crop_bottom",
    "z_index",
    "is_locked",
    "aspect_locked",
    "fit_mode",
    # Perspective correction and tone adjustments travel with the geometry so
    # GeometryCommand (and every subclass: crop, rotate, resize, layout) undoes
    # them too.  Leaving them out made "undo" silently keep the correction.
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


def item_snapshot(item: PageItem) -> dict[str, Any]:
    """Geometry snapshot used by the undo commands."""
    return {field: getattr(item, field) for field in GEOMETRY_FIELDS}


def item_row(item: PageItem) -> dict[str, Any]:
    """Full row snapshot (used for add/delete and page structure commands)."""
    return item.to_dict()


def document_exists(layout, document_id: str) -> bool:
    """True while the source image of a row still exists in the Image Library.

    Undo entries keep whole page-item rows.  If the library record was removed
    meanwhile, re-inserting such a row violates the foreign key on
    ``page_items.document_id``; every restore path must therefore skip it.
    """
    try:
        return layout.db.documents.get(document_id) is not None
    except Exception:  # pragma: no cover - defensive
        return False


class AppliedCommand(QUndoCommand):
    """Base class for commands whose effect is already visible."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._skip_first_redo = True

    def redo(self) -> None:  # noqa: D102 - Qt API
        if self._skip_first_redo:
            self._skip_first_redo = False
            return
        self.redo_action()

    def undo(self) -> None:  # noqa: D102 - Qt API
        self.undo_action()

    def redo_action(self) -> None:
        raise NotImplementedError

    def undo_action(self) -> None:
        raise NotImplementedError


#: Undo id shared by consecutive group moves; ``-1`` (Qt's default) never merges.
GROUP_MOVE_MERGE_ID = 1200
#: Window in which a follow-up group move folds into the previous one.
GROUP_MOVE_MERGE_WINDOW = 2.0


class GeometryCommand(AppliedCommand):
    """Restores geometry/crop/rotation for a set of items."""

    def __init__(
        self,
        editor,
        label: str,
        before: dict[str, dict[str, Any]],
        after: dict[str, dict[str, Any]],
        *,
        merge: bool = False,
    ) -> None:
        super().__init__(label)
        self._editor = editor
        self._before = before
        self._after = after
        self._merge = merge
        self._stamp = time.monotonic()
        self._changed = {
            item_id: state
            for item_id, state in after.items()
            if before.get(item_id) != state
        }
        self.setObsolete(not self._changed)

    def id(self) -> int:  # noqa: A003 - Qt API
        """Allow a burst of group moves to fold into one undo entry."""
        return GROUP_MOVE_MERGE_ID if self._merge else -1

    def mergeWith(self, other) -> bool:  # noqa: N802 - Qt API
        """Fold a following group move into this one.

        Spinbox arrow steps arrive as one command per step; without merging,
        Ctrl+Z would undo a single millimetre.  The first command keeps its
        ``before`` state and takes the newest ``after`` state, so one undo
        restores the position the group had before the first step.
        """
        if not isinstance(other, GeometryCommand):
            return False
        if not self._merge or not other._merge:
            return False
        if set(self._after) != set(other._after):
            return False
        # Only a quick burst (arrow steps, fast retyping) is one gesture; a
        # position typed minutes later must stay a separate undo entry.
        if time.monotonic() - self._stamp > GROUP_MOVE_MERGE_WINDOW:
            return False
        self._stamp = time.monotonic()
        self._after = dict(other._after)
        self._changed = {
            item_id: state
            for item_id, state in self._after.items()
            if self._before.get(item_id) != state
        }
        self.setObsolete(not self._changed)
        return True

    def _apply(self, state: dict[str, dict[str, Any]]) -> None:
        layout = self._editor.layout
        for item_id, values in state.items():
            item = layout.db.items.get(item_id)
            if item is None:
                continue
            for field, value in values.items():
                setattr(item, field, value)
            layout.save_item(item)
            graphic = self._editor.scene.item_by_id(item_id)
            if graphic is not None:
                self._editor.scene.refresh_item(item)
        self._editor.after_command()

    def redo_action(self) -> None:
        self._apply(self._after)

    def undo_action(self) -> None:
        self._apply(self._before)


class ItemStateCommand(AppliedCommand):
    """Generic command driven by three callables (apply, revert, refresh)."""

    def __init__(
        self,
        label: str,
        apply_fn: Callable[[], None],
        revert_fn: Callable[[], None],
    ) -> None:
        super().__init__(label)
        self._apply_fn = apply_fn
        self._revert_fn = revert_fn

    def redo_action(self) -> None:
        self._apply_fn()

    def undo_action(self) -> None:
        self._revert_fn()
