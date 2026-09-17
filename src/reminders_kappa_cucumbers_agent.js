// Backs the Reminders window's Hidden Counters section -- the Kappa Cucumbers counter
// (app/reminders_live_data.py's RemindersKappaCucumbersData). See data/pointer_map.md's "Kappa
// Cucumbers Counter (Blue Power Berry reward)" section:
//   SaveDataBase + 0xE614 -- confirmed live across a fresh restart. Throwing cucumbers
//   into the lake near Kappa awards a one-time Blue Power Berry once this counter reaches 11
//   (includes the first cucumber that just meets Kappa -- an earlier pass wrongly assumed a
//   10-cucumber threshold). Confirmed live: the counter resets to 0 the instant the reward is
//   given -- so a separate
//   permanent flag, Blue Power Berry Obtained (PLAYER_SUBSTRUCT_OFFSET + 0x3B2, see the
//   Player-state struct table), is what actually distinguishes "never earned it" from "already
//   claimed it, counter's back at 0."

const PLAYER_SUBSTRUCT_OFFSET = 0xbce0;
const KAPPA_CUCUMBERS_OFFSET = 0xe614;
const BLUE_POWER_BERRY_OBTAINED_OFFSET = PLAYER_SUBSTRUCT_OFFSET + 0x3b2;
const POLL_INTERVAL_MS = 500; // only changes when a cucumber is actually thrown in

let saveDataBase = null;
let pollTimer = null;
let lastValue = null;
let lastObtained = null;

function poll() {
    if (saveDataBase === null) return;
    let value, obtained;
    try {
        value = saveDataBase.add(KAPPA_CUCUMBERS_OFFSET).readU32();
        obtained = saveDataBase.add(BLUE_POWER_BERRY_OBTAINED_OFFSET).readU8();
    } catch (e) {
        return; // base went stale (process closing, etc.) -- just skip this tick
    }
    if (value === lastValue && obtained === lastObtained) return;
    lastValue = value;
    lastObtained = obtained;
    send({ type: 'kappaCucumbers', value: value, obtained: obtained !== 0 });
}

function useSaveDataBase(base) {
    saveDataBase = base;
    lastValue = null; // force a resend
    lastObtained = null;
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

send({ type: 'status', message: 'reminders kappa cucumbers agent loaded, waiting for Save Data Base' });
