// Harvest Sprite Viewer: matrix over the 7 Harvest Sprite records, split out from the now-removed
// Character Viewer since sprites were expected to have different tracked properties than regular
// villagers. That held up for most of the record (Status/Skill Levels/Task Assignment/Days Left
// below have no villager equivalent at the same offsets), but the leading 0x0-0xC block turned out
// to be identical to the villager struct's own Location/Spawn X/Spawn Y/Spawn Facing Direction
// (same offsets/types as data/tables/character_viewer.json's KNOWN_FIELDS, see below). Friendship
// (+0x10) is also
// confirmed for sprites (src/reminders_harvest_sprites_agent.js, the Harvest Sprite Auto-Minigame
// cheat's training-eligibility gate) but isn't in this window's own KNOWN_FIELDS table yet.
//
// Sprites share the exact same underlying system as villagers (RelationshipManagerBase + a fixed
// per-ID offset from the game's own compiled ID lookup, exe+2F6B40 -- see data/pointer_map.md's
// "NPC/Villager relationship struct" section, PROGRESS.md Milestone 32), but their 7 offsets are
// evenly spaced exactly 0x50 (80 bytes) apart -- a noticeably tighter, more uniform pattern than
// regular villagers (whose gaps range from ~0x38 to ~0xD8).
//
// Confirmed fields:
//  +0x0  Location -- same LOCATION enum concept as villagers.
//  +0x4  Spawn X / +0x8 Spawn Y -- floats, same as villagers.
//  +0xC  Spawn Facing Direction.
//  +0x18 Status -- a byte-packed flags value. Decoding the 4 observed samples
//        as individual bytes (MSB..LSB): "not talked, no gift" = 01 00 00 00; "talked, no gift" =
//        01 00 01 01; "talked + gift" = 01 01 01 01; "gift, not talked" = 01 01 00 00. Top byte is
//        a constant 1 in every sample (probably an "assigned/active" flag). The middle byte tracks
//        Gift Given; the bottom TWO bytes both track Talked To identically in every sample so far
//        (likely a duplicated flag, not two independent things -- only 4 data points, not fully
//        proven).
//  +0x38 Skill Levels -- 3 packed skill bytes (top byte unused/always 0 so far). Setting this to
//        16777215 (0x00FFFFFF) maxed all 3 skills "mostly" -- suggests the real in-game display
//        cap is below 255, so a raw 0xFF overshoots. Byte order assumed to match the +0x40 task
//        enum below (Harvest/Watering/Animal Care), NOT independently confirmed which byte is which.
//  +0x40 Task Assignment -- enum: 0 Harvest Crops, 1 Watering, 2 Animal Care, 3 Not Assigned.
//  +0x44 Days Left (of current work assignment) -- plain integer.
//
// Everything else in the window is still a plain "Unknown" gap row -- interact with sprites and
// watch which offset moves to find more.

const RELATIONSHIP_MANAGER_OFFSET = 0xcacc; // saveDataBase + this = RelationshipManagerBase -- confirmed across a restart, 2026-09-08
const PLAYER_SUBSTRUCT_OFFSET = 0xbce0; // only used by looksLikeSaveData's stamina sanity check
const SPRITE_WINDOW_SIZE = 0x50; // confirmed stride between consecutive sprites -- see header comment
const POLL_INTERVAL_MS = 500;

// SPRITES (offsets/names read directly off the same case table as Character Viewer, see
// data/pointer_map.md's "Confirmed IDs" table) and TASK_NAMES are injected globals now, generated
// from data/tables/harvest_sprite_viewer.json (see app/agent_loader.py) -- single source, no
// longer hand-written in this file.

function decodeStatus(raw) {
    const talked = (raw & 0xff) !== 0 || ((raw >>> 8) & 0xff) !== 0;
    const gifted = ((raw >>> 16) & 0xff) !== 0;
    return (talked ? 'Talked' : 'Not talked') + ', ' + (gifted ? 'Gift given' : 'No gift');
}

function decodeSkills(raw) {
    // Byte order (Harvest/Water/Animal) assumed to match the Task enum -- not independently confirmed.
    const harvest = raw & 0xff;
    const water = (raw >>> 8) & 0xff;
    const animal = (raw >>> 16) & 0xff;
    return 'Harvest ' + harvest + ' / Water ' + water + ' / Animal ' + animal;
}

function decodeTask(raw) {
    return TASK_NAMES[raw] !== undefined ? TASK_NAMES[raw] : 'Unknown (' + raw + ')';
}

// KNOWN_FIELDS is an injected global (data/tables/harvest_sprite_viewer.json) whose `calc` entries
// arrive as decoder-name strings, not functions (JSON can't hold functions) -- hydrate them back
// into real callables here, right after the data arrives.
const DECODERS = { decodeStatus, decodeSkills, decodeTask };
for (const field of KNOWN_FIELDS) {
    if (field.calc) field.calc = DECODERS[field.calc];
}

function offsetLabel(offset) {
    return '+0x' + offset.toString(16);
}

// Known fields (sorted by offset) plus 4-byte "unknown" rows filling every gap between them and
// out to the full window -- same pattern as villagers_agent.js's buildRowTemplate.
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
    while (cursor < SPRITE_WINDOW_SIZE) {
        rows.push({ offset: cursor, name: null, size: 4 });
        cursor += 4;
    }
    return rows;
}

const ROW_TEMPLATE = buildRowTemplate();

const COLUMNS = SPRITES.map((sprite) => ({
    id: sprite.id,
    offset: sprite.offset,
    label: sprite.name,
}));

function readField(base, field) {
    const addr = base.add(field.offset);
    if (field.type === 'float') return addr.readFloat();
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

let relationshipManagerBase = null;
const lastValues = {}; // keyed by "rowIndex:colIndex"
let pollTimer = null;

function columnBase(column) {
    if (relationshipManagerBase === null) return null;
    return relationshipManagerBase.add(column.offset);
}

function poll() {
    // Collected into one batch and sent as a single message (2026-09-16) instead of one send()
    // per changed cell -- on the first poll after a (re-)attach, lastValues is empty and EVERY
    // cell in the whole grid qualifies as "changed" at once. See
    // app/windows/harvest_sprite_viewer.py's _on_cell_batch for the Python side of this.
    const updates = [];
    for (let colIndex = 0; colIndex < COLUMNS.length; colIndex++) {
        const base = columnBase(COLUMNS[colIndex]);
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

// Dumps one right-clicked sprite's whole window as raw bytes, printed to the console by
// app/windows/harvest_sprite_viewer.py -- same pattern as villagers_agent.js's dumpBytes.
function dumpBytes(colIndex) {
    const column = COLUMNS[colIndex];
    if (!column) return;
    const base = columnBase(column);
    if (base === null) {
        send({ type: 'status', message: 'Dump Bytes: no Save Data Base yet' });
        return;
    }
    const bytes = Array.from(new Uint8Array(base.readByteArray(SPRITE_WINDOW_SIZE)));
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

function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

function sendColumnBases() {
    const bases = COLUMNS.map((column) => {
        const base = columnBase(column);
        return base === null ? null : base.toString();
    });
    send({ type: 'columnBases', bases });
}

function useSaveDataBase(base) {
    relationshipManagerBase = base.add(RELATIONSHIP_MANAGER_OFFSET);
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
