"""Shared attach/enable/teardown lifecycle for every memory-WRITE "cheat" controller in this
project (Coin Spawn Count, Guaranteed Ground Item Spawn, Tired Animations, Harvest Sprite
Auto-Minigame). Every write feature defaults off and is driven by set_enabled() rather than
attaching unconditionally like a read-only feature window -- before this file existed, each cheat
controller hand-carried its own copy of that same enable/disable state machine (attach on a
background thread, tear back down if toggled off again mid-attach, unload on cleanup()); this
factors that identical ~60 lines out to one place.

A subclass only needs to set AGENT_PATH/LOG_NAME/CHEAT_LABEL and override whichever of the hooks
below its cheat actually needs:
- `_on_script_loaded(script)` -- post an initial state message right after load, before the
  enabled-check (e.g. Coin Spawn Count pushing its current slider value). Default: no-op.
- `_on_attached(game_session)` -- wire up any shared-session signals once attached and enabled
  (e.g. Harvest Sprite connecting to save_data_base_found). Default: no-op.
- `_on_detached()` -- undo whatever `_on_attached` wired up. Default: no-op.
- `_on_payload(kind, payload)` -- handle a cheat-specific message kind; return True if handled, so
  the default `status` handling below is skipped for it. Default: never handles anything.

Every subclass keeps its own extra live-adjustable methods (set_count(), set_disabled_animations(),
complete_minigame(), ...) as-is -- those aren't boilerplate, they're each cheat's own real API.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .agent_loader import load_agent_source
from .game_session import get_shared_session


class CheatController(QObject):
    status = Signal(str)

    AGENT_PATH: Path
    AGENT_TABLES: tuple[str, ...] = ()
    LOG_NAME: str = "cheat"
    CHEAT_LABEL: str = "the cheat"

    def __init__(self):
        super().__init__()
        self._script = None
        self._enabled = False

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            threading.Thread(target=self._attach_worker, daemon=True).start()
        else:
            self._teardown()

    def _attach_worker(self) -> None:
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session()  # blocks this background thread, not Qt's
            if self.AGENT_TABLES:
                agent_source = load_agent_source(self.AGENT_PATH, tables=self.AGENT_TABLES)
            else:
                agent_source = self.AGENT_PATH.read_text()
            script = session.create_script(agent_source)
            script.on("message", self._on_message)
            script.load()
            self._on_script_loaded(script)
        except Exception as exc:
            self.status.emit(f"Couldn't arm {self.CHEAT_LABEL}: {exc}")
            return
        if not self._enabled:
            # Toggled off again while this was still attaching -- tear back down right away.
            try:
                script.unload()
            except Exception:
                pass
            return
        self._script = script
        self._on_attached(game_session)

    def _on_script_loaded(self, script) -> None:
        """Override to post an initial state message right after load (e.g. setCoinCount)."""

    def _on_attached(self, game_session) -> None:
        """Override to wire up shared-session signals (e.g. save_data_base_found)."""

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread -- only touch plain state here, marshal to Qt via
        # the Signal/emit calls, same rule as every other window's own message handler.
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[{self.LOG_NAME}] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if self._on_payload(kind, payload):
            return
        if kind == "status":
            self.status.emit(payload.get("message", str(payload)))

    def _on_payload(self, kind: Optional[str], payload: dict) -> bool:
        """Override for cheat-specific payload kinds. Return True once handled, to skip the
        default `status` handling above for that message."""
        return False

    def _teardown(self) -> None:
        script = self._script
        self._script = None
        self._on_detached()

        def detach() -> None:
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()

    def _on_detached(self) -> None:
        """Override to undo whatever _on_attached wired up (disconnect signals, reset state)."""

    def cleanup(self) -> None:
        """Called once from the shell on app shutdown, same as any other feature's script."""
        if self._enabled:
            self._enabled = False
            self._teardown()
