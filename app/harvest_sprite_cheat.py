"""Controller for the Harvest Sprite Auto-Minigame cheat: a settings-gated feature that fast-
forwards a Harvest Sprite's training minigame instead of playing it, replicating the exact writes
the game performs on a win (see src/harvest_sprite_minigame_agent.js for the derivation).

This is a memory-WRITE feature, so it must default off and only run once explicitly enabled.
This controller is owned by app/overlays/harvest_sprite_minigame.py's HarvestSpriteMinigameOverlay,
whose own "Enabled" checkbox (Overlay Settings tab) plus the master "Enable Overlay" switch is the
on/off control -- see app/cheat_controller.py's CheatController base class for the shared
attach/enable/teardown lifecycle every cheat controller in this project uses.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal

from .cheat_controller import CheatController

AGENT_PATH = Path(__file__).resolve().parent.parent / "src" / "harvest_sprite_minigame_agent.js"


class HarvestSpriteCheatController(CheatController):
    AGENT_PATH = AGENT_PATH
    AGENT_TABLES = ("harvest_sprite_viewer",)
    LOG_NAME = "harvest sprite cheat"
    CHEAT_LABEL = "the harvest sprite cheat"

    location_changed = Signal(bool)  # True while the player is standing in the Harvest Sprite Hut
    sprite_data_updated = Signal(list)  # list of {id, name, daysLeft, playedToday, skills}

    def __init__(self):
        super().__init__()
        self._game_session = None

    def complete_minigame(self, sprite_id: int, skill_index: int) -> None:
        if self._script is not None:
            self._script.post({"type": "completeMinigame", "id": sprite_id, "skillIndex": skill_index})

    def _on_attached(self, game_session) -> None:
        self._game_session = game_session
        game_session.save_data_base_found.connect(self._on_shared_save_data_base)
        # Covers the case where Save Data Base was already found before this script finished loading.
        if game_session.current_save_data_base:
            self._on_shared_save_data_base(game_session.current_save_data_base)

    def _on_shared_save_data_base(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setSaveDataBase", "address": address})

    def _on_payload(self, kind, payload) -> bool:
        if kind == "locationUpdate":
            self.location_changed.emit(payload["inHut"])
            return True
        if kind == "spriteData":
            self.sprite_data_updated.emit(payload["sprites"])
            return True
        if kind == "minigameCompleted":
            self.status.emit(payload.get("message", str(payload)))
            return True
        return False

    def _on_detached(self) -> None:
        if self._game_session is not None:
            try:
                self._game_session.save_data_base_found.disconnect(self._on_shared_save_data_base)
            except (TypeError, RuntimeError):
                pass
        self._game_session = None
        self.location_changed.emit(False)
