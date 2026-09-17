"""Cheats dialog -- on/off (and, for a couple, live-adjustable) controls for every memory-WRITE
feature in this project except the Harvest Sprite Auto-Minigame and the Harvest Goddess Win
Streak, which live in the Overlay Settings tab instead (they're overlay windows as well as
cheats -- see app/overlays/harvest_sprite_minigame.py and app/overlays/harvest_goddess_streak.py).
Any feature that WRITES to game memory must ship its own on/off control here (or, for an
overlay-button write feature, its own "Enabled" checkbox in Overlay Settings), defaulting off.
Grows as more get added.

Also hosts two controls that aren't memory-write features either (neither touches game memory at
all), kept here as cheat-style on/off controls with the same default-off pattern: "Show Memory
Viewing Windows" -- a UI toggle for whether the raw testing/debugging viewer tabs (Memory Viewer,
Animal/Marriage Candidate/Villager/Harvest Sprite Viewers) appear in the "Add Window" menu -- and
"Ultrawide Window Position" -- a plain Win32 window-positioning toggle (see
app/ultrawide_window_position.py) with a Left/Right side dropdown.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import config
from .ultrawide_window_position import LEFT, RIGHT

_GEOMETRY_KEY = "cheats/geometry"

SHOW_MEMORY_VIEWING_WINDOWS_KEY = "cheats/show_memory_viewing_windows_enabled"

GUARANTEED_ITEM_SPAWN_CHEAT_ENABLED_KEY = "cheats/guaranteed_item_spawn_enabled"
COIN_SPAWN_CHEAT_ENABLED_KEY = "cheats/coin_spawn_count_enabled"
COIN_SPAWN_CHEAT_COUNT_KEY = "cheats/coin_spawn_count_value"
COIN_SPAWN_CHEAT_MIN_COUNT = 5
COIN_SPAWN_CHEAT_MAX_COUNT = 20
COIN_SPAWN_CHEAT_DEFAULT_COUNT = 20

# Animation IDs 36-42 (see data/pointer_map.md's Tired Animations section): 36/37/38/39 = Stamina
# below ~50%/20%/5%/0, 40/41 = Fatigue >= 100/160, 42 = Fatigue >= 200 (pass out). Labeled by raw ID
# for now -- swap in descriptive text once each is confirmed live.
TIRED_ANIMATION_IDS = [36, 37, 38, 39, 40, 41, 42]
TIRED_ANIMATIONS_CHEAT_ENABLED_KEY = "cheats/tired_animations_disabled_enabled"
TIRED_ANIMATION_DISABLED_KEY_TEMPLATE = "cheats/tired_animations_disabled/anim_{id}"

# Descriptive labels, confirmed live by watching each ID play with the cheat off -- anything not
# listed here just shows its raw ID.
TIRED_ANIMATION_LABELS = {
    36: "Shakes Head (~75 Stamina)",
    37: "Panting, Hands on Knees (~30 Stamina)",
    38: "Falls on Butt (~6 Stamina)",
    39: "Falls on Hands and Knees (0 Stamina)",
    40: "Standing Dizzy (~50 Fatigue)",
    41: "Falls on Butt, Dizzy (~80 Fatigue)",
    42: "Pass Out (Will prevent Doctor's Visit)",
}


def tired_animation_label(animation_id: int) -> str:
    return TIRED_ANIMATION_LABELS.get(animation_id, f"Animation {animation_id}")


ULTRAWIDE_WINDOW_POSITION_ENABLED_KEY = "cheats/ultrawide_window_position_enabled"
ULTRAWIDE_WINDOW_POSITION_SIDE_KEY = "cheats/ultrawide_window_position_side"
ULTRAWIDE_WINDOW_POSITION_DEFAULT_SIDE = LEFT
ULTRAWIDE_WINDOW_POSITION_SIDE_LABELS = {LEFT: "Left", RIGHT: "Right"}


def show_memory_viewing_windows_enabled() -> bool:
    value = config.get_settings().value(SHOW_MEMORY_VIEWING_WINDOWS_KEY, False)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def guaranteed_item_spawn_cheat_enabled() -> bool:
    value = config.get_settings().value(GUARANTEED_ITEM_SPAWN_CHEAT_ENABLED_KEY, False)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def coin_spawn_cheat_enabled() -> bool:
    value = config.get_settings().value(COIN_SPAWN_CHEAT_ENABLED_KEY, False)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def coin_spawn_cheat_count() -> int:
    return int(config.get_settings().value(COIN_SPAWN_CHEAT_COUNT_KEY, COIN_SPAWN_CHEAT_DEFAULT_COUNT))


def tired_animations_cheat_enabled() -> bool:
    value = config.get_settings().value(TIRED_ANIMATIONS_CHEAT_ENABLED_KEY, False)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def tired_animation_disabled(animation_id: int) -> bool:
    key = TIRED_ANIMATION_DISABLED_KEY_TEMPLATE.format(id=animation_id)
    value = config.get_settings().value(key, True)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def ultrawide_window_position_enabled() -> bool:
    value = config.get_settings().value(ULTRAWIDE_WINDOW_POSITION_ENABLED_KEY, False)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def ultrawide_window_position_side() -> str:
    value = config.get_settings().value(ULTRAWIDE_WINDOW_POSITION_SIDE_KEY, ULTRAWIDE_WINDOW_POSITION_DEFAULT_SIDE)
    return value if value in ULTRAWIDE_WINDOW_POSITION_SIDE_LABELS else ULTRAWIDE_WINDOW_POSITION_DEFAULT_SIDE


def disabled_tired_animation_ids() -> list[int]:
    # The master toggle gates all 7 sub-toggles: if it's off, nothing is disabled regardless of
    # which individual animations are checked (their checked state is just remembered for next
    # time the master toggle is turned back on, not acted on while it's off).
    if not tired_animations_cheat_enabled():
        return []
    return [aid for aid in TIRED_ANIMATION_IDS if tired_animation_disabled(aid)]


class CheatsDialog(QDialog):
    def __init__(
        self,
        on_show_memory_viewing_windows_toggled,
        on_guaranteed_item_spawn_cheat_toggled,
        on_coin_spawn_cheat_toggled,
        on_coin_spawn_count_changed,
        on_tired_animations_cheat_toggled,
        on_tired_animation_toggled,
        on_ultrawide_window_position_toggled,
        on_ultrawide_window_position_side_changed,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Cheats")
        self._restore_geometry()

        layout = QVBoxLayout(self)

        self._show_memory_viewing_windows_checkbox = QCheckBox("Show Memory Viewing Windows")
        self._show_memory_viewing_windows_checkbox.setChecked(show_memory_viewing_windows_enabled())
        self._show_memory_viewing_windows_checkbox.toggled.connect(
            lambda checked: self._on_show_memory_viewing_windows_toggled(checked, on_show_memory_viewing_windows_toggled)
        )
        layout.addWidget(self._show_memory_viewing_windows_checkbox)

        self._guaranteed_item_spawn_checkbox = QCheckBox("Guarantee Ground Item Spawns")
        self._guaranteed_item_spawn_checkbox.setChecked(guaranteed_item_spawn_cheat_enabled())
        self._guaranteed_item_spawn_checkbox.toggled.connect(
            lambda checked: self._on_guaranteed_item_spawn_cheat_toggled(checked, on_guaranteed_item_spawn_cheat_toggled)
        )
        layout.addWidget(self._guaranteed_item_spawn_checkbox)

        coin_spawn_row = QHBoxLayout()
        self._coin_spawn_checkbox = QCheckBox("Override Mine Coin Spawn Count")
        self._coin_spawn_checkbox.setChecked(coin_spawn_cheat_enabled())
        coin_spawn_row.addWidget(self._coin_spawn_checkbox)

        self._coin_spawn_slider = QSlider(Qt.Orientation.Horizontal)
        self._coin_spawn_slider.setMinimum(COIN_SPAWN_CHEAT_MIN_COUNT)
        self._coin_spawn_slider.setMaximum(COIN_SPAWN_CHEAT_MAX_COUNT)
        self._coin_spawn_slider.setValue(coin_spawn_cheat_count())
        self._coin_spawn_slider.setEnabled(self._coin_spawn_checkbox.isChecked())
        coin_spawn_row.addWidget(self._coin_spawn_slider)

        self._coin_spawn_readout = QLabel(str(self._coin_spawn_slider.value()))
        coin_spawn_row.addWidget(self._coin_spawn_readout)

        layout.addLayout(coin_spawn_row)

        self._coin_spawn_checkbox.toggled.connect(
            lambda checked: self._on_coin_spawn_cheat_toggled(checked, on_coin_spawn_cheat_toggled)
        )
        self._coin_spawn_checkbox.toggled.connect(self._coin_spawn_slider.setEnabled)
        self._coin_spawn_slider.valueChanged.connect(
            lambda value: self._on_coin_spawn_count_changed(value, on_coin_spawn_count_changed)
        )

        self._tired_animations_checkbox = QCheckBox("Disable Tired Animations")
        self._tired_animations_checkbox.setChecked(tired_animations_cheat_enabled())
        layout.addWidget(self._tired_animations_checkbox)

        # Indented sub-list: which specific animation(s) the cheat actually suppresses. Some
        # players may want to keep a tier or two enabled on purpose, as a visible reminder that
        # Stamina/Fatigue is getting low.
        self._tired_animation_checkboxes: dict[int, QCheckBox] = {}
        tired_animation_list = QWidget()
        tired_animation_layout = QVBoxLayout(tired_animation_list)
        tired_animation_layout.setContentsMargins(24, 0, 0, 0)
        for animation_id in TIRED_ANIMATION_IDS:
            checkbox = QCheckBox(tired_animation_label(animation_id))
            checkbox.setChecked(tired_animation_disabled(animation_id))
            checkbox.toggled.connect(
                lambda checked, aid=animation_id: self._on_tired_animation_toggled(
                    aid, checked, on_tired_animation_toggled
                )
            )
            self._tired_animation_checkboxes[animation_id] = checkbox
            tired_animation_layout.addWidget(checkbox)
        tired_animation_list.setEnabled(self._tired_animations_checkbox.isChecked())
        layout.addWidget(tired_animation_list)

        self._tired_animations_checkbox.toggled.connect(
            lambda checked: self._on_tired_animations_cheat_toggled(checked, on_tired_animations_cheat_toggled)
        )
        self._tired_animations_checkbox.toggled.connect(tired_animation_list.setEnabled)

        ultrawide_row = QHBoxLayout()
        self._ultrawide_checkbox = QCheckBox("Ultrawide Window Position")
        self._ultrawide_checkbox.setChecked(ultrawide_window_position_enabled())
        ultrawide_row.addWidget(self._ultrawide_checkbox)

        ultrawide_row.addWidget(QLabel("Dock to:"))
        self._ultrawide_side_combo = QComboBox()
        for side, label in ULTRAWIDE_WINDOW_POSITION_SIDE_LABELS.items():
            self._ultrawide_side_combo.addItem(label, side)
        current_side = ultrawide_window_position_side()
        self._ultrawide_side_combo.setCurrentIndex(self._ultrawide_side_combo.findData(current_side))
        self._ultrawide_side_combo.setEnabled(self._ultrawide_checkbox.isChecked())
        ultrawide_row.addWidget(self._ultrawide_side_combo)

        layout.addLayout(ultrawide_row)

        self._ultrawide_checkbox.toggled.connect(
            lambda checked: self._on_ultrawide_window_position_toggled(checked, on_ultrawide_window_position_toggled)
        )
        self._ultrawide_checkbox.toggled.connect(self._ultrawide_side_combo.setEnabled)
        self._ultrawide_side_combo.currentIndexChanged.connect(
            lambda index: self._on_ultrawide_window_position_side_changed(
                self._ultrawide_side_combo.itemData(index), on_ultrawide_window_position_side_changed
            )
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        layout.addWidget(buttons)

    def _restore_geometry(self) -> None:
        geometry = config.get_settings().value(_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:
        config.get_settings().setValue(_GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)

    @staticmethod
    def _on_show_memory_viewing_windows_toggled(checked: bool, callback) -> None:
        config.get_settings().setValue(SHOW_MEMORY_VIEWING_WINDOWS_KEY, checked)
        callback(checked)

    @staticmethod
    def _on_guaranteed_item_spawn_cheat_toggled(checked: bool, callback) -> None:
        config.get_settings().setValue(GUARANTEED_ITEM_SPAWN_CHEAT_ENABLED_KEY, checked)
        callback(checked)

    @staticmethod
    def _on_coin_spawn_cheat_toggled(checked: bool, callback) -> None:
        config.get_settings().setValue(COIN_SPAWN_CHEAT_ENABLED_KEY, checked)
        callback(checked)

    def _on_coin_spawn_count_changed(self, value: int, callback) -> None:
        self._coin_spawn_readout.setText(str(value))
        config.get_settings().setValue(COIN_SPAWN_CHEAT_COUNT_KEY, value)
        callback(value)

    @staticmethod
    def _on_tired_animations_cheat_toggled(checked: bool, callback) -> None:
        config.get_settings().setValue(TIRED_ANIMATIONS_CHEAT_ENABLED_KEY, checked)
        callback(checked)

    @staticmethod
    def _on_tired_animation_toggled(animation_id: int, checked: bool, callback) -> None:
        key = TIRED_ANIMATION_DISABLED_KEY_TEMPLATE.format(id=animation_id)
        config.get_settings().setValue(key, checked)
        callback(animation_id, checked)

    @staticmethod
    def _on_ultrawide_window_position_toggled(checked: bool, callback) -> None:
        config.get_settings().setValue(ULTRAWIDE_WINDOW_POSITION_ENABLED_KEY, checked)
        callback(checked)

    @staticmethod
    def _on_ultrawide_window_position_side_changed(side: str, callback) -> None:
        config.get_settings().setValue(ULTRAWIDE_WINDOW_POSITION_SIDE_KEY, side)
        callback(side)
