"""Rewrite the DPI of card crops so they print at their real physical size.

Nothing is resampled - only the resolution tag changes, so the pixels (and the
quality) stay exactly as they are. By default new copies are written next to the
source files, so the originals are never touched.

Usage:
    py -3.14 tools\\set_card_size.py <folder-or-file> --preset card
    py -3.14 tools\\set_card_size.py <folder> --preset passport
    py -3.14 tools\\set_card_size.py <file> --target 85.6x54 --inplace
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from core.image_service import read_image_dpi  # noqa: E402

PRESETS = {
    # name: (width_mm, height_mm, matches the file name?)
    "card": (85.6, 54.0, ("iqama", "license", "istimara", "registration", "vehicle")),
    "passport": (88.0, 125.0, ("passport",)),
    "photo": (40.0, 60.0, ("photo", "mugshot")),
}
EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def target_for(name: str, preset: str, explicit: tuple[float, float] | None):
    if explicit:
        return explicit
    if preset != "auto":
        return PRESETS[preset][:2]
    low = name.lower()
    for key, (width, height, needles) in PRESETS.items():
        if any(needle in low for needle in needles):
            return width, height
    return PRESETS["card"][:2]


def process(path: Path, target: tuple[float, float], inplace: bool, quality: int) -> str:
    with Image.open(path) as image:
        width, height = image.size
        old_dpi = read_image_dpi(image)[0] or 300.0
        target_w, target_h = target
        # Match the target height as well, but never distort: use the width scale.
        dpi = width / (target_w / 25.4)
        dpi_h = height / (target_h / 25.4)
        dpi = round((dpi + dpi_h) / 2.0, 2)
        new_w_mm = width / dpi * 25.4
        new_h_mm = height / dpi * 25.4
        image.load()
        out_path = path
        if not inplace:
            out_path = path.with_name(f"{path.stem}_print{path.suffix}")
        save_kwargs = {"dpi": (dpi, dpi)}
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            save_kwargs.update(quality=quality, subsampling=0)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
        image.save(out_path, **save_kwargs)
    return (
        f"{path.name}: {width}x{height} px  dpi {old_dpi:.0f} -> {dpi:.2f}  "
        f"prints {width / old_dpi * 25.4:.1f}x{height / old_dpi * 25.4:.1f} mm "
        f"-> {new_w_mm:.1f}x{new_h_mm:.1f} mm  => {out_path.name}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--preset", default="card", choices=[*PRESETS, "auto"])
    parser.add_argument("--target", help="explicit size in mm, e.g. 85.6x54")
    parser.add_argument("--inplace", action="store_true", help="overwrite the originals")
    parser.add_argument("--quality", type=int, default=95)
    args = parser.parse_args()

    explicit = None
    if args.target:
        w, h = args.target.lower().split("x")
        explicit = (float(w), float(h))

    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(
                p for p in sorted(path.iterdir())
                if p.is_file() and p.suffix.lower() in EXTS
            )
        elif path.is_file():
            files.append(path)
        else:
            print(f"missing: {path}")

    for path in files:
        if path.stem.endswith("_print"):
            continue
        target = target_for(path.name, args.preset, explicit)
        print(process(path, target, args.inplace, args.quality))
    return 0


if __name__ == "__main__":
    sys.exit(main())
