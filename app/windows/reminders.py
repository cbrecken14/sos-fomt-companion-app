"""Reminders window.

Header/Weather and Harvest Sprite Schedule read real game memory via app/reminders_live_data.py
(the shared session's Time Base and Save Data Base respectively). Daily Reminders, Available
Events' condition-checking, and the crop-harvest/blacksmith rows inside Future Events are fed by
app/reminders_mock_data.py where a live source isn't wired up yet -- see that module's docstring.
Player Notes is pure local storage, not game-memory-dependent (app/player_notes_store.py). Every
section that needs "today's date" (Festivals, Villager Birthdays, Shop Info, Festival Reminders,
Available Events' Other-Events matching, Future Events' date window) goes through
_current_header()/_current_weather() below, which use the live reading once it's ready and fall
back to the mock reading until then -- so the whole window is internally consistent about what
day it thinks "today" is, whichever source that comes from. Same idea for Harvest Sprite
Schedule's _current_harvest_sprites().

One scrollable column, sections top to bottom per the spec: header/time, weather, festivals,
villager birthdays, shop info, today's player notes, today's festival-prep reminders, harvest
sprite schedule, daily reminders, available events (Heart-Event-style mock entries plus real
matches from data/tables/other_events.json), future events. Each section (other than
header/weather, always shown) hides itself when disabled in Settings or when it has nothing to
show.

Weekday display throughout this window relies on app/game_calendar.py's weekday convention -- see
that module's docstring.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config
from .. import event_settings
from .. import game_calendar
from .. import reminders_mock_data as mock
from .. import reminders_settings as settings
from .. import sleep_recovery
from ..data_tables import load_table
from ..event_settings import EventSettingsDialog
from ..game_session import get_shared_session
from ..player_notes_dialog import PlayerNotesDialog
from ..player_notes_store import get_note
from ..reminders_crop_data import RemindersCropData
from ..reminders_mock_data import HeaderInfo, WeatherInfo
from ..reminders_live_data import (
    RemindersAnimalData,
    RemindersForgeData,
    RemindersGottsData,
    RemindersHarvestGoddessGiftsData,
    RemindersHarvestSpriteData,
    RemindersKappaCucumbersData,
    RemindersLiveData,
    RemindersShopCounterData,
    RemindersSleepData,
    RemindersVillagerData,
)
from ..reminders_settings import RemindersSettingsDialog
from ..widgets import CopyableTableWidget
from . import WindowSpec, register

_FESTIVALS = load_table("festivals")["FESTIVALS"]
_SHOPS = load_table("shops")["SHOPS"]
_VILLAGERS = load_table("villagers")["VILLAGERS"]
_OTHER_EVENTS = load_table("other_events")["OTHER_EVENTS"]
_HG_OFFERINGS = load_table("harvest_goddess_offerings")["HARVEST_GODDESS_OFFERINGS"]
_HG_TENTH_GIFT_ITEM = "White Grass"
_MARRIAGE_CANDIDATE_IDS = set(load_table("character_viewer")["MARRIAGE_CANDIDATE_IDS"])
_FRIENDSHIP_NOTES = load_table("friendship_notes")["FRIENDSHIP_NOTES"]
_HEART_EVENTS = load_table("heart_events")["HEART_EVENTS"]
_HEART_LEVELS = load_table("heart_levels")["HEART_LEVELS"]
# Villager's own wiki page + loved-gift data, keyed by their PLAIN name (Harvest Sprites' own
# villagers.json entries are named e.g. "Aqua (Harvest Sprite)", but the Harvest Sprite Schedule's
# live data only carries the plain "Aqua") -- lets the status tables' "Favorite Gifts"/hyperlinked
# name columns share one lookup for both regular villagers and sprites (2026-09-15).
_VILLAGER_BY_PLAIN_NAME = {v["name"].replace(" (Harvest Sprite)", ""): v for v in _VILLAGERS}

# data/tables/animals.json's own wiki page per species, for the Animals table's "Animal" column
# hyperlink (2026-09-15). Keyed by the SAME species word _animal_type() (reminders_live_data.py)
# returns, e.g. "Adult Cow"/"Calf"/"Baby Cow" all map to the one "Cow" wiki page -- the memory
# struct doesn't distinguish Cow/Coffee Cow/Fruit Cow/Strawberry Cow (or Sheep/Alpaca) from each
# other, so every color variant of a species links to that same base species page.
_ANIMALS_BY_NAME = {a["name"]: a for a in load_table("animals")["ANIMALS"]}
_ANIMAL_TYPE_TO_SPECIES = {
    "Chicken": "Chicken", "Chick": "Chicken",
    "Rabbit": "Angora Rabbit", "Baby Rabbit": "Angora Rabbit",
    "Baby Cow": "Cow", "Calf": "Cow", "Adult Cow": "Cow",
    "Baby Sheep": "Sheep", "Adult Sheep": "Sheep",
    "Little Horse": "Horse", "Adult Horse": "Horse",
}


def _animal_wiki_url(animal_type: str) -> str | None:
    species = _ANIMAL_TYPE_TO_SPECIES.get(animal_type)
    return _ANIMALS_BY_NAME[species]["url"] if species else None

# Fallback values for the brief window where Save Data Base is ready (so _rebuild() has already
# passed its gate) but Time Base -- a separate hook, RemindersLiveData's own -- hasn't reported in
# yet (2026-09-15). Never shown as if real; _build_header_section()/_build_weather_section() just
# render whatever's current the instant Time Base's own "updated" signal triggers the next rebuild.
_UNATTACHED_HEADER = mock.HeaderInfo(year=1, season="Spring", day=1, hour=6, minute=0)
_UNATTACHED_WEATHER = mock.WeatherInfo(today="Sunny", tomorrow="Sunny")

NOT_LOADED_TITLE = "Reload Save Data or Change Areas to Populate"

_NOTE_SYMBOL = "♪"
_HEART_SYMBOL = "❤"

# Heart Color -> a legible QColor for the colored heart symbol (Marriage Candidates table).
_HEART_COLOR_QCOLOR = {
    "Gray": QColor(140, 140, 140),
    "Purple": QColor(160, 90, 220),
    "Blue": QColor(50, 110, 240),
    "Green": QColor(40, 160, 60),
    "Yellow": QColor(210, 180, 20),
    "Orange": QColor(240, 130, 20),
    "Red": QColor(220, 30, 30),
}


def _friendship_notes_count(fp: int) -> int:
    # Ascending-minimum lookup, same convention as heart_levels.json/animal_affection_hearts.json
    # (data/tables/friendship_notes.json's own _note): 25 FP = 1 note, up to 10 at 250+.
    notes = 0
    for tier in _FRIENDSHIP_NOTES:
        if fp >= tier["min_fp"]:
            notes = tier["notes"]
    return notes


def _friendship_display(fp: int) -> str:
    # "3♪" rather than "♪♪♪".
    return f"{_friendship_notes_count(fp)}{_NOTE_SYMBOL}"


def _heart_color_for(lp: int) -> str:
    color = _HEART_LEVELS[0]["color"]
    for tier in _HEART_LEVELS:
        if lp >= tier["min_lp"]:
            color = tier["color"]
    return color


def _tenth_gift_upcoming_reward(tenth: int, total: int) -> str:
    """What the 10th-Gift counter's next threshold actually awards. Normally White Grass, but the
    Total Gifts counter reaching one of its own milestones on that same visit supersedes it
    entirely -- no double reward. `total` moves in
    lockstep with `tenth` (both increment once per gift given), so the total at the moment tenth
    reaches 10 is exactly predictable from where each counter stands right now.
    """
    predicted_total = total + (10 - tenth)
    milestone = next((row for row in _HG_OFFERINGS if row["offerings_given"] == predicted_total), None)
    return milestone["reward_received"] if milestone is not None else _HG_TENTH_GIFT_ITEM


def _hyperlink(name: str, url: str | None) -> str:
    return f'<a href="{url}">{name}</a>' if url else name


def _is_maker_item(item_name: str) -> bool:
    # "X Maker" items (Cheese Maker, Mayonnaise Maker, Yarn Maker (Coop/Barn), Butter Maker --
    # data/tables/enums.json's SAIBARA_CATALOG) are placed directly in the coop/barn once finished,
    # no Saibara pickup needed -- unlike tool upgrades and accessories (Bracelet/Necklace/Earrings/
    # Brooch), which still need an explicit visit to collect.
    return "Maker" in item_name

FUTURE_EVENTS_WINDOW_DAYS = 14
HARVEST_SPRITES_COLUMN_WIDTHS_KEY = "reminders/harvest_sprites_column_widths"
ANIMALS_COLUMN_WIDTHS_KEY = "reminders/animals_column_widths"
CROP_STATUS_COLUMN_WIDTHS_KEY = "reminders/crop_status_column_widths"
MARRIAGE_CANDIDATES_COLUMN_WIDTHS_KEY = "reminders/marriage_candidates_column_widths"
VILLAGERS_STATUS_COLUMN_WIDTHS_KEY = "reminders/villagers_status_column_widths"

# Collapse-state keys, one per section -- tested first on just Animals (2026-09-14), now rolled
# out to every titled section (see _make_collapsible_box()). Header/Weather aren't included: they
# aren't boxed sections with a title bar, just a plain always-shown summary line at the top.
FESTIVALS_COLLAPSED_KEY = "reminders/festivals_collapsed"
BED_TIME_REMINDER_COLLAPSED_KEY = "reminders/bed_time_reminder_collapsed"
SHOP_INFO_COLLAPSED_KEY = "reminders/shop_info_collapsed"
HIDDEN_COUNTERS_COLLAPSED_KEY = "reminders/hidden_counters_collapsed"
PLAYER_NOTES_COLLAPSED_KEY = "reminders/player_notes_collapsed"
FESTIVAL_REMINDERS_COLLAPSED_KEY = "reminders/festival_reminders_collapsed"
HARVEST_SPRITES_COLLAPSED_KEY = "reminders/harvest_sprites_collapsed"
VILLAGER_REMINDERS_COLLAPSED_KEY = "reminders/villager_reminders_collapsed"
ANIMALS_COLLAPSED_KEY = "reminders/animals_collapsed"
MARRIAGE_CANDIDATES_COLLAPSED_KEY = "reminders/marriage_candidates_collapsed"
VILLAGERS_STATUS_COLLAPSED_KEY = "reminders/villagers_status_collapsed"
CROP_STATUS_COLLAPSED_KEY = "reminders/crop_status_collapsed"
DAILY_REMINDERS_COLLAPSED_KEY = "reminders/daily_reminders_collapsed"
AVAILABLE_EVENTS_COLLAPSED_KEY = "reminders/available_events_collapsed"
FUTURE_EVENTS_COLLAPSED_KEY = "reminders/future_events_collapsed"

DANGEROUS_WEATHER = ("Typhoon", "Blizzard")
WEATHER_FLASH_INTERVAL_MS = 500


def _other_event_matches(event: dict, info, weekday: str) -> bool:
    # Day/season/year gates deliberately mirror heart_events.json's own days_mode/season_mode
    # convention (see _heart_event_entries's day/season checks) -- same field names, same
    # "only"/"exclude"/"any" semantics, so both tables read the same way. day_of_month/
    # day_of_month_range/year_mode are this table's own extensions for cases heart_events.json
    # never needed (a fixed day-of-month like Mayor Thomas' Day 2, or a Year N+ restriction).
    if event["days_mode"] == "only" and weekday not in event["days"]:
        return False
    if event["days_mode"] == "exclude" and weekday in event["days"]:
        return False
    if event["season_mode"] == "only" and info.season != event["season"]:
        return False
    if event["season_mode"] == "exclude" and info.season == event["season"]:
        return False
    if event["year_mode"] == "only" and info.year != event["year"]:
        return False
    if event["year_mode"] == "min" and info.year < event["year"]:
        return False
    day_of_month = event.get("day_of_month")
    if day_of_month is not None and info.day != day_of_month:
        return False
    day_range = event.get("day_of_month_range")
    if day_range is not None and not (day_range[0] <= info.day <= day_range[1]):
        return False
    return True


def _other_event_weather_ok(allowed_weather: Optional[list], weather: WeatherInfo) -> bool:
    # other_events.json's "weather" is null (no requirement) or a list of allowed WEATHER_NAMES
    # strings, OR'd together (e.g. Dudley and Duke's Fight needs Rain OR Snow) -- heart_events.json's
    # own "weather_required" is boolean/Sunny-only, which this table's Rain-or-Snow cases need more
    # than.
    return not allowed_weather or weather.today in allowed_weather


# Must match data/tables/shops.json's own "name" exactly (see _build_shop_info_section's Gotts
# building-upgrade override).
_GOTTS_WORKSHOP_NAME = "Gotts' Workshop"

# Shops whose real open/closed rule isn't a plain weekday match (data/tables/shops.json flags
# both in its own notes) -- modeled here individually rather than guessing at a generic schema
# addition until more shops like this turn up.
_SPECIAL_SHOP_OPEN_RULES = {
    # Only operates in Summer at all -- outside that season it's closed regardless of weekday.
    "Beach Cafe": lambda shop, info, weekday: info.season == "Summer" and weekday not in shop.get("days_closed", []),
    # Opens by day-of-month (the 15th of every season), not by weekday -- see
    # data/tables/other_events.json's own entry for this same rule.
    "Van's Pet Shop": lambda shop, info, weekday: info.day == 15,
}


def _shop_is_open(shop: dict, info, weekday: str) -> bool:
    rule = _SPECIAL_SHOP_OPEN_RULES.get(shop["name"])
    if rule is not None:
        return rule(shop, info, weekday)
    return weekday not in shop.get("days_closed", [])


class RemindersWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        top_bar = QHBoxLayout()
        top_bar.addStretch()
        player_notes_button = QPushButton("Player Notes...")
        player_notes_button.clicked.connect(self._open_player_notes)
        top_bar.addWidget(player_notes_button)
        settings_button = QPushButton("Settings...")
        settings_button.clicked.connect(self._open_settings)
        top_bar.addWidget(settings_button)
        # Separate from Settings above -- data/tables/other_events.json's own
        # per-event Show/Completed toggles, kept in their own window since that table is expected
        # to keep growing. See app/event_settings.py's own docstring.
        event_settings_button = QPushButton("Event Settings...")
        event_settings_button.clicked.connect(self._open_event_settings)
        top_bar.addWidget(event_settings_button)
        outer.addLayout(top_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)

        # _rebuild() tears down and recreates every OTHER section's widgets from scratch on every
        # live data update -- fine for plain labels, but it meant a table like Harvest Sprite
        # Schedule got replaced by a brand new QTableWidget instance on every single poll tick,
        # permanently interrupting any drag-to-resize or scroll gesture in progress. The real fix
        # is to never rebuild these
        # tables at all: each is built ONCE (see _make_persistent_table()) and lives for the
        # widget's whole lifetime; live updates only ever update cell text in place. _rebuild()
        # re-inserts the same persistent box into the layout each time (skipping it in its
        # teardown loop) rather than asking a builder function for a fresh one.
        self._harvest_sprites_box, self._harvest_sprites_table, self._harvest_sprites_title = (
            self._make_persistent_table(
                # Renamed from "Harvest Sprite Schedule", and its columns
                "Harvest Sprites",
                # rebuilt to match the Villagers table's own layout/conventions (2026-09-15, second
                # pass): the old combined "Status" text column (e.g. "Talked, No gift") is now
                # separate Talked to Today/Gifted Today Yes/No columns, "Friendship"/"Friendship
                # Points" reuse the same +0x10 field/scale the Villagers/Marriage Candidates tables
                # read (sprites already needed it for their own training-eligibility gate, see
                # MIN_FRIENDSHIP_TO_PLAY), "Location" reuses the same +0x0 offset/decode confirmed
                # for sprites the same day (see src/reminders_harvest_sprites_agent.js), and
                # "Work Status"/"Assigned Task" keep their prior meaning (the old "Working (N
                # day(s) left)"/training-eligibility text, and the current task assignment) just
                # reordered to the end. "Favorite Gifts" reuses data/tables/villagers.json's
                # loved_forage/loved_ingredients, same as the other two tables, looked up by the
                # sprite's plain name (_VILLAGER_BY_PLAIN_NAME).
                ["Sprite", "Location", "Friendship", "FP", "Talked to Today",
                 "Gifted Today", "Work Status", "Assigned Task", "Experience", "Favorite Gifts"],
                HARVEST_SPRITES_COLUMN_WIDTHS_KEY,
                HARVEST_SPRITES_COLLAPSED_KEY,
            )
        )
        self._animals_box, self._animals_table, self._animals_title = self._make_persistent_table(
            "Animals",
            # "Animal" (2026-09-13) replaces the old removed "Stage" column -- now backed by a real
            # per-species growth-stage calc (_animal_type() in reminders_live_data.py) instead of
            # the earlier dead Baby/Adult logic. "FP" (2026-09-13, renamed from "Friendship Points"
            # 2026-09-15 to match the Villagers/Marriage Candidates/Harvest Sprites tables'
            # abbreviation) shows the raw Affection Points number alongside the derived Hearts.
            ["Animal", "Name", "Location", "Hearts", "FP", "Age", "Fed Today?",
             "Talked To", "Brushed", "Milked/Sheared", "Pregnant"],
            ANIMALS_COLUMN_WIDTHS_KEY,
            ANIMALS_COLLAPSED_KEY,
        )
        # Column indices within the Animals table whose cells get a red/green complete-today
        # background -- "Yes"/"No" cells only; N/A/"Not Fed Yesterday"/
        # "Pregnant" and everything else (Animal/Name/Location/Hearts/Friendship Points/Age/
        # Pregnant column) stay uncolored.
        self._animals_status_columns = {6, 7, 8, 9} # Fed Today? / Talked To / Brushed / Milked-Sheared

        # Marriage Candidates / Villagers status tables (2026-09-14) -- split from the same
        # RemindersVillagerData source by data/tables/character_viewer.json's
        # MARRIAGE_CANDIDATE_IDS, same convention the Marriage Candidates/Villagers dock tabs use.
        # Heart Color/LP only make sense for marriage candidates (Love Points is what those two
        # columns are about), so only that table gets them -- inserted before Friendship in the
        # column order. "Friendship Notes" renamed to "Friendship" and "Favorite
        # Gifts" (from villagers.json's loved_forage/loved_ingredients, same text
        # _villager_birthday_text() already shows) added at the end.
        self._marriage_candidates_box, self._marriage_candidates_table, self._marriage_candidates_title = (
            self._make_persistent_table(
                "Marriage Candidates",
                ["Villager", "Location", "Heart Color", "LP", "Friendship", "FP",
                 "Talked to Today", "Gifted Today", "Favorite Gifts"],
                MARRIAGE_CANDIDATES_COLUMN_WIDTHS_KEY,
                MARRIAGE_CANDIDATES_COLLAPSED_KEY,
            )
        )
        self._villagers_status_box, self._villagers_status_table, self._villagers_status_title = (
            self._make_persistent_table(
                "Villagers",
                ["Villager", "Location", "Friendship", "FP", "Talked to Today", "Gifted Today",
                 "Favorite Gifts"],
                VILLAGERS_STATUS_COLUMN_WIDTHS_KEY,
                VILLAGERS_STATUS_COLLAPSED_KEY,
            )
        )

        self._crop_status_box, self._crop_status_table, self._crop_status_title = self._make_persistent_table(
            "Crop Status",
            # "Needs Harvest" added 2026-09-13 once the growth table was confirmed live, day-by-day
            # (see data/pointer_map.md's "Growth table" section) -- occupant==5 across every crop.
            ["Crop", "Needs Water", "Needs Harvest", "Total Planted"],
            CROP_STATUS_COLUMN_WIDTHS_KEY,
            CROP_STATUS_COLLAPSED_KEY,
        )

        self._column_widths_sized: set[str] = set()
        self._pending_calls: set[str] = set()

        # Tracks whether a Save Data Base has been found at all -- gates the whole window body in
        # _rebuild(). Every
        # data source below still owns its own additional script/attach; this is only for that
        # top-level gate, reusing the same shared session they all attach through.
        self._game_session = get_shared_session()
        self._game_session.save_data_base_found.connect(lambda _addr: self._coalesce("rebuild", self._rebuild))

        self._live_data = RemindersLiveData(self)
        self._live_data.updated.connect(lambda: self._coalesce("rebuild", self._rebuild))

        self._harvest_sprite_data = RemindersHarvestSpriteData(self)
        self._harvest_sprite_data.updated.connect(
            lambda: self._coalesce("harvest_sprites", self._refresh_harvest_sprites_table)
        )

        self._villager_data = RemindersVillagerData(self)
        self._villager_data.updated.connect(lambda: self._coalesce("rebuild", self._rebuild))
        self._villager_data.updated.connect(
            lambda: self._coalesce("villager_status_tables", self._refresh_villager_status_tables)
        )

        self._animal_data = RemindersAnimalData(self)
        self._animal_data.updated.connect(lambda: self._coalesce("animals", self._refresh_animals_table))

        self._gotts_data = RemindersGottsData(self)
        self._gotts_data.updated.connect(lambda: self._coalesce("rebuild", self._rebuild))

        self._forge_data = RemindersForgeData(self)
        self._forge_data.updated.connect(lambda: self._coalesce("rebuild", self._rebuild))

        self._sleep_data = RemindersSleepData(self)
        self._sleep_data.updated.connect(lambda: self._coalesce("rebuild", self._rebuild))

        self._crop_data = RemindersCropData(self)
        self._crop_data.updated.connect(lambda: self._coalesce("crop_status", self._refresh_crop_status_table))

        self._shop_counter_data = RemindersShopCounterData(self)
        self._shop_counter_data.updated.connect(lambda: self._coalesce("rebuild", self._rebuild))

        self._hg_gifts_data = RemindersHarvestGoddessGiftsData(self)
        self._hg_gifts_data.updated.connect(lambda: self._coalesce("rebuild", self._rebuild))

        self._kappa_cucumbers_data = RemindersKappaCucumbersData(self)
        self._kappa_cucumbers_data.updated.connect(lambda: self._coalesce("rebuild", self._rebuild))

        self._refresh_harvest_sprites_table()
        self._refresh_animals_table()
        self._refresh_villager_status_tables()
        self._refresh_crop_status_table()
        self._rebuild()

    # -- top bar actions --------------------------------------------------------------------

    def _open_settings(self) -> None:
        dialog = RemindersSettingsDialog(on_changed=self._rebuild, parent=self)
        dialog.exec()

    def _open_event_settings(self) -> None:
        dialog = EventSettingsDialog(on_changed=self._rebuild, parent=self)
        dialog.exec()

    def _open_player_notes(self) -> None:
        info = self._current_header()
        dialog = PlayerNotesDialog(default_year=info.year, default_season=info.season,
                                    on_changed=self._rebuild, parent=self)
        dialog.exec()

    def cleanup(self) -> None:
        self._live_data.cleanup()
        self._harvest_sprite_data.cleanup()
        self._villager_data.cleanup()
        self._animal_data.cleanup()
        self._gotts_data.cleanup()
        self._forge_data.cleanup()
        self._sleep_data.cleanup()
        self._crop_data.cleanup()
        self._shop_counter_data.cleanup()
        self._hg_gifts_data.cleanup()
        self._kappa_cucumbers_data.cleanup()

    def _coalesce(self, key: str, fn) -> None:
        # Live data sources report one message per slot/villager, and a burst of them (e.g.
        # Animals' ~25 slot messages, or Villager Reminders' ~40) all lands within the same
        # instant on attach. Calling fn() straight from every single message made those sections
        # visibly grow row by row / line by line instead of appearing all at once while loading.
        # Collapsing every call for the same key that arrives before Qt gets around to it into a
        # single deferred call
        # means a whole burst only triggers one rebuild.
        if key in self._pending_calls:
            return
        self._pending_calls.add(key)

        def run() -> None:
            self._pending_calls.discard(key)
            fn()

        QTimer.singleShot(0, run)

    # -- collapsible section boxes: shared by every titled section, persistent or rebuilt --------

    def _make_collapsible_box(self, title: str, collapsed_key: str):
        """Builds a QGroupBox with a custom header row (collapse toggle + title label) and an
        empty content area below it that callers fill in. Returns (box, content_layout,
        title_label) -- content_layout is where section content goes; title_label lets a caller
        update the displayed title later (used by the persistent-table sections, whose title
        reflects live/mock state) without touching the collapse toggle itself.

        Collapse state is read from/written to config.py under collapsed_key, so a section rebuilt
        from scratch every _rebuild() call (every non-persistent section) still remembers whether
        it was left collapsed, the same as the persistent tables that are only ever built once.
        """
        box = QGroupBox()
        outer_layout = QVBoxLayout(box)

        header = QHBoxLayout()
        toggle_button = QToolButton()
        toggle_button.setCheckable(True)
        toggle_button.setAutoRaise(True)
        # Checked state (collapsed) otherwise repaints with the platform style's own
        # checked-button palette, which flips this text to black -- pin the
        # color explicitly for both states so it doesn't change on toggle.
        toggle_button.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; color: white; }"
            "QToolButton:checked { background: transparent; color: white; }"
        )
        header.addWidget(toggle_button)
        title_label = QLabel(f"<b>{title}</b>")
        header.addWidget(title_label)
        header.addStretch()
        outer_layout.addLayout(header)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(content)

        collapsed = self._read_collapsed(collapsed_key)

        def apply_collapsed(is_collapsed: bool) -> None:
            content.setVisible(not is_collapsed)
            toggle_button.setText("▶" if is_collapsed else "▼") # right/down triangle

        def on_toggled(checked: bool) -> None:
            apply_collapsed(checked)
            config.get_settings().setValue(collapsed_key, checked)

        toggle_button.setChecked(collapsed)
        toggle_button.toggled.connect(on_toggled)
        apply_collapsed(collapsed)

        return box, content_layout, title_label

    # -- persistent tables: built once, updated in place, column widths saved to config.py -----

    def _make_persistent_table(self, title: str, columns: list[str], width_key: str, collapsed_key: str):
        box, content_layout, title_label = self._make_collapsible_box(title, collapsed_key)

        table = CopyableTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.verticalHeader().setVisible(False)
        table.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(table)
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        table.horizontalHeader().sectionResized.connect(
            lambda *_args, t=table, k=width_key: self._save_column_widths(t, k)
        )
        # Re-fit the table's own fixed height whenever its horizontal scrollbar's range changes
        # (Harvest Sprites -- 10 columns, the widest of any of these tables -- kept showing a
        # vertical scrollbar even after _fit_table_height() accounted for the header/row heights,
        # because a horizontal scrollbar appearing at the bottom eats into the same fixed height
        # and wasn't being accounted for at all). rangeChanged fires whenever
        # whether a horizontal scrollbar is needed changes -- column resize, window resize, or
        # content changing width -- so this stays correct as any of those change later too.
        table.horizontalScrollBar().rangeChanged.connect(lambda *_args, t=table: self._fit_table_height(t))

        return box, table, title_label

    @staticmethod
    def _read_collapsed(collapsed_key: str | None) -> bool:
        if collapsed_key is None:
            return False
        value = config.get_settings().value(collapsed_key, False)
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    @staticmethod
    def _save_column_widths(table, key: str) -> None:
        widths = [table.columnWidth(col) for col in range(table.columnCount())]
        config.get_settings().setValue(key, widths)

    def _size_columns_once(self, table, key: str, has_rows: bool) -> None:
        # Auto-size (or restore a saved width) once, the first time there's anything to size
        # against -- never overwrite a user's own resize on a later refresh. Saved to config.py on
        # every resize (see _save_column_widths()) so it survives closing the tab or restarting
        # the app, not just staying stable within one already-open session.
        if key in self._column_widths_sized or not has_rows:
            return
        saved_widths = config.get_settings().value(key)
        if saved_widths is not None and len(saved_widths) == table.columnCount():
            for col, width in enumerate(saved_widths):
                table.setColumnWidth(col, int(width))
        else:
            table.resizeColumnsToContents()
        self._column_widths_sized.add(key)

    @staticmethod
    def _set_name_link_cell(table, row: int, col: int, name: str, url: str | None) -> None:
        # A hyperlinked name needs a QLabel (rich text), not a plain QTableWidgetItem. Reuses the
        # existing label if this cell already has one instead of recreating it every refresh, but
        # -- unlike an earlier "set once and never touch again" version -- always keeps its text in
        # sync with the current name/url. That matters for the Animals table specifically: unlike
        # Villagers/Marriage Candidates/Harvest Sprites (a fixed list, same row = same character
        # every refresh), a given ROW here can end up showing a completely different animal over
        # time as animals are bought/sold and the row count/order shifts -- a "set once" label would
        # silently keep pointing at a stale species' wiki page.
        text = f'<a href="{url}">{name}</a>' if url else name
        widget = table.cellWidget(row, col)
        if isinstance(widget, QLabel):
            if widget.text() != text:
                widget.setText(text)
            return
        label = QLabel(text)
        label.setOpenExternalLinks(True)
        label.setContentsMargins(4, 0, 4, 0)
        table.setCellWidget(row, col, label)

    @staticmethod
    def _fit_table_height(table) -> None:
        # A fixed "30px per row" guess used to set every persistent table's height -- broke once a
        # row's real height differed from that. The hyperlinked
        # QLabel cell widgets (_set_name_link_cell(), 2026-09-15) can render taller than a plain
        # QTableWidgetItem depending on style/DPI, so the assumption silently broke there first.
        # Ask the table for its own real header + row heights instead of guessing. A flat "+2"
        # fudge for the table's own frame border still came in short on every table, not just
        # Marriage Candidates -- use the frame's real width
        # instead of guessing that too, plus a slightly bigger safety margin.
        table.resizeRowsToContents()
        total = table.horizontalHeader().height()
        for row in range(table.rowCount()):
            total += table.rowHeight(row)
        # A visible horizontal scrollbar (e.g. Harvest Sprites' 10 columns not all fitting the
        # box's current width) eats into this same fixed height at the bottom -- account for it,
        # not just the header/row heights. __init__ also connects this table's
        # horizontal scrollbar's rangeChanged signal back to this method, so a column resize or
        # window resize that changes whether a horizontal scrollbar is needed re-fits the height
        # again rather than leaving it stale.
        h_scrollbar = table.horizontalScrollBar()
        if h_scrollbar.isVisible():
            total += h_scrollbar.height()
        table.setFixedHeight(total + table.frameWidth() * 2 + 6)

    def _save_data_ready(self) -> bool:
        return self._game_session.current_save_data_base is not None

    # -- current data (real only -- see _rebuild()'s "no Save Data Base yet" gate) ------------

    def _current_header(self) -> HeaderInfo:
        # _rebuild() never renders anything that calls this unless Save Data Base is ready (see
        # _save_data_ready()'s gate), but Time Base -- a separate hook -- can still lag behind it
        # by a tick, and _open_player_notes()'s default year/season calls this from the "Player
        # Notes..." button, which is always clickable even before a save is loaded. Either way,
        # _UNATTACHED_HEADER below is never shown as if it were real data.
        return self._live_data.header() if self._live_data.is_ready() else _UNATTACHED_HEADER

    def _current_weather(self) -> WeatherInfo:
        # Same Time Base lag as _current_header() above -- only ever reached from
        # _build_weather_section(), which is itself gated on Save Data Base already being ready.
        return self._live_data.weather() if self._live_data.is_ready() else _UNATTACHED_WEATHER

    def _current_harvest_sprites(self) -> list:
        return self._harvest_sprite_data.sprites() if self._harvest_sprite_data.is_ready() else []

    def _gotts_job(self):
        # No mock fallback -- real data or nothing, same reasoning as Villager Reminders' per-
        # villager talk/gift status below.
        return self._gotts_data.job() if self._gotts_data.is_ready() else None

    @staticmethod
    def _gotts_building_text(building_name: str, remaining: int, delayed: bool) -> str:
        # Confirmed live 2026-09-13: the Days Remaining countdown does NOT progress on a festival
        # day -- shared between Daily Reminders (today) and Future Events (a simulated future day).
        if delayed:
            return f"Gotts Building {building_name} (Delayed due to Festival, {remaining} Days Remaining)"
        if remaining <= 0:
            return f"Gotts finished building {building_name}"
        return f"Gotts is building {building_name} ({remaining} Days remaining)"

    def _forge_job(self):
        # No mock fallback -- real data or nothing, same reasoning as _gotts_job() above.
        return self._forge_data.job() if self._forge_data.is_ready() else None

    @staticmethod
    def _forge_text(item_name: str, level_name: str, remaining: int) -> str:
        # Unlike Gotts' building-upgrade countdown, a festival/holiday day does NOT freeze
        # Saibara's -- confirmed live 2026-09-13 (see src/reminders_forge_agent.js's derivation
        # notes), so there's no "delayed" variant here the way _gotts_building_text() has one.
        # Once ready, keep showing this every day until the item is actually
        # collected -- already the natural behavior here since Days Remaining stays at 0 (and the
        # job stays active) until pickup, so this branch just keeps firing on every later call.
        # Doesn't apply to "Maker" items though -- those are auto-delivered to
        # the coop/barn, not collected from Saibara, and _build_daily_reminders_section stops
        # calling this once one of those is done rather than showing a stale "ready for pickup"
        # line forever; this wording only still shows up as a one-day-ahead Future Events preview.
        display_name = f"{level_name} {item_name}" if level_name else item_name
        if remaining <= 0:
            if _is_maker_item(item_name):
                return f"{display_name} placed in the coop/barn"
            return f"{display_name} is ready for pickup from Saibara!"
        if level_name:
            # "Hoe being upgraded to Golden Hoe (X days remaining)" -- tool jobs only, since a
            # level doesn't apply to a non-tool item.
            return f"{item_name} being upgraded to {display_name} ({remaining} Days remaining)"
        return f"Saibara is working on your {item_name} ({remaining} Days remaining)"

    def _festival_on(self, season: str, day: int) -> Optional[dict]:
        return next((row for row in _FESTIVALS if row["season"] == season and row["day"] == day), None)

    def _festival_today(self, info) -> Optional[dict]:
        return self._festival_on(info.season, info.day)

    # -- Harvest Sprites: persistent table, updated in place -----------------------------------

    def _refresh_harvest_sprites_table(self) -> None:
        sprites = self._current_harvest_sprites()
        is_live = self._harvest_sprite_data.is_ready()
        self._harvest_sprites_title.setText(
            "<b>Harvest Sprites</b>" if is_live else "<b>Harvest Sprites (waiting for game)</b>"
        )

        table = self._harvest_sprites_table
        if table.rowCount() != len(sprites):
            table.setRowCount(len(sprites))

        for row, sprite in enumerate(sprites):
            villager_entry = _VILLAGER_BY_PLAIN_NAME.get(sprite.name)
            self._set_name_link_cell(
                table, row, 0, sprite.name, villager_entry.get("url") if villager_entry else None
            )

            talked = "Yes" if sprite.talked else "No"
            gifted = "Yes" if sprite.gifted else "No"
            # Column 0 (Sprite) is the hyperlinked QLabel set above -- the rest are plain items,
            # starting at column 1.
            values = [
                sprite.location or "-",
                _friendship_display(sprite.friendship),
                str(sprite.friendship),
                talked,
                gifted,
                sprite.training_status,
                sprite.task,
                sprite.exp_text,
                self._villager_loved_gifts(villager_entry) if villager_entry else "",
            ]
            for offset, text in enumerate(values):
                col = offset + 1
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                if item.text() != text:
                    item.setText(text)
                if col in (4, 5): # Talked to Today / Gifted Today
                    item.setBackground(self._animal_status_brush(text))
                    item.setForeground(self._animal_status_foreground(text))

        self._size_columns_once(table, HARVEST_SPRITES_COLUMN_WIDTHS_KEY, bool(sprites))
        self._fit_table_height(table)

    # -- Animals: persistent table, updated in place -------------------------------------------

    def _refresh_animals_table(self) -> None:
        animals = self._animal_data.animals()
        self._animals_title.setText("<b>Animals</b>" if animals else "<b>Animals (waiting for game)</b>")

        table = self._animals_table
        if table.rowCount() != len(animals):
            table.setRowCount(len(animals))

        for row, animal in enumerate(animals):
            self._set_name_link_cell(table, row, 0, animal.animal_type, _animal_wiki_url(animal.animal_type))

            # Column 0 (Animal) is the hyperlinked QLabel set above -- the rest are plain items,
            # starting at column 1.
            values = [
                animal.name,
                animal.location,
                animal.hearts,
                str(animal.friendship_points),
                str(animal.age),
                animal.fed_today,
                animal.talked,
                animal.brushed,
                animal.milked_sheared,
                animal.pregnant,
            ]
            for offset, text in enumerate(values):
                col = offset + 1
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                if item.text() != text:
                    item.setText(text)
                if col in self._animals_status_columns:
                    item.setBackground(self._animal_status_brush(text))
                    item.setForeground(self._animal_status_foreground(text))

        self._size_columns_once(table, ANIMALS_COLUMN_WIDTHS_KEY, bool(animals))
        self._fit_table_height(table)

    @staticmethod
    def _animal_status_brush(text: str) -> QBrush:
        # "N/A" (the Horse's Fed/Milked-Sheared columns, Coop's Brushed column) and "Not Fed
        # Yesterday" (Barn's Milked/Sheared column) are left uncolored -- none of these are a
        # plain complete/incomplete Yes/No, there's nothing to color.
        if text == "Yes":
            return QBrush(QColor(200, 255, 200))
        if text == "No":
            return QBrush(QColor(255, 200, 200))
        return QBrush()

    @staticmethod
    def _animal_status_foreground(text: str) -> QBrush:
        # The red/green Yes/No backgrounds above need black text to stay legible --
        # everything else keeps the table's own default text color, since it also
        # keeps the default (uncolored) background.
        if text in ("Yes", "No"):
            return QBrush(QColor(0, 0, 0))
        return QBrush()

    # -- Marriage Candidates / Villagers status tables: persistent, updated in place -------------

    @staticmethod
    def _tracked_villagers() -> list[dict]:
        # Same "confirmed game id, not a Harvest Sprite" filter reminders_villagers_agent.js's own
        # TRACKED list uses -- everyone these two tables could possibly show live data for.
        return [v for v in _VILLAGERS if v.get("id") is not None and "(Harvest Sprite)" not in v["name"]]

    def _marriage_candidate_rows(self) -> list[dict]:
        return sorted(
            (v for v in self._tracked_villagers() if v["id"] in _MARRIAGE_CANDIDATE_IDS),
            key=lambda v: v["id"],
        )

    def _villager_rows(self) -> list[dict]:
        return sorted(
            (v for v in self._tracked_villagers() if v["id"] not in _MARRIAGE_CANDIDATE_IDS),
            key=lambda v: v["id"],
        )

    def _refresh_villager_status_tables(self) -> None:
        self._refresh_marriage_candidates_table()
        self._refresh_villagers_status_table()

    def _refresh_marriage_candidates_table(self) -> None:
        candidates = self._marriage_candidate_rows()
        is_live = self._villager_data.is_ready()
        self._marriage_candidates_title.setText(
            "<b>Marriage Candidates</b>" if is_live else "<b>Marriage Candidates (waiting for game)</b>"
        )

        table = self._marriage_candidates_table
        if table.rowCount() != len(candidates):
            table.setRowCount(len(candidates))

        for row, villager in enumerate(candidates):
            self._set_name_link_cell(table, row, 0, villager["name"], villager.get("url"))

            status = self._villager_data.status_for(villager["name"])
            friendship = status["friendship"] if status else 0
            love_points = status["love_points"] if status else 0
            talked = ("Yes" if status["talked"] else "No") if status else ""
            gifted = ("Yes" if status["gifted"] else "No") if status else ""
            heart_color = _heart_color_for(love_points) if status else None
            # Column 0 (Villager) is the hyperlinked QLabel set above -- the rest are plain items,
            # starting at column 1.
            values = [
                (status["location"] if status else None) or "-",
                _HEART_SYMBOL if status else "",
                str(love_points) if status else "",
                _friendship_display(friendship) if status else "",
                str(friendship) if status else "",
                talked,
                gifted,
                self._villager_loved_gifts(villager),
            ]
            for offset, text in enumerate(values):
                col = offset + 1
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                if item.text() != text:
                    item.setText(text)
                if col == 2: # Heart Color -- the heart symbol tinted by LP's current tier
                    item.setForeground(
                        QBrush(_HEART_COLOR_QCOLOR.get(heart_color, QColor(0, 0, 0))) if status else QBrush()
                    )
                elif col in (6, 7): # Talked to Today / Gifted Today
                    item.setBackground(self._animal_status_brush(text))
                    item.setForeground(self._animal_status_foreground(text))

        self._size_columns_once(table, MARRIAGE_CANDIDATES_COLUMN_WIDTHS_KEY, bool(candidates))
        self._fit_table_height(table)

    def _refresh_villagers_status_table(self) -> None:
        villagers = self._villager_rows()
        is_live = self._villager_data.is_ready()
        self._villagers_status_title.setText(
            "<b>Villagers</b>" if is_live else "<b>Villagers (waiting for game)</b>"
        )

        table = self._villagers_status_table
        if table.rowCount() != len(villagers):
            table.setRowCount(len(villagers))

        for row, villager in enumerate(villagers):
            self._set_name_link_cell(table, row, 0, villager["name"], villager.get("url"))

            status = self._villager_data.status_for(villager["name"])
            friendship = status["friendship"] if status else 0
            talked = ("Yes" if status["talked"] else "No") if status else ""
            gifted = ("Yes" if status["gifted"] else "No") if status else ""
            # Column 0 (Villager) is the hyperlinked QLabel set above -- the rest are plain items,
            # starting at column 1.
            values = [
                (status["location"] if status else None) or "-",
                _friendship_display(friendship) if status else "",
                str(friendship) if status else "",
                talked,
                gifted,
                self._villager_loved_gifts(villager),
            ]
            for offset, text in enumerate(values):
                col = offset + 1
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                if item.text() != text:
                    item.setText(text)
                if col in (4, 5): # Talked to Today / Gifted Today
                    item.setBackground(self._animal_status_brush(text))
                    item.setForeground(self._animal_status_foreground(text))

        self._size_columns_once(table, VILLAGERS_STATUS_COLUMN_WIDTHS_KEY, bool(villagers))
        self._fit_table_height(table)

    # -- Crop Status: persistent table, updated in place -----------------------------------------

    def _refresh_crop_status_table(self) -> None:
        rows = self._crop_data.rows()
        self._crop_status_title.setText(
            "<b>Crop Status</b>" if self._crop_data.is_ready() else "<b>Crop Status (waiting for game)</b>"
        )

        table = self._crop_status_table
        if table.rowCount() != len(rows):
            table.setRowCount(len(rows))

        for row, crop in enumerate(rows):
            values = [crop.crop_name, str(crop.needs_water), str(crop.needs_harvest), str(crop.total)]
            for col, text in enumerate(values):
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                if item.text() != text:
                    item.setText(text)
                # "Needs Water" (col 1) / "Needs Harvest" (col 2) -- flag any crop with at least
                # one tile needing that action, same red/green convention as the Animals table's
                # Yes/No columns.
                if col in (1, 2):
                    needs_action = (crop.needs_water if col == 1 else crop.needs_harvest) > 0
                    item.setBackground(QBrush(QColor(255, 200, 200) if needs_action else QColor(200, 255, 200)))
                    item.setForeground(QBrush(QColor(0, 0, 0)))

        self._size_columns_once(table, CROP_STATUS_COLUMN_WIDTHS_KEY, bool(rows))
        self._fit_table_height(table)

    # -- layout -------------------------------------------------------------------------------

    # Maps each reorderable group (see reminders_settings.SECTION_ORDER_GROUPS) to how it's
    # rendered. "self_gated" groups decide their own visibility (the builder returns None to
    # hide); everything else is gated by settings.section_enabled() on every key in the group.
    def _section_renderers(self) -> dict[tuple[str,...], tuple[str, object]]:
        return {
            ("festivals",): ("builder", self._build_festivals_section),
            ("bed_time_reminder",): ("builder", self._build_bed_time_reminder_section),
            ("shop_info",): ("builder", self._build_shop_info_section),
            ("hidden_counters",): ("builder", self._build_hidden_counters_section),
            ("player_notes",): ("builder", self._build_player_notes_section),
            ("festival_reminders",): ("builder", self._build_festival_reminders_section),
            ("harvest_sprites",): ("persistent", self._harvest_sprites_box),
            # Villager Reminders covers BOTH birthdays and per-villager talk/gift reminders, each independently toggleable --
            # so it can't be gated by one single settings.section_enabled() check the way every
            # other section is. The builder checks both toggles itself and decides. Both keys
            # share one movable slot (see SECTION_ORDER_GROUPS) since they're one physical box.
            ("villager_birthdays", "villager_reminders"): ("self_gated", self._build_villager_reminders_section),
            # Animals moved below Villager Reminders.
            ("animals",): ("persistent", self._animals_box),
            ("marriage_candidates",): ("persistent", self._marriage_candidates_box),
            ("villagers_status",): ("persistent", self._villagers_status_box),
            ("crop_status",): ("persistent", self._crop_status_box),
            ("daily_reminders",): ("builder", self._build_daily_reminders_section),
            ("available_events",): ("builder", self._build_available_events_section),
            ("future_events",): ("builder", self._build_future_events_section),
        }

    def _rebuild(self) -> None:
        persistent_boxes = (
            self._harvest_sprites_box, self._animals_box, self._marriage_candidates_box,
            self._villagers_status_box, self._crop_status_box,
        )
        while (item:= self._content_layout.takeAt(0)) is not None:
            widget = item.widget()
            # Persistent tables (see __init__/_make_persistent_table()) -- detach from the layout
            # like everything else, but never delete them.
            if widget is not None and widget not in persistent_boxes:
                widget.deleteLater()

        if not self._save_data_ready():
            # No mock placeholder content any more -- before a save is loaded
            # (or briefly while changing areas, which can momentarily invalidate this), the whole
            # window is just this one line. Every persistent table stays built (never torn down,
            # see _make_persistent_table()) but hidden here rather than shown with stale/fake rows.
            for box in persistent_boxes:
                box.hide()
            label = QLabel(f"<b>{NOT_LOADED_TITLE}</b>")
            label.setStyleSheet("font-size: 16pt;")
            self._content_layout.addWidget(label)
            self._content_layout.addStretch(1)
            return

        self._content_layout.addWidget(self._build_header_section())
        self._content_layout.addWidget(self._build_weather_section())

        renderers = self._section_renderers()
        for group in settings.section_order():
            kind, thing = renderers[group]
            if kind == "self_gated":
                section = thing()
                if section is not None:
                    self._content_layout.addWidget(section)
            elif not any(settings.section_enabled(key) for key in group):
                # BUG: a persistent table taken out of the layout above by
                # takeAt() doesn't disappear on its own -- removing a widget from a layout doesn't
                # hide it, so with its section disabled it just sat there, still visible, floating
                # at its last on-screen position, until the app was restarted (which never adds it
                # to the layout in the first place). Explicitly hide it here.
                if kind == "persistent":
                    thing.hide()
            elif kind == "persistent":
                thing.show() # undo the.hide() above from the last time this section was off
                self._content_layout.addWidget(thing)
            else:
                section = thing()
                if section is not None:
                    self._content_layout.addWidget(section)

        # Without this, leftover vertical space in the scroll area gets distributed across the
        # sections above
        # instead of staying blank below the last one -- this stretch item absorbs all of it, so
        # every section stays pinned to its own natural height regardless of window size.
        self._content_layout.addStretch(1)

    # -- sections -----------------------------------------------------------------------------

    def _sleep_reminder_text(self) -> Optional[str]:
        # Dynamic bedtime-recovery reminder -- recomputed from whatever
        # Stamina/Fatigue currently are every time this header rebuilds, not a value snapshotted
        # once at day-start. See app/sleep_recovery.py's module docstring for why that means eating
        # food to recover automatically clears this without any special-case handling here.
        # Master on/off is now the "Bed Time Reminder" checkbox in Settings' "Sections Shown" box
        #; Reminders Settings' "Bed Time Reminder" box still
        # controls the lead-hours threshold and the optional fixed-time floor.
        if not settings.section_enabled("bed_time_reminder"):
            return None
        sleep_status = self._sleep_data.status_now()
        if sleep_status is None:
            return None
        info = self._current_header()
        fixed_time = settings.sleep_reminder_fixed_time()
        fixed_hour, fixed_minute = fixed_time if fixed_time is not None else (None, None)
        return sleep_recovery.reminder_text(
            info.hour, info.minute, sleep_status.stamina, sleep_status.fatigue, sleep_status.power_berries,
            lead_hours=settings.sleep_reminder_lead_hours(),
            fixed_hour=fixed_hour, fixed_minute=fixed_minute,
        )

    def _build_header_section(self) -> QWidget:
        # Only reached once _rebuild()'s "no Save Data Base yet" gate has already passed -- no
        # "(mock)"/"(waiting for game)" suffix needed any more. Time Base (a
        # separate hook from Save Data Base) can still lag behind by a tick or two, in which case
        # _current_header() briefly returns _UNATTACHED_HEADER until its own "updated" signal
        # rebuilds this with the real value.
        info = self._current_header()
        text = game_calendar.format_datetime(info.year, info.season, info.day, info.hour, info.minute)
        label = QLabel(f"<b>{text}</b>")
        label.setStyleSheet("font-size: 16pt;")
        return label

    def _build_bed_time_reminder_section(self):
        # Own section now, not appended to the header line. Gated by the
        # "Bed Time Reminder" Sections Shown checkbox (see _sleep_reminder_text()) same as every
        # other builder section now (2026-09-15) -- still returns None with nothing to show even
        # when the section's enabled (no sleep status yet, or the reminder isn't due).
        text = self._sleep_reminder_text()
        if not text:
            return None
        box, layout, _ = self._make_collapsible_box("Bed Time Reminder", BED_TIME_REMINDER_COLLAPSED_KEY)
        layout.addWidget(QLabel(text))
        return box

    def _build_weather_section(self) -> QWidget:
        # Only reached once _rebuild()'s "no Save Data Base yet" gate has already passed -- see
        # _current_weather()'s own note on the brief Time Base lag.
        weather = self._current_weather()
        label = QLabel()

        normal_html = f"Weather: <b>{weather.today}</b> today, <b>{weather.tomorrow}</b> tomorrow"
        if weather.tomorrow not in DANGEROUS_WEATHER:
            label.setText(normal_html)
            return label

        # Tomorrow's a Typhoon or Blizzard -- flash the tomorrow value red/normal on a timer
        # rather than just coloring it, for a more attention-grabbing warning. QTimer(label)
        # parents the timer to the label, so it's torn down for free
        # the next time this section is rebuilt and the old label is deleted.
        flash_html = (f'Weather: <b>{weather.today}</b> today, '
                       f'<b style="color: red;">{weather.tomorrow}</b> tomorrow')
        state = {"on": True}
        label.setText(flash_html)

        def _toggle():
            state["on"] = not state["on"]
            label.setText(flash_html if state["on"] else normal_html)

        timer = QTimer(label)
        timer.timeout.connect(_toggle)
        timer.start(WEATHER_FLASH_INTERVAL_MS)
        return label

    def _build_festivals_section(self):
        info = self._current_header()
        festival = self._festival_today(info)
        if festival is None:
            return None
        box, layout, _ = self._make_collapsible_box("Festivals", FESTIVALS_COLLAPSED_KEY)
        name_html = _hyperlink(festival["festival"], festival.get("url"))
        label = QLabel(f"Today: <b>{name_html}</b> -- {festival['time']} at {festival['location']}")
        label.setOpenExternalLinks(True)
        layout.addWidget(label)
        return box

    def _build_shop_info_section(self):
        info = self._current_header()
        # All shops are assumed closed on a festival day (see data/tables/shops.json's note) --
        # rather than list every shop as closed (noise), just hide the section entirely.
        if self._festival_today(info) is not None:
            return None

        weekday = game_calendar.weekday_name(info.year, info.season, info.day)

        lines = []
        for shop in _SHOPS:
            mode = settings.shop_mode(shop["name"])
            if mode == settings.SHOP_MODE_HIDDEN:
                continue
            name_html = _hyperlink(shop["name"], shop.get("url"))

            # Gotts is away at a build site rather than in his workshop while a building upgrade
            # is in progress -- overrides the normal open/closed line entirely for his shop while
            # a job is active. SHOP_MODE_OPEN shows nothing here on purpose:
            # the shop genuinely isn't open while he's working.
            job = self._gotts_job() if shop["name"] == _GOTTS_WORKSHOP_NAME else None
            if job is not None:
                if mode in (settings.SHOP_MODE_CLOSED, settings.SHOP_MODE_ALWAYS):
                    lines.append(f"{name_html} - Closed (Building {job.building_name})")
                continue

            is_open = _shop_is_open(shop, info, weekday)
            if mode == settings.SHOP_MODE_CLOSED and not is_open:
                lines.append(f"{name_html} - Closed")
            elif mode == settings.SHOP_MODE_OPEN and is_open:
                lines.append(f"{name_html} -- {shop['hours']}")
            elif mode == settings.SHOP_MODE_ALWAYS:
                lines.append(f"{name_html} -- {shop['hours']}" if is_open else f"{name_html} - Closed")
        if not lines:
            return None

        box, layout, _ = self._make_collapsible_box("Shop Info", SHOP_INFO_COLLAPSED_KEY)
        for text in lines:
            label = QLabel(text)
            label.setOpenExternalLinks(True)
            layout.addWidget(label)
        return box

    @staticmethod
    def _hidden_counter_line(item_name: str, counter_label: str, mode: str, current: Optional[int]) -> Optional[str]:
        # Van's Favorite confirmed live (data/pointer_map.md's "Shop Purchased Item
        # Counter" section) -- Harvest Goddess Gifts/Kappa Cucumbers share this same mod-10
        # mail-reward mechanic.
        if mode == settings.HIDDEN_COUNTER_MODE_HIDDEN or current is None:
            return None
        if mode == settings.HIDDEN_COUNTER_MODE_REWARD:
            # The game itself resets the counter to 0 the moment it pays out specifically so a
            # freshly-reset counter (0 purchases since) never re-triggers the reward on its own.
            if current != 0 and current % 10 == 0:
                return f"{item_name} in Mail Tomorrow"
            return None
        # HIDDEN_COUNTER_MODE_SHOW
        return f"{counter_label} at {current}. Purchase {10 - current} more times for {item_name}."

    @staticmethod
    def _harvest_goddess_total_gifts_line(mode: str, current: Optional[int]) -> Optional[str]:
        # Cumulative, never resets -- milestone lookup against data/tables/harvest_goddess_offerings
        # .json rather than a mod-10 check (see data/pointer_map.md's "Harvest Goddess Gift
        # Counters" section).
        if mode == settings.HIDDEN_COUNTER_MODE_HIDDEN or current is None:
            return None
        if mode == settings.HIDDEN_COUNTER_MODE_REWARD:
            milestone = next((row for row in _HG_OFFERINGS if row["offerings_given"] == current), None)
            if milestone is None:
                return None
            return f"Harvest Goddess Reward Unlocked: {milestone['reward_received']}"
        # HIDDEN_COUNTER_MODE_SHOW
        next_milestone = next((row for row in _HG_OFFERINGS if row["offerings_given"] > current), None)
        if next_milestone is None:
            return f"Harvest Goddess Total Gifts at {current}. All rewards claimed."
        remaining = next_milestone["offerings_given"] - current
        return (f"Harvest Goddess Total Gifts at {current}. Gift {remaining} more time(s) for "
                f"{next_milestone['reward_received']}.")

    @staticmethod
    def _harvest_goddess_10th_gift_line(mode: str, tenth: Optional[int], total: Optional[int]) -> Optional[str]:
        if mode == settings.HIDDEN_COUNTER_MODE_HIDDEN or tenth is None or total is None:
            return None
        # What this threshold actually awards -- White Grass, or the Total Gifts milestone reward
        # if that counter also lands on one of its own milestones on this same visit (no double
        # reward). Computed the same way for both display modes so SHOW's "N more for X" text and
        # REWARD's "you got X" text never disagree with each other or with the Total Gifts line.
        upcoming = _tenth_gift_upcoming_reward(tenth, total)
        if mode == settings.HIDDEN_COUNTER_MODE_REWARD:
            if tenth == 0 or tenth % 10 != 0:
                return None
            if upcoming != _HG_TENTH_GIFT_ITEM:
                # Milestone reward supersedes White Grass -- already reported by the Total Gifts
                # reward line, don't also claim White Grass here.
                return None
            return f"{_HG_TENTH_GIFT_ITEM} Reward from the Harvest Goddess"
        # HIDDEN_COUNTER_MODE_SHOW
        return f"Harvest Goddess Gift Counter at {tenth}. Gift {10 - tenth} more time(s) for {upcoming}."

    @staticmethod
    def _kappa_cucumbers_line(mode: str, current: Optional[int], obtained: Optional[bool]) -> Optional[str]:
        # One-time reward, NOT a repeating mod-10 mechanic like Van's Favorite/the Harvest
        # Goddess's 10th Gift counter -- "no more gifts after that".
        # Confirmed live 2026-09-14: the raw counter resets to 0 the instant the reward is given,
        # so a separate permanent flag (Blue Power Berry Obtained, see
        # data/pointer_map.md's "Kappa Cucumbers Counter" section) is what actually tells "never
        # earned it" apart from "already claimed it, counter's back at 0" -- once that flag is
        # set, there's nothing left to work toward, so the whole line hides rather than showing a
        # misleading "throw N more" progress line for a reward that can never be earned again.
        # Threshold is 11, not 10 -- the counter DOES include the first
        # cucumber that just meets Kappa, it just takes one more than the other mod-10 counters.
        if mode == settings.HIDDEN_COUNTER_MODE_HIDDEN or current is None:
            return None
        if obtained:
            return None
        if mode == settings.HIDDEN_COUNTER_MODE_REWARD:
            return "Blue Power Berry from Kappa" if current == 11 else None
        # HIDDEN_COUNTER_MODE_SHOW
        return f"Kappa Cucumber Counter at {current}. Throw {11 - current} more cucumber(s) for Blue Power Berry."

    def _build_hidden_counters_section(self):
        # Gated by the "Hidden Counters" Sections Shown checkbox (via _rebuild()'s
        # section_enabled() check, same as every other builder section) plus each individual
        # counter's own "Don't Show"/"Show"/"Show when Item Reward" mode in Settings' "Hidden
        # Counters" box below.
        lines = []

        vans_favorite_line = self._hidden_counter_line(
            "Van's Favorite", "Shop Counter",
            settings.hidden_counter_mode("vans_favorite"), self._shop_counter_data.value(),
        )
        if vans_favorite_line is not None:
            lines.append(vans_favorite_line)

        total_gifts_line = self._harvest_goddess_total_gifts_line(
            settings.hidden_counter_mode("harvest_goddess_total_gifts"), self._hg_gifts_data.total(),
        )
        if total_gifts_line is not None:
            lines.append(total_gifts_line)

        tenth_gift_line = self._harvest_goddess_10th_gift_line(
            settings.hidden_counter_mode("harvest_goddess_10th_gift"),
            self._hg_gifts_data.tenth(), self._hg_gifts_data.total(),
        )
        if tenth_gift_line is not None:
            lines.append(tenth_gift_line)

        kappa_line = self._kappa_cucumbers_line(
            settings.hidden_counter_mode("kappa_cucumbers"),
            self._kappa_cucumbers_data.value(), self._kappa_cucumbers_data.obtained(),
        )
        if kappa_line is not None:
            lines.append(kappa_line)

        if not lines:
            return None
        box, layout, _ = self._make_collapsible_box("Hidden Counters", HIDDEN_COUNTERS_COLLAPSED_KEY)
        for text in lines:
            layout.addWidget(QLabel(text))
        return box

    def _build_player_notes_section(self):
        info = self._current_header()
        text = get_note(info.year, info.season, info.day)
        if not text:
            return None

        box, layout, _ = self._make_collapsible_box("Player Notes (Today)", PLAYER_NOTES_COLLAPSED_KEY)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        return box

    def _build_festival_reminders_section(self):
        info = self._current_header()
        lines = []
        for row in _FESTIVALS:
            if row["season"] != info.season:
                continue
            days_until = row["day"] - info.day
            if days_until <= 0:
                continue # the event itself is handled by the Festivals section, not here
            lead_days = settings.festival_lead_days(row["festival"])
            if not lead_days or days_until > lead_days:
                continue
            custom_text = settings.festival_prep_text(row["festival"])
            lines.append(custom_text if custom_text else
                          f"{row['festival']} is in {days_until} day(s) -- start preparing")
        if not lines:
            return None

        box, layout, _ = self._make_collapsible_box("Festival Reminders (Today)", FESTIVAL_REMINDERS_COLLAPSED_KEY)
        for text in lines:
            layout.addWidget(QLabel(text))
        return box

    @staticmethod
    def _villager_loved_gifts(villager: dict) -> str:
        return ", ".join(g for g in (villager.get("loved_forage"), villager.get("loved_ingredients")) if g)

    def _villager_birthday_text(self, villager: dict) -> str:
        # Format: "Birthday - Saibara - Loved Gifts: Bamboo Shoots"
        name_html = _hyperlink(villager["name"], villager.get("url"))
        text = f"Birthday - {name_html}"
        gifts = self._villager_loved_gifts(villager)
        if gifts:
            text += f" - Loved Gifts: {gifts}"
        return text

    def _villager_reminder_line(self, villager: dict, status: dict, mode: str) -> Optional[str]:
        # Combined into ONE line per villager, not a separate line each for
        # talk/gift -- says whichever of the two is still outstanding today, hides once both are
        # done (or once the one thing "Talk Only" cares about is done).
        name_html = _hyperlink(villager["name"], villager.get("url"))
        needs_talk = not status["talked"]
        needs_gift = mode == settings.VILLAGER_MODE_TALK_GIFT and not status["gifted"]

        if not needs_talk and not needs_gift:
            return None
        if needs_talk and needs_gift:
            text = f"Talk to {name_html} and give a gift today"
        elif needs_talk:
            text = f"Talk to {name_html} today"
        else:
            text = f"Give {name_html} a gift today"

        if needs_gift:
            gifts = self._villager_loved_gifts(villager)
            if gifts:
                text += f" (Loved Gifts: {gifts})"
        return text

    def _build_villager_reminders_section(self):
        # Birthdays merged to the top of this section -- still its own
        # independent toggle ("villager_birthdays"), separate from the per-villager talk/gift
        # reminders below ("villager_reminders"), so either can be turned off without the other.
        show_birthdays = settings.section_enabled("villager_birthdays")
        show_reminders = settings.section_enabled("villager_reminders")
        if not show_birthdays and not show_reminders:
            return None

        lines = []

        if show_birthdays:
            info = self._current_header()
            for villager in _VILLAGERS:
                if villager.get("season") != info.season or villager.get("day") != info.day:
                    continue
                text = self._villager_birthday_text(villager)
                lines.append(text)

        # Real data from the start (no mock fallback) -- unlike Header/Weather/Harvest Sprites,
        # there's no existing mock generator for ~40 villagers' talk/gift status, and this only
        # existed as a real-memory feature to begin with. Just contributes no reminder lines until
        # the game's attached (birthdays above still work either way).
        if show_reminders and self._villager_data.is_ready():
            for villager in _VILLAGERS:
                name = villager["name"]
                if "(Harvest Sprite)" in name:
                    continue
                mode = settings.villager_mode(name)
                if mode == settings.VILLAGER_MODE_HIDDEN:
                    continue
                status = self._villager_data.status_for(name)
                if status is None:
                    continue # no confirmed game id for this villager -- can't read them live yet
                line = self._villager_reminder_line(villager, status, mode)
                if line is not None:
                    lines.append(line)

        if not lines:
            return None
        box, layout, _ = self._make_collapsible_box("Villager Reminders", VILLAGER_REMINDERS_COLLAPSED_KEY)
        for text in lines:
            label = QLabel(text)
            label.setOpenExternalLinks(True)
            layout.addWidget(label)
        return box

    def _build_daily_reminders_section(self):
        # mock.daily_reminders()'s own catalog is empty now -- every reminder type it used to stand
        # in for has since moved to real data (Gotts/Saibara below, or its own real section
        # entirely -- Animals, Crop Status). Kept as a no-op passthrough rather than deleted
        # outright, in case a genuinely new day-of reminder type needs this same
        # type_key/enabled/done shape before it has real detection to back it.
        lines = [r.text for r in mock.daily_reminders()
                 if settings.type_enabled(r.type_key) and not r.done]

        # Gotts building upgrades -- real data, not one of mock.daily_reminders()'s type_key
        # entries.
        job = self._gotts_job()
        if job is not None and settings.type_enabled("building_upgrades"):
            if not job.started:
                # Purchased today, construction hasn't visibly started yet -- Days Remaining reads
                # the same as it will tomorrow (the game doesn't decrement it until the day AFTER
                # construction starts), so it isn't shown here at all.
                lines.append(f"Gotts will start building {job.building_name} Tomorrow")
            else:
                info = self._current_header()
                delayed_today = self._festival_today(info) is not None
                lines.append(self._gotts_building_text(job.building_name, job.days_remaining, delayed_today))

        # Saibara's tool-forge job -- real data, not one of mock.daily_reminders()'s type_key
        # entries (same pattern as Gotts above, 2026-09-13). Unlike Gotts, no separate "will start
        # Tomorrow" wording -- Days Remaining already reads the full total on the purchase day
        # itself, so the
        # same countdown text works day one through pickup. "Maker" items are the one exception
        # -- they're auto-delivered to the coop/barn once finished, so once
        # Days Remaining hits 0 there's nothing left to remind about; tool upgrades/accessories
        # still need an explicit Saibara visit, so those keep showing until presumably collected.
        forge_job = self._forge_job()
        if forge_job is not None and settings.type_enabled("tool_upgrades"):
            forge_done = forge_job.days_remaining <= 0
            if not (forge_done and _is_maker_item(forge_job.item_name)):
                lines.append(
                    self._forge_text(forge_job.item_name, forge_job.level_name, forge_job.days_remaining)
                )

        if not lines:
            return None

        box, layout, _ = self._make_collapsible_box("Daily Reminders", DAILY_REMINDERS_COLLAPSED_KEY)
        for text in lines:
            layout.addWidget(QLabel(text))
        return box

    def _other_villager_fp_below(self, villager_name: str, min_fp: int) -> bool:
        # Used by _heart_event_entries()'s 'other_villager_fp_min' gate (e.g. Cliff's Yellow event
        # needs Yu/Mei/Popuri each at 150+ FP) -- reuses the same live Friendship field
        # (RemindersVillagerData) every other villager-status display already reads.
        status = self._villager_data.status_for(villager_name)
        if status is None:
            return True # can't confirm -- treat the requirement as unmet rather than assume it
        return status["friendship"] < min_fp

    def _other_villager_not_met(self, villager_name: str) -> bool:
        # Used by _heart_event_entries()'s 'requires_met' gate (e.g. Elly's Black 2 needs Jeff
        # already met) -- reuses the live "met at least once" flag (+0x1B) every villager already
        # exposes through RemindersVillagerData.
        status = self._villager_data.status_for(villager_name)
        if status is None:
            return True # can't confirm -- treat the requirement as unmet rather than assume it
        return not status["met"]

    def _other_villager_lp_at_least(self, villager_name: str, max_lp: int) -> bool:
        # other_events.json's 'villager_lp_max' gate -- the mirror image of
        # _other_villager_fp_below() above: some events (e.g. Rick and Karen's Rival Events)
        # require the player's own live Love Points with a villager to still be BELOW a heart-color
        # threshold, not above one. Same live field (RemindersVillagerData) every other
        # LP-dependent display already reads.
        status = self._villager_data.status_for(villager_name)
        if status is None:
            return True # can't confirm -- treat the requirement as unmet rather than assume it
        return status["love_points"] >= max_lp

    def _other_villager_lp_below(self, villager_name: str, min_lp: int) -> bool:
        # other_events.json's 'villager_lp_min' gate -- the mirror image of
        # _other_villager_lp_at_least() above (a "must be AT LEAST this LP" gate, e.g. Elly's
        # Medical Study needs Purple Heart or higher with both Elly and Doctor). Same live field.
        status = self._villager_data.status_for(villager_name)
        if status is None:
            return True # can't confirm -- treat the requirement as unmet rather than assume it
        return status["love_points"] < min_lp

    def _heart_event_entries(self, info, weekday: str) -> list:
        # Real Heart Events, replacing mock.available_events()'s old random sample -- see that
        # function's own comment. Each candidate's real event sequence is 8 events (an uncolored
        # "meeting" event, then Black 2/Purple/Blue/Green/Yellow/Orange/Red) -- rebuilt from each
        # villager's own wiki page (data/tables/heart_events.json's own _note has the full story).
        # "Next" event per villager comes straight from the live Heart Event Triggered counter
        # (+0x3C) -- confirmed live: it's simply the 0-based index into a villager's 8 rows here,
        # no adjustment needed. A counter of 8 means every row has fired.
        if not self._villager_data.is_ready():
            return []
        weather = self._current_weather()
        by_villager: dict[str, list[dict]] = {}
        for row in _HEART_EVENTS:
            by_villager.setdefault(row["villager_name"], []).append(row)

        entries = []
        for name, rows in by_villager.items():
            if not settings.heart_event_show(name):
                continue
            status = self._villager_data.status_for(name)
            if status is None:
                continue
            index = status["heart_event_triggered"]
            if index < 0 or index >= len(rows):
                continue # every heart event already fired (or an unexpected negative reading)
            # Having seen every prior event isn't enough on its own -- the game also won't fire an
            # event until live LP reaches that color's own threshold (data/tables/heart_levels.json,
            # e.g. Purple needs 10,000+). Black 1/Black 2 both sit in the Gray/Black tier (min 0),
            # so this is a no-op gate for them; from Purple on it's real.
            required_lp = _HEART_LEVELS[max(index - 1, 0)]["min_lp"]
            if status["love_points"] < required_lp:
                continue
            event = rows[index]
            if event["trigger_type"] == "gift":
                # Green (Confession)/Red (Proposal) -- give an item whenever the player chooses,
                # no day/time/weather gate to remind about, so never shown here.
                continue
            event_name = f"{name}'s {event['color']} Heart Event"

            if event["days_mode"] == "only" and weekday not in event["days"]:
                continue
            if event["days_mode"] == "exclude" and weekday in event["days"]:
                continue
            if event["weather_required"] and weather.today != "Sunny":
                continue
            if event["season_mode"] == "only" and info.season != event["season"]:
                continue
            if event["season_mode"] == "exclude" and info.season == event["season"]:
                continue
            min_date = event["min_date"]
            if min_date is not None and game_calendar.day_index(info.year, info.season, info.day) <= \
                    game_calendar.day_index(min_date["year"], min_date["season"], min_date["day"]):
                continue
            if any(d["season"] == info.season and d["day"] == info.day for d in event["excluded_dates"]):
                continue
            if any(self._other_villager_fp_below(d["villager_name"], d["min_fp"])
                   for d in event["other_villager_fp_min"]):
                continue
            if any(self._other_villager_not_met(other_name) for other_name in event["requires_met"]):
                continue

            # "requirements" already reads as a complete display string (time/day/weather/season/
            # special all folded in by the table's own generator) -- shown as-is, no rebuilding.
            entries.append(mock.AvailableEvent(
                event_name, "", event["location"], event["requirements"], event.get("url"),
            ))
        return entries

    def _build_available_events_section(self):
        info = self._current_header()
        weekday = game_calendar.weekday_name(info.year, info.season, info.day)
        weather = self._current_weather()

        # mock.available_events() only still stands in for Harvest Goddess Prize Available (not
        # yet a real feature) -- its old Heart Event rows are replaced by the real ones below.
        events = list(mock.available_events())
        events.extend(self._heart_event_entries(info, weekday))
        for other_event in _OTHER_EVENTS:
            name = other_event["event_name"]
            if not event_settings.event_show(name) or event_settings.event_completed(name):
                continue
            if not _other_event_matches(other_event, info, weekday):
                continue
            if not _other_event_weather_ok(other_event.get("weather"), weather):
                continue
            if any(self._other_villager_fp_below(d["villager_name"], d["min_fp"])
                   for d in other_event.get("villager_fp_min", [])):
                continue
            if any(self._other_villager_lp_at_least(d["villager_name"], d["max_lp"])
                   for d in other_event.get("villager_lp_max", [])):
                continue
            if any(self._other_villager_lp_below(d["villager_name"], d["min_lp"])
                   for d in other_event.get("villager_lp_min", [])):
                continue
            if any(self._other_villager_not_met(n) for n in other_event.get("requires_met", [])):
                continue
            events.append(mock.AvailableEvent(
                name, other_event["time"], other_event["location"],
                other_event["requirements"], other_event.get("url"),
            ))

        if not events:
            return None
        box, layout, _ = self._make_collapsible_box("Available Events", AVAILABLE_EVENTS_COLLAPSED_KEY)
        for event in events:
            name_html = _hyperlink(event.name, event.url)
            # Heart events have no separate "time" (it's folded into the requirements text
            # already) -- skip the "-- time at" bit rather than show a dangling " -- at Location".
            where = f"{event.time} at {event.location}" if event.time else event.location
            # An other_event with nothing left to display as a caveat (condition_note == "", e.g.
            # Lou's Recipes once its requirements are fully live-checked) shouldn't leave a
            # trailing blank line.
            text = f"<b>{name_html}</b> -- {where}"
            if event.condition_note:
                text += f"<br>{event.condition_note}"
            label = QLabel(text)
            label.setOpenExternalLinks(True)
            layout.addWidget(label)
        return box

    def _build_future_events_section(self):
        info = self._current_header()

        # Fixed 14-day window starting tomorrow -- every date in range gets its own header below,
        # even with nothing to show ("No Events"), rather than only showing dates that have
        # something.
        window_dates = [
            game_calendar.add_days(info.year, info.season, info.day, offset)
            for offset in range(1, FUTURE_EVENTS_WINDOW_DAYS + 1)
        ]
        window_set = set(window_dates)

        events_by_date: dict[tuple, list[str]] = {}

        for event in mock.future_events():
            date_key = (event.year, event.season, event.day)
            if date_key not in window_set:
                continue # outside the 14-day window -- don't show it until it's in range
            if event.type_key is not None and not (
                settings.type_enabled(event.type_key) and settings.type_shows_in_future(event.type_key)
            ):
                continue
            events_by_date.setdefault(date_key, []).append(event.text)

        for date_key in window_dates:
            note_text = get_note(*date_key)
            if note_text:
                events_by_date.setdefault(date_key, []).append(note_text)

        for date_key in window_dates:
            year, season, day = date_key
            for villager in _VILLAGERS:
                if villager.get("season") == season and villager.get("day") == day:
                    events_by_date.setdefault(date_key, []).append(self._villager_birthday_text(villager))

        for date_key in window_dates:
            year, season, day = date_key
            for row in _FESTIVALS:
                if row["season"] == season and row["day"] == day:
                    name_html = _hyperlink(row["festival"], row.get("url"))
                    events_by_date.setdefault(date_key, []).append(
                        f"{name_html} -- {row['time']} at {row['location']}"
                    )

        # Confirmed live: Days Left decrements by 1 at the end of each day, so
        # today's reading of N means the sprite is still working for N-1 MORE days after today
        # (today itself is the "day 0" of that countdown, not part of this future-only window).
        # E.g. Days Left = 3 today -> tomorrow reads 2, the day after reads 1 (their last working
        # day), and the day after that they're free (0 = eligible to train, not "still working").
        for sprite in self._current_harvest_sprites():
            if not sprite.working or sprite.days_left <= 0:
                continue
            last_offset = min(sprite.days_left - 1, FUTURE_EVENTS_WINDOW_DAYS)
            for offset in range(1, last_offset + 1):
                remaining = sprite.days_left - offset
                if remaining <= 1:
                    text = f"{sprite.name} - {sprite.task} - Last Day"
                else:
                    text = f"{sprite.name} - {sprite.task} ({remaining}) days left"
                events_by_date.setdefault(window_dates[offset - 1], []).append(text)

        # Gotts building upgrades -- Days Remaining decrements by 1 per non-festival day, hitting
        # exactly 0 on the day the job completes (confirmed live 2026-09-13 across two separate
        # upgrades, see data/pointer_map.md's "Gotts building upgrade job tracking" section).
        # **Confirmed live 2026-09-13: a festival day freezes the countdown** (it does not
        # decrement that day) -- so this walks the window day by day rather than just subtracting
        # the offset, since a festival landing partway through pushes every later day's countdown
        # (and the completion date itself) back by one.
        job = self._gotts_job()
        if job is not None and settings.type_enabled("building_upgrades") and settings.type_shows_in_future(
            "building_upgrades"
        ):
            remaining = job.days_remaining
            for offset in range(1, FUTURE_EVENTS_WINDOW_DAYS + 1):
                if remaining <= 0:
                    break # already completed on an earlier date within this window
                date_key = window_dates[offset - 1]
                year, season, day = date_key
                is_festival = self._festival_on(season, day) is not None
                # The day right after purchase never decrements either -- construction visibly
                # starts that day but Days Remaining doesn't drop until the day after that (same
                # "one extra flat day" job.started/_build_daily_reminders_section accounts for).
                # Only applies to the FIRST day of this window, and only when today (the purchase
                # day) hasn't started construction yet.
                holds_flat = is_festival or (offset == 1 and not job.started)
                if holds_flat:
                    text = self._gotts_building_text(job.building_name, remaining, delayed=is_festival)
                else:
                    remaining -= 1
                    text = self._gotts_building_text(job.building_name, remaining, delayed=False)
                events_by_date.setdefault(date_key, []).append(text)

        # Saibara's tool-forge job -- Days Remaining decrements by 1 per day starting the day
        # after purchase (unlike Gotts, only the purchase day itself holds flat, not a second day
        # after that -- see src/reminders_forge_agent.js's derivation notes), so every day in this
        # future-only window (which starts tomorrow) is a plain decrement from today's value, with
        # no offset==1 special case needed. Whether a festival day also freezes this countdown
        # hasn't been checked live yet, so that's not assumed here the way it is for Gotts.
        forge_job = self._forge_job()
        if (
            forge_job is not None
            and settings.type_enabled("tool_upgrades")
            and settings.type_shows_in_future("tool_upgrades")
        ):
            remaining = forge_job.days_remaining
            for offset in range(1, FUTURE_EVENTS_WINDOW_DAYS + 1):
                if remaining <= 0:
                    break # already completed on an earlier date within this window
                date_key = window_dates[offset - 1]
                remaining -= 1
                text = self._forge_text(forge_job.item_name, forge_job.level_name, remaining)
                events_by_date.setdefault(date_key, []).append(text)

        # Crop harvest predictions -- real data (RemindersCropData), replacing the old mock "N
        # Turnips ready to harvest" entries (2026-09-13, confirmed live day-by-day against
        # data/tables/crop_growth.json -- see data/pointer_map.md's "Growth table" section).
        # Assumes every growing tile keeps getting watered every remaining day (a day it isn't
        # watered doesn't advance its +0xC counter at all) -- not guaranteed, just the best
        # available estimate, same caveat as every other future-day prediction in this section.
        if settings.type_enabled("crop_harvest") and settings.type_shows_in_future("crop_harvest"):
            for entry in self._crop_data.future_harvest_entries():
                if entry.day_offset > FUTURE_EVENTS_WINDOW_DAYS:
                    continue # outside this window -- don't show it until it's in range
                date_key = window_dates[entry.day_offset - 1]
                plural = "" if entry.count == 1 else "s"
                events_by_date.setdefault(date_key, []).append(
                    f"{entry.count} {entry.crop_name}{plural} ready to harvest"
                )

        box, layout, _ = self._make_collapsible_box("Future Events", FUTURE_EVENTS_COLLAPSED_KEY)
        for date_key in window_dates:
            year, season, day = date_key
            layout.addWidget(QLabel(f"<u>{game_calendar.format_date(year, season, day)}</u>"))
            texts = events_by_date.get(date_key) or ["No Events"]
            for text in texts:
                label = QLabel(text)
                label.setOpenExternalLinks(True)
                # Real indentation instead of leading "&nbsp;&nbsp;" (that was never a testing
                # leftover, just an awkward way to indent -- setIndent() is the plain-text way).
                label.setIndent(20)
                layout.addWidget(label)
        return box


register(WindowSpec("reminders", "Reminders", RemindersWidget))
