"""Regression tests for task-scoped OK-WW configuration preflight."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from wuwa_auto.okww import config
from wuwa_auto.okww.runner import (
    preflight_farm_echo_task,
    preflight_weekly_garden_task,
    run_farm_echo_task,
    run_weekly_garden_task,
)


FARM_ECHO_CONFIG = {
    "Teleport to Boss": "Boss Challenge",
    "Which Boss Challenge to Teleport": 2,
    "Boss Level": "80",
    "Boss": "Other",
    "Repeat Farm Count": 5,
}


class OkConfigurationTests(unittest.TestCase):
    def test_farm_echo_preflight_reads_only_farm_echo_configuration(self) -> None:
        with patch.object(config, "_validate_common_paths"), patch.object(
            config,
            "_load_json",
            return_value=FARM_ECHO_CONFIG,
        ) as load_json:
            facts = config.validate_farm_echo_configuration()

        load_json.assert_called_once_with(config.OK_FARM_ECHO_CONFIG)
        self.assertEqual(facts["boss_challenge_index"], 2)
        self.assertEqual(facts["repeat_farm_count"], 5)

    def test_temporary_repeat_count_restores_original_bytes(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as folder:
            path = Path(folder) / "FarmEchoTask.json"
            original = (json.dumps(FARM_ECHO_CONFIG, ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
            path.write_bytes(original)
            with patch.object(config, "OK_FARM_ECHO_CONFIG", path):
                with config.temporary_farm_echo_repeat_count(2):
                    current = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(current["Repeat Farm Count"], 2)
                self.assertEqual(path.read_bytes(), original)

    def test_temporary_repeat_count_restores_original_bytes_after_error(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as folder:
            path = Path(folder) / "FarmEchoTask.json"
            original = (json.dumps(FARM_ECHO_CONFIG, ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
            path.write_bytes(original)
            with patch.object(config, "OK_FARM_ECHO_CONFIG", path):
                with self.assertRaisesRegex(RuntimeError, "simulated retry failure"):
                    with config.temporary_farm_echo_repeat_count(2):
                        raise RuntimeError("simulated retry failure")
                self.assertEqual(path.read_bytes(), original)

    def test_temporary_repeat_count_allows_bounded_combat_passes(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as folder:
            path = Path(folder) / "FarmEchoTask.json"
            original = (json.dumps(FARM_ECHO_CONFIG, ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
            path.write_bytes(original)
            with patch.object(config, "OK_FARM_ECHO_CONFIG", path):
                with config.temporary_farm_echo_repeat_count(60):
                    current = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(current["Repeat Farm Count"], 60)
                self.assertEqual(path.read_bytes(), original)

    def test_confirmed_retry_attempt_limit_allows_detector_reentry(self) -> None:
        self.assertEqual(config.confirmed_retry_attempt_limit(1), 12)
        self.assertEqual(config.confirmed_retry_attempt_limit(5), 60)

    def test_weekly_preflight_reads_only_garden_configuration(self) -> None:
        with patch.object(config, "_validate_common_paths"), patch.object(
            config,
            "_load_json",
            return_value={},
        ) as load_json:
            facts = config.validate_weekly_garden_configuration()

        load_json.assert_called_once_with(config.OK_GARDEN_CONFIG)
        self.assertEqual(facts, {"garden_config": {}})

    def test_daily_preflight_keeps_required_farm_echo_policy(self) -> None:
        daily = {
            "Which to Farm": "Tacet Suppression",
            "Which Tacet Suppression to Farm": 6,
            "Additional Tasks to Run After Daily Task": [
                config.TELEPORT_AND_FARM_4C,
            ],
        }

        def load_json(path: object) -> dict[str, object]:
            if path == config.OK_DAILY_CONFIG:
                return daily
            if path == config.OK_FARM_ECHO_CONFIG:
                return FARM_ECHO_CONFIG
            raise AssertionError(f"unexpected configuration read: {path}")

        with patch.object(config, "_validate_common_paths"), patch.object(
            config,
            "_load_json",
            side_effect=load_json,
        ):
            facts = config.validate_daily_configuration()

        self.assertEqual(facts["daily_farm_index"], 6)
        self.assertEqual(facts["repeat_farm_count"], 5)


class OkRunnerPreflightTests(unittest.TestCase):
    def test_farm_echo_runner_uses_farm_echo_preflight(self) -> None:
        with patch("wuwa_auto.okww.runner._run_task") as run_task:
            run_farm_echo_task()

        preflight = run_task.call_args.kwargs["preflight"]
        with patch(
            "wuwa_auto.okww.runner.preflight_farm_echo_task",
            return_value={"repeat_farm_count": 5},
        ) as farm_preflight:
            self.assertEqual(preflight(), {"repeat_farm_count": 5})
        farm_preflight.assert_called_once_with(5)

    def test_weekly_runner_uses_weekly_preflight(self) -> None:
        with patch("wuwa_auto.okww.runner._run_task") as run_task:
            run_weekly_garden_task()

        self.assertIs(
            run_task.call_args.kwargs["preflight"],
            preflight_weekly_garden_task,
        )


if __name__ == "__main__":
    unittest.main()
