// Coin Spawn Count cheat: overrides how many coins a mined coin-bearing mine tile spawns, forcing
// a chosen fixed count instead of the game's natural roll. Off by default -- only loads while
// enabled in Preferences (see app/coin_spawn_cheat.py).
//
// Derivation (confirmed live via manual call-stack/register tracing in Cheat Engine, starting from
// the tile-flip instruction exe+2E650C and walking the call chain through
// exe+2E6400 -> exe+317778 (the till-handling function) -> exe+5C080 (the coin-object spawner,
// itself called once per till) -> exe+5C280, a per-reward-type dispatcher keyed by item ID. The
// coin case (item ID 420) rolls ucrtbase.rand(), reduces it to a signed value in 0-15 via the
// `and eax,0x8000000F` / sign-correction idiom, then adds 5 -- giving the natural range of 5-20
// coins. That result becomes a loop counter; a nearby loop calls exe+270520 (the per-coin spawn
// call) exactly that many times. Verified predictively live: RDI read at this hook, before the
// spawn loop even started, correctly predicted the exact visible coin count across multiple tills
// (10 predicted -> 10 coins spawned). See data/pointer_map.md's mine coin spawn roll section.
//
// Hooking onEnter of the `test edi,edi` instruction right after the roll is fully computed (but
// before it's used as the loop counter) lets us read the natural roll for the debug message, then
// overwrite it with the chosen fixed count -- without touching the RNG call itself, the item ID,
// or the spawn loop/call that follows.

const COIN_ROLL_HOOK_OFFSET = 0x5c3bc; // STORY OF SEASONS Friends of Mineral Town.exe+5C3BC

const moduleBase = Process.mainModule.base;

let overrideCount = 20;

function listenForSetCount() {
    recv('setCoinCount', (message) => {
        overrideCount = message.count;
        listenForSetCount();
    });
}
listenForSetCount();

Interceptor.attach(moduleBase.add(COIN_ROLL_HOOK_OFFSET), {
    onEnter() {
        const naturalCount = this.context.rdi.toInt32();
        this.context.rdi = ptr(overrideCount);
        // Debug text -- commented out; re-enable if the override ever needs to be verified live
        // again.
        // send({
        //     type: 'status',
        //     message: `Coin spawn would have been ${naturalCount}, overridden to ${overrideCount}`,
        // });
    },
});

send({ type: 'status', message: 'coin spawn count cheat hook installed' });
