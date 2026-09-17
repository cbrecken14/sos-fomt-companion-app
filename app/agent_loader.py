"""Loads a Frida agent script's source with its data tables prepended, generated on the fly as
`const` JS declarations from data/tables/*.json (see app/data_tables.py).

Agent scripts in this project are plain text handed to `session.create_script()` as one flat
script -- there's no bundler/import system, so "sharing" a table between agent scripts means
concatenating a generated declaration ahead of the consumer's own source, putting both in the same
JS execution context. Any agent script that wants a table's data should load its source through
this instead of reading its own file directly.

Each requested table's JSON keys become top-level `const` globals with those exact names, so
existing agent code that already references e.g. VILLAGER_OFFSETS/KNOWN_FIELDS doesn't need to
change -- only the literal declaration moves from hand-written to generated. Two special cases:
"enums" is exposed as one ENUMS object (its JSON keys -- SEASON, WEATHER, ... -- become
ENUMS.SEASON etc., matching every consumer's ENUMS.* usage) with `locationCalc` (logic, not data)
appended right after it; SET_GLOBALS names become real JS Sets instead of plain arrays, so their
existing .has() calls keep working.
"""
from __future__ import annotations

import json
from pathlib import Path

from .data_tables import load_table

ENUM_LOGIC_PATH = Path(__file__).resolve().parent.parent / "src" / "enum_logic.js"

# Table keys that must become a JS Set rather than a plain array when declared as a global.
SET_GLOBALS = {"MARRIAGE_CANDIDATE_IDS"}


def _declarations_for(table_name: str) -> list[str]:
    data = load_table(table_name)
    if table_name == "enums":
        return [f"const ENUMS = {json.dumps(data)};", ENUM_LOGIC_PATH.read_text()]
    declarations = []
    for global_name, value in data.items():
        if global_name in SET_GLOBALS:
            declarations.append(f"const {global_name} = new Set({json.dumps(value)});")
        else:
            declarations.append(f"const {global_name} = {json.dumps(value)};")
    return declarations


def load_agent_source(agent_path: Path, tables: tuple[str, ...] = ("enums",)) -> str:
    blocks: list[str] = []
    for table_name in tables:
        blocks.extend(_declarations_for(table_name))
    return "\n".join(blocks) + "\n" + agent_path.read_text()
