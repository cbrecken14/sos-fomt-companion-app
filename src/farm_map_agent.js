// Reads the farm's tile grid from a grid_base address supplied by the shared session
// (app/game_session.py's derived FarmGridBase = SaveDataBase + 0x78C4, src/core_hooks_agent.js) --
// no independent hooking or shape-validation of that value here; the app's own tab just uses
// whatever the shared session derives.
//
// A manual override ('setGridBase') still exists as an escape hatch, used only by
// src/farm_map.py's standalone CLI script (its --address flag or a cached address from disk),
// since that script attaches independently and has no shared session to receive
// 'setFarmGridBase' from.

const ROWS = 25;
const COLS = 43;
const ROW_STRIDE = 0x2b0;
// No padding between real tile data and a max-width buffer here (unlike the mine's fixed
// 28-column buffer) -- 43 columns * 0x10 bytes/tile == 0x2B0 exactly -- so one contiguous read of
// ROWS * ROW_STRIDE bytes captures the whole grid.
const GRID_SIZE = ROWS * ROW_STRIDE;

let gridBase = null;

// Player/farm-tile position marker (2026-09-10). Went through several wrong approaches before
// this one -- global player X/Y, then hooking the shared tile-math function directly, then
// latching "whichever entity reports Location==2" (broke as soon as another entity, e.g. an
// animal, could also be on the farm) -- before landing here: identify the player's entity
// specifically by its VTABLE POINTER, which is unique per class/role (confirmed live: every
// villager shares one vtable, every "cow/sheep"-type animal shares another, etc., but the player
// has its own). The Entity Manager array itself (see src/core_hooks_agent.js's derivation comment
// for the shared exe+532A0 hook) is scanned fresh every poll for the one entity whose own vtable
// matches the player's, rather than trying to cache/latch a single pointer -- cheap
// enough given the array is small, and avoids ever latching onto the wrong entity.
//
// Player vtable confirmed live (via Cheat Engine): 0x00007FF73CA84300 -- expressed here as a
// module-relative offset so it stays correct regardless of where Windows loads the module in a
// given run (ASLR): the vtable is just compiled data (like everything else in this file's
// exe+XXXXXX offsets), so its offset from the module base is fixed even though the base itself
// isn't. First attempt assumed moduleBase ~0x00007FF73C000000 (matching the rough magnitude of
// every other offset in this codebase) and got offset 0xA84300 -- WRONG, confirmed live: this
// session's actual module base is 0x00007FF73C550000 (exposed via a one-off diagnostic status
// message), giving the corrected offset below.
const ARRAY_BEGIN_OFFSET = 0x20;
const ARRAY_END_OFFSET = 0x28;
const MAX_ENTITIES = 512; // sanity cap in case the array bounds are ever bogus mid-read
const PLAYER_VTABLE_OFFSET = 0x534300;

// True position (2026-09-10): the entity's own X/Y are at +0x3B4 (X) and +0x3BC (Y) -- NOT the
// entity's +0xA2C/+0xA34 pair, which turned out to be the currently-TARGETED tile, not the
// player's own position (confirmed by hand: exe+57B30-57B44, TargetTileX = [entity+0x3B4] +
// [entity+0xA28]*facingDirX). +0x3B4 stays constant while turning in place, unlike +0xA2C/+0xA34.
// Y is NOT simply 4 bytes after X (that's a near-zero Z/height-like field instead, confirmed live)
// -- it's 8 bytes after X, at +0x3BC -- i.e. position here is a 3-float (X, height, Y) tuple,
// unlike the flattened 2-float (X, Y) target-tile pair.
const PLAYER_ENTITY_X_OFFSET = 0x3b4;
const PLAYER_ENTITY_Y_OFFSET = 0x3bc;
// Kept only to derive a facing-direction vector (target - true) for the facing indicator -- see
// PLAYER_ENTITY_X_OFFSET's comment above for why these two aren't used for the dot itself.
const PLAYER_ENTITY_TARGET_X_OFFSET = 0xa2c;
const PLAYER_ENTITY_TARGET_Y_OFFSET = 0xa34;
const PLAYER_SUBSTRUCT_OFFSET = 0xbce0; // Save Data Base -> player sub-struct (Memory Viewer's own)
const PLAYER_LOCATION_OFFSET = 0x340;
const FARM_LOCATION_ID = 2;

let entityManagerPtr = null;
let saveDataBase = null;

// The exe+532A0 hook itself now lives in src/core_hooks_agent.js (2026-09-16) -- this file used to
// carry its own independent copy, hooking the game a second time whenever Farm Map was open
// alongside Mine Map/Entity Viewer/the barn overlay (all of which also read this same pointer).
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

function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        saveDataBase = ptr(message.address);
        listenForSetSaveDataBase(); // recv() only fires once per registration -- re-arm
    });
}
listenForSetSaveDataBase();

const PLAYER_POLL_INTERVAL_MS = 50; // was 200 -- faster polling for smoother dot movement
function readPlayerPos() {
    if (saveDataBase === null) return;
    try {
        const location = saveDataBase.add(PLAYER_SUBSTRUCT_OFFSET).add(PLAYER_LOCATION_OFFSET).readS32();
        const playerEntityPtr = findPlayerEntity();
        if (location !== FARM_LOCATION_ID || playerEntityPtr === null) {
            send({
                type: 'playerPos', ok: true, location: location,
                x: null, y: null, facingDx: null, facingDy: null,
            });
            return;
        }
        const x = playerEntityPtr.add(PLAYER_ENTITY_X_OFFSET).readFloat();
        const y = playerEntityPtr.add(PLAYER_ENTITY_Y_OFFSET).readFloat();
        const targetX = playerEntityPtr.add(PLAYER_ENTITY_TARGET_X_OFFSET).readFloat();
        const targetY = playerEntityPtr.add(PLAYER_ENTITY_TARGET_Y_OFFSET).readFloat();
        send({
            type: 'playerPos', ok: true, location: location,
            x: x, y: y, facingDx: targetX - x, facingDy: targetY - y,
        });
    } catch (e) {
        send({ type: 'playerPos', ok: false, error: e.message });
    }
}
setInterval(readPlayerPos, PLAYER_POLL_INTERVAL_MS);

function bytesToHex(bytes) {
    return Array.from(new Uint8Array(bytes))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');
}

function readGrid() {
    if (gridBase === null) return;
    try {
        const hex = bytesToHex(gridBase.readByteArray(GRID_SIZE));
        send({ type: 'grid', ok: true, hex: hex });
    } catch (e) {
        send({ type: 'grid', ok: false, error: e.message });
    }
}

function useGridBase(base) {
    gridBase = base;
    readGrid();
}

setInterval(readGrid, 1000);

// Manual override -- trusted as-is, no validation, since it's always an explicit instruction
// (src/farm_map.py's --address/cached-address handling).
function listenForSetGridBase() {
    recv('setGridBase', (message) => {
        gridBase = ptr(message.address);
        send({ type: 'status', message: `grid base manually set to ${gridBase}` });
        readGrid();
        listenForSetGridBase(); // recv() only fires once per registration -- re-arm for the next message
    });
}
listenForSetGridBase();

// The one automatic path -- SaveDataBase + 0x78C4, pushed from the shared session
// (app/game_session.py, src/core_hooks_agent.js) as soon as Save Data Base is known. See this
// file's header comment for why this is trusted unconditionally now (no shape check).
function listenForSetFarmGridBase() {
    recv('setFarmGridBase', (message) => {
        useGridBase(ptr(message.address));
        listenForSetFarmGridBase();
    });
}
listenForSetFarmGridBase();
