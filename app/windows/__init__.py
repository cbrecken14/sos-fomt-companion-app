"""Registry of available feature windows, shown in the shell's "+" menu.

A feature window module registers itself by calling `register()` at import time (see any module
under this package for the pattern, e.g. harvest_sprite_viewer.py for a simple one). The shell only needs
to import each window module once (for the registration side effect) and can then look everything
else up by id.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class WindowSpec:
    id: str
    title: str
    factory: Callable[[], QWidget]
    # Raw memory-viewing/testing tools (Memory Viewer, Animal/Animal Live/Marriage
    # Candidate/Villager/Harvest Sprite Viewers, ...) vs. real gameplay-facing windows. Gated in
    # the shell's "Add Window" menu behind the "Show Memory Viewing Windows" cheat -- see
    # app/cheats_dialog.py.
    debug: bool = False


_REGISTRY: dict[str, WindowSpec] = {}

# Explicit "Add Window" menu order: standard (non-debug) windows first, then debug windows, each
# group in this listed order. Edit this list to reorder the menu. Anything registered but not
# listed here falls in alphabetically after its group.
_MENU_ORDER = [
    "reminders",
    "overlay_settings",
    "farm_map",
    "mine_floor_map",
    "memory_viewer",
    "animal_live_viewer",
    "villagers",
    "marriage_candidates",
    "harvest_sprite_viewer",
]


def register(spec: WindowSpec) -> None:
    _REGISTRY[spec.id] = spec


def all_specs() -> list[WindowSpec]:
    def sort_key(spec: WindowSpec):
        if spec.id in _MENU_ORDER:
            return (0, _MENU_ORDER.index(spec.id))
        return (1, spec.title)

    return sorted(_REGISTRY.values(), key=sort_key)


def get_spec(window_id: str) -> Optional[WindowSpec]:
    return _REGISTRY.get(window_id)
