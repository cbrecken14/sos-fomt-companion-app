"""The main app shell: a dockable/tabbable window container.

Each feature "window" (Reminders, Animals, Villagers,...) is a QDockWidget. Docked widgets
that share a dock area automatically show as a tab strip; Qt already supports dragging a tab
out into its own floating window and dragging it back in, and gives every dock widget a close
(X) button by default -- so the tear-off/dock/close behavior is native Qt behavior here, not
something hand-built.

Session persistence (which windows were open, and the full dock/tab/float layout) is handled
by `app/config.py`.

Floating dock windows staying above the main window: a floating QDockWidget is still
parented to the QMainWindow, which on Windows makes it an OWNED window (GWLP_HWNDPARENT set) --
Windows enforces that an owned window always stays above its owner whenever the owner is activated,
which otherwise means clicking the main window can't bring it above an undocked Mine Floor
Map/Reminders window. This is an OS-level rule, not a Qt window-flag setting,
so it can't be fixed by changing `Qt::Tool` vs `Qt::Window` or similar. _ManagedDockWidget instead
clears the native owner (SetWindowLong(..., GWL_HWNDPARENT, 0)) the moment a dock actually goes
floating -- same trick game_window.py's Win32 use already relies on pywin32 for. This only affects
Z-order; drag-to-redock, tabbing, and everything else about Qt's own dock management is untouched
since that's tracked via Qt's own bookkeeping (addDockWidget's internal registration), not the
native parent relationship. A window with WS_EX_TOOLWINDOW (which a floating dock already has)
stays out of the taskbar/alt-tab either way, owned or not.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDockWidget, QMainWindow, QTabWidget

import win32con
import win32gui

from . import config
from .cheats_dialog import (
    CheatsDialog,
    coin_spawn_cheat_count,
    coin_spawn_cheat_enabled,
    disabled_tired_animation_ids,
    guaranteed_item_spawn_cheat_enabled,
    show_memory_viewing_windows_enabled,
    tired_animations_cheat_enabled,
    ultrawide_window_position_enabled,
    ultrawide_window_position_side,
)
from .coin_spawn_cheat import CoinSpawnCheatController
from .game_session import shutdown_shared_session
from .guaranteed_item_spawn_cheat import GuaranteedItemSpawnCheatController
from .overlay_manager import get_overlay_manager, shutdown_overlay_manager
from .tired_animations_cheat import TiredAnimationsCheatController
from .ultrawide_window_position import UltrawideWindowPositionController
from .windows import all_specs, get_spec
from .windows import animal_live_viewer, farm_map, harvest_sprite_viewer, marriage_candidates, memory_viewer, mine_map, overlay_settings, reminders, villagers # noqa: F401 (registration side effects)

_OPEN_WINDOWS_KEY = "shell/open_window_ids"
_GEOMETRY_KEY = "shell/geometry"
_STATE_KEY = "shell/state"
# Per-window floating/geometry memory (2026-09-16): _STATE_KEY/_GEOMETRY_KEY above only get
# written/read on a full app close/launch (MainWindow.closeEvent/_restore_session), so they cover
# "app closed with this window open, relaunched later" but not "this ONE window's own X button was
# clicked mid-session, then re-added via Add Window" -- open_window() always re-docks a window it
# doesn't already have open, with no memory of how it looked right before it was closed. These two
# keys close that gap: written the moment a dock actually closes (_ManagedDockWidget.closeEvent),
# read back in open_window() when (re-)creating that same window id, so e.g. undocking Mine Floor
# Map and sizing it over the Reminders window survives closing it when leaving the mines and
# re-adding it next time.
_DOCK_FLOATING_KEY_TEMPLATE = "dock/{id}/floating"
_DOCK_GEOMETRY_KEY_TEMPLATE = "dock/{id}/geometry"


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


class _ManagedDockWidget(QDockWidget):
    """A QDockWidget that reports back to the shell when the user actually closes it.

    QDockWidget also fires `visibilityChanged(False)` when it's merely the non-active tab in a
    tabbed group -- that's not a close, so we can't use that signal for this. Overriding
    closeEvent() only fires on a real close (the X button, or a programmatic.close()).
    """

    def __init__(self, window_id: str, title: str, parent, on_close):
        super().__init__(title, parent)
        self._window_id = window_id
        self._on_close = on_close
        # Without this, closing a dock just hides it -- the widget (and anything it's holding
        # open, e.g. a live Frida session) lingers in memory forever as a hidden orphan.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # Fires for every reason this dock's floating state can change: dragging a tab out, our own
        # setFloating(True) call when reopening a previously-floating window, and QMainWindow's own
        # restoreState() on app launch -- see this module's docstring for why floating specifically
        # needs the native-owner fix below.
        self.topLevelChanged.connect(self._on_top_level_changed)

    def _on_top_level_changed(self, floating: bool) -> None:
        if not floating:
            return
        # Deferred, not called inline -- the inline version only reliably worked when a window
        # went docked-to-undocked, e.g. a drag tear-off; a window that comes back floating directly
        # (open_window()'s setFloating(True), called BEFORE .show()) stayed stuck behind the main
        # window. Calling winId()/SetWindowLong here fixes up whatever native window exists AT THIS
        # INSTANT -- for the drag case that's already the real, final one, but for
        # setFloating-before-show it's an intermediate one Qt still goes on to finish
        # creating/showing, which silently re-establishes the owner link afterward.
        # QTimer.singleShot(0,...) defers to the next trip through the event loop, after whatever
        # call is currently in progress (open_window()'s subsequent .show(), in that case) finishes
        # -- by then the native window is in its real final state and the fix sticks.
        QTimer.singleShot(0, self._detach_native_owner)

    def _detach_native_owner(self) -> None:
        if not self.isFloating():
            return
        try:
            hwnd = int(self.winId())
            win32gui.SetWindowLong(hwnd, win32con.GWL_HWNDPARENT, 0)
        except Exception as exc:
            print(f"[shell] couldn't detach floating window's native owner: {exc}")

    def closeEvent(self, event):
        widget = self.widget()
        if widget is not None and hasattr(widget, "cleanup"):
            widget.cleanup()
        # Snapshot this window's own floating/geometry state right as it closes -- see
        # _DOCK_FLOATING_KEY_TEMPLATE's comment above for why this can't just piggyback on the
        # whole-app _save_session() snapshot.
        settings = config.get_settings()
        settings.setValue(_DOCK_FLOATING_KEY_TEMPLATE.format(id=self._window_id), self.isFloating())
        settings.setValue(_DOCK_GEOMETRY_KEY_TEMPLATE.format(id=self._window_id), self.saveGeometry())
        super().closeEvent(event)
        self._on_close(self._window_id)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SoS: FoMT Companion")
        # Nesting lets dock areas split into side-by-side panels -- docked tabs are meant to
        # always share one single content area (like a plain Windows tab control), never split,
        # so nesting stays off. Tearing a tab out into its own floating window is unaffected by
        # this -- that's controlled separately by each dock's floatable feature.
        self.setDockNestingEnabled(False)
        # Tabbed dock groups default to a bottom tab strip; North puts the tab row directly
        # under the menu bar for every edge a group could dock to, so tabs sit in their own row
        # right under the menu.
        for area in (
            Qt.DockWidgetArea.TopDockWidgetArea,
            Qt.DockWidgetArea.LeftDockWidgetArea,
            Qt.DockWidgetArea.RightDockWidgetArea,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        ):
            self.setTabPosition(area, QTabWidget.TabPosition.North)
        self._docks: dict[str, QDockWidget] = {}

        self._guaranteed_item_spawn_cheat = GuaranteedItemSpawnCheatController()
        self._guaranteed_item_spawn_cheat.status.connect(lambda msg: print(f"[guaranteed item spawn cheat] {msg}"))
        self._guaranteed_item_spawn_cheat.set_enabled(guaranteed_item_spawn_cheat_enabled())

        self._coin_spawn_cheat = CoinSpawnCheatController()
        self._coin_spawn_cheat.status.connect(lambda msg: print(f"[coin spawn cheat] {msg}"))
        self._coin_spawn_cheat.set_count(coin_spawn_cheat_count())
        self._coin_spawn_cheat.set_enabled(coin_spawn_cheat_enabled())

        self._tired_animations_cheat = TiredAnimationsCheatController()
        self._tired_animations_cheat.status.connect(lambda msg: print(f"[tired animations cheat] {msg}"))
        self._tired_animations_cheat.set_disabled_animations(disabled_tired_animation_ids())
        self._tired_animations_cheat.set_enabled(tired_animations_cheat_enabled())

        # Not a memory-write feature (pure Win32 window positioning) -- still defaults off and
        # lives in Cheats for consistency, per app/ultrawide_window_position.py.
        self._ultrawide_window_position = UltrawideWindowPositionController()
        self._ultrawide_window_position.status.connect(lambda msg: print(f"[ultrawide window position] {msg}"))
        self._ultrawide_window_position.set_side(ultrawide_window_position_side())
        self._ultrawide_window_position.set_enabled(ultrawide_window_position_enabled())

        # Created eagerly (not lazily on first Overlay Settings tab open) so a previously-enabled
        # overlay window shows up again on launch even without opening that tab this session.
        self._overlay_manager = get_overlay_manager()

        # Default size for a first run (nothing saved yet) -- _restore_session() below overrides
        # this with restoreGeometry() when a prior session's size/position was saved.
        self.resize(900, 600)

        self._build_menu()
        self._restore_session()

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        cheats_action = file_menu.addAction("Cheats...")
        cheats_action.triggered.connect(self._open_cheats)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        # "+" is a plain top-level menu, same as File, rather than a corner widget -- corner
        # widgets on QMenuBar render inconsistently depending on style/platform (didn't render at
        # all in testing), whereas a top-level menu is guaranteed to render since it's the exact
        # same mechanism as File. Keeps everything on line 1, tabs on line 2.
        add_menu = self.menuBar().addMenu("Add Window")
        self._debug_window_actions = []
        for spec in all_specs():
            action = add_menu.addAction(spec.title)
            action.triggered.connect(lambda checked=False, wid=spec.id: self.open_window(wid))
            if spec.debug:
                action.setVisible(show_memory_viewing_windows_enabled())
                self._debug_window_actions.append(action)

    def open_window(self, window_id: str) -> None:
        if window_id in self._docks:
            dock = self._docks[window_id]
            dock.show()
            dock.raise_()
            return

        spec = get_spec(window_id)
        if spec is None:
            return

        dock = _ManagedDockWidget(window_id, spec.title, self, self._on_dock_closed)
        dock.setObjectName(window_id) # required so saveState()/restoreState() can match this dock
        dock.setWidget(spec.factory())

        existing = list(self._docks.values())
        self._docks[window_id] = dock

        # A dock must be added to the main window before tabifyDockWidget will actually merge it
        # into an existing tab group -- skipping addDockWidget here was the bug that made every
        # new window after the first show up as its own floating window instead of a new tab.
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, dock)
        if existing:
            self.tabifyDockWidget(existing[-1], dock)

        # Re-applies this window's own last-closed floating/geometry state, if any (see
        # _DOCK_FLOATING_KEY_TEMPLATE's comment above) -- e.g. re-adding Mine Floor Map after
        # closing it comes back undocked and sized/positioned the same as last time, rather than
        # re-docked at the default tab position every time. Only the floating case restores
        # geometry -- a re-docked window's position is already handled by the tabify above, same as
        # it always has been.
        settings = config.get_settings()
        was_floating = _as_bool(
            settings.value(_DOCK_FLOATING_KEY_TEMPLATE.format(id=window_id)), False
        )
        if was_floating:
            dock.setFloating(True)
            geometry = settings.value(_DOCK_GEOMETRY_KEY_TEMPLATE.format(id=window_id))
            if geometry is not None:
                dock.restoreGeometry(geometry)

        # Fires whenever this dock becomes the active tab in a tabbed group, or is shown after
        # being torn off into its own floating window -- raise_() on it then brings whichever
        # top-level window it belongs to to the front, so selecting a tab/window that's currently
        # behind another floating window actually comes forward instead of staying behind it
        #.
        dock.visibilityChanged.connect(lambda visible, d=dock: d.raise_() if visible else None)
        dock.show()
        dock.raise_()

    def _on_dock_closed(self, window_id: str) -> None:
        self._docks.pop(window_id, None)

    def _open_cheats(self) -> None:
        dialog = CheatsDialog(
            self._on_show_memory_viewing_windows_toggled,
            self._guaranteed_item_spawn_cheat.set_enabled,
            self._coin_spawn_cheat.set_enabled,
            self._coin_spawn_cheat.set_count,
            self._on_tired_animations_cheat_toggled,
            self._on_tired_animation_toggled,
            self._ultrawide_window_position.set_enabled,
            self._ultrawide_window_position.set_side,
            self,
        )
        dialog.exec()

    def _on_show_memory_viewing_windows_toggled(self, enabled: bool) -> None:
        for action in self._debug_window_actions:
            action.setVisible(enabled)

    def _on_tired_animations_cheat_toggled(self, enabled: bool) -> None:
        self._tired_animations_cheat.set_enabled(enabled)
        if enabled:
            # The disabled-IDs list may have changed while the cheat was off (sub-checkboxes stay
            # interactive-looking in memory even though the dialog grays them out) -- push the
            # current set the moment it's turned back on, same as the initial set at startup.
            self._tired_animations_cheat.set_disabled_animations(disabled_tired_animation_ids())

    def _on_tired_animation_toggled(self, animation_id: int, checked: bool) -> None:
        self._tired_animations_cheat.set_disabled_animations(disabled_tired_animation_ids())

    def closeEvent(self, event):
        self._save_session()
        # Closing the whole app doesn't fire each dock's own closeEvent (Qt just tears down child
        # widgets directly when their parent is destroyed) -- so cleanup() has to be called here
        # too, or a still-open tab's live resources (e.g. a Frida session) never get released and
        # can hang app shutdown -- the missing half of the earlier per-tab close fix.
        for dock in list(self._docks.values()):
            widget = dock.widget()
            if widget is not None and hasattr(widget, "cleanup"):
                widget.cleanup()
        # Not a dock -- these cheat controllers' floating button/dialog and the controller itself
        # need the same explicit cleanup call for the same reason. (The Harvest Sprite
        # Auto-Minigame's controller is cleaned up by shutdown_overlay_manager() below instead --
        # it's now owned by app/overlays/harvest_sprite_minigame.py's overlay window.)
        self._guaranteed_item_spawn_cheat.cleanup()
        self._coin_spawn_cheat.cleanup()
        self._tired_animations_cheat.cleanup()
        self._ultrawide_window_position.cleanup()
        # Same story as the shared GameSession below -- overlay windows are app-lifetime, owned by
        # OverlayManager, not by any dock, so they need their own explicit shutdown call too.
        shutdown_overlay_manager()
        # The shared GameSession (app/game_session.py) is app-lifetime, not owned by any one dock,
        # so it isn't covered by the per-window cleanup() calls above -- shut it down separately.
        shutdown_shared_session()
        super().closeEvent(event)

    def _save_session(self) -> None:
        settings = config.get_settings()
        settings.setValue(_OPEN_WINDOWS_KEY, list(self._docks.keys()))
        settings.setValue(_GEOMETRY_KEY, self.saveGeometry())
        settings.setValue(_STATE_KEY, self.saveState())

    def _restore_session(self) -> None:
        settings = config.get_settings()
        open_ids = settings.value(_OPEN_WINDOWS_KEY, [])
        if open_ids is None:
            open_ids = [] # QSettings returns None (not []) for a previously-saved empty list
        elif isinstance(open_ids, str):
            open_ids = [open_ids] # QSettings collapses a 1-item list back to a bare string
        for window_id in open_ids:
            self.open_window(window_id)

        geometry = settings.value(_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = settings.value(_STATE_KEY)
        if state is not None:
            self.restoreState(state)
