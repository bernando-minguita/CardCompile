"""Automatic layout command (grid presets)."""

from __future__ import annotations

from commands.page_command import PageStateCommand


class LayoutCommand(PageStateCommand):
    """Applying a grid arrangement can create pages and items at once."""

    def __init__(self, editor, before, after, label: str = "Apply automatic layout") -> None:
        super().__init__(editor, label, before, after)
