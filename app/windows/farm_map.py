"""Farm map, as a dockable tab -- wraps src/farm_map.py's FarmMapWindow (the rendering, template
warp, and grid-line logic all live there and are reused unchanged) with the same attach/cleanup
plumbing already proven out in mine_map.py.

Grid_base detection is a single, centralized path: the shared session (app/game_session.py)
derives FarmGridBase = SaveDataBase + 0x78C4 (src/core_hooks_agent.js) the moment Save Data Base is
known and broadcasts it; this tab just receives it (`setFarmGridBase`) and uses it as-is, trusted
unconditionally (no shape check, no local call-site hooks of its own) -- see data/pointer_map.md's
"Farm tile array" section and src/farm_map_agent.js's own header comment for the full writeup. The
standalone CLI tool (src/farm_map.py, run directly rather than through this app) still has its own
manual address override for when it's attaching independently with no shared session to receive
FarmGridBase from.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.farm_map import DEFAULT_CORNERS, FarmMapWindow, parse_grid

from ..agent_loader import load_agent_source
from ..game_session import get_shared_session
from . import WindowSpec, register

AGENT_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "farm_map_agent.js"


class FarmMapWidget(QWidget):
    _attach_result = Signal(object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._script = None
        self._game_session = None
        self._attached = False
        self._tiles_ref = {"tiles": {}}
        self._player_ref = {"player": None}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._status_label = QLabel("Attaching to the game...")
        self._status_label.setStyleSheet("background:#1a1a1a; color:#e0e0e0; padding:4px;")
        layout.addWidget(self._status_label)

        # corners=dict(DEFAULT_CORNERS) so each tab gets its own copy -- FarmMapWindow mutates
        # self._corners in place when edit-layout is unlocked, and dicts are shared by reference.
        self._map = FarmMapWindow(
            self._tiles_ref, corners=dict(DEFAULT_CORNERS), player_ref=self._player_ref
        )
        layout.addWidget(self._map, stretch=1)

        self._attach_result.connect(self._on_attach_result)
        threading.Thread(target=self._attach_worker, daemon=True).start()

    def _attach_worker(self) -> None:
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session()  # blocks this background thread, not Qt's
            # tables=() -- farm_map_agent.js doesn't reference any lookup table today, but this
            # routes through the same shared loader every other window uses (see agent_loader.py),
            # so adding one later is just adding a table name here, not a whole new load path.
            agent_source = load_agent_source(AGENT_PATH, tables=())
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
        self._attached = True
        # The one automatic path (see module docstring) -- trusted as-is, no shape check.
        self._game_session.farm_grid_base_found.connect(self._on_shared_farm_grid_base)
        if self._game_session.current_farm_grid_base:
            self._on_shared_farm_grid_base(self._game_session.current_farm_grid_base)

        # Player-position marker (2026-09-10) -- needs Save Data Base for the Location field
        # (gating whether the dot shows at all) and the Entity Manager pointer (below) for the
        # entity X/Y source, identified by vtable match inside the agent itself, see
        # farm_map_agent.js.
        self._game_session.save_data_base_found.connect(self._on_shared_save_data_base)
        if self._game_session.current_save_data_base:
            self._on_shared_save_data_base(self._game_session.current_save_data_base)

        # Entity Manager pointer (for the player-position marker) is hooked once, centrally, in
        # src/core_hooks_agent.js -- this tab receives it rather than hooking exe+532A0 itself.
        self._game_session.entity_manager_found.connect(self._on_shared_entity_manager)
        if self._game_session.current_entity_manager:
            self._on_shared_entity_manager(self._game_session.current_entity_manager)

    def _on_shared_farm_grid_base(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setFarmGridBase", "address": address})
        self._status_label.setText("Attached.")

    def _on_shared_save_data_base(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setSaveDataBase", "address": address})

    def _on_shared_entity_manager(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setEntityManager", "address": address})

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread -- only touch plain state here, never widgets.
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[farm map] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "grid":
            if payload.get("ok"):
                raw = bytes.fromhex(payload["hex"])
                self._tiles_ref["tiles"] = parse_grid(raw)
            else:
                print(f"[farm map] failed to read grid: {payload.get('error')}")
        elif kind == "playerPos":
            self._player_ref["player"] = payload if payload.get("ok") else None
        elif kind == "status":
            print(f"[farm map] {payload['message']}")
            self._status_label.setText(payload["message"])

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
            # can take a real moment (or hang) -- that froze the whole app on close once already.
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()


register(WindowSpec("farm_map", "Farm Map", FarmMapWidget))
