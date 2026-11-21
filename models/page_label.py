"""Text label model - a caption/note placed directly on a page.

Labels are independent of documents: they carry their own text, font and colour
and are exported as real PDF text (not as an image).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field

#: Font families offered in the dialog.  These are the Windows families Qt can
#: actually resolve: it has no real "Helvetica" or "Times-Roman", so it
#: substituted Tahoma for both while the PDF used the matching base-14 face -
#: the canvas and the export disagreed in family and in text width.
FONT_CHOICES = (
    "Arial",
    "Times New Roman",
    "Courier New",
)

DEFAULT_FONT_FAMILY = "Arial"

DEFAULT_FONT_SIZE = 12.0

#: Family for every stored name, including the base-14 names that older projects
#: still contain.  Shared by the canvas and the PDF writer so both draw the same
#: face.
_FAMILY_ALIASES = {
    "arial": "Arial",
    "helvetica": "Arial",
    "times new roman": "Times New Roman",
    "times-roman": "Times New Roman",
    "times": "Times New Roman",
    "courier new": "Courier New",
    "courier": "Courier New",
}


#: Typographic ascender / descender of each offered family, as a fraction of the
#: font size (the hhea values of the Windows face each choice maps to).  The
#: canvas and the PDF writer both place the baseline and size the label box from
#: these: Qt's ascent and PyMuPDF's ``Font.ascender`` (a bounding-box value,
#: 1.075 for Helvetica) disagree by ~0.8 mm at 12 pt.
FONT_ASCENDERS = {
    "Arial": 0.905,
    "Times New Roman": 0.891,
    "Courier New": 0.833,
}

FONT_DESCENDERS = {
    "Arial": 0.212,
    "Times New Roman": 0.216,
    "Courier New": 0.300,
}


def font_ascender(name: str) -> float:
    """Baseline offset below the text top, as a fraction of the font size."""
    return FONT_ASCENDERS.get(canonical_font_family(name), FONT_ASCENDERS["Arial"])


def font_descender(name: str) -> float:
    """Depth below the baseline, as a fraction of the font size."""
    return FONT_DESCENDERS.get(canonical_font_family(name), FONT_DESCENDERS["Arial"])


def canonical_font_family(name: str) -> str:
    """Canonical Qt family for a stored label font.

    Legacy base-14 names (``Helvetica``, ``Times-Roman``, ``Courier``) map onto
    the metric-compatible Windows family; unknown names are passed through so a
    user-typed family still reaches Qt unchanged.
    """
    text = (name or "").strip()
    return _FAMILY_ALIASES.get(text.lower(), text or DEFAULT_FONT_FAMILY)

#: Padding between the label's top-left corner and its text (mm).  The canvas
#: and the PDF writer must use the same value or exported text drifts.
LABEL_PADDING_MM = 0.8


@dataclass(slots=True)
class PageLabel:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    page_id: str = ""
    text: str = "Text"
    position_x: float = 10.0
    position_y: float = 10.0
    font_family: str = DEFAULT_FONT_FAMILY
    font_size: float = DEFAULT_FONT_SIZE
    bold: bool = False
    italic: bool = False
    color: str = "#000000"
    rotation: float = 0.0
    z_index: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> "PageLabel":
        data = dict(row)
        return cls(
            id=data["id"],
            page_id=data["page_id"],
            text=data.get("text") or "",
            position_x=float(data.get("position_x") or 0.0),
            position_y=float(data.get("position_y") or 0.0),
            font_family=data.get("font_family") or DEFAULT_FONT_FAMILY,
            font_size=float(data.get("font_size") or DEFAULT_FONT_SIZE),
            bold=bool(data.get("bold", 0)),
            italic=bool(data.get("italic", 0)),
            color=data.get("color") or "#000000",
            rotation=float(data.get("rotation") or 0.0),
            z_index=int(data.get("z_index") or 0),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "page_id": self.page_id,
            "text": self.text,
            "position_x": self.position_x,
            "position_y": self.position_y,
            "font_family": self.font_family,
            "font_size": self.font_size,
            "bold": int(self.bold),
            "italic": int(self.italic),
            "color": self.color,
            "rotation": self.rotation,
            "z_index": self.z_index,
        }
