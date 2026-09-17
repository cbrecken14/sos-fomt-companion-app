// Combines several things in one Frida session: a live-polled read of the tile grid, the live
// player X/Y hook (duplicated from agent.js — kept separate since Frida agents are standalone
// scripts, not worth a shared-module build step for a prototype), and the spawn-tile hook (from
// PROGRESS.md Milestone 13; the floor-generation hook that used to also live here has been
// removed — see below and data/pointer_map.md's "Mine tile array" section).

const manualGridBase = GRID_BASE_OVERRIDE !== null;
let gridBase = GRID_BASE_OVERRIDE;
// Chunk 2's base — see floor_map.py for why: kept only for the rare case a real split is ever
// found; unused (null) in the normal case of a single contiguous grid.
const gridBase2 = GRID_BASE2_OVERRIDE;

function bytesToHex(bytes) {
    return Array.from(new Uint8Array(bytes))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');
}

function readGrid() {
    if (gridBase === null) return;
    try {
        let hex = bytesToHex(gridBase.readByteArray(TARGET_SIZE));
        if (gridBase2 !== null && TARGET_SIZE2 > 0) {
            hex += bytesToHex(gridBase2.readByteArray(TARGET_SIZE2));
        }
        send({ type: 'grid', ok: true, hex: hex });
    } catch (e) {
        send({ type: 'grid', ok: false, error: e.message });
    }
}

if (gridBase !== null) {
    send({ type: 'status', message: `using manually-provided grid address ${gridBase}` });
    readGrid();
} else {
    send({ type: 'status', message: 'waiting for a floor-generation event to find the grid automatically (go down a ladder)...' });
}

setInterval(readGrid, 1000);

// Loose on purpose -- same shape check src/core_hooks_agent.js/src/memory_viewer_agent.js use.
function looksLikeMineFloor(base) {
    try {
        const state = base.add(0x00).readU32();
        const rock = base.add(0x04).readU32();
        const soil = base.add(0x08).readU32();
        return state <= 10 && rock <= 40 && soil <= 40;
    } catch (e) {
        return false;
    }
}

// grid_base source of truth: SaveDataBase + 0xFB48 (see data/pointer_map.md's "Time Base / Mine
// Floor Base" section), pushed here from the app's shared core hooks session
// (app/game_session.py, src/core_hooks_agent.js) every time Save Data Base is (re)found -- which
// is every zone transition, not just once, since it rides on the same useSaveDataBase() call the
// zone-transition regrab hook drives. Mine-agnostic (pure SaveDataBase offset, shape-checked every
// time via looksLikeMineFloor before being trusted), continuously re-applied on every transition
// in either mine -- the sole grid_base source now that the floor-generation hook that used to also
// supply it (Spring-Mine-only, see data/pointer_map.md) has been removed.
function listenForSetDerivedMineFloorBase() {
    recv('setDerivedMineFloorBase', (message) => {
        if (!manualGridBase) {
            const candidate = ptr(message.address);
            if (looksLikeMineFloor(candidate)) {
                if (gridBase === null || !gridBase.equals(candidate)) {
                    gridBase = candidate;
                    send({ type: 'status', message: `grid base (derived): ${candidate}` });
                    readGrid();
                }
            }
        }
        listenForSetDerivedMineFloorBase();
    });
}
listenForSetDerivedMineFloorBase();

// --- Player facing direction (2026-09-11) -- reuses farm_map_agent.js's Entity Manager +
// vtable-match trick rather than investigating a facing field inside the position hook's own
// struct below from scratch: this technique is location-agnostic (the player's vtable is the same
// everywhere), so it was expected to carry over directly, and does. Finds the player's own Live
// Entity by matching its vtable pointer (unique per class/role), then takes the vector from its
// true position (+0x3B4/+0x3BC) to its currently-targeted tile (+0xA2C/+0xA34) as the facing
// direction -- see src/farm_map_agent.js for the full derivation of these offsets/vtable.
const ARRAY_BEGIN_OFFSET = 0x20;
const ARRAY_END_OFFSET = 0x28;
const MAX_ENTITIES = 512;
const PLAYER_VTABLE_OFFSET = 0x534300;
const PLAYER_ENTITY_X_OFFSET = 0x3b4;
const PLAYER_ENTITY_Y_OFFSET = 0x3bc;
const PLAYER_ENTITY_TARGET_X_OFFSET = 0xa2c;
const PLAYER_ENTITY_TARGET_Y_OFFSET = 0xa34;

let entityManagerPtr = null;

// The exe+532A0 hook itself now lives in src/core_hooks_agent.js (2026-09-16) -- this file used to
// carry its own independent copy, hooking the game a second time whenever Mine Map was open
// alongside Farm Map/Entity Viewer/the barn overlay (all of which also read this same pointer).
function listenForSetEntityManager() {
    recv('setEntityManager', (message) => {
        entityManagerPtr = ptr(message.address);
        listenForSetEntityManager();
    });
}
listenForSetEntityManager();

function findPlayerEntity() {
    if (entityManagerPtr === null) return null;
    const playerVtable = Process.mainModule.base.add(PLAYER_VTABLE_OFFSET);
    try {
        const begin = entityManagerPtr.add(ARRAY_BEGIN_OFFSET).readPointer();
        const end = entityManagerPtr.add(ARRAY_END_OFFSET).readPointer();
        const count = Math.max(0, Math.min(Math.floor(end.sub(begin).toInt32() / 8), MAX_ENTITIES));
        for (let i = 0; i < count; i++) {
            let entityPtr;
            try {
                entityPtr = begin.add(i * 8).readPointer();
            } catch (e) {
                continue;
            }
            if (entityPtr.isNull()) continue;
            try {
                if (entityPtr.readPointer().equals(playerVtable)) return entityPtr;
            } catch (e) {
                // Skip -- not every entity necessarily has this layout.
            }
        }
    } catch (e) {
        // Entity Manager not fully populated yet -- treated the same as "not found".
    }
    return null;
}

function readPlayerFacing() {
    const entity = findPlayerEntity();
    if (entity === null) return null;
    try {
        const x = entity.add(PLAYER_ENTITY_X_OFFSET).readFloat();
        const y = entity.add(PLAYER_ENTITY_Y_OFFSET).readFloat();
        const targetX = entity.add(PLAYER_ENTITY_TARGET_X_OFFSET).readFloat();
        const targetY = entity.add(PLAYER_ENTITY_TARGET_Y_OFFSET).readFloat();
        return { dx: targetX - x, dy: targetY - y };
    } catch (e) {
        return null;
    }
}

// --- Player position, Live Entity source -----------------------------------------------------
// Reuses the exact same Live Entity read farm_map_agent.js's own player marker uses (rendered
// on-screen position, +0x3B4/+0x3BC via findPlayerEntity() above) instead of relying solely on
// the write-hook below. Confirmed live (console trail: "spawn tile detected" fired on entering
// Mine Floor 0, but "dot anchored" didn't print until the player had already descended to Mine
// Floor 1) that the write-hook source only updates once the player actually takes a
// qualifying step -- so a freshly-entered floor's dot didn't anchor until whenever that first step
// happened to occur, sometimes not until the NEXT floor transition. This source instead reflects
// the entity's current rendered position continuously, the same way Farm Map's own marker behaves
// (always accurate immediately, no movement needed to "wake up"). Tagged with its own `src:
// 'entity'` so app/windows/mine_map.py can prefer it over the write-hook's own 'write'/'poll'
// messages below -- that hook is left fully intact (not removed) since src/floor_map.py, the
// frozen Tkinter fallback, still keys its own spawn-tile anchor off 'write' specifically and isn't
// being touched here.
//
// requestEntityPosition (2026-09-16): a second, on-demand path to the same read, triggered by
// app/windows/mine_map.py the instant it processes a 'spawnTile' message. Needed because the
// periodic poll below only sends when the entity's position CHANGES -- confirmed live the periodic
// poll can race ahead of the SPAWN_TILE_OFFSET hook (below) actually firing for the new floor: the
// entity's position had already settled at the new floor's real spawn spot and gone out over the
// periodic poll BEFORE spawnTile arrived, so mine_map.py's anchor paired that (correct) new-floor
// position with the OLD floor's still-unconsumed spawn tile -- one floor behind, every transition
// (confirmed against 3 consecutive floors, not just the first). Forcing a fresh read right after
// spawnTile is received guarantees the sample used for calibration is read only once mine_map.py
// already has that same floor's real spawn tile in hand, removing the race entirely.
{
    let lastEntityPos = { x: null, y: null };

    function readEntityPosition() {
        const entity = findPlayerEntity();
        if (entity === null) return null;
        try {
            return {
                x: entity.add(PLAYER_ENTITY_X_OFFSET).readFloat(),
                y: entity.add(PLAYER_ENTITY_Y_OFFSET).readFloat(),
            };
        } catch (e) {
            return null;
        }
    }

    function sendEntityPosition(pos) {
        lastEntityPos = pos;
        const facing = readPlayerFacing();
        send({
            type: 'position', x: pos.x, y: pos.y, src: 'entity',
            facingDx: facing ? facing.dx : null,
            facingDy: facing ? facing.dy : null,
        });
    }

    setInterval(() => {
        const pos = readEntityPosition();
        if (pos === null) return;
        if (lastEntityPos.x === pos.x && lastEntityPos.y === pos.y) return;
        sendEntityPosition(pos);
    }, 100);

    // Re-arms itself each time since recv() only fires once per registration -- same idiom every
    // other listener in this project's agent scripts uses.
    function listenForRequestEntityPosition() {
        recv('requestEntityPosition', () => {
            const pos = readEntityPosition();
            if (pos !== null) sendEntityPosition(pos);
            listenForRequestEntityPosition();
        });
    }
    listenForRequestEntityPosition();
}

// --- Player position (see agent.js for full explanation) ---
{
    const patternHex =
        '4C 8D 85 18 01 00 00 48 8D 95 28 01 00 00 48 8D 4C 24 50 E8 ?? ?? ?? ?? ' +
        '48 8B 44 24 50 48 8B 10 48 8B 44 24 58 48 8B 08 ' +
        'C5 FA 10 01 C5 FA 58 0A C5 FA 11 0A ' +
        'C5 FA 10 41 04 C5 FA 58 4A 04 C5 FA 11 4A 04 ' +
        'C5 FA 10 41 08 C5 FA 58 4A 08 C5 FA 11 4A 08';
    const X_WRITE_OFFSET = 0x30;
    const mod = Process.getModuleByName('STORY OF SEASONS Friends of Mineral Town.exe');

    // Scanning all of process memory for this pattern is the slow part of attaching (a few
    // seconds) -- but the pattern lives inside the game's own module code,
    // so once found, its offset *from the module's base* is worth caching: module-relative offsets
    // stay valid across game restarts (only the module's own load base moves, under ASLR -- Frida
    // re-resolves that fresh every attach), unlike a raw absolute address. See PROGRESS.md
    // Milestone 12 for the same insight applied to the floor-size/grid-base hook below.
    //
    // Still validated before being trusted, in case a game update ever moves this code: reject a
    // cached offset whose bytes no longer match the pattern (wildcard bytes always pass) and fall
    // back to a full scan, same as if there were no cache at all.
    function patternMatchesAt(address) {
        const parts = patternHex.split(' ');
        try {
            const view = new Uint8Array(address.readByteArray(parts.length));
            for (let i = 0; i < parts.length; i++) {
                if (parts[i] === '??') continue;
                if (view[i] !== parseInt(parts[i], 16)) return false;
            }
            return true;
        } catch (e) {
            return false;
        }
    }

    let found = null;
    if (POSITION_HOOK_OFFSET_OVERRIDE !== null) {
        const candidate = mod.base.add(POSITION_HOOK_OFFSET_OVERRIDE);
        if (patternMatchesAt(candidate)) {
            found = candidate;
            send({ type: 'status', message: 'player position: found instantly via cached offset' });
        } else {
            send({ type: 'status', message: 'player position: cached offset no longer valid, scanning fresh...' });
        }
    }

    if (found === null) {
        const ranges = Process.enumerateRanges('r--');
        for (const range of ranges) {
            try {
                const matches = Memory.scanSync(range.base, range.size, patternHex);
                if (matches.length > 0) {
                    found = matches[0].address;
                    break;
                }
            } catch (e) {
                // ignore and continue
            }
        }
        if (found !== null) {
            send({ type: 'positionHookOffsetFound', offset: found.sub(mod.base).toString() });
        }
    }

    if (found === null) {
        send({ type: 'error', label: 'Player Position', message: 'pattern not found anywhere in process memory' });
    } else {
        const hookAddr = found.add(X_WRITE_OFFSET);
        const last = {};
        let cachedBase = null;

        function reportPosition(base, src) {
            const x = base.readFloat();
            const y = base.add(8).readFloat();
            // Sent as one atomic message (not separate X/Y messages) so the Python side never
            // sees a fresh X paired with a stale Y (or vice versa) — that mismatch was causing
            // spawn-tile calibration to fire on a mixed, wrong position.
            if (last.x !== x || last.y !== y) {
                last.x = x;
                last.y = y;
                const facing = readPlayerFacing();
                send({
                    type: 'position', x, y, src,
                    facingDx: facing ? facing.dx : null,
                    facingDy: facing ? facing.dy : null,
                });
            }
        }

        // Confirmed live (2026-09-05, see PROGRESS.md Milestone 13): this hook only reflects a
        // trustworthy position while the player is actively walking (it's the movement-
        // integration code) — immediately after a floor loads, before the first step, reads are
        // an exact (0,0) sentinel or other leftover garbage, not a real spawn position. `src`
        // ('write' vs 'poll') is kept on each message for any future troubleshooting.
        let reportedBase = false;
        Interceptor.attach(hookAddr, {
            onEnter(args) {
                cachedBase = this.context.rdx;
                if (!reportedBase) {
                    reportedBase = true;
                    send({ type: 'status', message: `Player position struct at ${cachedBase} (X at +0x0, Y at +0x8)` });
                }
                reportPosition(cachedBase, 'write');
            },
        });

        // The write only happens while actually moving, so on a fresh floor (before the first
        // step) the position would otherwise stay stuck on the previous floor's last value.
        // Poll the same address directly so it catches up immediately even while standing still.
        setInterval(() => {
            if (cachedBase) reportPosition(cachedBase, 'poll');
        }, 200);
    }
}

// The floor-generation call-site hook that used to live here (`exe+2E7C1D`, R14D=cols/R15D=rows)
// is removed -- it only ever fired during Spring Mine's floor generation, never Lake Mine's. Real
// floor size is now derived from the tile buffer's own data instead (see
// app/windows/mine_map.py's infer_floor_size() and data/pointer_map.md's "Mine tile array"
// section), which works identically in both mines with no hook needed.

// --- Player spawn tile (row/col) — same investigation as floor size, a bit further along in the
// same floor-generation function. Right here it writes a 2-byte (col, row) pair into its own
// output parameter — confirmed live to be the exact tile the player spawns on (not the exit
// ladder, which is a separate nearby tile). Byte order corrected 2026-09-11: originally read as
// (row, col) -- Milestone 13's confirmation apparently only checked that the two bytes identified
// the right TILE, not which byte was which axis, and on this game's more square/symmetric test
// floor that distinction wouldn't have shown up. Caught live once origin-anchoring launched on
// several differently-shaped floors: standing at the true top-left tile (row=0, col=0) came back
// as roughly (col, -col) instead of (0, 0) every time -- the exact signature of the two axes being
// swapped, not per-floor noise.
// Feeds app/windows/mine_map.py's automatic per-floor origin-anchor (2026-09-11) -- the moment a
// floor loads, the player's live position is guaranteed to match this known tile, no user action
// needed (a fixed origin can't work here: two different rooms measured live needed two different
// origins, see that file's WORLD_UNITS_PER_TILE comment). An earlier version of that anchor made
// the dot visibly jump/"teleport" right after a floor loaded; fixed on the Python side by hiding
// the dot (resetting the origin to "unknown") the instant a new floor is detected, rather than
// drawing the previous floor's now-stale origin until this fires.
{
    const SPAWN_TILE_OFFSET = 0x2e623a;
    const mod = Process.getModuleByName('STORY OF SEASONS Friends of Mineral Town.exe');
    const hookAddr = mod.base.add(SPAWN_TILE_OFFSET);

    Interceptor.attach(hookAddr, {
        onEnter() {
            const rsi = this.context.rsi;
            const col = rsi.readU8();
            const row = rsi.add(1).readU8();
            // Status line (not just the raw spawnTile message) makes it visible in the console
            // whether this hook fires at all for a given floor -- e.g. to check whether it fires
            // on the very first floor entered from the surface, same as every later floor-to-floor
            // transition (useful for diagnosing why the player dot sometimes doesn't appear until
            // the second floor of a session).
            send({ type: 'status', message: `spawn tile detected: row ${row} col ${col}` });
            send({ type: 'spawnTile', row, col });
        },
    });
}
