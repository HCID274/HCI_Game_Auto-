from game_automation_core.uu.update import recover_mandatory_update


def test_mandatory_update_waits_for_new_process_generation() -> None:
    clock = [0.0]
    pid_states = iter(
        [frozenset({10}), frozenset({10}), frozenset(), frozenset({20})]
    )
    focused: list[float] = []

    assert recover_mandatory_update(
        accept_update=lambda: True,
        update_visible=lambda _timeout: False,
        primary_pids=lambda: next(pid_states),
        start_process=lambda: None,
        focus_window=lambda timeout: focused.append(timeout) or "UU",
        timeout=10,
        relaunch_grace=3,
        poll_interval=1,
        monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    assert focused


def test_absent_process_is_relaunched_once() -> None:
    clock = [0.0]
    starts: list[None] = []
    states = [frozenset({10}), frozenset(), frozenset(), frozenset({20})]

    def pids() -> frozenset[int]:
        return states.pop(0) if states else frozenset({20})

    assert recover_mandatory_update(
        accept_update=lambda: True,
        update_visible=lambda _timeout: False,
        primary_pids=pids,
        start_process=lambda: starts.append(None),
        focus_window=lambda _timeout: "UU",
        timeout=10,
        relaunch_grace=1,
        poll_interval=1,
        monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    assert starts == [None]
