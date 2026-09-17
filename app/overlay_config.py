"""Persisted settings for the overlay feature: the master Enable Overlay switch (defaults off),
the global Lock Overlay Windows switch, and per-window Enabled / Transparent Background flags.
Geometry (offset from the game window's corner, and size) is persisted separately by
OverlayWindow itself (see app/overlay_window.py) -- it's keyed by window id the same way, just
kept apart since it's written on every drag/resize rather than from a settings-tab checkbox.
Follows the same QSettings-backed getter/setter pattern as cheats_dialog.py.
"""
from __future__ import annotations

from . import config

OVERLAY_ENABLED_KEY = "overlay/enabled"
OVERLAY_LOCKED_KEY = "overlay/locked"
_WINDOW_ENABLED_KEY_TEMPLATE = "overlay/windows/{id}/enabled"
_WINDOW_TRANSPARENT_KEY_TEMPLATE = "overlay/windows/{id}/transparent"
_WINDOW_SELECTED_WIN_STREAK_KEY_TEMPLATE = "overlay/windows/{id}/selected_win_streak"


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def overlay_enabled() -> bool:
    return _as_bool(config.get_settings().value(OVERLAY_ENABLED_KEY, False), False)


def set_overlay_enabled(enabled: bool) -> None:
    config.get_settings().setValue(OVERLAY_ENABLED_KEY, enabled)


def overlay_locked() -> bool:
    return _as_bool(config.get_settings().value(OVERLAY_LOCKED_KEY, False), False)


def set_overlay_locked(locked: bool) -> None:
    config.get_settings().setValue(OVERLAY_LOCKED_KEY, locked)


def window_enabled(window_id: str, default: bool = True) -> bool:
    key = _WINDOW_ENABLED_KEY_TEMPLATE.format(id=window_id)
    return _as_bool(config.get_settings().value(key, default), default)


def set_window_enabled(window_id: str, enabled: bool) -> None:
    config.get_settings().setValue(_WINDOW_ENABLED_KEY_TEMPLATE.format(id=window_id), enabled)


def window_transparent(window_id: str, default: bool = False) -> bool:
    key = _WINDOW_TRANSPARENT_KEY_TEMPLATE.format(id=window_id)
    return _as_bool(config.get_settings().value(key, default), default)


def set_window_transparent(window_id: str, transparent: bool) -> None:
    config.get_settings().setValue(_WINDOW_TRANSPARENT_KEY_TEMPLATE.format(id=window_id), transparent)


def window_selected_win_streak(window_id: str, default: int = 0) -> int:
    """Generic per-window 'last selection' slot -- currently only used by the Harvest Goddess
    Win Streak overlay's dropdown, named for that specific value rather than generically since no
    other overlay window has needed a persisted selection yet."""
    key = _WINDOW_SELECTED_WIN_STREAK_KEY_TEMPLATE.format(id=window_id)
    try:
        return int(config.get_settings().value(key, default))
    except (TypeError, ValueError):
        return default


def set_window_selected_win_streak(window_id: str, value: int) -> None:
    config.get_settings().setValue(_WINDOW_SELECTED_WIN_STREAK_KEY_TEMPLATE.format(id=window_id), value)
