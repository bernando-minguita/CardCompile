"""Generate the application icon (resources/icons/app.ico) with Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "resources" / "icons" / "app.ico"
BLUE = (37, 99, 235)
DARK_BLUE = (29, 78, 216)
WHITE = (255, 255, 255)
LIGHT = (232, 240, 254)


def draw_icon(size: int = 256) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(size * 0.22)
    draw.rounded_rectangle(
        [(0, 0), (size - 1, size - 1)], radius=radius, fill=BLUE
    )
    # document
    margin = int(size * 0.20)
    page = [
        (margin, margin - int(size * 0.04)),
        (size - margin, margin + int(size * 0.08)),
    ]
    draw.rounded_rectangle(
        [page[0], page[1]], radius=int(size * 0.05), fill=WHITE
    )
    # text lines
    line_x0 = margin + int(size * 0.06)
    line_x1 = size - margin - int(size * 0.06)
    for index in range(4):
        top = margin + int(size * 0.08) + index * int(size * 0.10)
        draw.rounded_rectangle(
            [(line_x0, top), (line_x1, top + int(size * 0.035))],
            radius=int(size * 0.02),
            fill=LIGHT,
        )
    # accent corner (PDF badge)
    badge = int(size * 0.30)
    x0 = size - badge - int(size * 0.06)
    y0 = size - badge - int(size * 0.10)
    draw.rounded_rectangle(
        [(x0, y0), (x0 + badge, y0 + badge)],
        radius=int(size * 0.06),
        fill=DARK_BLUE,
        outline=WHITE,
        width=max(1, int(size * 0.015)),
    )
    return image


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    master = draw_icon(256)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(TARGET, format="ICO", sizes=sizes)
    master.save(TARGET.with_suffix(".png"), format="PNG")
    print(f"Wrote {TARGET} ({TARGET.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
