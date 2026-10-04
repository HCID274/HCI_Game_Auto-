import pytest

from wuwa_auto.okww.daily_activity import DailyActivityVerificationError
from wuwa_auto.okww.tacet_skip import (
    ACTIVITY_SHORT_AFTER_TACET_SKIP_MARKER,
    TACET_UNREACHABLE_MARKER,
    install_tacet_unreachable_skip,
)


class WaitFailed(Exception):
    pass


def _classes(*, banner: bool, tacet_fails: bool = True):
    calls: list[str] = []

    class Task:
        config = {"Which Tacet Suppression to Farm": 2}

        def wait_ocr(self, *_: object, **__: object) -> object:
            calls.append("ocr")
            return object() if banner else None

        def ensure_main(self, **_: object) -> None:
            calls.append("ensure_main")

        def log_info(self, message: str) -> None:
            calls.append(message)

    class TacetTask(Task):
        def farm_tacet(self, daily: bool = False, used_stamina: int = 0, config=None):
            calls.append("farm_tacet")
            if tacet_fails:
                raise WaitFailed()
            return None

    class DailyTask(Task):
        def claim_daily(self):
            calls.append("claim_daily")
            raise DailyActivityVerificationError("points=40, target=100")

    install_tacet_unreachable_skip(DailyTask, TacetTask)
    return DailyTask(), TacetTask(), calls


def test_greyed_travel_skips_tacet_and_keeps_the_daily_going() -> None:
    daily, tacet, calls = _classes(banner=True)

    assert tacet.farm_tacet(daily=True, config={"Which Tacet Suppression to Farm": 2}) is None
    assert daily.claim_daily() is None

    assert f"{TACET_UNREACHABLE_MARKER} index=2" in calls
    assert "ensure_main" in calls
    assert any(str(line).startswith(ACTIVITY_SHORT_AFTER_TACET_SKIP_MARKER) for line in calls)


def test_other_tacet_failures_still_stop_the_daily() -> None:
    daily, tacet, calls = _classes(banner=False)

    with pytest.raises(WaitFailed):
        tacet.farm_tacet(daily=True)
    with pytest.raises(DailyActivityVerificationError):
        daily.claim_daily()
    assert not any(TACET_UNREACHABLE_MARKER in str(line) for line in calls)


def test_short_activity_without_a_skip_is_still_an_error() -> None:
    daily, tacet, calls = _classes(banner=True, tacet_fails=False)

    tacet.farm_tacet(daily=True)
    with pytest.raises(DailyActivityVerificationError):
        daily.claim_daily()
    assert "ocr" not in calls
