"""Persistent storage for Player Notes -- one free-text note per (Year, Season, Day), covering
every day of a season rather than only days the player chose to flag.

Storage is sparse (only non-empty notes are written) rather than pre-generating rows for every
day -- there's nothing to "generate" when the player cycles into a new year, since a lookup for a
day with no saved note just returns "" already. See app/player_notes_dialog.py.
"""
from __future__ import annotations

import json
from pathlib import Path

STORE_DIR = Path(__file__).resolve().parent.parent / "config"
STORE_PATH = STORE_DIR / "player_notes.json"


def _key(year: int, season: str, day: int) -> str:
    return f"{year}-{season}-{day}"


def _load() -> dict:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_note(year: int, season: str, day: int) -> str:
    return _load().get(_key(year, season, day), "")


def set_note(year: int, season: str, day: int, text: str) -> None:
    data = _load()
    key = _key(year, season, day)
    if text:
        data[key] = text
    else:
        data.pop(key, None)  # don't keep empty entries around forever
    STORE_DIR.mkdir(exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2))
