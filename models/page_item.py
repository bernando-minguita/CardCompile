"""Page item model - one placed instance of a document on a page.

All geometry is stored in millimetres relative to the top-left corner of the
page.  Rotation is stored in degrees (clockwise) around the item centre.
Crop values are normalised fractions (0..1) trimmed from each image edge, so a
crop is always reversible and never touches the original file.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from typing import Sequence

FIT_MODES = ("contain", "cover", "stretch")


@dataclass(slots=True)
class PageItem:
    id: str
    page_id: str
    document_id: str
    position_x: float = 0.0
    position_y: float = 0.0
    width: float = 50.0
    height: float = 50.0
    rotation: float = 0.0
    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 0.0
    crop_bottom: float = 0.0
    aspect_locked: bool = True
    fit_mode: str = "contain"
    z_index: int = 0
    is_locked: bool = False

    # ------------------------------------------------- perspective correction
    #: The four source corners as normalised fractions (0..1) of the image, in
    #: the order top-left, top-right, bottom-right, bottom-left.  The default is
    #: the identity quad (the whole image).  A phone photo of a card that is
    #: skewed or slanted is mapped from this quad onto a true rectangle when it
    #: is rendered, so the correction is a parameter - never baked pixels.
    quad_tl_x: float = 0.0
    quad_tl_y: float = 0.0
    quad_tr_x: float = 1.0
    quad_tr_y: float = 0.0
    quad_br_x: float = 1.0
    quad_br_y: float = 1.0
    quad_bl_x: float = 0.0
    quad_bl_y: float = 1.0

    # ------------------------------------------------------ tone correction
    #: Neutral multipliers are 1.0; ``adjust_shadow`` flattens uneven lighting.
    adjust_brightness: float = 1.0
    adjust_contrast: float = 1.0
    adjust_saturation: float = 1.0
    adjust_sharpness: float = 1.0
    adjust_shadow: bool = False

    # Transient flag (not persisted): set when an "original size" placement had
    # to be reduced so it fits the printable area.
    was_scaled_to_fit: bool = False

    # Transient flag (not persisted): set when the file's resolution tag would
    # have produced an unusably small card, so the item was scaled up to the
    # ID-1 long edge.
    was_enlarged: bool = False

    # --------------------------------------------------------------- geometry
    @property
    def right(self) -> float:
        return self.position_x + self.width

    @property
    def bottom(self) -> float:
        return self.position_y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.position_x + self.width / 2.0, self.position_y + self.height / 2.0)

    def rect(self) -> tuple[float, float, float, float]:
        return (self.position_x, self.position_y, self.width, self.height)

    def set_rect(self, x: float, y: float, width: float, height: float) -> None:
        self.position_x = round(float(x), 3)
        self.position_y = round(float(y), 3)
        self.width = round(max(1.0, float(width)), 3)
        self.height = round(max(1.0, float(height)), 3)

    def clone(self, *, new_id: str, page_id: str | None = None) -> "PageItem":
        return replace(
            self,
            id=new_id,
            page_id=page_id or self.page_id,
        )

    # ------------------------------------------------------------------- crop
    def crop_box(self) -> tuple[float, float, float, float]:
        """Normalised ``(left, top, right, bottom)`` of the *visible* region."""
        left = min(max(0.0, self.crop_left), 0.98)
        top = min(max(0.0, self.crop_top), 0.98)
        right = 1.0 - min(max(0.0, self.crop_right), 0.98 - left)
        bottom = 1.0 - min(max(0.0, self.crop_bottom), 0.98 - top)
        return (left, top, right, bottom)

    def crop_pixels(self, image_width: int, image_height: int) -> tuple[int, int, int, int]:
        """Pixel ``(left, top, right, bottom)`` box of the visible region."""
        left, top, right, bottom = self.crop_box()
        x0 = int(round(left * image_width))
        y0 = int(round(top * image_height))
        x1 = int(round(right * image_width))
        y1 = int(round(bottom * image_height))
        x0 = max(0, min(x0, image_width - 1))
        y0 = max(0, min(y0, image_height - 1))
        x1 = max(x0 + 1, min(x1, image_width))
        y1 = max(y0 + 1, min(y1, image_height))
        return x0, y0, x1, y1

    def has_crop(self) -> bool:
        return any(
            value > 0.0001
            for value in (self.crop_left, self.crop_top, self.crop_right, self.crop_bottom)
        )

    # ------------------------------------------------------------ perspective
    def quad(self) -> tuple[float, float, float, float, float, float, float, float]:
        """The four source corners as ``(tl_x, tl_y, tr_x, tr_y, br_x, br_y, bl_x, bl_y)``."""
        return (
            self.quad_tl_x,
            self.quad_tl_y,
            self.quad_tr_x,
            self.quad_tr_y,
            self.quad_br_x,
            self.quad_br_y,
            self.quad_bl_x,
            self.quad_bl_y,
        )

    def set_quad(self, values: Sequence[float]) -> None:
        """Store four corners, clamped to the image and kept convex enough to warp."""
        clamped = [min(1.0, max(0.0, float(value))) for value in values]
        if len(clamped) != 8:  # pragma: no cover - defensive
            raise ValueError("a quad needs eight normalised coordinates")
        (
            self.quad_tl_x,
            self.quad_tl_y,
            self.quad_tr_x,
            self.quad_tr_y,
            self.quad_br_x,
            self.quad_br_y,
            self.quad_bl_x,
            self.quad_bl_y,
        ) = (round(value, 5) for value in clamped)

    def has_perspective(self) -> bool:
        """True when the quad is not the identity (the whole image)."""
        identity = (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
        return any(abs(a - b) > 0.0005 for a, b in zip(self.quad(), identity))

    def reset_quad(self) -> None:
        self.set_quad((0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0))

    # -------------------------------------------------------------- tone
    def has_adjustments(self) -> bool:
        """True when any tone correction differs from neutral."""
        return (
            abs(self.adjust_brightness - 1.0) > 0.005
            or abs(self.adjust_contrast - 1.0) > 0.005
            or abs(self.adjust_saturation - 1.0) > 0.005
            or abs(self.adjust_sharpness - 1.0) > 0.005
            or bool(self.adjust_shadow)
        )

    def reset_adjustments(self) -> None:
        self.adjust_brightness = 1.0
        self.adjust_contrast = 1.0
        self.adjust_saturation = 1.0
        self.adjust_sharpness = 1.0
        self.adjust_shadow = False

    def has_correction(self) -> bool:
        """True when the item needs any image correction at render time."""
        return self.has_perspective() or self.has_adjustments()

    def correction_key(self) -> tuple:
        """Hashable signature of the correction, used to invalidate caches."""
        return (
            *self.quad(),
            round(self.adjust_brightness, 4),
            round(self.adjust_contrast, 4),
            round(self.adjust_saturation, 4),
            round(self.adjust_sharpness, 4),
            bool(self.adjust_shadow),
        )

    # ------------------------------------------------------------- factories
    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> "PageItem":
        data = dict(row)
        return cls(
            id=data["id"],
            page_id=data["page_id"],
            document_id=data["document_id"],
            position_x=float(data.get("position_x") or 0.0),
            position_y=float(data.get("position_y") or 0.0),
            width=float(data.get("width") or 50.0),
            height=float(data.get("height") or 50.0),
            rotation=float(data.get("rotation") or 0.0),
            crop_left=float(data.get("crop_left") or 0.0),
            crop_top=float(data.get("crop_top") or 0.0),
            crop_right=float(data.get("crop_right") or 0.0),
            crop_bottom=float(data.get("crop_bottom") or 0.0),
            aspect_locked=bool(data.get("aspect_locked", 1)),
            fit_mode=data.get("fit_mode") or "contain",
            z_index=int(data.get("z_index") or 0),
            is_locked=bool(data.get("is_locked", 0)),
            quad_tl_x=float(data.get("quad_tl_x") if data.get("quad_tl_x") is not None else 0.0),
            quad_tl_y=float(data.get("quad_tl_y") if data.get("quad_tl_y") is not None else 0.0),
            quad_tr_x=float(data.get("quad_tr_x") if data.get("quad_tr_x") is not None else 1.0),
            quad_tr_y=float(data.get("quad_tr_y") if data.get("quad_tr_y") is not None else 0.0),
            quad_br_x=float(data.get("quad_br_x") if data.get("quad_br_x") is not None else 1.0),
            quad_br_y=float(data.get("quad_br_y") if data.get("quad_br_y") is not None else 1.0),
            quad_bl_x=float(data.get("quad_bl_x") if data.get("quad_bl_x") is not None else 0.0),
            quad_bl_y=float(data.get("quad_bl_y") if data.get("quad_bl_y") is not None else 1.0),
            adjust_brightness=float(
                data.get("adjust_brightness")
                if data.get("adjust_brightness") is not None
                else 1.0
            ),
            adjust_contrast=float(
                data.get("adjust_contrast") if data.get("adjust_contrast") is not None else 1.0
            ),
            adjust_saturation=float(
                data.get("adjust_saturation")
                if data.get("adjust_saturation") is not None
                else 1.0
            ),
            adjust_sharpness=float(
                data.get("adjust_sharpness") if data.get("adjust_sharpness") is not None else 1.0
            ),
            adjust_shadow=bool(
                int(data.get("adjust_shadow") or 0)
                if data.get("adjust_shadow") is not None
                else 0
            ),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "page_id": self.page_id,
            "document_id": self.document_id,
            "position_x": self.position_x,
            "position_y": self.position_y,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "crop_left": self.crop_left,
            "crop_top": self.crop_top,
            "crop_right": self.crop_right,
            "crop_bottom": self.crop_bottom,
            "aspect_locked": int(self.aspect_locked),
            "fit_mode": self.fit_mode,
            "z_index": self.z_index,
            "is_locked": int(self.is_locked),
            "quad_tl_x": self.quad_tl_x,
            "quad_tl_y": self.quad_tl_y,
            "quad_tr_x": self.quad_tr_x,
            "quad_tr_y": self.quad_tr_y,
            "quad_br_x": self.quad_br_x,
            "quad_br_y": self.quad_br_y,
            "quad_bl_x": self.quad_bl_x,
            "quad_bl_y": self.quad_bl_y,
            "adjust_brightness": self.adjust_brightness,
            "adjust_contrast": self.adjust_contrast,
            "adjust_saturation": self.adjust_saturation,
            "adjust_sharpness": self.adjust_sharpness,
            "adjust_shadow": int(self.adjust_shadow),
        }
