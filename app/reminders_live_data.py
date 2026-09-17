"""Live game-memory data for the Reminders window -- Header/Weather (src/reminders_time_agent.js),
Harvest Sprites (src/reminders_harvest_sprites_agent.js), Villager Reminders
(src/reminders_villagers_agent.js), Animals (src/reminders_animals_agent.js), and the Hidden
Counters (shop purchases, Harvest Goddess gifts, Kappa Cucumbers), Gotts' building-upgrade jobs, and
Saibara's tool-forge jobs.

Each class here is a app/reminders_data_source.py `RemindersDataSource` -- that base class owns the
attach/signal-wiring/message-envelope/cleanup boilerplate every one of these used to hand-carry a
copy of; what's left in each class below is just its own real logic: which payload kind(s) it
decodes, what state it keeps, and its own accessor methods. Exposes the same dataclass shapes
app/reminders_mock_data.py uses so app/windows/reminders.py can read from either source through an
identical interface. Sections move off mock data onto real memory one at a time this way; the rest
(most of Daily Reminders, Available Events' condition-checking) stay mocked until each gets its own
investigation session. Gotts' building-upgrade jobs (RemindersGottsData) and Saibara's tool-forge
jobs (RemindersForgeData) are both real now -- the old combined "Blacksmith/Carpenter" mock
placeholder they stood in for is retired.

Year: pointer_map.md's Time/Weather struct lists TWO raw fields (Year (x1), max 6; Year (x7), max
29). Confirmed live that Year (x1) alone IS the displayed year -- unlike Day, it's not 0-indexed,
so the original "+1" guess read one year ahead of the real one and has been removed. Year (x7)'s
role is still unconfirmed; presumably it only starts mattering once x1 wraps
past 6, which hasn't been reached/tested yet -- don't assume a combination formula is needed at
all until that's actually observed. Season is also only "predicted from layout" per
pointer_map.md, not independently confirmed like Weather/Day/Hour/Minute -- still worth a live
sanity check (does the displayed season match what's actually growing/visible in-game right now?).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .data_tables import load_table
from .reminders_data_source import TIME_BASE, RemindersDataSource
# Sharing the exact same threshold the Harvest Sprite Auto-Minigame cheat's own management dialog
# uses (rather than a second copy of the number 75), so Training Status reads consistently with
# the harvest sprite trainer.
from .overlays.harvest_sprite_minigame import MIN_FRIENDSHIP_TO_PLAY
from .reminders_mock_data import HarvestSpriteStatus, HeaderInfo, WeatherInfo

_SRC = Path(__file__).resolve().parent.parent / "src"
TIME_AGENT_PATH = _SRC / "reminders_time_agent.js"
HARVEST_SPRITES_AGENT_PATH = _SRC / "reminders_harvest_sprites_agent.js"
VILLAGERS_AGENT_PATH = _SRC / "reminders_villagers_agent.js"
ANIMALS_AGENT_PATH = _SRC / "reminders_animals_agent.js"
GOTTS_AGENT_PATH = _SRC / "reminders_gotts_agent.js"
FORGE_AGENT_PATH = _SRC / "reminders_forge_agent.js"
SLEEP_AGENT_PATH = _SRC / "reminders_sleep_agent.js"
SHOP_COUNTER_AGENT_PATH = _SRC / "reminders_shop_counter_agent.js"
HG_GIFTS_AGENT_PATH = _SRC / "reminders_harvest_goddess_gifts_agent.js"
KAPPA_CUCUMBERS_AGENT_PATH = _SRC / "reminders_kappa_cucumbers_agent.js"

SEASON_NAMES = {0: "Spring", 1: "Summer", 2: "Fall", 3: "Winter"}
WEATHER_NAMES = {0: "Sunny", 1: "Rainy", 2: "Snowy", 3: "Typhoon", 4: "Blizzard"}

_TASK_NAMES = load_table("harvest_sprite_viewer")["TASK_NAMES"]
_NOT_ASSIGNED_TASK = _TASK_NAMES.index("Not Assigned")

_ANIMAL_AFFECTION_HEARTS = load_table("animal_affection_hearts")["ANIMAL_AFFECTION_HEARTS"]
_TALKED_BIT = 0x10000
_BRUSHED_BIT = 0x100
_FED_TODAY_BIT = 0x1
# Food State's high byte -- confirmed in data/pointer_map.md's Animal struct section (same combined
# value as the "Animal Viewer (Live Data)" investigation's foodState decoder).
_FED_YESTERDAY_BIT = 0x100

# Species Sub-Type Flag (-0x17, see data/pointer_map.md's "Animal Viewer (Live Data)" section) --
# meaning depends on which roster the slot is in. Sheep and Alpaca read identically on this byte;
# both are shown as "Sheep" for now until something is found that tells them apart.
_SPECIES_FLAG_TO_NAME = {
    "Coop": {1: "Chicken", 0: "Rabbit"},
    "Barn": {1: "Cow", 0: "Sheep"},
}

# Animal growth stages. Each pair is (fed_days_threshold, age_days_threshold) -- an animal advances
# once EITHER is met.
_CHICK_TO_CHICKEN_AGE = 7
_RABBIT_BABY_THRESHOLDS = (10, 15)
_COW_BABY_TO_CALF_THRESHOLDS = (14, 20)
_COW_CALF_TO_ADULT_THRESHOLDS = (14 + 9, 20 + 14)
_SHEEP_BABY_THRESHOLDS = (14, 20)
_HORSE_ADULT_AGE = 90


def _hearts_for_affection(affection: int) -> int:
    # Same ascending-minimum lookup convention as friendship_notes.json/heart_levels.json: find
    # the largest tier whose min_affection is <= the value.
    hearts = 0
    for tier in _ANIMAL_AFFECTION_HEARTS:
        if affection >= tier["min_affection"]:
            hearts = tier["hearts"]
    return hearts


# Animals start with a 5-heart cap; breeding raises it by 1 per success, up to +5 (10 total) --
# data/pointer_map.md's Animal struct section, "Affection Bonus (Breeding)" (+0x194). The Hearts
# column shows "current/cap" rather than just the current count.
_ANIMAL_BASE_HEART_CAP = 5
# The Horse has no breeding mechanic and no equivalent field to read, so its cap is just a fixed
# display default rather than a real per-animal value.
_HORSE_HEART_CAP = 10


def _hearts_display(affection: int, breeding_bonus: int) -> str:
    return f"{_hearts_for_affection(affection)}/{_ANIMAL_BASE_HEART_CAP + breeding_bonus}"


def _horse_hearts_display(affection: int) -> str:
    return f"{_hearts_for_affection(affection)}/{_HORSE_HEART_CAP}"


def _yes_no(value: bool) -> str:
    return "Yes" if value else "No"


# Gotts' Activity/schedule state code (+0x24) reads this specific value both when idle AND right
# after a job is purchased but before construction visibly starts -- Days Remaining alone can't
# tell those two apart (it reads the same on both days), so this is the signal used instead. See
# data/pointer_map.md's "Gotts building upgrade job tracking" section.
_GOTTS_IDLE_ACTIVITY_STATE = 1

# Tool upgrade level names -- index = the raw level value (1-4) decoded from Saibara's Job/Catalog
# ID (see _decode_forge_job() below). Iron (0) is the starting level, never itself a purchase
# target. Cursed-tool upgrades (beyond Mythril) aren't understood yet, so this list stops at
# Mythril; an out-of-range level falls back to the raw number. "Golden" (the adjective form,
# matching data/tables/enums.json's TOOL item names, e.g. "Golden Hoe") rather than the bare "Gold"
# used for the standalone EXP thresholds -- this list is only ever combined with an item name for
# display (e.g. "Golden Hoe").
_TOOL_LEVEL_NAMES = ["Iron", "Copper", "Silver", "Golden", "Mythril"]

# Saibara's Job/Catalog ID (+0x38) blocks tool jobs as `toolIndex*5 + level` -- confirmed for
# Sickle/Hoe/Axe/Hammer (indices 0-3); Watering Can/Fishing Rod (4-5) inferred by the same order
# the TOOL item enum already uses, not yet independently confirmed live. See
# src/reminders_forge_agent.js's derivation notes.
_FORGE_TOOL_INDEX_NAMES = ["Sickle", "Hoe", "Axe", "Hammer", "Watering Can", "Fishing Rod"]


def _tool_level_name(level: int) -> str:
    if 0 <= level < len(_TOOL_LEVEL_NAMES):
        return _TOOL_LEVEL_NAMES[level]
    return str(level)


def _decode_forge_job(job_id: int) -> Optional[tuple[str, str]]:
    """Returns (item_name, level_name) for a nonzero Saibara Job/Catalog ID, or None for `0` (no
    active job). `level_name` is "" for a non-tool item (an upgrade level doesn't apply to those).
    IDs that don't cleanly decode as one of the 6 known tool blocks fall back to
    data/tables/enums.json's SAIBARA_CATALOG table, filled in item-by-item as each one is
    identified (same convention as the BUILDING enum), raw ID shown until then."""
    if job_id == 0:
        return None
    if 1 <= job_id <= 29:
        tool_index, level = divmod(job_id, 5)
        if level != 0 and tool_index < len(_FORGE_TOOL_INDEX_NAMES):
            return _FORGE_TOOL_INDEX_NAMES[tool_index], _tool_level_name(level)
    catalog = load_table("enums").get("SAIBARA_CATALOG", {})
    return catalog.get(str(job_id), str(job_id)), ""


def _building_name(building_id: int) -> str:
    # data/tables/enums.json's BUILDING table, filled in building-by-building as each ID is
    # identified live; until an ID is named, just show the raw number rather than guess.
    names = load_table("enums").get("BUILDING", {})
    return names.get(str(building_id), str(building_id))


def _matured(age_days: int, fed_days: int, thresholds: tuple[int, int]) -> bool:
    fed_threshold, age_threshold = thresholds
    return fed_days >= fed_threshold or age_days >= age_threshold


def _animal_type(species: str, age_days: int, fed_days: int) -> str:
    if species == "Chicken":
        return "Chicken" if age_days >= _CHICK_TO_CHICKEN_AGE else "Chick"
    if species == "Rabbit":
        return "Rabbit" if _matured(age_days, fed_days, _RABBIT_BABY_THRESHOLDS) else "Baby Rabbit"
    if species == "Cow":
        if not _matured(age_days, fed_days, _COW_BABY_TO_CALF_THRESHOLDS):
            return "Baby Cow"
        return "Adult Cow" if _matured(age_days, fed_days, _COW_CALF_TO_ADULT_THRESHOLDS) else "Calf"
    if species == "Sheep":
        return "Adult Sheep" if _matured(age_days, fed_days, _SHEEP_BABY_THRESHOLDS) else "Baby Sheep"
    if species == "Horse":
        return "Adult Horse" if age_days >= _HORSE_ADULT_AGE else "Little Horse"
    return species


@dataclass
class AnimalStatus:
    animal_type: str # e.g. "Adult Cow", "Chick" -- see data/pointer_map.md's Animal growth stages
    name: str
    location: str # decoded Location (e.g. "Barn", "Mother's Hill") -- 2026-09-15, real per-animal
    # field (-0x14 off the roster slot, +0x1D8 off SaveDataBase for the Horse), not the roster
    # (Coop/Barn) it's stored in -- see reminders_animals_agent.js's own note on that distinction.
    hearts: str # "current/cap", e.g. "3/6" -- see _hearts_display()/_horse_hearts_display()
    friendship_points: int # raw Affection Points, shown alongside the derived Hearts
    age: int # raw Age in Days
    fed_today: str
    talked: str
    brushed: str
    milked_sheared: str
    pregnant: str


@dataclass
class GottsJobStatus:
    building_name: str # resolved via _building_name() -- a real name, or the raw ID as a string
    building_id: int
    days_remaining: int
    started: bool # False = purchased but construction hasn't visibly started yet (see below)


@dataclass
class SleepStatus:
    stamina: int
    fatigue: int # halved from the raw stored value -- see RemindersSleepData._on_payload
    power_berries: int


@dataclass
class ForgeJobStatus:
    # Unlike GottsJobStatus, no `started` flag -- Days Remaining already reads the full total on
    # the purchase day itself (confirmed 2026-09-13), so app/windows/reminders.py's Daily Reminders
    # line uses the same countdown wording from day one through pickup, no separate "will start
    # Tomorrow" case needed.
    item_name: str # e.g. "Hoe", "Necklace", "Cheese Maker" -- see _decode_forge_job()
    level_name: str # e.g. "Golden" for a tool upgrade; "" for a non-tool item (no level applies)
    days_remaining: int


def _decode_status_bools(raw: int) -> tuple[bool, bool]:
    talked = (raw & 0xFF) != 0 or ((raw >> 8) & 0xFF) != 0
    gifted = ((raw >> 16) & 0xFF) != 0
    return talked, gifted


def _decode_exp_text(raw: int) -> str:
    harvest, water, animal = raw & 0xFF, (raw >> 8) & 0xFF, (raw >> 16) & 0xFF
    return f"Harvest {harvest} / Water {water} / Animal {animal}"


def _training_status_text(days_left: int, friendship: int, played_today: bool) -> str:
    # Same wording as app/overlays/harvest_sprite_minigame.py's _on_sprite_data eligibility text.
    if days_left != 0:
        return f"Working ({days_left} day(s) left)"
    if friendship < MIN_FRIENDSHIP_TO_PLAY:
        return f"Not friendly enough ({friendship}/{MIN_FRIENDSHIP_TO_PLAY})"
    if played_today:
        return "Already played today"
    return "Ready"


class RemindersLiveData(RemindersDataSource):
    AGENT_PATH = TIME_AGENT_PATH
    LOG_NAME = "reminders live data"
    BASE = TIME_BASE

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: Optional[dict] = None

    def _on_payload(self, kind, payload) -> None:
        if kind == "time":
            self._values = payload["values"]
            self.updated.emit()

    def is_ready(self) -> bool:
        return self._values is not None

    def header(self) -> HeaderInfo:
        v = self._values
        return HeaderInfo(
            # Confirmed live: the original "+1" placeholder read one year
            # ahead of the actual in-game year -- Year (x1), unlike Day, is NOT 0-indexed, it's
            # the displayed year directly. Year (x7)'s role is still unconfirmed (presumably only
            # matters once x1 wraps past its max of 6, which hasn't been reached/tested yet).
            year=v["yearX1"],
            season=SEASON_NAMES.get(v["season"], "Spring"),
            day=v["day"] + 1, # confirmed 0-indexed (data/pointer_map.md)
            hour=v["hour"],
            minute=v["minute"],
        )

    def weather(self) -> WeatherInfo:
        v = self._values
        return WeatherInfo(
            today=WEATHER_NAMES.get(v["weather"], "Sunny"),
            tomorrow=WEATHER_NAMES.get(v["nextWeather"], "Sunny"),
        )


class RemindersHarvestSpriteData(RemindersDataSource):
    """Reuses the exact addressing/fields Harvest Sprite Viewer and the Harvest Sprite
    Auto-Minigame cheat already confirmed live (Friendship, Status, Skill Levels, Task Assignment,
    Days Left, Played Today), plus Location (+0x0, confirmed for sprites 2026-09-15, same as
    villagers) -- see src/reminders_harvest_sprites_agent.js. Attaches on Save Data Base
    (RelationshipManagerBase = SaveDataBase + 0xCACC).
    """

    AGENT_PATH = HARVEST_SPRITES_AGENT_PATH
    AGENT_TABLES = ("harvest_sprite_viewer", "enums")
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sprites: dict[int, HarvestSpriteStatus] = {}

    def _on_payload(self, kind, payload) -> None:
        if kind != "sprite":
            return
        task = payload["task"]
        days_left = payload["daysLeft"]
        working = task != _NOT_ASSIGNED_TASK
        talked, gifted = _decode_status_bools(payload["statusRaw"])
        self._sprites[payload["id"]] = HarvestSpriteStatus(
            name=payload["name"],
            working=working,
            task=_TASK_NAMES[task] if working and task < len(_TASK_NAMES) else "-",
            days_left=days_left if working else 0,
            training_status=_training_status_text(
                days_left, payload["friendship"], payload["playedToday"]
            ),
            exp_text=_decode_exp_text(payload["skillsRaw"]),
            friendship=payload["friendship"],
            talked=talked,
            gifted=gifted,
            location=payload["location"],
        )
        self.updated.emit()

    def is_ready(self) -> bool:
        return len(self._sprites) > 0

    def sprites(self) -> list[HarvestSpriteStatus]:
        return [self._sprites[sprite_id] for sprite_id in sorted(self._sprites)]


class RemindersVillagerData(RemindersDataSource):
    """Per-villager Talked To Today / Gift Given Today flags -- confirmed generic to every
    villager, including the Harvest Goddess, same Status field (+0x18) previously only documented for
    Harvest Sprites. Also carries Friendship (raw FP), Love Points (raw LP), live Location
    (2026-09-14, for the Villagers/Marriage Candidates status tables -- app/windows/reminders.py)
    -- same per-villager addressing, no new confirmation needed -- and Heart Event Triggered plus
    Met At Least Once (+0x1B, the Status field's 4th packed byte; both added 2026-09-17, backing
    the Reminders window's real Heart Events section -- Met backs its "already met X" prerequisite
    gates). See src/reminders_villagers_agent.js. Attaches on Save Data Base.
    """

    AGENT_PATH = VILLAGERS_AGENT_PATH
    AGENT_TABLES = ("character_viewer", "villagers", "enums")
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._villagers: dict[str, dict] = {} # name -> {"talked": bool, "gifted": bool,...}

    def _on_payload(self, kind, payload) -> None:
        if kind != "villager":
            return
        self._villagers[payload["name"]] = {
            "talked": payload["talked"],
            "gifted": payload["gifted"],
            "met": payload["met"],
            "friendship": payload["friendship"],
            "love_points": payload["lovePoints"],
            "location": payload["location"],
            "heart_event_triggered": payload["heartEventTriggered"],
        }
        self.updated.emit()

    def is_ready(self) -> bool:
        return len(self._villagers) > 0

    def status_for(self, name: str) -> Optional[dict]:
        return self._villagers.get(name)


class RemindersAnimalData(RemindersDataSource):
    """Horse + every Coop/Barn animal's Fed/Talked/Brushed/Milked-Sheared/Pregnant status and
    Affection-derived heart count -- see src/reminders_animals_agent.js. All fields confirmed live
    2026-09-12 (Calf/Cow/Chicken byte-diff testing, see PROGRESS.md). Attaches on Save Data Base.
    """

    AGENT_PATH = ANIMALS_AGENT_PATH
    AGENT_TABLES = ("enums",)
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animals: dict[str, AnimalStatus] = {}

    def _on_payload(self, kind, payload) -> None:
        if kind == "animal":
            name = payload["name"].strip()
            if not name:
                self._animals.pop(payload["key"], None) # empty slot -- don't show a blank row
                self.updated.emit()
                return
            # `roster` (Coop/Barn) identifies which physical array/offsets this animal's OTHER
            # fields came from -- species/brushed logic below still keys off this, not the real
            # Location (an animal's actual Location can be somewhere else entirely, e.g. out to
            # pasture, while its record still lives in the Coop/Barn roster array either way).
            roster = payload["roster"]
            daily = payload["dailyInteraction"]
            food_state = payload["foodState"]
            age_days = payload["ageDays"]
            fed_days = payload["daysFed"]
            pregnant = bool(payload["pregnant"])
            species = _SPECIES_FLAG_TO_NAME[roster][int(bool(payload["speciesFlag"]))]

            # Milked/Sheared is species-dependent (see data/pointer_map.md's
            # Animal struct section). Chicken never produces either. Cow needs Fed Yesterday AND
            # not Pregnant. Rabbit/Sheep go straight off the raw flag -- neither depends on being
            # fed the day before.
            if species == "Chicken":
                milked_sheared = "N/A"
            elif species == "Cow":
                if pregnant:
                    milked_sheared = "Pregnant"
                elif not (food_state & _FED_YESTERDAY_BIT):
                    milked_sheared = "Not Fed Yesterday"
                else:
                    milked_sheared = _yes_no(bool(payload["milkedSheared"]))
            else: # Rabbit, Sheep
                milked_sheared = _yes_no(bool(payload["milkedSheared"]))

            self._animals[payload["key"]] = AnimalStatus(
                animal_type=_animal_type(species, age_days, fed_days),
                name=name,
                location=payload["currentLocation"] or "-",
                hearts=_hearts_display(payload["affection"], payload["breedingBonus"]),
                friendship_points=payload["affection"],
                age=age_days,
                fed_today=_yes_no(bool(food_state & _FED_TODAY_BIT)),
                talked=_yes_no(bool(daily & _TALKED_BIT)),
                # Coop animals (Chicken/Rabbit) can't be brushed.
                brushed="N/A" if roster == "Coop" else _yes_no(bool(daily & _BRUSHED_BIT)),
                milked_sheared=milked_sheared,
                pregnant=_yes_no(pregnant),
            )
            self.updated.emit()
        elif kind == "horse":
            daily = payload["dailyInteraction"]
            age_days = payload["ageDays"]
            self._animals["horse"] = AnimalStatus(
                animal_type=_animal_type("Horse", age_days, 0),
                name=payload["name"].strip() or "Horse",
                location=payload["currentLocation"] or "-",
                hearts=_horse_hearts_display(payload["affection"]),
                friendship_points=payload["affection"],
                age=age_days,
                fed_today="N/A", # no confirmed feeding mechanic/field for the horse
                talked=_yes_no(bool(daily & _TALKED_BIT)),
                brushed=_yes_no(bool(daily & _BRUSHED_BIT)),
                milked_sheared="N/A",
                pregnant="N/A",
            )
            self.updated.emit()

    def is_ready(self) -> bool:
        return len(self._animals) > 0

    def animals(self) -> list[AnimalStatus]:
        # Horse first, then Coop slots in order, then Barn slots in order.
        order = ["horse"] + [f"coop-{i}" for i in range(8)] + [f"barn-{i}" for i in range(16)]
        return [self._animals[key] for key in order if key in self._animals]


class RemindersShopCounterData(RemindersDataSource):
    """Van's Favorite shop-purchase counter for the Hidden Counters section -- see
    src/reminders_shop_counter_agent.js and data/pointer_map.md's "Shop Purchased Item Counter
    (Van's Favorite mail reward)" section. Reads SaveDataBase + 0xE65C directly (NOT nested under
    RelationshipManagerBase like most other Save Data fields -- see that pointer_map.md section for
    why). Attaches on Save Data Base.
    """

    AGENT_PATH = SHOP_COUNTER_AGENT_PATH
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value: Optional[int] = None

    def _on_payload(self, kind, payload) -> None:
        if kind == "shopCounter":
            self._value = payload["value"]
            self.updated.emit()

    def is_ready(self) -> bool:
        return self._value is not None

    def value(self) -> Optional[int]:
        return self._value


class RemindersHarvestGoddessGiftsData(RemindersDataSource):
    """The Harvest Goddess's two separate gift counters for the Hidden Counters section -- see
    src/reminders_harvest_goddess_gifts_agent.js and data/pointer_map.md's "Harvest Goddess Gift
    Counters" section. Total Gifts (SaveDataBase + 0xE664) is cumulative and never resets; 10th
    Gift (SaveDataBase + 0xE60C) counts to 10 and resets -- these were originally logged as one
    duplicated field before being corrected into two separate ones. Attaches on Save Data Base.
    """

    AGENT_PATH = HG_GIFTS_AGENT_PATH
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total: Optional[int] = None
        self._tenth: Optional[int] = None

    def _on_payload(self, kind, payload) -> None:
        if kind == "hgGifts":
            self._total = payload["total"]
            self._tenth = payload["tenth"]
            self.updated.emit()

    def is_ready(self) -> bool:
        return self._total is not None

    def total(self) -> Optional[int]:
        return self._total

    def tenth(self) -> Optional[int]:
        return self._tenth


class RemindersKappaCucumbersData(RemindersDataSource):
    """The Kappa Cucumbers counter for the Hidden Counters section -- see
    src/reminders_kappa_cucumbers_agent.js and data/pointer_map.md's "Kappa Cucumbers Counter
    (Blue Power Berry reward)" section. Reads SaveDataBase + 0xE614 (the counter itself, which
    resets to 0 once the reward is given) and PlayerStructBase + 0x3B2 (the permanent "Blue Power
    Berry Obtained" flag that distinguishes an unearned 0 from an already-claimed one). Attaches
    on Save Data Base.
    """

    AGENT_PATH = KAPPA_CUCUMBERS_AGENT_PATH
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value: Optional[int] = None
        self._obtained: Optional[bool] = None

    def _on_payload(self, kind, payload) -> None:
        if kind == "kappaCucumbers":
            self._value = payload["value"]
            self._obtained = payload["obtained"]
            self.updated.emit()

    def is_ready(self) -> bool:
        return self._value is not None

    def value(self) -> Optional[int]:
        return self._value

    def obtained(self) -> Optional[bool]:
        return self._obtained


class RemindersGottsData(RemindersDataSource):
    """Gotts' building-upgrade job (Job Type/Building ID + Days Remaining) -- see
    src/reminders_gotts_agent.js and data/pointer_map.md's "Gotts building upgrade job tracking"
    section. Attaches on Save Data Base.
    """

    AGENT_PATH = GOTTS_AGENT_PATH
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._attached = False # distinguishes "not attached yet" from "attached, no active job"
        self._job: Optional[GottsJobStatus] = None

    def _on_payload(self, kind, payload) -> None:
        if kind != "gotts":
            return
        self._attached = True
        building_id = payload["jobType"]
        if building_id == 0:
            self._job = None
        else:
            self._job = GottsJobStatus(
                building_name=_building_name(building_id),
                building_id=building_id,
                days_remaining=payload["daysRemaining"],
                started=payload["activityState"] != _GOTTS_IDLE_ACTIVITY_STATE,
            )
        self.updated.emit()

    def is_ready(self) -> bool:
        return self._attached

    def job(self) -> Optional[GottsJobStatus]:
        return self._job


class RemindersForgeData(RemindersDataSource):
    """Saibara's tool-forge job (which tool/level is queued + Days Remaining) -- see
    src/reminders_forge_agent.js and data/pointer_map.md for the derivation. Attaches on Save Data
    Base, same pattern as RemindersGottsData.
    """

    AGENT_PATH = FORGE_AGENT_PATH
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._attached = False
        self._job: Optional[ForgeJobStatus] = None

    def _on_payload(self, kind, payload) -> None:
        if kind != "forge":
            return
        self._attached = True
        decoded = _decode_forge_job(payload["jobId"])
        if decoded is None:
            self._job = None
        else:
            item_name, level_name = decoded
            self._job = ForgeJobStatus(
                item_name=item_name,
                level_name=level_name,
                days_remaining=payload["daysRemaining"],
            )
        self.updated.emit()

    def is_ready(self) -> bool:
        return self._attached

    def job(self) -> Optional[ForgeJobStatus]:
        return self._job


class RemindersSleepData(RemindersDataSource):
    """Stamina/Fatigue/Power Berries Found -- feeds app/sleep_recovery.py's bedtime-recovery
    reminder (app/windows/reminders.py's header line). See src/reminders_sleep_agent.js and
    data/pointer_map.md's Player-state struct section (+0x3B6/+0x3B8/+0xC090). Attaches on Save
    Data Base.
    """

    AGENT_PATH = SLEEP_AGENT_PATH
    LOG_NAME = "reminders live data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status: Optional[SleepStatus] = None

    def _on_payload(self, kind, payload) -> None:
        if kind != "sleepStatus":
            return
        # The game's own getter (exe+2F06C0) halves the raw stored Fatigue value before using
        # it for the Tired Animation thresholds -- same halving src/fatigue_value_agent.js's
        # overlay applies, kept consistent here since data/tables/sleep_recovery.json's formula
        # is calibrated against that same 0-100ish range, not the raw stored number.
        self._status = SleepStatus(
            stamina=payload["stamina"],
            fatigue=payload["fatigueRaw"] // 2,
            power_berries=payload["powerBerries"],
        )
        self.updated.emit()

    def is_ready(self) -> bool:
        return self._status is not None

    def status_now(self) -> Optional[SleepStatus]:
        return self._status
