// Tired Animations cheat: suppresses specific tired/exhausted reaction animations by zeroing the
// animation ID right before it's stored, whenever that ID is in the chosen disabled set. Off by
// default -- only loads while enabled in Preferences (see app/tired_animations_cheat.py).
//
// Derivation (confirmed live 2026-09-12 via manual call-stack/register tracing in Cheat Engine,
// starting from the Stamina/Fatigue getters and walking up through exe+5B0C0 (tool-swing result),
// exe+5AFE0 (applies the result + a Fatigue>=200 override), and exe+32CA00 (assigns all 7 tiers into
// a "reaction ID" field at [rbx+0x15C] based on Stamina/Fatigue thresholds) -- see
// data/pointer_map.md's Tired Animations section for the full derivation and tier->ID mapping
// (36/37/38/39 = Stamina below ~50%/20%/5%/0, 40/41 = Fatigue >= 100/160, 42 = Fatigue >= 200/pass
// out). Every tier funnels through exe+5AFE0's two "store the final animation ID" instructions:
//   exe+5B065  mov [rbp+A54],ax   ; stores whatever tier (0, or 36-41) was decided
//   exe+5B08A  mov [rbp+A54],ax   ; stores the hardcoded 42 (pass-out override)
// Hooking onEnter of each and zeroing RAX first (only when AX is one of the disabled IDs) prevents
// that specific tired reaction from ever being applied, without touching Stamina/Fatigue themselves,
// the game's own flag/hysteresis bookkeeping in exe+32CA00, or any normal animation ID (never in the
// 36-42 range).

const STORE_HOOK_OFFSETS = [0x5b065, 0x5b08a]; // STORY OF SEASONS Friends of Mineral Town.exe+5B065 / +5B08A

const moduleBase = Process.mainModule.base;

let disabledIds = new Set();

function listenForSetDisabled() {
    recv("setDisabledAnimations", (message) => {
        disabledIds = new Set(message.ids);
        listenForSetDisabled();
    });
}
listenForSetDisabled();

for (const offset of STORE_HOOK_OFFSETS) {
    Interceptor.attach(moduleBase.add(offset), {
        onEnter() {
            const animId = this.context.rax.and(0xffff).toInt32();
            if (disabledIds.has(animId)) {
                this.context.rax = ptr(0);
            }
        },
    });
}

send({ type: "status", message: "tired animations cheat hooks installed" });
