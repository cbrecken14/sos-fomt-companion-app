// Backs the Reminders window's Forge (Saibara job) tracking (app/reminders_live_data.py's
// RemindersForgeData). Both fields come off Saibara's own record
// (RelationshipManagerBase + 0x3DC, ID 7) -- same base pattern already confirmed for Gotts'
// building-upgrade job (see reminders_gotts_agent.js / data/pointer_map.md's "Gotts building
// upgrade job tracking" section), though Saibara's own job doesn't need Gotts' third field
// (Activity/Schedule State) -- see the Days Remaining note below for why.
//
// +0x38 = Job/Catalog ID. NOT a plain "tool level" -- confirmed live to be a single
//   flat enumeration of everything Saibara can make, unique per (tool, level) pair AND per
//   non-tool item, which is why this agent reads it directly instead of the 6 separate per-tool
//   target-level fields the app used at first. Decode:
//     - 0 = no active job.
//     - 1-29 = a tool upgrade: `toolIndex = Math.floor((id - 1) / 5)`, `level = ((id - 1) % 5) + 1`
//       (level 1-4 = Copper/Silver/Golden/Mythril; Iron/0 is never itself a purchase target, so
//       ids 0/5/10/15/20/25 never occur in practice). toolIndex 0-5 = Sickle/Hoe/Axe/Hammer/
//       Watering Can/Fishing Rod, confirmed via 4 of the 6 blocks (Sickle/Hoe/Axe/Hammer); the
//       last two (Watering Can/Fishing Rod) are inferred by the same ordering the TOOL item enum
//       already uses, not yet independently confirmed live.
//     - 30+ = a non-tool item (accessories, machines, ...) -- looked up in
//       data/tables/enums.json's SAIBARA_CATALOG table on the Python side, filled in one item at a
//       time as each is identified (same convention as the BUILDING enum).
// +0x3C = Days Remaining. Confirmed 2026-09-13: total forge days is NOT a fixed function of the
//   target level alone -- e.g. Copper->Silver only takes 1 day, not Silver's "normal" 2, because
//   it's actually (target level - current level) tiers being jumped, each apparently worth 1 day.
//   Doesn't matter here since this field is read live off Saibara's own counter, never computed
//   from the target level. Reads the full total already on the purchase day itself (doesn't wait
//   until the next day to show a real number), then counts down by 1 each day starting the day
//   after purchase, reaching 0 the day it's ready for pickup, and STAYS at 0 (does not go negative
//   or reset) until the finished item is actually collected -- confirmed 2026-09-13 by dumping a
//   Mythril Sickle build all the way through: Days Remaining alone, with no other field needed,
//   distinguishes "just purchased" (reads the full total), "in progress" (counts down), and "ready,
//   not yet collected" (reads 0) -- Saibara's own Activity/Schedule State (+0x24, Gotts' third
//   field) turned out unnecessary here and isn't read by this agent.
//   **Confirmed 2026-09-13 (a holiday landed mid-job): unlike Gotts' building-upgrade countdown,
//   a holiday/festival day does NOT freeze this one** -- it decremented normally through a holiday
//   that fell on day 2 of a 4-day Mythril Sickle build.
// Not yet confirmed across a fresh restart -- two solid sessions' worth of dumps (both on the same
// Mythril Sickle upgrade path), not this project's usual two-independent-*restart* bar.

const RELATIONSHIP_MANAGER_OFFSET = 0xcacc; // RelationshipManagerBase = SaveDataBase + this
const SAIBARA_OFFSET = 0x3dc; // ID 7's offset from RelationshipManagerBase
const JOB_ID_OFFSET = 0x38;
const DAYS_REMAINING_OFFSET = 0x3c;

const POLL_INTERVAL_MS = 500;

let saibaraBase = null;
let lastSnapshot = null;
let pollTimer = null;

function poll() {
    if (saibaraBase === null) return;
    let jobId, daysRemaining;
    try {
        jobId = saibaraBase.add(JOB_ID_OFFSET).readU32();
        daysRemaining = saibaraBase.add(DAYS_REMAINING_OFFSET).readU32();
    } catch (e) {
        return;
    }
    const snapshot = jobId + ":" + daysRemaining;
    if (snapshot === lastSnapshot) return;
    lastSnapshot = snapshot;
    send({ type: "forge", jobId: jobId, daysRemaining: daysRemaining });
}

function startPolling() {
    if (pollTimer === null) pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

function looksLikeSaveData(base) {
    try {
        const money = base.add(0xbc50).readU32();
        return money <= 99999999;
    } catch (e) {
        return false;
    }
}

function useSaveDataBase(base) {
    saibaraBase = base.add(RELATIONSHIP_MANAGER_OFFSET).add(SAIBARA_OFFSET);
    lastSnapshot = null; // force a resend against the new base
    startPolling();
    poll();
    send({ type: "status", message: "forge: save data base received " + base.toString() });
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom every
// other listener in this project uses.
function listenForSetSaveDataBase() {
    recv("setSaveDataBase", (message) => {
        const candidate = ptr(message.address);
        if (looksLikeSaveData(candidate)) useSaveDataBase(candidate);
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

send({ type: "status", message: "reminders forge agent loaded, waiting for Save Data Base" });
