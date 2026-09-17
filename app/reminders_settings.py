"""Persisted Reminders preferences, plus the settings dialog for editing them -- see
app/windows/reminders.py.

Kinds of toggle, all persisted via config.py (same QSettings file as everything else):
- Section on/off: hide a whole section of the Reminders window (e.g. no interest in Available
  Events at all).
- Per reminder-type "also show in future days": most reminder types are day-of only; a few
  (crop harvest, blacksmith/carpenter pickup) are worth seeing a few days ahead.
- Per-shop display mode: "Don't Show" / "Show when Closed" / "Show when Open" -- lets a player
  track only the shops they actually care about (either "is it closed today" for most, or "is it
  open today" for the uncommon ones that are closed most days -- Van's Pet Shop, Beach Cafe).
- Per-festival lead days + custom prep text: how many days before a festival to start showing a
  prep reminder (0 = none), and what that reminder should say (e.g. "Don't shear your competing
  sheep!" for the Fluffy Festival) -- falls back to a generic message if left blank.
- Per-villager reminder mode: "Don't Show" / "Talk Only" / "Talk + Gift" -- lets a player pick
  which villagers they want a daily nudge for, backed by the Status field (+0x18) confirmed generic
  to every villager (including the Harvest Goddess). See Villager Reminders in
  app/windows/reminders.py -- this replaced Harvest Goddess's old spot as a plain Daily Reminders
  entry now that the real per-villager mechanism covers her too.
- Per-candidate Heart Events show/hide (2026-09-17): which of the 12 marriage candidates with a
  colored Heart Event sequence (data/tables/heart_events.json) get a row in the Reminders window's
  Heart Events section (nested under Available Events). Defaults to hidden, same reasoning as
  Villagers above -- opt in per candidate rather than flooding the section for all 12 at once.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import config
from .data_tables import load_table
from .sleep_recovery import DEFAULT_LEAD_HOURS

SECTION_KEY_PREFIX = "reminders/section_enabled/"
TYPE_ENABLED_PREFIX = "reminders/type_enabled/"
TYPE_FUTURE_PREFIX = "reminders/type_future/"
FESTIVAL_LEAD_DAYS_PREFIX = "reminders/festival_lead_days/"
FESTIVAL_PREP_TEXT_PREFIX = "reminders/festival_prep_text/"
SHOP_MODE_PREFIX = "reminders/shop_mode/"
VILLAGER_MODE_PREFIX = "reminders/villager_mode/"
HEART_EVENT_SHOW_PREFIX = "reminders/heart_event_show/"
HIDDEN_COUNTER_MODE_PREFIX = "reminders/hidden_counter_mode/"
SLEEP_REMINDER_LEAD_HOURS_KEY = "reminders/sleep_reminder_lead_hours"
SLEEP_REMINDER_FIXED_TIME_KEY = "reminders/sleep_reminder_fixed_time"

SECTIONS = [
    ("festivals", "Festivals"),
    # Both now plain SECTIONS entries -- previously self-gated by their own
    # dedicated settings box only (no "Sections Shown" checkbox at all). Bed Time Reminder's old
    # "Enabled" checkbox inside its own box is retired in favor of this one, same on/off meaning.
    ("bed_time_reminder", "Bed Time Reminder"),
    ("shop_info", "Shop Info"),
    ("hidden_counters", "Hidden Counters"),
    ("player_notes", "Player Notes (Today)"),
    ("festival_reminders", "Festival Reminders (Today)"),
    ("harvest_sprites", "Harvest Sprites"),
    ("animals", "Animals"),
    # New 2026-09-14 -- per-villager status tables, placed after Animals: Marriage
    # Candidates (Heart Color/LP + Friendship/FP + Talked/Gifted) and Villagers (the same, minus
    # Heart Color/LP) for every OTHER confirmed villager. See
    # app/windows/reminders.py's _marriage_candidate_rows()/_villager_rows().
    ("marriage_candidates", "Marriage Candidates"),
    ("villagers_status", "Villagers"),
    ("crop_status", "Crop Status"),
    # Both control the same physical "Villager Reminders" section -- kept as two independent toggles so either can be turned off
    # without the other.
    ("villager_birthdays", "Villager Birthdays (in Villager Reminders)"),
    ("villager_reminders", "Villager Reminders (Talk/Gift)"),
    ("daily_reminders", "Daily Reminders"),
    ("available_events", "Available Events"),
    ("future_events", "Future Events"),
]

# Physical section order (each entry a group of one or more SECTIONS keys that must move
# together -- villager_birthdays/villager_reminders always render as ONE physical box, so they
# share one movable slot). This is the starting default; reorderable via the Settings dialog's
# Up/Down buttons, persisted to SECTION_ORDER_KEY.
SECTION_ORDER_KEY = "reminders/section_order"
SECTION_ORDER_GROUPS: list[tuple[str,...]] = [
    ("festivals",),
    ("bed_time_reminder",),
    ("shop_info",),
    ("hidden_counters",),
    ("player_notes",),
    ("festival_reminders",),
    ("harvest_sprites",),
    ("villager_birthdays", "villager_reminders"),
    ("animals",),
    ("marriage_candidates",),
    ("villagers_status",),
    ("crop_status",),
    ("daily_reminders",),
    ("available_events",),
    ("future_events",),
]


def _group_id(group: tuple[str,...]) -> str:
    return "+".join(group)


def section_order() -> list[tuple[str,...]]:
    """Physical section order, as a list of groups (each a tuple of one or more SECTIONS keys
    that move together). Falls back to SECTION_ORDER_GROUPS for anything never saved (first run),
    and any group missing from a saved order (e.g. a newly added section in a later build) is
    inserted at its default-order position relative to the groups already placed, rather than
    just appended at the end -- so upgrading doesn't silently dump new sections at the bottom.
    """
    saved = config.get_settings().value(SECTION_ORDER_KEY)
    if not saved:
        return list(SECTION_ORDER_GROUPS)
    if isinstance(saved, str):
        saved = [saved]
    by_id = {_group_id(g): g for g in SECTION_ORDER_GROUPS}
    ordered = [by_id[gid] for gid in saved if gid in by_id]
    known_ids = {_group_id(g) for g in ordered}
    default_index_of = {_group_id(g): i for i, g in enumerate(SECTION_ORDER_GROUPS)}
    for default_index, group in enumerate(SECTION_ORDER_GROUPS):
        gid = _group_id(group)
        if gid in known_ids:
            continue
        insert_at = len(ordered)
        for i, placed in enumerate(ordered):
            if default_index_of[_group_id(placed)] > default_index:
                insert_at = i
                break
        ordered.insert(insert_at, group)
        known_ids.add(gid)
    return ordered


def set_section_order(groups: list[tuple[str,...]]) -> None:
    config.get_settings().setValue(SECTION_ORDER_KEY, [_group_id(g) for g in groups])


# (type_key, label, default "also show in future days"). Daily/reset-every-day tasks default to
# day-of only; tasks with a genuinely predictable future date default to showing ahead.
# "hg_gift" moved out: the Harvest Goddess is now just another entry in the
# generic per-villager Villager Reminders mechanism. "feed_animals"/"animal_care" moved out the
# same day too -- the new Animals section (real per-animal Fed/Talked/Brushed/Milked/Pregnant
# data) replaced what those two placeholder mock types stood in for. "water_crops" moved out
# 2026-09-13 -- replaced by the real, non-mocked Crop Status section (per-crop-type needs-water
# counts). "crop_harvest" is also real now (same day, second pass) -- RemindersCropData predicts
# "ready to harvest" day-by-day from data/tables/crop_growth.json, confirmed live against real
# elapsed days (see data/pointer_map.md's "Growth table" section); this key still gates it exactly
# like it gated the old mock version.
REMINDER_TYPES = [
    ("crop_harvest", "Crop Harvest", True),
    # Gotts' building-upgrade jobs and Saibara's tool-forge jobs (Daily Reminders' day-of line +
    # Future Events' countdown) -- real data (RemindersGottsData/RemindersForgeData), not a
    # mock.daily_reminders() type_key like the others above. Both default to shown in future
    # days since the whole point is seeing the countdown ahead of time. The old
    # combined "blacksmith" mock type (which stood in for both) is retired now that both halves
    # have real data.
    ("building_upgrades", "Building Upgrades", True),
    ("tool_upgrades", "Tool Upgrades (Forge)", True),
]

SHOP_MODE_HIDDEN = "hidden"
SHOP_MODE_CLOSED = "closed"
SHOP_MODE_OPEN = "open"
SHOP_MODE_ALWAYS = "always"
SHOP_MODE_LABELS = {
    SHOP_MODE_HIDDEN: "Don't Show",
    SHOP_MODE_CLOSED: "Show when Closed",
    SHOP_MODE_OPEN: "Show when Open",
    SHOP_MODE_ALWAYS: "Show when Open Or Closed",
}
SHOP_MODE_DEFAULT = SHOP_MODE_CLOSED

VILLAGER_MODE_HIDDEN = "hidden"
VILLAGER_MODE_TALK = "talk"
VILLAGER_MODE_TALK_GIFT = "talk_gift"
VILLAGER_MODE_LABELS = {
    VILLAGER_MODE_HIDDEN: "Don't Show",
    VILLAGER_MODE_TALK: "Talk Only",
    VILLAGER_MODE_TALK_GIFT: "Talk + Gift",
}
# Defaults to hidden -- with ~40 villagers, defaulting everyone to shown would flood the section
# immediately; opt in per villager for a nudge.
VILLAGER_MODE_DEFAULT = VILLAGER_MODE_HIDDEN

# "Hidden Counters" -- obscure internal counters the game tracks toward a
# one-time daily mail reward when they end the day divisible by 10 (Van's Favorite confirmed live;
# Harvest Goddess Gifts/Kappa Cucumbers share the same mechanic).
# See data/pointer_map.md's "Shop Purchased Item Counter (Van's Favorite mail reward)" section.
HIDDEN_COUNTER_MODE_HIDDEN = "hidden"
HIDDEN_COUNTER_MODE_SHOW = "show"
HIDDEN_COUNTER_MODE_REWARD = "show_reward"
HIDDEN_COUNTER_MODE_LABELS = {
    HIDDEN_COUNTER_MODE_HIDDEN: "Don't Show",
    HIDDEN_COUNTER_MODE_SHOW: "Show",
    HIDDEN_COUNTER_MODE_REWARD: "Show when Item Reward",
}
# Defaults to hidden -- these are obscure mechanics most players never notice; opt in per counter.
HIDDEN_COUNTER_MODE_DEFAULT = HIDDEN_COUNTER_MODE_HIDDEN
HIDDEN_COUNTERS = [
    ("vans_favorite", "Van's Favorite"),
    # Split 2026-09-14 -- originally one "Harvest Goddess Gifts" entry, but the two
    # underlying memory fields turned out to be separate counters, not duplicates of each other.
    # See data/pointer_map.md's "Harvest Goddess Gift Counters" section.
    ("harvest_goddess_total_gifts", "Harvest Goddess Total Gifts"),
    ("harvest_goddess_10th_gift", "Harvest Goddess 10th Gift"),
    ("kappa_cucumbers", "Kappa Cucumbers"),
]


def _get_bool(key: str, default: bool) -> bool:
    value = config.get_settings().value(key, default)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def section_enabled(section_key: str) -> bool:
    return _get_bool(SECTION_KEY_PREFIX + section_key, True)


def type_enabled(type_key: str) -> bool:
    return _get_bool(TYPE_ENABLED_PREFIX + type_key, True)


def type_shows_in_future(type_key: str) -> bool:
    default = next((d for k, _, d in REMINDER_TYPES if k == type_key), False)
    return _get_bool(TYPE_FUTURE_PREFIX + type_key, default)


def festival_lead_days(festival_name: str) -> int:
    return int(config.get_settings().value(FESTIVAL_LEAD_DAYS_PREFIX + festival_name, 0))


def festival_prep_text(festival_name: str) -> str:
    return str(config.get_settings().value(FESTIVAL_PREP_TEXT_PREFIX + festival_name, ""))


def shop_mode(shop_name: str) -> str:
    value = config.get_settings().value(SHOP_MODE_PREFIX + shop_name, SHOP_MODE_DEFAULT)
    return value if value in SHOP_MODE_LABELS else SHOP_MODE_DEFAULT


def villager_mode(villager_name: str) -> str:
    value = config.get_settings().value(VILLAGER_MODE_PREFIX + villager_name, VILLAGER_MODE_DEFAULT)
    return value if value in VILLAGER_MODE_LABELS else VILLAGER_MODE_DEFAULT


def hidden_counter_mode(counter_key: str) -> str:
    value = config.get_settings().value(HIDDEN_COUNTER_MODE_PREFIX + counter_key, HIDDEN_COUNTER_MODE_DEFAULT)
    return value if value in HIDDEN_COUNTER_MODE_LABELS else HIDDEN_COUNTER_MODE_DEFAULT


def heart_event_show(villager_name: str) -> bool:
    return _get_bool(HEART_EVENT_SHOW_PREFIX + villager_name, False)


# Accepts "10:00 PM", "10 PM", "10:00PM", "10PM", "22:00", or "22" -- deliberately permissive
# since this is a plain QLineEdit, not a QTimeEdit (a QTimeEdit can't be "blank", and blank needs
# to mean off). Checked longest-first so e.g. "10:00 PM" doesn't get cut short by "%H" matching
# just the "10" prefix (strptime only fails on a full mismatch, not on trailing leftover text --
# actually it DOES fail on trailing text, but ordering longest-first still avoids relying on that).
_TIME_OF_DAY_FORMATS = ["%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M", "%H"]


def parse_time_of_day(text: str) -> Optional[tuple[int, int]]:
    """(hour, minute) in 24-hour form, or None if `text` is blank/unparseable."""
    # Normalize "10:00 A.M." / "10:00 a.m." style periods and collapse extra whitespace before
    # trying to match -- accepts more real-world spellings than requiring one exact shape (a plain
    # "10 AM" needs to trigger too).
    cleaned = " ".join(text.strip().upper().replace(".", "").split())
    if not cleaned:
        return None
    for fmt in _TIME_OF_DAY_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        return parsed.hour, parsed.minute
    return None


def sleep_reminder_lead_hours() -> float:
    return float(config.get_settings().value(SLEEP_REMINDER_LEAD_HOURS_KEY, DEFAULT_LEAD_HOURS))


def sleep_reminder_fixed_time_text() -> str:
    return str(config.get_settings().value(SLEEP_REMINDER_FIXED_TIME_KEY, ""))


def sleep_reminder_fixed_time() -> Optional[tuple[int, int]]:
    """(hour, minute), or None if the "Always Show at Time" field is blank/unparseable -- the
    "only show according to Hours Before to Notify" case."""
    return parse_time_of_day(sleep_reminder_fixed_time_text())


_GEOMETRY_KEY = "reminders/settings_geometry"


class RemindersSettingsDialog(QDialog):
    def __init__(self, on_changed, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reminders Settings")
        self._restore_geometry()
        self._on_changed = on_changed

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)

        layout.addWidget(self._build_sections_box())
        layout.addWidget(self._build_sleep_reminder_box())
        layout.addWidget(self._build_types_box())
        layout.addWidget(self._build_villagers_box())
        layout.addWidget(self._build_heart_events_box())
        layout.addWidget(self._build_shops_box())
        layout.addWidget(self._build_hidden_counters_box())
        layout.addWidget(self._build_festivals_box())
        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        outer.addWidget(buttons)

    def _restore_geometry(self) -> None:
        geometry = config.get_settings().value(_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(520, 680)

    def closeEvent(self, event) -> None:
        config.get_settings().setValue(_GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)

    def _build_sections_box(self) -> QGroupBox:
        box = QGroupBox("Sections Shown")
        self._sections_layout = QVBoxLayout(box)
        self._rebuild_sections_rows()
        return box

    def _rebuild_sections_rows(self) -> None:
        # Full rebuild on every reorder (rather than moving the existing row widgets around) --
        # simplest way to keep each row's Up/Down buttons' enabled state (top/bottom of the list)
        # and closures correct without hand-rolled index bookkeeping. Cheap: at most ~13 rows.
        layout = self._sections_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        labels = dict(SECTIONS)
        groups = section_order()
        for index, group in enumerate(groups):
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)

            # Almost always one checkbox; the Villager Reminders pair (birthdays + talk/gift)
            # shares a single movable row since they're one physical section (see
            # SECTION_ORDER_GROUPS's comment).
            checkboxes_col = QVBoxLayout()
            for key in group:
                checkbox = QCheckBox(labels[key])
                checkbox.setChecked(section_enabled(key))
                checkbox.toggled.connect(
                    lambda checked, k=key: self._set_bool_and_refresh(SECTION_KEY_PREFIX + k, checked)
                )
                checkboxes_col.addWidget(checkbox)
            row.addLayout(checkboxes_col)
            row.addStretch()

            up_button = QToolButton()
            up_button.setText("▲") # up triangle
            up_button.setEnabled(index > 0)
            up_button.setToolTip("Move section up")
            up_button.clicked.connect(lambda _checked=False, i=index: self._move_section(i, -1))
            row.addWidget(up_button)

            down_button = QToolButton()
            down_button.setText("▼") # down triangle
            down_button.setEnabled(index < len(groups) - 1)
            down_button.setToolTip("Move section down")
            down_button.clicked.connect(lambda _checked=False, i=index: self._move_section(i, 1))
            row.addWidget(down_button)

            layout.addWidget(row_widget)

    def _move_section(self, index: int, direction: int) -> None:
        groups = section_order()
        target = index + direction
        if not (0 <= target < len(groups)):
            return
        groups[index], groups[target] = groups[target], groups[index]
        set_section_order(groups)
        self._rebuild_sections_rows()
        self._on_changed()

    def _build_sleep_reminder_box(self) -> QGroupBox:
        # No more "Enabled" checkbox here -- whether this section shows at all
        # is now controlled by its own "Bed Time Reminder" checkbox in "Sections Shown" above, same
        # as every other section. These two controls (lead hours / fixed time) just configure WHEN
        # it shows once that section is on, so they're always editable here regardless.
        box = QGroupBox("Bed Time Reminder")
        layout = QVBoxLayout(box)

        lead_row = QHBoxLayout()
        lead_row.addWidget(QLabel("Hours Before to Notify:"))
        lead_spin = QDoubleSpinBox()
        lead_spin.setRange(0.0, 24.0)
        lead_spin.setSingleStep(0.5)
        lead_spin.setDecimals(1)
        lead_spin.setValue(sleep_reminder_lead_hours())
        lead_spin.valueChanged.connect(
            lambda value: self._set_value_and_refresh(SLEEP_REMINDER_LEAD_HOURS_KEY, value)
        )
        lead_row.addWidget(lead_spin)
        lead_row.addStretch()
        layout.addLayout(lead_row)

        fixed_row = QHBoxLayout()
        fixed_row.addWidget(QLabel("Always Show at Time:"))
        fixed_edit = QLineEdit(sleep_reminder_fixed_time_text())
        fixed_edit.setPlaceholderText("blank = off, e.g. 10:00 PM")
        fixed_edit.editingFinished.connect(
            lambda: self._set_value_and_refresh(SLEEP_REMINDER_FIXED_TIME_KEY, fixed_edit.text())
        )
        fixed_row.addWidget(fixed_edit)
        layout.addLayout(fixed_row)

        return box

    def _build_types_box(self) -> QGroupBox:
        box = QGroupBox("Reminder Types")
        layout = QVBoxLayout(box)
        for key, label, _default_future in REMINDER_TYPES:
            row = QHBoxLayout()
            enabled_checkbox = QCheckBox(label)
            enabled_checkbox.setChecked(type_enabled(key))
            enabled_checkbox.toggled.connect(
                lambda checked, k=key: self._set_bool_and_refresh(TYPE_ENABLED_PREFIX + k, checked)
            )
            row.addWidget(enabled_checkbox)

            future_checkbox = QCheckBox("Show in future days")
            future_checkbox.setChecked(type_shows_in_future(key))
            future_checkbox.setEnabled(enabled_checkbox.isChecked())
            future_checkbox.toggled.connect(
                lambda checked, k=key: self._set_bool_and_refresh(TYPE_FUTURE_PREFIX + k, checked)
            )
            enabled_checkbox.toggled.connect(future_checkbox.setEnabled)
            row.addWidget(future_checkbox)
            layout.addLayout(row)
        return box

    def _build_shops_box(self) -> QGroupBox:
        box = QGroupBox("Shop Info")
        layout = QVBoxLayout(box)
        shops = load_table("shops")["SHOPS"]
        for shop in shops:
            name = shop["name"]
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            row.addStretch()
            combo = QComboBox()
            for mode in (SHOP_MODE_HIDDEN, SHOP_MODE_CLOSED, SHOP_MODE_OPEN, SHOP_MODE_ALWAYS):
                combo.addItem(SHOP_MODE_LABELS[mode], mode)
            combo.setCurrentIndex(combo.findData(shop_mode(name)))
            combo.currentIndexChanged.connect(
                lambda _index, c=combo, n=name: self._set_value_and_refresh(SHOP_MODE_PREFIX + n, c.currentData())
            )
            row.addWidget(combo)
            layout.addLayout(row)
        return box

    def _build_hidden_counters_box(self) -> QGroupBox:
        box = QGroupBox("Hidden Counters")
        layout = QVBoxLayout(box)
        for key, label in HIDDEN_COUNTERS:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch()
            combo = QComboBox()
            for mode in (HIDDEN_COUNTER_MODE_HIDDEN, HIDDEN_COUNTER_MODE_SHOW, HIDDEN_COUNTER_MODE_REWARD):
                combo.addItem(HIDDEN_COUNTER_MODE_LABELS[mode], mode)
            combo.setCurrentIndex(combo.findData(hidden_counter_mode(key)))
            combo.currentIndexChanged.connect(
                lambda _index, c=combo, k=key: self._set_value_and_refresh(HIDDEN_COUNTER_MODE_PREFIX + k, c.currentData())
            )
            row.addWidget(combo)
            layout.addLayout(row)
        return box

    def _build_villagers_box(self) -> QGroupBox:
        box = QGroupBox("Villagers")
        layout = QVBoxLayout(box)
        # Harvest Sprites already have their own dedicated schedule/status tracking (Harvest
        # Sprite Schedule) -- excluded here to avoid a second, redundant control surface for them.
        # Also excludes the one villager with no confirmed game id (the player's Child) -- nothing
        # to read live yet for them. Ordered by Internal ID, same convention
        # as Marriage Candidates/Villagers' own column ordering.
        villagers = sorted(
            (v for v in load_table("villagers")["VILLAGERS"]
             if v.get("id") is not None and "(Harvest Sprite)" not in v["name"]),
            key=lambda v: v["id"],
        )

        table = QTableWidget(len(villagers), 2)
        table.setHorizontalHeaderLabels(["Villager", "Reminder"])
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        for row, villager in enumerate(villagers):
            name = villager["name"]

            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, name_item)

            combo = QComboBox()
            for mode in (VILLAGER_MODE_HIDDEN, VILLAGER_MODE_TALK, VILLAGER_MODE_TALK_GIFT):
                combo.addItem(VILLAGER_MODE_LABELS[mode], mode)
            combo.setCurrentIndex(combo.findData(villager_mode(name)))
            combo.currentIndexChanged.connect(
                lambda _index, c=combo, n=name: self._set_value_and_refresh(VILLAGER_MODE_PREFIX + n, c.currentData())
            )
            table.setCellWidget(row, 1, combo)

        table.resizeRowsToContents()
        # Same reasoning as the Festival Prep table below -- fix height to content so it doesn't
        # try to scroll on its own inside the settings dialog's own scroll area.
        table.setFixedHeight(table.horizontalHeader().height()
                              + sum(table.rowHeight(r) for r in range(table.rowCount())) + 2)
        table.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(table)
        return box

    def _build_heart_events_box(self) -> QGroupBox:
        box = QGroupBox("Heart Events")
        layout = QVBoxLayout(box)
        # Only the 12 marriage candidates with a colored Heart Event sequence -- see
        # data/tables/heart_events.json's own _note. Deduped to one row per villager (each has 7
        # rows in the table, one per color), ordered by Internal ID like Marriage Candidates'
        # own column ordering.
        seen: dict[str, int] = {}
        for row in load_table("heart_events")["HEART_EVENTS"]:
            seen.setdefault(row["villager_name"], row["villager_id"])
        names = sorted(seen, key=lambda n: seen[n])

        for name in names:
            checkbox = QCheckBox(name)
            checkbox.setChecked(heart_event_show(name))
            checkbox.toggled.connect(
                lambda checked, n=name: self._set_bool_and_refresh(HEART_EVENT_SHOW_PREFIX + n, checked)
            )
            layout.addWidget(checkbox)
        return box

    def _build_festivals_box(self) -> QGroupBox:
        # A real table instead of stacked rows -- a plain QHBoxLayout per festival put the Days
        # Before spinner right after the name, so longer festival names pushed it (and the text
        # box after it) further right per row, misaligning everything.
        box = QGroupBox("Festival Prep Reminders")
        layout = QVBoxLayout(box)
        festivals = load_table("festivals")["FESTIVALS"]

        table = QTableWidget(len(festivals), 3)
        table.setHorizontalHeaderLabels(["Festival", "Days Before", "Text to Show"])
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        for row, festival in enumerate(festivals):
            name = festival["festival"]

            name_item = QTableWidgetItem(f"{name} ({festival['season']} {festival['day']})")
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, name_item)

            spin = QSpinBox()
            spin.setRange(0, 14)
            spin.setValue(festival_lead_days(name))
            spin.valueChanged.connect(
                lambda value, n=name: self._set_int_and_refresh(FESTIVAL_LEAD_DAYS_PREFIX + n, value)
            )
            table.setCellWidget(row, 1, spin)

            text_box = QLineEdit(festival_prep_text(name))
            text_box.setPlaceholderText("blank = generic message")
            text_box.editingFinished.connect(
                lambda t=text_box, n=name: self._set_value_and_refresh(FESTIVAL_PREP_TEXT_PREFIX + n, t.text())
            )
            table.setCellWidget(row, 2, text_box)

        table.resizeRowsToContents()
        # This box already lives inside the settings dialog's own scroll area -- fix this table's
        # height to its content so it doesn't also try to scroll independently (same reasoning as
        # the Harvest Sprite Schedule table in app/windows/reminders.py).
        table.setFixedHeight(table.horizontalHeader().height()
                              + sum(table.rowHeight(r) for r in range(table.rowCount())) + 2)
        table.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(table)
        return box

    def _set_bool_and_refresh(self, key: str, value: bool) -> None:
        config.get_settings().setValue(key, value)
        self._on_changed()

    def _set_int_and_refresh(self, key: str, value: int) -> None:
        config.get_settings().setValue(key, value)
        self._on_changed()

    def _set_value_and_refresh(self, key: str, value) -> None:
        config.get_settings().setValue(key, value)
        self._on_changed()
