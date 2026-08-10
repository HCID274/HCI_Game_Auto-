import json
import re
from types import SimpleNamespace

import wuwa_auto.okww.daily_trace as daily_trace_module
from wuwa_auto.okww.daily_trace import STAMINA_OCR_REGION, install_daily_trace


class _Box:
    def __init__(
        self,
        name: str,
        x: int = 1,
        y: int = 2,
        confidence: float = 91,
    ) -> None:
        self.name = name
        self.x = x
        self.y = y
        self.width = 30
        self.height = 12
        self.confidence = confidence


def test_stamina_trace_preserves_raw_ocr_boxes_and_parsed_tuple() -> None:
    class Tacet:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def log_info(self, message: str) -> None:
            self.messages.append(message)

        def ocr(self, *_args: object, **_kwargs: object) -> list[_Box]:
            return [_Box("体力: 24"), _Box("17")]

        def wait_ocr(self, *args: object, **kwargs: object) -> list[_Box]:
            # Match OK-WW's real wait_ocr -> ocr call shape: x/y positional,
            # to_x/to_y forwarded as keyword arguments.
            return self.ocr(
                args[0],
                args[1],
                to_x=kwargs.pop("to_x", args[2] if len(args) > 2 else 1),
                to_y=kwargs.pop("to_y", args[3] if len(args) > 3 else 1),
                **kwargs,
            )

        def get_stamina(self) -> tuple[int, int, int]:
            boxes = self.wait_ocr(
                STAMINA_OCR_REGION[0],
                STAMINA_OCR_REGION[1],
                to_x=STAMINA_OCR_REGION[2],
                to_y=STAMINA_OCR_REGION[3],
                raise_if_not_found=False,
                match=[re.compile(r"体力\s*[:：]?\s*(\d+)"), re.compile(r"\d+")],
            )
            assert boxes
            return 24, 17, 41

    install_daily_trace(SimpleNamespace, tacet_task_class=Tacet)
    task = Tacet()

    assert task.get_stamina() == (24, 17, 41)
    stamina_ocr = [
        json.loads(message.split("HOST_OKWW_DAILY_TRACE ", 1)[1])
        for message in task.messages
        if '"event": "stamina_ocr"' in message
    ]
    stamina_end = [
        json.loads(message.split("HOST_OKWW_DAILY_TRACE ", 1)[1])
        for message in task.messages
        if '"event": "stamina_end"' in message
    ]

    assert stamina_ocr[0]["region"] == list(STAMINA_OCR_REGION)
    assert stamina_ocr[0]["raw_names"] == ["体力: 24", "17"]
    assert stamina_ocr[0]["raw_boxes"][0]["confidence"] == 91
    assert stamina_end[0]["current_stamina"] == 24
    assert stamina_end[0]["back_up_stamina"] == 17
    assert stamina_end[0]["total_stamina"] == 41
    assert stamina_end[0]["ocr_available"] is True


def test_stamina_trace_logs_upstream_unavailable_sentinel_without_zeroing() -> None:
    class Tacet:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def log_info(self, message: str) -> None:
            self.messages.append(message)

        def ocr(self, *_args: object, **_kwargs: object) -> list[_Box]:
            return []

        def wait_ocr(self, *args: object, **kwargs: object) -> list[_Box]:
            return self.ocr(
                args[0],
                args[1],
                to_x=kwargs.get("to_x"),
                to_y=kwargs.get("to_y"),
                match=kwargs.get("match"),
            )

        def get_stamina(self) -> tuple[int, int, int]:
            self.wait_ocr(
                STAMINA_OCR_REGION[0],
                STAMINA_OCR_REGION[1],
                to_x=STAMINA_OCR_REGION[2],
                to_y=STAMINA_OCR_REGION[3],
            )
            return -1, -1, -1

    install_daily_trace(SimpleNamespace, tacet_task_class=Tacet)
    task = Tacet()

    assert task.get_stamina() == (-1, -1, -1)
    stamina_end = [
        json.loads(message.split("HOST_OKWW_DAILY_TRACE ", 1)[1])
        for message in task.messages
        if '"event": "stamina_end"' in message
    ]

    assert stamina_end[0]["current_stamina"] == -1
    assert stamina_end[0]["back_up_stamina"] == -1
    assert stamina_end[0]["total_stamina"] == -1
    assert stamina_end[0]["ocr_available"] is False


def test_low_confidence_standalone_zero_is_not_treated_as_exhausted(
    monkeypatch,
) -> None:
    class Tacet:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def log_info(self, message: str) -> None:
            self.messages.append(message)

        def sleep(self, _seconds: float) -> None:
            return None

        def ocr(self, *_args: object, **_kwargs: object) -> list[_Box]:
            return [_Box("0", x=2106, y=57, confidence=0.5328)]

        def wait_ocr(self, *args: object, **kwargs: object) -> list[_Box]:
            return self.ocr(
                args[0],
                args[1],
                to_x=kwargs.get("to_x"),
                to_y=kwargs.get("to_y"),
                match=kwargs.get("match"),
            )

        def get_stamina(self) -> tuple[int, int, int]:
            self.wait_ocr(
                STAMINA_OCR_REGION[0],
                STAMINA_OCR_REGION[1],
                to_x=STAMINA_OCR_REGION[2],
                to_y=STAMINA_OCR_REGION[3],
            )
            return 0, 0, 0

    monkeypatch.setattr(
        daily_trace_module,
        "_capture_stamina_evidence",
        lambda *_args: "stamina.png",
    )
    install_daily_trace(SimpleNamespace, tacet_task_class=Tacet)
    task = Tacet()

    assert task.get_stamina() == (-1, -1, -1)
    attempts = [
        json.loads(message.split("HOST_OKWW_DAILY_TRACE ", 1)[1])
        for message in task.messages
        if '"event": "stamina_read_attempt"' in message
    ]
    assert len(attempts) == 3
    assert all(attempt["semantic_read"] is False for attempt in attempts)
    assert attempts[0]["raw_observations"][0]["raw_names"] == ["0"]
    assert attempts[0]["raw_observations"][0]["raw_boxes"][0][
        "confidence"
    ] == 0.5328
    unverified = [
        json.loads(message.split("HOST_OKWW_DAILY_TRACE ", 1)[1])
        for message in task.messages
        if '"event": "stamina_read_unverified"' in message
    ]
    assert unverified[0]["evidence_path"] == "stamina.png"


def test_stamina_guard_retries_after_hid_panel_refresh(monkeypatch) -> None:
    class Tacet:
        def __init__(self) -> None:
            self.messages: list[str] = []
            self.calls = 0

        def log_info(self, message: str) -> None:
            self.messages.append(message)

        def sleep(self, _seconds: float) -> None:
            return None

        def ocr(self, *_args: object, **_kwargs: object) -> list[_Box]:
            if self.calls <= 3:
                return [_Box("0", x=2106, y=57, confidence=0.71)]
            return [_Box("180/240", confidence=0.95), _Box("12", confidence=0.95)]

        def wait_ocr(self, *args: object, **kwargs: object) -> list[_Box]:
            return self.ocr(
                args[0],
                args[1],
                to_x=kwargs.get("to_x"),
                to_y=kwargs.get("to_y"),
                match=kwargs.get("match"),
            )

        def get_stamina(self) -> tuple[int, int, int]:
            self.calls += 1
            self.wait_ocr(
                STAMINA_OCR_REGION[0],
                STAMINA_OCR_REGION[1],
                to_x=STAMINA_OCR_REGION[2],
                to_y=STAMINA_OCR_REGION[3],
            )
            return (0, 0, 0) if self.calls <= 3 else (180, 12, 192)

    refreshes: list[bool] = []
    monkeypatch.setattr(
        daily_trace_module,
        "_refresh_stamina_panel_with_hid",
        lambda _task: refreshes.append(True) or True,
    )
    install_daily_trace(SimpleNamespace, tacet_task_class=Tacet)

    assert Tacet().get_stamina() == (180, 12, 192)
    assert refreshes == [True]


def test_updated_book_tab_is_selected_by_bounded_ocr_and_hid(monkeypatch) -> None:
    from wuwa_auto.okww import virtual_hid

    clicks: list[tuple[int, int]] = []

    class Capture:
        def get_abs_cords(self, x: int, y: int) -> tuple[int, int]:
            return x + 7, y + 11

    class Tacet:
        executor = SimpleNamespace(
            interaction=SimpleNamespace(capture=Capture()),
        )

        def __init__(self) -> None:
            self.messages: list[str] = []
            self.upstream_calls: list[str] = []

        def log_info(self, message: str) -> None:
            self.messages.append(message)

        def sleep(self, _seconds: float) -> None:
            return None

        def ocr(self, *args: object, **_kwargs: object) -> list[_Box]:
            if float(args[0]) >= 0.60:
                return [_Box("前往查看", x=1900, y=1150)]
            return [_Box("无音清剿", x=410, y=800)]

        def open_boss_book(self, name: str, after_sleep: float = 2) -> None:
            self.upstream_calls.append(name)

    monkeypatch.setattr(
        daily_trace_module,
        "_capture_stamina_evidence",
        lambda *_args: "book-before.png",
    )
    monkeypatch.setattr(
        virtual_hid,
        "_virtual_hid_click",
        lambda x, y, **_kwargs: clicks.append((x, y)),
    )
    install_daily_trace(SimpleNamespace, tacet_task_class=Tacet)
    task = Tacet()

    task.open_boss_book("wuyin", after_sleep=0)

    assert clicks == [(432, 817), (1922, 1167)]
    assert task.upstream_calls == []
    assert any('"event": "book_tab_hid_click"' in item for item in task.messages)
    assert any(
        '"event": "book_tab_forward_view_hid_click"' in item
        for item in task.messages
    )
