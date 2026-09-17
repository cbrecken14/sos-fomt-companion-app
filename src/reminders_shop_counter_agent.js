// Backs the Reminders window's Hidden Counters section -- the Van's Favorite shop-purchase
// counter (app/reminders_live_data.py's RemindersShopCounterData). See data/pointer_map.md's
// "Shop Purchased Item Counter (Van's Favorite mail reward)" section:
//   SaveDataBase + 0xE65C -- increments once per closed store interface (any shop). Checked at
//   bedtime: divisible by 10 (and nonzero) -> Van's Favorite mailed and the counter reset to 0;
//   otherwise left untouched (can carry across multiple days until it lands on a multiple of 10).

const SHOP_COUNTER_OFFSET = 0xe65c;
const POLL_INTERVAL_MS = 500; // only changes on a closed store window / at the nightly check -- no need for a fast poll

let saveDataBase = null;
let pollTimer = null;
let lastValue = null;

function poll() {
    if (saveDataBase === null) return;
    let value;
    try {
        value = saveDataBase.add(SHOP_COUNTER_OFFSET).readU32();
    } catch (e) {
        return; // base went stale (process closing, etc.) -- just skip this tick
    }
    if (value === lastValue) return;
    lastValue = value;
    send({ type: 'shopCounter', value: value });
}

function useSaveDataBase(base) {
    saveDataBase = base;
    lastValue = null; // force a resend
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

send({ type: 'status', message: 'reminders shop counter agent loaded, waiting for Save Data Base' });
