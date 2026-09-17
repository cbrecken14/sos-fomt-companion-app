// Villagers: a homogenized matrix over every non-marriage-candidate, non-sprite villager -- split
// out of Character Viewer alongside Marriage Candidates (see that file's header comment for the
// shared rationale: homogenization means every row fits within EVERY included villager's own real
// window, capped at the group's SMALLEST window rather than the largest, and specifically NOT just
// a hand-picked subset like Friendship/Heart Level/Location -- Heart Level doesn't make sense for
// a window that specifically excludes marriage candidates, and may or may not even survive the
// homogenization cutoff on its own merits). Includes
// Gotts and Saibara, but their own extra carpenter/forge-job-specific bytes past the shared region
// are excluded the same way any other villager's non-shared tail would be -- by virtue of some
// OTHER, more tightly-packed villager in this list setting HOMOGENIZED_WINDOW_SIZE below, not by
// special-casing them. Internal ID ordering/labeling, same as Marriage Candidates. This file
// mirrors marriage_candidates_agent.js exactly except for which villagers it includes.
//
// VILLAGER_OFFSETS, INTERNAL_IDS, KNOWN_FIELDS, NAME_OVERRIDES, MARRIAGE_CANDIDATE_IDS are
// injected globals, generated from data/tables/character_viewer.json (see app/agent_loader.py) --
// the exact same source Marriage Candidates reads from.

const RELATIONSHIP_MANAGER_OFFSET = 0xcacc; // saveDataBase + this = RelationshipManagerBase -- confirmed across a restart, 2026-09-08
const PLAYER_SUBSTRUCT_OFFSET = 0xbce0; // only used by looksLikeSaveData's stamina sanity check
// Fallback window for a column whose "next" offset in the array isn't actually larger -- see
// marriage_candidates_agent.js's identical constant/reasoning.
const FALLBACK_WINDOW_SIZE = 0x38;
const POLL_INTERVAL_MS = 500;

// Same technique as marriage_candidates_agent.js's computeWindowSize -- the gap to the CLOSEST
// LARGER offset ANYWHERE in the full (unfiltered) array (not just scanning forward from this
// position), since that's the real boundary to the next character's record regardless of which
// columns this particular tab happens to display (fixes a bug where Brandon/Jennifer's
// out-of-sequence offsets made Lou's window read 216 bytes instead of the true 56 -- Lou is
// one of THIS tab's own columns, so this fix also corrects his previously-bogus Location reading,
// which was actually landing in Brandon's record).
// Gotts/Saibara each get a real (large) windowSize here reflecting their own extra job-tracking
// bytes -- irrelevant to HOMOGENIZED_WINDOW_SIZE below since some other, more tightly-packed
// villager already sets a smaller minimum, but keeps the per-column bounds check in poll() correct
// regardless.
function computeWindowSize(index) {
    const current = VILLAGER_OFFSETS[index];
    let closest = null;
    for (let j = 0; j < VILLAGER_OFFSETS.length; j++) {
        const candidate = VILLAGER_OFFSETS[j];
        if (candidate > current && (closest === null || candidate < closest)) closest = candidate;
    }
    return closest === null ? FALLBACK_WINDOW_SIZE : closest - current;
}

const COLUMNS = VILLAGER_OFFSETS
    .map((offset, i) => {
        const id = INTERNAL_IDS[i];
        if (id === null || MARRIAGE_CANDIDATE_IDS.has(id)) return null;
        const name = NAME_OVERRIDES[id];
        return { id, offset, label: name || ('ID ' + id), windowSize: computeWindowSize(i) };
    })
    .filter((column) => column !== null)
    .sort((a, b) => a.id - b.id); // ordered by Internal ID

function offsetLabel(offset) {
    return '+0x' + offset.toString(16);
}

const HOMOGENIZED_WINDOW_SIZE = Math.min(...COLUMNS.map((c) => c.windowSize));

// Known fields (sorted by offset) plus 4-byte "unknown" rows filling every gap between them and
// out to the homogenized window -- same pattern as marriage_candidates_agent.js's buildRowTemplate,
// just capped at the group's MINIMUM window instead of the MAX, so nothing here can ever read into
// a tighter neighbor's own record. A known field whose offset+size doesn't fit (and everything
// after it, since KNOWN_FIELDS is offset-sorted) is left out entirely, same as any unknown row past
// the boundary -- not shown as a half-populated row.
function buildRowTemplate() {
    const known = KNOWN_FIELDS.slice().sort((a, b) => a.offset - b.offset);
    const rows = [];
    let cursor = 0;
    for (const field of known) {
        if (field.offset + field.size > HOMOGENIZED_WINDOW_SIZE) break;
        while (cursor + 4 <= field.offset) {
            rows.push({ offset: cursor, name: null, size: 4 });
            cursor += 4;
        }
        rows.push(field);
        cursor = Math.ceil((field.offset + field.size) / 4) * 4;
    }
    while (cursor < HOMOGENIZED_WINDOW_SIZE) {
        rows.push({ offset: cursor, name: null, size: 4 });
        cursor += 4;
    }
    return rows;
}

const ROW_TEMPLATE = buildRowTemplate();

function readField(base, field) {
    const addr = base.add(field.offset);
    if (field.type === 'float') return addr.readFloat();
    if (field.size === 1) return addr.readU8();
    if (field.size === 2) return addr.readU16();
    return addr.readS32(); // signed -- see marriage_candidates_agent.js's identical note
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
    // app/windows/villagers.py's _on_cell_batch for the Python side of this.
    const updates = [];
    for (let colIndex = 0; colIndex < COLUMNS.length; colIndex++) {
        const base = columnBase(COLUMNS[colIndex]);
        if (base === null) continue;
        for (let rowIndex = 0; rowIndex < ROW_TEMPLATE.length; rowIndex++) {
            const row = ROW_TEMPLATE[rowIndex];
            if (row.offset + row.size > COLUMNS[colIndex].windowSize) continue; // past this villager's own record -- leave blank rather than read into the next one's
            const key = rowIndex + ':' + colIndex;
            let raw;
            try {
                raw = readField(base, row);
            } catch (e) {
                continue;
            }
            if (lastValues[key] === raw) continue;
            lastValues[key] = raw;
            updates.push({ rowIndex, colIndex, raw });
        }
    }
    if (updates.length > 0) send({ type: 'cellBatch', updates });
}

function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

// Dumps one right-clicked villager's whole valid window (the same homogenized-independent
// per-column windowSize poll() itself respects) as raw bytes, printed to the console by
// app/windows/villagers.py -- same pattern as marriage_candidates_agent.js's dumpBytes.
function dumpBytes(colIndex) {
    const column = COLUMNS[colIndex];
    if (!column) return;
    const base = columnBase(column);
    if (base === null) {
        send({ type: 'status', message: 'Dump Bytes: no Save Data Base yet' });
        return;
    }
    const bytes = Array.from(new Uint8Array(base.readByteArray(column.windowSize)));
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

// One-shot bulk write, same category as farm_tile_debug_agent.js's debug actions -- no
// Preferences on/off toggle needed (per this project's write-feature rule, that's for a STANDING
// hook left running, not a single button click that writes once and is done). Adds `amount` to
// every displayed column's own copy of the named KNOWN_FIELDS field, skipping any column whose
// window doesn't reach that offset.
function addToField(fieldName, amount) {
    const field = KNOWN_FIELDS.find((f) => f.name === fieldName);
    if (!field) return;
    let count = 0;
    for (const column of COLUMNS) {
        if (field.offset + field.size > column.windowSize) continue;
        const base = columnBase(column);
        if (base === null) continue;
        const addr = base.add(field.offset);
        addr.writeS32(addr.readS32() + amount);
        count++;
    }
    send({ type: 'status', message: 'Added ' + amount + ' ' + fieldName + ' to ' + count + ' character(s)' });
}

function listenForAddFriendship() {
    recv('addFriendship', (message) => {
        addToField('Friendship', message.amount);
        listenForAddFriendship();
    });
}
listenForAddFriendship();

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
