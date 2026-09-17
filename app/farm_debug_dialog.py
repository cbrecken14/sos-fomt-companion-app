"""Dialog for the Reminders window's farm-tile debugging tools -- one button per action on
app/farm_tile_debug.py's FarmTileDebugController, plus a shared status line showing the
controller's last result. Not currently wired into the UI (see app/windows/reminders.py); kept as
a developer tool for verifying the farm tile struct. Stays open after an action runs so several
can be chained in one sitting (e.g. Clear Debris, then Till All Soil, then Water All Tilled Soil).
No confirmation prompts -- every action fires immediately on click, even though each one writes
directly to the running game's memory.

Includes a day-by-day growth-tracking experiment: "Plant One of Each Crop (Seed Stage)" plants one
seed-stage tile per crop-type id to be watered by hand every real day, and "Export Field Data"
prints one CSV block per click to the terminal (not the GUI) with every known field for those
tiles, headed by the current in-game day number. `get_header` is a zero-arg callable (passed in
from app/windows/reminders.py) returning the current REAL in-game header (year/season/day) or None
if the game isn't attached/ready yet -- deliberately not falling back to
app/reminders_mock_data.py's mock day the way most of the Reminders window does, since a fake day
number would corrupt the growth-tracking log.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import QDialog, QGroupBox, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from . import config, game_calendar
from .data_tables import load_table
from .farm_tile_debug import FarmTileDebugController

_GEOMETRY_KEY = "farm_debug/geometry"

# Sorted by crop-type id (not alphabetically) -- column N = crop-type id N in every one of this
# tool's column-planting actions, so listing crops in that same order matches what's seen walking
# the farm left to right.
_CROPS_BY_ID = sorted(load_table("crops")["CROPS"], key=lambda c: c["id"])
_CROP_NAME_BY_ID = {crop["id"]: crop["crop_name"] for crop in _CROPS_BY_ID}


class FarmDebugDialog(QDialog):
    def __init__(
        self,
        controller: FarmTileDebugController,
        get_header: Optional[Callable[[], object]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Farm Tile Debug Tools")
        self._restore_geometry()
        self._controller = controller
        self._get_header = get_header

        layout = QVBoxLayout(self)

        self._add_action(
            layout,
            "Clear Field",
            "Resets the ENTIRE farm to a blank, untilled map -- removes every debris object "
            "(weeds, stones, branches, fences, stumps, boulders), removes every crop regardless "
            "of growth stage, and un-tills all soil. Leaves nothing behind.",
            controller.clear_field,
        )
        self._add_action(
            layout,
            "Till All Soil",
            "Tills every farm tile (dry, not watered) and clears any existing crops. Only runs "
            "if the farm has no debris left -- Clear Field first if it refuses.",
            controller.till_soil,
        )
        self._add_action(
            layout,
            "Water All Tilled Soil",
            "Waters every already-tilled tile (dry or already wet). Untilled ground is left "
            "alone.",
            controller.water_tilled_soil,
        )
        self._add_action(
            layout,
            "Plant Crop Columns",
            "Clears and tills the ENTIRE farm, then plants one FULL-HEIGHT column per confirmed "
            "crop-type id 0-22 (23 columns, column N = id N) side by side starting at the farm's "
            "row-0/col-0 corner.",
            controller.plant_crop_columns,
        )
        self._add_action(
            layout,
            "Plant Season's Crops",
            "Tills the ENTIRE farm, then plants only the CURRENT season's crops, each in its own "
            "9x9 block with a 1-tile gap between blocks -- each block's tiles individually "
            "randomized for growth stage (1-5) and watered/dry state. For testing the Reminders "
            "window's crop-status tracking against a realistic, varied field. Only 8 blocks fit "
            "on the farm, so a season with more than 8 crops (Fall has 10) leaves its last few "
            "crop-type ids unplanted.",
            controller.plant_season_crops,
        )
        self._add_action(
            layout,
            "Plant All Crops - Stages 1-5",
            "Clears and tills the ENTIRE farm, then plants EVERY crop-type id 0-22 in its own "
            "column (starting at the farm's row-0/col-0 corner, column N = id N) at every growth "
            "stage 1-5, one stage per row -- so you can walk the columns and note which stages "
            "look visually identical for each crop. Also fills in the copy/paste template below.",
            self._plant_all_crop_stages,
        )
        self._add_action(
            layout,
            "Plant One of Each Crop (Seed Stage)",
            "Clears and tills the ENTIRE farm, then plants one seed-stage (dry, unwatered) tile "
            "per crop-type id 0-22 along row 0, starting at col 0 (col N = crop-type id N). Water "
            'each one yourself every real day, then click "Export Field Data" once per day to '
            "record its growth.",
            controller.plant_seed_row,
        )
        self._add_action(
            layout,
            "Export Field Data",
            "Reads every known field (stage, times-watered, tilled/watered state, and the three "
            "still-unidentified bytes) off the tiles planted by \"Plant One of Each Crop\" and "
            "prints a CSV block to the TERMINAL (not here), headed by the current in-game day "
            "number. Run once per real day after watering.",
            self._export_field_data,
        )

        self._report_box = QPlainTextEdit()
        self._report_box.setReadOnly(True)
        self._report_box.setPlaceholderText(
            'Click "Plant All Crops - Stages 1-5" to fill this in with one line per crop '
            '("Turnip - ") -- fill in the number of visually distinct stages you see for each '
            "and copy/paste it back."
        )
        layout.addWidget(self._report_box)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        controller.result.connect(self._on_result)
        controller.export_ready.connect(self._on_export_ready)

    def _plant_all_crop_stages(self) -> None:
        # The report is plain crop-name/id data, known locally -- no need to wait on the Frida
        # round trip (see _on_result for the separate, async write-confirmation status).
        lines = [f"{crop['crop_name']} - " for crop in _CROPS_BY_ID]
        self._report_box.setPlainText("\n".join(lines))
        self._controller.plant_all_crop_stages()

    def _export_field_data(self) -> None:
        self._controller.export_field_data()

    def _on_export_ready(self, rows: list) -> None:
        header = self._get_header() if self._get_header is not None else None
        if header is not None:
            day_number = game_calendar.day_index(header.year, header.season, header.day) + 1
            day_desc = f"Day {day_number} ({header.season} {header.day}, Year {header.year})"
        else:
            day_number = ""
            day_desc = "Day unknown -- game not attached/ready yet"

        lines = [
            f"=== Farm Crop Growth Export -- {day_desc} ===",
            "day,col,crop_id,crop_name,stage,times_watered,state,byte_0xD,byte_0xE,byte_0xF",
        ]
        # Sorted by column (the tile's fixed, physical position -- see plant_seed_row()), NOT by
        # whatever crop_id the tile's content currently reads. The two can diverge: a dead
        # out-of-season crop's tile can revert to untilled and later grow debris (content=24,
        # occupant=8) on its own, so "col" is the only stable way to know which planted crop a row
        # was originally about (confirmed by testing non-Spring crops in Spring).
        for row in sorted(rows, key=lambda r: r["col"]):
            crop_id = row["cropId"]
            name = _CROP_NAME_BY_ID.get(crop_id, f"Unknown (id {crop_id})")
            lines.append(
                f"{day_number},{row['col']},{crop_id},{name},{row['stage']},{row['timesWatered']},"
                f"{row['state']},{row['byteD']},{row['byteE']},{row['byteF']}"
            )
        print("\n".join(lines))

        self._status_label.setText(f"Exported field data for {day_desc} to the terminal.")
        self._status_label.setStyleSheet("")

    def _add_action(self, layout, title, description, handler) -> None:
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        box_layout.addWidget(desc_label)
        button = QPushButton(title)
        button.clicked.connect(handler)
        box_layout.addWidget(button)
        layout.addWidget(box)

    def _on_result(self, ok: bool, message: str) -> None:
        self._status_label.setText(message)
        self._status_label.setStyleSheet("" if ok else "color: red;")

    def _restore_geometry(self) -> None:
        geometry = config.get_settings().value(_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:
        config.get_settings().setValue(_GEOMETRY_KEY, self.saveGeometry())
        try:
            self._controller.result.disconnect(self._on_result)
        except (TypeError, RuntimeError):
            pass
        try:
            self._controller.export_ready.disconnect(self._on_export_ready)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
