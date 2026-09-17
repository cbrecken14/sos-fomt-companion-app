// Backs the Reminders window's Hidden Counters section -- the Harvest Goddess's two separate gift
// counters (app/reminders_live_data.py's RemindersHarvestGoddessGiftsData). See
// data/pointer_map.md's "Harvest Goddess Gift Counters" section:
//   SaveDataBase + 0xE664 -- Total Gifts, cumulative, never resets (milestone rewards per
//   data/tables/harvest_goddess_offerings.json).
//   SaveDataBase + 0xE60C -- 10th Gift, counts to 10 then resets (White Grass, unless Total Gifts
//   also lands on one of its own milestones on the same visit -- no double reward).
// These were originally logged as one duplicated field written twice -- corrected once confirmed
// live to be independent counters that only usually move together.

const TOTAL_GIFTS_OFFSET = 0xe664;
const TENTH_GIFT_OFFSET = 0xe60c;
const POLL_INTERVAL_MS = 500; // only changes when a gift is actually given to the Harvest Goddess

let saveDataBase = null;
let pollTimer = null;
let lastSnapshot = null;

function poll() {
    if (saveDataBase === null) return;
    let total, tenth;
    try {
        total = saveDataBase.add(TOTAL_GIFTS_OFFSET).readU32();
        tenth = saveDataBase.add(TENTH_GIFT_OFFSET).readU32();
    } catch (e) {
        return; // base went stale (process closing, etc.) -- just skip this tick
    }
    const snapshot = total + ':' + tenth;
    if (snapshot === lastSnapshot) return;
    lastSnapshot = snapshot;
    send({ type: 'hgGifts', total: total, tenth: tenth });
}

function useSaveDataBase(base) {
    saveDataBase = base;
    lastSnapshot = null; // force a resend
    if (pollTimer === null) pollTimer = setInterval(poll, POLL_INTERVAL_MS);
    poll();
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom every
// other agent in this project uses.
function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        useSaveDataBase(ptr(message.address));
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

send({ type: 'status', message: 'reminders harvest goddess gifts agent loaded, waiting for Save Data Base' });
