"""Persisted app settings: which windows were open last session, and window layout/geometry.

Backed by a plain INI file (not the Windows registry, which is Qt's default on this platform) so
it's easy to find and inspect directly.
"""
from pathlib import Path

from PySide6.QtCore import QSettings

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_PATH = CONFIG_DIR / "app_config.ini"


def get_settings() -> QSettings:
    CONFIG_DIR.mkdir(exist_ok=True)
    return QSettings(str(CONFIG_PATH), QSettings.Format.IniFormat)
