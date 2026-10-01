"""Unit tests for HoYo launcher update recovery and location-matched launcher PIDs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starrail_auto.m7a import environment as env
from starrail_auto.m7a import launcher_update as lu


class _FakeProcess:
    def __init__(self, name: str, exe: str, pid: int) -> None:
        self.info = {"name": name, "exe": exe}
        self.pid = pid


class _FakeClock:
    """Manual monotonic clock; the test sets ``now`` between calls."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _sequence(*results: object):
    """Return a locator that yields each result once, then repeats the last."""
    state = {"index": 0}

    def locate(_region: object) -> object:
        result = results[min(state["index"], len(results) - 1)]
        state["index"] += 1
        return result

    return locate


class LauncherRecoveryClickGateTests(unittest.TestCase):
    """recover_launcher_download_failure clicks once per failure episode."""

    ACCEPTED_MATCH = SimpleNamespace(x=800, y=700)
    REJECTED_MATCH = SimpleNamespace(x=200, y=300)

    def setUp(self) -> None:
        self.template = Path(tempfile.gettempdir()) / "hyp_download_failed.png"
        self.template.write_bytes(b"x")
        self.clicks: list[tuple[int, int]] = []
        self.clock = _FakeClock()
        lu.DOWNLOAD_FAILED_TEMPLATE = self.template
        lu._retry_clicked = False
        lu._absent_since = None
        lu._hyp_window = lambda: (1, 0, 0, 1000, 800)
        lu._save_evidence = lambda name, region: Path(tempfile.gettempdir()) / f"{name}.png"
        lu.activate_window = lambda hwnd, timeout: None
        self.monotonic_patch = patch.object(lu.time, "monotonic", self.clock)
        self.monotonic_patch.start()
        self.sleep_patch = patch.object(lu.time, "sleep")
        self.sleep_patch.start()
        self.center_patch = patch.object(
            lu.pyautogui,
            "center",
            lambda match: SimpleNamespace(x=match.x, y=match.y),
        )
        self.center_patch.start()
        self.click_patch = patch.object(
            lu.pyautogui,
            "click",
            side_effect=lambda x, y: self.clicks.append((x, y)),
        )
        self.click_patch.start()

    def tearDown(self) -> None:
        self.click_patch.stop()
        self.center_patch.stop()
        self.sleep_patch.stop()
        self.monotonic_patch.stop()
        self.template.unlink(missing_ok=True)

    def test_persistent_failure_is_clicked_once_only(self) -> None:
        # First call: present -> one click -> template disappears during the poll.
        lu._locate_failure = _sequence(self.ACCEPTED_MATCH, None)
        self.assertTrue(lu.recover_launcher_download_failure())
        self.assertEqual(len(self.clicks), 1)
        # The same failure is still on screen: no second click on later calls.
        lu._locate_failure = lambda _region: self.ACCEPTED_MATCH
        self.assertFalse(lu.recover_launcher_download_failure())
        self.assertFalse(lu.recover_launcher_download_failure())
        self.assertEqual(len(self.clicks), 1)

    def test_detection_flicker_does_not_allow_a_second_click(self) -> None:
        lu._locate_failure = _sequence(self.ACCEPTED_MATCH, None)
        self.assertTrue(lu.recover_launcher_download_failure())
        self.assertEqual(len(self.clicks), 1)
        # One momentary miss (overlay/move) then the same dialog reappears.
        self.clock.now = 5.0
        lu._locate_failure = lambda _region: None
        self.assertFalse(lu.recover_launcher_download_failure())
        self.clock.now = 6.0
        lu._locate_failure = lambda _region: self.ACCEPTED_MATCH
        self.assertFalse(lu.recover_launcher_download_failure())
        self.assertFalse(lu.recover_launcher_download_failure())
        self.assertEqual(len(self.clicks), 1)

    def test_guard_resets_only_after_continuous_absence(self) -> None:
        lu._locate_failure = _sequence(self.ACCEPTED_MATCH, None)
        self.assertTrue(lu.recover_launcher_download_failure())
        self.assertEqual(len(self.clicks), 1)
        # Short absence does not reset the guard.
        self.clock.now = 0.0
        lu._locate_failure = lambda _region: None
        self.assertFalse(lu.recover_launcher_download_failure())
        self.clock.now = 10.0
        self.assertFalse(lu.recover_launcher_download_failure())
        self.assertTrue(lu._retry_clicked)
        # Absence lasting >= RETRY_RESET_ABSENCE_SECONDS resets it.
        self.clock.now = 31.0
        self.assertFalse(lu.recover_launcher_download_failure())
        self.assertFalse(lu._retry_clicked)
        # A later fresh failure is clickable again (new episode).
        lu._locate_failure = _sequence(self.ACCEPTED_MATCH, None)
        self.assertTrue(lu.recover_launcher_download_failure())
        self.assertEqual(len(self.clicks), 2)

    def test_out_of_area_match_is_rejected_without_consuming_the_budget(self) -> None:
        lu._locate_failure = lambda _region: self.REJECTED_MATCH
        self.assertFalse(lu.recover_launcher_download_failure())
        self.assertFalse(lu._retry_clicked)
        self.assertEqual(len(self.clicks), 0)


class LauncherProcessIdMatchTests(unittest.TestCase):
    """game_launcher_process_ids keeps only the configured HoYo launcher PIDs."""

    def test_location_matching_keeps_only_configured_launcher_pids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher_dir = root / "StarRail" / "Auto"
            launcher_dir.mkdir(parents=True)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"launcher_path: {launcher_dir / 'HYP.exe'}\n",
                encoding="utf-8-sig",
            )
            procs = [
                _FakeProcess("hyp.exe", str(launcher_dir / "HYP.exe"), 101),
                _FakeProcess("hyp.exe", r"C:\OtherGame\HYP.exe", 202),
                _FakeProcess("starrail.exe", r"C:\Games\starrail.exe", 303),
            ]
            with patch.object(env, "M7A_CONFIG_PATH", config_path), patch.object(
                env.psutil, "process_iter", return_value=procs
            ):
                self.assertEqual(env.game_launcher_process_ids(), {101})
                self.assertTrue(env.is_game_launcher_running())

    def test_hyp_binary_outside_launcher_root_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"launcher_path: {root / 'HYP.exe'}\n",
                encoding="utf-8-sig",
            )
            procs = [_FakeProcess("hyp.exe", r"C:\Outside\HYP.exe", 404)]
            with patch.object(env, "M7A_CONFIG_PATH", config_path), patch.object(
                env.psutil, "process_iter", return_value=procs
            ):
                self.assertEqual(env.game_launcher_process_ids(), set())
                self.assertFalse(env.is_game_launcher_running())

    def test_missing_config_yields_no_launcher_pids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            procs = [_FakeProcess("hyp.exe", r"C:\Any\HYP.exe", 505)]
            with patch.object(env, "M7A_CONFIG_PATH", root / "missing.yaml"), patch.object(
                env.psutil, "process_iter", return_value=procs
            ):
                self.assertEqual(env.game_launcher_process_ids(), set())
                self.assertFalse(env.is_game_launcher_running())


if __name__ == "__main__":
    unittest.main()
