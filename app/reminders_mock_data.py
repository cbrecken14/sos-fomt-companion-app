"""Remaining FAKE DATA for the Reminders window.

Nothing in this file reads real game memory. What's left here is placeholder content for
sections that don't have a real memory-reading source yet (Available Events' "Harvest Goddess
Prize Available" sample row, the standalone "Fluffy Festival" Future Events demo entry, and
daily_reminders()'s now-empty catalog -- kept as a passthrough shape, see
app/windows/reminders.py's own comment on it) -- these are unrelated to whether a save is loaded,
so they aren't gated behind Save Data Base readiness the way real sections are. Available Events'
Heart Event rows used to be mocked here too (a random sample of data/tables/heart_events.json);
replaced 2026-09-17 by real ones (app/windows/reminders.py's `_heart_event_entries()`), so this
file no longer touches that table at all.

Header/Weather/Harvest Sprites are NOT mocked any more -- those three now only ever show real
data, or nothing at all, gated by
app/windows/reminders.py's _rebuild()). HeaderInfo/WeatherInfo/HarvestSpriteStatus stay defined
here as the shared data shapes app/reminders_live_data.py's real classes also use -- an internal
`_state["header"]` is still rolled once below purely to give the leftover Future Events demo entry
a date to offset from, not exposed as a public header()/weather() accessor any more.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from . import game_calendar

SEASONS = ["Spring", "Summer", "Fall", "Winter"]


@dataclass
class HeaderInfo:
    year: int
    season: str
    day: int
    hour: int
    minute: int


@dataclass
class WeatherInfo:
    today: str
    tomorrow: str


@dataclass
class HarvestSpriteStatus:
    name: str
    working: bool
    task: str
    days_left: int
    training_status: str # same wording as the Harvest Sprite Auto-Minigame cheat's Status column
    exp_text: str # "Harvest X / Water Y / Animal Z"
    friendship: int = 0 # raw FP (+0x10), same field/scale as villagers -- Reminders' Friendship/FP columns (2026-09-15)
    # Talked To Today / Gift Given Today (2026-09-15) -- replaced the old combined status_text
    # ("Talked, No gift") once the Reminders Harvest Sprites table split them into separate
    # Yes/No columns, matching the Villagers/Marriage Candidates tables' convention.
    talked: bool = False
    gifted: bool = False
    location: str | None = None # decoded Location (+0x0), same as villagers -- 2026-09-15


@dataclass
class DailyReminder:
    # `type_key` cross-references a reminder *type* in reminders_settings.py's catalog, so the
    # settings dialog's per-type toggles (enabled / show-in-future) apply to it.
    type_key: str
    text: str
    done: bool


@dataclass
class AvailableEvent:
    name: str
    time: str
    location: str
    condition_note: str
    url: Optional[str] = None


@dataclass
class FutureEvent:
    year: int
    season: str
    day: int
    type_key: Optional[str]
    text: str


_state = {}


def _roll() -> None:
    # Only used internally, to give the Future Events demo entry below a date to offset from --
    # not exposed as a public header() accessor any more (2026-09-15).
    _state["header"] = HeaderInfo(
        year=1, season=random.choice(SEASONS), day=random.randint(1, 30),
        hour=random.randint(6, 23), minute=random.choice([0, 15, 30, 45]),
    )

    # "hg_gift" removed -- the Harvest Goddess moved into the real,
    # non-mocked Villager Reminders section, since her gift/talk tracking is now confirmed live.
    # "feed_animals"/"animal_care" removed the same day -- replaced by the real, non-mocked
    # Animals section (Fed/Talked/Brushed/Milked/Pregnant per animal). "water_crops" removed
    # 2026-09-13 -- replaced by the real, non-mocked Crop Status section.
    daily_catalog = []
    _state["daily_reminders"] = [
        DailyReminder(type_key=key, text=text, done=random.random() > 0.5)
        for key, text in daily_catalog
    ]

    _state["available_events"] = random.sample([
        AvailableEvent("Harvest Goddess Prize Available", "Anytime", "Goddess Pond",
                        "Win streak of 5 or more banked"),
    ], k=random.randint(0, 1))

    future = []
    h = _state["header"]
    # Offsets go past 14 on purpose (up to 20) so the Reminders window's 14-day future cutoff has
    # something to actually filter out, not just an empty case.
    # "crop_harvest" mock entries removed 2026-09-13 -- replaced by real predictions
    # (RemindersCropData, see app/windows/reminders.py's _build_future_events_section).
    for offset in range(1, 21):
        year, season, day = game_calendar.add_days(h.year, h.season, h.day, offset)
        if offset == 10:
            future.append(FutureEvent(year, season, day, None,
                                       "Fluffy Festival -- shear competing sheep by today"))
    _state["future_events"] = future


def daily_reminders() -> list[DailyReminder]:
    return _state["daily_reminders"]


def available_events() -> list[AvailableEvent]:
    return _state["available_events"]


def future_events() -> list[FutureEvent]:
    return _state["future_events"]


_roll()
