"""Live farm crop-status data for the Reminders window's "Crop Status" section and future-harvest
predictions -- see src/reminders_crop_status_agent.js. Attaches its own script
on the shared session's derived farm grid base (same pattern as every class in
app/reminders_live_data.py, and the same grid base app/farm_tile_debug.py's debug tools use).

Tracks, per planted crop-type id, how many tiles still need watering today, how many are ready to
harvest (occupant == 5) right now, and how many are planted in total -- plus, for every tile still
growing (not yet at stage 5), predicts how many more REAL WATERED DAYS until it reaches stage 5 by
walking data/tables/crop_growth.json's row forward from that tile's own +0xC times-watered value
(confirmed live, day-by-day, 2026-09-13 -- see data/pointer_map.md's "Growth table" section: this
assumes the tile keeps getting watered every remaining day, since a day it isn't watered doesn't
advance +0xC at all). Retired the old mock "Water Crops" daily-reminder line and "Crop Harvest"
future events (app/reminders_mock_data.py) now that both are real.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .data_tables import load_table
from .reminders_data_source import FARM_GRID_BASE, RemindersDataSource

AGENT_PATH = Path(__file__).resolve().parent.parent / "src" / "reminders_crop_status_agent.js"

_CROPS = load_table("crops")["CROPS"]
_CROP_NAME_BY_ID = {c["id"]: c["crop_name"] for c in _CROPS if c["id"] is not None}
_GROWTH_TABLE = load_table("crop_growth")["GROWTH_TABLE"] # {"0": [1,1,2,2,5,0,...],...}
_HARVEST_READY_STAGE = 5
# Grass (content==23) isn't a real crop -- it's rendered directly on untilled ground (state==0,
# see data/pointer_map.md's "Tile struct fields" section) and has no row in data/tables/crops.json,
# so it needs a name here instead of falling through to "Unknown crop". The agent already never
# counts it toward needsWater/needsHarvest -- still shown in Crop Status for
# its total-planted count, just always 0 for those two.
_GRASS_CONTENT_ID = 23
_GRASS_NAME = "Grass"


def _crop_name_for(crop_id: int) -> str:
    if crop_id == _GRASS_CONTENT_ID:
        return _GRASS_NAME
    return _CROP_NAME_BY_ID.get(crop_id, f"Unknown crop (id {crop_id})")


def _days_until_ready(crop_id: int, times_watered: int) -> Optional[int]:
    """How many more WATERED days (not calendar days) until this crop reaches stage 5, given its
    current times-watered value -- walks data/tables/crop_growth.json's row forward rather than
    assuming a flat rate, since growth curves aren't linear. None if the crop id or a stage-5 entry
    isn't found (shouldn't happen for any of the 23 confirmed crops, but this is live game data)."""
    row = _GROWTH_TABLE.get(str(crop_id))
    if row is None:
        return None
    for index in range(times_watered, len(row)):
        if row[index] >= _HARVEST_READY_STAGE:
            return index - times_watered
    return None


@dataclass
class CropStatusRow:
    crop_id: int
    crop_name: str
    total: int
    needs_water: int
    needs_harvest: int


@dataclass
class FutureHarvestEntry:
    day_offset: int # 1 = tomorrow, assuming watered every remaining day
    crop_id: int
    crop_name: str
    count: int


class RemindersCropData(RemindersDataSource):
    AGENT_PATH = AGENT_PATH
    LOG_NAME = "reminders crop data"
    BASE = FARM_GRID_BASE

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready = False
        self._rows: list[CropStatusRow] = []
        self._future_entries: list[FutureHarvestEntry] = []

    def _on_payload(self, kind, payload) -> None:
        if kind != "cropStatus":
            return
        if not payload.get("ok"):
            self.status.emit(f"Couldn't read crop status: {payload.get('error')}")
            return

        rows = []
        for crop_id_str, counts in payload["counts"].items():
            crop_id = int(crop_id_str)
            name = _crop_name_for(crop_id)
            rows.append(CropStatusRow(
                crop_id=crop_id, crop_name=name,
                total=counts["total"], needs_water=counts["needsWater"],
                needs_harvest=counts["needsHarvest"],
            ))
        rows.sort(key=lambda r: r.crop_name)

        future_counts: dict[tuple[int, int], int] = defaultdict(int)
        for item in payload.get("growing", []):
            days = _days_until_ready(item["cropId"], item["timesWatered"])
            if not days or days <= 0:
                continue # unknown crop id, or already ready (shouldn't happen -- guarded anyway)
            future_counts[(days, item["cropId"])] += 1

        future_entries = [
            FutureHarvestEntry(
                day_offset=day_offset, crop_id=crop_id,
                crop_name=_crop_name_for(crop_id),
                count=count,
            )
            for (day_offset, crop_id), count in future_counts.items()
        ]
        future_entries.sort(key=lambda e: (e.day_offset, e.crop_name))

        self._rows = rows
        self._future_entries = future_entries
        self._ready = True
        self.updated.emit()

    def is_ready(self) -> bool:
        return self._ready

    def rows(self) -> list[CropStatusRow]:
        return self._rows

    def future_harvest_entries(self) -> list[FutureHarvestEntry]:
        return self._future_entries
