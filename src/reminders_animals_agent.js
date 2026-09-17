// Live poller backing the Reminders window's Animals section (app/reminders_live_data.py).
// Reuses the exact confirmed addressing/fields validated live 2026-09-12 (Calf, Cow, and Chicken
// byte-diff testing -- see PROGRESS.md): Name (+0x84, UTF32), Age in Days (+0x188), Daily
// Interaction bitflags (+0x18C: bit 0x10000 = Talked, bit 0x100 = Brushed), Affection Points
// (+0x190 -- NOT +0x188, a stale offset fixed in data/tables/animal_viewer.json the same day),
// Food State (+0x19C, bit 0 = fed today), Milked/Sheared flag (+0x1B4), Pregnant flag (+0x1C4) --
// all relative to each Coop/Barn slot's own base, same roster addressing animal_viewer_agent.js
// uses. The Horse gets its own equivalent fields at fixed offsets off SaveDataBase directly
// (data/pointer_map.md's Horse section for Name/Age/Affection; Daily Interaction at +0x378 is a
// new find from this same session, matching the animal convention exactly).
//
// 2026-09-13: also reads Days Fed (+0x198, feeds the stage-of-life calc) and the Species Sub-Type
// Flag (-0x17, Coop: 1=Chicken/0=Rabbit, Barn: 1=Cow/0=Sheep or Alpaca -- see data/pointer_map.md's
// "Animal Viewer (Live Data)" section for how this byte was confirmed). Both decoded in
// app/reminders_live_data.py, not here -- this agent only reads/forwards raw values.
//
// Also reads Affection Bonus (Breeding) (+0x194) -- animals start with a 5-heart cap, breeding
// adds +1 up to +5 (10 total) -- so the Animals table's Hearts column can show "X/Y" (current
// hearts / this animal's own cap) instead of just "X". The Horse has no breeding mechanic and no
// equivalent field, so it isn't read/sent for the horse -- app/reminders_live_data.py hardcodes
// its cap at 10 instead.
//
// 2026-09-15: also reads each animal's real, live Location -- confirmed at -0x14 off the same
// per-slot roster base (src/animal_live_viewer_agent.js's own confirmed field, same offset), and
// at +0x1D8 off SaveDataBase for the Horse (data/tables/memory_viewer.json's "Horse Location",
// immediately before the already-confirmed Horse X Position -- same "Location right before X/Y/
// Facing" layout the villager/Harvest Sprite/animal structs all share). Decoded via locationCalc,
// sent as `currentLocation` -- separate from the `roster` field ("Coop"/"Barn"), which still just
// identifies which physical roster/array this animal's OTHER fields are being read from and isn't
// meant to represent where the animal actually is right now (it previously did double duty as
// both, mislabeled "location" -- fixed to use the real field instead of a generic Coop/Barn/Horse
// label). ENUMS/locationCalc are injected globals (app/agent_loader.py).

const ANIMAL_RECORD_SIZE = 0x200;
const ANIMAL_ROSTER_OFFSET = 0x4580;
const ANIMAL_SLOT_COUNT = 8;
const BARN_ROSTER_OFFSET = 0x58c0;
const BARN_SLOT_COUNT = 16;

// Species Sub-Type Flag: confirmed 2026-09-13 across 2 individual Chickens, 1 Rabbit, 4 Cow color
// variants, 1 Sheep, and 1 Alpaca -- see data/pointer_map.md's "Animal Viewer (Live Data)"
// section. Coop: 1 = Chicken, 0 = Rabbit. Barn: 1 = Cow, 0 = Sheep/Alpaca (the two read
// identically on this byte -- both shown as "Sheep" for now).
const SPECIES_FLAG_OFFSET = -0x17;
const LOCATION_OFFSET = -0x14; // confirmed 2026-09-15, see src/animal_live_viewer_agent.js
const NAME_OFFSET = 0x84;
const NAME_LENGTH = 8; // utf32 chars
const AGE_OFFSET = 0x188;
const DAILY_INTERACTION_OFFSET = 0x18c;
const AFFECTION_OFFSET = 0x190;
const BREEDING_BONUS_OFFSET = 0x194; // "Affection Bonus (Breeding)" -- see data/pointer_map.md's Animal struct section
const DAYS_FED_OFFSET = 0x198; // "Days Fed (Produce Threshold)" -- also feeds the stage-of-life calc now
const FOOD_STATE_OFFSET = 0x19c;
const MILKED_SHEARED_OFFSET = 0x1b4;
const PREGNANT_OFFSET = 0x1c4;

// Horse fields are off SaveDataBase directly, not a per-slot roster (data/pointer_map.md's "Save
// Header -- Farm Name / Horse Name / Horse stats" section, plus this session's own new find).
const HORSE_LOCATION_OFFSET = 0x1d8; // confirmed 2026-09-15, see data/tables/memory_viewer.json's "Horse Location"
const HORSE_NAME_OFFSET = 0x1ec;
const HORSE_NAME_LENGTH = 10; // utf16 chars
const HORSE_AGE_OFFSET = 0x374;
const HORSE_DAILY_INTERACTION_OFFSET = 0x378;
const HORSE_AFFECTION_OFFSET = 0x37c;

const POLL_INTERVAL_MS = 500;

function readUtf32(base, offset, length) {
    let out = '';
    for (let i = 0; i < length; i++) {
        const code = base.add(offset + i * 4).readU32();
        if (code === 0) break;
        out += String.fromCharCode(code);
    }
    return out;
}

let saveDataBase = null;
let animalRosterBase = null;
let barnRosterBase = null;
const lastValues = {};
let pollTimer = null;

function slotBase(roster, slotIndex) {
    const rosterBase = roster === 'Coop' ? animalRosterBase : barnRosterBase;
    if (rosterBase === null) return null;
    return rosterBase.add(slotIndex * ANIMAL_RECORD_SIZE);
}

function pollSlot(roster, slotIndex) {
    const base = slotBase(roster, slotIndex);
    if (base === null) return;
    const key = roster.toLowerCase() + '-' + slotIndex;
    let locationRaw, name, ageDays, dailyInteraction, affection, breedingBonus, daysFed, foodState, milkedSheared, pregnant, speciesFlag;
    try {
        locationRaw = base.add(LOCATION_OFFSET).readS32();
        name = readUtf32(base, NAME_OFFSET, NAME_LENGTH);
        ageDays = base.add(AGE_OFFSET).readU32();
        dailyInteraction = base.add(DAILY_INTERACTION_OFFSET).readU32();
        affection = base.add(AFFECTION_OFFSET).readU32();
        breedingBonus = base.add(BREEDING_BONUS_OFFSET).readU32();
        daysFed = base.add(DAYS_FED_OFFSET).readU32();
        foodState = base.add(FOOD_STATE_OFFSET).readU16();
        milkedSheared = base.add(MILKED_SHEARED_OFFSET).readU32();
        pregnant = base.add(PREGNANT_OFFSET).readU32();
        speciesFlag = base.add(SPECIES_FLAG_OFFSET).readU8();
    } catch (e) {
        return;
    }
    const snapshot = [locationRaw, name, ageDays, dailyInteraction, affection, breedingBonus, daysFed, foodState,
        milkedSheared, pregnant, speciesFlag].join(':');
    if (lastValues[key] === snapshot) return;
    lastValues[key] = snapshot;
    send({
        // `roster` identifies which physical array (Coop/Barn) this animal's other fields come
        // from -- NOT where the animal currently is (that's `currentLocation` below). Renamed from
        // `location` to use the real field for Location, not a generic label.
        type: 'animal', key: key, roster: roster, currentLocation: locationCalc(locationRaw),
        name: name, ageDays: ageDays,
        dailyInteraction: dailyInteraction, affection: affection, breedingBonus: breedingBonus,
        daysFed: daysFed, foodState: foodState,
        milkedSheared: milkedSheared, pregnant: pregnant, speciesFlag: speciesFlag,
    });
}

function pollHorse() {
    if (saveDataBase === null) return;
    let locationRaw, name, ageDays, dailyInteraction, affection;
    try {
        locationRaw = saveDataBase.add(HORSE_LOCATION_OFFSET).readS32();
        name = saveDataBase.add(HORSE_NAME_OFFSET).readUtf16String(HORSE_NAME_LENGTH);
        ageDays = saveDataBase.add(HORSE_AGE_OFFSET).readU32();
        dailyInteraction = saveDataBase.add(HORSE_DAILY_INTERACTION_OFFSET).readU32();
        affection = saveDataBase.add(HORSE_AFFECTION_OFFSET).readU32();
    } catch (e) {
        return;
    }
    const snapshot = [locationRaw, name, ageDays, dailyInteraction, affection].join(':');
    if (lastValues.horse === snapshot) return;
    lastValues.horse = snapshot;
    send({
        type: 'horse', currentLocation: locationCalc(locationRaw), name: name, ageDays: ageDays,
        dailyInteraction: dailyInteraction, affection: affection,
    });
}

function poll() {
    for (let i = 0; i < ANIMAL_SLOT_COUNT; i++) pollSlot('Coop', i);
    for (let i = 0; i < BARN_SLOT_COUNT; i++) pollSlot('Barn', i);
    pollHorse();
}

function startPolling() {
    if (pollTimer === null) pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

function looksLikeSaveData(base) {
    try {
        const money = base.add(0xbc50).readU32();
        return money <= 99999999;
    } catch (e) {
        return false;
    }
}

function useSaveDataBase(base) {
    saveDataBase = base;
    animalRosterBase = base.add(ANIMAL_ROSTER_OFFSET);
    barnRosterBase = base.add(BARN_ROSTER_OFFSET);
    for (const key of Object.keys(lastValues)) delete lastValues[key]; // force a full resend
    startPolling();
    poll();
    send({ type: 'status', message: 'animals: save data base received ' + base.toString() });
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom every
// other listener in this project uses.
function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        const candidate = ptr(message.address);
        if (looksLikeSaveData(candidate)) useSaveDataBase(candidate);
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

send({ type: 'status', message: 'reminders animals agent loaded, waiting for Save Data Base' });
