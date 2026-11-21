"""Automatic page overflow: decide which pages a layout run needs.

When a grid arrangement needs more slots than the target page provides the
engine either creates follow-up pages (automatic overflow) or reports how many
documents could not be placed (overflow disabled), so the UI can warn the user
instead of silently dropping documents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from core.logging_setup import get_logger

LOGGER = get_logger("overflow")


@dataclass(slots=True)
class OverflowPlan:
    """Result of planning a multi page layout run."""

    page_ids: list[str] = field(default_factory=list)
    capacity: int = 1
    total_slots: int = 0
    unassigned_slots: int = 0
    created_pages: int = 0

    @property
    def overflowed(self) -> bool:
        return self.created_pages > 0

    @property
    def has_unassigned(self) -> bool:
        return self.unassigned_slots > 0


class PageOverflowEngine:
    """Plans (and optionally creates) the pages required by a layout run."""

    def __init__(self, layout_service) -> None:
        self._layout = layout_service

    def plan(
        self,
        start_page_id: str,
        document_count: int,
        capacity: int,
        auto_overflow: bool = True,
        document_ids: Sequence[str] | None = None,
    ) -> OverflowPlan:
        capacity = max(1, int(capacity))
        document_count = max(0, int(document_count))
        pages_needed = max(1, (document_count + capacity - 1) // capacity)
        plan = OverflowPlan(
            page_ids=[start_page_id],
            capacity=capacity,
            total_slots=document_count,
        )

        if pages_needed > 1:
            if not auto_overflow:
                plan.unassigned_slots = document_count - capacity
                plan.page_ids = [start_page_id]
                LOGGER.info(
                    "Overflow disabled: %s documents cannot be placed",
                    plan.unassigned_slots,
                )
                return plan
            # Re-use the pages a previous run already produced instead of adding
            # new ones every time: "one document per page" applied twice used to
            # leave 7 pages, then 10, then 13 ...
            for page_id in self._reusable_page_ids(
                start_page_id, set(document_ids or ())
            ):
                if len(plan.page_ids) >= pages_needed:
                    break
                plan.page_ids.append(page_id)
            if len(plan.page_ids) < pages_needed:
                previous = self._layout.pages()[-1].id
                while len(plan.page_ids) < pages_needed:
                    page = self._layout.add_page(after_page_id=previous)
                    previous = page.id
                    plan.page_ids.append(page.id)
                    plan.created_pages += 1
            if plan.created_pages:
                LOGGER.info(
                    "Automatic overflow created %s additional page(s)",
                    plan.created_pages,
                )
            else:
                LOGGER.info(
                    "Automatic overflow re-used %s existing page(s)",
                    len(plan.page_ids) - 1,
                )
        return plan

    def _reusable_page_ids(
        self, start_page_id: str, document_ids: set[str]
    ) -> list[str]:
        """Pages after *start_page_id* that this run may fill again.

        A page qualifies when it is empty or when everything on it belongs to the
        documents being laid out (i.e. it is the output of an earlier run of the
        same layout).  Pages holding other content are skipped, never cleared.
        """
        pages = self._layout.pages()
        ids = [page.id for page in pages]
        if start_page_id not in ids:
            return []
        reusable: list[str] = []
        for page in pages[ids.index(start_page_id) + 1 :]:
            if self._has_foreign_content(page.id, document_ids):
                continue
            reusable.append(page.id)
        return reusable

    def _has_foreign_content(self, page_id: str, document_ids: set[str]) -> bool:
        try:
            if self._layout.db.labels.list_for_page(page_id):
                return True
        except AttributeError:  # pragma: no cover - layout without labels
            pass
        for item in self._layout.items(page_id):
            if item.document_id not in document_ids:
                return True
        return False


def grid_preset(rows: int, columns: int) -> tuple[int, int]:
    """Clamp a rows/columns pair to the supported range."""
    return max(1, min(int(rows), 8)), max(1, min(int(columns), 8))


def best_grid_for(count: int, portrait: bool = True) -> tuple[int, int]:
    """Suggest a rows/columns pair that fits *count* documents neatly."""
    count = max(1, int(count))
    if count == 1:
        return 1, 1
    if count == 2:
        return (2, 1) if portrait else (1, 2)
    if count == 4:
        return 2, 2
    if count == 6:
        return 3, 2
    columns = 1
    while columns * columns < count:
        columns += 1
    rows = (count + columns - 1) // columns
    return max(1, rows), max(1, columns)
