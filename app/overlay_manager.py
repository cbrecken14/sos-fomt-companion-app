"""Owns one instance of every registered overlay window (app/overlay_registry.py) for the app's
lifetime, and applies the master Enable Overlay / Lock Overlay / per-window Enabled / Transparent
Background state (app/overlay_config.py) to them. Mirrors app/game_session.py's
get_shared_session() singleton pattern: created lazily on first use (shell startup, or whenever
the Overlay Settings tab is opened, whichever comes first) and shut down once from the shell on
app close via shutdown_overlay_manager().
"""
from __future__ import annotations

import threading
from typing import Optional

from . import overlay_config
from .overlay_registry import all_specs
from .overlay_window import OverlayWindow

# Importing this registers every concrete overlay window (side effect, same pattern shell.py uses
# for app/windows/*). Add new overlay modules here as they're built (Coop Animal Position Reset, ...).
from .overlays import fatigue_value, harvest_goddess_streak, harvest_sprite_minigame  # noqa: F401


class OverlayManager:
    def __init__(self):
        self._windows: dict[str, OverlayWindow] = {spec.id: spec.factory() for spec in all_specs()}
        locked = overlay_config.overlay_locked()
        for window_id, window in self._windows.items():
            window.set_locked(locked)
            window.set_transparent(overlay_config.window_transparent(window_id, window.default_transparent))
        self._apply_all_visibility()

    def specs(self):
        return all_specs()

    def master_enabled(self) -> bool:
        return overlay_config.overlay_enabled()

    def set_master_enabled(self, enabled: bool) -> None:
        overlay_config.set_overlay_enabled(enabled)
        self._apply_all_visibility()

    def locked(self) -> bool:
        return overlay_config.overlay_locked()

    def set_locked(self, locked: bool) -> None:
        overlay_config.set_overlay_locked(locked)
        for window in self._windows.values():
            window.set_locked(locked)

    def window_enabled(self, window_id: str) -> bool:
        window = self._windows.get(window_id)
        default = window.default_enabled if window is not None else True
        return overlay_config.window_enabled(window_id, default)

    def set_window_enabled(self, window_id: str, enabled: bool) -> None:
        overlay_config.set_window_enabled(window_id, enabled)
        self._apply_visibility(window_id)

    def window_transparent(self, window_id: str) -> bool:
        window = self._windows.get(window_id)
        default = window.default_transparent if window is not None else False
        return overlay_config.window_transparent(window_id, default)

    def set_window_transparent(self, window_id: str, transparent: bool) -> None:
        overlay_config.set_window_transparent(window_id, transparent)
        window = self._windows.get(window_id)
        if window is not None:
            window.set_transparent(transparent)

    def reset_window_geometry(self, window_id: str) -> None:
        window = self._windows.get(window_id)
        if window is not None:
            window.reset_geometry()

    def _apply_all_visibility(self) -> None:
        for window_id in self._windows:
            self._apply_visibility(window_id)

    def _apply_visibility(self, window_id: str) -> None:
        window = self._windows[window_id]
        should_show = self.master_enabled() and self.window_enabled(window_id)
        window.set_active(should_show)
        if should_show:
            window.show()
            window.start_following()
        else:
            window.stop_following()
            window.hide()

    def cleanup(self) -> None:
        for window in self._windows.values():
            window.cleanup()
            window.close()


_shared: Optional[OverlayManager] = None
_shared_lock = threading.Lock()


def get_overlay_manager() -> OverlayManager:
    """Returns the app's one shared OverlayManager, creating every overlay window on first call."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = OverlayManager()
        return _shared


def shutdown_overlay_manager() -> None:
    """Call once from the shell on app shutdown. A no-op if the manager was never created."""
    global _shared
    if _shared is not None:
        _shared.cleanup()
        _shared = None
