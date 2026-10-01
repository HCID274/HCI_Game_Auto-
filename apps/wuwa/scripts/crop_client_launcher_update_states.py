"""Extract exact launcher update-button states from physical desktop captures."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


EXPECTED_SIZE = (2560, 1440)
ACTION_BOX = (1704, 1018, 2030, 1090)


def _crop(source: Path, output: Path) -> None:
    with Image.open(source) as image:
        if image.size != EXPECTED_SIZE:
            raise ValueError(
                f"unexpected launcher capture size {image.size}; "
                f"expected {EXPECTED_SIZE}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        image.crop(ACTION_BOX).save(output)
        print(f"saved {output} box={ACTION_BOX}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", type=Path, required=True)
    parser.add_argument("--downloading", type=Path, required=True)
    parser.add_argument("--paused", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    _crop(args.update, args.output_dir / "client_launcher_update_action.png")
    _crop(args.downloading, args.output_dir / "client_launcher_downloading.png")
    _crop(args.paused, args.output_dir / "client_launcher_download_paused.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
