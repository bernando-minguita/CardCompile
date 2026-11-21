"""Create resources/styles/app.qss from the theme module (single source of truth)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.theme import STYLE_SHEET  # noqa: E402

target = ROOT / "resources" / "styles" / "app.qss"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(STYLE_SHEET, encoding="utf-8")
print(f"Wrote {target} ({len(STYLE_SHEET)} characters)")
