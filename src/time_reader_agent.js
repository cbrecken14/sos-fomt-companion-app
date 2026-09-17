// Reads current Day/Hour/Minute/Season/Year/Weather by hooking a known save-load instruction that
// initializes TimeBasePtr, then polling the known field offsets from there for the rest of the
// session.
//
// Unlike the farm map's grid_base detection, this hook doesn't need an arm/disarm "listen" flow or
// a memcpy-size filter -- it's a specific, already-known game instruction (found via Cheat
// Engine's "find what writes to this address" on Day, triggered by a save reload), not a generic/frequently-
// reused function, so it's safe to leave hooked permanently. It fires exactly once per save load
// (game launch, or reloading a save mid-session), which also means TimeBasePtr only genuinely
// changes on an actual reload -- so a previously-found address is cached (app/hook_cache.py) and
// reused immediately on the next attach, same "cache + validate" idea as the farm's grid_base
// (see PROGRESS.md Milestone 22), without needing that flow's one-shot "listen" gating since there's
// no collision risk here to guard against.
//
// Derivation: the traced instruction is `mov [rcx+0x38], rdi` at
// "STORY OF SEASONS Friends of Mineral Town.exe+2FAECD" -- an 8-byte write that Cheat Engine's
// "find what writes" reported against the Day address. IMPORTANT GOTCHA (found via live testing,
// 2026-09-06 -- every field printed one slot off from reality by a consistent 4 bytes): the watched
// byte only has to fall SOMEWHERE inside an 8-byte write, not at its start. Day's real address
// here is rcx+0x3C (4 bytes into the write), not rcx+0x38 -- so TimeBasePtr = rcx + 0x30, not
// rcx + 0x2C. Confirmed live: Weather/Day both showed up one field label off from correct at the
// wrong offset, exactly matching a base that's 4 bytes too low.
// Full field layout (see data/pointer_map.md): Weather -0x08, Next Weather -0x04, Year(x1) +0x00,
// Year(x7) +0x04, Season +0x08, Day +0x0C, Hour +0x10, Minute +0x11.

const HOOK_OFFSET = 0x2faecd; // STORY OF SEASONS Friends of Mineral Town.exe+2FAECD
const RCX_TO_TIMEBASE = 0x30;
const POLL_INTERVAL_MS = 250;

const FIELDS = [
    { name: 'Weather', offset: -0x08, min: 0, max: 4 },
    { name: 'Next Weather', offset: -0x04, min: 0, max: 4 },
    { name: 'Year (x1)', offset: 0x00, min: 0, max: 6 },
    { name: 'Year (x7)', offset: 0x04, min: 0, max: 29 },
    { name: 'Season', offset: 0x08, min: 0, max: 3 },
    { name: 'Day', offset: 0x0c, min: 0, max: 29 },
    { name: 'Hour', offset: 0x10, min: 0, max: 23 },
    { name: 'Minute', offset: 0x11, min: 0, max: 59 },
];

// Sanity-checks a candidate TimeBasePtr against every field's known valid range at once -- catches
// a stale cached address left over from before a game restart (same purpose as the farm's
// looksLikeFarmGrid, just against this struct's own field ranges instead of tile shape).
function looksLikeTimeBase(base) {
    try {
        for (const f of FIELDS) {
            const value = base.add(f.offset).readU8();
            if (value < f.min || value > f.max) return false;
        }
        return true;
    } catch (e) {
        return false;
    }
}

let timeBase = null;
let lastValues = {};
let pollTimer = null;

function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setInterval(() => {
        if (timeBase === null) return;
        for (const f of FIELDS) {
            let value;
            try {
                value = timeBase.add(f.offset).readU8();
            } catch (e) {
                continue;
            }
            if (lastValues[f.name] !== value) {
                lastValues[f.name] = value;
                send({ type: 'value', label: f.name, value: value });
            }
        }
    }, POLL_INTERVAL_MS);
}

function useTimeBase(base, { fromCache }) {
    timeBase = base;
    lastValues = {};
    send({
        type: fromCache ? 'status' : 'timeBaseFound',
        address: base.toString(),
        message: (fromCache ? 'using cached TimeBasePtr ' : 'TimeBasePtr resolved: ') + base.toString(),
    });
    startPolling();
}

// Three ways timeBase can end up set, same shape as the farm map's grid_base:
// - TIME_BASE_OVERRIDE, substituted at script-load time -- a previously-cached address the Python
//   side loaded from disk. Validated with looksLikeTimeBase before being trusted.
// - The permanent save-load hook below, any time it fires (first time this session, or again if
//   the player reloads another save later -- always accepted, no "listening" gate needed).
if (TIME_BASE_OVERRIDE !== null && looksLikeTimeBase(TIME_BASE_OVERRIDE)) {
    useTimeBase(TIME_BASE_OVERRIDE, { fromCache: true });
} else if (TIME_BASE_OVERRIDE !== null) {
    send({
        type: 'status',
        message: `cached TimeBasePtr ${TIME_BASE_OVERRIDE} doesn't look valid (stale? game restarted?) -- waiting for a save reload`,
    });
} else {
    send({ type: 'status', message: 'no cached TimeBasePtr yet -- waiting for a save reload' });
}

const hookAddr = Process.mainModule.base.add(HOOK_OFFSET);
send({
    type: 'status',
    message: 'hooked at ' + hookAddr + (timeBase === null ? ' -- save/reload the game to arm the timer' : ''),
});

Interceptor.attach(hookAddr, {
    onEnter(args) {
        const base = this.context.rcx.add(RCX_TO_TIMEBASE);
        useTimeBase(base, { fromCache: false });
    },
});
