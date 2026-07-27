from types import SimpleNamespace

from starrail_auto.uu import desktop


def test_focus_uu_window_uses_verified_activation(monkeypatch) -> None:
    window = SimpleNamespace(title="网易UU加速器", isMinimized=False, _hWnd=123)
    activations: list[tuple[int, float]] = []
    monkeypatch.setattr(desktop, "get_uu_windows", lambda: [window])
    monkeypatch.setattr(
        desktop,
        "activate_window",
        lambda hwnd, timeout: activations.append((hwnd, timeout),),
    )
    monkeypatch.setattr(desktop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(desktop.ctypes.windll.user32, "GetForegroundWindow", lambda: 123)

    assert desktop.focus_uu_window(timeout=0.1) == "网易UU加速器"
    assert activations == [(123, 1.5)]
