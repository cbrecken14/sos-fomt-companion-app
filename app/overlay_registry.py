"""Registry of available OVERLAY windows (Fatigue Value today, more to come) -- separate from
app/windows/__init__.py's dock-tab registry, since overlay windows are independent floating
widgets that sit over the game, not QDockWidget tabs in the shell. A concrete overlay module
(see app/overlays/) registers itself by calling `register()` at import time, same pattern as
app/windows/__init__.py. The Overlay Settings tab (app/windows/overlay_settings.py) lists every
registered spec; app/overlay_manager.py owns one instance of each for the app's lifetime.
"""
from dataclasses import dataclass
from typing import Callable

from .overlay_window import OverlayWindow


@dataclass(frozen=True)
class OverlaySpec:
    id: str
    title: str
    factory: Callable[[], OverlayWindow]


_REGISTRY: dict[str, OverlaySpec] = {}


def register(spec: OverlaySpec) -> None:
    _REGISTRY[spec.id] = spec


def all_specs() -> list[OverlaySpec]:
    return sorted(_REGISTRY.values(), key=lambda s: s.title)
