"""Player Notes -- the player-authored notes feature: every day of the current season gets its own
always-there text box, and you page between years/seasons to see the rest.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import config, game_calendar
from .player_notes_store import get_note, set_note

MIN_YEAR = 1
ROW_HEIGHT = 60  # tall enough for a few lines -- see _NoteEdit

_GEOMETRY_KEY = "player_notes/geometry"


class _NoteEdit(QPlainTextEdit):
    """A QPlainTextEdit that emits editingFinished on focus-out, like QLineEdit does -- plain
    QPlainTextEdit has no such signal (Enter just inserts a newline, as it should for multi-line
    notes), so this is the save trigger instead.
    """
    editingFinished = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.editingFinished.emit()


class PlayerNotesDialog(QDialog):
    def __init__(self, default_year: int, default_season: str, on_changed=None, parent=None):
        super().__init__(parent)
        self._year = default_year
        self._season = default_season
        self._on_changed = on_changed
        self.setWindowTitle("Player Notes")
        self._restore_geometry()

        layout = QVBoxLayout(self)

        year_row = QHBoxLayout()
        year_prev = QPushButton("<")
        year_prev.clicked.connect(lambda: self._change_year(-1))
        year_row.addWidget(year_prev)
        self._year_label = QLabel()
        self._year_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._year_label.setStyleSheet("font-weight: bold; font-size: 13pt;")
        year_row.addWidget(self._year_label, stretch=1)
        year_next = QPushButton(">")
        year_next.clicked.connect(lambda: self._change_year(1))
        year_row.addWidget(year_next)
        layout.addLayout(year_row)

        season_row = QHBoxLayout()
        season_prev = QPushButton("<")
        season_prev.clicked.connect(lambda: self._change_season(-1))
        season_row.addWidget(season_prev)
        self._season_label = QLabel()
        self._season_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._season_label.setStyleSheet("font-weight: bold;")
        season_row.addWidget(self._season_label, stretch=1)
        season_next = QPushButton(">")
        season_next.clicked.connect(lambda: self._change_season(1))
        season_row.addWidget(season_next)
        layout.addLayout(season_row)

        self._table = QTableWidget(game_calendar.DAYS_PER_SEASON, 2)
        self._table.setHorizontalHeaderLabels(["Day", "Notes"])
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        layout.addWidget(buttons)

        self._refresh()

    def _restore_geometry(self) -> None:
        geometry = config.get_settings().value(_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(480, 640)

    def closeEvent(self, event) -> None:
        config.get_settings().setValue(_GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)

    def _change_year(self, delta: int) -> None:
        new_year = self._year + delta
        if new_year < MIN_YEAR:
            return
        # Nothing to "generate" for a newly-visited year -- storage is sparse (see
        # app/player_notes_store.py), so any day with no saved note already just reads as "".
        self._year = new_year
        self._refresh()

    def _change_season(self, delta: int) -> None:
        index = game_calendar.SEASON_ORDER.index(self._season)
        self._season = game_calendar.SEASON_ORDER[(index + delta) % len(game_calendar.SEASON_ORDER)]
        self._refresh()

    def _refresh(self) -> None:
        self._year_label.setText(f"Year {self._year}")
        self._season_label.setText(self._season)

        for day in range(1, game_calendar.DAYS_PER_SEASON + 1):
            row = day - 1
            self._table.setRowHeight(row, ROW_HEIGHT)

            weekday = game_calendar.weekday_name(self._year, self._season, day)
            day_item = QTableWidgetItem(f"Day {day} - {weekday}")
            day_item.setFlags(day_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, day_item)

            # Capture year/season/day by value at connect time, not read from self._year/_season
            # live -- this widget gets torn down (and could plausibly fire editingFinished during
            # that) the moment the year/season changes again, so a live self._year read could race
            # against the value already having moved on.
            note_box = _NoteEdit()
            note_box.setPlainText(get_note(self._year, self._season, day))
            note_box.editingFinished.connect(
                lambda box=note_box, y=self._year, s=self._season, d=day: self._save_note(y, s, d, box.toPlainText())
            )
            self._table.setCellWidget(row, 1, note_box)

    def _save_note(self, year: int, season: str, day: int, text: str) -> None:
        set_note(year, season, day, text)
        if self._on_changed:
            self._on_changed()
