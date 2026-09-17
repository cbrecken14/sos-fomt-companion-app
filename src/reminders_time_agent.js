// Live poller backing the Reminders window's Header/Weather display (app/reminders_live_data.py).
// TimeBasePtr arrives via a `setTimeBase` message from the shared session (app/game_session.py
// already finds and validates it via src/core_hooks_agent.js) -- this script never hooks
// anything of its own, it just polls the known field offsets from there.
//
// Field layout, all offsets from TimeBasePtr (see data/pointer_map.md's "Time/Weather struct"
// section): Weather -0x08, Next Weather -0x04, Year (x1) +0x00, Year (x7) +0x04, Season +0x08,
// Day +0x0C, Hour +0x10, Minute +0x11. Weather/Day/Hour/Minute are confirmed live; Next
// Weather/Year (x1)/Year (x7)/Season are only "predicted from layout", not independently
// confirmed -- read anyway (no reason not to), but app/reminders_live_data.py surfaces that
// distinction rather than silently trusting them the same as the confirmed fields.

const POLL_INTERVAL_MS = 250;

const FIELDS = [
    { key: 'weather', offset: -0x08, min: 0, max: 4 },
    { key: 'nextWeather', offset: -0x04, min: 0, max: 4 },
    { key: 'yearX1', offset: 0x00, min: 0, max: 6 },
    { key: 'yearX7', offset: 0x04, min: 0, max: 29 },
    { key: 'season', offset: 0x08, min: 0, max: 3 },
    { key: 'day', offset: 0x0c, min: 0, max: 29 },
    { key: 'hour', offset: 0x10, min: 0, max: 23 },
    { key: 'minute', offset: 0x11, min: 0, max: 59 },
];

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
let pollTimer = null;

function poll() {
    if (timeBase === null) return;
    const values = {};
    try {
        for (const f of FIELDS) {
            values[f.key] = timeBase.add(f.offset).readU8();
        }
    } catch (e) {
        return; // base went stale (process closing, etc.) -- just skip this tick
    }
    send({ type: 'time', values: values });
}

function useTimeBase(base) {
    timeBase = base;
    if (pollTimer === null) {
        pollTimer = setInterval(poll, POLL_INTERVAL_MS);
    }
    poll(); // don't wait a full interval for the first reading
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom
// memory_viewer_agent.js's listenForSetTimeBase uses.
function listenForSetTimeBase() {
    recv('setTimeBase', (message) => {
        const candidate = ptr(message.address);
        if (looksLikeTimeBase(candidate)) {
            useTimeBase(candidate);
        } else {
            send({ type: 'status', message: `setTimeBase address ${candidate} didn't look valid, ignored` });
        }
        listenForSetTimeBase();
    });
}
listenForSetTimeBase();

send({ type: 'status', message: 'reminders time agent loaded, waiting for TimeBasePtr' });
