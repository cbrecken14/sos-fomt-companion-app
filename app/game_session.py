"""Shared, app-lifetime Frida session + "core" hooks (Save Data Base, Time Base, the Entity
Manager pointer, and derived Farm grid / Mine Floor bases) -- one attach point for the whole app
instead of every feature window
re-deriving the same hooks independently, reducing redundant reads/hooks across windows. Before
this, save_data_reader_agent.js, time_reader_agent.js, and memory_viewer_agent.js each carried
their own identical copy of the Save Data / Time hook-finding logic, and Mine Map's own
independent attach duplicated the mine floor grid-base hook a second time (fixed 2026-09-08, once
two independent fresh-restart sessions confirmed MineFloorBase/TimeBase are both fixed offsets
from Save Data Base -- see src/core_hooks_agent.js's derivation comment).

`src/core_hooks_agent.js` does the actual hooking -- see that file for the derivation of each
address. Windows that need one of these addresses connect to the matching Signal below (and can
read the `current_*` attribute immediately for a value already found before they connected).
Windows that also need to read their OWN additional fields off one of these bases (e.g. Memory
Viewer's animal roster rows, Farm Map's tile grid) create their OWN script via
`get_shared_session().wait_for_session().create_script(...)` -- reusing this same attached session
instead of a separate `frida.attach()` call -- and receive the resolved address via
`script.post(...)` rather than hooking it a second time.

Saved Addresses isn't migrated to this (2026-09-08) -- it doesn't attach to the game at all, just
reads config/hook_cache.json off disk, so there's nothing to migrate.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import frida
from PySide6.QtCore import QObject, Signal

from . import hook_cache
from .agent_loader import load_agent_source

PROCESS_NAME = "STORY OF SEASONS Friends of Mineral Town.exe"
AGENT_PATH = Path(__file__).resolve().parent.parent / "src" / "core_hooks_agent.js"

SAVE_DATA_BASE_CACHE_KEY = "save_data_base_ptr"
FARM_GRID_BASE_CACHE_KEY = "farm_map_grid_base"  # same key farm_map.py's own detection already uses

ATTACH_POLL_INTERVAL_S = 0.05


class GameSession(QObject):
    save_data_base_found = Signal(str)
    time_base_found = Signal(str)
    farm_grid_base_found = Signal(str)
    mine_floor_base_found = Signal(str)
    entity_manager_found = Signal(str)
    player_location_changed = Signal(int)
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self.session: Optional["frida.core.Session"] = None
        self._script = None
        self.current_save_data_base: Optional[str] = None
        self.current_time_base: Optional[str] = None
        self.current_farm_grid_base: Optional[str] = None
        self.current_mine_floor_base: Optional[str] = None
        self.current_entity_manager: Optional[str] = None
        self.current_player_location: Optional[int] = None
        threading.Thread(target=self._attach_worker, daemon=True).start()

    def _attach_worker(self) -> None:
        try:
            session = frida.attach(PROCESS_NAME)
            agent_source = load_agent_source(AGENT_PATH, tables=("enums",))
            cached_save_data = hook_cache.get(SAVE_DATA_BASE_CACHE_KEY)
            agent_source = agent_source.replace(
                "SAVE_DATA_BASE_OVERRIDE", f'ptr("{cached_save_data}")' if cached_save_data else "null"
            )
            script = session.create_script(agent_source)
            script.on("message", self._on_message)
            script.load()
        except Exception as exc:
            self.status.emit(f"Couldn't attach to the game: {exc}")
            return
        self._script = script
        self.session = session  # set last -- wait_for_session() polls this to know attach is done

    def wait_for_session(self) -> "frida.core.Session":
        """Blocks the CALLING thread until the shared session is attached -- never call this from
        the Qt main thread (same rule as every other window's own Frida attach in this project)."""
        while self.session is None:
            time.sleep(ATTACH_POLL_INTERVAL_S)
        return self.session

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread. Signal.emit() here is safe -- every receiver is a
        # QObject method living on the Qt main thread, so Qt auto-queues the call, same pattern
        # already used by every window's own message handler in this project.
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[game session] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "saveDataBaseFound":
            hook_cache.set(SAVE_DATA_BASE_CACHE_KEY, payload["address"])
            self.current_save_data_base = payload["address"]
            self.save_data_base_found.emit(payload["address"])
        elif kind == "timeBaseFound":
            # No hook_cache entry (2026-09-16, removed alongside Time Base's old direct hook) --
            # it's now always instantly re-derived from Save Data Base's own offset the moment
            # Save Data Base itself is known, so there's nothing a cache would save waiting for.
            self.current_time_base = payload["address"]
            self.time_base_found.emit(payload["address"])
        elif kind == "farmGridBaseFound":
            hook_cache.set(FARM_GRID_BASE_CACHE_KEY, payload["address"])
            self.current_farm_grid_base = payload["address"]
            self.farm_grid_base_found.emit(payload["address"])
        elif kind == "mineFloorBaseFound":
            # No hook_cache entry -- unlike the others, this isn't session-stable (a new floor, or
            # even the same floor regenerating, can allocate a new one), so persisting it across
            # restarts would just mean starting from a stale guess. Just re-derived every save load.
            self.current_mine_floor_base = payload["address"]
            self.mine_floor_base_found.emit(payload["address"])
        elif kind == "entityManagerFound":
            # No hook_cache entry -- same reasoning as Mine Floor Base: this is a live object
            # pointer, not stable across restarts, so persisting it would just be a stale guess.
            self.current_entity_manager = payload["address"]
            self.entity_manager_found.emit(payload["address"])
        elif kind == "playerLocationChanged":
            # No hook_cache entry -- a live, constantly-changing value, nothing to persist.
            self.current_player_location = payload["locationId"]
            self.player_location_changed.emit(payload["locationId"])
        elif kind == "status":
            print(f"[game session] {payload['message']}")
            self.status.emit(payload["message"])

    def cleanup(self) -> None:
        """Detaches the shared session -- an app-lifetime resource, so this is called once from
        the shell on app shutdown (see shutdown_shared_session()), never per-window like every
        other feature window's own cleanup()."""
        session, script = self.session, self._script
        self.session, self._script = None, None

        def detach() -> None:
            # Never block the Qt main thread on a Frida call that can take a moment (or hang) --
            # same reasoning as every other cleanup() in this project.
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass
            try:
                if session is not None:
                    session.detach()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()


_shared: Optional[GameSession] = None
# Guards creation of _shared -- windows attach from their own background threads (see
# memory_viewer.py/farm_map.py's _attach_worker), and if two of them call get_shared_session()
# at nearly the same moment (e.g. both restored from a saved session on app startup) without this
# lock, both could see _shared as None and each create their own separate GameSession, defeating
# the whole point of sharing one attach point.
_shared_lock = threading.Lock()


def get_shared_session() -> GameSession:
    """Returns the app's one shared GameSession, creating and attaching it on first call."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = GameSession()
        return _shared


def shutdown_shared_session() -> None:
    """Call once from the shell on app shutdown. A no-op if the shared session was never created
    (e.g. the app closed before any window that needed it was ever opened)."""
    global _shared
    if _shared is not None:
        _shared.cleanup()
        _shared = None
