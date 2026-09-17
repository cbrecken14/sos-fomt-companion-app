"""Controller for the Tired Animations cheat: suppresses specific tired/exhausted reaction
animations (IDs 36-42, one per Stamina/Fatigue threshold -- see data/pointer_map.md's Tired
Animations section) by zeroing the animation ID right before it's stored, for whichever IDs are
checked in Cheats (see app/cheats_dialog.py).

This is a memory-WRITE feature, so it defaults off and only runs once explicitly enabled in
Cheats -- see app/cheat_controller.py's CheatController base class for the shared
attach/enable/teardown lifecycle every cheat controller in this project uses. Like the Coin Spawn
Count cheat, this has a live-adjustable parameter (which animation IDs are currently disabled),
pushed to the already-running script via `script.post()` whenever a sub-checkbox is toggled,
rather than requiring a re-attach.
"""
from __future__ import annotations

from pathlib import Path

from .cheat_controller import CheatController

AGENT_PATH = Path(__file__).resolve().parent.parent / "src" / "tired_animations_cheat_agent.js"


class TiredAnimationsCheatController(CheatController):
    AGENT_PATH = AGENT_PATH
    LOG_NAME = "tired animations cheat"
    CHEAT_LABEL = "the tired animations cheat"

    def __init__(self):
        super().__init__()
        self._disabled_ids: list[int] = []

    def set_disabled_animations(self, disabled_ids: list[int]) -> None:
        self._disabled_ids = list(disabled_ids)
        script = self._script
        if script is not None:
            try:
                script.post({"type": "setDisabledAnimations", "ids": self._disabled_ids})
            except Exception as exc:
                self.status.emit(f"Couldn't update the disabled animation list: {exc}")

    def _on_script_loaded(self, script) -> None:
        script.post({"type": "setDisabledAnimations", "ids": self._disabled_ids})
