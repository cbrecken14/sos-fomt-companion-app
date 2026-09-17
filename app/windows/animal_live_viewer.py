"""Animal Viewer: live per-animal position/facing view, kept independent from Animal Viewer
(Static Data)'s own field list (animal_viewer.py/animal_viewer_agent.js) -- even though Facing
Direction/Age/X/Y mathematically resolve to small negative offsets from the roster's own Name
(Secondary) origin, they're tracked here only, not merged into the static tab.

This tab's own field list covers Age/X/Y/Facing (negative offsets) plus Name (Secondary)/Name
(duplicated from the roster for a self-contained picture) -- see data/pointer_map.md's "Animal
Viewer (Live Data) investigation" section for the field derivation.

Structurally almost identical to animal_viewer.py (same matrix widget, same message protocol,
same column shape) -- only real differences are AGENT_PATH (points at
src/animal_live_viewer_agent.js) and loading the agent source through load_agent_source() so
ENUMS.FACING is available for the Facing Direction field. Since this window now includes a "Name"
field (the primary Name, duplicated from the roster), the empty-slot-hiding behavior inherited from
animal_viewer.py's code activates here too -- a column hides until its Name cell reports an animal.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
from ..game_session import get_shared_session
from ..widgets import CopyableTableWidget
from . import WindowSpec, register

AGENT_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "animal_live_viewer_agent.js"

UNKNOWN_ROW_COLOR = QColor(Qt.GlobalColor.gray)
UNVERIFIED_ROW_COLOR = QColor(200, 140, 40)

FLASH_COLOR = QColor(220, 45, 45)
FLASH_DURATION_S = 1.2
FLASH_TICK_MS = 40

# A batch this big can only be a bulk resync (first poll after (re-)attach, or a Save Data Base
# change forcing a full resend) -- see animal_live_viewer_agent.js's poll(). Flashing thousands of
# cells at once is both meaningless (nothing actually "just changed" from the player's perspective)
# and the real cost of the old per-cell-message loading lag, so bulk batches skip the flash effect
# entirely; normal small live-update batches (a moving animal's X/Y/Facing) still flash as before.
BULK_UPDATE_FLASH_THRESHOLD = 20

# Cells applied per event-loop turn during a bulk resync (see _apply_cell_batch_chunk) -- small
# enough that the rest of the app stays responsive between chunks, big enough that a resync of a
# few thousand cells doesn't take an excessive number of turns to finish.
BULK_UPDATE_CHUNK_SIZE = 200


class AnimalLiveViewerWidget(QWidget):
    _attach_result = Signal(object, object, str)
    _schema_ready = Signal(list, list)
    _cell_batch = Signal(list)
    _status_update = Signal(str)
    _base_update = Signal(str)
    _column_bases_ready = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._script = None
        self._game_session = None
        self._items: list[list[QTableWidgetItem]] = []
        self._flashing: dict[int, tuple[QTableWidgetItem, float]] = {}
        self._name_row_index: int | None = None
        self._row_offsets: list[int] = []
        self._column_bases: list[str | None] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        base_row = QHBoxLayout()
        base_row.addWidget(QLabel("Save Data Base:"))
        self._base_field = QLineEdit()
        self._base_field.setReadOnly(True)
        self._base_field.setPlaceholderText("not found yet")
        base_row.addWidget(self._base_field)
        layout.addLayout(base_row)

        self._status_label = QLabel("Attaching to the game...")
        self._status_label.setStyleSheet("background:#1a1a1a; color:#e0e0e0; padding:4px;")
        layout.addWidget(self._status_label)

        # Separate from _status_label on purpose: the "status" messages below (save data base
        # received, etc.) arrive and overwrite _status_label's text almost immediately, which would
        # otherwise clobber a "Loading data..." message before anyone could read it. This label is
        # only ever touched by _on_shared_save_data_base (show) and _on_cell_batch (hide once the
        # resulting bulk batch is actually applied to the table) -- see BULK_UPDATE_FLASH_THRESHOLD.
        self._loading_label = QLabel("Loading data...")
        self._loading_label.setStyleSheet("color:#4a90d9; font-style: italic; padding: 0 4px 4px 4px;")
        self._loading_label.hide()
        layout.addWidget(self._loading_label)

        self._table = CopyableTableWidget(0, 0)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._table, stretch=1)

        self._attach_result.connect(self._on_attach_result)
        self._schema_ready.connect(self._on_schema_ready)
        self._cell_batch.connect(self._on_cell_batch)
        self._status_update.connect(self._status_label.setText)
        self._base_update.connect(self._base_field.setText)
        self._column_bases_ready.connect(self._on_column_bases_ready)
        threading.Thread(target=self._attach_worker, daemon=True).start()

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._tick_flashes)
        self._flash_timer.start(FLASH_TICK_MS)

    def _attach_worker(self) -> None:
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session()  # blocks this background thread, not Qt's
            agent_source = load_agent_source(AGENT_PATH, tables=("enums", "animal_live_viewer"))
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
        # Push whatever the shared session already knows immediately -- covers the case where Save
        # Data Base was found before this tab's own script even finished loading.
        if self._game_session.current_save_data_base:
            self._on_shared_save_data_base(self._game_session.current_save_data_base)

    def _on_shared_save_data_base(self, address: str) -> None:
        self._base_update.emit(address)
        # Posting this always makes the agent clear its diff cache and resend every cell (see
        # animal_live_viewer_agent.js's useSaveDataBase), so a bulk batch is always coming next --
        # show the loading indicator now rather than waiting for the batch to actually arrive.
        self._loading_label.show()
        if self._script is not None:
            self._script.post({"type": "setSaveDataBase", "address": address})

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread -- only touch plain state here, marshal to Qt via
        # the Signal/emit calls above, same rule as every other window's own message handler.
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[animal live viewer] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "schema":
            self._schema_ready.emit(payload["rows"], payload["columns"])
        elif kind == "cellBatch":
            self._cell_batch.emit(payload["updates"])
        elif kind == "status":
            self._status_update.emit(payload["message"])
        elif kind == "columnBases":
            self._column_bases_ready.emit(payload["bases"])
        elif kind == "bytesDump":
            # Printed directly here (not marshaled through a Signal) -- plain console output, not
            # a Qt widget update, same as the frida-error print a few lines up.
            self._print_hex_dump(payload["label"], payload["address"], payload["bytes"])

    def _on_schema_ready(self, rows: list, columns: list) -> None:
        self._table.setRowCount(len(rows))
        self._table.setColumnCount(len(columns))
        self._table.setHorizontalHeaderLabels([c["label"] for c in columns])

        self._name_row_index = next((i for i, r in enumerate(rows) if r["label"] == "Name"), None)
        self._row_offsets = [r["offset"] for r in rows]

        self._items = []
        for row_index, row in enumerate(rows):
            header_item = QTableWidgetItem(row["label"])
            if not row["known"]:
                header_item.setForeground(UNKNOWN_ROW_COLOR)
            elif not row["verified"]:
                header_item.setForeground(UNVERIFIED_ROW_COLOR)
            self._table.setVerticalHeaderItem(row_index, header_item)

            row_items = []
            for col_index in range(len(columns)):
                item = QTableWidgetItem()
                self._table.setItem(row_index, col_index, item)
                row_items.append(item)
            self._items.append(row_items)

        # This array has no known "Name" field, so _name_row_index stays None and every slot
        # column stays visible -- occupancy can't be determined yet, unlike the roster tab.
        if self._name_row_index is not None:
            for col_index in range(len(columns)):
                self._table.setColumnHidden(col_index, True)

    def _on_cell_batch(self, updates: list) -> None:
        # One Qt-thread hop for the whole poll tick's worth of changes instead of one per cell (see
        # animal_live_viewer_agent.js's poll()). setUpdatesEnabled(False) consolidates every
        # item.setText()/setBackground() in this batch into a single repaint instead of one per
        # item -- the other half of the old loading-lag cost, on top of the signal-per-cell flood.
        suppress_flash = len(updates) > BULK_UPDATE_FLASH_THRESHOLD
        self._table.setUpdatesEnabled(False)
        if suppress_flash:
            # A bulk resync (thousands of cells) applied in one synchronous call held the Qt event
            # loop -- and the GIL -- for its whole duration, so no other Python slot anywhere in the
            # app (switching tabs, opening a menu) could run until it returned; the whole app looked
            # frozen, not just this tab. Qt widgets can only be touched from the main thread, so the
            # fix isn't a background thread -- it's applying the batch in small chunks, each
            # rescheduled via QTimer.singleShot(0, ...) instead of looping straight through. That
            # hands control back to the event loop between chunks so queued UI input actually gets
            # processed while this tab is still loading.
            self._apply_cell_batch_chunk(updates, 0)
        else:
            self._apply_updates(updates, suppress_flash=False)
            self._table.setUpdatesEnabled(True)

    def _apply_cell_batch_chunk(self, updates: list, start: int) -> None:
        end = min(start + BULK_UPDATE_CHUNK_SIZE, len(updates))
        self._apply_updates(updates[start:end], suppress_flash=True)
        if end < len(updates):
            QTimer.singleShot(0, lambda: self._apply_cell_batch_chunk(updates, end))
        else:
            self._table.setUpdatesEnabled(True)
            self._loading_label.hide()

    def _apply_updates(self, updates: list, suppress_flash: bool) -> None:
        for update in updates:
            row_index = update["rowIndex"]
            col_index = update["colIndex"]
            raw = update["raw"]
            calculated = update.get("calculated")
            item = self._items[row_index][col_index]
            item.setText(str(calculated) if calculated else str(raw))
            if not suppress_flash:
                self._flash(item)
            if row_index == self._name_row_index:
                self._table.setColumnHidden(col_index, not bool(raw))

    def _on_column_bases_ready(self, bases: list) -> None:
        self._column_bases = bases

    def _cell_address(self, row_index: int, col_index: int) -> str | None:
        if col_index >= len(self._column_bases) or row_index >= len(self._row_offsets):
            return None
        base = self._column_bases[col_index]
        if base is None:
            return None
        return hex(int(base, 16) + self._row_offsets[row_index])

    def _show_context_menu(self, pos) -> None:
        item = self._table.itemAt(pos)
        if item is None:
            return
        col_index = item.column()
        address = self._cell_address(item.row(), col_index)

        menu = QMenu(self)
        copy_action = menu.addAction("Copy Address")
        copy_action.setEnabled(address is not None)
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == copy_action and address is not None:
            QApplication.clipboard().setText(address)

    @staticmethod
    def _print_hex_dump(label: str, address: str, byte_values: list[int]) -> None:
        # Dumps an animal's raw bytes to the console for diffing before/after an in-game action.
        print(f"[animal live viewer] Dump: {label} @ {address} ({len(byte_values)} bytes)")
        for offset in range(0, len(byte_values), 16):
            chunk = byte_values[offset:offset + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            print(f"  +0x{offset:04X}  {hex_part:<47}  {ascii_part}")

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

    def cleanup(self) -> None:
        """Called by the shell when this tab is actually closed (not just switched away from).

        Only unloads this tab's OWN script -- `self._session` is the app's shared GameSession
        session, not owned by this tab, so it must NOT be detached here (see memory_viewer.py's
        identical note)."""
        script = self._script
        self._session, self._script = None, None

        def detach() -> None:
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()


register(WindowSpec("animal_live_viewer", "Animal Viewer", AnimalLiveViewerWidget, debug=True))
