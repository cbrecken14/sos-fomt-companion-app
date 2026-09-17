// Animal Viewer: live per-animal position/facing view, covering the territory just before the
// animal roster record's own origin (Name (Secondary), offset 0x000 in src/animal_viewer_agent.js).
//
// Facing Direction is part of the SAME roster record as the rest of the animal data, 8 bytes
// before Name (Secondary) -- confirmed by two independent data points both fitting "Facing = (that
// slot's own roster address) - 8" exactly (Coop Slot 1 directly; Barn Slot 2's facing address,
// confirmed across two restarts, matches BARN_ROSTER_OFFSET + 1*0x200 - 8). This file is
// nonetheless kept as its own separate, independently-tracked field list from
// src/animal_viewer_agent.js -- Facing/Age/X/Y are intentionally not merged into the static Animal
// Viewer's own KNOWN_FIELDS, even though they resolve to offsets within that same record. See
// data/pointer_map.md's "Animal Viewer (Live Data) investigation" section.
//
// This tab explores the territory before the earliest confirmed field (HEAD_PADDING bytes of it),
// using the same confirmed per-slot addressing as the roster (Coop/Barn bases, 0x200 stride).
//
// X/Y/Facing confirmed live (Coop Slot 1 and Slot 2), packing perfectly contiguously back to
// -0x10. Name (Secondary)/Name (+0x000/+0x084) are also included here, up through where they end,
// so this tab shows a self-contained picture -- animal_viewer_agent.js remains the
// authoritative/original place for those two fields (and everything from Affection onward); this
// file duplicates them for reference only, keeping the two tabs' own field lists independent
// rather than merging discoveries into the static tab.
// -0x14 (one slot further back) is confirmed as Location, completing the same Location/X/Y/Facing
// block the villager and Harvest Sprite structs also have. See data/pointer_map.md's "Animal
// Viewer (Live Data) investigation" section.

const PLAYER_SUBSTRUCT_OFFSET = 0xbce0; // only used by looksLikeSaveData's stamina sanity check
const ANIMAL_RECORD_SIZE = 0x200; // same confirmed per-slot stride as animal_viewer_agent.js
const ANIMAL_ROSTER_OFFSET = 0x4580; // saveDataBase + this = Coop Slot 1's Name (Secondary) address -- confirmed live 2026-09-08
const ANIMAL_SLOT_COUNT = 8;
const BARN_ROSTER_OFFSET = 0x58c0; // saveDataBase + this = Barn Slot 1's Name (Secondary) address -- unconfirmed for Barn specifically, same as animal_viewer_agent.js
const BARN_SLOT_COUNT = 16;
const POLL_INTERVAL_MS = 500;

const HEAD_PADDING = 0x8; // window starts at -0x18 from the roster origin -- RECORD_WINDOW_END shifted by the same +0x14 below, to keep each slot's total span at exactly ANIMAL_RECORD_SIZE
// Extends all the way to where the NEXT slot's own window starts (known[0].offset - HEAD_PADDING
// + ANIMAL_RECORD_SIZE), i.e. this window's full span always equals ANIMAL_RECORD_SIZE exactly --
// otherwise a gap opens up between slots that never gets shown as rows (the last row of one slot
// and the first row of the next weren't actually adjacent).
const RECORD_WINDOW_END = 0x1e8;

// The confirmed fields in this window. Name (Secondary)/Name are also present in
// animal_viewer_agent.js's own KNOWN_FIELDS (that file remains the authoritative place for them --
// fields aren't renamed/restructured on the static Animal Viewer) -- duplicated here only so this
// tab shows a self-contained picture up through where the roster's own known fields begin.
// KNOWN_FIELDS is an injected global now, generated from data/tables/animal_live_viewer.json (see
// app/agent_loader.py) -- kept as its OWN table, independent from animal_viewer.json (the two
// tabs' field lists stay independent, not merged), including its own diverged Name (Secondary)
// buffer length (10 chars here vs. 12 in animal_viewer.json -- see that field's history in
// data/pointer_map.md). -0x14 is confirmed as Location -- not Age (Days), which is at +0x188
// (the same offset animal_viewer_agent.js's roster labels "Affection" -- not resolved, flagged in
// data/pointer_map.md). Location uses the same LOCATION enum/locationCalc concept as the villager
// and Harvest Sprite structs' own Location fields, and the same relative layout (Location
// immediately before X/Y/Facing) as those two.
// Milked/Sheared Today (raw, +0x1BC) is shown raw, not decoded -- its pre-action value
// (4294967295 / 0xFFFFFFFF) looks like a day-of-last-action "never" sentinel, not a plain boolean.
//
// `calc` entries arrive as decoder-name strings, not functions (JSON can't hold functions),
// hydrated back into real callables here.
const DECODERS = {
    facing: (v) => ENUMS.FACING[v],
    location: locationCalc,
    // bit 8 (0x100) = brushed, bit 16 (0x10000) = talked to -- confirmed against all 4 observed
    // values: 0, 256, 65536, 65792 (=256+65536, both flags set).
    dailyInteraction: (v) => {
        const parts = [];
        if (v & 0x10000) parts.push('Talked to');
        if (v & 0x100) parts.push('Brushed');
        return parts.length ? parts.join(', ') : 'No interaction today';
    },
    foodState: (v) => (v & 0x1 ? 'Fed today, ' : 'Not fed today, ') + (v & 0x100 ? 'fed yesterday' : 'not fed yesterday'),
    yesNo: (v) => (v ? 'Yes' : 'No'),
};
for (const field of KNOWN_FIELDS) {
    if (field.calc) field.calc = DECODERS[field.calc];
}

function offsetLabel(offset) {
    return offset < 0 ? '-0x' + (-offset).toString(16) : '+0x' + offset.toString(16);
}

// Same gap-filling algorithm as animal_viewer_agent.js's buildRowTemplate, but bounded to
// [known[0].offset - HEAD_PADDING, RECORD_WINDOW_END) instead of the full record -- this tool only
// covers the unmapped territory before the roster's own known fields start.
function buildRowTemplate() {
    const known = KNOWN_FIELDS.slice().sort((a, b) => a.offset - b.offset);
    const rows = [];
    let cursor = known[0].offset - HEAD_PADDING;
    for (const field of known) {
        while (cursor + 4 <= field.offset) {
            rows.push({ offset: cursor, name: null, size: 4 });
            cursor += 4;
        }
        rows.push(field);
        cursor = Math.ceil((field.offset + field.size) / 4) * 4;
    }
    while (cursor < RECORD_WINDOW_END) {
        rows.push({ offset: cursor, name: null, size: 4 });
        cursor += 4;
    }
    return rows;
}

const ROW_TEMPLATE = buildRowTemplate();

// Dump range matches exactly what this tab already explores -- from known[0].offset - HEAD_PADDING
// (covers the negative-offset Age/X/Y/Facing fields the plain Animal Viewer doesn't have) through
// RECORD_WINDOW_END, i.e. the same span ROW_TEMPLATE itself covers, nothing more/less arbitrary.
const DUMP_START = ROW_TEMPLATE[0].offset;
const DUMP_SIZE = RECORD_WINDOW_END - DUMP_START;

// Dumps one right-clicked animal's bytes (this tab's own, richer window -- see DUMP_START/
// DUMP_SIZE above) as raw bytes, printed to the console by app/windows/animal_live_viewer.py
// rather than shown in the table -- same idea as villagers_agent.js's dumpBytes: grab a dump right
// before and right after some in-game action (feeding, petting, milking, etc.) and diff them by
// eye to find which byte(s) changed. Lives on this tab rather than the static Animal Viewer since
// it has the richer field set.
function dumpBytes(colIndex) {
    const column = COLUMNS[colIndex];
    if (!column) return;
    const slot = slotBase(column);
    if (slot === null) {
        send({ type: 'status', message: 'Dump Bytes: no Save Data Base yet' });
        return;
    }
    const base = slot.add(DUMP_START);
    const bytes = Array.from(new Uint8Array(base.readByteArray(DUMP_SIZE)));
    send({ type: 'bytesDump', label: column.label, address: base.toString(), bytes });
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom every
// other listener in this file uses.
function listenForDumpBytes() {
    recv('dumpBytes', (message) => {
        dumpBytes(message.colIndex);
        listenForDumpBytes();
    });
}
listenForDumpBytes();

// Same per-roster labeling convention as animal_viewer_agent.js (Coop Slot 1-8, Barn Slot 1-16).
const COLUMNS = [];
for (let i = 0; i < ANIMAL_SLOT_COUNT; i++) COLUMNS.push({ roster: 'Coop', slotIndex: i, label: 'Coop Slot ' + (i + 1) });
for (let i = 0; i < BARN_SLOT_COUNT; i++) COLUMNS.push({ roster: 'Barn', slotIndex: i, label: 'Barn Slot ' + (i + 1) });

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

function looksLikeSaveData(base) {
    try {
        const money = base.add(0xbc50).readU32();
        const stamina = base.add(PLAYER_SUBSTRUCT_OFFSET + 0x3b6).readU16();
        return money <= 99999999 && stamina <= 9999;
    } catch (e) {
        return false;
    }
}

let animalRosterBase = null; // Coop
let barnRosterBase = null; // Barn
const lastValues = {}; // keyed by "rowIndex:colIndex"
let pollTimer = null;

function slotBase(column) {
    const rosterBase = column.roster === 'Coop' ? animalRosterBase : barnRosterBase;
    if (rosterBase === null) return null;
    return rosterBase.add(column.slotIndex * ANIMAL_RECORD_SIZE);
}

function poll() {
    // Collected into one batch and sent as a single message (2026-09-16) instead of one send()
    // per changed cell -- on the first poll after a (re-)attach, lastValues is empty and EVERY
    // cell in the whole grid (128 rows x 24 columns) qualifies as "changed" at once, which used to
    // mean ~3,000 individual Frida IPC round-trips in one burst. One batched message per poll tick
    // is both cheaper on this side (one send()/JSON-serialize instead of thousands) and lets the
    // Python side apply the whole batch as a single Qt-thread hop instead of thousands of queued
    // signal dispatches -- see app/windows/animal_live_viewer.py's _on_cell_batch.
    const updates = [];
    for (let colIndex = 0; colIndex < COLUMNS.length; colIndex++) {
        const base = slotBase(COLUMNS[colIndex]);
        if (base === null) continue;
        for (let rowIndex = 0; rowIndex < ROW_TEMPLATE.length; rowIndex++) {
            const row = ROW_TEMPLATE[rowIndex];
            const key = rowIndex + ':' + colIndex;
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
            updates.push({ rowIndex, colIndex, raw, calculated: calc || null });
        }
    }
    if (updates.length > 0) send({ type: 'cellBatch', updates });
}

function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

function sendColumnBases() {
    // Lets Python compute an exact address for any cell (columnBase + row.offset) for the
    // right-click "Copy Address" action, without needing every cell update to carry its own
    // address string.
    const bases = COLUMNS.map((column) => {
        const base = slotBase(column);
        return base === null ? null : base.toString();
    });
    send({ type: 'columnBases', bases });
}

function useSaveDataBase(base) {
    animalRosterBase = base.add(ANIMAL_ROSTER_OFFSET);
    barnRosterBase = base.add(BARN_ROSTER_OFFSET);
    for (const key of Object.keys(lastValues)) delete lastValues[key]; // force a full resend
    send({ type: 'status', message: 'save data base received ' + base.toString() });
    sendColumnBases();
    startPolling();
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom
// src/animal_viewer_agent.js's listenForSetSaveDataBase uses.
function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        const candidate = ptr(message.address);
        if (looksLikeSaveData(candidate)) {
            useSaveDataBase(candidate);
        }
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

send({
    type: 'schema',
    rows: ROW_TEMPLATE.map((row) => ({
        offset: row.offset,
        label: row.name || 'Unknown ' + offsetLabel(row.offset),
        known: row.name !== null,
        verified: row.name !== null && row.verified !== false,
    })),
    columns: COLUMNS,
});

send({ type: 'status', message: 'waiting for Save Data Base from the shared session...' });
