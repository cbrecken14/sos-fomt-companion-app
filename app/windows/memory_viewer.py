"""Memory Viewer: a dockable tab showing every currently-known field across the Save Data struct
(Money, player stats) and the Time struct (Day/Season/Weather/Hour/Minute), plus the "gap" bytes
(4-byte granularity) between known fields -- useful for watching raw values change live in-game and
identifying what an unlabeled field is for, without needing Cheat Engine open.

Deliberately doesn't try to cover the whole 74536-byte Save Data struct -- see
src/memory_viewer_agent.js for why (most of it is still unexplored, and dumping all of it would be
tens of thousands of rows). Only fills gaps between fields already documented in
data/pointer_map.md.

Save Data Base, Time Base, and Mine Floor Base are no longer found by this tab's own hooks
(2026-09-08) -- all three come from the app's one shared Frida session (app/game_session.py,
src/core_hooks_agent.js), which this tab creates its own additional script on top of (still
reading its own extensive field catalog -- Save Data/Time/mine floor rows -- just no longer
re-deriving any of the three base addresses itself). This tab used to carry its own copy of the
mine floor grid-base hook, duplicating Mine Map's identical hook, until Mine Floor Base was
confirmed to be a fixed offset from Save Data Base. Same attach/cleanup plumbing as
mine_map.py/farm_map.py otherwise, except this tab's own `cleanup()` only unloads its own script --
the shared session itself is app-lifetime, torn down once from the shell, not per-tab.

Coop/Barn animal rows moved out to the Animal Viewer tab (2026-09-08) -- see
app/windows/animal_viewer.py.
"""
from __future__ import annotations

import bisect
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..agent_loader import load_agent_source
from ..data_tables import load_table
from ..game_session import get_shared_session
from ..widgets import CopyableTableWidget
from . import WindowSpec, register

AGENT_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "memory_viewer_agent.js"
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "memory_snapshot.txt"

COLUMNS = ["Struct", "Name", "Raw Value", "Calculated", "Offset (Dec)", "Offset", "Address", "Exe Offset"]
DEFAULT_COLUMN_WIDTHS = [70, 220, 90, 140, 90, 70, 130, 100]
UNKNOWN_ROW_COLOR = QColor(Qt.GlobalColor.gray)
# Fields data/pointer_map.md itself flags as "found manually... not yet independently
# re-confirmed" -- e.g. Facing Direction turned out not to change while walking (2026-09-07),
# exactly the kind of thing this color is meant to flag as lower-confidence than the rest.
UNVERIFIED_ROW_COLOR = QColor(200, 140, 40)

FLASH_COLOR = QColor(220, 45, 45)
FLASH_DURATION_S = 1.2
FLASH_TICK_MS = 40

# A batch this big can only be a bulk resync (first poll after (re-)attach, or a base address
# change forcing a full resend of that struct's rows) -- see memory_viewer_agent.js's
# startPolling(). Flashing/inserting hundreds of rows at once is both meaningless and the real
# cost of the old per-row-message loading lag, so bulk batches skip the flash effect; normal small
# live-update batches still flash as before.
BULK_UPDATE_FLASH_THRESHOLD = 20

# Rows applied per event-loop turn during a bulk resync (see _apply_row_batch_chunk) -- small
# enough that the rest of the app stays responsive between chunks, big enough that a resync of a
# few hundred rows doesn't take an excessive number of turns to finish.
BULK_UPDATE_CHUNK_SIZE = 50

# Sort key for the table's one supported ordering: "offset from the Save Data Base" -- saveHeader
# and saveData share priority 0 because both already report their
# offset relative to Save Data Base (see the JS agent's baseOffsetFromSaveData), so priority-0
# rows end up correctly interleaved by real Save-Data-Base offset (saveHeader's low offsets before
# saveData's Money at 0xbc50, etc.). time/mineFloor have no Save-Data-Base-relative offset at all
# (separate allocations) -- they form their own trailing groups, each ordered by their own base's
# offset. Lives in data/tables/memory_viewer.json now (single source, see app/data_tables.py).
STRUCT_PRIORITY = load_table("memory_viewer")["STRUCT_PRIORITY"]


class MemoryViewerWidget(QWidget):
    _attach_result = Signal(object, object, str)
    _row_batch = Signal(list)
    _status_update = Signal(str)
    _base_update = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._script = None
        self._game_session = None
        self._items: dict[tuple[str, str], dict[str, QTableWidgetItem]] = {}
        self._row_keys: list[tuple[int, int]] = []  # parallel to table rows -- see _apply_row_update
        self._flashing: dict[int, tuple[QTableWidgetItem, float]] = {}
        self._search_text = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter by name, struct, address, exe offset...")
        self._search_box.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_box, stretch=1)
        layout.addLayout(search_row)

        # Persistent reference to each struct's own base address -- the transient status label
        # shows this too when a base is first found, but gets overwritten by later status
        # messages. Kept here (read-only, so it's easy to select/copy) to combine with any row's
        # Offset column and hand-compute an address for something not currently tracked as its own
        # row.
        bases_row = QHBoxLayout()
        self._base_fields: dict[str, QLineEdit] = {}
        for struct_key, label in (("saveData", "Save Data Base:"), ("time", "Time Base:"), ("mineFloor", "Mine Floor Base:")):
            bases_row.addWidget(QLabel(label))
            field = QLineEdit()
            field.setReadOnly(True)
            field.setPlaceholderText("not found yet")
            bases_row.addWidget(field)
            self._base_fields[struct_key] = field
        layout.addLayout(bases_row)

        status_row = QHBoxLayout()
        self._status_label = QLabel("Attaching to the game...")
        self._status_label.setStyleSheet("background:#1a1a1a; color:#e0e0e0; padding:4px;")
        status_row.addWidget(self._status_label, stretch=1)
        layout.addLayout(status_row)

        # Separate from _status_label on purpose -- see animal_live_viewer.py's identical note.
        self._loading_label = QLabel("Loading data...")
        self._loading_label.setStyleSheet("color:#4a90d9; font-style: italic; padding: 0 4px 4px 4px;")
        self._loading_label.hide()
        layout.addWidget(self._loading_label)

        self._table = CopyableTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        header = self._table.horizontalHeader()
        # Interactive (not ResizeToContents/Stretch) is the only mode that lets column borders be
        # dragged to resize by hand -- the other modes look similar but silently disable that.
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate(DEFAULT_COLUMN_WIDTHS):
            self._table.setColumnWidth(col, width)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        # No Qt-side sorting -- there's only one supported final ordering, offset from Save Data
        # Base, not interactive click-to-sort. _apply_row_update keeps the table in
        # that order itself via a manual bisect insert (see STRUCT_PRIORITY above), which is also much
        # cheaper than Qt's setSortingEnabled(True): the old approach toggled sorting off/on around
        # every single new row, and every re-enable triggered a full-table resort -- on first
        # attach, every row arrives at once (the JS-side diff cache starts empty), so that was a
        # resort-per-row storm across hundreds of rows.
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._table, stretch=1)

        self._attach_result.connect(self._on_attach_result)
        self._row_batch.connect(self._on_row_batch)
        self._status_update.connect(self._status_label.setText)
        self._base_update.connect(self._on_base_update)
        threading.Thread(target=self._attach_worker, daemon=True).start()

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._tick_flashes)
        self._flash_timer.start(FLASH_TICK_MS)

    def _attach_worker(self) -> None:
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session()  # blocks this background thread, not Qt's
            agent_source = load_agent_source(AGENT_PATH, tables=("enums", "memory_viewer"))
            script = session.create_script(agent_source)
            script.on("message", self._on_message)
            script.load()
        except Exception as exc:
            self._attach_result.emit(None, None, str(exc))
            return
        self._game_session = game_session
        self._attach_result.emit(session, script, "")

    def _on_attach_result(self, session, script, error_message: str) -> None:
        if error_message:
            self._status_label.setText(f"Couldn't attach to the game: {error_message}")
            return
        self._session = session
        self._script = script
        self._game_session.save_data_base_found.connect(self._on_shared_save_data_base)
        self._game_session.time_base_found.connect(self._on_shared_time_base)
        self._game_session.mine_floor_base_found.connect(self._on_shared_mine_floor_base)
        # Push whatever the shared session already knows immediately -- covers the case where Save
        # Data / Time / Mine Floor were found before this tab's own script even finished loading.
        if self._game_session.current_save_data_base:
            self._on_shared_save_data_base(self._game_session.current_save_data_base)
        if self._game_session.current_time_base:
            self._on_shared_time_base(self._game_session.current_time_base)
        if self._game_session.current_mine_floor_base:
            self._on_shared_mine_floor_base(self._game_session.current_mine_floor_base)

    def _on_shared_save_data_base(self, address: str) -> None:
        self._on_base_update("saveData", address)
        # Posting this always makes the agent clear its saveData/saveHeader diff cache and resend
        # every row in both, so a bulk batch is always coming next -- show the loading indicator.
        self._loading_label.show()
        if self._script is not None:
            self._script.post({"type": "setSaveDataBase", "address": address, "fromCache": False})

    def _on_shared_time_base(self, address: str) -> None:
        self._on_base_update("time", address)
        self._loading_label.show()
        if self._script is not None:
            self._script.post({"type": "setTimeBase", "address": address, "fromCache": False})

    def _on_shared_mine_floor_base(self, address: str) -> None:
        # No _on_base_update() call here, unlike the other two -- the "Mine Floor Base:" field and
        # status line are already driven by this script's own "mineFloorBaseFound" message (see
        # _on_message), sent from useMineFloorBase() once it accepts this candidate.
        self._loading_label.show()
        if self._script is not None:
            self._script.post({"type": "setMineFloorBase", "address": address})

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread -- only touch plain state here, never widgets
        # directly (the Signal/emit calls above marshal back to the Qt main thread safely).
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[memory viewer] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "rowBatch":
            self._row_batch.emit(payload["updates"])
        elif kind == "mineFloorBaseFound":
            self._base_update.emit("mineFloor", payload["address"])
            self._status_update.emit(payload["message"])
        elif kind == "status":
            self._status_update.emit(payload["message"])
        elif kind == "horseBytesDump":
            # Printed directly here (not marshaled through a Signal) -- plain console output, not
            # a Qt widget update, same as the frida-error print a few lines up.
            for dump in payload["dumps"]:
                self._print_hex_dump(dump["label"], dump["address"], dump["bytes"])

    def _on_row_batch(self, updates: list) -> None:
        # One Qt-thread hop for the whole poll tick's worth of changes instead of one per row. See
        # animal_live_viewer.py's identical method for the full reasoning (signal-per-row flood +
        # per-row insert storm + the GIL/event-loop freeze from applying a bulk batch in one go).
        suppress_flash = len(updates) > BULK_UPDATE_FLASH_THRESHOLD
        self._table.setUpdatesEnabled(False)
        if suppress_flash:
            self._apply_row_batch_chunk(updates, 0)
        else:
            self._apply_updates(updates, suppress_flash=False)
            self._table.setUpdatesEnabled(True)

    def _apply_row_batch_chunk(self, updates: list, start: int) -> None:
        end = min(start + BULK_UPDATE_CHUNK_SIZE, len(updates))
        self._apply_updates(updates[start:end], suppress_flash=True)
        if end < len(updates):
            QTimer.singleShot(0, lambda: self._apply_row_batch_chunk(updates, end))
        else:
            self._table.setUpdatesEnabled(True)
            self._loading_label.hide()

    def _apply_updates(self, updates: list, suppress_flash: bool) -> None:
        for update in updates:
            self._apply_row_update(
                update["struct"],
                update["name"],
                update["known"],
                update["verified"],
                update["offset"],
                update["address"],
                update["exeOffset"],
                update["raw"],
                update.get("calculated"),
                suppress_flash,
            )

    def _apply_row_update(
        self,
        struct: str,
        name: str,
        known: bool,
        verified: bool,
        offset: str,
        address: str,
        exe_offset: str,
        raw,
        calculated,
        suppress_flash: bool,
    ) -> None:
        key = (struct, name)
        items = self._items.get(key)
        is_new_row = items is None
        if is_new_row:
            offset_int = int(offset, 16)
            # Keep the table ordered by (STRUCT_PRIORITY[struct], offset) -- "offset from the Save
            # Data Base" for saveHeader/saveData, own-base offset for time/mineFloor (see
            # STRUCT_PRIORITY above) -- via a plain sorted insert instead of Qt's sort machinery.
            sort_key = (STRUCT_PRIORITY.get(struct, 99), offset_int)
            row = bisect.bisect_right(self._row_keys, sort_key)
            self._row_keys.insert(row, sort_key)
            self._table.insertRow(row)
            struct_item = QTableWidgetItem(struct)
            name_item = QTableWidgetItem(name)
            raw_item = QTableWidgetItem()
            calc_item = QTableWidgetItem()
            # Offset arrives as a hex string (e.g. "0x17c", or "-0x8" for the time struct's
            # negative offsets) -- shown alongside plain decimal too, since decimal reads more
            # easily for "how far away is this."
            offset_dec_item = QTableWidgetItem(str(offset_int))
            offset_item = QTableWidgetItem(offset)
            addr_item = QTableWidgetItem(address)
            exe_offset_item = QTableWidgetItem(exe_offset)
            for col, item in enumerate(
                [struct_item, name_item, raw_item, calc_item, offset_dec_item, offset_item, addr_item, exe_offset_item]
            ):
                self._table.setItem(row, col, item)
            items = {
                "name": name_item,
                "raw": raw_item,
                "calc": calc_item,
                "offset_dec": offset_dec_item,
                "offset": offset_item,
                "address": addr_item,
                "exe_offset": exe_offset_item,
            }
            self._items[key] = items
        if not known:
            items["name"].setForeground(UNKNOWN_ROW_COLOR)
            items["raw"].setForeground(UNKNOWN_ROW_COLOR)
        elif not verified:
            items["name"].setForeground(UNVERIFIED_ROW_COLOR)
        if not is_new_row and not suppress_flash:
            # Only flash on an actual update, not the row's first appearance -- otherwise every
            # row would flash red the moment the tab attaches, which isn't a meaningful signal.
            self._flash(items["raw"])
            if calculated:
                self._flash(items["calc"])
        items["raw"].setText(str(raw))
        items["calc"].setText(str(calculated) if calculated else "")
        # Address can change if the base is re-found after a reload -- doesn't affect this row's
        # position (that's fixed by offset, which never changes), so a plain setText is enough.
        items["address"].setText(address)
        items["exe_offset"].setText(exe_offset)
        self._apply_filter_to_row(items["name"].row())

    def _on_base_update(self, struct: str, address: str) -> None:
        field = self._base_fields.get(struct)
        if field is not None:
            field.setText(address)

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.lower()
        for row in range(self._table.rowCount()):
            self._apply_filter_to_row(row)

    def _apply_filter_to_row(self, row: int) -> None:
        if not self._search_text:
            self._table.setRowHidden(row, False)
            return
        matches = any(
            item is not None and self._search_text in item.text().lower()
            for item in (self._table.item(row, col) for col in range(len(COLUMNS)))
        )
        self._table.setRowHidden(row, not matches)

    def _flash(self, item: QTableWidgetItem) -> None:
        # QTableWidgetItem has no __hash__ in PySide6 (TypeError: unhashable type), so key by
        # id(item) instead and keep the item object itself in the value alongside the timestamp.
        self._flashing[id(item)] = (item, time.monotonic())
        item.setBackground(QBrush(FLASH_COLOR))

    def _tick_flashes(self) -> None:
        if not self._flashing:
            return
        now = time.monotonic()
        done = []
        for key, (item, start) in self._flashing.items():
            fraction = (now - start) / FLASH_DURATION_S
            if fraction >= 1.0:
                done.append(key)
                continue
            color = QColor(FLASH_COLOR)
            color.setAlphaF(max(0.0, 1.0 - fraction))
            item.setBackground(QBrush(color))
        for key in done:
            item, _ = self._flashing.pop(key)
            item.setData(Qt.ItemDataRole.BackgroundRole, None)  # back to the table's default look

    def _show_context_menu(self, pos) -> None:
        item = self._table.itemAt(pos)
        if item is None:
            return
        if item not in self._table.selectedItems():
            self._table.setCurrentItem(item)
        menu = QMenu(self)
        copy_action = menu.addAction("Copy")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == copy_action:
            self._table.copy_selection()

    def _save_snapshot(self) -> None:
        """Writes every current row to a plain text file, for sharing what the Memory Viewer
        currently shows without copying a large table by hand."""
        lines = [f"Memory Viewer snapshot -- {datetime.now().isoformat(timespec='seconds')}", ""]
        for row in range(self._table.rowCount()):
            values = [self._table.item(row, col).text() if self._table.item(row, col) else "" for col in range(len(COLUMNS))]
            struct, name, raw, calculated, offset_dec, offset, address, exe_offset = values
            line = f"[{struct}] {name} = {raw}"
            if calculated:
                line += f" ({calculated})"
            line += f"  @ {address} (offset {offset} / {offset_dec} decimal, {exe_offset})"
            lines.append(line)
        SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
        SNAPSHOT_PATH.write_text("\n".join(lines), encoding="utf-8")
        self._status_label.setText(f"Snapshot saved to {SNAPSHOT_PATH}")

    def _dump_horse_bytes(self) -> None:
        if self._script is not None:
            self._script.post({"type": "dumpHorseBytes"})

    @staticmethod
    def _print_hex_dump(label: str, address: str, byte_values: list[int]) -> None:
        # Dumps raw bytes to the console for diffing before/after an in-game action.
        print(f"[memory viewer] Dump: {label} @ {address} ({len(byte_values)} bytes)")
        for offset in range(0, len(byte_values), 16):
            chunk = byte_values[offset:offset + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            print(f"  +0x{offset:04X}  {hex_part:<47}  {ascii_part}")

    def cleanup(self) -> None:
        """Called by the shell when this tab is actually closed (not just switched away from).

        Only unloads this tab's OWN script -- `self._session` is the app's shared GameSession
        session now (see app/game_session.py), not owned by this tab, so it must NOT be detached
        here or every other window sharing it would break. The shared session itself is torn down
        once from the shell on actual app shutdown.
        """
        script = self._script
        self._session, self._script = None, None

        def detach() -> None:
            # Same reasoning as mine_map.py: never block the Qt main thread on a Frida call that
            # can take a real moment (or hang).
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()


register(WindowSpec("memory_viewer", "Memory Viewer", MemoryViewerWidget, debug=True))
