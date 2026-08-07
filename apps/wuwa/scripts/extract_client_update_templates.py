"""Extract stable client hot-update templates from a preserved 2560x1440 screenshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


EXPECTED_SIZE = (2560, 1440)
CROPS = {
    "client_update_restart_notice.png": (1040, 680, 1600, 755),
    "client_update_restart_confirm.png": (1430, 850, 1925, 955),
}
DIALOG_CROP = (400, 320, 2000, 1120)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--fixture-output", type=Path)
    args = parser.parse_args()

    screenshot = Image.open(args.source).convert("RGB")
    if screenshot.size != EXPECTED_SIZE:
        raise SystemExit(
            f"expected screenshot size {EXPECTED_SIZE}, got {screenshot.size}"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, box in CROPS.items():
        output = args.output_dir / name
        screenshot.crop(box).save(output)
        print(f"saved {output} box={box}")
    if args.fixture_output is not None:
        args.fixture_output.parent.mkdir(parents=True, exist_ok=True)
        screenshot.crop(DIALOG_CROP).save(args.fixture_output)
        print(f"saved {args.fixture_output} box={DIALOG_CROP}")


if __name__ == "__main__":
    main()
