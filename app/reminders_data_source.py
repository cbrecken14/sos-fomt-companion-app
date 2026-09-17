"""Shared attach/message/cleanup transport for every live-memory data source backing the Reminders
window (app/reminders_live_data.py, app/reminders_crop_data.py). Each data source owns its own
additional script on the app's shared Frida session (app/game_session.py), same pattern every
other feature window uses for its own extra fields -- but before this file existed, each of the 11
classes hand-carried an identical copy of that attach/signal-wiring/message-envelope/cleanup
boilerplate, differing only in which shared base address it needs (Save Data Base, Time Base, or
Farm Grid Base) and what it does with its own payload. This factors the identical part out once.

A subclass sets AGENT_PATH (and optionally AGENT_TABLES/LOG_NAME) plus the three BASE_* class
attributes naming which GameSession signal/attribute/message-type it needs (see the three constants
below), and overrides `_on_payload(kind, payload)` to decode its own message kind(s) -- exactly the
real, non-boilerplate part of each original class -- into its own state, ending with
`self.updated.emit()`. Everything else (data-readiness flags, dataclasses, accessor methods like
`sprites()`/`value()`/`job()`) stays in the subclass; that's each data source's own real API, not
boilerplate.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .agent_loader import load_agent_source
from .game_session import get_shared_session

# The three shared base addresses a Reminders data source can key off of -- each names a
# (Signal attribute, "current value" attribute, message type) triple on GameSession/the agent side.
SAVE_DATA_BASE = ("save_data_base_found", "current_save_data_base", "setSaveDataBase")
TIME_BASE = ("time_base_found", "current_time_base", "setTimeBase")
FARM_GRID_BASE = ("farm_grid_base_found", "current_farm_grid_base", "setFarmGridBase")


class RemindersDataSource(QObject):
    updated = Signal()
    status = Signal(str)

    AGENT_PATH: Path
    AGENT_TABLES: tuple[str, ...] = ()
    LOG_NAME: str = "reminders live data"
    BASE = SAVE_DATA_BASE  # override with TIME_BASE/FARM_GRID_BASE where needed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._script = None
        self._game_session = None
        threading.Thread(target=self._attach_worker, daemon=True).start()

    def _attach_worker(self) -> None:
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session()  # background thread only, never Qt main
            if self.AGENT_TABLES:
                agent_source = load_agent_source(self.AGENT_PATH, tables=self.AGENT_TABLES)
            else:
                agent_source = self.AGENT_PATH.read_text()
            script = session.create_script(agent_source)
            script.on("message", self._on_message)
            script.load()
        except Exception as exc:
            self.status.emit(f"Couldn't attach to the game: {exc}")
            return
        self._game_session = game_session
        self._script = script
        signal_attr, current_attr, _ = self.BASE
        getattr(game_session, signal_attr).connect(self._on_base_found)
        # Covers the case where the base was already found (e.g. another window got there first)
        # before this script even finished loading.
        current = getattr(game_session, current_attr)
        if current:
            self._on_base_found(current)

    def _on_base_found(self, address: str) -> None:
        if self._script is not None:
            _, _, message_type = self.BASE
            self._script.post({"type": message_type, "address": address})

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread -- only touch plain state here, marshal to Qt via
        # Signal.emit(), same rule as every other window's own message handler in this project.
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[{self.LOG_NAME}] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "status":
            self.status.emit(payload["message"])
            return
        self._on_payload(kind, payload)

    def _on_payload(self, kind: Optional[str], payload: dict) -> None:
        """Override to decode this data source's own payload kind(s) into local state, ending with
        self.updated.emit() on success -- the real, subclass-specific part of each data source."""

    def cleanup(self) -> None:
        """Only unloads this data source's OWN script -- the shared session itself is app-lifetime,
        detached once from the shell on app shutdown (see game_session.shutdown_shared_session)."""
        script = self._script
        self._script = None
        if self._game_session is not None:
            signal_attr, _, _ = self.BASE
            try:
                getattr(self._game_session, signal_attr).disconnect(self._on_base_found)
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
