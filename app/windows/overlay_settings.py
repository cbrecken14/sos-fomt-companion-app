"""Overlay Settings tab -- the master "Enable Overlay" switch (defaults off), a global "Lock
Overlay Windows" switch, and a list of every registered overlay window with its own
Enabled / Transparent Background toggles. See app/overlay_manager.py (owns the actual overlay
window instances and applies this state to them) and app/overlay_window.py (the base class every
overlay window subclasses).

No hotkey column yet -- per-window hotkey assignment is a planned follow-up phase.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import WindowSpec, register
from ..overlay_manager import get_overlay_manager


class OverlaySettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = get_overlay_manager()

        layout = QVBoxLayout(self)

        self._enable_checkbox = QCheckBox("Enable Overlay")
        self._enable_checkbox.setChecked(self._manager.master_enabled())
        self._enable_checkbox.toggled.connect(self._manager.set_master_enabled)
        layout.addWidget(self._enable_checkbox)

        self._lock_checkbox = QCheckBox("Lock Overlay Windows (prevent moving/resizing)")
        self._lock_checkbox.setChecked(self._manager.locked())
        self._lock_checkbox.toggled.connect(self._manager.set_locked)
        layout.addWidget(self._lock_checkbox)

        windows_box = QGroupBox("Overlay Windows")
        windows_layout = QVBoxLayout(windows_box)
        for spec in self._manager.specs():
            windows_layout.addLayout(self._build_window_row(spec.id, spec.title))
        layout.addWidget(windows_box)

        layout.addStretch(1)

    def cleanup(self) -> None:
        pass

    def _build_window_row(self, window_id: str, title: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(title))

        enabled_checkbox = QCheckBox("Enabled")
        enabled_checkbox.setChecked(self._manager.window_enabled(window_id))
        enabled_checkbox.toggled.connect(
            lambda checked, wid=window_id: self._manager.set_window_enabled(wid, checked)
        )
        row.addWidget(enabled_checkbox)

        transparent_checkbox = QCheckBox("Transparent Background")
        transparent_checkbox.setChecked(self._manager.window_transparent(window_id))
        transparent_checkbox.toggled.connect(
            lambda checked, wid=window_id: self._manager.set_window_transparent(wid, checked)
        )
        row.addWidget(transparent_checkbox)

        # Escape hatch if a drag/resize leaves this overlay off-screen or otherwise hard to grab
        # back -- puts its position/size back to the built-in default for every window mode.
        reset_button = QPushButton("Reset Position/Size")
        reset_button.clicked.connect(lambda checked=False, wid=window_id: self._manager.reset_window_geometry(wid))
        row.addWidget(reset_button)

        row.addStretch(1)
        return row


def _factory() -> OverlaySettingsWidget:
    return OverlaySettingsWidget()


register(WindowSpec("overlay_settings", "Overlay Settings", _factory))
