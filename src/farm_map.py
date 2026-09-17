"""
Farm map: shows the whole farm's tile grid (tilled/watered state, debris, and planted crops with
their growth stage), overlaid on a background template image of the actual farm layout.

Usage:
    python src/farm_map.py "EXACT_PROCESS_NAME.exe" [--address 0xADDRESS]

The farm's grid_base is read from app/hook_cache.py's on-disk cache if one is there (written by the
full app's own shared session the last time it derived one -- SaveDataBase + 0x78C4, see
data/pointer_map.md's "Farm tile array" section) -- used as-is, no validation, since this script has
no shared session of its own to derive or confirm one independently (2026-09-14: the two permanent
save-load hooks this script used to run on its own were retired in favor of that single, centralized
detection path -- see src/farm_map_agent.js's header comment). If nothing is cached and --address
isn't given either, run the full app once first (it always finds and caches a fresh grid_base as
soon as it attaches) or find one by hand via Cheat Engine.

--address forces a specific address by hand instead, skipping the cache for this run.

The template photo has real camera perspective, so the farm's true shape in it is a trapezoid, not
a rectangle. Rather than warp the grid to match that trapezoid, the grid stays a plain straight
rectangle and the *template photo* gets warped to align with it instead -- via a perspective
transform (QTransform.quadToQuad) mapping four calibrated points in the photo onto the grid's
rectangle. This is calibrated and locked by default so it can't be bumped by accident. Pass
--edit-layout to unlock it if the template or grid ever need re-dialing in:

    Tab              cycle which corner is being edited (shown in the window title)
    arrow keys       move the selected corner (Shift for x10)
    Ctrl+arrow keys  move all four corners together, i.e. reposition the whole grid at once

The window title and console both show all four current corners on every change, so you can report
back whatever lines up and it gets set as the new default.

Player-position marker (red dot + facing line): see FarmMapWindow._draw_player_dot below and
FARM_ORIGIN_X/Y's own comment for the world-position-to-farm-tile conversion.
"""
import math
import struct
import sys
from pathlib import Path

import frida

# Run directly as a script (python src/farm_map.py), sys.path[0] is this file's own directory
# (src/), not the project root -- so the sibling `app` package (project root/app/) isn't
# importable without this. app/main.py's own entry point avoids the issue by running via
# `python -m app.main` instead; this script doesn't have that luxury since it's meant to be run
# directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import hook_cache  # noqa: E402
from app.data_tables import load_table  # noqa: E402

GRID_BASE_CACHE_KEY = "farm_map_grid_base"
from PySide6.QtCore import Qt, QPointF, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QApplication, QWidget

AGENT_PATH = Path(__file__).parent / "farm_map_agent.js"
ASSETS_DIR = Path(__file__).parent.parent / "assets" / "farm"
TEMPLATE_PATH = ASSETS_DIR / "Farm Map Template.jpg"
# Wiki crop icons (2026-09-13, now that all 22 real crop-type ids are confirmed -- see
# data/tables/crops.json and data/pointer_map.md's "Tile struct fields" section) -- deliberately a
# SEPARATE folder from ASSETS_DIR: assets/farm/*.png are live-game screenshot captures (map
# template, tile-state/debris/large-object icons), not wiki icons, and downloading wiki icons into
# that same folder risked silently overwriting a confirmed live asset (see crops.json's own note).
CROP_ICONS_ASSETS_DIR = Path(__file__).parent.parent / "assets" / "crop_icons"

ROWS = 25
COLS = 43
TILE_STRIDE = 0x10
ROW_STRIDE = 0x2B0

# World-space -> tile-space conversion (confirmed 2026-09-10 by hand-tracing the hoe's tile-write
# code in Cheat Engine -- see data/pointer_map.md's "World position -> tile row/col conversion"
# section). Only meaningful while the player's Location field reads 2 ("My Farm").
FARM_LOCATION_ID = 2
FARM_ORIGIN_X = -150.0
FARM_ORIGIN_Y = -110.0
FARM_TILE_WORLD_SIZE = 10.0
PLAYER_DOT_RADIUS = 6.0
PLAYER_DOT_COLOR = QColor("red")
# Facing indicator: a short line from the dot, length expressed in logical (tile) units so it
# scales with the grid rather than being a fixed pixel length -- mapped through the same transform
# as the dot itself, so it stays correct even though the transform isn't a pure uniform scale.
FACING_INDICATOR_LENGTH_TILES = 0.35
FACING_INDICATOR_COLOR = QColor("#2f6fed")
FACING_INDICATOR_WIDTH = 2.0

TILLED_WATERED_STATE = 2  # see data/pointer_map.md's "Tile struct fields" section
# Harvest-ready stage -- confirmed live, day-by-day, 2026-09-13: every crop tops out at stage 5,
# with no exceptions (see data/pointer_map.md's "Growth table" section).
HARVEST_READY_STAGE = 5
# "Needs watering" / "needs harvest" flashes -- 500ms, matching app/windows/reminders.py's own
# weather flash cadence (WEATHER_FLASH_INTERVAL_MS) for a consistent flash speed across the app.
# Harvest (green) supersedes watering (red) on a tile that's both fully grown and unwatered.
FLASH_INTERVAL_MS = 500
NEEDS_WATER_FLASH_COLOR = QColor(255, 0, 0, 130)  # translucent red overlay
NEEDS_HARVEST_FLASH_COLOR = QColor(0, 220, 0, 130)  # translucent green overlay

# Lookup tables below live in data/tables/farm_map.json now (single source, see
# app/data_tables.py).
_FARM_MAP_TABLE = load_table("farm_map")

# Corner order used throughout -- must stay consistent between the logical rectangle and whatever
# quad it's mapped onto (see FarmMapWindow._transform).
CORNER_ORDER = _FARM_MAP_TABLE["CORNER_ORDER"]

# The grid's four corners in template pixel space -- calibrated live to match the template photo's
# real camera perspective (the farm's true shape in it is a trapezoid).
DEFAULT_CORNERS = {name: tuple(xy) for name, xy in _FARM_MAP_TABLE["DEFAULT_CORNERS"].items()}

# Icons are loaded at a fixed pixel size and then drawn through the perspective transform, which
# scales (and skews) them to fit each tile -- the source resolution just needs to be reasonable
# quality, it no longer needs to match the tile's on-screen size directly.
ICON_LOAD_SIZE = 64

# 0=untilled (color sampled from assets/Blank farm tile.png), 1=tilled/dry, 2=tilled/watered --
# colors are fallbacks only, real images come from STATE_ICON_FILES below.
STATE_COLORS = {int(k): QColor(v) for k, v in _FARM_MAP_TABLE["STATE_COLORS"].items()}
DEFAULT_TILE_COLOR = STATE_COLORS[0]

# state -> background image filename (assets/). Tilled and watered are two genuinely distinct
# textures -- watered briefly reused the dry image as a stand-in before this asset existed, don't
# revert that.
STATE_ICON_FILES = {int(k): v for k, v in _FARM_MAP_TABLE["STATE_ICON_FILES"].items()}

# object-type id (debris, occupant==8) -> image filename (assets/), for single-tile debris only.
DEBRIS_ICON_FILES = {int(k): v for k, v in _FARM_MAP_TABLE["DEBRIS_ICON_FILES"].items()}

# Debris ids that together form ONE large object spanning a 2x2 tile footprint (confirmed live:
# stumps and large stones each cover 4 tiles, one id per corner). Which specific id lands on
# which corner isn't known, so grouping is detected at render time by position instead: whichever
# of the 4 tiles has no same-group neighbor above or to its left is treated as the top-left corner,
# and the whole group is drawn there as one image spanning 2x2 cells. Stored in JSON as a list of
# {ids, file} entries (a JSON object key can't be a frozenset) -- rebuilt into the frozenset-keyed
# shape this file's own rendering code wants, right here.
LARGE_OBJECT_GROUPS = {
    frozenset(entry["ids"]): entry["file"] for entry in _FARM_MAP_TABLE["LARGE_OBJECT_GROUPS"]
}
LARGE_OBJECT_GROUP_OF_ID = {}
for _group_ids, _filename in LARGE_OBJECT_GROUPS.items():
    for _cid in _group_ids:
        LARGE_OBJECT_GROUP_OF_ID[_cid] = _group_ids

# crop-type index -> image filename (assets/farm/), for any occupant value that isn't 0 (empty) or
# 8 (debris). Growth stage isn't capped at 5 for every crop -- confirmed live (turnip seen at
# occupant==10) -- so one image is shown regardless of the exact stage number rather than assuming
# a fixed range. Explicit overrides only (none currently -- every crop-type id's icon comes from
# CROP_ICON_FILES_BY_ID below, wiki icons in assets/crop_icons/, merged in below CROP_ICON_FILES so
# an explicit override here would always win if one were ever needed). id 23 (Grass) is placed
# directly on untilled ground, always shows occupant==5.
CROP_ICON_FILES = {int(k): v for k, v in _FARM_MAP_TABLE["CROP_ICON_FILES"].items()}
GENERIC_CROP_ICON_KEY = 0
GRASS_CONTENT_ID = 23  # not a real crop -- never flashes red/green, see paintEvent below

# Wiki crop icons (data/tables/crops.json's own `icon` field, assets/crop_icons/), keyed by the
# confirmed crop-type id -- 2026-09-13, once all 22 real crop-type ids were identified live (see
# data/pointer_map.md's "Tile struct fields" section). `icon` is null for True Magic Red Flower (no
# dedicated wiki icon exists for it), so it's excluded here and falls back to
# GENERIC_CROP_ICON_KEY's image like any other still-unmapped id would.
CROP_ICON_FILES_BY_ID = {
    crop["id"]: crop["icon"]
    for crop in load_table("crops")["CROPS"]
    if crop["id"] is not None and crop["icon"]
}


def load_icon(filename, size=ICON_LOAD_SIZE, directory=ASSETS_DIR):
    pixmap = QPixmap(str(directory / filename))
    return pixmap.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def load_icons(file_map, size=ICON_LOAD_SIZE, directory=ASSETS_DIR):
    # Several dict values repeat the same filename (the 2x2 debris groups) -- load each unique
    # file once and share the QPixmap rather than re-decoding it per id.
    cache = {}
    icons = {}
    for key, filename in file_map.items():
        if filename not in cache:
            cache[filename] = load_icon(filename, size, directory)
        icons[key] = cache[filename]
    return icons


def parse_grid(raw):
    tiles = {}
    for row in range(ROWS):
        for col in range(COLS):
            offset = row * ROW_STRIDE + col * TILE_STRIDE
            if offset + 12 > len(raw):
                continue
            state, content, occupant = struct.unpack_from("<iii", raw, offset)
            tiles[(row, col)] = (state, content, occupant)
    return tiles


class FarmMapWindow(QWidget):
    def __init__(self, tiles_ref, corners=None, edit_layout=False, player_ref=None):
        super().__init__()
        self._tiles_ref = tiles_ref  # {"tiles": {...}}, mutated by the message handler
        # {"player": {"ok": True, "location": int, "x": float, "y": float} | None}, same mutated-
        # in-place-by-the-message-handler pattern as tiles_ref. None (the default) if the caller
        # never wires up a player position source -- the standalone CLI script doesn't yet.
        self._player_ref = player_ref if player_ref is not None else {"player": None}
        self._corners = dict(corners or DEFAULT_CORNERS)
        self._active_corner = 0  # index into CORNER_ORDER
        # Locked by default so the calibrated corners can't be bumped by accident -- pass
        # --edit-layout to re-enable if the template or grid ever needs re-dialing in. All the
        # editing logic below is unchanged, just gated on this flag.
        self._edit_layout = edit_layout
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._template = QPixmap(str(TEMPLATE_PATH))
        # Resizable -- paintEvent scales the whole scene to fit whatever size the window/dock area
        # actually is, preserving the template's aspect ratio. Just sets a sensible starting size;
        # drag an edge (or a dock area) to resize freely afterward.
        self.resize(self._template.size())

        self._state_icons = load_icons(STATE_ICON_FILES)
        self._debris_icons = load_icons(DEBRIS_ICON_FILES)
        self._crop_icons = load_icons(CROP_ICON_FILES)
        # Wiki icons (assets/crop_icons/) fill in every crop-type id NOT already covered by
        # CROP_ICON_FILES's explicit overrides (none currently) -- setdefault so an override
        # always wins over the generic wiki icon for the same id.
        for _crop_id, _icon in load_icons(CROP_ICON_FILES_BY_ID, directory=CROP_ICONS_ASSETS_DIR).items():
            self._crop_icons.setdefault(_crop_id, _icon)
        self._large_object_icons = {
            group_ids: load_icon(filename, ICON_LOAD_SIZE * 2)
            for group_ids, filename in LARGE_OBJECT_GROUPS.items()
        }

        self._update_title()

        timer = QTimer(self)
        timer.timeout.connect(self.update)
        # 100ms (was 500ms) -- speeds up the player-position dot's visual responsiveness; this
        # only controls how often already-arrived data gets repainted, not how often it's fetched,
        # so it's a cheap change with no extra Frida/game-read traffic.
        timer.start(100)

        # Red "needs watering" / green "needs harvest" flash -- a separate, slower timer just flips
        # the on/off flag; the 100ms timer above already repaints often enough to pick up the
        # change, no need for this one to also call update().
        self._flash_on = True
        flash_timer = QTimer(self)
        flash_timer.timeout.connect(self._toggle_flash)
        flash_timer.start(FLASH_INTERVAL_MS)

    def _toggle_flash(self):
        self._flash_on = not self._flash_on

    def sizeHint(self):
        return self._template.size()

    def minimumSizeHint(self):
        # Small enough to dock comfortably, big enough that the grid/labels stay legible.
        return self._template.size() / 4

    def _pixel_quad(self):
        return QPolygonF([QPointF(*self._corners[name]) for name in CORNER_ORDER])

    def _straight_rect(self):
        # Bounding box of the calibrated corners -- where the (plain, unwarped) grid renders.
        xs = [x for x, _ in self._corners.values()]
        ys = [y for _, y in self._corners.values()]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def _straight_quad(self):
        r = self._straight_rect()
        return QPolygonF([
            QPointF(r.left(), r.top()), QPointF(r.right(), r.top()),
            QPointF(r.right(), r.bottom()), QPointF(r.left(), r.bottom()),
        ])

    def _transform(self):
        # The grid itself is always a plain rectangle -- see _draw_template for the photo's warp.
        # This is in template-pixel space; _base_transform() below scales that into whatever the
        # widget's current (resizable) size actually is.
        logical = QPolygonF([
            QPointF(0, 0), QPointF(COLS, 0), QPointF(COLS, ROWS), QPointF(0, ROWS),
        ])
        return QTransform.quadToQuad(logical, self._straight_quad())

    def _base_transform(self):
        # Scales the whole scene (template-pixel space) to fit the widget's current size, letter-
        # boxing to preserve the template's aspect ratio rather than stretching it. Recomputed
        # every paint so resizing the window/dock area rescales everything live.
        template_size = self._template.size()
        if template_size.isEmpty() or self.width() <= 0 or self.height() <= 0:
            return QTransform()
        scale = min(self.width() / template_size.width(), self.height() / template_size.height())
        offset_x = (self.width() - template_size.width() * scale) / 2
        offset_y = (self.height() - template_size.height() * scale) / 2
        base = QTransform()
        base.translate(offset_x, offset_y)
        base.scale(scale, scale)
        return base

    def _draw_template(self, painter, base_transform):
        # Maps the four calibrated points in the photo (where the farm actually is, a trapezoid
        # due to camera perspective) onto the grid's plain straight rectangle, so the photo warps
        # to align with the grid instead of the other way around.
        image_transform = QTransform.quadToQuad(self._pixel_quad(), self._straight_quad())
        painter.save()
        # QTransform's `*` composes as "apply the left side first, then the right" -- confirmed
        # empirically, since combine=True's own ordering isn't obvious to eyeball for perspective
        # transforms. image_transform first (photo -> template-pixel space), then base_transform
        # (template-pixel space -> current widget size).
        painter.setTransform(image_transform * base_transform)
        painter.drawPixmap(0, 0, self._template)
        painter.restore()

    def _update_title(self):
        if not self._edit_layout:
            self.setWindowTitle("Farm Map  (layout locked -- pass --edit-layout to adjust)")
            return
        corner_name = CORNER_ORDER[self._active_corner]
        x, y = self._corners[corner_name]
        self.setWindowTitle(
            f"Farm Map  (editing {corner_name}: {x:.0f},{y:.0f}  -- "
            "Tab=switch corner, arrows=move it, Ctrl+arrows=move all, Shift=x10)"
        )

    def _print_corners(self):
        parts = ", ".join(f"{name}=({x:.0f},{y:.0f})" for name, (x, y) in self._corners.items())
        print(f"[corners] {parts}")

    def keyPressEvent(self, event):
        if not self._edit_layout:
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_Tab:
            self._active_corner = (self._active_corner + 1) % len(CORNER_ORDER)
            self._update_title()
            self.update()
            return

        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        step = 10 if shift else 1

        dx = dy = 0
        if event.key() == Qt.Key.Key_Left:
            dx = -step
        elif event.key() == Qt.Key.Key_Right:
            dx = step
        elif event.key() == Qt.Key.Key_Up:
            dy = -step
        elif event.key() == Qt.Key.Key_Down:
            dy = step
        else:
            super().keyPressEvent(event)
            return

        names = CORNER_ORDER if ctrl else [CORNER_ORDER[self._active_corner]]
        for name in names:
            x, y = self._corners[name]
            self._corners[name] = (x + dx, y + dy)

        self._update_title()
        self._print_corners()
        self.update()

    def _player_logical_pos(self):
        """Returns (col, row) in the same logical (fractional, 1-unit-per-tile) space `_transform`
        maps -- or None if there's no live player position, or the player isn't on the farm
        (Location != FARM_LOCATION_ID, the same gate the game's own tile-write code checks)."""
        player = self._player_ref.get("player")
        if not player or not player.get("ok"):
            return None
        if player.get("location") != FARM_LOCATION_ID:
            return None
        x, y = player.get("x"), player.get("y")
        if x is None or y is None:
            return None  # on farm, but the player entity pointer hasn't been latched yet
        col = (x - FARM_ORIGIN_X) / FARM_TILE_WORLD_SIZE
        row = (y - FARM_ORIGIN_Y) / FARM_TILE_WORLD_SIZE
        return col, row

    def _player_facing_tip(self, transform, col, row):
        """Screen point for the tip of the facing-indicator line, or None if there's no facing
        data. Normalizes the raw (facingDx, facingDy) world-unit vector to a fixed logical-space
        length, then maps it through `transform` -- same coordinate space as the dot -- so it's
        correct regardless of any non-uniform scale/warp baked into that transform."""
        player = self._player_ref.get("player")
        if not player:
            return None
        dx, dy = player.get("facingDx"), player.get("facingDy")
        if dx is None or dy is None:
            return None
        magnitude = math.hypot(dx, dy)
        if magnitude < 1e-6:
            return None
        logical_dx = dx / magnitude * FACING_INDICATOR_LENGTH_TILES
        logical_dy = dy / magnitude * FACING_INDICATOR_LENGTH_TILES
        return transform.map(QPointF(col + logical_dx, row + logical_dy))

    def _draw_player_dot(self, painter, transform):
        pos = self._player_logical_pos()
        if pos is None:
            return
        col, row = pos
        center = transform.map(QPointF(col, row))
        tip = self._player_facing_tip(transform, col, row)
        # Un-transformed (see _draw_fallback_text) so it stays a plain round dot/line regardless
        # of the grid's perspective warp -- same reasoning as the fallback text labels.
        painter.save()
        painter.resetTransform()
        if tip is not None:
            pen = QPen(FACING_INDICATOR_COLOR)
            pen.setWidthF(FACING_INDICATOR_WIDTH)
            painter.setPen(pen)
            painter.drawLine(center, tip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(PLAYER_DOT_COLOR)
        painter.drawEllipse(center, PLAYER_DOT_RADIUS, PLAYER_DOT_RADIUS)
        painter.restore()

    def _draw_fallback_text(self, painter, transform, col, row, text, color):
        # Drawn un-transformed at the tile's mapped screen center, at a normal readable point
        # size -- drawing it in logical (1-unit-per-tile) space under the perspective transform
        # (the original approach) made the font size interact with the transform's scale in a way
        # that rendered as basically illegible dots.
        center = transform.map(QPointF(col + 0.5, row + 0.5))
        painter.save()
        painter.resetTransform()
        font = QFont("Consolas", 9, QFont.Weight.Bold)
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text)
        baseline_y = center.y() + (metrics.ascent() - metrics.descent()) / 2
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QPointF(center.x() - width / 2, baseline_y), text)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101010"))  # letterbox bars if aspect ratio differs

        base = self._base_transform()
        self._draw_template(painter, base)

        tiles = self._tiles_ref["tiles"]
        # image_transform-then-base ordering (see _draw_template) applies here too: grid's own
        # transform (logical tile space -> template-pixel space) first, then base (-> widget size).
        transform = self._transform() * base

        painter.save()
        painter.setTransform(transform)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Pass 1: ground layer (background color/texture) for every tile, in logical (1 unit per
        # tile) coordinates -- the transform set above handles all scaling/perspective warping.
        for (row, col), (state, content, occupant) in tiles.items():
            rect = QRectF(col, row, 1, 1)
            # Filling the color first too, even when an icon is drawn, is a cheap safety net in
            # case the icon doesn't fully cover the cell.
            painter.fillRect(rect, STATE_COLORS.get(state, DEFAULT_TILE_COLOR))
            state_icon = self._state_icons.get(state)
            if state_icon is not None:
                painter.drawPixmap(rect, state_icon, QRectF(state_icon.rect()))

        # Pass 2: occupant layer (debris/crops), drawn over the ground -- separate pass so a
        # multi-tile object's image (drawn once, spanning 2x2) never gets painted over by a
        # neighboring tile's own ground layer drawn later in pass 1.
        fallback_texts = []  # (col, row, text, color), drawn after restoring the transform below
        for (row, col), (state, content, occupant) in tiles.items():
            if occupant == 8 and content in LARGE_OBJECT_GROUP_OF_ID:
                group = LARGE_OBJECT_GROUP_OF_ID[content]
                above = tiles.get((row - 1, col))
                left = tiles.get((row, col - 1))
                has_group_neighbor_above = above is not None and above[2] == 8 and above[1] in group
                has_group_neighbor_left = left is not None and left[2] == 8 and left[1] in group
                if not has_group_neighbor_above and not has_group_neighbor_left:
                    icon = self._large_object_icons[group]
                    painter.drawPixmap(QRectF(col, row, 2, 2), icon, QRectF(icon.rect()))
                continue

            glyph = None
            glyph_color = None
            icon = None
            needs_water = False
            needs_harvest = False

            if occupant == 8:  # debris
                icon = self._debris_icons.get(content)
                if icon is None:  # not yet identified -- fall back to the raw id as text
                    glyph, glyph_color = str(content), QColor("white")
            elif occupant not in (0, 8):  # crop -- always tops out at HARVEST_READY_STAGE
                # Any crop without its own asset yet shows the generic (Turnip) icon instead of a
                # text label -- more useful than just showing the raw value.
                icon = self._crop_icons.get(content, self._crop_icons[GENERIC_CROP_ICON_KEY])
                if icon is None:  # only possible if GENERIC_CROP_ICON_KEY's own asset is missing
                    glyph, glyph_color = f"{content}:{occupant}", QColor("#88ff88")
                # Grass isn't a real crop (rendered on untilled ground, but always shows
                # occupant==5 -- see GRASS_CONTENT_ID's own comment) -- never flashes.
                if content != GRASS_CONTENT_ID:
                    needs_harvest = occupant >= HARVEST_READY_STAGE
                    needs_water = not needs_harvest and state != TILLED_WATERED_STATE

            if icon is not None:
                painter.drawPixmap(QRectF(col, row, 1, 1), icon, QRectF(icon.rect()))
            elif glyph:
                fallback_texts.append((col, row, glyph, glyph_color))

            # Green "needs harvest" flash supersedes red "needs watering" -- drawn right over the
            # crop's own icon/glyph, in the same pass/transform, so it lines up with the tile
            # exactly.
            if needs_harvest and self._flash_on:
                painter.fillRect(QRectF(col, row, 1, 1), NEEDS_HARVEST_FLASH_COLOR)
            elif needs_water and self._flash_on:
                painter.fillRect(QRectF(col, row, 1, 1), NEEDS_WATER_FLASH_COLOR)

        painter.restore()

        # Pass 3: grid lines on top of everything (tile boundaries stay visible even with real
        # ground/icon images in every cell), drawn directly in screen space rather than through
        # the active transform. A cosmetic (width=0) pen under setTransform() rendered correctly
        # in the standalone script but turned out to be invisible whenever this widget was
        # embedded as a child (a dock tab, and still after tearing it out into its own floating
        # window) -- confirmed live, at any size. Rather than chase why a
        # cosmetic pen behaves differently for a nested child widget, sidestepped it: the grid is
        # always a plain, non-skewed rectangle (see _transform/_straight_quad), so its screen-space
        # bounding rect is exactly `transform.mapRect(...)` and the lines can be drawn with plain
        # arithmetic and a normal (non-cosmetic) pen, with no dependency on cosmetic-pen support in
        # whatever is compositing this widget.
        grid_rect = transform.mapRect(QRectF(0, 0, COLS, ROWS))
        if grid_rect.width() > 0 and grid_rect.height() > 0:
            cell_w = grid_rect.width() / COLS
            cell_h = grid_rect.height() / ROWS
            pen = QPen(QColor(0, 0, 0, 255))
            pen.setWidth(1)
            painter.setPen(pen)
            for i in range(COLS + 1):
                x = grid_rect.left() + i * cell_w
                painter.drawLine(QPointF(x, grid_rect.top()), QPointF(x, grid_rect.bottom()))
            for j in range(ROWS + 1):
                y = grid_rect.top() + j * cell_h
                painter.drawLine(QPointF(grid_rect.left(), y), QPointF(grid_rect.right(), y))

        # Fallback text, also un-transformed (see _draw_fallback_text), so it always reads at a
        # normal size regardless of the grid's current warp.
        for col, row, text, color in fallback_texts:
            self._draw_fallback_text(painter, transform, col, row, text, color)

        # Player position marker, drawn last so it's always visible on top of tiles/icons/grid.
        self._draw_player_dot(painter, transform)


def main():
    args = sys.argv[1:]
    address = None
    edit_layout = False
    if "--address" in args:
        i = args.index("--address")
        address = args[i + 1]
        args = args[:i] + args[i + 2 :]
    if "--edit-layout" in args:
        edit_layout = True
        args = [a for a in args if a != "--edit-layout"]

    if len(args) != 1:
        print(
            'Usage: python src/farm_map.py "EXACT_PROCESS_NAME.exe" [--address 0xADDRESS] '
            "[--edit-layout]"
        )
        sys.exit(1)

    process_name = args[0]

    print(f"Attaching to '{process_name}'...")
    session = frida.attach(process_name)

    # This standalone script attaches independently -- it has no shared session to receive an
    # automatic 'setFarmGridBase' from (see src/farm_map_agent.js's header comment and
    # data/pointer_map.md's "Farm tile array" section for why the app itself no longer needs a
    # manual/cached override of its own). --address (or a value cached on disk by a previous run
    # of the full app) is sent through the same manual-override path the app's own "Set" button
    # uses -- trusted as-is, no shape check.
    if address is not None:
        override_address = address
    else:
        override_address = hook_cache.get(GRID_BASE_CACHE_KEY)

    agent_source = AGENT_PATH.read_text()
    script = session.create_script(agent_source)

    tiles_ref = {"tiles": {}}

    def on_message(message, data):
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[frida error] {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "grid":
            if payload.get("ok"):
                raw = bytes.fromhex(payload["hex"])
                tiles_ref["tiles"] = parse_grid(raw)
            else:
                print(f"Failed to read grid: {payload.get('error')}")
        elif kind == "status":
            print(f"[status] {payload['message']}")

    script.on("message", on_message)
    script.load()

    if override_address is not None:
        script.post({"type": "setGridBase", "address": override_address})
    else:
        print(
            "[status] no --address given and no cached grid base on disk -- run the full app "
            "once (or pass --address) to get one"
        )

    app = QApplication(sys.argv)
    window = FarmMapWindow(tiles_ref, edit_layout=edit_layout)
    window.show()

    def close():
        session.detach()

    app.aboutToQuit.connect(close)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
