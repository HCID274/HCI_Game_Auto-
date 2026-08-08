from unittest.mock import Mock, patch

import pytest
from wuwa_auto.okww.recovery_worker import (
    IN_PLACE_REVIVAL_COMPLETED_MARKER,
    REALM_DEFEAT_RETRY_COMPLETED_MARKER,
    RECOVERY_COMPLETED_MARKER,
    _active_challenge_visible,
    _recover_detected_death_state,
    _require_recovery_completion,
    _retry_realm_defeat,
)


def test_visible_realm_defeat_overrides_normal_death_log_classification() -> None:
    task = Mock()
    task.wait_until.return_value = True
    task.wait_click_feature.return_value = True
    with patch(
        "wuwa_auto.okww.recovery_worker.realm_defeat_visible",
        return_value=True,
    ), patch(
        "wuwa_auto.okww.recovery_worker.click_realm_defeat_retry"
    ) as click_retry:
        marker = _recover_detected_death_state(task)

    assert marker == REALM_DEFEAT_RETRY_COMPLETED_MARKER
    click_retry.assert_called_once_with(task)
    task.wait_click_skip_dialog_confirm.assert_called_once_with()
    task.revive_at_tower_and_heal.assert_not_called()
    task.log_info.assert_called_once_with(REALM_DEFEAT_RETRY_COMPLETED_MARKER)


def test_visible_character_revive_dialog_resumes_the_same_fight() -> None:
    task = Mock()
    task.wait_feature.return_value = object()
    task.wait_until.return_value = True
    with patch(
        "wuwa_auto.okww.recovery_worker.realm_defeat_visible",
        return_value=False,
    ), patch(
        "wuwa_auto.okww.recovery_worker.click_revive_confirm"
    ) as click_confirm:
        marker = _recover_detected_death_state(task)

    assert marker == IN_PLACE_REVIVAL_COMPLETED_MARKER
    click_confirm.assert_called_once_with(task)
    task.revive_action.assert_not_called()


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


def test_explicit_realm_defeat_mode_uses_same_retry_state_machine() -> None:
    task = Mock()
    task.wait_click_feature.return_value = False
    task.wait_until.return_value = True
    with patch(
        "wuwa_auto.okww.recovery_worker.click_realm_defeat_retry"
    ) as click_retry:
        marker = _retry_realm_defeat(task)

    assert marker == REALM_DEFEAT_RETRY_COMPLETED_MARKER
    click_retry.assert_called_once_with(task)
    task.revive_at_tower_and_heal.assert_not_called()
