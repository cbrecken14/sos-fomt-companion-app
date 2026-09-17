"""Base class for a floating overlay window that sits over the running game window.

Position is stored as an OFFSET from the game window's top-left corner, not an absolute screen
position -- so an overlay follows correctly if the game window moves, resizes, or changes monitor.
Subclass this for each concrete overlay (Fatigue Value, Harvest Sprite button, ...). Handles:
following the game window, drag-to-move + drag-to-resize (only while unlocked), an optional
transparent background, and persisting offset/size across app restarts -- separately per game
WINDOW MODE (see game_window.find_game_window()'s docstring for exactly what that does/doesn't
distinguish), so switching between windowed and a full-monitor game window recalls whichever
position was last used in that mode.

Same follow technique the Harvest Sprite Auto-Minigame's old bespoke floating button used before it
was migrated onto this base class (poll game_window.find_game_window() on a timer, see
app/overlays/harvest_sprite_minigame.py) -- generalized here so every overlay window shares one
implementation instead of each reinventing it.

Resizing is a hand-rolled bottom-right corner handle, not QSizeGrip -- QSizeGrip didn't reliably
resize this combination of frameless/always-on-top/translucent-background window on Windows (it
couldn't resize at all, with or without a background). Handling it with the same mouse-event
approach already used for dragging sidesteps whatever QSizeGrip didn't like.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QWidget

from . import config
from .game_window import FULLSCREEN_OR_BORDERLESS, WINDOWED, find_game_window

FOLLOW_INTERVAL_MS = 500
RESIZE_HANDLE_SIZE = 14

_OFFSET_KEY_TEMPLATE = "overlay/windows/{id}/{mode}/offset"
_SIZE_KEY_TEMPLATE = "overlay/windows/{id}/{mode}/size"


class OverlayWindow(QWidget):
    def __init__(
        self,
        window_id: str,
        title: str,
        default_offset: tuple[int, int] = (20, 20),
        default_size: Optional[tuple[int, int]] = None,
        default_transparent: bool = False,
        default_enabled: bool = True,
        resizable: bool = True,
        parent=None,
    ):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._window_id = window_id
        self._title = title
        self._default_offset = default_offset
        self._default_size = default_size
        self._resizable = resizable
        self._locked = False
        self._dragging = False
        self._drag_start_global = QPoint()
        self._drag_start_window = QPoint()
        self._resizing = False
        self._resize_start_global = QPoint()
        self._resize_start_size = QSize()
        self._paused_follow_for_gesture = False

        # None (not a real mode value) so the FIRST _follow_game_window() tick always sees a
        # "mode changed" mismatch and loads whatever was actually saved -- if this started equal to
        # the game's real (and common) windowed mode, that first comparison would silently no-op
        # and the saved offset/size would never get loaded at all, since every later tick only
        # reloads on an actual mode CHANGE, not on startup.
        self._current_mode: Optional[str] = None
        self._offset = default_offset
        if default_size is not None:
            self.resize(*default_size)

        self.setMinimumSize(40, 24)

        self.default_transparent = default_transparent
        self._transparent = default_transparent
        # Per-window default for the Overlay Settings "Enabled" checkbox -- True everywhere except
        # a memory-WRITE overlay button (e.g. Barn/Coop Animal Position Reset), which must default
        # off per this project's hard rule for write features (see overlay_manager.py's
        # window_enabled()).
        self.default_enabled = default_enabled
        # Always create the window as translucent-CAPABLE, and leave it that way for the window's
        # whole lifetime -- toggling WA_TranslucentBackground on an already-shown native window is
        # unreliable on Windows (a Qt/Windows quirk: it wants to be set once at creation, not
        # flipped later -- unchecking/rechecking Transparent Background stopped actually hiding the
        # panel). Whether it LOOKS transparent is controlled entirely by whether a subclass's
        # paintEvent draws an opaque backing rect for self._transparent, never by touching this
        # attribute again after this point.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_game_window)

    # --- geometry persistence -----------------------------------------------------------------

    def _load_offset(self, mode: str) -> tuple[int, int]:
        key = _OFFSET_KEY_TEMPLATE.format(id=self._window_id, mode=mode)
        value = config.get_settings().value(key)
        if value is None:
            return self._default_offset
        try:
            x, y = value
            return int(x), int(y)
        except (TypeError, ValueError):
            return self._default_offset

    def _load_size(self, mode: str) -> Optional[tuple[int, int]]:
        key = _SIZE_KEY_TEMPLATE.format(id=self._window_id, mode=mode)
        value = config.get_settings().value(key)
        if value is None:
            return None
        try:
            w, h = value
            return int(w), int(h)
        except (TypeError, ValueError):
            return None

    def _commit_geometry(self) -> None:
        """Called once the user finishes a drag or resize: recompute this window's offset from
        the game window's CURRENT rect (not the one at drag-start -- the game window itself could
        have moved mid-drag) and persist offset + size under the current mode."""
        info = find_game_window()
        if info is not None:
            self._current_mode = info.mode
            left, top, _, _ = info.rect
            self._offset = (self.x() - left, self.y() - top)
        mode = self._current_mode or WINDOWED  # guards the (unlikely) case a drag finishes before
                                                # the game window was ever found even once
        settings = config.get_settings()
        settings.setValue(_OFFSET_KEY_TEMPLATE.format(id=self._window_id, mode=mode), self._offset)
        settings.setValue(
            _SIZE_KEY_TEMPLATE.format(id=self._window_id, mode=mode),
            (self.width(), self.height()),
        )

    def reset_geometry(self) -> None:
        """Resets this window's saved offset/size for EVERY known game-window mode back to its
        built-in default and repositions immediately -- an escape hatch if a drag/resize leaves an
        overlay off-screen or otherwise hard to grab back (Overlay Settings' per-window
        "Reset" button, app/windows/overlay_settings.py). Resets both modes, not just whichever one
        is currently active, since a bad position could be stuck in either one and there's no
        separate confirmed default per mode for most overlay windows yet anyway."""
        settings = config.get_settings()
        for mode in (WINDOWED, FULLSCREEN_OR_BORDERLESS):
            settings.setValue(_OFFSET_KEY_TEMPLATE.format(id=self._window_id, mode=mode), self._default_offset)
            if self._default_size is not None:
                settings.setValue(_SIZE_KEY_TEMPLATE.format(id=self._window_id, mode=mode), self._default_size)
        self._offset = self._default_offset
        if self._default_size is not None:
            self.resize(*self._default_size)
        self._follow_game_window()  # snap to the reset position immediately, don't wait for the next tick

    # --- following the game window -------------------------------------------------------------

    def start_following(self) -> None:
        self._follow_game_window()
        self._follow_timer.start(FOLLOW_INTERVAL_MS)

    def stop_following(self) -> None:
        self._follow_timer.stop()

    def _follow_game_window(self) -> None:
        info = find_game_window()
        if info is None:
            return  # game window not found this tick -- keep last known position rather than jump
        if info.mode != self._current_mode:
            # Window mode changed (e.g. Windowed <-> Borderless/Fullscreen) -- recall whichever
            # offset/size was last used in the NEW mode instead of reapplying the old mode's
            # offset somewhere it was never meant for.
            self._current_mode = info.mode
            self._offset = self._load_offset(info.mode)
            size = self._load_size(info.mode) or self._default_size
            if size is not None:
                self.resize(*size)
        left, top, _, _ = info.rect
        self.move(left + self._offset[0], top + self._offset[1])

    def _pause_following_for_gesture(self) -> None:
        # Without this, the follow timer's own move() (still firing every 500ms) fights a drag or
        # resize already in progress -- the window visibly snaps back mid-gesture.
        self._paused_follow_for_gesture = self._follow_timer.isActive()
        self._follow_timer.stop()

    def _resume_following_after_gesture(self) -> None:
        if self._paused_follow_for_gesture:
            self._follow_timer.start(FOLLOW_INTERVAL_MS)
            self._paused_follow_for_gesture = False

    # --- lock / transparency --------------------------------------------------------------------

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.update()  # the resize handle only paints while unlocked -- see _draw_resize_handle

    def set_transparent(self, transparent: bool) -> None:
        self._transparent = transparent
        self.update()

    # --- drag to move / drag to resize (only while unlocked) -------------------------------------

    def _resize_handle_rect(self) -> QRect:
        s = RESIZE_HANDLE_SIZE
        return QRect(self.width() - s, self.height() - s, s, s)

    def _draw_resize_handle(self, painter) -> None:
        """Call at the end of a subclass's paintEvent to show a grabbable corner indicator --
        matches the mouse-hit-testing in _resize_handle_rect() above. Hidden while transparent, not
        just while locked (the handle was still showing up over a transparent background with
        nothing else around it to give it context) -- resizing while transparent still works by
        feel (the hit-test in _resize_handle_rect() doesn't depend on this), it just isn't visually
        advertised."""
        if not self._resizable or self._locked or self._transparent:
            return
        painter.save()
        pen = QPen(QColor(255, 255, 255, 150))
        pen.setWidth(1)
        painter.setPen(pen)
        rect = self._resize_handle_rect()
        for offset in (4, 8, 12):
            painter.drawLine(rect.right() - offset, rect.bottom(), rect.right(), rect.bottom() - offset)
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if not self._locked and event.button() == Qt.MouseButton.LeftButton:
            if self._resizable and self._resize_handle_rect().contains(event.position().toPoint()):
                self._resizing = True
                self._resize_start_global = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
            else:
                self._dragging = True
                self._drag_start_global = event.globalPosition().toPoint()
                self._drag_start_window = self.pos()
            self._pause_following_for_gesture()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_start_global
            new_width = max(self.minimumWidth(), self._resize_start_size.width() + delta.x())
            new_height = max(self.minimumHeight(), self._resize_start_size.height() + delta.y())
            self.resize(new_width, new_height)
        elif self._dragging and not self._locked:
            delta = event.globalPosition().toPoint() - self._drag_start_global
            self.move(self._drag_start_window + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing or self._dragging:
            self._resizing = False
            self._dragging = False
            self._commit_geometry()
            self._resume_following_after_gesture()
        super().mouseReleaseEvent(event)

    def cleanup(self) -> None:
        self._follow_timer.stop()

    # --- write-feature gating hook ---------------------------------------------------------------

    def set_active(self, active: bool) -> None:
        """Called by OverlayManager whenever this window's combined enabled state (master "Enable
        Overlay" switch AND this window's own "Enabled" checkbox) changes -- alongside show()/hide().
        A no-op here; override it in a subclass whose enabled state must gate something beyond
        visibility, e.g. a memory-WRITE overlay button attaching/detaching its own Frida script only
        while switched on (see app/overlays/harvest_goddess_streak.py)."""
        pass
