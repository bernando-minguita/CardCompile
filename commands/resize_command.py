"""Resize command."""

from __future__ import annotations

from commands.base import GeometryCommand


class ResizeCommand(GeometryCommand):
    def __init__(self, editor, before, after) -> None:
        super().__init__(editor, "Resize image", before, after)
