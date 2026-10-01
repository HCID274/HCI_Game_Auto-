"""Extract audited launcher self-update templates from a window capture."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

EXPECTED_SIZE = (1600, 950)
CROPS = {
    "client_launcher_selfupdate_notice.png": (500, 280, 700, 335),
    "client_launcher_selfupdate_confirm.png": (867, 607, 1093, 659),
}
DIALOG_FIXTURE_CROP = (450, 240, 1150, 710)
AGREEMENT_CROPS = {
    "client_agreement_notice.png": (1165, 550, 1395, 596),
    "client_agreement_confirm.png": (1400, 833, 1488, 875),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fixture-output", type=Path)
    parser.add_argument("--agreement", action="store_true", help="从归档截图提取客户端协议更新弹窗")
    args = parser.parse_args()

    expected_size = (2560, 1440) if args.agreement else EXPECTED_SIZE
    crops = AGREEMENT_CROPS if args.agreement else CROPS
    with Image.open(args.source) as image:
        if image.size != expected_size:
            raise ValueError(
                f"unexpected self-update capture size {image.size}; "
                f"expected {expected_size}"
            )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, box in crops.items():
            output = args.output_dir / name
            image.crop(box).save(output)
            print(f"saved {output} box={box}")
        if args.fixture_output is not None:
            if args.agreement:
                raise ValueError("协议截图直接引用原始运行证据，不生成测试夹具")
            args.fixture_output.parent.mkdir(parents=True, exist_ok=True)
            image.crop(DIALOG_FIXTURE_CROP).save(args.fixture_output)
            print(f"saved {args.fixture_output} box={DIALOG_FIXTURE_CROP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
