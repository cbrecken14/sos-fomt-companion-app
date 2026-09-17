"""Controller for the Ultrawide Window Position cheat: docks the game's window to the left or
right edge of whichever monitor it's on, instead of wherever the game itself centers it (built for
an ultrawide monitor where Borderless Window mode centers the game with unused space on both
sides, but works the same on any monitor -- on one close to the window's own width, left/right
dock to nearly the same spot).

Pure Win32 window positioning -- doesn't touch game memory at all, so it's not Frida-attached like
every other cheat controller (app/cheat_controller.py), but it lives in the Cheats dialog
(app/cheats_dialog.py) and follows the same default-off, set_enabled()/cleanup() shape for
consistency.

Polls for the game window on a timer rather than reacting to an event, since there's no signal for
"the game's window just appeared/moved" to hook into. Re-applies once per (hwnd, window mode) pair
seen -- covers a fresh game launch and a Windowed/Borderless mode switch while the game keeps
running, without fighting a manual drag afterward (the window is left alone once positioned, until
its hwnd or mode actually changes again).
"""
from __future__ import annotations

from typing import Optional

import win32api
import win32con
import win32gui
from PySide6.QtCore import QObject, QTimer, Signal

from .game_window import GameWindowInfo, find_game_window

POLL_INTERVAL_MS = 1000

LEFT = "left"
RIGHT = "right"


class UltrawideWindowPositionController(QObject):
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self._enabled = False
        self._side = LEFT
        self._applied_state: Optional[tuple[int, str]] = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            self._applied_state = None  # force a reapply, even if this exact window was already positioned before being turned off
            self._timer.start(POLL_INTERVAL_MS)
            self._poll()
        else:
            self._timer.stop()

    def set_side(self, side: str) -> None:
        self._side = side
        if self._enabled:
            self._applied_state = None  # force a reapply with the new side right away
            self._poll()

    def cleanup(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        info = find_game_window()
        if info is None:
            return  # game not running (or no window yet) this tick -- keep waiting
        state = (info.hwnd, info.mode)
        if state == self._applied_state:
            return
        self._apply(info)
        self._applied_state = state

    def _apply(self, info: GameWindowInfo) -> None:
        left, top, right, bottom = info.rect
        width, height = right - left, bottom - top
        try:
            monitor = win32api.MonitorFromWindow(info.hwnd, win32con.MONITOR_DEFAULTTONEAREST)
            mon_left, mon_top, mon_right, mon_bottom = win32api.GetMonitorInfo(monitor)["Monitor"]
        except Exception as exc:
            self.status.emit(f"Couldn't read monitor bounds: {exc}")
            return

        mon_height = mon_bottom - mon_top
        new_x = mon_left if self._side == LEFT else mon_right - width
        new_y = mon_top + (mon_height - height) // 2

        try:
            win32gui.SetWindowPos(
                info.hwnd, 0, new_x, new_y, 0, 0,
                win32con.SWP_NOSIZE | win32con.SWP_NOZORDER,
            )
        except Exception as exc:
            self.status.emit(f"Couldn't move the game window: {exc}")
            return

        self.status.emit(f"Docked game window to the {self._side} at ({new_x}, {new_y})")
