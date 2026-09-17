"""Controller for the Coin Spawn Count cheat: overrides how many coins a mined coin-bearing mine
tile spawns, forcing a chosen fixed count instead of the game's natural 5-20 range roll (see
src/coin_spawn_cheat_agent.js for the derivation and exact hook site).

This is a memory-WRITE feature, so it must default off and only run once explicitly enabled in
Cheats (see app/cheats_dialog.py) -- see app/cheat_controller.py's CheatController base class for
the shared attach/enable/teardown lifecycle every cheat controller in this project uses. Unlike a
plain on/off cheat, this one has a live-adjustable parameter (the forced coin count), pushed to the
already-running script via `script.post()` whenever the Cheats dialog's slider moves, rather than
requiring a re-attach.
"""
from __future__ import annotations

from pathlib import Path

from .cheat_controller import CheatController

AGENT_PATH = Path(__file__).resolve().parent.parent / "src" / "coin_spawn_cheat_agent.js"


class CoinSpawnCheatController(CheatController):
    AGENT_PATH = AGENT_PATH
    LOG_NAME = "coin spawn cheat"
    CHEAT_LABEL = "the coin spawn count cheat"

    def __init__(self):
        super().__init__()
        self._count = 20

    def set_count(self, count: int) -> None:
        self._count = count
        script = self._script
        if script is not None:
            try:
                script.post({"type": "setCoinCount", "count": count})
            except Exception as exc:
                self.status.emit(f"Couldn't update the coin spawn count: {exc}")

    def _on_script_loaded(self, script) -> None:
        script.post({"type": "setCoinCount", "count": self._count})
