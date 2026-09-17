"""
Mine floor map overlay: shows the whole floor's tile grid (ladder / black grass / money /
rock-with-ore icons) plus a live marker for the player's position.

Usage:
    python src/floor_map.py "EXACT_PROCESS_NAME.exe" [--rows N] [--cols N] [--address 0xADDRESS]

By default, the tile grid's location is found automatically from the same floor-generation hook
used for floor size and spawn tile (see floor_map_agent.js / PROGRESS.md Milestone 13) — it fires
the moment a floor generates, so just go down any ladder after launching this and the grid
populates on its own. No more tilling a tile to trigger detection, and no more per-floor manual
--address hunting in the ordinary case (a new address is only picked up automatically when the
game actually allocates a new one, e.g. a bigger room — see Milestone 9/12).

Pass --address 0xADDRESS (the grid_base for the *current* game session, found by hand — see
PROGRESS.md for how) to skip auto-detection entirely and read from that address directly. Useful
while auto-detection is unreliable, or just to get moving quickly. This address is per-session —
it changes on every game restart.

Floor size (rows/cols) is fully automatic (see PROGRESS.md Milestone 13): the game's own
floor-generation code is hooked directly for the real row/col count, so the window resizes itself
the moment you go down a ladder. --rows/--cols only matter as the initial guess shown before your
first floor transition this session (e.g. if you attach mid-floor without going through a new
ladder) — pass them if you already know the current floor's size, otherwise they default to
28x28 (the max) and get corrected automatically on the next floor change.

Player-position calibration is automatic (see PROGRESS.md Milestone 13): on a fresh floor, it waits
for the confirmed spawn tile plus the first *movement-sourced* position reading (i.e. one that
arrived because the player actually took a step, not the background poll) and derives the offset
from that pair — a step or two after spawning, the marker should already be correctly placed. The
'C' hotkey (stand on the visible 'E' exit-ladder tile, press C) is kept as a manual fallback, e.g.
if you attach mid-floor without going through a new ladder.
"""

import struct
import sys
import tkinter as tk
from fractions import Fraction
from pathlib import Path

import frida

AGENT_PATH = Path(__file__).parent / "floor_map_agent.js"
ORE_ICON_DIR = Path(__file__).parent.parent / "assets" / "ore_icons"

TILE_STRIDE = 0x0C
ROW_STRIDE = 0x150
CELL_SIZE = 24
MAX_ROWS = 28  # confirmed max buffer size — always fetch this many rows regardless of --rows,
# since the real floor's size (now detected live) is never bigger than this.

WORLD_UNITS_PER_TILE = 10.0

# Visual margin around the grid representing the room's outer wall — the dot can wander into
# this area (near an edge) without looking like it's floating off the edge of the window.
WALL_MARGIN_TILES = 1.0
WALL_COLOR = "#4a3728"
MARGIN_PX = WALL_MARGIN_TILES * CELL_SIZE

# Directly-measured world coordinate of (row 0, col 0), keyed by (rows, cols) as passed on the
# command line. Measured by standing on that exact tile and reading precise Player X/Y.
## CORRECTION 2026-09-05: these were measured back when the row formula had an undocumented
# "+0.5" baked in to fake centering (only on the row axis, never the column axis — the actual bug
# behind the "dot at the corner, not center" report). Now that both axes center symmetrically at
# render time instead, these stored Y offsets need the same -5 (half a tile) shift to keep
# matching. Adjusted below; not reverified live since these smaller tiers aren't in active use.
CALIBRATION = {
    (6, 13): (-64.70986938476562, -29.79),  # "smallest" tier
    (14, 13): (-64.56, -70.98999786376953),  # "biggish"(?) tier
    (28, 28): (-140, -141),  # "biggest" tier — from a top-left corner reading
}
DEFAULT_OFFSET = (-64.70986938476562, -29.79)  # fallback guess for an uncalibrated size

SOIL_CONTENT = {
    1: ("L", "#3ad13a"),  # ladder
    2: ("$", "#e0c020"),  # money
    0x0B: ("g", "#1f7a1f"),  # black grass
}

# state (not soil_content!) values 5/6 confirmed live: an uncovered/hidden hole.
# Same red "X" for both, same as exit ladder (3) and rock (4), this is a *state* value, not a
# soil_content one — got this wrong on the first pass by assuming soil_content.
HOLE_STATES = {5, 6}
ROCK_COLOR = "#888888"

MYTHIC_ORE_ID = 23
GODDESS_JEWEL_ID = 32
# Exceptionally rare finds — flashed on the map (see flash_rare_ore()) so they're not missed.
RARE_FLASH_IDS = {MYTHIC_ORE_ID, GODDESS_JEWEL_ID}
MYTHIC_FLASH_COLORS = ("#fff200", "#ff2b2b")  # alternating flash border colors
MYTHIC_FLASH_MS = 400

# rock_content (the ore-type number stored in each rock tile) -> icon filename in
# assets/ore_icons/. This ID is an internal mine-specific enum, not the game's normal item ID, so
# it has to be built up empirically — break a rock showing a given number in-game and see what
# ore you actually get, then add the mapping here. Unmapped numbers just show as plain text
# (the raw number), same as before.
ORE_ICONS = {
    0: "Empty Rock.png",
    12: "scrap_ore.png",
    13: "copper.png",
    14: "silver.png",
    15: "gold.png",
    16: "mithril.png",
    17: "orichalcum.png",
    18: "adamantite.png",
    MYTHIC_ORE_ID: "mythic_ore.png",  # confirmed live
    GODDESS_JEWEL_ID: "goddess_jewel.png",  # confirmed live
    # Moonstone still unconfirmed.
}

# soil_content -> icon filename, same idea as ORE_ICONS but for the soil-content field (ladder,
# money, black grass) instead of the rock-content field.
SOIL_ICONS = {
    0x0B: "black_grass.png",
}


def load_icon(path, target_size):
    """Load a PNG and rescale it to roughly target_size (square) using Tk's own zoom/subsample —
    no PIL dependency. Only approximate (limited to /8 fractions) since Tk only resizes by
    integer ratios, but plenty close enough for a small map icon."""
    img = tk.PhotoImage(file=str(path))
    orig = img.width()
    if orig == target_size:
        return img
    frac = Fraction(target_size, orig).limit_denominator(8)
    if frac.numerator != 1:
        img = img.zoom(frac.numerator, frac.numerator)
    if frac.denominator != 1:
        img = img.subsample(frac.denominator, frac.denominator)
    return img


def parse_grid(raw, rows, cols):
    tiles = {}
    for row in range(rows):
        for col in range(cols):
            offset = row * ROW_STRIDE + col * TILE_STRIDE
            if offset + 12 > len(raw):
                continue
            (state,) = struct.unpack("<i", raw[offset : offset + 4])
            (rock_content,) = struct.unpack("<i", raw[offset + 4 : offset + 8])
            (soil_content,) = struct.unpack("<i", raw[offset + 8 : offset + 12])
            tiles[(row, col)] = (state, rock_content, soil_content)
    return tiles


def room_offset(rows, cols):
    """World coordinate of this room's (row 0, col 0) tile — see CALIBRATION above."""
    key = (rows, cols)
    if key not in CALIBRATION:
        print(
            f"[calibration] No measured offset for room size {key} — using the fallback guess, "
            "the player dot is likely off. Stand on the true top-left tile and report the exact "
            "Player X/Y so this size can be calibrated."
        )
        return DEFAULT_OFFSET
    return CALIBRATION[key]


def main():
    args = sys.argv[1:]
    rows = 28
    cols = 28
    address = None
    address2 = None
    if "--rows" in args:
        i = args.index("--rows")
        rows = int(args[i + 1])
        args = args[:i] + args[i + 2 :]
    if "--cols" in args:
        i = args.index("--cols")
        cols = int(args[i + 1])
        args = args[:i] + args[i + 2 :]
    if "--address" in args:
        i = args.index("--address")
        address = args[i + 1]
        args = args[:i] + args[i + 2 :]
    if "--address2" in args:
        i = args.index("--address2")
        address2 = args[i + 1]
        args = args[:i] + args[i + 2 :]

    if len(args) != 1:
        print(
            'Usage: python src/floor_map.py "EXACT_PROCESS_NAME.exe" [--rows N] [--cols N] '
            "[--address 0xADDRESS] [--address2 0xADDRESS]"
        )
        sys.exit(1)

    process_name = args[0]
    # CORRECTION 2026-09-05: earlier theorized the grid was always split across two ~14-row
    # memory chunks. That was wrong — it was really just one wrong address for a specific room
    # (see PROGRESS.md). --address2 support is kept for the rare case a real split is ever found,
    # but only kicks in when explicitly given; otherwise read the full room in one contiguous
    # block from --address, same as before this whole detour.
    CHUNK_ROWS = 14
    if address2:
        chunk1_rows = min(MAX_ROWS, CHUNK_ROWS)
        chunk2_rows = max(0, MAX_ROWS - CHUNK_ROWS)
    else:
        chunk1_rows = MAX_ROWS
        chunk2_rows = 0
    size = chunk1_rows * ROW_STRIDE
    size2 = chunk2_rows * ROW_STRIDE
    offset_x, offset_y = room_offset(rows, cols)
    # Mutable so the manual 'C' hotkey, the spawn-tile auto-calibration, and the live floor-size
    # hook below can all update state as messages arrive. Defined here (before script.load())
    # rather than later, since Frida's background thread can start delivering messages as soon as
    # the script loads.
    calibration = {"offset_x": offset_x, "offset_y": offset_y}
    floor_state = {"rows": rows, "cols": cols}
    flash_phase = {"on": True}  # toggled by flash_rare_ore() below
    spawn_pending = {"have": False, "row": None, "col": None}

    print(f"Attaching to '{process_name}'...")
    session = frida.attach(process_name)

    agent_source = AGENT_PATH.read_text()
    agent_source = agent_source.replace("TARGET_SIZE2", str(size2))
    agent_source = agent_source.replace("TARGET_SIZE", str(size))
    grid_override = f'ptr("{address}")' if address else "null"
    agent_source = agent_source.replace("GRID_BASE_OVERRIDE", grid_override)
    grid2_override = f'ptr("{address2}")' if address2 else "null"
    agent_source = agent_source.replace("GRID_BASE2_OVERRIDE", grid2_override)
    # This standalone script doesn't cache the player-position hook offset (see app/mine_map.py
    # for that) -- always do the full scan.
    agent_source = agent_source.replace("POSITION_HOOK_OFFSET_OVERRIDE", "null")
    script = session.create_script(agent_source)
    if address:
        print(f"Using manually-provided grid address {address}.")
        if address2:
            print(f"Using manually-provided chunk-2 address {address2}.")
    else:
        print(
            "Waiting to find the mine grid — till any tile once if it doesn't appear right away "
            "(only needed the first time this game session)."
        )

    player = {"x": None, "y": None}
    tiles = {}

    def calibrate_from_ladder():
        """Derive the calibration offset assuming the player is standing exactly on the exit
        ladder tile right now. This is the manual 'C' hotkey's implementation — a fallback for
        when the automatic spawn-tile calibration hasn't fired (e.g. attaching mid-floor)."""
        if player["x"] is None or player["y"] is None:
            return False
        ladder = next((rc for rc, (state, _, _) in tiles.items() if state == 3), None)
        if ladder is None:
            return False
        row, col = ladder
        calibration["offset_x"] = player["x"] - col * WORLD_UNITS_PER_TILE
        calibration["offset_y"] = player["y"] - row * WORLD_UNITS_PER_TILE
        print(
            f"[calibration] offset_x={calibration['offset_x']:.4f}, "
            f"offset_y={calibration['offset_y']:.4f}  (from ladder at row={row}, col={col}, "
            f"player at {player['x']:.2f},{player['y']:.2f})"
        )
        return True

    def on_message(message, data):
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[frida error] {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "status":
            print(f"[status] {payload['message']}")
        elif kind == "grid":
            if payload.get("ok"):
                raw = bytes.fromhex(payload["hex"])
                tiles.update(parse_grid(raw, floor_state["rows"], floor_state["cols"]))
            else:
                print(f"Failed to read grid: {payload.get('error')}")
        elif kind == "floorSize":
            floor_state["rows"] = payload["rows"]
            floor_state["cols"] = payload["cols"]
            tiles.clear()  # stale tiles from a previous, differently-sized floor
            print(f"[floor] size: {payload['rows']} rows x {payload['cols']} cols")
        elif kind == "spawnTile":
            spawn_pending["row"] = payload["row"]
            spawn_pending["col"] = payload["col"]
            spawn_pending["have"] = True
            print(f"[floor] spawn tile: row={payload['row']}, col={payload['col']}")
        elif kind == "position":
            player["x"] = payload["x"]
            player["y"] = payload["y"]
            # Auto-calibrate from the confirmed spawn tile, but only using a *movement-sourced*
            # reading (src == "write", meaning the player actually took a step) — never the
            # background poll. Confirmed live (2026-09-05, see PROGRESS.md Milestone 13): a poll
            # reading taken right after a floor loads is unreliable (an exact (0,0) sentinel or
            # other leftover garbage), while the first write-sourced reading is at most one
            # frame's movement away from the true spawn tile — close enough for this overlay, and
            # available immediately without needing the player to press anything.
            if (
                spawn_pending["have"]
                and payload.get("src") == "write"
                and not (payload["x"] == 0.0 and payload["y"] == 0.0)
            ):
                row, col = spawn_pending["row"], spawn_pending["col"]
                calibration["offset_x"] = payload["x"] - col * WORLD_UNITS_PER_TILE
                calibration["offset_y"] = payload["y"] - row * WORLD_UNITS_PER_TILE
                spawn_pending["have"] = False
                print(
                    f"[calibration] Auto-calibrated from confirmed spawn tile (row={row}, "
                    f"col={col}): offset_x={calibration['offset_x']:.4f}, "
                    f"offset_y={calibration['offset_y']:.4f}"
                )
        elif kind == "error":
            print(f"[error] {payload.get('label')}: {payload.get('message')}")

    script.on("message", on_message)
    script.load()

    root = tk.Tk()
    root.title("Mine Floor Map")
    root.attributes("-topmost", True)

    canvas = tk.Canvas(
        root,
        width=floor_state["cols"] * CELL_SIZE + 2 * MARGIN_PX,
        height=floor_state["rows"] * CELL_SIZE + 2 * MARGIN_PX,
        bg=WALL_COLOR,
        highlightthickness=0,
    )
    canvas.pack()
    drawn_size = {"rows": floor_state["rows"], "cols": floor_state["cols"]}

    coord_label = tk.Label(
        root,
        text="World X: --  Y: --   Grid row: --  col: --",
        bg="#1a1a1a",
        fg="#e0e0e0",
        font=("Consolas", 9),
        anchor="w",
    )
    coord_label.pack(fill="x")

    ore_images = {}
    for rock_id, filename in ORE_ICONS.items():
        path = ORE_ICON_DIR / filename
        if path.exists():
            ore_images[rock_id] = load_icon(path, CELL_SIZE)
        else:
            print(f"[icons] Missing {path}, falling back to plain number for rock_content={rock_id}")

    soil_images = {}
    for soil_id, filename in SOIL_ICONS.items():
        path = ORE_ICON_DIR / filename
        if path.exists():
            soil_images[soil_id] = load_icon(path, CELL_SIZE)
        else:
            print(f"[icons] Missing {path}, falling back to plain label for soil_content={soil_id}")

    def draw_grid():
        # Resizing the canvas here (rather than in on_message, which runs on Frida's own
        # background thread) keeps all Tkinter widget access on the main thread.
        if (drawn_size["rows"], drawn_size["cols"]) != (floor_state["rows"], floor_state["cols"]):
            drawn_size["rows"] = floor_state["rows"]
            drawn_size["cols"] = floor_state["cols"]
            canvas.config(
                width=floor_state["cols"] * CELL_SIZE + 2 * MARGIN_PX,
                height=floor_state["rows"] * CELL_SIZE + 2 * MARGIN_PX,
            )
        def draw_ladder_badge(cx, cy):
            # A solid dark badge behind the letter, drawn only while a rock still conceals this
            # ladder (state == 4 and soil_content == 1 right now) — the plain green "L" alone
            # could get lost against a similarly-colored ore icon. Guarantees contrast regardless
            # of the ore icon's own colors.
            label, color = SOIL_CONTENT[1]
            badge_r = CELL_SIZE * 0.34
            canvas.create_oval(
                cx - badge_r,
                cy - badge_r,
                cx + badge_r,
                cy + badge_r,
                fill="#101010",
                outline=color,
                width=2,
                tags="tile",
            )
            canvas.create_text(
                cx, cy, text=label, fill=color, font=("Consolas", 10, "bold"), tags="tile"
            )

        canvas.delete("tile")
        for (row, col), (state, rock_content, soil_content) in tiles.items():
            x0 = MARGIN_PX + col * CELL_SIZE
            y0 = MARGIN_PX + row * CELL_SIZE
            x1, y1 = x0 + CELL_SIZE, y0 + CELL_SIZE
            if state == 3:  # exit ladder (leads out of the mine, distinct from a found ladder)
                canvas.create_rectangle(x0, y0, x1, y1, fill="#303030", outline="#444", tags="tile")
                canvas.create_text(
                    x0 + CELL_SIZE / 2,
                    y0 + CELL_SIZE / 2,
                    text="E",
                    fill="#40c0ff",
                    font=("Consolas", 10, "bold"),
                    tags="tile",
                )
            elif state in HOLE_STATES:  # uncovered (5) or hidden (6) hole
                canvas.create_rectangle(x0, y0, x1, y1, fill="#303030", outline="#444", tags="tile")
                canvas.create_text(
                    x0 + CELL_SIZE / 2,
                    y0 + CELL_SIZE / 2,
                    text="X",
                    fill="#ff2020",
                    font=("Consolas", 10, "bold"),
                    tags="tile",
                )
            elif state == 4:  # rock
                canvas.create_rectangle(x0, y0, x1, y1, fill=ROCK_COLOR, outline="#444", tags="tile")
                if rock_content in ore_images:
                    canvas.create_image(
                        x0 + CELL_SIZE / 2,
                        y0 + CELL_SIZE / 2,
                        image=ore_images[rock_content],
                        tags="tile",
                    )
                else:
                    canvas.create_text(
                        x0 + CELL_SIZE / 2,
                        y0 + CELL_SIZE / 2,
                        text=str(rock_content),
                        fill="white",
                        font=("Consolas", 8),
                        tags="tile",
                    )
                if rock_content in RARE_FLASH_IDS:
                    # A hollow (unfilled) border drawn on top of the icon, rather than tinting the
                    # tile's own background, so it stays visible regardless of whether the icon
                    # image happens to cover the whole cell. flash_rare_ore() re-colors it via the
                    # "rare_flash" tag on a faster cadence than this function's own 1s redraw.
                    flash_color = MYTHIC_FLASH_COLORS[0] if flash_phase["on"] else MYTHIC_FLASH_COLORS[1]
                    canvas.create_rectangle(
                        x0 + 1,
                        y0 + 1,
                        x1 - 1,
                        y1 - 1,
                        outline=flash_color,
                        width=3,
                        tags=("tile", "rare_flash"),
                    )
                if soil_content == 1:
                    # Still under the rock right now — badge style for legibility against the
                    # ore icon (see draw_ladder_badge).
                    draw_ladder_badge(x0 + CELL_SIZE / 2, y0 + CELL_SIZE / 2)
            elif state == 2:
                # The real, now-uncovered ladder — soil_content resets to 0 floor-wide the moment
                # this fires (confirmed live, which is why every other predicted "L" correctly
                # disappears at the same time), so this can't be reached through the
                # soil_content branch below. Render it as the same plain green "L" a predicted
                # ladder always showed, since this tile itself should keep showing on the map.
                label, color = SOIL_CONTENT[1]
                canvas.create_rectangle(x0, y0, x1, y1, fill="#303030", outline="#444", tags="tile")
                canvas.create_text(
                    x0 + CELL_SIZE / 2,
                    y0 + CELL_SIZE / 2,
                    text=label,
                    fill=color,
                    font=("Consolas", 10, "bold"),
                    tags="tile",
                )
            elif soil_content in SOIL_CONTENT:
                label, color = SOIL_CONTENT[soil_content]
                canvas.create_rectangle(x0, y0, x1, y1, fill="#303030", outline="#444", tags="tile")
                if soil_content in soil_images:
                    canvas.create_image(
                        x0 + CELL_SIZE / 2,
                        y0 + CELL_SIZE / 2,
                        image=soil_images[soil_content],
                        tags="tile",
                    )
                else:
                    canvas.create_text(
                        x0 + CELL_SIZE / 2,
                        y0 + CELL_SIZE / 2,
                        text=label,
                        fill=color,
                        font=("Consolas", 10, "bold"),
                        tags="tile",
                    )
            elif state == 1:  # tilled
                canvas.create_rectangle(x0, y0, x1, y1, fill="#4a4a4a", outline="#444", tags="tile")
            else:
                canvas.create_rectangle(x0, y0, x1, y1, fill="#303030", outline="#444", tags="tile")
        canvas.tag_raise("player")

    player_marker = canvas.create_oval(0, 0, CELL_SIZE, CELL_SIZE, fill="#ff3030", outline="", tags="player")

    def update_player_marker():
        if player["x"] is not None and player["y"] is not None:
            col = (player["x"] - calibration["offset_x"]) / WORLD_UNITS_PER_TILE
            row = (player["y"] - calibration["offset_y"]) / WORLD_UNITS_PER_TILE
            # +0.5 on both axes here (not baked into col/row themselves) centers the dot within
            # its cell, matching how draw_grid positions each tile's rectangle from the same
            # col/row values.
            cx = MARGIN_PX + (col + 0.5) * CELL_SIZE
            cy = MARGIN_PX + (row + 0.5) * CELL_SIZE
            r = CELL_SIZE / 4
            canvas.coords(player_marker, cx - r, cy - r, cx + r, cy + r)
            coord_label.config(
                text=f"World X: {player['x']:.2f}  Y: {player['y']:.2f}   "
                f"Grid row: {row:.2f}  col: {col:.2f}   (press C while standing on the exit "
                "ladder to (re)calibrate)"
            )
        root.after(100, update_player_marker)

    def calibrate_hotkey(_event=None):
        if not calibrate_from_ladder():
            print(
                "[calibration] Can't calibrate yet — need both a live position and a found "
                "exit ladder tile in the grid."
            )

    root.bind("<KeyPress-c>", calibrate_hotkey)

    def refresh_loop():
        draw_grid()
        root.after(1000, refresh_loop)

    def flash_rare_ore():
        # Runs independently of refresh_loop's 1s grid redraw so the flash itself is snappier;
        # itemconfig on a tag with no current matches (no rare ore on this floor) is a no-op.
        flash_phase["on"] = not flash_phase["on"]
        color = MYTHIC_FLASH_COLORS[0] if flash_phase["on"] else MYTHIC_FLASH_COLORS[1]
        canvas.itemconfig("rare_flash", outline=color)
        root.after(MYTHIC_FLASH_MS, flash_rare_ore)

    refresh_loop()
    update_player_marker()
    flash_rare_ore()

    def close(_event=None):
        session.detach()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Escape>", close)

    root.mainloop()


if __name__ == "__main__":
    main()
