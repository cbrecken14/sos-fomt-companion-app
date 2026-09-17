"""Persisted Available Events (data/tables/other_events.json) preferences, plus the settings
dialog for editing them -- see app/windows/reminders.py.

A separate window from Reminders Settings, not folded into it -- this table is expected to grow
large as Rival/Townsfolk events keep getting added one at a time, and most of them have no live
"already happened" flag the way Heart Events do (heart_events.json reads the real Heart Event
Triggered counter, +0x3C, so it always knows what's already fired; this table can only check
today's live day/weather/season/FP/LP conditions, with no memory of whether a matching event
already played out). Every other_events.json entry gets a manual "Show" checkbox (same show/hide
idea as every other per-item toggle in this app, e.g. Villagers/Heart Events in
reminders_settings.py) and a "Completed" checkbox the player checks off once they've actually seen
an event -- a completed event stops appearing even if today's conditions still match. Both default
to their non-hidden state (Show=True, Completed=False) so a newly added event shows up immediately
without an extra opt-in step, unlike Villagers/Heart Events above which default OFF.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import config
from .data_tables import load_table

EVENT_SHOW_PREFIX = "event_settings/show/"
EVENT_COMPLETED_PREFIX = "event_settings/completed/"


def _get_bool(key: str, default: bool) -> bool:
    value = config.get_settings().value(key, default)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def event_show(event_name: str) -> bool:
    return _get_bool(EVENT_SHOW_PREFIX + event_name, True)


def event_completed(event_name: str) -> bool:
    return _get_bool(EVENT_COMPLETED_PREFIX + event_name, False)


_GEOMETRY_KEY = "event_settings/geometry"


class EventSettingsDialog(QDialog):
    def __init__(self, on_changed, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Event Settings")
        self._restore_geometry()
        self._on_changed = on_changed

        outer = QVBoxLayout(self)

        events = load_table("other_events")["OTHER_EVENTS"]
        table = QTableWidget(len(events), 3)
        table.setHorizontalHeaderLabels(["Event", "Show", "Completed"])
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for row, event in enumerate(events):
            name = event["event_name"]

            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, name_item)

            table.setCellWidget(row, 1, self._make_checkbox_cell(
                event_show(name),
                lambda checked, n=name: self._set_bool_and_refresh(EVENT_SHOW_PREFIX + n, checked),
            ))
            table.setCellWidget(row, 2, self._make_checkbox_cell(
                event_completed(name),
                lambda checked, n=name: self._set_bool_and_refresh(EVENT_COMPLETED_PREFIX + n, checked),
            ))

        table.resizeRowsToContents()
        outer.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        outer.addWidget(buttons)

    @staticmethod
    def _make_checkbox_cell(checked: bool, on_toggled) -> QWidget:
        # A bare QCheckBox dropped straight into setCellWidget left-aligns oddly against the
        # header's centered/resize-to-contents column -- wrap it so it centers like the column.
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        checkbox = QCheckBox()
        checkbox.setChecked(checked)
        checkbox.toggled.connect(on_toggled)
        layout.addWidget(checkbox)
        return cell

    def _restore_geometry(self) -> None:
        geometry = config.get_settings().value(_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(520, 480)

    def closeEvent(self, event) -> None:
        config.get_settings().setValue(_GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)

    def _set_bool_and_refresh(self, key: str, value: bool) -> None:
        config.get_settings().setValue(key, value)
        self._on_changed()
