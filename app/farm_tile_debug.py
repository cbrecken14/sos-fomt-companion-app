"""Debug-only controller backing the Reminders window's farm-tile debugging dialog (see
src/farm_tile_debug_agent.js and app/farm_debug_dialog.py; not currently wired into the UI, kept
as a developer tool). One-shot memory-write actions for developing/verifying the farm tile struct:
clearing the whole field back to a blank untilled map (debris AND crops, whatever their growth
stage), tilling soil (only if no debris remains -- also clears any existing crops), watering
already-tilled soil, planting one full-height test column per confirmed crop-type id (0-22,
clearing+tilling the whole farm first), planting the current season's crops across the whole farm
in tidy 9x9 blocks with a 1-tile gap between blocks (for exercising the Reminders window's
crop-status tracking against a realistic, varied field), and planting EVERY crop-type id at every
growth stage 1-5 (one column per id, one row per stage) for comparing, per crop, which stages look
visually distinct -- stage 5 is always the harvestable one regardless of how many DISTINCT phases a
crop actually has, see data/pointer_map.md's "Growth table" section.

Also: `plant_seed_row()` (a write) plants one seed-stage tile per crop-type id for a day-by-day
growth-tracking experiment (confirming exactly how many real days/waterings each crop needs to
reach stage 5, cross-checked against `data/tables/crop_growth.json`'s table), and
`export_field_data()` (a READ, the only one in this file) reads every known field off those same
tracked tiles for a snapshot per real day. Export results arrive via the `export_ready` signal as
raw structured data (not a formatted report) -- formatting/printing is app/farm_debug_dialog.py's
job, since it also needs the current in-game day, which this controller has no way to know.

These are one-shot, explicitly user-triggered writes (button clicks, not a persistent background
hook), unlike every other memory-write feature in this project, so none of them get a Cheats
on/off toggle. Attaches its own script on the shared session (app/game_session.py), same pattern
as every other feature window.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .game_session import get_shared_session

AGENT_PATH = Path(__file__).resolve().parent.parent / "src" / "farm_tile_debug_agent.js"


class FarmTileDebugController(QObject):
    result = Signal(bool, str)  # (ok, message)
    export_ready = Signal(object)  # list of dicts, one per tracked tile (see plant_seed_row())

    def __init__(self, parent=None):
        super().__init__(parent)
        self._script = None
        self._game_session = None
        threading.Thread(target=self._attach_worker, daemon=True).start()

    def _attach_worker(self) -> None:
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session()  # background thread only, never Qt main
            script = session.create_script(AGENT_PATH.read_text())
            script.on("message", self._on_message)
            script.load()
        except Exception as exc:
            self.result.emit(False, f"Couldn't attach to the game: {exc}")
            return
        self._game_session = game_session
        self._script = script
        game_session.farm_grid_base_found.connect(self._on_farm_grid_base)
        if game_session.current_farm_grid_base:
            self._on_farm_grid_base(game_session.current_farm_grid_base)
        game_session.time_base_found.connect(self._on_time_base)
        if game_session.current_time_base:
            self._on_time_base(game_session.current_time_base)

    def _on_farm_grid_base(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setFarmGridBase", "address": address})

    def _on_time_base(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setTimeBase", "address": address})

    def _post(self, message_type: str) -> None:
        if self._script is None:
            self.result.emit(False, "Not attached to the game yet.")
            return
        self._script.post({"type": message_type})

    def clear_field(self) -> None:
        self._post("clearField")

    def till_soil(self) -> None:
        self._post("tillSoil")

    def water_tilled_soil(self) -> None:
        self._post("waterTilledSoil")

    def plant_crop_columns(self) -> None:
        self._post("plantCropColumns")

    def plant_season_crops(self) -> None:
        self._post("plantSeasonCrops")

    def plant_all_crop_stages(self) -> None:
        self._post("plantAllCropStages")

    def plant_seed_row(self) -> None:
        self._post("plantSeedRow")

    def export_field_data(self) -> None:
        self._post("exportFieldData")

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread -- only touch plain state here, marshal to Qt via
        # the Signal/emit call, same rule as every other window's own message handler.
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[farm tile debug] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "farmDebugToolResult":
            self.result.emit(bool(payload.get("ok")), payload.get("message", ""))
        elif kind == "exportFieldDataResult":
            if payload.get("ok"):
                self.export_ready.emit(payload["rows"])
            else:
                self.result.emit(False, f"Couldn't export field data: {payload.get('error')}")

    def cleanup(self) -> None:
        """Called once from the owning window's own cleanup(), same as any other feature's script."""
        script = self._script
        self._script = None
        if self._game_session is not None:
            try:
                self._game_session.farm_grid_base_found.disconnect(self._on_farm_grid_base)
            except (TypeError, RuntimeError):
                pass
            try:
                self._game_session.time_base_found.disconnect(self._on_time_base)
            except (TypeError, RuntimeError):
                pass
        self._game_session = None

        def detach() -> None:
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()
