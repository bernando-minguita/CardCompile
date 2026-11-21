"""Report every image in a folder: pixels, DPI, physical size, card fit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from core.image_service import read_image_dpi  # noqa: E402

CARD_MM = (85.6, 54.0)  # ISO/IEC 7810 ID-1 (Saudi Iqama, driving licence, bank card)
EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def main() -> int:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(f"folder: {folder.resolve()}")
    print(
        f"{'file':38s} {'pixels':>11s} {'dpi':>7s} {'mm @ dpi':>16s} "
        f"{'dpi for card':>13s} {'aspect':>7s}"
    )
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        try:
            with Image.open(path) as image:
                width, height = image.size
                dpi_x, _ = read_image_dpi(image)
        except Exception as exc:  # pragma: no cover
            print(f"{path.name:38s} ERROR {exc}")
            continue
        dpi = dpi_x or 300.0
        mm_w = width / dpi * 25.4
        mm_h = height / dpi * 25.4
        needed = width / (CARD_MM[0] / 25.4)
        print(
            f"{path.name:38s} {width:5d}x{height:<5d} {dpi:7.0f} "
            f"{mm_w:7.1f}x{mm_h:<7.1f} {needed:13.1f} {width / height:7.3f}"
            f"   {'CARD OK' if abs(mm_w - 85.6) < 2 else ''}"
        )
    print(
        f"\nID-1 card = {CARD_MM[0]} x {CARD_MM[1]} mm "
        f"(aspect {CARD_MM[0] / CARD_MM[1]:.3f}); at 300 dpi that is "
        f"{round(CARD_MM[0] / 25.4 * 300)} x {round(CARD_MM[1] / 25.4 * 300)} px"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
