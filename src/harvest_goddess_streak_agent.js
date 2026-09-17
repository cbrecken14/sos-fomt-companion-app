// Harvest Goddess High-Low "Set Starting Win Streak" overlay (2026-09-13) -- backs
// app/overlays/harvest_goddess_streak.py. See data/pointer_map.md's "Harvest Goddess High-Low
// minigame" section for the full derivation, and src/investigations/hg_streak_diagnostic.py /
// hg_streak_poc.py for the investigation trail that found it.
//
// - Channel detection: exe+352A30, edx == 0x5EA identifies the HG channel specifically (rcx at
//   this hook is the same interpreter object the reveal hook below operates on).
// - The interpreter resets its own win-streak counter to 0 almost immediately after the channel
//   opens, via the same generic reveal-write instruction used for every revealed number:
//     exe+351AA2 -- mov [rdi+r10*4+000001DC],r9d
//   with r10 == 3 specifically meaning "win streak" (confirmed live: r9 went 1 -> 2 -> 3 exactly
//   in step with 3 real consecutive wins, and the reset-to-0 write was caught firing the instant
//   the channel opens, before the first round's own number is even revealed).
//
// The chosen streak reaches the game two ways, both driven by the same cached `desiredStreak`
// value (kept current via the `setStreak` message from harvest_goddess_streak.py):
//   1. AUTOMATIC: the moment the channel opens, arm a flag; the very next r10==3 write (the
//      game's own reset-to-0) is intercepted and substituted with `desiredStreak` instead of 0,
//      before the instruction executes. Every r10==3 write after that is left alone -- normal
//      play takes over from there.
//   2. IMMEDIATE: if the player changes their choice in the dropdown WHILE ALREADY in the
//      channel (i.e. after the automatic reset above already fired), `setStreak` also writes
//      `desiredStreak` directly into the captured object right away, instead of waiting for the
//      next channel-open to take effect.
//
// Overlay visibility -- idle-timeout fallback: winning a prize (choosing to "stop" and cash out)
// does NOT go through the normal channel-close path (edx==0x219 at the channel hook) -- confirmed
// live by playing all the way through a prize win and back to normal gameplay with the hook never
// firing again (see data/pointer_map.md). Rather than chase down whatever different code path
// handles a cash-out (new, unmapped territory -- inventory/prize granting isn't investigated
// anywhere in this project yet), this falls back to activity timeout: the reveal-write instruction
// fires constantly while a round is actually in progress (several hits per single guess -- see the
// diagnostic trail above), so if it goes quiet for IDLE_TIMEOUT_MS (10s, confirmed working live)
// while we still think we're in the channel, the overlay hides itself too. This ONLY affects the
// overlay's own visibility, not
// the streak-tracking/override logic above (`inHgChannelReal`, gated purely by the exact hook) --
// so a false idle-timeout during a long human pause just hides the overlay for a moment; the
// instant the next reveal-write fires it un-hides itself again, with zero risk to the actual cheat.

const CHANNEL_HOOK_OFFSET = 0x352a30;
const REVEAL_HOOK_OFFSET = 0x351aa2;
const HG_CHANNEL_ID = 0x5ea;
const STREAK_SLOT = 3;
const HISTORY_DISP = 0x1dc;
const STREAK_FIELD_OFFSET = HISTORY_DISP + STREAK_SLOT * 4; // 0x1E8

const IDLE_TIMEOUT_MS = 10000; // tune this if it hides too eagerly/too slowly in practice
const IDLE_CHECK_INTERVAL_MS = 2000;

const base = Process.mainModule.base;
const channelHookAddr = base.add(CHANNEL_HOOK_OFFSET);
const revealHookAddr = base.add(REVEAL_HOOK_OFFSET);

let desiredStreak = 0; // updated live by harvest_goddess_streak.py's setStreak message
let hgSelf = null; // the HG interpreter object -- only ever updated while inHgChannelReal is true
let armed = false;
let inHgChannelReal = false; // exact, hook-driven -- never affected by the idle timeout below
let visible = false; // what's actually been sent to the UI -- may lag inHgChannelReal on idle
let lastActivityMs = 0;

function setVisible(value) {
    if (value === visible) return;
    visible = value;
    send({ type: 'channelUpdate', inHgChannel: value });
}

function writeStreakNow() {
    if (hgSelf === null) return false;
    try {
        hgSelf.add(STREAK_FIELD_OFFSET).writeS32(desiredStreak);
        return true;
    } catch (e) {
        send({ type: 'status', message: 'writeStreakNow failed: ' + e.message });
        return false;
    }
}

// recv() only fires once per registration -- re-arm for the next request, same idiom every other
// agent script in this project uses.
function listenForSetStreak() {
    recv('setStreak', (message) => {
        desiredStreak = message.value | 0;
        const wroteImmediately = writeStreakNow();
        send({
            type: 'status',
            message:
                'desired streak set to ' + desiredStreak +
                (wroteImmediately ? ' (written immediately)' : ' (will apply next time the channel opens)'),
        });
        listenForSetStreak();
    });
}
listenForSetStreak();

Interceptor.attach(channelHookAddr, {
    onEnter() {
        const channel = this.context.rdx.toInt32();
        inHgChannelReal = channel === HG_CHANNEL_ID;
        if (inHgChannelReal) {
            hgSelf = this.context.rcx;
            armed = true;
            lastActivityMs = Date.now();
            setVisible(true);
        } else {
            setVisible(false);
        }
    },
});

Interceptor.attach(revealHookAddr, {
    onEnter() {
        if (!inHgChannelReal) return; // ignore reveals from any other TV channel's own script

        hgSelf = this.context.rdi;
        lastActivityMs = Date.now();
        setVisible(true); // undoes a false idle-timeout hide the instant play actually resumes

        const r10 = this.context.r10.toInt32();
        if (r10 !== STREAK_SLOT) return;

        if (armed) {
            armed = false;
            this.context.r9 = ptr(desiredStreak);
            send({ type: 'status', message: 'intercepted streak reset -- set to ' + desiredStreak });
        }
    },
});

setInterval(() => {
    if (inHgChannelReal && visible && Date.now() - lastActivityMs > IDLE_TIMEOUT_MS) {
        setVisible(false);
    }
}, IDLE_CHECK_INTERVAL_MS);

send({ type: 'status', message: 'harvest goddess streak hooks armed' });
