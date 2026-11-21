"""Report page size and embedded image resolution of a PDF (scanned cards)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

MM_PER_PT = 25.4 / 72.0
CARD_MM = (85.6, 54.0)


def main() -> int:
    path = Path(sys.argv[1])
    print(f"file: {path}")
    document = pymupdf.open(path)
    print(f"pages: {document.page_count}")
    for index in range(document.page_count):
        page = document[index]
        rect = page.rect
        print(
            f"\npage {index + 1}: {rect.width * MM_PER_PT:.1f} x "
            f"{rect.height * MM_PER_PT:.1f} mm  ({rect.width:.0f} x {rect.height:.0f} pt)"
        )
        for info in page.get_images(full=True):
            xref = info[0]
            width, height = info[2], info[3]
            placed_w = info[2]
            extracted = document.extract_image(xref)
            ext = extracted.get("ext", "?")
            # Effective DPI: image pixels spread over the page area it covers.
            blocks = page.get_image_rects(xref)
            area = blocks[0] if blocks else rect
            dpi_x = width / (area.width * MM_PER_PT / 25.4) if area.width else 0
            print(
                f"  image xref {xref}: {width} x {height} px  ({ext}), "
                f"placed {area.width * MM_PER_PT:.1f} x {area.height * MM_PER_PT:.1f} mm "
                f"-> {dpi_x:.0f} dpi on paper"
            )
            # If this image holds a card, how large could the card be printed at 300 dpi?
            card_px_at_300 = round(CARD_MM[0] / 25.4 * 300)
            fraction = width / card_px_at_300
            print(
                f"    card pixels available if the card fills the page width: "
                f"{width} px = {width / 3.3701:.0f} dpi across 85.6 mm "
                f"({fraction * 100:.0f}% of 300 dpi)"
            )
    document.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
