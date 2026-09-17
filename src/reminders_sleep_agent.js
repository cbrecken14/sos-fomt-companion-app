// Backs the Reminders window's bedtime-recovery reminder (app/reminders_live_data.py's
// RemindersSleepData, app/sleep_recovery.py's formula). Polls the three Player-state sub-struct
// fields the sleep-recovery calc needs -- see data/pointer_map.md's "Player-state struct" section:
//   +0x3B6 Current Stamina (u16)
//   +0x3B8 Fatigue (u16, raw -- same value src/fatigue_value_agent.js halves on the Python side)
//   +0x3B0 Power Berries Found (u8) -- 6 bytes before Stamina. NOTE: corrected 2026-09-13 from an
//   earlier, wrong +0xC090 -- that number came straight off the Memory Viewer's "Unknown
//   +0xOFFSET" label, which already bakes PLAYER_SUBSTRUCT_OFFSET into the number it shows. Adding
//   PLAYER_SUBSTRUCT_OFFSET to it again (as this file originally did) pointed at the wrong address
//   and silently read 0. See data/pointer_map.md's Player-state struct table for the full story.
// All three are offsets from SaveDataBase + PLAYER_SUBSTRUCT_OFFSET (0xBCE0), not from
// SaveDataBase directly -- see this project's "Hard-won lessons" note about that extra offset.

const PLAYER_SUBSTRUCT_OFFSET = 0xbce0;
const STAMINA_OFFSET = PLAYER_SUBSTRUCT_OFFSET + 0x3b6;
const FATIGUE_OFFSET = PLAYER_SUBSTRUCT_OFFSET + 0x3b8;
const POWER_BERRIES_OFFSET = PLAYER_SUBSTRUCT_OFFSET + 0x3b0;

const POLL_INTERVAL_MS = 250; // same cadence as src/fatigue_value_agent.js -- these drain live during play

let saveDataBase = null;
let pollTimer = null;
let lastSnapshot = null;

function poll() {
    if (saveDataBase === null) return;
    let stamina, fatigueRaw, powerBerries;
    try {
        stamina = saveDataBase.add(STAMINA_OFFSET).readU16();
        fatigueRaw = saveDataBase.add(FATIGUE_OFFSET).readU16();
        powerBerries = saveDataBase.add(POWER_BERRIES_OFFSET).readU8();
    } catch (e) {
        return; // base went stale (process closing, etc.) -- just skip this tick
    }
    const snapshot = stamina + ':' + fatigueRaw + ':' + powerBerries;
    if (snapshot === lastSnapshot) return;
    lastSnapshot = snapshot;
    send({ type: 'sleepStatus', stamina: stamina, fatigueRaw: fatigueRaw, powerBerries: powerBerries });
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

send({ type: 'status', message: 'reminders sleep agent loaded, waiting for Save Data Base' });
