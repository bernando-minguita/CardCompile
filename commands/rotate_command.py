"""Rotate command."""

from __future__ import annotations

from commands.base import GeometryCommand


class RotateCommand(GeometryCommand):
    def __init__(self, editor, before, after, degrees: float | None = None) -> None:
        label = (
            f"Rotate image {degrees:g} deg" if degrees is not None else "Rotate image"
        )
        super().__init__(editor, label, before, after)
