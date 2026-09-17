// Reads the game's central save-data struct: Money, and (via a fixed sub-object offset) every
// player character stat -- Current Stamina, Fatigue, Hoe Level, all six Tool EXP values, and both
// Mine Depth Records. Hooks a single save-load instruction that fires whenever a save is loaded
// (game launch, or reloading mid-session) and re-derives every single time, no caching needed.
//
// Derivation: `mov [rsi+0xBC50],eax` at "STORY OF SEASONS Friends of Mineral Town.exe"+94391
// writes Money directly -- `rsi` at that instruction IS this struct's base, no correction needed.
// Found via Cheat Engine's "find what writes to this address" on Money's live address, then
// reloading a save (see PROGRESS.md Milestone 26).
//
// This struct is much bigger than just Money -- at least 74536 bytes, per a live-traced
// allocation (see PROGRESS.md Milestone 30). A whole separate investigation into a dedicated
// "player struct" hook (Milestone 29 -- several dead ends, all turning out to be shared with an
// unrelated menu-refresh code path) was solving an already-solved problem: the player's own stats
// live inside THIS SAME struct, at a fixed offset of `+0xBCE0` from this base. Confirmed two ways:
// (1) independently-measured base addresses for this struct and the separately-hooked "player
// struct" differed by exactly `0xBCE0` in a live session, and (2) that exact constant appears
// literally in the disassembly of the function that initializes both sub-objects
// (`lea rcx,[rbx+0000BCE0]`, right after `lea rcx,[rbx+0000BC50]` for Money, in the same
// function). This offset is compiled into the game, not something that varies by session.
//
// Cached between runs (app/hook_cache.py, key save_data_base_ptr) -- avoids reloading a save every
// single test run; a validated cached address (checked with looksValid before being trusted, same
// as Time/Money originally) skips that wait entirely
// when the game process hasn't restarted since. Still no scanning involved either way -- this
// hook is a plain module offset, so even the "waiting for a reload" path is instant to arm.

const HOOK_OFFSET = 0x94391; // STORY OF SEASONS Friends of Mineral Town.exe+94391
const PLAYER_SUBSTRUCT_OFFSET = 0xbce0;
const POLL_INTERVAL_MS = 250;

const FIELDS = [
    { name: 'Money', offset: 0xbc50, size: 4 },
    { name: 'Current Stamina', offset: PLAYER_SUBSTRUCT_OFFSET + 0x3b6, size: 2 },
    { name: 'Fatigue', offset: PLAYER_SUBSTRUCT_OFFSET + 0x3b8, size: 2 },
    { name: 'Hoe Level', offset: PLAYER_SUBSTRUCT_OFFSET + 0x130, size: 4 },
    { name: 'Hoe EXP', offset: PLAYER_SUBSTRUCT_OFFSET + 0x350, size: 4 },
    { name: 'Sickle EXP', offset: PLAYER_SUBSTRUCT_OFFSET + 0x360, size: 4 },
    { name: 'Axe EXP', offset: PLAYER_SUBSTRUCT_OFFSET + 0x370, size: 4 },
    { name: 'Hammer EXP', offset: PLAYER_SUBSTRUCT_OFFSET + 0x380, size: 4 },
    { name: 'Watering Can EXP', offset: PLAYER_SUBSTRUCT_OFFSET + 0x390, size: 4 },
    { name: 'Fishing Rod EXP', offset: PLAYER_SUBSTRUCT_OFFSET + 0x3a0, size: 4 },
    { name: 'Spring Mine Depth Record', offset: PLAYER_SUBSTRUCT_OFFSET + 0x3c8, size: 2 },
    { name: 'Winter Mine Depth Record', offset: PLAYER_SUBSTRUCT_OFFSET + 0x3ca, size: 2 },
];

function readField(base, field) {
    const addr = base.add(field.offset);
    return field.size === 2 ? addr.readU16() : addr.readU32();
}

// Sanity-checks a freshly-found base against Money and the player stats' plausible ranges before
// trusting it -- cheap defense against a spurious firing, same idea used throughout this project.
function looksValid(base) {
    try {
        const money = base.add(0xbc50).readU32();
        const stamina = base.add(PLAYER_SUBSTRUCT_OFFSET + 0x3b6).readU16();
        const fatigue = base.add(PLAYER_SUBSTRUCT_OFFSET + 0x3b8).readU16();
        return money >= 0 && money <= 99999999 && stamina <= 9999 && fatigue <= 9999;
    } catch (e) {
        return false;
    }
}

let dataBase = null;
let lastValues = {};
let pollTimer = null;

function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setInterval(() => {
        if (dataBase === null) return;
        for (const field of FIELDS) {
            let value;
            try {
                value = readField(dataBase, field);
            } catch (e) {
                continue;
            }
            if (lastValues[field.name] !== value) {
                lastValues[field.name] = value;
                send({ type: 'value', label: field.name, value: value });
            }
        }
    }, POLL_INTERVAL_MS);
}

function useBase(base, { fromCache }) {
    dataBase = base;
    lastValues = {};
    send({
        type: fromCache ? 'status' : 'baseFound',
        address: base.toString(),
        message: (fromCache ? 'using cached save data base ' : 'save data base found: ') + base.toString(),
    });
    startPolling();
}

if (DATA_BASE_OVERRIDE !== null && looksValid(DATA_BASE_OVERRIDE)) {
    useBase(DATA_BASE_OVERRIDE, { fromCache: true });
} else if (DATA_BASE_OVERRIDE !== null) {
    send({
        type: 'status',
        message: `cached save data base ${DATA_BASE_OVERRIDE} doesn't look valid (stale? game restarted?) -- waiting for a save reload`,
    });
} else {
    send({ type: 'status', message: 'no cached save data base yet -- waiting for a save reload' });
}

const moduleBase = Process.mainModule.base;
Interceptor.attach(moduleBase.add(HOOK_OFFSET), {
    onEnter() {
        const candidate = this.context.rsi;
        if (!looksValid(candidate)) return;
        useBase(candidate, { fromCache: false });
    },
});
