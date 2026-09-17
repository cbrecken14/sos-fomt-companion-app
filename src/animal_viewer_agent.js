// Animal Viewer: a matrix of every animal record byte (known fields plus 4-byte "unknown" gap
// rows, same idea as src/memory_viewer_agent.js's gap-row exploration) across every Coop/Barn
// slot. Coop and Barn share the identical 0x200-byte record layout, so the row template (offsets
// + labels) is built ONCE here and reused across all 24 columns -- previously (when this lived
// inside Memory Viewer as 24 separate struct sections) each slot built its own copy of the same
// ~100-row template, which was pure duplication now that the layout's confirmed identical across
// slots (data/pointer_map.md's "Animal (Coop/Barn) struct" section).
//
// Shows full coverage here, not just the fields identified so far -- same exploratory purpose
// Memory Viewer's gap rows already serve, just laid out as a matrix instead of a long flat list so
// 24 slots' worth of data stays scannable.

// Record origin: the record actually STARTS at the secondary name field, not the primary Name --
// confirmed by measuring the secondary name's own address directly (two coop slots, 0x200 apart,
// confirming the stride); it sits 0x84 bytes BEFORE the primary Name address that
// ANIMAL_ROSTER_OFFSET used to anchor to. Every field offset below is relative to that corrected
// origin (old_offset + 0x84, wrapping mod 0x200) -- see data/pointer_map.md's "Animal (Coop/Barn)
// struct" section for the full derivation.
const PLAYER_SUBSTRUCT_OFFSET = 0xbce0; // only used by looksLikeSaveData's stamina sanity check
const ANIMAL_RECORD_SIZE = 0x200;
const ANIMAL_ROSTER_OFFSET = 0x4580; // saveDataBase + this = Coop Slot 1's Name (Secondary) address -- confirmed live
const ANIMAL_SLOT_COUNT = 8; // real coop capacity not yet confirmed live -- guess, room to explore
const BARN_ROSTER_OFFSET = 0x58c0; // saveDataBase + this = Barn Slot 1's Name (Secondary) address -- shifted by the same +0x84 origin correction, still an unconfirmed guess for Barn specifically
const BARN_SLOT_COUNT = 16; // unconfirmed guess -- the corrected origin above resolves the earlier "might really be 15" suspicion (see pointer_map.md), 16 fits cleanly now
const POLL_INTERVAL_MS = 500;

// One record layout, shared by every Coop/Barn slot (see data/pointer_map.md for the derivation
// of each field below -- moved here verbatim from the old per-slot copies in memory_viewer_agent.js).
//
// Facing Direction/Age/X/Y are intentionally NOT included here -- they're tracked in
// src/animal_live_viewer_agent.js's own field list (a separate tab, kept deliberately independent
// from this one), even though they mathematically resolve to offsets within this same record (see
// data/pointer_map.md's "Animal (Coop/Barn) struct" section).
// KNOWN_FIELDS is an injected global now, generated from data/tables/animal_viewer.json (see
// app/agent_loader.py) -- its `calc` entries arrive as decoder-name strings, not functions (JSON
// can't hold functions), hydrated back into real callables here.
const DECODERS = {
    yesNo: (v) => (v ? 'Yes' : 'No'),
    // bit 8 (0x100) = brushed, bit 16 (0x10000) = talked to -- same confirmed bits as
    // animal_live_viewer_agent.js's identical decoder (cross-confirmed live 2026-09-12 on a
    // Calf, a Cow, and a Chicken -- see PROGRESS.md).
    dailyInteraction: (v) => {
        const parts = [];
        if (v & 0x10000) parts.push('Talked to');
        if (v & 0x100) parts.push('Brushed');
        return parts.length ? parts.join(', ') : 'No interaction today';
    },
};
for (const field of KNOWN_FIELDS) {
    if (field.calc) field.calc = DECODERS[field.calc];
}

function offsetLabel(offset) {
    return '+0x' + offset.toString(16);
}

// Builds ROW_TEMPLATE once: known fields (sorted by offset) plus 4-byte "unknown" rows filling
// every gap between them and out to the full ANIMAL_RECORD_SIZE stride boundary -- covers the
// entire record, not just up to the last identified field, so nothing is hidden.
function buildRowTemplate() {
    const known = KNOWN_FIELDS.slice().sort((a, b) => a.offset - b.offset);
    const rows = [];
    let cursor = 0;
    for (const field of known) {
        while (cursor + 4 <= field.offset) {
            rows.push({ offset: cursor, name: null, size: 4 });
            cursor += 4;
        }
        rows.push(field);
        cursor = Math.ceil((field.offset + field.size) / 4) * 4;
    }
    while (cursor < ANIMAL_RECORD_SIZE) {
        rows.push({ offset: cursor, name: null, size: 4 });
        cursor += 4;
    }
    return rows;
}

const ROW_TEMPLATE = buildRowTemplate();

// One column per animal slot -- static for the life of the script, sent once in 'schema'. Each
// roster labels its own slots 1-N (Coop Slot 1-8, Barn Slot 1-16) rather than continuous 1-24
// numbering, which obscured that Barn's real capacity is 16, not a continuation of Coop's count --
// per-roster numbering makes that limit visible directly in the label.
const COLUMNS = [];
for (let i = 0; i < ANIMAL_SLOT_COUNT; i++) COLUMNS.push({ roster: 'Coop', slotIndex: i, label: 'Coop Slot ' + (i + 1) });
for (let i = 0; i < BARN_SLOT_COUNT; i++) COLUMNS.push({ roster: 'Barn', slotIndex: i, label: 'Barn Slot ' + (i + 1) });

function readField(base, field) {
    const addr = base.add(field.offset);
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
    // cell in the whole grid qualifies as "changed" at once. See
    // app/windows/animal_viewer.py's _on_cell_batch for the Python side of this.
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
// src/memory_viewer_agent.js's listenForSetSaveDataBase uses.
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
