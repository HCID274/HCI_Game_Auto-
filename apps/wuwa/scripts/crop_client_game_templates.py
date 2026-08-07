"""Crop reviewed login and in-world states into reproducible templates."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


EXPECTED_SIZE = (2560, 1440)
LOGIN_CROP = (1030, 1272, 1525, 1402)
MONTHLY_REWARD_CROP = (1010, 1240, 1550, 1320)
REWARD_RESULT_CROP = (1060, 1230, 1500, 1325)
NETWORK_RETRY_CROP = (1285, 770, 1590, 855)


def crop(source: Path, box: tuple[int, int, int, int], output: Path) -> Path:
    with Image.open(source) as image:
        if image.size != EXPECTED_SIZE:
            raise ValueError(
                f"unexpected game capture size {image.size}; expected {EXPECTED_SIZE}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        image.crop(box).save(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login-source", type=Path, required=True)
    parser.add_argument("--monthly-reward-source", type=Path, required=True)
    parser.add_argument("--reward-result-source", type=Path, required=True)
    parser.add_argument("--network-retry-source", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "templates",
    )
    args = parser.parse_args()
    outputs = (
        crop(
            args.login_source,
            LOGIN_CROP,
            args.output_dir / "client_login_connect.png",
        ),
        crop(
            args.monthly_reward_source,
            MONTHLY_REWARD_CROP,
            args.output_dir / "client_monthly_reward.png",
        ),
        crop(
            args.reward_result_source,
            REWARD_RESULT_CROP,
            args.output_dir / "client_reward_result_close.png",
        ),
        crop(
            args.network_retry_source,
            NETWORK_RETRY_CROP,
            args.output_dir / "client_network_retry.png",
        ),
    )
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
