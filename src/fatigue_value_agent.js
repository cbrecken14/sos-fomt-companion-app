// Live poller backing the Fatigue Value overlay (app/overlays/fatigue_value.py).
// Fatigue is a u16 field at +0x3B8 on the PLAYER-STATE sub-struct (see data/pointer_map.md's
// "Player-state struct" section), not directly on Save Data Base itself -- that sub-struct sits
// at SaveDataBase + 0xBCE0 (same PLAYER_SUBSTRUCT_OFFSET constant src/memory_viewer_agent.js
// uses). Missing that extra +0xBCE0 was a bug caught live 2026-09-13: the overlay read some
// unrelated, effectively-static bytes and never appeared to update, even though Memory Viewer's
// Fatigue row (which does add PLAYER_SUBSTRUCT_OFFSET) was updating correctly the whole time.
//
// The game's own getter (exe+2F06C0, see pointer_map.md's "Tired Reaction Animations" section)
// divides the raw stored value by 2 before using it for the tired-animation thresholds -- this
// sends that same halved value (0-100 range, matching the overlay's displayed max of 100), not
// the raw stored number.

const PLAYER_SUBSTRUCT_OFFSET = 0xbce0;
const FATIGUE_OFFSET = PLAYER_SUBSTRUCT_OFFSET + 0x3b8;
const POLL_INTERVAL_MS = 250;

let saveDataBase = null;
let pollTimer = null;
let lastRaw = null;

function poll() {
    if (saveDataBase === null) return;
    let raw;
    try {
        raw = saveDataBase.add(FATIGUE_OFFSET).readU16();
    } catch (e) {
        return; // base went stale (process closing, etc.) -- just skip this tick
    }
    if (raw === lastRaw) return;
    lastRaw = raw;
    send({ type: 'fatigue', raw: raw });
}

function useSaveDataBase(base) {
    saveDataBase = base;
    lastRaw = null; // force a resend
    if (pollTimer === null) pollTimer = setInterval(poll, POLL_INTERVAL_MS);
    poll();
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom every
// other agent in this project uses (e.g. src/reminders_time_agent.js).
function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        useSaveDataBase(ptr(message.address));
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

send({ type: 'status', message: 'fatigue value agent loaded, waiting for Save Data Base' });
