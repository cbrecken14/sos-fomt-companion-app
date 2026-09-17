// Guaranteed Ground Item Spawn cheat: forces every candidate in an area's daily ground-item spawn
// roll to succeed, without changing which item gets picked or touching any of the game's own
// eligibility checks. This is a write feature, only ever loaded while enabled in Preferences (see
// app/guaranteed_item_spawn_cheat.py).
//
// Derivation (confirmed live via manual call-stack/register tracing in Cheat Engine, starting from
// the area-transition location-setter hook and walking up the call stack through the per-category
// "already spawned today" gate and its 10-candidate spawn loop -- see data/pointer_map.md's
// "Ground item spawn roll" section for the full trail). Each area keeps a per-day-reset list of
// candidate items; for each candidate, the game rolls a pseudo-random percentage (0-99) and
// compares it against that candidate's own spawn-chance threshold (a 16-bit field in its static
// definition, confirmed live: Wood Branch -- which always spawns with no RNG involved -- behaves
// exactly as this threshold being 100 would predict). The comparison, in the function that owns
// the whole per-category candidate loop:
//   exe+2F9432  shr rcx, 0x20        (rcx = the rolled 0-99 percentage)
//   exe+2F9436  sub ecx, eax         (ecx -= this candidate's threshold)
//   exe+2F9438  test ecx, ecx
//   exe+2F943A  jg <skip this candidate for today>
// Hooking onEnter of the `sub ecx,eax` instruction and zeroing ECX first guarantees the subtraction
// result is <= 0 (thresholds are never negative), so the `jg` never fires and the candidate always
// proceeds to spawn -- without touching the threshold value, the variant ultimately picked, or any
// of the eligibility checks earlier in the same function. A guaranteed spawn, not a changed drop
// table.

const SPAWN_ROLL_HOOK_OFFSET = 0x2f9436; // STORY OF SEASONS Friends of Mineral Town.exe+2F9436

const moduleBase = Process.mainModule.base;

Interceptor.attach(moduleBase.add(SPAWN_ROLL_HOOK_OFFSET), {
    onEnter() {
        this.context.rcx = ptr(0);
    },
});

send({ type: 'status', message: 'guaranteed ground item spawn hook installed' });
