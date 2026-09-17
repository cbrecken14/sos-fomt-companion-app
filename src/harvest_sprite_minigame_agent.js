// Harvest Sprite Auto-Minigame "cheat": fast-forwards a Harvest Sprite's training minigame instead
// of playing it, by replicating the exact writes the game itself performs on a win (see
// data/pointer_map.md's "Harvest Sprite struct" section for the disassembly this is derived from
// -- exe+2F2F90). This is a write feature, only ever loaded while it's turned on via the Harvest
// Sprite Auto-Minigame overlay's "Enabled" checkbox (Overlay Settings tab) -- see
// app/overlays/harvest_sprite_minigame.py.
//
// Skill index mapping (confirmed live by watching a real Watering win -- matches the existing Task
// Assignment enum, NOT the order originally assumed): 0 = Harvest Crops/Harvesting, 1 = Watering,
// 2 = Animal Care.
//
// Per skill (3-byte arrays, index 0/1/2 = Harvest/Water/Animal):
//   Skill Level, offset 0x38 -- 0-255, the sprite's actual XP in that skill.
//   Streak,      offset 0x3B -- 0-31, consecutive wins. NOT reset on a loss, only stops growing.
// Points per round come from the resulting streak (after a win increments it, capped at 31):
//   0-5 -> 1, 6-10 -> 2, 11-16 -> 3, 17-31 -> 4. A win adds that many points to the skill level,
// clamped 0-255. The real training function also unconditionally sets a byte at 0x45 to 1 -- traced
// (via its own dedicated "has this sprite played today" checker function at exe+3650E4) and
// confirmed live to be exactly that flag, so it gates a second play the same day. This agent
// replicates that write too, so a cheated round leaves the sprite in the same state a real one
// would, including making it correctly un-playable again until the next day.
//
// SPRITES/TASK_NAMES are injected globals from data/tables/harvest_sprite_viewer.json (see
// app/agent_loader.py) -- same single source the Harvest Sprite Viewer tab uses, so a sprite's
// offset never needs a second hand-maintained copy.

const RELATIONSHIP_MANAGER_OFFSET = 0xcacc;
const PLAYER_SUBSTRUCT_OFFSET = 0xbce0;
const LOCATION_OFFSET = 0x340;
const HARVEST_SPRITE_HUT_LOCATION = 29;

const SKILL_LEVEL_BASE = 0x38;
const STREAK_BASE = 0x3b;
// Days Left is a single byte (exe+2F2F83: `mov [rcx+44],r8b`), NOT a 4-byte int -- reading it as
// U32 would also pull in PLAYED_TODAY_OFFSET's byte (0x45, the very next byte) as part of the same
// "integer", making Days Left read as 256 once the played-today flag is written.
const DAYS_LEFT_OFFSET = 0x44;
const PLAYED_TODAY_OFFSET = 0x45;
const MAX_STREAK = 31;
const MAX_SKILL = 255;

// Friendship -- 3 music notes' worth is required before a sprite will play the minigame at all.
// Same field the fallback tail of the training function (exe+2F300D) bumps by 1
// when called with an out-of-range skill index -- see data/pointer_map.md's "Harvest Sprite
// struct" section.
const FRIENDSHIP_OFFSET = 0x10;
const MIN_FRIENDSHIP_TO_PLAY = 75;

const POLL_INTERVAL_MS = 500;

function looksLikeSaveData(base) {
    try {
        const money = base.add(0xbc50).readU32();
        const stamina = base.add(PLAYER_SUBSTRUCT_OFFSET + 0x3b6).readU16();
        return money <= 99999999 && stamina <= 9999;
    } catch (e) {
        return false;
    }
}

function pointsForStreak(streak) {
    if (streak <= 5) return 1;
    if (streak <= 10) return 2;
    if (streak <= 16) return 3;
    return 4;
}

let saveDataBase = null;
let relationshipManagerBase = null;
let pollTimer = null;
let lastInHut = null;

function spriteRecord(spriteId) {
    if (relationshipManagerBase === null) return null;
    const sprite = SPRITES.find((s) => s.id === spriteId);
    return sprite ? relationshipManagerBase.add(sprite.offset) : null;
}

function readSpriteRow(sprite) {
    const record = relationshipManagerBase.add(sprite.offset);
    const daysLeft = record.add(DAYS_LEFT_OFFSET).readU8();
    const playedToday = record.add(PLAYED_TODAY_OFFSET).readU8() !== 0;
    const friendship = record.add(FRIENDSHIP_OFFSET).readU32();
    const skills = [0, 1, 2].map((i) => record.add(SKILL_LEVEL_BASE + i).readU8());
    return { id: sprite.id, name: sprite.name, daysLeft, playedToday, friendship, skills };
}

function pollLocation() {
    if (saveDataBase === null) return;
    let location;
    try {
        location = saveDataBase.add(PLAYER_SUBSTRUCT_OFFSET).add(LOCATION_OFFSET).readS32();
    } catch (e) {
        return;
    }
    const inHut = location === HARVEST_SPRITE_HUT_LOCATION;
    if (inHut !== lastInHut) {
        lastInHut = inHut;
        send({ type: 'locationUpdate', inHut });
    }
}

function pollSprites() {
    if (relationshipManagerBase === null) return;
    try {
        send({ type: 'spriteData', sprites: SPRITES.map(readSpriteRow) });
    } catch (e) {
        // Sprite data isn't resolvable yet (e.g. no save loaded) -- try again next tick.
    }
}

function poll() {
    pollLocation();
    pollSprites();
}

function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

function useSaveDataBase(base) {
    saveDataBase = base;
    relationshipManagerBase = base.add(RELATIONSHIP_MANAGER_OFFSET);
    lastInHut = null; // force a resend of the current in-hut state under the new base
    startPolling();
}

function listenForSetSaveDataBase() {
    recv('setSaveDataBase', (message) => {
        const candidate = ptr(message.address);
        if (looksLikeSaveData(candidate)) useSaveDataBase(candidate);
        listenForSetSaveDataBase();
    });
}
listenForSetSaveDataBase();

function completeMinigame(spriteId, skillIndex) {
    if (skillIndex < 0 || skillIndex > 2) {
        send({ type: 'status', message: 'completeMinigame: invalid skill index ' + skillIndex });
        return;
    }
    const record = spriteRecord(spriteId);
    if (record === null) {
        send({ type: 'status', message: 'completeMinigame: unknown sprite id ' + spriteId });
        return;
    }

    const daysLeft = record.add(DAYS_LEFT_OFFSET).readU8();
    const playedToday = record.add(PLAYED_TODAY_OFFSET).readU8();
    const friendship = record.add(FRIENDSHIP_OFFSET).readU32();
    if (daysLeft !== 0 || playedToday !== 0 || friendship < MIN_FRIENDSHIP_TO_PLAY) {
        send({ type: 'status', message: 'completeMinigame: sprite ' + spriteId + ' is not eligible right now' });
        return;
    }

    record.add(PLAYED_TODAY_OFFSET).writeU8(1);

    const streakAddr = record.add(STREAK_BASE + skillIndex);
    let streak = streakAddr.readU8();
    if (streak !== MAX_STREAK) {
        streak += 1;
        streakAddr.writeU8(streak);
    }

    const points = pointsForStreak(streak);
    const skillAddr = record.add(SKILL_LEVEL_BASE + skillIndex);
    const newSkill = Math.min(skillAddr.readU8() + points, MAX_SKILL);
    skillAddr.writeU8(newSkill);

    send({ type: 'minigameCompleted', id: spriteId, skillIndex, streak, pointsAwarded: points, newSkill });
    pollSprites();
}

function listenForCompleteMinigame() {
    recv('completeMinigame', (message) => {
        completeMinigame(message.id, message.skillIndex);
        listenForCompleteMinigame();
    });
}
listenForCompleteMinigame();

send({ type: 'status', message: 'waiting for Save Data Base from the shared session...' });
