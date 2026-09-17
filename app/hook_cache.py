"""Small persistent cache for Frida hook locations that are expensive to (re-)find via a full
memory scan but cheap to validate once a candidate is known -- see mine_map.py's player-position
hook. Stored as plain JSON next to the app's other config.

Delete config/hook_cache.json to force every cached entry to be rediscovered from scratch.
"""
import json
from pathlib import Path
from typing import Optional

CACHE_DIR = Path(__file__).resolve().parent.parent / "config"
CACHE_PATH = CACHE_DIR / "hook_cache.json"


def _load() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get(key: str) -> Optional[str]:
    return _load().get(key)


def set(key: str, value: str) -> None:
    data = _load()
    data[key] = value
    CACHE_DIR.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, indent=2))
