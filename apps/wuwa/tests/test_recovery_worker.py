from unittest.mock import Mock, patch

import pytest
from wuwa_auto.okww.recovery_worker import (
    PARTY_MEMBER_HEAL_RECOVERY_COMPLETED_MARKER,
    REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER,
    RECOVERY_COMPLETED_MARKER,
    REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED_MARKER,
    VirtualHidRecoveryMixin,
    _active_challenge_visible,
    _heal_after_realm_defeat,
    _heal_after_party_member_unavailable,
    _recover_detected_death_state,
    _recovery_result_payload,
    _require_recovery_completion,
)


class _OcrBox:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def test_relative_recovery_click_is_converted_then_sent_through_hid() -> None:
    class Capture:
        @staticmethod
        def get_abs_cords(x: int, y: int) -> tuple[int, int]:
            return x + 10, y + 20

    class BaseTask:
        width = 2560
        height = 1440

        def __init__(self) -> None:
            self.executor = Mock()
            self.executor.interaction.capture = Capture()
            self.post_message_calls: list[tuple[object, object]] = []

        def check_interval(self, _interval: float) -> bool:
            return True

        def click_relative(self, x: float, y: float, **kwargs: object) -> object:
            return self.click(int(self.width * x), int(self.height * y), **kwargs)

        def click(self, x: object, y: object, **_kwargs: object) -> object:
            self.post_message_calls.append((x, y))
            return False

        def log_info(self, _message: str) -> None:
            return None

        def sleep(self, _seconds: float) -> None:
            return None

    class Task(VirtualHidRecoveryMixin, BaseTask):
        pass

    task = Task()
    with patch(
        "wuwa_auto.okww.recovery_worker._virtual_hid_click"
    ) as hid_click:
        result = task.click(0.50, 0.50, name="relative")

    assert result is True
    hid_click.assert_called_once_with(
        1290,
        740,
        button="left",
        hold=0.08,
        log_action=True,
    )
    assert task.post_message_calls == []


def test_recovery_detect_action_uses_ocr_center_instead_of_stale_point() -> None:
    class Capture:
        @staticmethod
        def get_abs_cords(x: int, y: int) -> tuple[int, int]:
            return x + 10, y + 20

    class BaseTask:
        width = 2560
        height = 1440

        def __init__(self) -> None:
            self.executor = Mock()
            self.executor.interaction.capture = Capture()
            self.messages: list[str] = []

        def wait_ocr(self, *_args: object, **_kwargs: object) -> list[_OcrBox]:
            return [_OcrBox(2000, 1230, 300, 80)]

        def check_interval(self, _interval: float) -> bool:
            return True

        def click_relative(self, x: float, y: float, **kwargs: object) -> object:
            return self.click(int(self.width * x), int(self.height * y), **kwargs)

        def click(self, _x: object, _y: object, **_kwargs: object) -> object:
            raise AssertionError("recovery click must not use PostMessage")

        def log_info(self, message: str) -> None:
            self.messages.append(message)

        def sleep(self, _seconds: float) -> None:
            return None

    class Task(VirtualHidRecoveryMixin, BaseTask):
        pass

    task = Task()
    with patch(
        "wuwa_auto.okww.recovery_worker._virtual_hid_click"
    ) as hid_click:
        result = task.click(0.89, 0.92, after_sleep=1)

    assert result is True
    hid_click.assert_called_once_with(
        2160,
        1290,
        button="left",
        hold=0.08,
        log_action=True,
    )
    assert any("DETECT_ACTION_OCR 2150,1270" in message for message in task.messages)


def test_visible_realm_defeat_overrides_normal_death_log_classification() -> None:
    task = Mock()
    task.wait_in_team_and_world.return_value = True
    with patch(
        "wuwa_auto.okww.recovery_worker.realm_defeat_visible",
        return_value=True,
    ), patch(
        "wuwa_auto.okww.recovery_worker.click_realm_defeat_exit"
    ) as click_exit:
        marker = _recover_detected_death_state(task)

    assert marker == REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER
    click_exit.assert_called_once_with(task)
    task.revive_at_tower_and_heal.assert_called_once_with()
    task.log_info.assert_called_once_with(REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER)


def test_visible_character_revive_dialog_uses_waypoint_healing() -> None:
    task = Mock()
    task.wait_feature.return_value = object()
    task.revive_action.return_value = True
    with patch(
        "wuwa_auto.okww.recovery_worker.realm_defeat_visible",
        return_value=False,
    ):
        marker = _recover_detected_death_state(task)

    assert marker == REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED_MARKER
    task.revive_action.assert_called_once_with()


def test_realm_ui_without_combat_is_not_an_active_challenge() -> None:
    task = Mock()
    task.in_combat.return_value = False
    task.in_realm.return_value = True

    assert _active_challenge_visible(task) is False


def test_combat_signal_is_an_active_challenge() -> None:
    task = Mock()
    task.in_combat.side_effect = lambda **kwargs: kwargs.get("target") is True

    assert _active_challenge_visible(task) is True


def test_recovery_requires_host_completion_latch() -> None:
    assert _require_recovery_completion(RECOVERY_COMPLETED_MARKER) == (
        RECOVERY_COMPLETED_MARKER
    )
    with pytest.raises(RuntimeError, match="without a host completion marker"):
        _require_recovery_completion(None)


def test_success_result_always_reenters_from_configured_entry() -> None:
    payload = _recovery_result_payload(
        REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER,
        started_at="start",
        finished_at="finish",
    )

    assert payload["success"] is True
    assert payload["resume_active_realm"] is False
    assert payload["reason"] == REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER


def test_realm_defeat_exits_and_heals_before_next_entry() -> None:
    task = Mock()
    task.wait_in_team_and_world.return_value = True
    sequence: list[str] = []
    task.wait_in_team_and_world.side_effect = lambda **_kwargs: sequence.append(
        "wait"
    ) or True
    task.revive_at_tower_and_heal.side_effect = lambda: sequence.append("heal")
    with patch(
        "wuwa_auto.okww.recovery_worker.click_realm_defeat_exit",
        side_effect=lambda _task: sequence.append("exit"),
    ) as click_exit:
        marker = _heal_after_realm_defeat(task)

    assert marker == REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER
    click_exit.assert_called_once_with(task)
    task.revive_at_tower_and_heal.assert_called_once_with()
    assert sequence == ["exit", "wait", "heal", "wait"]


def test_unavailable_party_member_exits_active_realm_then_heals() -> None:
    task = Mock()
    task.wait_click_feature.return_value = object()
    task.wait_feature.return_value = None
    task.wait_ocr.return_value = None
    task.in_world.return_value = False
    task.in_combat.return_value = True
    task.wait_in_team_and_world.return_value = True

    marker = _heal_after_party_member_unavailable(task)

    assert marker == PARTY_MEMBER_HEAL_RECOVERY_COMPLETED_MARKER
    task.send_key.assert_called_once_with("esc", after_sleep=1)
    task.wait_click_feature.assert_called_once_with(
        "gray_confirm_exit_button",
        relative_x=-1,
        raise_if_not_found=False,
        time_out=5,
        click_after_delay=0.5,
        threshold=0.7,
        after_sleep=1,
    )
    task.revive_at_tower_and_heal.assert_called_once_with()
    assert task.wait_in_team_and_world.call_count == 2


class _NotInCombatException(Exception):
    """Stand-in for src.task.BaseCombatTask.NotInCombatException."""


class _CombatGuardedTask:
    """Replay the upstream BaseCombatTask sleep contract that killed the
    2026-08-18 party-member recovery: send_key(after_sleep) sleeps through
    sleep_check, which raises once the Esc menu hides the combat HUD."""

    def __init__(self) -> None:
        self.skip_combat_check = False
        self.combat_hud_visible = True
        self.esc_menu_open = False
        self.exit_clicked = False
        self.healed = False
        self.messages: list[str] = []

    def log_info(self, message: str) -> None:
        self.messages.append(message)

    def in_combat(self, *_args: object, **_kwargs: object) -> bool:
        return self.combat_hud_visible

    def wait_ocr(self, *_args: object, **_kwargs: object) -> None:
        return None

    def wait_feature(self, *_args: object, **_kwargs: object) -> None:
        return None

    def in_world(self) -> bool:
        return self.esc_menu_open

    def send_key(self, key: str, *, after_sleep: float = 0) -> None:
        if key == "esc":
            self.esc_menu_open = True
            self.combat_hud_visible = False
        if after_sleep > 0:
            self.sleep(after_sleep)

    def sleep(self, _seconds: float) -> None:
        # BaseCombatTask.sleep_check: the guard fires only while the task
        # believes it is in combat and the flag is not disabled.
        if self.skip_combat_check:
            return
        if self.combat_hud_visible is False:
            raise _NotInCombatException("sleep check not in combat")

    def wait_click_feature(self, name: str, **_kwargs: object) -> object:
        if name == "gray_confirm_exit_button":
            self.exit_clicked = True
        return object()

    def wait_in_team_and_world(self, **_kwargs: object) -> bool:
        return True

    def revive_at_tower_and_heal(self) -> None:
        self.healed = True


def test_party_member_recovery_survives_combat_guarded_esc_sleep() -> None:
    """Negative replay of the 0818 05:51 traceback (farm-echo-recovery-1.log)."""

    task = _CombatGuardedTask()

    marker = _heal_after_party_member_unavailable(task)

    assert marker == PARTY_MEMBER_HEAL_RECOVERY_COMPLETED_MARKER
    assert task.exit_clicked is True
    assert task.healed is True


def test_unavailable_party_member_extends_upstream_waypoint_loading_wait() -> None:
    task = Mock()
    task.wait_click_feature.return_value = object()
    task.wait_feature.return_value = None
    task.wait_ocr.return_value = None
    task.in_world.return_value = False
    task.in_combat.return_value = True
    observed_timeouts: list[float] = []
    task.wait_in_team_and_world.side_effect = lambda **kwargs: (
        observed_timeouts.append(kwargs["time_out"]) or True
    )
    task.revive_at_tower_and_heal.side_effect = lambda: (
        task.wait_in_team_and_world(time_out=20)
    )

    marker = _heal_after_party_member_unavailable(task)

    assert marker == PARTY_MEMBER_HEAL_RECOVERY_COMPLETED_MARKER
    assert observed_timeouts == [120, 120.0, 120]


def test_party_member_recovery_uses_live_realm_defeat_at_worker_start() -> None:
    """Replay the 8/14 state change already complete before worker action."""

    task = Mock()
    task.wait_in_team_and_world.return_value = True
    with patch(
        "wuwa_auto.okww.recovery_worker.realm_defeat_visible",
        return_value=True,
    ), patch(
        "wuwa_auto.okww.recovery_worker.click_realm_defeat_exit"
    ) as click_exit:
        marker = _heal_after_party_member_unavailable(task)

    assert marker == REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER
    click_exit.assert_called_once_with(task)
    task.send_key.assert_not_called()
    task.revive_at_tower_and_heal.assert_called_once_with()


def test_party_member_recovery_reclassifies_defeat_while_exit_probe_waits() -> None:
    """Replay 8/14: combat at trigger time, defeat UI after worker startup."""

    task = Mock()
    task.wait_feature.return_value = None
    task.in_world.return_value = False
    task.in_combat.return_value = True
    task.wait_click_feature.return_value = None
    task.wait_in_team_and_world.return_value = True
    with patch(
        "wuwa_auto.okww.recovery_worker.realm_defeat_visible",
        side_effect=[False, True],
    ), patch(
        "wuwa_auto.okww.recovery_worker.click_realm_defeat_exit"
    ) as click_exit:
        marker = _heal_after_party_member_unavailable(task)

    assert marker == REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER
    task.send_key.assert_called_once_with("esc", after_sleep=1)
    click_exit.assert_called_once_with(task)
    task.revive_at_tower_and_heal.assert_called_once_with()


def test_party_member_recovery_uses_live_revive_dialog_before_combat() -> None:
    task = Mock()
    task.wait_feature.return_value = object()
    task.revive_action.return_value = True
    with patch(
        "wuwa_auto.okww.recovery_worker.realm_defeat_visible",
        return_value=False,
    ):
        marker = _heal_after_party_member_unavailable(task)

    assert marker == REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED_MARKER
    task.revive_action.assert_called_once_with()
    task.send_key.assert_not_called()


def test_party_member_recovery_heals_if_transition_already_reached_world() -> None:
    task = Mock()
    task.wait_feature.return_value = None
    task.wait_ocr.return_value = None
    task.in_combat.return_value = False
    task.in_world.return_value = True
    task.wait_in_team_and_world.return_value = True

    marker = _heal_after_party_member_unavailable(task)

    assert marker == PARTY_MEMBER_HEAL_RECOVERY_COMPLETED_MARKER
    task.revive_at_tower_and_heal.assert_called_once_with()
    task.send_key.assert_not_called()


def test_party_member_recovery_refuses_unknown_live_state() -> None:
    task = Mock()
    task.wait_feature.return_value = None
    task.wait_ocr.return_value = None
    task.in_world.return_value = False
    task.in_combat.return_value = False

    with pytest.raises(RuntimeError, match="live UI is neither"):
        _heal_after_party_member_unavailable(task)

    task.send_key.assert_not_called()
