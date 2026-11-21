"""Move command (also used for multi item drags)."""

from __future__ import annotations

from commands.base import GeometryCommand


class MoveCommand(GeometryCommand):
    def __init__(self, editor, before, after) -> None:
        super().__init__(editor, "Move image", before, after)
