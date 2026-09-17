// Live poller backing the Reminders window's Villager Reminders section AND (2026-09-14) the
// Villagers/Marriage Candidates status tables (app/reminders_live_data.py). Reuses Character
// Viewer's confirmed per-villager addressing (RelationshipManagerBase + VILLAGER_OFFSETS[i], where
// i is the array position whose INTERNAL_IDS[i] equals the villager's Internal ID -- see
// data/pointer_map.md's "NPC/Villager relationship struct" section) and the Status packed-flags
// field at +0x18 -- confirmed generic to EVERY villager (via Character Viewer's own byte-dump
// tooling on Karen, then the Harvest Goddess): byte +0x18/+0x19 =
// Talked To Today (duplicated), +0x1A = Gift Given Today, +0x1B = a permanent "met at least once"
// flag (not daily). Confirmed to reset back to 0 after enough time passes with no interaction
// (Karen, tested 2 days later). Friendship (+0x10), Love Points (+0x38), and Location (+0x00,
// decoded via locationCalc) reuse the same offsets already confirmed live for Marriage
// Candidates/Villagers (data/tables/character_viewer.json's KNOWN_FIELDS) -- no new confirmation
// needed here, just reading them through this agent too. Heart Event Triggered (+0x3C) added
// 2026-09-17, same reasoning -- backs the Reminders window's real Heart Events section.
//
// BUG FIXED: Lillia/Rick not updating in the Villagers table, and most Marriage Candidates reading
// wrong -- this file used to compute `VILLAGER_OFFSETS[villager.id - 1]`, which
// is wrong for every tracked villager. INTERNAL_IDS[i] === i for ids 1-34 (NOT i-1 -- index 0 is a
// reserved special-position slot, not id 1), and ids 37/38 (Brandon/Jennifer) sit at indices
// 42/43, not 36/37. So `id - 1` silently read the PREVIOUS villager's own record for every id
// 2-34/38 (id 1 read the reserved index-0 slot instead, which is why Lillia -- id 1 -- never
// looked like it was updating: nothing real interacts with that slot). Character
// Viewer/Marriage Candidates/Villagers never had this bug -- they build columns by walking
// VILLAGER_OFFSETS/INTERNAL_IDS together by ARRAY POSITION, never re-deriving an index from an id.
// Fixed the same way: build an id->offset map from INTERNAL_IDS/VILLAGER_OFFSETS once, up front.
//
// VILLAGERS (data/tables/villagers.json), VILLAGER_OFFSETS/INTERNAL_IDS (data/tables/
// character_viewer.json), and ENUMS/locationCalc (data/tables/enums.json + src/enum_logic.js) are
// injected globals (app/agent_loader.py).

const RELATIONSHIP_MANAGER_OFFSET = 0xcacc;
const STATUS_OFFSET = 0x18;
const FRIENDSHIP_OFFSET = 0x10;
const LOVE_POINTS_OFFSET = 0x38;
const LOCATION_OFFSET = 0x0;
// Heart Event Triggered (+0x3C, data/pointer_map.md) -- added 2026-09-17 to back the Reminders
// window's real Heart Events (under Available Events). Increments per event fired, in order; used
// as a 0-based index into a villager's 7 data/tables/heart_events.json rows to find the next
// un-triggered one. Same confirmed offset already documented for Marriage Candidates/Villagers,
// just read through this agent too -- no new hook.
const HEART_EVENT_TRIGGERED_OFFSET = 0x3c;
const POLL_INTERVAL_MS = 500;

const OFFSET_BY_ID = {};
INTERNAL_IDS.forEach((id, i) => {
    if (id !== null) OFFSET_BY_ID[id] = VILLAGER_OFFSETS[i];
});

// Every villager with a confirmed game id, excluding the 7 Harvest Sprites -- they already have
// their own dedicated tracking (Harvest Sprite Schedule), so they're deliberately left out here.
const TRACKED = VILLAGERS.filter((v) => v.id !== null && !v.name.includes('(Harvest Sprite)'));

let relationshipManagerBase = null;
let pollTimer = null;
const lastValues = {};

function poll() {
    if (relationshipManagerBase === null) return;
    for (const villager of TRACKED) {
        const offset = OFFSET_BY_ID[villager.id];
        if (offset === undefined) continue;
        const base = relationshipManagerBase.add(offset);
        let statusRaw, friendshipRaw, lovePointsRaw, locationRaw, heartEventTriggeredRaw;
        try {
            statusRaw = base.add(STATUS_OFFSET).readU32();
            friendshipRaw = base.add(FRIENDSHIP_OFFSET).readS32();
            lovePointsRaw = base.add(LOVE_POINTS_OFFSET).readS32();
            locationRaw = base.add(LOCATION_OFFSET).readS32();
            heartEventTriggeredRaw = base.add(HEART_EVENT_TRIGGERED_OFFSET).readU32();
        } catch (e) {
            continue;
        }
        const signature = statusRaw + ':' + friendshipRaw + ':' + lovePointsRaw + ':' + locationRaw
            + ':' + heartEventTriggeredRaw;
        if (lastValues[villager.id] === signature) continue;
        lastValues[villager.id] = signature;
        const talked = (statusRaw & 0xff) !== 0 || ((statusRaw >>> 8) & 0xff) !== 0;
        const gifted = ((statusRaw >>> 16) & 0xff) !== 0;
        // +0x1B, the Status field's 4th packed byte -- "met at least once", a permanent flag that
        // never resets (data/pointer_map.md's "Confirmed field offsets within a villager record"
        // section). Added 2026-09-17 to back Heart Events' "already met X" prerequisites (e.g.
        // Elly's Black 2 needs Jeff already met) -- the byte was already being read as part of
        // statusRaw for Talked/Gifted, just never extracted before now.
        const met = ((statusRaw >>> 24) & 0xff) !== 0;
        send({
            type: 'villager',
            name: villager.name,
            talked: talked,
            gifted: gifted,
            met: met,
            friendship: friendshipRaw,
            lovePoints: lovePointsRaw,
            location: locationCalc(locationRaw),
            heartEventTriggered: heartEventTriggeredRaw,
        });
    }
}

function startPolling() {
    if (pollTimer === null) pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

function useSaveDataBase(base) {
    relationshipManagerBase = base.add(RELATIONSHIP_MANAGER_OFFSET);
    for (const key of Object.keys(lastValues)) delete lastValues[key]; // force a full resend
    startPolling();
    poll();
    send({ type: 'status', message: 'villager reminders: save data base received ' + base.toString() });
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom every
// other listener in this project uses.
function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        useSaveDataBase(ptr(message.address));
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

send({ type: 'status', message: 'reminders villager agent loaded, waiting for Save Data Base' });
