"""Single loader for every project-wide lookup/reference table, stored as JSON under
data/tables/*.json. Both plain Python code and generated Frida agent source (see
app/agent_loader.py) read tables through this instead of each hand-maintaining its own copy of the
same data.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TABLES_DIR = Path(__file__).resolve().parent.parent / "data" / "tables"


@lru_cache(maxsize=None)
def load_table(name: str) -> dict:
    # utf-8-sig strips a leading BOM if present (e.g. data/tables/recipes.json has one) and falls
    # back to plain utf-8 otherwise -- read_text()'s default encoding isn't guaranteed to be utf-8.
    return json.loads((TABLES_DIR / f"{name}.json").read_text(encoding="utf-8-sig"))
