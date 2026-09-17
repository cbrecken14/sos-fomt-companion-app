"""'Harvest Sprite Auto-Minigame' overlay -- a floating button that opens the cheat's management
dialog. Built on the OverlayWindow base class rather than its own bespoke follow timer, so it gets
follow/lock/drag/resize/transparency for free, same as every other overlay window. It's both an
overlay AND a cheat, so it lives here (Overlay Settings) rather than in app/cheats_dialog.py, same
as app/overlays/harvest_goddess_streak.py.

Per this project's hard rule for write features, defaults OFF (default_enabled=False below) and
only attaches the underlying cheat's script (app/harvest_sprite_cheat.py's
HarvestSpriteCheatController, unchanged) while THIS overlay's own "Enabled" checkbox (Overlay
Settings tab) *and* the master "Enable Overlay" switch are both on -- same set_active() gating
pattern as app/overlays/harvest_goddess_streak.py, just delegating the actual attach/detach to the
existing controller instead of duplicating it.

Only shown while the player is standing in the Harvest Sprite Hut (Location 29), same
channel-gated setVisible() override pattern as harvest_goddess_streak.py uses for the HG TV
channel.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import config
from ..data_tables import load_table
from ..harvest_sprite_cheat import HarvestSpriteCheatController
from ..overlay_registry import OverlaySpec, register
from ..overlay_window import OverlayWindow

# Reuses the Harvest Sprite Viewer's own table -- single source for sprite offsets/names and the
# skill-name strings (TASK_NAMES[0:3] == Harvest Crops/Watering/Animal Care, the same order
# confirmed live 2026-09-11 to match the training function's skill index).
_TABLE = load_table("harvest_sprite_viewer")
SKILL_NAMES = _TABLE["TASK_NAMES"][:3]
# "Don't Train" goes first so a plain skill index is combo.currentIndex() - 1; index 0 means skip.
_TRAINING_OPTIONS = ["Don't Train", *SKILL_NAMES]

# 3 music notes' worth of friendship -- matches src/harvest_sprite_minigame_agent.js's own copy of
# this threshold (also enforced there server-side; kept here too just for the Status text/eligibility).
MIN_FRIENDSHIP_TO_PLAY = 75

_COLUMNS = ["Name", "Status", "Harvest Crops XP", "Watering XP", "Animal Care XP", "Training"]
_TRAINING_COLUMN = len(_COLUMNS) - 1
_LOCKED_FLAGS_MASK = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

_DIALOG_GEOMETRY_KEY = "harvest_sprite_minigame/geometry"
_DEFAULT_DIALOG_SIZE = (1050, 480)  # roughly 3x the dialog's original auto-sized default

WINDOW_ID = "harvest_sprite_minigame"

# Not yet confirmed live -- migrated from the old button's fixed "centered horizontally, 40px below
# the game window's top edge" position, which isn't expressible as a fixed corner offset without
# knowing the game window's width. Drag/resize it into place once live, then copy the resulting
# offset/size into these constants, same as every other overlay window's confirmed default.
_DEFAULT_OFFSET = (280, 40)
_DEFAULT_SIZE = (260, 32)

_PANEL_BACKING_COLOR = QColor(30, 30, 30, 200)


class HarvestSpriteMinigameOverlay(OverlayWindow):
    def __init__(self, parent=None):
        super().__init__(
            WINDOW_ID,
            "Harvest Sprite Auto-Minigame",
            default_offset=_DEFAULT_OFFSET,
            default_size=_DEFAULT_SIZE,
            default_transparent=False,
            default_enabled=False,  # hard rule for write features -- the player opts in explicitly
            parent=parent,
        )
        self._controller = HarvestSpriteCheatController()
        self._controller.status.connect(lambda msg: print(f"[harvest sprite cheat] {msg}"))
        self._controller.location_changed.connect(self._on_location_changed)
        self._in_hut = False
        self._manager_wants_shown = False
        self._dialog: Optional[HarvestSpriteMinigameDialog] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        button = QPushButton("Auto Harvest Sprite Minigame")
        button.clicked.connect(self._open_dialog)
        layout.addWidget(button)

    # --- combined visibility: OverlayManager's enabled/master state AND standing in the Hut --------
    # Same pattern as harvest_goddess_streak.py's setVisible() override -- OverlayManager only ever
    # calls show()/hide() based on the master+per-window config; this layers the Hut-only gating on
    # top without changing overlay_manager.py at all.

    def setVisible(self, visible: bool) -> None:
        self._manager_wants_shown = visible
        super().setVisible(visible and self._in_hut)

    def _on_location_changed(self, in_hut: bool) -> None:
        self._in_hut = in_hut
        self.setVisible(self._manager_wants_shown)

    # --- write-feature gating: only attach the cheat's script while switched on ---------------------

    def set_active(self, active: bool) -> None:
        self._controller.set_enabled(active)

    def _open_dialog(self) -> None:
        if self._dialog is None:
            self._dialog = HarvestSpriteMinigameDialog(self._controller, self)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def cleanup(self) -> None:
        """Tears down both this window's follow timer (OverlayWindow.cleanup) and the underlying
        cheat controller's script (CheatController.cleanup) -- previously two separate calls the
        shell made on app shutdown (self._harvest_sprite_button.cleanup() +
        self._harvest_sprite_cheat.cleanup()); OverlayManager.cleanup() now covers both via this
        one call, same as every other overlay window."""
        super().cleanup()
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None
        self._controller.cleanup()

    # --- painting --------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        if not self._transparent:
            painter.fillRect(self.rect(), _PANEL_BACKING_COLOR)
        self._draw_resize_handle(painter)


class HarvestSpriteMinigameDialog(QDialog):
    """Lists all 7 sprites; "Complete Minigames" replicates one won round for every ELIGIBLE
    sprite at once, using whatever's picked in that row's Training dropdown. Picking "Don't Train"
    for a row excludes it from the batch even if it's otherwise eligible."""

    def __init__(self, controller: HarvestSpriteCheatController, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Harvest Sprite Auto-Minigame")
        self._controller = controller
        self._rows_by_id: dict[int, int] = {}

        self._restore_geometry()

        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self._table)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._complete_button = QPushButton("Complete Minigames")
        self._complete_button.setEnabled(False)
        self._complete_button.clicked.connect(self._on_complete_clicked)
        button_row.addWidget(self._complete_button)
        layout.addLayout(button_row)

        controller.sprite_data_updated.connect(self._on_sprite_data)

    def _restore_geometry(self) -> None:
        geometry = config.get_settings().value(_DIALOG_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(*_DEFAULT_DIALOG_SIZE)

    def closeEvent(self, event) -> None:
        config.get_settings().setValue(_DIALOG_GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)

    def _on_sprite_data(self, sprites: list) -> None:
        self._table.setRowCount(len(sprites))
        self._rows_by_id = {sprite["id"]: row for row, sprite in enumerate(sprites)}

        for row, sprite in enumerate(sprites):
            eligible = (
                sprite["daysLeft"] == 0
                and not sprite["playedToday"]
                and sprite["friendship"] >= MIN_FRIENDSHIP_TO_PLAY
            )

            if sprite["daysLeft"] != 0:
                status = f"Working ({sprite['daysLeft']} day(s) left)"
            elif sprite["friendship"] < MIN_FRIENDSHIP_TO_PLAY:
                status = f"Not friendly enough ({sprite['friendship']}/{MIN_FRIENDSHIP_TO_PLAY})"
            elif sprite["playedToday"]:
                status = "Already played today"
            else:
                status = "Ready"

            texts = [sprite["name"], status, *[str(v) for v in sprite["skills"]]]
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                flags = item.flags()
                item.setFlags((flags | _LOCKED_FLAGS_MASK) if eligible else (flags & ~_LOCKED_FLAGS_MASK))
                self._table.setItem(row, col, item)

            combo = self._table.cellWidget(row, _TRAINING_COLUMN)
            if not isinstance(combo, QComboBox):
                combo = QComboBox()
                combo.addItems(_TRAINING_OPTIONS)
                combo.setCurrentIndex(1 + max(range(3), key=lambda i: sprite["skills"][i]))
                combo.currentIndexChanged.connect(self._update_button_state)
                self._table.setCellWidget(row, _TRAINING_COLUMN, combo)
            combo.setEnabled(eligible)

        self._update_button_state()

    def _row_is_queued(self, row: int) -> bool:
        """True if this row is eligible AND not set to "Don't Train" -- i.e. it'll actually run
        when "Complete Minigames" is clicked."""
        status_item = self._table.item(row, 1)
        if status_item is None or status_item.text() != "Ready":
            return False
        combo = self._table.cellWidget(row, _TRAINING_COLUMN)
        return isinstance(combo, QComboBox) and combo.currentIndex() != 0

    def _update_button_state(self) -> None:
        self._complete_button.setEnabled(any(self._row_is_queued(row) for row in self._rows_by_id.values()))

    def _on_complete_clicked(self) -> None:
        for sprite_id, row in self._rows_by_id.items():
            if not self._row_is_queued(row):
                continue
            combo = self._table.cellWidget(row, _TRAINING_COLUMN)
            self._controller.complete_minigame(sprite_id, combo.currentIndex() - 1)
        self._complete_button.setEnabled(False)


def _factory() -> HarvestSpriteMinigameOverlay:
    return HarvestSpriteMinigameOverlay()


register(OverlaySpec(WINDOW_ID, "Harvest Sprite Auto-Minigame", _factory))
