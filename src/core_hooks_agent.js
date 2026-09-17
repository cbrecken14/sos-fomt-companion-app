// Shared "core" hooks: finds Save Data Base (Money's struct) and TimeBasePtr -- the two addresses
// almost every feature window needs -- plus derives the Farm grid base as a fixed offset off Save
// Data Base. One attach point for the whole app instead of every window re-deriving these
// independently, reducing redundant code -- this game is an old, unpatched build and isn't
// expected to change, so these offsets are treated as permanent.
//
// Loaded once by app/game_session.py's single shared Frida session. Other windows create their
// OWN additional script on that SAME session (via GameSession.session.create_script(...)) for
// whatever feature-specific fields they need (e.g. Memory Viewer's animal roster rows, Farm Map's
// tile grid), and receive these base addresses via script.post() instead of hooking them a second
// time -- see src/memory_viewer_agent.js's setSaveDataBase/setTimeBase receivers and
// src/farm_map_agent.js's setFarmGridBase receiver for how they consume this.
//
// Entity Manager hook: exe+532A0 used to be independently re-hooked in farm_map_agent.js,
// floor_map_agent.js, and other per-window agents -- up to several copies of the same
// Interceptor.attach live in the game process at once whenever more than one of those
// windows/overlays was open. Consolidated here for the same reason as Save Data/Time Base: one
// physical hook, broadcast to whoever needs it -- now just Farm Map and Mine Map. This one fires
// on an "ambient every-frame path" (the highest-frequency hook in the app), so unlike Save/Time
// Base the broadcast is deduplicated (tryReportEntityManager below) -- sending on every firing
// would flood script.post() traffic for a pointer that in practice only changes on a
// scene/manager reallocation, not every frame.
//
// Save Data hook derivation: see src/save_data_reader_agent.js (`mov [rsi+0xBC50],eax` at
// exe+94391 -- rsi IS the base, no correction needed).
// Farm grid base derivation: see data/pointer_map.md's "Farm tile array" section --
// FarmGridBase = SaveDataBase + 0x78C4, confirmed two independent ways (manual address comparison
// across two sessions, and this exact offset already present between two separately-cached
// addresses in config/hook_cache.json from a different session) landing on the identical value
// both times.
//
// Time/Mine Floor derived-offset confirmation: two independent fresh-restart sessions read off
// Memory Viewer's own base-address fields (same evidence bar as Farm Grid Base above) both landed
// on TimeBase = SaveDataBase + 0x30 and MineFloorBase = SaveDataBase + 0xFB48, byte-identical both
// times. Mine Floor Base's derived value only gets used when nothing fresher is already known (it
// isn't session-stable the way Time/Farm Grid are -- see tryDeriveMineFloorBase and
// src/floor_map_agent.js's consumer-side note).
//
// Time Base used to also have its own direct accessor hook (`mov [rcx+0x38],rdi` at exe+2FAECD,
// a save-load write, same lifecycle moment as the Save Data hook above) -- removed, since this
// project already trusts every other fixed compile-time offset unconditionally (Farm Grid Base
// above, RelationshipManagerBase, PLAYER_SUBSTRUCT_OFFSET) with no independent hook backing them
// up, on the same "this is a frozen, unpatched build" reasoning as the header comment. The derived
// path below (tryDeriveTimeBase) still shape-checks every candidate via looksLikeTimeBase() before
// broadcasting it, so removing the direct hook costs no validation -- it only removes a second,
// redundant SOURCE of the same address. It's also now the fresher of the two ways to arrive at
// this value: unlike the old hook (which only refired on a save reload), this path re-fires on
// every zone transition too, since it rides on useSaveDataBase() the same way Farm Grid Base does.

const SAVE_DATA_HOOK_OFFSET = 0x94391; // STORY OF SEASONS Friends of Mineral Town.exe+94391
const PLAYER_SUBSTRUCT_OFFSET = 0xbce0;
// Player's Current Location setter -- a tiny, isolated leaf function (`mov [rcx+0x340],edx; ret`,
// padded with int3 on both sides, not a generic/shared routine like the farm's memcpy false
// positive) that fires on zone transitions. rcx is the player struct itself, confirmed live
// (rcx == SaveDataBase + PLAYER_SUBSTRUCT_OFFSET, call stack confirmed player-only, fires only on
// zone transitions, not every frame). Added because the Money
// hook (SAVE_DATA_HOOK_OFFSET) only refires on a save reload -- Save Data Base can otherwise go
// stale across a zone transition. Reuses looksLikeSaveData() as a defensive filter in case this
// setter ever turns out to also run for non-player entities.
const PLAYER_LOCATION_HOOK_OFFSET = 0x2f1850; // STORY OF SEASONS Friends of Mineral Town.exe+2F1850
const ENTITY_MANAGER_HOOK_OFFSET = 0x532a0; // STORY OF SEASONS Friends of Mineral Town.exe+532A0
const FARM_GRID_BASE_OFFSET = 0x78c4; // saveDataBase + this = farm tile grid_base
const TIME_BASE_OFFSET = 0x30; // saveDataBase + this = TimeBase -- confirmed across 2 restarts
const MINE_FLOOR_BASE_OFFSET = 0xfb48; // saveDataBase + this = mine floor grid_base -- confirmed across 2 restarts

function looksLikeSaveData(base) {
    try {
        const money = base.add(0xbc50).readU32();
        const stamina = base.add(PLAYER_SUBSTRUCT_OFFSET + 0x3b6).readU16();
        return money <= 99999999 && stamina <= 9999;
    } catch (e) {
        return false;
    }
}

function looksLikeTimeBase(base) {
    try {
        const day = base.add(0x0c).readU8();
        const hour = base.add(0x10).readU8();
        const minute = base.add(0x11).readU8();
        return day <= 29 && hour <= 23 && minute <= 59;
    } catch (e) {
        return false;
    }
}

// Ported from src/memory_viewer_agent.js's looksLikeMineFloor -- used only to validate the
// DERIVED mine floor base before broadcasting it. Loose on purpose, same reasoning as that copy.
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

let saveDataBase = null;
let timeBase = null;
let farmGridBaseSent = null; // avoid re-broadcasting the identical derived address every re-fire
let mineFloorBaseSent = null; // same idea, for the derived mine floor base
let entityManagerSent = null; // same idea -- this hook fires every frame, so dedup matters here

function tryDeriveFarmGridBase() {
    // No shape check -- unlike Time/Mine Floor Base, FarmGridBase = SaveDataBase + 0x78C4 is a
    // confirmed, fixed compile-time offset (data/pointer_map.md's "Farm tile array" section), not
    // a guess -- the thing actually worth validating is SaveDataBase itself, and that's already
    // been through looksLikeSaveData() before useSaveDataBase() ever calls this. A shape check on
    // top of that used to block this broadcast outright on saves where it perpetually failed,
    // which was a real bug for every consumer (app/farm_tile_debug.py, app/reminders_crop_data.py,
    // src/farm_map_agent.js), none of which have a fallback and would otherwise never receive a
    // farm grid base at all on an affected save.
    if (saveDataBase === null) return;
    const candidate = saveDataBase.add(FARM_GRID_BASE_OFFSET);
    if (farmGridBaseSent !== null && farmGridBaseSent.equals(candidate)) return;
    farmGridBaseSent = candidate;
    send({ type: 'farmGridBaseFound', address: candidate.toString() });
}

// The sole source of Time Base now (see the header comment for why the old direct accessor hook
// was removed) -- same "trust the fixed compile-time offset" treatment as tryDeriveFarmGridBase
// above, just with looksLikeTimeBase() kept as a shape check since (unlike Farm Grid Base) this
// offset was only ever cross-confirmed twice, not measured directly against a known-good address.
function tryDeriveTimeBase() {
    if (saveDataBase === null) return;
    const candidate = saveDataBase.add(TIME_BASE_OFFSET);
    if (looksLikeTimeBase(candidate)) {
        useTimeBase(candidate);
    }
}

function tryDeriveMineFloorBase() {
    if (saveDataBase === null) return;
    const candidate = saveDataBase.add(MINE_FLOOR_BASE_OFFSET);
    if (mineFloorBaseSent !== null && mineFloorBaseSent.equals(candidate)) return;
    if (!looksLikeMineFloor(candidate)) {
        send({
            type: 'status',
            message: 'derived mine floor base ' + candidate + ' failed the shape check -- no floor loaded yet',
        });
        return;
    }
    mineFloorBaseSent = candidate;
    send({ type: 'mineFloorBaseFound', address: candidate.toString() });
}

function useSaveDataBase(base, { fromCache }) {
    if (saveDataBase !== null && !saveDataBase.equals(base)) {
        send({
            type: 'status',
            message: 'save data base changed: ' + saveDataBase.toString() + ' -> ' + base.toString(),
        });
    }
    saveDataBase = base;
    send({
        type: 'saveDataBaseFound',
        address: base.toString(),
        fromCache: fromCache,
        message: (fromCache ? 'using cached save data base ' : 'save data base found: ') + base.toString(),
    });
    tryDeriveFarmGridBase();
    tryDeriveTimeBase();
    tryDeriveMineFloorBase();
}

function tryReportEntityManager(candidate) {
    if (entityManagerSent !== null && entityManagerSent.equals(candidate)) return;
    entityManagerSent = candidate;
    send({ type: 'entityManagerFound', address: candidate.toString() });
}

function useTimeBase(base) {
    timeBase = base;
    send({
        type: 'timeBaseFound',
        address: base.toString(),
        message: 'time base found: ' + base.toString(),
    });
}

if (SAVE_DATA_BASE_OVERRIDE !== null && looksLikeSaveData(SAVE_DATA_BASE_OVERRIDE)) {
    useSaveDataBase(SAVE_DATA_BASE_OVERRIDE, { fromCache: true });
} else if (SAVE_DATA_BASE_OVERRIDE !== null) {
    send({
        type: 'status',
        message: `cached save data base ${SAVE_DATA_BASE_OVERRIDE} doesn't look valid (stale? game restarted?) -- waiting for a save reload`,
    });
} else {
    send({ type: 'status', message: 'no cached save data base yet -- waiting for a save reload' });
}

const moduleBase = Process.mainModule.base;

Interceptor.attach(moduleBase.add(SAVE_DATA_HOOK_OFFSET), {
    onEnter() {
        const candidate = this.context.rsi;
        if (!looksLikeSaveData(candidate)) return;
        useSaveDataBase(candidate, { fromCache: false });
    },
});

Interceptor.attach(moduleBase.add(PLAYER_LOCATION_HOOK_OFFSET), {
    onEnter() {
        send({ type: 'status', message: 'area transition hook processed' });
        const newLocationId = this.context.rdx.toInt32();
        const newLocationName = locationCalc(newLocationId);
        send({
            type: 'status',
            message: 'new player location: ' + newLocationId + ' - ' + (newLocationName !== null ? newLocationName : 'Unknown'),
        });
        // Structured broadcast (2026-09-16), alongside the human-readable status line above --
        // added for app/windows/mine_map.py's Mine Floor 0 special case (see that file's own
        // comment): Floor 0 has no ladder entrance of its own (you walk straight into it from
        // Mother's Hill), and its own floor-generation hooks in floor_map_agent.js turned out
        // unreliable for that specific transition (confirmed live: the entity-position source
        // used to anchor the dot wasn't consistently valid yet at that exact moment). This
        // location broadcast is the one signal confirmed to fire reliably and immediately on
        // every transition into Floor 0, ladder or not.
        send({ type: 'playerLocationChanged', locationId: newLocationId });
        const candidate = this.context.rcx.sub(PLAYER_SUBSTRUCT_OFFSET);
        if (!looksLikeSaveData(candidate)) return;
        useSaveDataBase(candidate, { fromCache: false });
    },
});

Interceptor.attach(moduleBase.add(ENTITY_MANAGER_HOOK_OFFSET), {
    onEnter() {
        tryReportEntityManager(this.context.rcx);
    },
});

send({ type: 'status', message: 'core hooks installed -- waiting for a save to load...' });
