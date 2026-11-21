"""Report pixel size, DPI and physical size of an image file (or a name search).

Usage:
    py -3.14 tools\\inspect_image.py <path-or-filename>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from core.image_service import read_image_dpi  # noqa: E402

SEARCH_ROOTS = [
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Desktop",
    Path.home() / "Pictures",
    Path.home() / "OneDrive",
]

ID1_WIDTH_MM = 85.6
ID1_HEIGHT_MM = 54.0


def find(name: str) -> list[Path]:
    direct = Path(name)
    if direct.is_file():
        return [direct]
    target = direct.name.lower()
    found: list[Path] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.name.lower() == target:
                    found.append(path)
            except OSError:
                continue
    return found


def report(path: Path) -> None:
    with Image.open(path) as image:
        width, height = image.size
        fmt = image.format
        dpi_x, dpi_y = read_image_dpi(image)
    print(f"file          : {path}")
    print(f"format        : {fmt}")
    print(f"pixels        : {width} x {height}  (aspect {width / height:.4f})")
    size_bytes = path.stat().st_size
    print(f"file size     : {size_bytes / 1024:.1f} KB")
    if dpi_x and dpi_y:
        print(f"DPI in file   : {dpi_x:.2f} x {dpi_y:.2f}")
    else:
        print("DPI in file   : none (falls back to the configured DPI)")
    for assumed in (300.0, dpi_x or 300.0):
        mm_w = width / assumed * 25.4
        mm_h = height / assumed * 25.4
        label = "measured" if assumed == dpi_x else "assumed 300 dpi"
        print(
            f"size @ {assumed:5.1f} dpi: {mm_w:7.2f} x {mm_h:7.2f} mm  ({label})"
            f"   ratio to ID-1 card: {mm_w / ID1_WIDTH_MM:.2f} x {mm_h / ID1_HEIGHT_MM:.2f}"
        )
    print(f"ID-1 card target: {ID1_WIDTH_MM} x {ID1_HEIGHT_MM} mm at 300 dpi = "
          f"{round(ID1_WIDTH_MM / 25.4 * 300)} x {round(ID1_HEIGHT_MM / 25.4 * 300)} px")
    dx = abs(width - round(ID1_WIDTH_MM / 25.4 * (dpi_x or 300.0)))
    dy = abs(height - round(ID1_HEIGHT_MM / 25.4 * (dpi_x or 300.0)))
    print(f"pixel difference to a real card: {dx} px wide, {dy} px high")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: py -3.14 tools\\inspect_image.py <path-or-filename>")
        return 2
    matches = find(sys.argv[1])
    print(f"matches: {len(matches)}")
    for path in matches[:5]:
        report(path)
        print("-" * 70)
    return 0 if matches else 1


if __name__ == "__main__":
    sys.exit(main())
