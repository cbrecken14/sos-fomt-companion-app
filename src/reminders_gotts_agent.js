// Backs the Reminders window's Gotts building-upgrade tracking (app/reminders_live_data.py's
// RemindersGottsData). Gotts (Character Viewer ID 28, RelationshipManagerBase + 0x954) carries
// carpenter-specific job-tracking fields not present on regular villagers -- found 2026-09-13 by
// diffing his record across two separate building upgrades (a Coop Upgrade and a First House
// Upgrade) over their full multi-day duration. See data/pointer_map.md's "Gotts building upgrade
// job tracking" section for the full derivation.
//
// +0x38 = Job Type / Building ID (0 = no active job; a distinct nonzero value per building type --
// confirmed 2 = Coop Upgrade, 1 = House Upgrade so far, more to be identified over time).
// +0x3C = Days Remaining -- a clean countdown to 0 on the day the job completes. Does NOT
// decrement on the day the job is purchased, or again on the very first day after that (the day
// construction visibly starts) -- only starts counting down the day after that. Also confirmed
// 2026-09-13 to hold flat (not decrement) on a festival day at any point in the countdown.
// +0x24 = Activity/schedule state code -- `1` while idle OR while a job has just been purchased
// but construction hasn't visibly started yet; a distinct nonzero value once construction is
// actually under way (confirmed `8` for Coop Upgrade, `6` for House Upgrade). This is the only
// field that changes between "just purchased" and "day 1 of construction" -- Days Remaining reads
// identically for both -- so it's what `app/reminders_live_data.py` uses to tell them apart.

const RELATIONSHIP_MANAGER_OFFSET = 0xcacc; // RelationshipManagerBase = SaveDataBase + this
const GOTTS_OFFSET = 0x954; // ID 28's offset from RelationshipManagerBase
const JOB_TYPE_OFFSET = 0x38;
const DAYS_REMAINING_OFFSET = 0x3c;
const ACTIVITY_STATE_OFFSET = 0x24;

const POLL_INTERVAL_MS = 500;

let gottsBase = null;
let lastSnapshot = null;
let pollTimer = null;

function poll() {
    if (gottsBase === null) return;
    let jobType, daysRemaining, activityState;
    try {
        jobType = gottsBase.add(JOB_TYPE_OFFSET).readU32();
        daysRemaining = gottsBase.add(DAYS_REMAINING_OFFSET).readU32();
        activityState = gottsBase.add(ACTIVITY_STATE_OFFSET).readU32();
    } catch (e) {
        return;
    }
    const snapshot = jobType + ':' + daysRemaining + ':' + activityState;
    if (snapshot === lastSnapshot) return;
    lastSnapshot = snapshot;
    send({ type: 'gotts', jobType: jobType, daysRemaining: daysRemaining, activityState: activityState });
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
    gottsBase = base.add(RELATIONSHIP_MANAGER_OFFSET).add(GOTTS_OFFSET);
    lastSnapshot = null; // force a resend against the new base
    startPolling();
    poll();
    send({ type: 'status', message: 'gotts: save data base received ' + base.toString() });
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom every
// other listener in this project uses.
function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        const candidate = ptr(message.address);
        if (looksLikeSaveData(candidate)) useSaveDataBase(candidate);
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

send({ type: 'status', message: 'reminders gotts agent loaded, waiting for Save Data Base' });
