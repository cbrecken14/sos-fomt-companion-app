// Live memory viewer: reports every currently-known field across the Save Data struct (Money,
// player stats) and the Time struct (Day/Season/Weather/Hour/Minute), plus the "gap" bytes
// (4-byte granularity) between known fields within each struct's currently-mapped extent -- so
// unlabeled fields can be watched live in-game and identified by what changes when.
//
// Deliberately does NOT try to cover the entire 74536-byte Save Data struct -- most of that is
// still totally unexplored, and dumping all of it as 4-byte rows would be tens of thousands of
// entries. This only fills gaps *between* fields we already know about (see PROGRESS.md's Save
// Data / Player-state struct sections), which keeps the row count small and focuses attention on
// the region we've actually started mapping.
//
// Save Data Base, Time Base, and Mine Floor Base are no longer hooked directly in this script
// (2026-09-08) -- all three are now found once by the shared core hooks script
// (src/core_hooks_agent.js, loaded via one shared Frida session, see app/game_session.py) and
// pushed here via setSaveDataBase/setTimeBase/setMineFloorBase messages instead of this script
// re-deriving them itself. This script used to carry its own separate copy of the mine floor
// grid-base hook (duplicating Mine Map's own identical hook) until Mine Floor Base was confirmed
// to be a fixed offset from Save Data Base, the same way Farm Grid Base already was. See
// PROGRESS.md and data/pointer_map.md for the full derivation history.

const PLAYER_SUBSTRUCT_OFFSET = 0xbce0;
const POLL_INTERVAL_MS = 500;

// ---- Known field catalog -------------------------------------------------------------------
// FIELDS, TAIL_ROWS_OVERRIDE, and HEAD_PADDING_ROWS are injected globals now, generated from
// data/tables/memory_viewer.json (see app/agent_loader.py) -- single source, no longer
// hand-written in this file. struct: 'time' | 'saveHeader' | 'saveData' | 'mineFloor'. offset is
// relative to that struct's own base (saveData/saveHeader fields under the player sub-struct or
// save header have their PLAYER_SUBSTRUCT_OFFSET-relative offset already baked in as a plain
// number in the JSON, rather than an expression computed here).
//
// See data/pointer_map.md for the full derivation/history of every field below (Time/Weather
// struct, Save Header's Farm Name/Horse block, Save Data's Money/player-substruct fields, the
// Mine Floor tile-grid header). Coop/Barn animal rosters moved out to src/animal_viewer_agent.js /
// the Animal Viewer tab (2026-09-08) -- not part of this catalog.
//
// `calc` entries arrive as decoder-name strings, not functions (JSON can't hold functions),
// hydrated back into real callables here.
const DECODERS = {
    weather: (v) => ENUMS.WEATHER[v],
    season: (v) => ENUMS.SEASON[v],
    day: (v) => 'Day ' + (v + 1),
    facing: (v) => ENUMS.FACING[v],
    location: locationCalc,
    item: (v) => ENUMS.ITEM[v],
    bagSlot: (v) => 'Row ' + Math.floor(v / 8) + ', Col ' + (v % 8),
};
for (const field of FIELDS) {
    if (field.calc) field.calc = DECODERS[field.calc];
}

// Clears every cached last-seen value for one struct's rows, so a fresh base (e.g. after a
// reload) always resends every row at least once -- otherwise a field whose value happens to
// coincidentally match what the OLD base had at that offset would never get resent, leaving its
// address/exeOffset columns silently stale even though the underlying base actually moved.
function clearLastValuesFor(structName) {
    const prefix = structName + ':';
    for (const key of Object.keys(lastValues)) {
        if (key.startsWith(prefix)) delete lastValues[key];
    }
}

function readField(base, field) {
    const addr = base.add(field.offset);
    if (field.type === 'float') return addr.readFloat();
    if (field.type === 'utf16') return addr.readUtf16String(field.length);
    if (field.type === 'utf32') {
        let out = '';
        for (let i = 0; i < field.length; i++) {
            const code = addr.add(i * 4).readU32();
            if (code === 0) break; // null terminator
            out += String.fromCharCode(code);
        }
        return out;
    }
    if (field.size === 1) return addr.readU8();
    if (field.size === 2) return addr.readU16();
    return addr.readU32();
}

// How many extra 4-byte "unknown" rows to keep showing past the very last known field -- without
// this the table stops dead right at the last named field, hiding whatever comes right after it.
// 100 rows = 400 bytes.
const EXTRA_TAIL_ROWS = 100;

// Builds the full row list for one struct: optional HEAD_PADDING_ROWS before the first known
// field, the known fields plus 4-byte "gap" rows filling the space between them (bounded to the
// span between the first and last currently-known field, not the whole mostly-unexplored struct),
// plus EXTRA_TAIL_ROWS more past the last known field -- so there's always some room to keep
// exploring just beyond what's currently mapped, in both directions.
function buildRows(structName) {
    const known = FIELDS.filter((f) => f.struct === structName)
        .slice()
        .sort((a, b) => a.offset - b.offset);
    if (known.length === 0) return [];
    const rows = [];
    const headPaddingBytes = (HEAD_PADDING_ROWS[structName] || 0) * 4;
    let cursor = known[0].offset - headPaddingBytes;
    while (cursor < known[0].offset) {
        rows.push({ struct: structName, name: null, offset: cursor, size: 4 });
        cursor += 4;
    }
    for (const field of known) {
        while (cursor + 4 <= field.offset) {
            rows.push({ struct: structName, name: null, offset: cursor, size: 4 });
            cursor += 4;
        }
        rows.push(field);
        cursor = Math.ceil((field.offset + field.size) / 4) * 4;
    }
    const tailRows = TAIL_ROWS_OVERRIDE[structName] !== undefined ? TAIL_ROWS_OVERRIDE[structName] : EXTRA_TAIL_ROWS;
    for (let i = 0; i < tailRows; i++) {
        rows.push({ struct: structName, name: null, offset: cursor, size: 4 });
        cursor += 4;
    }
    return rows;
}

const SAVE_DATA_ROWS = buildRows('saveData');
const SAVE_HEADER_ROWS = buildRows('saveHeader');
const TIME_ROWS = buildRows('time');
const MINE_FLOOR_ROWS = buildRows('mineFloor');

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

// Loose on purpose -- state/rock/soil content's full valid ranges aren't as tightly pinned down
// as Save Data's fields (see src/floor_map_agent.js/app/windows/mine_map.py for what's confirmed).
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
let mineFloorBase = null;
const lastValues = {};
let pollTimer = null;

// JS's Number#toString(16) puts the minus sign OUTSIDE the digits for a negative number (e.g.
// (-8).toString(16) === '-8'), so a naive '0x' + n.toString(16) produces '0x-8' for the time
// struct's negative offsets (Weather/Next Weather, -0x08/-0x04) -- malformed hex Python's
// int(s, 16) can't parse (it wants the sign BEFORE '0x', i.e. '-0x8'). Fixed here so the offset
// string is always valid hex, negative or not (needed by the decimal-offset column, which shows
// offsets in plain decimal too, not just hex).
function formatOffset(offset) {
    return offset < 0 ? '-0x' + (-offset).toString(16) : '0x' + offset.toString(16);
}

// Same as formatOffset but with a leading '+' for non-negative offsets, for the "Unknown +0xNN"
// label -- avoids a double sign like "Unknown +-0x8" for the time struct's negative offsets.
function offsetLabel(offset) {
    return offset < 0 ? formatOffset(offset) : '+' + formatOffset(offset);
}

// baseOffsetFromSaveData: how far this struct's OWN base sits from saveDataBase (0 for saveData/
// saveHeader) -- null for structs with no saveDataBase relationship at all (time, mineFloor,
// separate allocations entirely). When set, the reported "offset" is always relative to
// saveDataBase, not this struct's own local base, so the offset column gives one consistent "how
// far is this from the one address I actually have" reading throughout the whole table.
function pollStruct(structName, base, rows, baseOffsetFromSaveData, updates) {
    if (base === null) return;
    for (const row of rows) {
        const key = structName + ':' + row.offset;
        let raw;
        try {
            raw = readField(base, row);
        } catch (e) {
            continue;
        }
        if (lastValues[key] === raw) continue;
        lastValues[key] = raw;
        let calc = null;
        if (row.calc) {
            try {
                calc = row.calc(raw);
            } catch (e) {
                calc = null;
            }
        }
        const addr = base.add(row.offset);
        const displayOffset = baseOffsetFromSaveData !== null ? baseOffsetFromSaveData + row.offset : row.offset;
        updates.push({
            struct: structName,
            name: row.name || 'Unknown ' + offsetLabel(displayOffset),
            known: row.name !== null,
            verified: row.name !== null && row.verified !== false,
            offset: formatOffset(displayOffset),
            address: addr.toString(),
            exeOffset: 'exe+' + addr.sub(moduleBase).toString(16),
            raw: raw,
            calculated: calc || null,
        });
    }
}

function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setInterval(() => {
        // Collected into one batch and sent as a single message (2026-09-16) instead of one
        // send() per changed row -- on the first poll after a (re-)attach, lastValues is empty
        // and EVERY row across all 4 structs qualifies as "changed" at once. See
        // app/windows/memory_viewer.py's _on_row_batch for the Python side of this.
        const updates = [];
        // baseOffsetFromSaveData = 0 for saveData/saveHeader -- their own base IS saveDataBase.
        // time/mineFloor pass null -- separate allocations, no meaningful saveDataBase-relative
        // offset to report.
        pollStruct('saveData', saveDataBase, SAVE_DATA_ROWS, 0, updates);
        pollStruct('saveHeader', saveDataBase, SAVE_HEADER_ROWS, 0, updates); // same base as saveData, own section
        pollStruct('time', timeBase, TIME_ROWS, null, updates);
        pollStruct('mineFloor', mineFloorBase, MINE_FLOOR_ROWS, null, updates);
        if (updates.length > 0) send({ type: 'rowBatch', updates });
    }, POLL_INTERVAL_MS);
}

// base/fromCache arrive via setSaveDataBase/setTimeBase messages from the shared core hooks
// session now (see the recv() listeners below) instead of this script's own hook firing --
// app/windows/memory_viewer.py updates the "Save Data Base:"/"Time Base:" UI fields directly from
// the shared session's own signal, not from a message round-tripped back through this script, so
// these just need to update local state/polling; the 'status' message here is just for the
// transient status line.
function useSaveDataBase(base, { fromCache }) {
    saveDataBase = base;
    clearLastValuesFor('saveData');
    clearLastValuesFor('saveHeader');
    send({
        type: 'status',
        message: (fromCache ? 'using cached save data base ' : 'save data base received ') + base.toString(),
    });
    startPolling();
}

function useTimeBase(base, { fromCache }) {
    timeBase = base;
    clearLastValuesFor('time');
    send({
        type: 'status',
        message: (fromCache ? 'using cached time base ' : 'time base received ') + base.toString(),
    });
    startPolling();
}

// No caching for the mine floor grid base -- unlike Save Data/Time, it isn't session-stable (a
// new floor, or even the same floor regenerating, can allocate a new one -- see
// app/windows/mine_map.py's own notes on this). Just re-fires every floor load, same as that tab.
function useMineFloorBase(base) {
    if (mineFloorBase !== null && mineFloorBase.equals(base)) return; // unchanged, nothing to do
    mineFloorBase = base;
    clearLastValuesFor('mineFloor');
    send({
        type: 'mineFloorBaseFound',
        address: base.toString(),
        message: 'mine floor grid base found: ' + base.toString(),
    });
    startPolling();
}

// Save Data Base and Time Base arrive from the shared core hooks session (app/game_session.py,
// src/core_hooks_agent.js) via these two receivers, not this script's own hooks (2026-09-08).
// app/windows/memory_viewer.py pushes the already-known value immediately on attach (covers the
// case where the shared session found it before this script even loaded) and again every time the
// shared session's own signal fires (a later save reload). Re-arms itself each time since recv()
// only fires once per registration -- same idiom src/farm_map_agent.js's listenForSetGridBase uses
// for its manual-override feature.
function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        const candidate = ptr(message.address);
        if (looksLikeSaveData(candidate)) {
            useSaveDataBase(candidate, { fromCache: message.fromCache });
        }
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

function listenForSetTimeBase() {
    recv('setTimeBase', (message) => {
        const candidate = ptr(message.address);
        if (looksLikeTimeBase(candidate)) {
            useTimeBase(candidate, { fromCache: message.fromCache });
        }
        listenForSetTimeBase();
    });
}
listenForSetTimeBase();

// Mine Floor Base arrives the same way as of 2026-09-08 -- this script used to hook
// MINE_FLOOR_HOOK_OFFSET itself, which meant the exact same instruction got hooked twice (Mine
// Map's own independent attach installed the identical hook for its own floor-generation logic).
// Now derived once in src/core_hooks_agent.js (SaveDataBase + a fixed offset, confirmed live) and
// broadcast from there instead.
function listenForSetMineFloorBase() {
    recv('setMineFloorBase', (message) => {
        const candidate = ptr(message.address);
        if (looksLikeMineFloor(candidate)) {
            useMineFloorBase(candidate);
        }
        listenForSetMineFloorBase();
    });
}
listenForSetMineFloorBase();

const moduleBase = Process.mainModule.base; // used for the Exe Offset column

// Dumps raw bytes around the two known Horse field clusters (data/pointer_map.md's "Save Header —
// Farm Name / Horse Name / Horse stats" section) for diffing before/after some in-game action
// (e.g. talking to the horse) to find flags not yet identified -- same "Dump Bytes to
// Console" idea as animal_viewer_agent.js, just as two fixed offset
// ranges off SaveDataBase instead of a per-column base (there's no "column" here, just one horse).
// Block A covers the tight X/Y/Facing/Name cluster (+0x1DC-+0x1EC) with padding on both sides;
// Block B covers Age/Affection (+0x374/+0x37C), a separate region further along.
function dumpHorseBytes() {
    if (saveDataBase === null) {
        send({ type: 'status', message: 'Dump Horse Bytes: no Save Data Base yet' });
        return;
    }
    const blocks = [
        { label: 'Horse Position/Facing/Name area', offset: 0x1c0, size: 0x80 },
        { label: 'Horse Age/Affection area', offset: 0x360, size: 0x40 },
    ];
    const dumps = blocks.map((b) => {
        const base = saveDataBase.add(b.offset);
        return { label: b.label, address: base.toString(), bytes: Array.from(new Uint8Array(base.readByteArray(b.size))) };
    });
    send({ type: 'horseBytesDump', dumps: dumps });
}

function listenForDumpHorseBytes() {
    recv('dumpHorseBytes', () => {
        dumpHorseBytes();
        listenForDumpHorseBytes();
    });
}
listenForDumpHorseBytes();

send({
    type: 'status',
    message: 'waiting for Save Data / Time / Mine Floor Base from the shared session...',
});
