"""Crop command."""

from __future__ import annotations

from commands.base import GeometryCommand


class CropCommand(GeometryCommand):
    def __init__(self, editor, before, after) -> None:
        super().__init__(editor, "Crop image", before, after)
