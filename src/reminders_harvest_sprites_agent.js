// Live poller backing the Reminders window's Harvest Sprites section
// (app/reminders_live_data.py). Reuses the exact same confirmed addressing/fields Harvest Sprite
// Viewer and the Harvest Sprite Auto-Minigame cheat already use (data/pointer_map.md's "Harvest
// Sprite struct" section): each sprite's record sits at SaveDataBase + 0xCACC
// (RelationshipManagerBase) + a fixed per-sprite offset (SPRITES, injected from
// data/tables/harvest_sprite_viewer.json).
//
// Fields read: Location (+0x0, decoded via locationCalc -- same offset/concept confirmed for
// sprites 2026-09-15, see harvest_sprite_viewer_agent.js), Friendship (+0x10), Status (+0x18),
// Skill Levels (+0x38), Task Assignment (+0x40), Days Left (+0x44), Played Today (+0x45).
// Status/Skill decoding mirrors harvest_sprite_viewer_agent.js's decodeStatus/decodeSkills;
// Training Status wording mirrors app/overlays/harvest_sprite_minigame.py's eligibility text exactly (same
// MIN_FRIENDSHIP_TO_PLAY = 75) so this reads the same as the cheat's own management dialog. All
// the actual text/eligibility assembly happens on the Python side (app/reminders_live_data.py) --
// this script only sends the raw values, same split every other agent in this project uses.

const RELATIONSHIP_MANAGER_OFFSET = 0xcacc;
const LOCATION_OFFSET = 0x0;
const FRIENDSHIP_OFFSET = 0x10;
const STATUS_OFFSET = 0x18;
const SKILLS_OFFSET = 0x38;
const TASK_OFFSET = 0x40;
const DAYS_LEFT_OFFSET = 0x44;
const PLAYED_TODAY_OFFSET = 0x45;
const POLL_INTERVAL_MS = 500;

// SPRITES/TASK_NAMES are injected globals (data/tables/harvest_sprite_viewer.json via
// app/agent_loader.py) -- same source Harvest Sprite Viewer itself uses. ENUMS/locationCalc
// (data/tables/enums.json + src/enum_logic.js) are also injected the same way.

let relationshipManagerBase = null;
let pollTimer = null;
const lastValues = {};

function poll() {
    if (relationshipManagerBase === null) return;
    for (const sprite of SPRITES) {
        const base = relationshipManagerBase.add(sprite.offset);
        let locationRaw, friendship, statusRaw, skillsRaw, task, daysLeft, playedToday;
        try {
            locationRaw = base.add(LOCATION_OFFSET).readS32();
            friendship = base.add(FRIENDSHIP_OFFSET).readU32();
            statusRaw = base.add(STATUS_OFFSET).readU32();
            skillsRaw = base.add(SKILLS_OFFSET).readU32();
            task = base.add(TASK_OFFSET).readU32();
            daysLeft = base.add(DAYS_LEFT_OFFSET).readU8();
            playedToday = base.add(PLAYED_TODAY_OFFSET).readU8() !== 0;
        } catch (e) {
            continue;
        }
        const snapshot = [locationRaw, friendship, statusRaw, skillsRaw, task, daysLeft, playedToday].join(':');
        if (lastValues[sprite.id] === snapshot) continue;
        lastValues[sprite.id] = snapshot;
        send({
            type: 'sprite',
            id: sprite.id,
            name: sprite.name,
            location: locationCalc(locationRaw),
            friendship: friendship,
            statusRaw: statusRaw,
            skillsRaw: skillsRaw,
            task: task,
            daysLeft: daysLeft,
            playedToday: playedToday,
        });
    }
}

function startPolling() {
    if (pollTimer === null) pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

function useSaveDataBase(base) {
    relationshipManagerBase = base.add(RELATIONSHIP_MANAGER_OFFSET);
    for (const key of Object.keys(lastValues)) delete lastValues[key]; // force a full resend
    startPolling();
    poll();
    send({ type: 'status', message: 'harvest sprites: save data base received ' + base.toString() });
}

// Re-arms itself each time since recv() only fires once per registration -- same idiom
// src/memory_viewer_agent.js's listenForSetSaveDataBase uses.
function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        useSaveDataBase(ptr(message.address));
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

send({ type: 'status', message: 'reminders harvest sprite agent loaded, waiting for Save Data Base' });
