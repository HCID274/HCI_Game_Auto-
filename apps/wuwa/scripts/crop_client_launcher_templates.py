"""Crop reviewed Wuthering Waves launcher states into reusable templates.

The source image must be a real launcher-window screenshot.  Coordinates are
kept here deliberately so every tracked template can be reproduced and
audited instead of being an unexplained hand-edited bitmap.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


# Coordinates refer to the 2560x1440 physical desktop capture collected on
# 2026-08-07.  The launcher itself is 1634x980 at Windows 125% scaling.
# The first crop proves the exact "进入游戏" state.  The second crop contains
# only the stable left side of the primary action button, allowing an update,
# continue, install, or launch label to change without losing the button.
CROPS = {
    "client_launcher_ready.png": (1704, 1018, 2030, 1090),
    "client_launcher_primary_anchor.png": (1704, 1018, 1795, 1090),
}


def crop_templates(source: Path, output_dir: Path) -> list[Path]:
    with Image.open(source) as image:
        if image.size != (2560, 1440):
            raise ValueError(
                f"unexpected launcher capture size {image.size}; expected (2560, 1440)"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, box in CROPS.items():
            path = output_dir / name
            image.crop(box).save(path)
            paths.append(path)
        return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "templates",
    )
    args = parser.parse_args()
    for path in crop_templates(args.source, args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
