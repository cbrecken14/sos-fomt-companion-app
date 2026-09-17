"""Controller for the Guaranteed Ground Item Spawn cheat: forces every candidate in an area's daily
ground-item spawn roll to succeed, without changing which item gets picked (see
src/guaranteed_item_spawn_agent.js for the derivation and exact hook site).

This is a memory-WRITE feature, so it must default off and only run once explicitly enabled in
Cheats (see app/cheats_dialog.py) -- see app/cheat_controller.py's CheatController base class for
the shared attach/enable/teardown lifecycle every cheat controller in this project uses.

This hook patches a fixed code address that doesn't depend on any particular save being loaded, so
there's no save-data-base wiring here -- the script just installs its one Interceptor hook as soon
as it loads, and every message it ever sends is a plain `status` (handled by the base class).
"""
from __future__ import annotations

from pathlib import Path

from .cheat_controller import CheatController

AGENT_PATH = Path(__file__).resolve().parent.parent / "src" / "guaranteed_item_spawn_agent.js"


class GuaranteedItemSpawnCheatController(CheatController):
    AGENT_PATH = AGENT_PATH
    LOG_NAME = "guaranteed item spawn cheat"
    CHEAT_LABEL = "the guaranteed item spawn cheat"
