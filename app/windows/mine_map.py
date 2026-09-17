"""Mine floor map, ported from src/floor_map.py (a working Tkinter prototype) to a native Qt
widget so it can live as a tab in the app shell.

Reuses src/floor_map_agent.js (one addition: a cached/validated player-position hook offset, see
_attach_worker below) -- the Frida agent doesn't know or care which GUI toolkit is asking it for
data. Only the Python-side rendering and event loop moved: Tkinter's Canvas -> a QWidget with a
custom paintEvent, Tkinter's root.after() timers -> a single QTimer.

Player-position dot (2026-09-11): WORLD_UNITS_PER_TILE (below) is confirmed live at 10.0, same as
the farm's -- an initial disassembly-derived guess of 16.0 (plus a fixed origin) turned out to
apply to a different position struct than the one floor_map_agent.js's own hook reads, see that
constant's own comment. There's no universal fixed origin either: two different-shaped rooms
measured live needed two different origins, confirming each mine room instance sits at its own
spot in the game's own world space. So the origin is auto-anchored per floor from the spawn tile
(MineMapWidget._on_message's 'spawnTile'/'position' handling) -- an earlier version of this made
the dot visibly jump/"teleport" right after a floor loaded, because the OLD floor's origin was
still in effect (and being drawn) for the moment before the new one arrived; fixed by resetting the
origin to "unknown" (hiding the dot) the instant a new floor is detected, rather than leaving the
stale one in place to jump from. No manual button/keypress either way.

Position source (2026-09-16): reads the player's Live Entity X/Y (floor_map_agent.js's own
findPlayerEntity(), same Entity Manager + vtable-match technique farm_map_agent.js uses for its
marker), not floor_map_agent.js's older write-hook/poll position struct. Switched after confirming
live that the write-hook source only updates once the player actually takes a step -- a freshly
loaded floor's dot didn't anchor until that first step happened, sometimes not until the NEXT floor
transition. The Live Entity source reflects the rendered position continuously, so the anchor fires
the instant a floor loads. _on_message only accepts 'entity'-sourced position messages
(payload["src"] == "entity") -- the write-hook is still running and still sending its own
'write'/'poll' messages (unchanged, ignored here) since src/floor_map.py, the frozen Tkinter
fallback, still depends on them. The facing-direction line reuses the same Live Entity read.

Anchor race, part 1 (2026-09-16): switching to the Live Entity source above exposed a bug --
confirmed live across 3 consecutive floors that the anchor could pair the CURRENT floor's real
entity position with the PREVIOUS floor's still-unconsumed spawn tile, one floor behind, every
transition (the periodic entity poll's dedup-on-change send could go out before this floor's own
'spawnTile' message arrived). Fixed by having the 'spawnTile' handler explicitly request ("post")
a forced fresh entity-position read the moment it updates spawn_row/spawn_col, rather than relying
on whichever position sample happens to already be in flight -- see floor_map_agent.js's
requestEntityPosition listener.

Anchor race, part 2 (2026-09-16): that fix itself turned out to have its own timing bug, seen only
on the largest floor size, confirmed live via console ordering on a floor 19->20 transition:
'spawn tile detected' (and the resulting 'dot anchored') both printed BEFORE 'area transition hook
processed'/the location broadcast for floor 20. The floor-generation function that fires
'spawnTile' runs (and completes) BEFORE the game actually relocates the player to the new floor --
so requesting the entity's position the instant 'spawnTile' fires can still sample the PREVIOUS
floor's leftover coordinates, paired against the NEW floor's real spawn tile. Bigger floors take
longer to generate, widening this window, which is why it showed up there specifically. Fixed by
moving the request to MineMapWidget._on_player_location instead -- GameSession's
player_location_changed signal is the one thing confirmed to fire only after relocation actually
happens, making it the safe moment to sample. The 'spawnTile' handler still records
spawn_row/spawn_col and hides the dot, it just no longer requests the read itself.

src/floor_map.py itself is left in place, untouched, as a known-working fallback until this port
has been tried live.

Attaches via the app's one shared Frida session (app/game_session.py) rather than its own
`frida.attach()` call, same shared-session pattern every attaching window uses. grid_base comes
from the shared session's derived Mine
Floor Base (SaveDataBase + a fixed offset, see src/core_hooks_agent.js), re-applied on every zone
transition in either mine -- see floor_map_agent.js's listenForSetDerivedMineFloorBase. Real floor
size (rows/cols) is derived from the tile buffer's own data -- see infer_floor_size() below -- not
from a hook. The spawn-tile hook and the position pattern-scan stay local to floor_map_agent.js.
"""
from __future__ import annotations

import math
import struct
import threading
import time
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, hook_cache
from ..agent_loader import load_agent_source
from ..data_tables import load_table
from ..game_session import get_shared_session
from ..widgets import CopyableTableWidget

from . import WindowSpec, register

AGENT_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "floor_map_agent.js"
ORE_ICON_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "ore_icons"

# The player-position pattern scan is the slow part of attaching (a few seconds) -- see
# hook_cache.py and _attach_worker below for how the found offset gets cached and re-validated.
POSITION_HOOK_CACHE_KEY = "mine_map_position_hook_offset"

# Drag-to-resize split between the map and Floor Contents (2026-09-16) -- see the QSplitter set up
# in __init__. Persisted the same way shell.py persists the main window's own dock layout.
SPLITTER_STATE_KEY = "mine_map/splitter_state"
DEFAULT_SPLITTER_SIZES = [520, 220]

TILE_STRIDE = 0x0C
ROW_STRIDE = 0x150
CELL_SIZE = 24
MAX_ROWS = 28 # confirmed max buffer size -- always fetch this many rows regardless of the
# floor's real size, same as src/floor_map.py.

# World-space -> tile-space conversion. The disassembly-derived formula (UNITS=16.0, fixed origin
# -16.0/56.0 -- hand-traced from exe+31C7A0's tile-interaction code, see git history for the full
# derivation) turned out NOT to apply to this position source: floor_map_agent.js's own
# position hook reads a DIFFERENT struct than the {location,x,y} one exe+31C7A0 uses, and it's
# evidently in a different coordinate frame entirely -- not just a different origin, a different
# scale too (confirmed live 2026-09-11 via the "Export Position to Console" button: 10 real tiles
# moved the reported World X by ~99.2, and 4 real tiles moved World Y by ~41.3 -- both land right on
# 10.0 units/tile, not 16.0, matching the farm's own constant).
#
# Since this hook's frame doesn't match the tile-interaction formula, there's no known fixed origin
# to compute analytically -- confirmed live 2026-09-11: two different rooms each needed a different
# origin to line up (e.g. one measured at World X/Y -60.085105895996094/-25.258935928344727 at its
# own top-left tile, another needing a origin around -64.9 for the same axis) -- each mine room
# instance evidently sits at its own spot in the game's own world space, so a single fixed constant
# can't work across floors. The origin is auto-anchored instead, per floor, from the spawn tile
# (MineMapWidget._on_message's 'spawnTile'/'position' handling) -- see _MineMapState.calibration_x's
# comment for how the dot avoids visibly jumping when this fires.
WORLD_UNITS_PER_TILE = 10.0

# Mine Floor 0 special case: unlike every deeper floor, Floor 0 has no ladder entrance
# of its own -- you walk straight into it from Mother's Hill -- and that specific transition proved
# unreliable for the normal spawn-tile/entity-position anchor pipeline below (confirmed live: the
# entity position isn't consistently valid yet at the exact moment that transition happens, and two
# separate fresh-launch attempts to read Floor 0's own spawn tile came back with two DIFFERENT
# values, meaning the readings themselves were contaminated by that same timing issue rather than
# reflecting a real per-session-varying spawn point). Confirmed live that Floor 0
# is otherwise a fixed layout (always the small map, player always spawns at the bottom-center
# tile) -- so rather than depending on that unreliable pipeline for this one floor, its calibration
# is hardcoded from a clean "Export Position to Console" reading taken on a fresh launch, before
# touching any ladder: floor_rows=6, floor_cols=13 (bottom-center tile = row 5, col 6),
# World X=0.0, World Y=30.0 at that tile. Applied the instant GameSession broadcasts a transition
# into this location (see _on_player_location below) -- confirmed reliable across both a ladder-
# down-and-back-up return trip AND a cold app launch, unlike the spawnTile hook.
MINE_FLOOR_ZERO_LOCATION_ID = 61
MINE_FLOOR_ZERO_SPAWN_ROW = 5
MINE_FLOOR_ZERO_SPAWN_COL = 6
MINE_FLOOR_ZERO_SPAWN_WORLD_X = 0.0
MINE_FLOOR_ZERO_SPAWN_WORLD_Y = 30.0

# Special Floors: which mine and floor number the player is currently on, derived straight from
# GameSession's player_location_changed broadcast (already tracked in
# _MineMapState.current_location for the Floor 0 special case above) -- no new hook needed.
# Spring Mine base 61, 256 floors (confirmed live at floor 190 = location 251);
# Lake Mine base 317, 255 floors. See data/pointer_map.md's "Current location ID enum" section.
# Lake's own range has an unresolved 1-floor question: 317 + 255 = 572, one past the documented
# 571 -- meaning either the wiki's "floor 255" entries here are actually memory floor 254, or the
# documented upper bound itself is one short. Not yet reconciled live either way.
SPRING_MINE_LOCATION_BASE = 61
SPRING_MINE_MAX_FLOOR = 255
LAKE_MINE_LOCATION_BASE = 317
LAKE_MINE_MAX_FLOOR = 255


def mine_and_floor_from_location(location_id: int) -> tuple[str, int] | None:
    if SPRING_MINE_LOCATION_BASE <= location_id <= SPRING_MINE_LOCATION_BASE + SPRING_MINE_MAX_FLOOR:
        return "Spring", location_id - SPRING_MINE_LOCATION_BASE
    if LAKE_MINE_LOCATION_BASE <= location_id <= LAKE_MINE_LOCATION_BASE + LAKE_MINE_MAX_FLOOR:
        return "Lake", location_id - LAKE_MINE_LOCATION_BASE
    return None


WALL_MARGIN_TILES = 1.0
WALL_COLOR = QColor("#4a3728")
MARGIN_PX = int(WALL_MARGIN_TILES * CELL_SIZE)

# Facing indicator: same idea as farm_map.py's (a short line from the dot, normalized to a fixed
# logical-tile length so it doesn't change size with the grid's own scale), just drawn directly in
# pixel space here since the mine map has no perspective transform to go through.
FACING_INDICATOR_LENGTH_TILES = 0.35
FACING_INDICATOR_COLOR = QColor("#2f6fed")
FACING_INDICATOR_WIDTH = 2.0

# Icon/color lookup tables live in data/tables/mine_map.json now (single source, see
# app/data_tables.py) -- src/floor_map.py keeps its own separate inline copy untouched, as a
# frozen fallback that isn't extended alongside this file.
_MINE_MAP_TABLE = load_table("mine_map")
SOIL_CONTENT = {
    int(key): (label, QColor(color)) for key, (label, color) in _MINE_MAP_TABLE["SOIL_CONTENT"].items()
}
HOLE_STATES = set(_MINE_MAP_TABLE["HOLE_STATES"])
ROCK_COLOR = QColor("#888888")

# 23/32 = Mythic Ore / Goddess Jewel rock-content ids (see data/pointer_map.md).
RARE_FLASH_IDS = set(_MINE_MAP_TABLE["RARE_FLASH_IDS"])
MYTHIC_FLASH_COLORS = (QColor("#fff200"), QColor("#ff2b2b"))
MYTHIC_FLASH_MS = 400

# Moonstone still unconfirmed -- see src/floor_map.py.
ORE_ICONS = {int(key): value for key, value in _MINE_MAP_TABLE["ORE_ICONS"].items()}
SOIL_ICONS = {int(key): value for key, value in _MINE_MAP_TABLE["SOIL_ICONS"].items()}

# Floor Contents: display names/sell prices for the rock/soil content ids that have
# been identified so far (Spring Mine ores confirmed early on; Lake Mine's own gem ids -- 19, 20,
# 24-31, 35-39 -- confirmed live the first time this feature was tested against that mine). An id
# showing up in compute_floor_contents() but missing from ORE_NAMES/SOIL_NAMES is
# shown as "Unknown" rather than dropped -- that's what surfaces a not-yet-identified id in the
# first place, same way this batch of Lake Mine ids got found. Sell prices are wiki-sourced
# (fogu.com); an id with no entry in ORE_SELL_PRICES/SOIL_SELL_PRICES isn't missing data, it's not
# sellable at all (Goddess Jewel can't be shipped; Coins are picked up directly as money, never a
# shippable inventory item).
ORE_NAMES = {int(key): value for key, value in _MINE_MAP_TABLE["ORE_NAMES"].items()}
SOIL_NAMES = {int(key): value for key, value in _MINE_MAP_TABLE["SOIL_NAMES"].items()}
ORE_SELL_PRICES = {int(key): value for key, value in _MINE_MAP_TABLE["ORE_SELL_PRICES"].items()}
SOIL_SELL_PRICES = {int(key): value for key, value in _MINE_MAP_TABLE["SOIL_SELL_PRICES"].items()}

# Pits: the map's "X" hole markers (HOLE_STATES tiles below, state 5/6) aren't
# identified by rock_content or soil_content at all -- a hole IS the tile's `state`, a field
# entirely separate from both content fields, and a hole tile is never also a rock tile (state 4)
# the way a ground item can be hidden under one, so there's no "covered" variant to speak of. To
# still fit the existing (category, content_id) settings/order/flash machinery every
# tillable item needs, without pretending a hole has a real memory-derived id, it gets a reserved
# negative pseudo-id -- every real content id the game itself produces is a small non-negative
# integer (see ORE_NAMES/SOIL_NAMES above), so a negative one can never collide with one. Added
# directly here (not in mine_map.json) since it isn't wiki-sourced item data like everything else
# in that table -- it's a Python-side bookkeeping id, and the JSON table should stay pure fact.
PIT_CONTENT_ID = -1
SOIL_NAMES[PIT_CONTENT_ID] = "Pit"

# rock_content 0 = Empty Rock (a real "nothing" state, not an item) and soil_content 0 = no ground
# item -- both excluded from tallying no matter what. soil_content 1 is the mine's own hidden/found
# ladder marker -- excluded too, it's floor infrastructure (how you leave this floor), not
# something to collect, and its own dedicated rendering already covers it (see SOIL_CONTENT above).
EXCLUDED_ROCK_IDS = {0}
EXCLUDED_SOIL_IDS = {0, 1}

# Floor Contents Settings (2026-09-16): per-item show/hide, persisted the same
# getter/setter-function-over-QSettings pattern as cheats_dialog.py. Only identified items
# (ORE_NAMES/SOIL_NAMES) ever get a key here -- see compute_floor_contents()'s docstring for why an
# unidentified id has nothing to toggle.
_ITEM_VISIBLE_KEY_TEMPLATE = "mine_map/item_visible/{category}/{id}"

# Dialog size/position -- same saveGeometry()/restoreGeometry()-over-QSettings
# pattern as harvest_sprite_minigame.py's own dialog.
_FLOOR_CONTENTS_SETTINGS_GEOMETRY_KEY = "mine_map/floor_contents_settings_geometry"


def _item_visible(category: str, content_id: int) -> bool:
    value = config.get_settings().value(
        _ITEM_VISIBLE_KEY_TEMPLATE.format(category=category, id=content_id), True
    )
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _set_item_visible(category: str, content_id: int, visible: bool) -> None:
    config.get_settings().setValue(
        _ITEM_VISIBLE_KEY_TEMPLATE.format(category=category, id=content_id), visible
    )


def _hidden_item_ids() -> set[tuple[str, int]]:
    hidden = {("ore", content_id) for content_id in ORE_NAMES if not _item_visible("ore", content_id)}
    hidden.update(
        ("soil", content_id) for content_id in SOIL_NAMES if not _item_visible("soil", content_id)
    )
    return hidden


# Item order: lets the Floor Contents Settings dialog's Up/Down-button lists
# put e.g. Gold above Mithril even though its id is lower. Two independent orders, one per
# category -- the settings dialog shows Ores and Ground Items as two separate sections (an
# earlier single combined/interleaved list used drag-and-drop reordering, found unreliable in
# practice -- replaced with the same Up/Down-button pattern
# reminders_settings.py's section reordering already uses). The main Floor Contents table still
# shows one list overall -- _combined_item_order() below is just ore-block-then-soil-block, each
# block in its own persisted order; there's no longer a way to interleave a specific ore between
# two specific ground items, which is an acceptable simplification for a cosmetic ordering
# preference like this one.
_ORE_ITEM_ORDER_KEY = "mine_map/ore_item_order"
_SOIL_ITEM_ORDER_KEY = "mine_map/soil_item_order"


def _default_order_by_id(names: dict[int, str]) -> list[int]:
    # Highest id first -- corrected from an earlier highest-price-first version, which didn't
    # match the intended "highest to lowest" ordering.
    return sorted(names, reverse=True)


def _resolve_item_order(saved, known: set[int], default: list[int]) -> list[int]:
    if not saved:
        return default
    if isinstance(saved, str):
        saved = [saved] # QSettings collapses a 1-item list back to a bare string
    order: list[int] = []
    seen: set[int] = set()
    for entry in saved:
        try:
            content_id = int(entry)
        except (ValueError, TypeError):
            continue
        if content_id in known and content_id not in seen:
            order.append(content_id)
            seen.add(content_id)
    # Anything known but missing from a saved order (e.g. a newly-added ore id from an app update
    # that predates this particular save) is appended at the end, default order, rather than
    # silently vanishing from the settings dialog and the main table.
    for content_id in default:
        if content_id not in seen:
            order.append(content_id)
            seen.add(content_id)
    return order


def _ore_item_order() -> list[int]:
    return _resolve_item_order(
        config.get_settings().value(_ORE_ITEM_ORDER_KEY), set(ORE_NAMES), _default_order_by_id(ORE_NAMES)
    )


def _soil_item_order() -> list[int]:
    return _resolve_item_order(
        config.get_settings().value(_SOIL_ITEM_ORDER_KEY), set(SOIL_NAMES), _default_order_by_id(SOIL_NAMES)
    )


def _set_ore_item_order(order: list[int]) -> None:
    config.get_settings().setValue(_ORE_ITEM_ORDER_KEY, [str(content_id) for content_id in order])


def _set_soil_item_order(order: list[int]) -> None:
    config.get_settings().setValue(_SOIL_ITEM_ORDER_KEY, [str(content_id) for content_id in order])


def _combined_item_order() -> list[tuple[str, int]]:
    # Ground items first, then ores -- swapped from the original ore-first order; matches the
    # same order the Settings dialog's two sections show in (see
    # _FloorContentsSettingsDialog.__init__).
    return [("soil", content_id) for content_id in _soil_item_order()] + [
        ("ore", content_id) for content_id in _ore_item_order()
    ]


# Flash Covered / Flash Red: per-ITEM map-rendering toggles, not global ones
# (an earlier version was one blanket on/off pair for "any covered item" -- replaced with per-item
# control instead, e.g. only Mythic Ore flashing red, not every ground item hidden under a rock).
# Both off by default. Lives in the same settings dialog as Floor Contents' item list settings even
# though these affect the MAP, not the list, for a single place to configure both.
_ITEM_FLASH_COVERED_KEY_TEMPLATE = "mine_map/item_flash_covered/{category}/{id}"
_ITEM_FLASH_RED_KEY_TEMPLATE = "mine_map/item_flash_red/{category}/{id}"


def _item_flash_covered(category: str, content_id: int) -> bool:
    value = config.get_settings().value(
        _ITEM_FLASH_COVERED_KEY_TEMPLATE.format(category=category, id=content_id), False
    )
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _set_item_flash_covered(category: str, content_id: int, enabled: bool) -> None:
    config.get_settings().setValue(
        _ITEM_FLASH_COVERED_KEY_TEMPLATE.format(category=category, id=content_id), enabled
    )


def _item_flash_red(category: str, content_id: int) -> bool:
    value = config.get_settings().value(
        _ITEM_FLASH_RED_KEY_TEMPLATE.format(category=category, id=content_id), False
    )
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _set_item_flash_red(category: str, content_id: int, enabled: bool) -> None:
    config.get_settings().setValue(
        _ITEM_FLASH_RED_KEY_TEMPLATE.format(category=category, id=content_id), enabled
    )


def _flash_covered_item_ids() -> set[tuple[str, int]]:
    # Ground items only -- ore's own icon IS what's already shown on an unmined rock (it's the
    # rock_content icon), there's no separate "covered look" to swap in/out of the way a hidden
    # ground item's icon can swap with the rock's. Pit is excluded too, on top of that, even though
    # it's a "soil"-category id -- a hole is never also a rock tile, see PIT_CONTENT_ID's comment;
    # the settings dialog never offers this checkbox for it, this guard is just defense in depth.
    return {
        ("soil", content_id)
        for content_id in SOIL_NAMES
        if content_id != PIT_CONTENT_ID and _item_flash_covered("soil", content_id)
    }


def _flash_red_item_ids() -> set[tuple[str, int]]:
    ids = {("ore", content_id) for content_id in ORE_NAMES if _item_flash_red("ore", content_id)}
    ids.update(
        ("soil", content_id) for content_id in SOIL_NAMES if _item_flash_red("soil", content_id)
    )
    return ids


# Special Floors (2026-09-16): wiki-sourced rare item/statue/Cursed Tool spawns by mine floor --
# see data/tables/mine_floor_spawns.json's own header comment for sourcing. Per-item Show +
# notify-lead-floors settings, same getter/setter-over-QSettings pattern as Floor Contents'
# per-item settings above. Keyed by item name (unique across both mines -- no item appears in both
# MINE_FLOOR_SPAWNS' "Spring" and "Lake" rows).
_MINE_FLOOR_SPAWNS_TABLE = load_table("mine_floor_spawns")["MINE_FLOOR_SPAWNS"]

_SPECIAL_FLOOR_SHOW_KEY_TEMPLATE = "mine_map/special_floor_show/{item}"
_SPECIAL_FLOOR_LEAD_KEY_TEMPLATE = "mine_map/special_floor_lead/{item}"
_SPECIAL_FLOOR_FLASH_KEY_TEMPLATE = "mine_map/special_floor_flash/{item}"
DEFAULT_SPECIAL_FLOOR_LEAD = 5
_SPECIAL_FLOOR_SETTINGS_GEOMETRY_KEY = "mine_map/special_floor_settings_geometry"
# Same on/off cadence as the map canvas's own rare-ore flash (MYTHIC_FLASH_MS/MYTHIC_FLASH_COLORS
# above) -- reused here so a flashing Special Floors message alternates the same two colors,
# rather than introducing a second, different-looking flash convention.


def _special_floor_items_by_mine() -> dict[str, list[str]]:
    items: dict[str, list[str]] = {"Spring": [], "Lake": []}
    seen: dict[str, set[str]] = {"Spring": set(), "Lake": set()}
    for entry in _MINE_FLOOR_SPAWNS_TABLE:
        mine = entry["mine"]
        item = entry["item"]
        if item not in seen[mine]:
            seen[mine].add(item)
            items[mine].append(item)
    return items


def _special_floor_show(item: str) -> bool:
    value = config.get_settings().value(
        _SPECIAL_FLOOR_SHOW_KEY_TEMPLATE.format(item=item), True
    )
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _set_special_floor_show(item: str, show: bool) -> None:
    config.get_settings().setValue(_SPECIAL_FLOOR_SHOW_KEY_TEMPLATE.format(item=item), show)


def _special_floor_lead(item: str) -> int:
    value = config.get_settings().value(
        _SPECIAL_FLOOR_LEAD_KEY_TEMPLATE.format(item=item), DEFAULT_SPECIAL_FLOOR_LEAD
    )
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_SPECIAL_FLOOR_LEAD


def _set_special_floor_lead(item: str, lead: int) -> None:
    config.get_settings().setValue(_SPECIAL_FLOOR_LEAD_KEY_TEMPLATE.format(item=item), lead)


def _special_floor_flash(item: str) -> bool:
    value = config.get_settings().value(
        _SPECIAL_FLOOR_FLASH_KEY_TEMPLATE.format(item=item), False
    )
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _set_special_floor_flash(item: str, enabled: bool) -> None:
    config.get_settings().setValue(_SPECIAL_FLOOR_FLASH_KEY_TEMPLATE.format(item=item), enabled)


class SpecialFloorAlert(NamedTuple):
    item: str
    message: str


def compute_special_floor_alerts(mine: str, current_floor: int) -> list[SpecialFloorAlert]:
    """One alert per (item, occurrence) that's either active on `current_floor` right now, or
    coming up within that item's own configured notify-lead-floors window. A ranged entry (e.g.
    Goddess Statue, floor_min != floor_max) counts as "active" for every floor in its range, not
    just its first. An item with several scattered floor entries (e.g. Kappa Jewel) can produce
    more than one alert if more than one of its occurrences falls in range at once -- rare, but
    each is a real distinct occurrence, not a duplicate. Each alert carries its own item name (not
    just the rendered message) so the caller can decide per-item whether to flash it, per the
    Special Floors Settings dialog's own Flash checkbox.
    """
    alerts: list[SpecialFloorAlert] = []
    for entry in _MINE_FLOOR_SPAWNS_TABLE:
        if entry["mine"] != mine:
            continue
        item = entry["item"]
        if not _special_floor_show(item):
            continue
        floor_min, floor_max = entry["floor_min"], entry["floor_max"]
        if floor_min <= current_floor <= floor_max:
            alerts.append(SpecialFloorAlert(item, f"{item} can spawn on this floor"))
            continue
        if current_floor < floor_min:
            distance = floor_min - current_floor
            if distance <= _special_floor_lead(item):
                floor_word = "floor" if distance == 1 else "floors"
                alerts.append(
                    SpecialFloorAlert(
                        item, f"{item} can spawn in {distance} {floor_word} (Floor {floor_min})"
                    )
                )
    return alerts


def load_icon(path: Path, size: int) -> QPixmap:
    pixmap = QPixmap(str(path))
    return pixmap.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def parse_grid(raw: bytes, rows: int, cols: int) -> dict:
    tiles = {}
    for row in range(rows):
        for col in range(cols):
            offset = row * ROW_STRIDE + col * TILE_STRIDE
            if offset + 12 > len(raw):
                continue
            (state,) = struct.unpack("<i", raw[offset: offset + 4])
            (rock_content,) = struct.unpack("<i", raw[offset + 4: offset + 8])
            (soil_content,) = struct.unpack("<i", raw[offset + 8: offset + 12])
            tiles[(row, col)] = (state, rock_content, soil_content)
    return tiles


_ZERO_TILE = b"\x00" * TILE_STRIDE


def infer_floor_size(raw: bytes) -> tuple[int, int]:
    """Derives the real floor's row/col extent from the raw MAX_ROWS x MAX_ROWS buffer itself --
    mine-agnostic, no hook needed. Confirmed live across all three known floor-size tiers
    (6x13/14x13/28x28) in both mines: real tile data stops cleanly at the floor's true boundary,
    with genuine all-zero padding beyond it and no false "empty" row/col found within real bounds
    -- see data/pointer_map.md's "Mine tile array" section.
    """
    max_row = -1
    max_col = -1
    for row in range(MAX_ROWS):
        row_offset = row * ROW_STRIDE
        for col in range(MAX_ROWS):
            offset = row_offset + col * TILE_STRIDE
            if raw[offset: offset + TILE_STRIDE] != _ZERO_TILE:
                if row > max_row:
                    max_row = row
                if col > max_col:
                    max_col = col
    if max_row < 0:
        return MAX_ROWS, MAX_ROWS
    return max_row + 1, max_col + 1


class FloorContentRow(NamedTuple):
    content_id: int
    category: str # "ore" (still inside a rock) or "soil" (already exposed on the ground)
    name: str
    qty: int
    sell_price: int | None # None = not sellable (or not yet priced), shown as "--"


def compute_floor_contents(
    tiles: dict,
    hidden_ids: set[tuple[str, int]] | None = None,
    order: list[tuple[str, int]] | None = None,
) -> list[FloorContentRow]:
    """Tallies quantities of each item currently on the floor, split into three buckets: ore still
    inside a rock (state==4, counted by rock_content -- shown under its plain name, e.g. "Mithril"),
    ground items still hidden under a rock (state==4, counted by that SAME tile's soil_content --
    the same field the hidden-ladder badge reads, just tallied for every other ground item too now
    -- shown with a "(Covered)" suffix), and ground items already exposed with nothing covering
    them (soil_content on a non-rock tile -- shown with a "(Uncovered)" suffix). The game's own
    memory already carries soil_content for a still-unbroken rock (confirmed live) --
    there's just no on-screen indication of it without one of the Covered Item Indicator settings
    below turned on; either way it's always countable here regardless of those settings.

    An id not in ORE_NAMES/SOIL_NAMES is still included (as "Unknown Ore (#N)" / "Unknown Item
    (#N, Covered/Uncovered)") rather than silently dropped -- that's what surfaces a not-yet-
    identified id at all, the same way this project's known ore/gem ids were built up floor by
    floor. `hidden_ids` -- a set of (category, id) pairs from the Floor Contents Settings dialog --
    only ever filters KNOWN items, and filters BOTH a ground item's covered and uncovered rows
    together (there's one checkbox per item, not one per covered/uncovered variant); there's
    nothing to hide/show for an id that hasn't been identified yet.

    `order` -- a list of (category, id) pairs from the same dialog's Up/Down-button lists --
    controls row order for identified items (e.g. Gold can rank above Mithril despite its lower
    id); a ground item's covered/uncovered rows share one rank and sort Uncovered-then-Covered
    when both exist. Unidentified items always sort after every identified one, by id -- there's
    nothing to rank them against until they're named. Only items with a count > 0 are included.

    Pits (2026-09-18) tally under the reserved PIT_CONTENT_ID pseudo-id, category "soil" (they're
    a tillable/ground-item concept, same section as Coins/Black Grass in the settings dialog) --
    see that constant's comment for why they're identified by tile `state` (HOLE_STATES) rather
    than a content field, and always show as a plain "Pit" with no Covered/Uncovered suffix since
    a hole is never also a rock tile.
    """
    ore_counts: dict[int, int] = {}
    uncovered_soil_counts: dict[int, int] = {}
    covered_soil_counts: dict[int, int] = {}
    for state, rock_content, soil_content in tiles.values():
        if state == 4:
            if rock_content not in EXCLUDED_ROCK_IDS:
                ore_counts[rock_content] = ore_counts.get(rock_content, 0) + 1
            if soil_content not in EXCLUDED_SOIL_IDS:
                covered_soil_counts[soil_content] = covered_soil_counts.get(soil_content, 0) + 1
        elif state in HOLE_STATES: # pits (2026-09-18) -- see PIT_CONTENT_ID's comment
            uncovered_soil_counts[PIT_CONTENT_ID] = uncovered_soil_counts.get(PIT_CONTENT_ID, 0) + 1
        elif state not in (2, 3):
            if soil_content not in EXCLUDED_SOIL_IDS:
                uncovered_soil_counts[soil_content] = uncovered_soil_counts.get(soil_content, 0) + 1

    hidden_ids = hidden_ids or set()
    rank = {pair: i for i, pair in enumerate(order)} if order else {}
    known_rows: list[FloorContentRow] = []
    unknown_rows: list[FloorContentRow] = []
    for content_id, qty in sorted(ore_counts.items()):
        name = ORE_NAMES.get(content_id)
        if name is None:
            unknown_rows.append(
                FloorContentRow(content_id, "ore", f"Unknown Ore (#{content_id})", qty, None)
            )
        elif ("ore", content_id) not in hidden_ids:
            known_rows.append(
                FloorContentRow(content_id, "ore", name, qty, ORE_SELL_PRICES.get(content_id))
            )

    soil_ids = set(uncovered_soil_counts) | set(covered_soil_counts)
    for content_id in sorted(soil_ids):
        name = SOIL_NAMES.get(content_id)
        uncovered_qty = uncovered_soil_counts.get(content_id, 0)
        covered_qty = covered_soil_counts.get(content_id, 0)
        if name is None:
            if uncovered_qty:
                unknown_rows.append(
                    FloorContentRow(
                        content_id, "soil", f"Unknown Item (#{content_id}, Uncovered)", uncovered_qty, None
                    )
                )
            if covered_qty:
                unknown_rows.append(
                    FloorContentRow(
                        content_id, "soil", f"Unknown Item (#{content_id}, Covered)", covered_qty, None
                    )
                )
            continue
        if ("soil", content_id) in hidden_ids:
            continue
        price = SOIL_SELL_PRICES.get(content_id)
        if content_id == PIT_CONTENT_ID:
            # No Covered/Uncovered suffix -- a hole is never also a rock tile, so there's only ever
            # the one variant (covered_qty is always 0 here, see PIT_CONTENT_ID's comment).
            if uncovered_qty:
                known_rows.append(FloorContentRow(content_id, "soil", name, uncovered_qty, price))
            continue
        if uncovered_qty:
            known_rows.append(
                FloorContentRow(content_id, "soil", f"{name} (Uncovered)", uncovered_qty, price)
            )
        if covered_qty:
            known_rows.append(
                FloorContentRow(content_id, "soil", f"{name} (Covered)", covered_qty, price)
            )

    known_rows.sort(key=lambda r: rank.get((r.category, r.content_id), len(rank)))
    return known_rows + unknown_rows


class _MineMapState:
    """Plain data shared between the Frida message callback (its own thread) and the Qt-side
    timer/paint code (main thread). Mutated from the callback thread, and read from the main
    thread -- no widget calls happen from the callback itself. One exception (2026-09-16):
    MineMapWidget._on_player_location also writes calibration_x/y/spawn_pending/current_location,
    from the Qt main thread (it's a queued Signal slot, driven by GameSession's own callback
    thread) -- accepted as a benign race under the GIL (plain attribute assignment, no read-modify-
    write) rather than round-tripping the Mine Floor 0 special case through this tab's own script
    just to keep every write on one thread.
    """

    def __init__(self, rows: int, cols: int):
        self.floor_rows = rows
        self.floor_cols = cols
        self.tiles: dict[tuple[int, int], tuple[int, int, int]] = {}
        self.player_x: float | None = None
        self.player_y: float | None = None
        # World-unit direction vector (target tile - true position), from the player's own Live
        # Entity -- same source/meaning as farm_map.py's facingDx/facingDy, see floor_map_agent.js's
        # readPlayerFacing(). None until the entity's been found at least once.
        self.facing_dx: float | None = None
        self.facing_dy: float | None = None
        # None until auto-anchored from the spawn tile (see WORLD_UNITS_PER_TILE's comment above)
        # -- the dot itself only draws once this is known (MineMapCanvas.paintEvent), and
        # MineMapWidget._on_message resets both back to None the instant a NEW floor is detected
        # (before the new anchor arrives), rather than leaving the previous floor's values in place
        # to be drawn (wrong) and then visibly jump once the new anchor lands.
        self.calibration_x: float | None = None
        self.calibration_y: float | None = None
        self.spawn_pending = False
        self.spawn_row: int | None = None
        self.spawn_col: int | None = None
        # Updated from GameSession's player_location_changed signal -- only consumed so far to
        # gate the Mine Floor 0 special case (see MINE_FLOOR_ZERO_LOCATION_ID's comment above).
        self.current_location: int | None = None
        self.ore_icons: dict[int, QPixmap] = {}
        self.soil_icons: dict[int, QPixmap] = {}
        # Flash Covered / Flash Red settings (2026-09-17) -- per-item (category, id) sets, read by
        # _MineMapCanvas.paintEvent every frame, written by MineMapWidget at construction and
        # whenever Floor Contents Settings changes. Empty by default (nothing flashes) -- see
        # _FloorContentsSettingsDialog.
        self.flash_covered_item_ids: set[tuple[str, int]] = set()
        self.flash_red_item_ids: set[tuple[str, int]] = set()


class _MineMapCanvas(QWidget):
    """Just the grid + player-marker drawing surface. MineMapWidget owns everything else.

    Scale-to-fit (2026-09-16): this widget is no longer pixel-locked to the room's real size
    (setFixedSize inside a QScrollArea, scrollbars appearing for anything bigger than the visible
    pane) -- it now freely resizes to whatever space the splitter/window gives it, and paintEvent
    scales its whole drawing (a single QPainter.scale() applied before any tile is drawn, so every
    coordinate below -- tiles, the player dot, the facing line -- stays expressed in the same
    CELL_SIZE-based logical units as before and scales together automatically) to fit that space,
    in either direction. This lets the top pane shrink for a small/uninteresting room and have the
    map actually get smaller too, instead of just cropping behind scrollbars while
    Floor Contents below stays starved for room.
    """

    def __init__(self, state: _MineMapState, parent=None):
        super().__init__(parent)
        self._state = state
        self.setMinimumSize(80, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        s = self._state
        painter = QPainter(self)
        painter.fillRect(self.rect(), WALL_COLOR)

        natural_width = s.floor_cols * CELL_SIZE + 2 * MARGIN_PX
        natural_height = s.floor_rows * CELL_SIZE + 2 * MARGIN_PX
        if natural_width <= 0 or natural_height <= 0 or self.width() <= 0 or self.height() <= 0:
            return
        scale = min(self.width() / natural_width, self.height() / natural_height)
        # Smooths both the ore/soil icon pixmaps and the tile-border lines at non-1:1 scale --
        # without this, shrinking the map made icons look noisy rather than just smaller.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(
            (self.width() - natural_width * scale) / 2, (self.height() - natural_height * scale) / 2
        )
        painter.scale(scale, scale)

        painter.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        # QPainter.drawRect() with no explicit color fills using whatever brush is currently set --
        # start from "no fill" so a bare drawRect (used everywhere below purely for the 1px tile
        # border) only strokes the outline, leaving the fillRect() background call untouched.
        painter.setBrush(Qt.BrushStyle.NoBrush)

        flash_on = int(time.monotonic() * 1000 / MYTHIC_FLASH_MS) % 2 == 0
        flash_color = MYTHIC_FLASH_COLORS[0] if flash_on else MYTHIC_FLASH_COLORS[1]

        for (row, col), (state, rock_content, soil_content) in s.tiles.items():
            x0 = MARGIN_PX + col * CELL_SIZE
            y0 = MARGIN_PX + row * CELL_SIZE
            center = (x0 + CELL_SIZE / 2, y0 + CELL_SIZE / 2)

            if state == 3: # exit ladder
                self._fill(painter, x0, y0)
                self._text(painter, center, "E", QColor("#40c0ff"))
            elif state in HOLE_STATES: # uncovered (5) or hidden (6) hole -- a pit
                self._fill(painter, x0, y0)
                self._text(painter, center, "X", QColor("#ff2020"))
                # Flash Red (2026-09-18): same per-item setting every other item uses, keyed off
                # PIT_CONTENT_ID -- see that constant's comment. No Flash Covered equivalent here,
                # a hole is never also a rock tile.
                if ("soil", PIT_CONTENT_ID) in s.flash_red_item_ids and flash_on:
                    painter.setPen(QPen(QColor("#ff2020"), 3))
                    painter.drawRect(x0 + 1, y0 + 1, CELL_SIZE - 2, CELL_SIZE - 2)
            elif state == 4: # rock
                painter.fillRect(x0, y0, CELL_SIZE, CELL_SIZE, ROCK_COLOR)
                painter.setPen(QPen(QColor("#444"), 1))
                painter.drawRect(x0, y0, CELL_SIZE, CELL_SIZE)
                # Flash Covered (2026-09-17, per-item now -- see _flash_covered_item_ids()):
                # soil_content is meaningful even on an unbroken rock -- it's what gets revealed
                # once the rock breaks (the hidden-ladder badge below has always relied on this for
                # soil_content==1; every other ground item works the same way). has_covered_item
                # excludes the ladder deliberately -- it already gets its own dedicated badge.
                has_covered_item = soil_content not in EXCLUDED_SOIL_IDS and soil_content != 1
                if (
                    has_covered_item
                    and ("soil", soil_content) in s.flash_covered_item_ids
                    and not flash_on
                ):
                    covered_icon = s.soil_icons.get(soil_content)
                    if covered_icon is not None:
                        self._draw_icon(painter, center, covered_icon)
                    else:
                        label, color = SOIL_CONTENT.get(soil_content, (str(soil_content), QColor("white")))
                        self._text(painter, center, label, color)
                else:
                    icon = s.ore_icons.get(rock_content)
                    if icon is not None:
                        self._draw_icon(painter, center, icon)
                    else:
                        self._text(painter, center, str(rock_content), QColor("white"))
                if rock_content in RARE_FLASH_IDS:
                    painter.setPen(QPen(flash_color, 3))
                    painter.drawRect(x0 + 1, y0 + 1, CELL_SIZE - 2, CELL_SIZE - 2)
                # Flash Red (2026-09-17, per-item): unlike the icon-swap above, this doesn't care
                # whether an item is covered or exposed -- checking it for Mythic Ore highlights
                # every tile with Mythic Ore rock_content, and checking it for a ground item
                # highlights that item both while still hidden under a rock (here) AND once exposed
                # (the soil_content-only branch further below handles that half). Drawn after the
                # rare-ore border above, so on the rare tile where both would apply to the same
                # tile, this one wins the pixels during its on-phase -- a cosmetic edge case, not
                # worth resolving further for how rarely both could coincide.
                flashes_red = ("ore", rock_content) in s.flash_red_item_ids or (
                    has_covered_item and ("soil", soil_content) in s.flash_red_item_ids
                )
                if flashes_red and flash_on:
                    painter.setPen(QPen(QColor("#ff2020"), 3))
                    painter.drawRect(x0 + 1, y0 + 1, CELL_SIZE - 2, CELL_SIZE - 2)
                if soil_content == 1:
                    self._draw_ladder_badge(painter, center)
                    # _draw_ladder_badge sets a real fill brush for its circle -- clear it back
                    # to "no fill" so it doesn't leak into every tile drawn after this one
                    # (otherwise everything past the first hidden-ladder-under-rock tile
                    # rendered with a dark-tinted background instead of its real color).
                    painter.setBrush(Qt.BrushStyle.NoBrush)
            elif state == 2: # the real, now-uncovered ladder
                label, color = SOIL_CONTENT[1]
                self._fill(painter, x0, y0)
                self._text(painter, center, label, color)
            elif soil_content != 0:
                label, color = SOIL_CONTENT.get(soil_content, (str(soil_content), QColor("white")))
                self._fill(painter, x0, y0)
                icon = s.soil_icons.get(soil_content)
                if icon is not None:
                    self._draw_icon(painter, center, icon)
                else:
                    self._text(painter, center, label, color)
                # Flash Red, exposed half (see the state==4 branch's comment above for the covered
                # half of this same setting).
                if ("soil", soil_content) in s.flash_red_item_ids and flash_on:
                    painter.setPen(QPen(QColor("#ff2020"), 3))
                    painter.drawRect(x0 + 1, y0 + 1, CELL_SIZE - 2, CELL_SIZE - 2)
            elif state == 1: # tilled
                painter.fillRect(x0, y0, CELL_SIZE, CELL_SIZE, QColor("#4a4a4a"))
                painter.setPen(QPen(QColor("#444"), 1))
                painter.drawRect(x0, y0, CELL_SIZE, CELL_SIZE)
            else:
                self._fill(painter, x0, y0)

        if (
            s.player_x is not None
            and s.player_y is not None
            and s.calibration_x is not None
            and s.calibration_y is not None
        ):
            col = (s.player_x - s.calibration_x) / WORLD_UNITS_PER_TILE
            row = (s.player_y - s.calibration_y) / WORLD_UNITS_PER_TILE
            cx = MARGIN_PX + (col + 0.5) * CELL_SIZE
            cy = MARGIN_PX + (row + 0.5) * CELL_SIZE
            if s.facing_dx is not None and s.facing_dy is not None:
                magnitude = math.hypot(s.facing_dx, s.facing_dy)
                if magnitude >= 1e-6:
                    length_px = FACING_INDICATOR_LENGTH_TILES * CELL_SIZE
                    tip_x = cx + s.facing_dx / magnitude * length_px
                    tip_y = cy + s.facing_dy / magnitude * length_px
                    pen = QPen(FACING_INDICATOR_COLOR)
                    pen.setWidthF(FACING_INDICATOR_WIDTH)
                    painter.setPen(pen)
                    painter.drawLine(QPointF(cx, cy), QPointF(tip_x, tip_y))
            r = CELL_SIZE / 4
            painter.setBrush(QColor("#ff3030"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

    @staticmethod
    def _fill(painter: QPainter, x0: int, y0: int) -> None:
        painter.fillRect(x0, y0, CELL_SIZE, CELL_SIZE, QColor("#303030"))
        painter.setPen(QPen(QColor("#444"), 1))
        painter.drawRect(x0, y0, CELL_SIZE, CELL_SIZE)

    @staticmethod
    def _text(painter: QPainter, center: tuple[float, float], text: str, color: QColor) -> None:
        painter.setPen(color)
        painter.drawText(
            int(center[0] - CELL_SIZE / 2),
            int(center[1] - CELL_SIZE / 2),
            CELL_SIZE,
            CELL_SIZE,
            Qt.AlignmentFlag.AlignCenter,
            text,
        )

    @staticmethod
    def _draw_icon(painter: QPainter, center: tuple[float, float], pixmap: QPixmap) -> None:
        painter.drawPixmap(
            int(center[0] - pixmap.width() / 2), int(center[1] - pixmap.height() / 2), pixmap
        )

    def _draw_ladder_badge(self, painter: QPainter, center: tuple[float, float]) -> None:
        label, color = SOIL_CONTENT[1]
        badge_r = CELL_SIZE * 0.34
        painter.setBrush(QColor("#101010"))
        painter.setPen(QPen(color, 2))
        painter.drawEllipse(center[0] - badge_r, center[1] - badge_r, badge_r * 2, badge_r * 2)
        self._text(painter, center, label, color)


class _FloorContentsSettingsDialog(QDialog):
    """Two sections, Ores and Ground Items -- split out because Flash Covered
    only ever applies to ground items (a rock's own icon IS what's shown for the ore inside it,
    there's no separate "covered look" for ore to swap in -- ore is never itself hidden under
    something else, only ground items can be), so a single combined list meant an always-blank,
    confusing column for every ore row. Each section reorders independently via Up/Down buttons,
    full-rebuild-on-every-move (same pattern as reminders_settings.py's section reordering) rather
    than drag-and-drop -- the earlier drag-to-reorder version proved unreliable in practice.

    Per-item checkboxes, all persisting immediately and notifying the owner, no OK/Apply step (same
    live-update convention as CheatsDialog):
    - Show: include this item in the Floor Contents list (a ground item's checkbox covers both its
      Covered and Uncovered rows there together -- one settings row per ITEM, not per variant).
    - Flash Covered (ground items only): while a rock hides this item, alternate the rock's icon
      with this item's own icon on the map.
    - Flash Red (both sections): flash a red border around every tile with this item, covered or
      exposed -- e.g. checking it for Mythic Ore highlights every Mythic Ore rock on the floor.
    Unidentified items get no row -- see compute_floor_contents()'s docstring for why there's
    nothing to toggle/rank for those yet.
    """

    def __init__(self, on_settings_changed, parent=None):
        super().__init__(parent)
        self._on_settings_changed = on_settings_changed
        self.setWindowTitle("Floor Contents Settings")
        self._restore_geometry()

        outer_layout = QVBoxLayout(self)
        intro_label = QLabel(
            "Show: include in the Floor Contents list. Flash Covered: swap the rock's icon for "
            "this item's while it's hidden underneath. Flash Red: highlight every tile with "
            "this item."
        )
        # Same fix as _SpecialFloorsSettingsDialog's intro label: without word
        # wrap, Qt sizes this label -- and so the dialog's minimum width -- to fit the whole
        # sentence on one line, which is what actually blocks the dialog from shrinking below that
        # width (not the resize() call below, which is only a starting size).
        intro_label.setWordWrap(True)
        outer_layout.addWidget(intro_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        # Ground Items above Ores -- matches the main Floor Contents table's own order, see
        # _combined_item_order().
        self._soil_box = QGroupBox("Ground Items")
        self._soil_rows_layout = QVBoxLayout(self._soil_box)
        content_layout.addWidget(self._soil_box)

        self._ore_box = QGroupBox("Ores")
        self._ore_rows_layout = QVBoxLayout(self._ore_box)
        content_layout.addWidget(self._ore_box)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, stretch=1)

        self._rebuild_soil_rows()
        self._rebuild_ore_rows()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        outer_layout.addWidget(buttons)

    def _restore_geometry(self) -> None:
        geometry = config.get_settings().value(_FLOOR_CONTENTS_SETTINGS_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(380, 560)

    def closeEvent(self, event) -> None:
        config.get_settings().setValue(_FLOOR_CONTENTS_SETTINGS_GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)

    def _rebuild_ore_rows(self) -> None:
        self._clear_layout(self._ore_rows_layout)
        order = _ore_item_order()
        for index, content_id in enumerate(order):
            row_widget, row = self._make_row()
            row.addWidget(QLabel(f"#{content_id} -- {ORE_NAMES[content_id]}"))
            row.addStretch(1)
            self._add_checkbox(
                row,
                "Show",
                _item_visible("ore", content_id),
                lambda checked, cid=content_id: self._on_visible_toggled("ore", cid, checked),
            )
            self._add_checkbox(
                row,
                "Flash Red",
                _item_flash_red("ore", content_id),
                lambda checked, cid=content_id: self._on_flash_red_toggled("ore", cid, checked),
            )
            self._add_move_buttons(row, index, len(order), self._move_ore)
            self._ore_rows_layout.addWidget(row_widget)

    def _rebuild_soil_rows(self) -> None:
        self._clear_layout(self._soil_rows_layout)
        order = _soil_item_order()
        for index, content_id in enumerate(order):
            row_widget, row = self._make_row()
            # Pit shows as a plain name, no "#id --" prefix -- it isn't a real memory-derived id
            # the way every other row's number is, see PIT_CONTENT_ID's comment.
            label = (
                SOIL_NAMES[content_id]
                if content_id == PIT_CONTENT_ID
                else f"#{content_id} -- {SOIL_NAMES[content_id]}"
            )
            row.addWidget(QLabel(label))
            row.addStretch(1)
            self._add_checkbox(
                row,
                "Show",
                _item_visible("soil", content_id),
                lambda checked, cid=content_id: self._on_visible_toggled("soil", cid, checked),
            )
            # Flash Covered doesn't apply to Pit -- a hole is never also a rock tile, so there's no
            # "covered look" to swap in, see PIT_CONTENT_ID's comment.
            if content_id != PIT_CONTENT_ID:
                self._add_checkbox(
                    row,
                    "Flash Covered",
                    _item_flash_covered("soil", content_id),
                    lambda checked, cid=content_id: self._on_flash_covered_toggled("soil", cid, checked),
                )
            self._add_checkbox(
                row,
                "Flash Red",
                _item_flash_red("soil", content_id),
                lambda checked, cid=content_id: self._on_flash_red_toggled("soil", cid, checked),
            )
            self._add_move_buttons(row, index, len(order), self._move_soil)
            self._soil_rows_layout.addWidget(row_widget)

    @staticmethod
    def _make_row() -> tuple[QWidget, QHBoxLayout]:
        # A QWidget wrapper (not a bare QHBoxLayout added via addLayout()) so _clear_layout can
        # reliably tear a whole row down with a single deleteLater() on the widget, same pattern
        # reminders_settings.py's own row-rebuild uses -- a bare layout isn't a QObject and has no
        # deleteLater() of its own to rely on.
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        return row_widget, row

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _add_checkbox(row: QHBoxLayout, label: str, checked: bool, on_toggled) -> None:
        checkbox = QCheckBox(label)
        checkbox.setChecked(checked)
        checkbox.toggled.connect(on_toggled)
        row.addWidget(checkbox)

    @staticmethod
    def _add_move_buttons(row: QHBoxLayout, index: int, count: int, move) -> None:
        up_button = QToolButton()
        up_button.setText("▲")
        up_button.setEnabled(index > 0)
        up_button.setToolTip("Move up")
        up_button.clicked.connect(lambda _checked=False, i=index: move(i, -1))
        row.addWidget(up_button)

        down_button = QToolButton()
        down_button.setText("▼")
        down_button.setEnabled(index < count - 1)
        down_button.setToolTip("Move down")
        down_button.clicked.connect(lambda _checked=False, i=index: move(i, 1))
        row.addWidget(down_button)

    def _move_ore(self, index: int, direction: int) -> None:
        order = _ore_item_order()
        target = index + direction
        if not (0 <= target < len(order)):
            return
        order[index], order[target] = order[target], order[index]
        _set_ore_item_order(order)
        self._rebuild_ore_rows()
        self._on_settings_changed()

    def _move_soil(self, index: int, direction: int) -> None:
        order = _soil_item_order()
        target = index + direction
        if not (0 <= target < len(order)):
            return
        order[index], order[target] = order[target], order[index]
        _set_soil_item_order(order)
        self._rebuild_soil_rows()
        self._on_settings_changed()

    def _on_visible_toggled(self, category: str, content_id: int, checked: bool) -> None:
        _set_item_visible(category, content_id, checked)
        self._on_settings_changed()

    def _on_flash_covered_toggled(self, category: str, content_id: int, checked: bool) -> None:
        _set_item_flash_covered(category, content_id, checked)
        self._on_settings_changed()

    def _on_flash_red_toggled(self, category: str, content_id: int, checked: bool) -> None:
        _set_item_flash_red(category, content_id, checked)
        self._on_settings_changed()


class _SpecialFloorsSettingsDialog(QDialog):
    """Special Floors -- per-item Show toggle + notify-lead-floors textbox for every wiki-sourced
    rare item/statue/Cursed Tool floor in data/tables/mine_floor_spawns.json. Split into two
    sections, Spring Mine and Lake Mine, matching that table's own 'mine'
    field -- an item only ever belongs to one. No reordering -- unlike Floor Contents Settings,
    there's no on-screen list here whose order matters, just a settings row per item.

    Show/Floors Ahead persist immediately, no OK/Apply step, same live-update convention as
    _FloorContentsSettingsDialog.
    """

    def __init__(self, on_settings_changed, parent=None):
        super().__init__(parent)
        self._on_settings_changed = on_settings_changed
        self.setWindowTitle("Special Floors Settings")
        self._restore_geometry()

        outer_layout = QVBoxLayout(self)
        intro_label = QLabel(
            "Show: display a message below the map when this item is about to be (or "
            "currently is) findable on this floor. Floors Ahead: how many floors early to "
            "start showing that message. Flash: alternate that message's color instead of "
            "showing it plain."
        )
        # Without word wrap, Qt sizes this label (and so the whole dialog's minimum width) to fit
        # this entire sentence on one line -- the actual cause of the dialog being stuck wide and
        # not shrinkable, not the resize() call below (that's just a starting
        # size, not a lower bound).
        intro_label.setWordWrap(True)
        outer_layout.addWidget(intro_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        items_by_mine = _special_floor_items_by_mine()

        spring_box = QGroupBox("Spring Mine")
        spring_rows_layout = QVBoxLayout(spring_box)
        self._build_rows(spring_rows_layout, items_by_mine["Spring"])
        content_layout.addWidget(spring_box)

        lake_box = QGroupBox("Lake Mine")
        lake_rows_layout = QVBoxLayout(lake_box)
        self._build_rows(lake_rows_layout, items_by_mine["Lake"])
        content_layout.addWidget(lake_box)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        outer_layout.addWidget(buttons)

    def _restore_geometry(self) -> None:
        geometry = config.get_settings().value(_SPECIAL_FLOOR_SETTINGS_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(420, 560)

    def closeEvent(self, event) -> None:
        config.get_settings().setValue(_SPECIAL_FLOOR_SETTINGS_GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)

    def _build_rows(self, layout: QVBoxLayout, items: list[str]) -> None:
        for item in items:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(item))
            row.addStretch(1)

            show_checkbox = QCheckBox("Show")
            show_checkbox.setChecked(_special_floor_show(item))
            show_checkbox.toggled.connect(
                lambda checked, name=item: self._on_show_toggled(name, checked)
            )
            row.addWidget(show_checkbox)

            flash_checkbox = QCheckBox("Flash")
            flash_checkbox.setChecked(_special_floor_flash(item))
            flash_checkbox.toggled.connect(
                lambda checked, name=item: self._on_flash_toggled(name, checked)
            )
            row.addWidget(flash_checkbox)

            row.addWidget(QLabel("Floors Ahead:"))
            lead_edit = QLineEdit(str(_special_floor_lead(item)))
            lead_edit.setFixedWidth(40)
            lead_edit.editingFinished.connect(
                lambda name=item, edit=lead_edit: self._on_lead_edited(name, edit)
            )
            row.addWidget(lead_edit)

            layout.addWidget(row_widget)

    def _on_show_toggled(self, item: str, checked: bool) -> None:
        _set_special_floor_show(item, checked)
        self._on_settings_changed()

    def _on_flash_toggled(self, item: str, checked: bool) -> None:
        _set_special_floor_flash(item, checked)
        self._on_settings_changed()

    def _on_lead_edited(self, item: str, edit: QLineEdit) -> None:
        try:
            lead = max(0, int(edit.text()))
        except ValueError:
            lead = _special_floor_lead(item)
        edit.setText(str(lead))
        _set_special_floor_lead(item, lead)
        self._on_settings_changed()


class MineMapWidget(QWidget):
    """Attaches to the game on construction (i.e. as soon as this tab is added) and detaches in
    cleanup() (called by the shell when the tab is actually closed, not just hidden behind
    another tab -- see app/shell.py's _ManagedDockWidget).

    Attaching happens on a background thread (see _attach_worker) so this tab appears immediately
    instead of freezing the whole app for the few seconds attaching can take -- the same lesson as
    the close-hang bug: never block the Qt main thread on a Frida call. Attach progress/errors print
    to the console (an "Attaching to the game..." label that used to show them in the tab itself
    too has since been removed).
    """

    # session, script, error_message ('' means attach succeeded). Emitted from the background
    # attach thread; Qt automatically delivers it to _on_attach_result on the main thread since
    # this is a cross-thread signal/slot connection -- the standard safe way to marshal a
    # background thread's result back to the GUI thread.
    _attach_result = Signal(object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._state = _MineMapState(rows=MAX_ROWS, cols=MAX_ROWS)
        self._session = None
        self._script = None
        self._game_session = None
        self._hidden_item_ids = _hidden_item_ids()
        self._item_order = _combined_item_order()
        self._state.flash_covered_item_ids = _flash_covered_item_ids()
        self._state.flash_red_item_ids = _flash_red_item_ids()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._canvas = _MineMapCanvas(self._state)
        self._canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Coordinate readout gets its own row, separate from Floor Contents below it.
        bottom_layout = QVBoxLayout()
        bottom_layout.setContentsMargins(4, 2, 4, 2)
        bottom_layout.setSpacing(2)
        self._coord_label = QLabel("Grid row: -- col: --")
        self._coord_label.setStyleSheet("color:#e0e0e0; font-family:Consolas;")
        bottom_layout.addWidget(self._coord_label)

        # Special Floors: a live check of the wiki-sourced rare item/statue/Cursed
        # Tool floor table (data/tables/mine_floor_spawns.json) against the current mine+floor
        # (derived from GameSession's own location broadcast, see mine_and_floor_from_location()
        # above -- no new hook). Sits below the map and above Floor Contents. The message label is
        # hidden entirely when there's nothing to show,
        # same convention as Reminders' self-gated sections.
        special_header = QHBoxLayout()
        special_title = QLabel("<b>Special Floors</b>")
        special_title.setStyleSheet("color:#e0e0e0;")
        special_header.addWidget(special_title)
        special_header.addStretch(1)
        special_settings_button = QPushButton("Settings...")
        special_settings_button.clicked.connect(self._open_special_floors_settings)
        special_header.addWidget(special_settings_button)
        bottom_layout.addLayout(special_header)

        self._special_floor_label = QLabel("")
        self._special_floor_label.setStyleSheet("color:#ffcc00; font-family:Consolas;")
        self._special_floor_label.setWordWrap(True)
        self._special_floor_label.hide()
        bottom_layout.addWidget(self._special_floor_label)
        self._last_special_floor_text: str | None = None

        # Floor Contents (2026-09-16): a live tally of collectible item quantities on the current
        # floor -- see compute_floor_contents() above for the counting rules. Recomputed every tick
        # from the already-read tile grid (s.tiles), no extra memory reads needed. In-place cell
        # mutation (not a full rebuild) so it doesn't flicker/lose scroll position while ticking,
        # same convention as reminders.py's persistent tables.
        contents_header = QHBoxLayout()
        contents_title = QLabel("<b>Floor Contents</b>")
        contents_title.setStyleSheet("color:#e0e0e0;")
        contents_header.addWidget(contents_title)
        contents_header.addStretch(1)
        contents_settings_button = QPushButton("Settings...")
        contents_settings_button.clicked.connect(self._open_floor_contents_settings)
        contents_header.addWidget(contents_settings_button)
        bottom_layout.addLayout(contents_header)

        self._contents_table = CopyableTableWidget(0, 4)
        self._contents_table.setHorizontalHeaderLabels(["", "Item", "Qty", "Sell Price"])
        self._contents_table.verticalHeader().setVisible(False)
        self._contents_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._contents_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Interactive, not Stretch/ResizeToContents -- lets columns be dragged to
        # whatever width fits, and setStretchLastSection(False) below stops the table
        # auto-filling the pane's width by stretching the last column, so a narrower window scrolls
        # horizontally (the table's own built-in scrollbar) instead of squeezing every column.
        header = self._contents_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self._contents_table.setColumnWidth(0, 32)
        self._contents_table.setColumnWidth(1, 150)
        self._contents_table.setColumnWidth(2, 50)
        self._contents_table.setColumnWidth(3, 80)
        self._contents_table.setIconSize(QSize(CELL_SIZE, CELL_SIZE))
        self._contents_table.setStyleSheet("background:#202020; color:#e0e0e0;")
        bottom_layout.addWidget(self._contents_table, stretch=1)
        self._last_contents_rows: list[FloorContentRow] | None = None

        bottom_bar = QWidget()
        bottom_bar.setStyleSheet("background:#1a1a1a;")
        bottom_bar.setLayout(bottom_layout)

        # Map / Floor Contents split: a draggable QSplitter, not a fixed stretch
        # ratio -- an automatic stretch-based split (tried first) fought over space no matter which
        # side "won" it: giving the map stretch let it balloon with empty padding around the
        # fixed-size canvas on window resize; giving Floor Contents stretch instead let the map get
        # squeezed down toward nothing on a smaller window. A splitter sidesteps picking a policy
        # at all -- the handle can be dragged to whatever balance suits the room currently being
        # viewed, in either direction. Sizes persist across restarts the same way shell.py persists the main window's
        # own dock layout (saveState()/restoreState() on every drag).
        #
        # The canvas goes straight into the splitter now, no QScrollArea wrapper (2026-09-16) --
        # _MineMapCanvas.paintEvent scales its own drawing to fit whatever size it's actually given
        # instead of staying pixel-locked to the room's real size, so shrinking this pane shrinks
        # the rendered map instead of just cropping it behind scrollbars.
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(self._canvas)
        self._splitter.addWidget(bottom_bar)
        saved_splitter_state = config.get_settings().value(SPLITTER_STATE_KEY)
        if saved_splitter_state is not None:
            self._splitter.restoreState(saved_splitter_state)
        else:
            self._splitter.setSizes(DEFAULT_SPLITTER_SIZES)
        self._splitter.splitterMoved.connect(self._save_splitter_state)
        layout.addWidget(self._splitter, stretch=1)

        self._attach_result.connect(self._on_attach_result)
        self._load_icons()
        threading.Thread(target=self._attach_worker, daemon=True).start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    def _load_icons(self) -> None:
        for rock_id, filename in ORE_ICONS.items():
            path = ORE_ICON_DIR / filename
            if path.exists():
                self._state.ore_icons[rock_id] = load_icon(path, CELL_SIZE)
        for soil_id, filename in SOIL_ICONS.items():
            path = ORE_ICON_DIR / filename
            if path.exists():
                self._state.soil_icons[soil_id] = load_icon(path, CELL_SIZE)

    def _attach_worker(self) -> None:
        """Runs entirely on a background thread -- create_script()/load() are all blocking calls
        that can take a real moment, and must never run on the Qt main thread.
        """
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session() # blocks this background thread, not Qt's

            # tables=() -- floor_map_agent.js doesn't reference any lookup table today, but this
            # routes through the same shared loader every other window uses (see agent_loader.py).
            agent_source = load_agent_source(AGENT_PATH, tables=())
            agent_source = agent_source.replace("TARGET_SIZE2", "0")
            agent_source = agent_source.replace("TARGET_SIZE", str(MAX_ROWS * ROW_STRIDE))
            agent_source = agent_source.replace("GRID_BASE_OVERRIDE", "null")
            agent_source = agent_source.replace("GRID_BASE2_OVERRIDE", "null")
            cached_offset = hook_cache.get(POSITION_HOOK_CACHE_KEY)
            offset_expr = f'ptr("{cached_offset}")' if cached_offset else "null"
            agent_source = agent_source.replace("POSITION_HOOK_OFFSET_OVERRIDE", offset_expr)

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
            print(f"[mine map] couldn't attach to the game: {error_message}")
            return
        self._session = session
        self._script = script
        print(
            "[mine map] attached. Go down a ladder to load a floor (till a tile once if the grid "
            "doesn't appear right away)."
        )
        # Sole grid_base source (shape-validated by the agent) -- see
        # src/floor_map_agent.js's listenForSetDerivedMineFloorBase.
        self._game_session.mine_floor_base_found.connect(self._on_shared_mine_floor_base)
        if self._game_session.current_mine_floor_base:
            self._on_shared_mine_floor_base(self._game_session.current_mine_floor_base)

        # Entity Manager pointer (for the player facing-direction line) is hooked once, centrally,
        # in src/core_hooks_agent.js -- this tab receives it rather than hooking exe+532A0 itself.
        self._game_session.entity_manager_found.connect(self._on_shared_entity_manager)
        if self._game_session.current_entity_manager:
            self._on_shared_entity_manager(self._game_session.current_entity_manager)

        # Drives the Mine Floor 0 special case (see MINE_FLOOR_ZERO_LOCATION_ID's comment above) --
        # also hooked once, centrally, in src/core_hooks_agent.js.
        self._game_session.player_location_changed.connect(self._on_player_location)
        if self._game_session.current_player_location is not None:
            self._on_player_location(self._game_session.current_player_location)

    def _on_player_location(self, location_id: int) -> None:
        s = self._state
        s.current_location = location_id
        if location_id == MINE_FLOOR_ZERO_LOCATION_ID:
            # Bypasses the normal spawn-tile/entity-position anchor pipeline entirely for this one
            # floor (see MINE_FLOOR_ZERO_LOCATION_ID's comment above for why) -- re-applied every
            # time this location is entered, ladder-return trips included, so it can't be left
            # showing a stale offset from whichever floor was last actually anchored dynamically.
            s.calibration_x = (
                MINE_FLOOR_ZERO_SPAWN_WORLD_X - MINE_FLOOR_ZERO_SPAWN_COL * WORLD_UNITS_PER_TILE
            )
            s.calibration_y = (
                MINE_FLOOR_ZERO_SPAWN_WORLD_Y - MINE_FLOOR_ZERO_SPAWN_ROW * WORLD_UNITS_PER_TILE
            )
            s.spawn_pending = False
            print(
                "[mine map] Floor 0 dot anchored (fixed spawn point: "
                f"row={MINE_FLOOR_ZERO_SPAWN_ROW}, col={MINE_FLOOR_ZERO_SPAWN_COL})"
            )
            return
        # Every other floor's anchor-triggering entity-position request is made HERE, not from the
        # 'spawnTile' handler below -- confirmed live 2026-09-16 (floor 19->20 transition): the
        # engine generates the next floor (spawn tile included) and 'spawnTile' fires BEFORE it
        # actually relocates the player and flips the location id ('area transition hook
        # processed'/this signal firing after). Requesting the entity's position the instant
        # 'spawnTile' fires (the old approach) could therefore sample the player's LEFTOVER
        # position from the floor being LEFT, paired against the NEW floor's real spawn tile --
        # producing a wrong anchor (confirmed live: floor 20's dot anchored using a position read
        # before 'area transition hook processed' ever printed). This signal is the one thing known
        # to fire only after that relocation, so it's the safe moment to sample. Only relevant if a
        # spawn tile is actually pending (spawn_pending stays False the rest of the time).
        if s.spawn_pending and self._script is not None:
            self._script.post({"type": "requestEntityPosition"})

    def _on_shared_mine_floor_base(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setDerivedMineFloorBase", "address": address})

    def _on_shared_entity_manager(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setEntityManager", "address": address})

    def _on_message(self, message, data) -> None:
        # Runs on Frida's own background thread -- only touch plain state here, never widgets.
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[mine map] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        s = self._state
        if kind == "grid":
            if payload.get("ok"):
                raw = bytes.fromhex(payload["hex"])
                # Floor size is derived straight from this same raw buffer -- see
                # infer_floor_size()'s docstring for why (mine-agnostic, no hook needed; the
                # floor-generation hook that used to report this has been removed from
                # src/floor_map_agent.js). Full replace, not.update(), so a transition to a
                # smaller floor can't leave a larger previous floor's out-of-bounds tiles
                # lingering in the dict.
                s.floor_rows, s.floor_cols = infer_floor_size(raw)
                s.tiles = parse_grid(raw, s.floor_rows, s.floor_cols)
            else:
                print(f"[mine map] failed to read grid: {payload.get('error')}")
        elif kind == "spawnTile":
            if s.current_location == MINE_FLOOR_ZERO_LOCATION_ID:
                # Floor 0's own spawn-tile reading is the unreliable one this whole special case
                # exists to route around (see MINE_FLOOR_ZERO_LOCATION_ID's comment) -- ignored
                # here so it can't clobber the hardcoded anchor _on_player_location already applied.
                return
            s.spawn_row = payload["row"]
            s.spawn_col = payload["col"]
            s.spawn_pending = True
            # Hides the dot (paintEvent skips drawing while these are None) until the new anchor
            # resolves -- this fires on every floor-generation event regardless of whether the new
            # floor happens to share the last one's dimensions (floorSize alone wouldn't catch that
            # case, since it's deduplicated by size in floor_map_agent.js).
            s.calibration_x = None
            s.calibration_y = None
            # NOT requesting a fresh entity-position sample here anymore (2026-09-16 -- see
            # _on_player_location's comment): this hook fires as part of floor GENERATION, which
            # happens before the player is actually relocated to the new floor, so a position
            # sampled at this exact moment can still be the OLD floor's leftover value. The request
            # is made from _on_player_location instead, once relocation is confirmed done.
        elif kind == "position":
            # Only 'entity'-sourced messages (the Live Entity's own rendered X/Y, same source
            # Farm Map's marker uses) are used here -- the agent's older write-hook/poll source
            # ('write'/'poll') is ignored on this side even though the script still sends it (kept
            # for src/floor_map.py, the frozen Tkinter fallback). Switched 2026-09-16: the
            # write-hook source only updated once the player actually took a step, so a freshly
            # loaded floor's dot didn't anchor until movement happened, sometimes not until the
            # NEXT floor transition (confirmed live via the "spawn tile detected"/"dot anchored"
            # console lines). The entity source reflects the rendered position continuously, so it
            # can anchor the instant a floor loads, no movement needed.
            if payload.get("src") != "entity":
                return
            s.player_x = payload["x"]
            s.player_y = payload["y"]
            s.facing_dx = payload.get("facingDx")
            s.facing_dy = payload.get("facingDy")
            # Auto-anchors the origin from the spawn tile -- the one moment a live position is
            # guaranteed to match a known tile, no manual confirmation needed (see
            # WORLD_UNITS_PER_TILE's comment above for why every floor needs its own anchor).
            if s.spawn_pending and not (payload["x"] == 0.0 and payload["y"] == 0.0):
                s.calibration_x = payload["x"] - s.spawn_col * WORLD_UNITS_PER_TILE
                s.calibration_y = payload["y"] - s.spawn_row * WORLD_UNITS_PER_TILE
                s.spawn_pending = False
                print(
                    f"[mine map] dot anchored (spawn row={s.spawn_row}, col={s.spawn_col}, "
                    f"World X={payload['x']:.2f}, Y={payload['y']:.2f})"
                )
        elif kind == "positionHookOffsetFound":
            hook_cache.set(POSITION_HOOK_CACHE_KEY, payload["offset"])
        elif kind == "status":
            print(f"[mine map] {payload['message']}")
        elif kind == "error":
            print(f"[mine map] {payload.get('label')}: {payload.get('message')}")

    def _tick(self) -> None:
        s = self._state
        if s.player_x is not None and s.player_y is not None:
            if s.calibration_x is not None and s.calibration_y is not None:
                col = (s.player_x - s.calibration_x) / WORLD_UNITS_PER_TILE
                row = (s.player_y - s.calibration_y) / WORLD_UNITS_PER_TILE
                grid_text = f"Grid row: {row:.2f} col: {col:.2f}"
            else:
                grid_text = "Grid row/col: -- (not anchored yet)"
            self._coord_label.setText(grid_text)
        self._canvas.update()
        self._refresh_floor_contents()
        self._update_special_floors()

    def _update_special_floors(self) -> None:
        mine_floor = (
            mine_and_floor_from_location(self._state.current_location)
            if self._state.current_location is not None
            else None
        )
        alerts = (
            compute_special_floor_alerts(*mine_floor) if mine_floor is not None else []
        )
        if not alerts:
            text = ""
        else:
            # Flash (2026-09-19): reuses the map canvas's own rare-ore flash colors/cadence
            # (MYTHIC_FLASH_COLORS/MYTHIC_FLASH_MS) so a flashing message reads as the same
            # convention, not a second flash style. Only items with their own Flash checkbox
            # checked (Special Floors Settings) get a color span -- everything else renders in the
            # label's plain (stylesheet) color. This intentionally makes the label's rich-text
            # output change every MYTHIC_FLASH_MS/2 while any alert is flashing, so the early-out
            # below only fires while nothing is (a small, cheap label update, not a
            # matrix-viewer-scale bulk update).
            flash_on = int(time.monotonic() * 1000 / MYTHIC_FLASH_MS) % 2 == 0
            flash_color = MYTHIC_FLASH_COLORS[0] if flash_on else MYTHIC_FLASH_COLORS[1]
            lines = []
            for item, message in alerts:
                if _special_floor_flash(item):
                    lines.append(f'<span style="color:{flash_color.name()};">{message}</span>')
                else:
                    lines.append(message)
            text = "<br>".join(lines)
        if text == self._last_special_floor_text:
            return
        self._last_special_floor_text = text
        self._special_floor_label.setText(text)
        self._special_floor_label.setVisible(bool(text))

    def _open_special_floors_settings(self) -> None:
        dialog = _SpecialFloorsSettingsDialog(self._on_special_floors_settings_changed, self)
        dialog.exec()

    def _on_special_floors_settings_changed(self) -> None:
        # Forces the next _update_special_floors() to actually recheck even if the text it would
        # produce happens to match what's already showing -- same "nudge" pattern
        # _on_floor_contents_settings_changed() uses below.
        self._last_special_floor_text = None
        self._update_special_floors()

    def _refresh_floor_contents(self) -> None:
        rows = compute_floor_contents(self._state.tiles, self._hidden_item_ids, self._item_order)
        if rows == self._last_contents_rows:
            return
        self._last_contents_rows = rows
        table = self._contents_table
        if table.rowCount() != len(rows):
            table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            icons = self._state.ore_icons if row.category == "ore" else self._state.soil_icons
            icon_pixmap = icons.get(row.content_id)
            icon_item = table.item(row_idx, 0)
            if icon_item is None:
                icon_item = QTableWidgetItem()
                table.setItem(row_idx, 0, icon_item)
            icon_item.setIcon(QIcon(icon_pixmap) if icon_pixmap is not None else QIcon())

            price_text = f"{row.sell_price} G" if row.sell_price is not None else "--"
            for col_idx, text in ((1, row.name), (2, str(row.qty)), (3, price_text)):
                item = table.item(row_idx, col_idx)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row_idx, col_idx, item)
                if item.text() != text:
                    item.setText(text)
        table.resizeRowsToContents()

    def _open_floor_contents_settings(self) -> None:
        dialog = _FloorContentsSettingsDialog(self._on_floor_contents_settings_changed, self)
        dialog.exec()

    def _on_floor_contents_settings_changed(self) -> None:
        self._hidden_item_ids = _hidden_item_ids()
        self._item_order = _combined_item_order()
        self._state.flash_covered_item_ids = _flash_covered_item_ids()
        self._state.flash_red_item_ids = _flash_red_item_ids()
        # Forces the next _refresh_floor_contents() to actually rebuild the table even if the raw
        # tile counts haven't changed since the last tick -- only visibility/order has. The canvas
        # doesn't need an equivalent nudge -- it repaints every tick regardless (self._canvas.update()
        # in _tick()), so the two flash toggles above just take effect on the very next frame.
        self._last_contents_rows = None
        self._refresh_floor_contents()

    def _save_splitter_state(self, pos: int, index: int) -> None:
        config.get_settings().setValue(SPLITTER_STATE_KEY, self._splitter.saveState())

    def cleanup(self) -> None:
        """Called by the shell when this tab is actually closed (not just switched away from).

        Only unloads this tab's OWN script -- `self._session` is the app's shared GameSession
        session now (see app/game_session.py), not owned by this tab, so it must NOT be detached
        here or every other window sharing it would break. The shared session itself is torn down
        once from the shell on actual app shutdown.
        """
        self._timer.stop()
        script = self._script
        self._session, self._script = None, None

        def detach() -> None:
            # script.unload() is a synchronous call into the game process and can take a real
            # moment -- or hang outright if something's gone wrong there. Doing this on the Qt main
            # thread froze the whole app on close, confirmed live; a daemon
            # thread means even a full hang here can no longer block the UI or stop the app from
            # exiting.
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()


register(WindowSpec("mine_floor_map", "Mine Floor Map", MineMapWidget))
