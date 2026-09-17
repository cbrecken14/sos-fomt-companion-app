"""Finds the live game window's on-screen rectangle via the Win32 API, so a floating widget can
position itself relative to the actual game window instead of a fixed screen corner. Same
technique src/overlay.py's find_game_window_rect used (Milestone 3), pulled out here so any
floating UI -- the harvest sprite cheat's button today, the long-term overlay eventually -- doesn't
reimplement it.

Also detects a rough WINDOW MODE (see find_game_window()) so the overlay can remember separate
positions for "Windowed" vs. a window that covers the whole monitor with no border -- overlay
positions persist per window state, since a small windowed game and one that fills the screen
naturally want different overlay placement. This is only a 2-way split, not the game's own 3 menu
options (Windowed / Borderless Windowed /
Fullscreen): Win32 can reliably tell "has a title bar" from "no border, covers the whole monitor",
but true exclusive-fullscreen and borderless-windowed-at-full-monitor-size look identical from
outside the game process -- there's no reliable Win32 signal to split those two further without
guessing. If that distinction ever turns out to matter in practice, it needs a live check (e.g.
whether the display's actual resolution changes) rather than being assumed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import frida
import win32api
import win32con
import win32gui
import win32process

from .game_session import PROCESS_NAME

WINDOWED = "windowed"
FULLSCREEN_OR_BORDERLESS = "fullscreen_or_borderless"


def _find_pid() -> Optional[int]:
    try:
        return frida.get_local_device().get_process(PROCESS_NAME).pid
    except Exception:
        return None


def _find_game_window_candidates(pid: int) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Returns (hwnd, rect) for every visible top-level window owned by the game process."""
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == pid:
                candidates.append((hwnd, win32gui.GetWindowRect(hwnd)))
        return True

    win32gui.EnumWindows(callback, None)
    return candidates


def _area(rect: tuple[int, int, int, int]) -> int:
    left, top, right, bottom = rect
    return (right - left) * (bottom - top)


def find_game_window_rect() -> Optional[tuple[int, int, int, int]]:
    """Returns (left, top, right, bottom) of the largest visible top-level window owned by the
    game process, or None if the game isn't running / no window was found."""
    pid = _find_pid()
    if pid is None:
        return None
    candidates = _find_game_window_candidates(pid)
    if not candidates:
        return None
    return max((rect for _hwnd, rect in candidates), key=_area)


@dataclass(frozen=True)
class GameWindowInfo:
    rect: tuple[int, int, int, int]
    mode: str  # WINDOWED or FULLSCREEN_OR_BORDERLESS
    hwnd: int


def _detect_mode(hwnd: int, rect: tuple[int, int, int, int]) -> str:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    has_caption = bool(style & win32con.WS_CAPTION)
    try:
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        monitor_rect = win32api.GetMonitorInfo(monitor)["Monitor"]
    except Exception:
        return WINDOWED
    covers_monitor = tuple(rect) == tuple(monitor_rect)
    if not has_caption and covers_monitor:
        return FULLSCREEN_OR_BORDERLESS
    return WINDOWED


def find_game_window() -> Optional[GameWindowInfo]:
    """Same window lookup as find_game_window_rect(), but also returns a best-guess window mode
    -- see this module's docstring for exactly what can/can't be told apart."""
    pid = _find_pid()
    if pid is None:
        return None
    candidates = _find_game_window_candidates(pid)
    if not candidates:
        return None
    hwnd, rect = max(candidates, key=lambda c: _area(c[1]))
    return GameWindowInfo(rect=rect, mode=_detect_mode(hwnd, rect), hwnd=hwnd)
