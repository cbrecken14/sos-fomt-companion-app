"""'Harvest Goddess High/Low Game Win Streak' overlay -- lets the player pick a starting win streak
for the Harvest Goddess High-Low TV minigame, shown only while that specific channel is open. See
data/pointer_map.md's "Harvest Goddess High-Low minigame" section for the full derivation (which
slot in the reveal/history array is the win streak, and why the write has to intercept the game's
own reset-to-0 rather than just writing at channel-open).

A memory-WRITE feature. Per this project's hard rule for write features, defaults OFF
(default_enabled=False below) and only attaches its own script while THIS overlay's own "Enabled"
checkbox (Overlay Settings tab) *and* the master "Enable Overlay" switch are both on -- see
OverlayWindow.set_active()'s docstring for this gating pattern.

Once picked, the streak is a plain dropdown (data/tables/harvest_goddess_prizes.json's tiers, one
per row, labeled "Win Streak N - Prize") -- not a free-typed number, so the player always sees what
they're actually aiming for. The last selection persists (app/overlay_config.py) so it auto-populates next
time. Picking a NEW value while already standing in front of the TV re-writes it into the game
immediately (see the agent's `setStreak` handler) rather than waiting for the next channel-open,
since by the time this overlay is visible the automatic channel-open reset may have already fired.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QVBoxLayout

from .. import overlay_config
from ..game_session import get_shared_session
from ..overlay_registry import OverlaySpec, register
from ..overlay_window import OverlayWindow

AGENT_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "harvest_goddess_streak_agent.js"
PRIZES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "tables" / "harvest_goddess_prizes.json"

WINDOW_ID = "harvest_goddess_streak"

# Confirmed live (pulled from config/app_config.ini's persisted
# overlay/windows/harvest_goddess_streak/windowed/offset+size after dragging/resizing it during
# testing) -- this is for "windowed" mode specifically; no confirmed Borderless/Fullscreen default
# yet, so that mode currently falls back to these same numbers, same as fatigue_value.py.
_DEFAULT_OFFSET = (758, 79)
_DEFAULT_SIZE = (482, 47)

_PANEL_BACKING_COLOR = QColor(30, 30, 30, 220)


def _load_prize_tiers() -> list[dict]:
    data = json.loads(PRIZES_PATH.read_text())
    return data["HARVEST_GODDESS_PRIZES"]


class HarvestGoddessStreakOverlay(OverlayWindow):
    channel_updated = Signal(bool) # emitted from the Frida message-callback thread
    status_updated = Signal(str) # ditto

    def __init__(self, parent=None):
        super().__init__(
            WINDOW_ID,
            "Harvest Goddess High/Low Game Win Streak",
            default_offset=_DEFAULT_OFFSET,
            default_size=_DEFAULT_SIZE,
            default_transparent=False,
            default_enabled=False,  # hard rule for write features -- opt-in only
            parent=parent,
        )
        self._script = None
        self._active = False
        self._in_hg_channel = False
        self._manager_wants_shown = False
        self._tiers = _load_prize_tiers()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._combo = QComboBox()
        for tier in self._tiers:
            self._combo.addItem(f"Win Streak {tier['win_streak_min']} - {tier['prize']}", tier["win_streak_min"])
        saved_value = overlay_config.window_selected_win_streak(WINDOW_ID, default=0)
        index = self._combo.findData(saved_value)
        self._combo.setCurrentIndex(index if index >= 0 else 0)
        self._selected_streak = self._combo.currentData()
        self._combo.currentIndexChanged.connect(self._on_selection_changed)
        layout.addWidget(self._combo)
        self._apply_combo_text_color()

        self.channel_updated.connect(self._on_channel_update)
        self.status_updated.connect(self._on_status)

    # --- combined visibility: OverlayManager's enabled/master state AND the HG channel being open --
    # OverlayManager only ever calls show()/hide() (i.e. Qt's own setVisible()) based on the
    # master+per-window config; overriding setVisible() here lets this overlay layer the
    # channel-gated visibility on top without changing overlay_manager.py at all.

    def setVisible(self, visible: bool) -> None:
        self._manager_wants_shown = visible
        super().setVisible(visible and self._in_hg_channel)

    # --- text color: black over the transparent (no-panel) background, white over the solid panel --
    # -- OverlayWindow.set_transparent() only triggers a repaint of this
    # window's own paintEvent (the backing panel), it doesn't know about the QComboBox child
    # widget, so this overrides it to also restyle the combo's text color.

    def set_transparent(self, transparent: bool) -> None:
        super().set_transparent(transparent)
        self._apply_combo_text_color()

    def _apply_combo_text_color(self) -> None:
        color = "black" if self._transparent else "white"
        self._combo.setStyleSheet(f"QComboBox {{ color: {color}; }}")

    def _on_channel_update(self, in_hg_channel: bool) -> None:
        self._in_hg_channel = in_hg_channel
        self.setVisible(self._manager_wants_shown)

    def _on_status(self, message: str) -> None:
        print(f"[harvest goddess streak] {message}")

    def _on_selection_changed(self, _index: int) -> None:
        self._selected_streak = self._combo.currentData()
        overlay_config.set_window_selected_win_streak(WINDOW_ID, self._selected_streak)
        self._push_streak()

    def _push_streak(self) -> None:
        script = self._script
        if script is not None:
            try:
                script.post({"type": "setStreak", "value": self._selected_streak})
            except Exception as exc:
                self.status_updated.emit(f"Couldn't update the win streak: {exc}")

    # --- write-feature gating: only attach the script while switched on ----------------------------

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        if active:
            threading.Thread(target=self._attach_worker, daemon=True).start()
        else:
            self._teardown()

    # --- live memory attach (same pattern app/harvest_sprite_cheat.py's set_enabled() uses) --------

    def _attach_worker(self) -> None:
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session() # background thread only, never Qt main
            script = session.create_script(AGENT_PATH.read_text())
            script.on("message", self._on_message)
            script.load()
        except Exception as exc:
            self.status_updated.emit(f"Couldn't attach to the game: {exc}")
            return
        if not self._active:
            # Toggled off again while this was still attaching -- tear back down right away.
            try:
                script.unload()
            except Exception:
                pass
            return
        self._script = script
        self._push_streak() # so the agent already has the right value for the NEXT channel-open

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread -- only touch plain state here, marshal to Qt via
        # the Signal/emit calls, same rule as every other feature window's own message handler.
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[harvest goddess streak] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "channelUpdate":
            self.channel_updated.emit(payload["inHgChannel"])
        elif kind == "status":
            self.status_updated.emit(payload.get("message", ""))

    def _teardown(self) -> None:
        script = self._script
        self._script = None
        self._in_hg_channel = False
        self.setVisible(self._manager_wants_shown)

        def detach() -> None:
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()

    def cleanup(self) -> None:
        """Only unloads this window's OWN script -- the shared session itself is app-lifetime,
        detached once from the shell on app shutdown (see game_session.shutdown_shared_session)."""
        super().cleanup()
        if self._active:
            self._active = False
            self._teardown()

    # --- painting --------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        if not self._transparent:
            painter.fillRect(self.rect(), _PANEL_BACKING_COLOR)
        self._draw_resize_handle(painter)


def _factory() -> HarvestGoddessStreakOverlay:
    return HarvestGoddessStreakOverlay()


register(OverlaySpec(WINDOW_ID, "Harvest Goddess High/Low Game Win Streak", _factory))
