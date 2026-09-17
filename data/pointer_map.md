# Pointer Map — STORY OF SEASONS: Friends of Mineral Town

Canonical catalog of currently-known memory addresses/offsets/enums. Current state only — facts,
no dates, no derivation narrative.

## How these addresses resolve

There is no static `module + offset` chain for any of this — the game reallocates/moves its own
objects, so a plain pointer chain doesn't survive. Every value below is an offset from a live base
address captured via Frida function hooking.

To get the live base for the Player-state struct in a running session: run
`src/save_data_reader.py` (no gameplay action needed, resolves on next save load).

## Player-state struct

Offsets from `PlayerStructBase = SaveDataBase + 0xBCE0` (see Money section). Read live by
`src/agent.js` unless noted.

| Offset | Field | Size | Notes |
|---|---|---|---|
| `+0x08` | Player name (UTF-16, null-terminated) | variable | max length unconfirmed (read as 8 chars) |
| `+0x340` | Current location/area ID | u32? | see Location enum below |
| `+0x344` | Player X position | float | |
| `+0x348` | Player Y position | float | |
| `+0x34C` | Facing direction | u32? | `0`=Down, `1`=Up, `2`=Left, `3`=Right |
| `+0x350` | Hoe EXP | u32 | |
| `+0x360` | Sickle EXP | u32 | |
| `+0x370` | Axe EXP | u32 | |
| `+0x380` | Hammer EXP | u32 | |
| `+0x390` | Watering Can EXP | u32 | |
| `+0x3A0` | Fishing Rod EXP | u32 | |
| `+0x3B0` | Power Berries Found | u8 | each raises max Stamina +15 above base 150; max value 10 (only 10 exist). Struct-local offset — Memory Viewer's own "Unknown +0xOFFSET" label bakes `+0xBCE0` into the number it shows, so don't add that offset a second time when reading from that label. |
| `+0x3B2` | Blue Power Berry Obtained (flag) | u8 (0/1) | permanent, never resets. Write site `exe+2F17E0` |
| `+0x3B6` | Current Stamina | u16 | |
| `+0x3B8` | Fatigue | u16 | raw field range vs. the tired-tier getter's `/2` output not fully resolved — doesn't affect any feature, see "Tired Reaction Animations" |
| `+0x3C8` | Spring Mine Depth Record | u16 | |
| `+0x3CA` | Winter Mine Depth Record | u16 | |
| `+0x3CC` | Mole Hit Count | u32 | |
| `+0x3F4` | Item ID currently in hand | u32? | see Item ID enum below |
| `+0x400` | Highlighted item crop quality (candidate) | u8? | not yet verified |
| `+0x450` | Selected bag slot (0-indexed, 8/row) | u16 | |
| `+0x452` | Selected bag slot, alt copy? | u16 | not confirmed whether duplicate or distinct |

Not part of this struct — captured via a separate per-conversation hook:

| Offset | Field | Notes |
|---|---|---|
| `+0x10` | Friendship Level (last person spoken with) | 4-byte |
| `+0x38` | Heart Level (last person spoken with) | |
| `+0x1A4` | Heart Level (last animal petted) | separate hook, own captured pointer |

**Current tool equipped:** one-off address `0x1D915C8C8B8` (separate memory region, not an offset
off `MoneyBasePtr`). No hook yet. Value is a Tool ID (see enum below).

**Selected tool quantity:** one-off address `0x1D9185B69BC`. Not confirmed as a fixed offset from
the tool-equipped address above. Needs its own hook.

**Open:** Tool Levels and Selected Inventory Item still unmapped.

## Current location ID enum (`+0x340`)

Canonical data lives in `data/tables/enums.json`'s `LOCATION` table (consumed via
`src/enum_logic.js`'s `locationCalc()`). Not exhaustive.

| ID | Location | ID | Location |
|---|---|---|---|
| 0 | Mother's Hill | 18 | Coop |
| 1 | Mineral Beach | 20 | Clinic |
| 2 | My Farm | 25 | Library Downstairs |
| 3 | Forest | 26 | Library Upstairs |
| 4 | Church Rear | 27 | Basil's House |
| 5 | Northside Town | 29 | Harvest Sprite Hut |
| 6 | Rose Plaza | 30 | My House |
| 7 | Southside Town | 31 | Mayor's House |
| 8 | Mother's Hill Summit | 38 | Barn |
| 9 | Secret Forest | 39 | Forge |
| | | 40 | Town Villa |
| 10 | Stable | 41 | Workshop |
| 11 | PoPoultry | 42 | Yodel Ranch |
| 13 | Church | 44 | Jennifer's Tent |
| 14 | Zack's House | 45 | Lake Mine Pond |
| 15 | Ellen's House | | |
| 16 | General Store | | |

Mine floors, two separate mines, each with its own floor-0 base (`src/enum_logic.js`'s
`locationCalc()`): Spring Mine `location = 61 + floor number`, 256 floors (`61`-`316` → "Mine
floor " + (id-61) — confirmed live at floor 190 = location 251; an earlier version of this range
only covered floors 0-60); Lake Mine `location = 317 + floor number`, 255 floors (`317`-`571` →
"Lake Mine floor " + (id-317)). Mine Map's own detection/rendering is fully mine-agnostic: `grid_base` derives from
`SaveDataBase` and floor size derives from the tile buffer's own real-data/zero-padding boundary
(see "Mine tile array" below) — neither depends on a mine-specific hook. The spawn-tile and
player-position hooks fire identically in both mines. The only mine-specific code is the location
label itself (`src/enum_logic.js`).

## Facing direction enum (`+0x34C`)

`0`=Down, `1`=Up, `2`=Left, `3`=Right

## Time/Weather struct

Offsets from `TimeBasePtr = SaveDataBase + 0x30`.

| Offset | Field | Notes |
|---|---|---|
| `-0x08` | Weather | `0`=Sunny, `1`=Rainy (not Winter), `2`=Snowy (Winter only), `3`=Typhoon, `4`=Blizzard |
| `-0x04` | Next Weather | same enum |
| `+0x00` | Year (x1) | max 6 |
| `+0x04` | Year (x7) | max 29 |
| `+0x08` | Season | `0`=Spring, `1`=Summer, `2`=Fall, `3`=Winter |
| `+0x0C` | Day | 0-indexed (day 1 reads as `0`), max 29 |
| `+0x10` | Hour | u8, max 23 |
| `+0x11` | Minute | u8, max 59, plain decimal |

Found via permanent hook on the save-load write (`exe+2FAECD`). `src/time_reader.py`/`.js` cache
the resolved address (`hook_cache.json` key `time_base_ptr`), validated against known field ranges.

## Money — SaveDataBase (a.k.a. MoneyBasePtr)

Permanent hook: `exe+94391` — `mov [rsi+0xBC50],eax`; `rsi` at this instruction **is**
`SaveDataBase` directly (Money itself is `+0xBC50`).

**Zone-transition regrab hook:** `SaveDataBase` can go stale mid-session on an ordinary zone
transition (not just a save reload). Second permanent hook: `exe+2F1850` — `mov
[rcx+0x340],edx` (the Current Location setter) — `rcx == PlayerStructBase`, so `SaveDataBase =
rcx - 0xBCE0`. Also fires on mine floor transitions, but only carries the new location ID — does
not replace Mine Map's own spawn-tile hook (grid_base and floor size are both derived from data
now, not hooks — see "Time Base / Mine Floor Base" and "Mine tile array" below).

`PlayerStructBase = SaveDataBase + 0xBCE0` — constant compiled into the game; Money and the
player-state struct are the same object. Struct is ≥74536 bytes total, most unexplored.

**Second player name copy:** `SaveDataBase + 0xBD6C`, UTF-32 (one u32 per character). Not
confirmed whether a genuinely in-sync second copy or something else that happens to hold the same
string.

## Save Header — Farm Name / Horse Name / Horse stats

Offsets from `SaveDataBase` directly.

| Offset | Field | Notes |
|---|---|---|
| `+0x48` | Farm Name | UTF-16, ~10 characters (length not confirmed as the real buffer cap) |
| `+0x1DC` | Horse X Position | float |
| `+0x1E0` | Horse Y Position | float |
| `+0x1E4` | Horse Facing Direction | `FACING` enum |
| `+0x1EC` | Horse Name | UTF-16, ~10 characters |
| `+0x374` | Horse Age | size/type assumed u32 by analogy, not independently confirmed |
| `+0x378` | Horse Daily Interaction (bitflags) | bit `0x10000` = Talked To (same convention as the animal record's `+0x18C`) |
| `+0x37C` | Horse Affection | same size caveat as Age |

Open: unidentified 4 bytes at `+0x1E8`; whistling (not talking) changes `+0x388` (`0`→`0x100`,
unconfirmed meaning) and `+0x398`/`+0x39C` (no clean interpretation) — none of the three wired
into anything yet.

## Animal (Coop/Barn) struct

`AnimalRosterBase = SaveDataBase + 0x4580` (Coop). `BarnRosterBase = SaveDataBase + 0x58C0`
(Barn). Record size `0x200` (512 bytes) per slot, both rosters. Barn Slot 1 sits `0x1340` after
Coop Slot 1 — not an exact multiple of `0x200`, so real coop capacity or a non-record-sized
header/boundary gap is unconfirmed. `ANIMAL_SLOT_COUNT = 8`, `BARN_SLOT_COUNT = 16` are both
placeholders, not confirmed real capacities.

Record layout, offsets from `Name (Secondary)` as the record's own origin (`+0x000`):

| Offset | Field | Size | Notes |
|---|---|---|---|
| `+0x000` | Name (Secondary) | UTF-16, ≤12 chars, null-terminated | holds a different string than the primary Name — purpose unconfirmed |
| `+0x084` | Animal Name (primary) | UTF-32, 8 chars, null-terminated | |
| `+0x188` | Age (Days) | u32 | |
| `+0x18C` | Daily Interaction (bitflags) | u32 | bit `0x100`=Brushed, bit `0x10000`=Talked To today (same convention the Horse reuses) |
| `+0x190` | Affection points | u32 | +1 per Talk/Brush/Feed; NOT incremented by Milk/Shear or Pregnancy |
| `+0x194` | Affection Bonus (Breeding) | u32 | base 5 hearts, breeding adds up to +5 more (10 total) |
| `+0x198` | Days Fed (Produce Threshold) | u32 | only increments on days actually fed; gates produce (cow needs 22 fed-days for milk; other species' thresholds unconfirmed) |
| `+0x19C` | Food State | u16 | bit 0=Fed Today, bit 8=Fed Yesterday |
| `+0x1B4` | Milked/Sheared (flag) | u32 | 0/1 — species-dependent meaning, see below |
| `+0x1C4` | Pregnant (flag) | u32 | 0/1 |

**Milked/Sheared is species-dependent** — identified via the Species Sub-Type Flag (`-0x017`, see
Live Data table below) plus roster location: Chicken → always N/A. Cow → gated on Fed Yesterday
AND not Pregnant. Rabbit/Sheep/Alpaca → the raw flag alone. Implemented in
`app/reminders_live_data.py`'s Reminders "Animals" section, not the generic roster reader.

Open: what `Name (Secondary)` represents; real Name buffer cap; produce-threshold for non-cow
species; real coop/barn capacity; whether barn fields match coop layout field-for-field.

## Animal Viewer (Live Data) — tracked separately by design

Same underlying record as the roster above, offsets relative to its `Name (Secondary)` origin, but
kept in its own tab/agent (`src/animal_live_viewer_agent.js`) by design, not merged into the
roster's field list.

| Offset | Field | Notes |
|---|---|---|
| `-0x017` | Species Sub-Type Flag | u8. Coop: `1`=Chicken, `0`=Rabbit. Barn: `1`=Cow, `0`=Sheep/Alpaca (Sheep and Alpaca read identically — can't be told apart by this byte) |
| `-0x014` | Location | `LOCATION` enum via `locationCalc()`, same convention as the villager/Harvest Sprite Location fields |
| `-0x010` | X Position | float |
| `-0x00C` | Y Position | float |
| `-0x008` | Facing Direction | `FACING` enum |
| `-0x004` | Animal Slot (ID) | raw incrementing ID across both rosters |
| `+0x188`-`+0x19C` | Age/Daily Interaction/Affection/Affection Bonus/Days Fed/Food State | same fields as the roster table above |
| `+0x1AC` | Minute Count of Time Outside (Today) | |
| `+0x1B0` | Minute Count of Time Outside (Total) | |
| `+0x1B4` | Milked/Sheared (flag) | |
| `+0x1BC` | Milked/Sheared Today | `0xFFFFFFFF` sentinel before first done, not a plain bool |
| `+0x1C0` | Is Outside | u16, 0/1 |
| `+0x1C2` | Unknown | u16, constant per individual, purpose unknown |
| `+0x1C4` | Pregnant (flag) | 0/1 |
| `+0x1C8` | Pregnancy Counter | +10/day, resets to 0 at birth |
| `+0x1CC` | Pregnant (Post-Sleep flag) | sets after sleeping, vs. `+0x1C4` which sets immediately on impregnation |

Open: what distinguishes Sheep from Alpaca in memory (currently indistinguishable — both shown as
"Sheep"); meaning of `+0x1C2`.

## Animal growth stages

In-game knowledge, not wiki-sourced. Feeds Reminders' Animal-type column
(`app/reminders_live_data.py`). Two inputs: `Age (Days)` (`+0x188`, always increments) and `Age
(Fed Days)`/"Days Fed" (`+0x198`, only increments when fed). Stage transition =
`fed_days >= N or age_days >= M` (whichever first); a two-stage animal's second-stage thresholds
add onto the first stage's (cumulative from birth — an assumption, not live-confirmed).

| Species | Stage 1→2 | Stage 2→3 | Notes |
|---|---|---|---|
| Chicken | Age ≥7 | n/a | pure age, feeding doesn't matter |
| Rabbit | Fed ≥10 or Age ≥15 | n/a | pregnancy (5-10 days) tracked separately via Pregnancy Counter |
| Cow | Fed ≥14 or Age ≥20 | Fed ≥23 or Age ≥34 | two baby stages |
| Sheep/Alpaca | Fed ≥14 or Age ≥20 | n/a | Alpaca shown as "Sheep" (see open question above) |
| Horse | Age ≥90 | n/a | off `SaveDataBase` directly, pure age |

## Farm tile array

`FarmTileBase = SaveDataBase + 0x78C4` — fixed compile-time offset, confirmed. Sole detection
path: `src/core_hooks_agent.js` broadcasts this (`farmGridBaseFound`) the moment `SaveDataBase` is
known, unconditionally, no shape check. Every consumer (`farm_map_agent.js`,
`app/farm_tile_debug.py`, `app/reminders_crop_data.py`) uses it as-is. `farm_map_agent.js` also
has a manual override (`setGridBase`, used by its own "Set" field and `src/farm_map.py`'s
standalone CLI, which has no shared session to receive the broadcast from).

**Tile struct fields** — grid is 1075 tiles (25 rows × 43 cols), 16 bytes/tile, address =
`FarmTileBase + row*0x2B0 + col*0x10`:

| Offset | Field | Meaning |
|---|---|---|
| `+0x0` | `state` | `0`=untilled, `1`=tilled/dry, `2`=tilled/watered |
| `+0x4` | `content` | meaning depends on `occupant` — crop-type id (0-22) if occupant is a crop stage, debris id (24-49) if occupant==8, `23`=Grass (only rendered via `state==0`, not through `occupant`) |
| `+0x8` | `occupant` | `0`=empty, `1`-`5`=crop growth stage (`5`=harvest-ready, universal for every crop with no exceptions — always check this value directly, a crop can look fully grown at stage 4 already), `8`=debris/object |
| `+0xC` | times-watered counter | single byte, +1 each time the daily growth-update tick (`exe+2D9EE0`) runs on a watered tile. Drives the growth-table lookup below. Writing it directly has no effect without a following growth-update tick — read-only for prediction purposes. Stops incrementing once a tile reaches stage 5. |
| `+0xD`-`+0xF` | independent single-byte sub-fields | `+0xD` helper-computed; `+0xE` debris flavor/yield-quality byte (`-1` while `occupant<5`, flips to `0` the day occupant hits 5 — possible quality-tier roll, unconfirmed); `+0xF` unobserved. Writing `+0xC` must be a single-byte write so these three are never touched. |

Debris ids (`occupant==8`): `24`=Weed, `25`=Stone, `26`=Branch, `27`=Wood Fence, `28`-`29`
unnamed, `30`-`49` are four 2×2 large objects (one id per corner tile).

Crop-type ids (`occupant` 1-5+): full mapping in `data/tables/crops.json`'s `id` field. Notable:
`19`=Blue Magic Red Flower and `20`=True Magic Red Flower both grow from the same seed; `22`=
Sunsweet Flower. Grass (`content==23`) always reads `occupant==5` (the same value real crops use
for harvest-ready) — must be explicitly excluded from any "needs harvest"/"needs water" check or
it's miscounted as a mature crop.

**Growth table:** static table at `exe+0x5F2AA0` (int32, 21 entries/crop,
`growthTable[cropId*21 + timesWatered]`), full data in `data/tables/crop_growth.json`. Every
confirmed crop tops out at stage 5; growth is per-crop-curve, not a flat +1/watering. Matches
`crops.json`'s `growth` field for every crop except id `22` (off by one, likely a minor wiki
inaccuracy). Out-of-season crops revert to `occupant==0` after one day — test one season at a
time.

**World position → row/col (farm):** gated on `location==2`. Bounding box X: -150.0 to 280.0,
Y: -110.0 to 140.0. Tile size 10 units/tile. `col = trunc((entityX+150)/10)`,
`row = trunc((entityY+110)/10)`. Address = `SaveDataBase + 0x78C4 + (row*43+col)*0x10`. Uses a
Live Entity's `+0x3B4`/`+0x3BC` position (see below), not the Player-state struct's
`+0x344`/`+0x348`.

## Live Entity struct

A separate object system from Save Data — every character that moves around the world (player,
NPCs, farm animals) has its own Live Entity, distinct from its Save Data counterpart. Enumerated
via a global Entity Manager: permanent hook on `exe+532A0` entry (`rcx` = Entity Manager
pointer); `+0x20`/`+0x28` = array begin/end (`std::vector<T*>` layout, 8 bytes/element).

| Offset | Field | Notes |
|---|---|---|
| `+0x000` | Vtable pointer | identifies class/role, shared per species/role — see table below |
| `+0x3B4` | X position | float |
| `+0x3B8` | (unrelated) | float, likely a Z/height component — not part of the X/Y pair |
| `+0x3BC` | Y position | float |
| `+0x3E4` | Facing-related | float, feeds a `1.57 - value` trig conversion |
| `+0xA28` | Facing reach distance | float |
| `+0xA2C` | Target tile X | float, `= X + reach*facingDirX` |
| `+0xA34` | Target tile Y | float, `= Y + reach*facingDirY` |

Vtable pointers (module-relative — add to `Process.mainModule.base`):

| Offset | Identifies |
|---|---|
| `+0x534300` | Player |
| `+0x531ED8` | Horse |
| `+0x530DA0` | Cow/Sheep (shared) |
| `+0x5315E0` | Chickens/Rabbits (shared) |
| `+0x5385E8` | Villagers/NPCs (shared) |
| `+0x538DC0` | Harvest Sprites (shared) |

Confirmed location-agnostic — the vtable-match technique and its facing-direction vector carry
over from the farm map to the mine map with zero changes.

Open: no per-individual identifier found (can't tell apart two entities sharing a vtable, e.g. two
cows); relationship between a Live Entity's position and its Save Data counterpart's position
fields is unknown. There's no dedicated tab for browsing the raw Entity Manager array anymore (an
earlier Entity Viewer tab used to build the vtable table above was dropped — it carried no info
Farm Map/Mine Map's own player-position marker didn't already cover); finding a new vtable would
need a one-off Frida script rather than a live in-app table.

## Time Base / Mine Floor Base

`TimeBasePtr = SaveDataBase + 0x30`. `MineFloorBase = SaveDataBase + 0xFB48`. Both confirmed fixed
offsets, implemented as derived paths in `src/core_hooks_agent.js`, re-derived and re-broadcast on
every zone transition (riding on `useSaveDataBase()`), not just once per session. Mine Floor Base
is the sole source of the mine tile grid's address in `src/floor_map_agent.js` (`grid_base`) —
mine-agnostic and self-correcting on every transition, shape-checked via `looksLikeMineFloor()`
before being trusted. A former floor-generation call-site hook (`exe+2E7C1D`, removed from
`floor_map_agent.js`) used to also supply this via its own RDI register, but only ever fired during
Spring Mine's floor generation, never Lake Mine's — this derived path replaced it entirely.

Time Base has no accessor hook of its own in the app's shared session — it used to (a permanent
hook on the save-load write, `exe+2FAECD`, same lifecycle moment as the Money hook below), but that
hook was redundant once the `+0x30` offset was confirmed: this project already trusts every other
fixed compile-time offset unconditionally (Farm Grid Base, RelationshipManagerBase,
`PLAYER_SUBSTRUCT_OFFSET`) with no independent hook backing it up, on the same "frozen, unpatched
build" reasoning. The derived path still shape-checks every candidate via `looksLikeTimeBase()`
before broadcasting it, so no validation was lost — and it's actually fresher than the old hook was
(it re-fires on every zone transition too, riding on `useSaveDataBase()`, not just a save reload).
The standalone `src/time_reader.py`/`.js` tool (independent of the app's shared session) still has
its own direct `exe+2FAECD` hook and its own `time_base_ptr` cache entry, untouched.

## Mine tile array / world-position conversion

Two different, unrelated formulas — don't cross-apply.

**1. Tile array indexing:** `MineTileArrayBase = SaveDataBase + 0xFB44` (4 bytes before
`MineFloorBase`). Fixed 28-column-wide, 12-byte-per-tile array —
`tileAddress = MineTileArrayBase + (row*28+col)*12`. World position → row/col for this system: a
scale constant `0.125` (`exe+534A5C`) applied to a Live Entity's `+0x3B4`/`+0x3BC` —
`col = floor((trunc(X*0.125)-2)/2)`, `row = floor((trunc(Y*0.125)-7)/2)`. Location gating:
`location-0x3D` in `0..0x1FF` (i.e. `61`-`572`).

**Real floor size (rows/cols), current method:** derived directly from the tile buffer's own data,
not from a hook — `app/windows/mine_map.py`'s `infer_floor_size()` scans the full 28x28 buffer for
the last row and last column containing any tile with nonzero `state`/`rock_content`/`soil_content`
and returns that as the real size. Confirmed live in both mines across all three known floor-size
tiers (6x13 "small", 14x13 "medium", 28x28 "large" — the same three tiers in both Spring and Lake
Mine): real tile data stops cleanly at the floor's true boundary, with genuine all-zero padding
(never touched by generation) beyond it, and no false gaps found within a real floor's own bounds.
Runs on every grid poll (~1/sec), so it self-corrects on every floor transition with no dependency
on which mine generated the floor. Replaced a floor-generation call-site hook (`exe+2E7C1D`,
R14D=cols/R15D=rows) that turned out to only fire during Spring Mine's generation — see "Time Base
/ Mine Floor Base" above.

**2. The mine map's own player-position dot** reads the player's Live Entity `+0x3B4`/`+0x3BC`
(the same fields formula 1 above uses), not `floor_map_agent.js`'s older write-hook/poll position
struct (`X_WRITE_OFFSET`) — that older hook is still present and still hooked (kept only for
`src/floor_map.py`, the frozen Tkinter fallback) but its messages are ignored by `mine_map.py`.
Switched because the write-hook source only updated once the player actually took a step, so a
freshly loaded floor's dot didn't anchor until movement happened. The Live Entity source reflects
the rendered position continuously instead:
- `WORLD_UNITS_PER_TILE = 10.0`
- No universal fixed origin — different mine room instances sit at different points in the game's
  own world space
- Origin auto-anchored per floor from the spawn tile (`SPAWN_TILE_OFFSET = 0x2e623a`); resets to
  "unknown" (hides the dot) the instant a new floor is detected, to avoid a stale jump before the
  new anchor resolves
- The anchor's entity-position sample is requested from `MineMapWidget._on_player_location`
  (`GameSession.player_location_changed`), NOT from the `spawnTile` handler itself (changed
  2026-09-16 — see below for why) — `requestEntityPosition` only fires once relocation is
  confirmed done, and only when a spawn tile is actually pending
- **Anchor race, resolved in two steps, both confirmed live:** (1) first version requested the
  entity-position sample the moment `spawnTile` fired — the periodic position poll could otherwise
  race ahead of a floor's own `spawnTile` detection and pair the CURRENT floor's real position with
  the PREVIOUS floor's still-unconsumed spawn tile (confirmed across 3 consecutive floors). (2)
  that fix itself had a further bug, seen only on the largest floor size: floor generation (which
  fires `spawnTile`) completes BEFORE the game actually relocates the player and updates location —
  confirmed live via console ordering on a floor 19→20 transition, `spawnTile`/`dot anchored` both
  printed before `area transition hook processed`/the location broadcast — so requesting the
  position at `spawnTile` time could still sample the PREVIOUS floor's leftover coordinates.
  `player_location_changed` is the one signal confirmed to fire only after relocation actually
  happens, which is why the request moved there.
- **Mine Floor 0** (location `61`) has no ladder entrance — a plain area-transition from Mother's
  Hill — and that transition type is unreliable for the anchor above (two separate fresh-launch
  spawn-tile readings for Floor 0 disagreed with each other, `row 2 col 7` vs `row 3 col 5`,
  meaning neither reflected the real value). Floor 0 is otherwise a fixed layout: always the small
  map, `floor_rows=6`/`floor_cols=13`, player always spawns at the bottom-center tile (`row 5`,
  `col 6`), confirmed live at `World X=0.0`/`World Y=30.0` at that tile. Hardcoded in
  `mine_map.py` (`MINE_FLOOR_ZERO_*` constants) and applied the instant `GameSession`'s
  `player_location_changed` (broadcasts the raw player Location id, `core_hooks_agent.js`) reports
  `61`, bypassing the dynamic spawn-tile/entity-position pipeline for that one floor entirely.

The player-position marker's facing-direction technique (Entity Manager + player-vtable match) is
confirmed location-agnostic, reused unchanged from the farm map.

### Mine tile struct fields (`rock_content`/`soil_content`) and Floor Contents

Each 12-byte tile (see formula 1 above) is `state` (+0), `rock_content` (+4), `soil_content` (+8),
all `int32`. `state==4` is an unmined rock; `rock_content` is the ore inside it (`0`=Empty Rock,
not an item). Any other `state` (except the ladder/exit/hole ones below) exposes `soil_content`
directly as a ground item (`0`=none, `1`=the floor's own hidden/found ladder — not a collectible,
has its own dedicated badge). Critically, `soil_content` is meaningful even while `state==4` — it's
what gets revealed once the rock is broken, readable in memory the whole time regardless of whether
anything on screen indicates it. `state` in `{5,6}` is a pit ("X" marker, uncovered/hidden) — a
separate tile-state value, never combined with `state==4`, so a pit has no covered/uncovered
distinction the way a ground item does. Known `rock_content`/`soil_content` id → name/icon/sell
price mappings live in `data/tables/mine_map.json` (`ORE_NAMES`/`SOIL_NAMES`/`ORE_SELL_PRICES`/
`SOIL_SELL_PRICES`/`ORE_ICONS`/`SOIL_ICONS`), sourced from fogu.com — single source of truth, not
duplicated here; extend that table when a new id is identified (most recently: Lake Mine's gem ids
19/20/24-31/35-39, plus 21 Pink Diamond/22 Alexandrite/33 Kappa Jewel, all confirmed live
2026-09-16 through 2026-09-18; and, matching Special Floors' own item list below, ore id 10 Travel
Stone and soil ids 34 Tomatosetta Stone/40 Kappa Statue/41 Goddess Statue/42 Nature Sprite Statue —
the last three are the game's only three mine statues, all now identified). No sell price for any
of the three statues, Tomatosetta Stone, or Travel Stone — special/trophy items, not shippable. Of
every item Special Floors tracks (see below), only the 6 Cursed Tools remain unidentified —
no known content id for any of them yet.

Mine Map's Floor Contents panel (`app/windows/mine_map.py`) tallies every tile's
ore/covered-ground-item/exposed-ground-item/pit into a live-updating list, entirely from data
already being read for the visual grid — no additional hooks. An id not in the JSON table still
shows (as "Unknown Ore (#N)"/"Unknown Item (#N)") rather than being dropped, which is how new ids
get found in the first place. Per-item Settings (Show / Flash Covered [ground items only] / Flash
Red, plus Up/Down reordering, two sections split Ores vs. Ground Items) live in the Mine Map's own
Floor Contents Settings dialog.

### Special Floors mine/floor derivation

Current mine (Spring vs. Lake) + floor number, for the Special Floors feature
(`app/windows/mine_map.py`'s `mine_and_floor_from_location()`), is derived from
`GameSession.player_location_changed` alone — no new hook. Spring: `location - 61`, valid for
location `61`-`316` (floor `0`-`255`). Lake: `location - 317`, valid for location `317`-`572` (floor
`0`-`255`). Both ranges are wider than this file's own "Current location ID enum" section
currently documents as confirmed (Spring `61`-`121`, i.e. floor `0`-`60` only; Lake `317`-`571`, floor
`0`-`254`) — the wider bounds here are inferred from `data/tables/mine_floor_spawns.json`'s own
highest listed floor (`255`) for each mine, not independently verified live. Lake specifically has
an unresolved 1-floor question: `317 + 255 = 572`, one past that section's documented `571` — either
the wiki's "floor 255" entries are actually memory floor `254`, or the documented upper bound is one
short. Not yet reconciled either way; re-check live (a Lake Mine floor deep enough to reach either
boundary) before trusting a Special Floors alert on the highest few floors of either mine.

## NPC/Villager relationship struct

`RelationshipManagerBase = SaveDataBase + 0xCACC`. `VillagerStruct(id) = RelationshipManagerBase +
offset(id)` — a flat, per-ID offset from this one base. ID column below is the game's own
confirmed Internal ID (field `+0x1C`, see field table below), not array position. Row order below
is array-position order (used by `computeWindowSize`-style per-column bounds in code).

| Internal ID | Villager | Offset from `RelationshipManagerBase` |
|---|---|---|
| (none — special/conditional case; excluded from the Villagers/Marriage Candidates tabs, likely not a standard character) | | `+0x02C` |
| 1 | Lillia | `+0x294` |
| 2 | Rick | `+0x2CC` |
| 3 | Popuri | `+0x31C` |
| 4 | Mugi | `+0x36C` |
| 5 | Mei | `+0x3A4` |
| 6 | Saibara | `+0x3DC` |
| 7 | Gray | `+0x434` |
| 8 | Duke | `+0x484` |
| 9 | Manna | `+0x4BC` |
| 10 | Basil | `+0x4F4` |
| 11 | Anna | `+0x52C` |
| 12 | Marie | `+0x564` |
| 13 | Thomas | `+0x5B4` |
| 14 | Harris | `+0x5EC` |
| 15 | Ellen | `+0x624` |
| 16 | Yu | `+0x65C` |
| 17 | Jeff | `+0x694` |
| 18 | Sasha | `+0x6CC` |
| 19 | Karen | `+0x704` |
| 20 | Doctor | `+0x754` |
| 21 | Elly | `+0x7A4` |
| 22 | Carter | `+0x7F4` |
| 23 | Cliff | `+0x82C` |
| 24 | Dudley | `+0x87C` |
| 25 | Ran | `+0x8B4` |
| 26 | Kai | `+0x904` |
| 27 | Gotts | `+0x954` |
| 28 | Zach | `+0x998` |
| 29 | Huang | `+0x9D0` |
| 30 | Bon Vivant | `+0xA20` |
| 31 | Harvest Goddess | `+0xA70` |
| 32 | Kappa | `+0xAC0` |
| 33 | Van | `+0xB10` |
| 34 | Lou | `+0xB4C` |
| 46 | Blueberry (Harvest Sprite) | `+0xC24` |
| 47 | Pumpkin (Harvest Sprite) | `+0xC74` |
| 48 | Plum (Harvest Sprite) | `+0xCC4` |
| 49 | Cherry (Harvest Sprite) | `+0xD14` |
| 50 | Aqua (Harvest Sprite) | `+0xD64` |
| 51 | Sunny (Harvest Sprite) | `+0xDB4` |
| 52 | Mint (Harvest Sprite) | `+0xE04` |
| 37 | Brandon | `+0xB84` |
| 38 | Jennifer | `+0xBD4` |

**Confirmed field offsets within a villager record:**

| Offset | Field | Notes |
|---|---|---|
| `+0x00` | Current location | u32, `LOCATION` enum |
| `+0x04` | Spawn X | float — the position given on entering the current Location, not live-updated while stationary |
| `+0x08` | Spawn Y | float |
| `+0x0C` | Spawn Facing Direction | `FACING` enum |
| `+0x10` | Friendship | 4-byte value |
| `+0x14` | Event Stage | u32. Drops to `0` after a significant interaction, baseline varies by character (`0`/`2`/`3`) — may be an index into a queued-event list, consumed on firing. Not fully understood. |
| `+0x18`/`+0x19` | Talked To Today | `+0x19` reverts to 0 on leaving the location while `+0x18` persists — `+0x18` reads as "talked to today," `+0x19` as "actively in a conversation/scene right now" |
| `+0x1A` | Gift Given Today | |
| `+0x1B` | Met At Least Once | permanent flag, never resets |
| `+0x1C` | Character ID | 2 bytes, stable per-villager ID — this is the ID column above |
| `+0x34` | Event Active State | 2 bytes, nonzero during the post-cutscene dialogue state, `0` otherwise. Specific magnitude not understood. |
| `+0x36` | Unknown, per-character constant | 2 bytes, stable per character, differs between characters — not a working theory yet |
| `+0x38` | Love Points (LP) | 4 bytes, marriage candidates only. Rival/Heart Events award flat amounts (e.g. confirmed +3000 on a Black Heart Event). Drives `data/tables/heart_levels.json`'s LP-to-Heart-Color tiers |
| `+0x3C` | Heart Event Triggered | u32, increments per event (not a one-time flag), commits when leaving the location rather than at the cutscene's visual end. Confirmed live for Popuri 2026-09-18: starts at 0, becomes 1 once the (uncolored, 0 LP) meeting event fires, then increments by exactly 1 per further event in order — used as a straight 0-based index into that candidate's 8 `data/tables/heart_events.json` rows. Backs Reminders' Heart Events section via `src/reminders_villagers_agent.js` |
| `+0x40`/`+0x44`/`+0x48` | Unknown, candidate-only | 3× 4-byte fields, move around Rival Events but not confirmed to represent Rival Event completion itself — treat as an open lead only |

**Struct size varies by character role** — marriage candidates carry extra courtship/proposal
state, Saibara carries extra tool-upgrade state, Gotts carries extra building-upgrade state (see
its own section below); this is why consecutive record offsets range widely (~0x38 to ~0xD8
apart).

Open: the first table row (special/conditional case) isn't a standard villager; two candidate
offsets (`+0x60`, `+0x88`) found for Popuri, reproduced across a restart, not reconciled against
`+0x10`'s Friendship. `+0x3C` itself is now confirmed live for Popuri specifically (2026-09-18, see
its own row above) — the old open question about whether it held the same meaning for her was
resolved by that test. `+0x14` (Event Stage) remains an unconfirmed Heart Event candidate for
Popuri (role-specific — for Gotts these same two offsets are his confirmed Days Remaining (`+0x3C`)
and Days Elapsed (`+0x14`) fields below, expected since it's the same struct template used
differently per role).

**`+0x18`-`+0x1B` generalized from the Harvest Sprite struct's Status field** — same packed layout
(Talked To Today/Gift Given Today/Met At Least Once) confirmed across every regular villager and
the Harvest Goddess, not sprite-specific. Backs Reminders' "Villager Reminders" section via
`src/reminders_villagers_agent.js`; Met At Least Once (`+0x1B`) additionally backs Heart Events'
"already met X" prerequisite gates (2026-09-18, e.g. Elly's Black 2 needing Jeff already met) —
same field, just newly exposed through that agent.

## Gotts building upgrade job tracking

Gotts (Internal ID 27, `RelationshipManagerBase + 0x954`) carries carpenter-specific fields beyond
the generic villager layout above.

| Offset | Field | Notes |
|---|---|---|
| `+0x38` | Job Type / Building ID | u32. `0`=no job. `1`=House Upgrade (any tier, not just the first), `2`=Coop Upgrade, `3`=Barn Upgrade, `4`=Town Villa, `23`=Waterwheel, `24`=Silo — see `BUILDING` enum in `data/tables/enums.json`. `app/reminders_live_data.py`'s `_building_name()` prints the raw ID for anything unnamed. |
| `+0x3C` | Days Remaining | u32, counts down cleanly to `0` on completion day. Does NOT decrement on the purchase day, the day after (construction start), or on a festival day. |
| `+0x24` | Activity/Schedule State | u32. `1` = idle OR purchase day (job set, construction not yet visibly started); a job-type-specific nonzero value once construction is under way (`8`=Coop, `6`=House); `4` on the completion day. The only field that distinguishes "just purchased" from "day 1 of construction" — `started = (this != 1)`. |
| `+0x14` | Days Elapsed | u32, `0` on day 1, incrementing daily. Not currently used by the app. |

Wired into Reminders (`app/windows/reminders.py`): Shop Info override, Daily Reminders line,
14-day Future Events countdown (walked day-by-day to account for the purchase-day hold and
festival freezes) — all gated on Settings' "Building Upgrades" reminder type.

Also observed, not wired into anything (lower confidence): `+0x00` (idle-vs-job-type-specific
value, redundant with `+0x24`); `+0x04`/`+0x08` (two floats, guessed to be Gotts' own X/Y — build
site vs. shop); `+0x0C` (flag, inconsistent between the two jobs observed so far).

## Harvest Sprite struct

The 7 Harvest Sprites (Internal IDs 46-52) share the same `RelationshipManagerBase + offset(id)`
addressing as villagers. Friendship (`+0x10`) is shared with the villager layout; Heart (`+0x38`)
and Location (`+0x50`) are not.

| Offset | Field | Notes |
|---|---|---|
| `+0x10` | Friendship | `75` (3 music notes in-game) is the minimum needed before a sprite will play its training minigame |
| `+0x18` | Status (packed flags, 4 bytes) | top byte constant `01` (assigned/active); 3rd byte = Gift Given (0/1); bottom two bytes both track Talked To. Same layout every regular villager (and the Harvest Goddess) uses. |
| `+0x1C` | Sprite ID (species identity) | `46`=Blueberry, `47`=Pumpkin, `48`=Plum, `49`=Cherry, `50`=Aqua, `51`=Sunny, `52`=Mint. Read width not confirmed beyond 1 byte — don't widen the live read without checking the setter first. |
| `+0x38`-`+0x3A` | Skill Levels (1 byte each, index 0/1/2) | 0-255 clamped. `0`=Harvest Crops, `1`=Watering, `2`=Animal Care |
| `+0x3B`-`+0x3D` | Streak (1 byte each, same index) | 0-31 clamped, consecutive-win counter; a loss does not reset it, only stops it growing |
| `+0x40` | Task Assignment | `0`=Harvest Crops, `1`=Watering, `2`=Animal Care, `3`=Not Assigned |
| `+0x44` | Days Left | **1 byte**, not 4 — confirmed via the setter's own `mov [rcx+44],r8b` |
| `+0x45` | Played Today (flag) | 1 byte, set to `1` on completion regardless of win/loss |

**Training minigame mechanics** (`exe+2F2F90`): on completing a round — sets `+0x45`=1
unconditionally; a win increments that skill's Streak byte (capped 31); points looked up from the
streak: `0-5`→1, `6-10`→2, `11-16`→3, `17-31`→4; win adds those points to the Skill Level byte
(clamped 0-255), a loss subtracts them (streak used as-is, not reset first). "Already played
today" check (`exe+3650E4`) resolves the sprite and returns `+0x45` as a bool.

## Harvest Goddess Gift Counters

Two independent counters that usually move together but track different things — not duplicates.

**Total Gifts (cumulative, never resets):** `SaveDataBase + 0xE664` (write site `exe+35E150`).
Matches `data/tables/harvest_goddess_offerings.json`'s milestones (10/20/30/50/75/100/150).

**10th Gift (counts to 10, then resets):** `SaveDataBase + 0xE60C` (write site `exe+35DFA0`).
Every 10th gift awards a plain White Grass — except on a visit where Total Gifts also lands on one
of its own milestones that's a multiple of 10 (10/20/30/50/100/150; 75 never collides), in which
case the milestone reward is given instead, no double reward.
`app/windows/reminders.py`'s `_tenth_gift_upcoming_reward()` computes the true upcoming reward
once so every display mode agrees.

Giving a gift to a regular villager does not touch either counter. Both back the Reminders
"Hidden Counters" section.

## Shop Purchased Item Counter (Van's Favorite mail reward)

`SaveDataBase + 0xE65C`. Tracks purchases at any shop, incrementing once per closed store
interface (not once per item bought). Checked at bedtime: divisible by 10 → Van's Favorite
(Item ID `333`) placed in the mail, counter resets to `0`. Reset write: `exe+35E12C` — shares a
dispatch block with the Harvest Goddess Total Gifts write above; `+0xE654`/`+0xE658`/`+0xE660` are
unmapped neighbors in the same block, not confirmed to be "other shops."

A separate General-Store-specific tally exists at `RelationshipManagerBase + 0x4`
(`SaveDataBase + 0xCAD0`, write site `exe+2F7023`) — increments per single purchase rather than
per closed window, unrelated purpose unconfirmed.

Not yet wired into the app.

## Kappa Cucumbers Counter (Blue Power Berry reward)

`SaveDataBase + 0xE614`. Tracks cucumbers thrown to Kappa — threshold is **11** (meeting Kappa the
first time costs 1 cucumber and is counted). Resets to `0` the instant the one-time Blue Power
Berry is awarded; the permanent claim record is `PlayerStructBase + 0x3B2` (Blue Power Berry
Obtained). `app/reminders_live_data.py` reads both; Reminders hides the Kappa Cucumbers line once
that flag is set, rather than showing progress toward a reward that can't be earned again. Write
instruction not traced — only address, reset behavior, and the 11-threshold are confirmed.

## Harvest Goddess High-Low minigame (TV channel)

The in-game TV runs a shared "load and interpret a script" system for every channel. Channel
dispatch: `exe+352A30` — `edx` = channel/program index, `rcx` = the interpreter's own `this`
(same object the reveal-write hook uses). `0x5EA` = HG High-Low game; `0x5FA`=weather;
`0x5EC`=education; `0x5F5`=entertainment; `0x219`=fires on closing any channel. Detect "HG game
active": hook this, `edx==0x5EA`.

**Win streak:** slot `r10==3` in the same 100-slot reveal/history array the number-reveal hook
uses (`self+0x1E8`). The game resets this slot to `0` itself immediately on channel open — the
cheat intercepts that exact write and substitutes the chosen streak; a second direct-write path
covers changing the dropdown selection while already inside the channel, after that reset has
already fired. `r10==1` → `self+0x1E0` is "last number revealed."

Winning a prize (cashing out) does NOT fire the channel-close hook (`edx==0x219` never fires) —
unmapped code path. Overlay visibility falls back to a ~20s reveal-write-activity idle timeout
instead (doesn't affect the streak-tracking/override logic itself, which stays gated purely on the
exact channel-open/close hook).

Note: Cheat Engine displays hex with no `0x` prefix when a value happens to only contain digits
0-9 — a decimal-looking value copied from CE can actually be hex. Verify the format explicitly
when in doubt.

## Tired Reaction Animations (Stamina/Fatigue tiers)

Backs the Tired Animations cheat. Reaction/tier ID: a 4-byte value at `[reactionObj+0x15C]`,
reached via `[exe+780EC8] -> +0x22B0 -> call exe+301030`. `0`=no reaction; `36`-`42`=one specific
tired reaction:

| ID | Trigger | Animation |
|---|---|---|
| 36 | Stamina below ~50% of max (~75 @ 150 max) | Shakes Head |
| 37 | Stamina below ~20% (~30) | Panting, Hands on Knees |
| 38 | Stamina below ~5% (~6) | Falls on Butt |
| 39 | Stamina == 0 | Falls on Hands and Knees |
| 40 | Fatigue ≥ ~100 (getter reads Fatigue/2==50) | Standing Dizzy |
| 41 | Fatigue ≥ ~160 (getter reads /2==80) | Falls on Butt, Dizzy |
| 42 | Fatigue ≥ ~200 (getter reads /2==100) | Pass Out (triggers Doctor's Visit) |

Open: whether raw Fatigue's true range is 0-100 or 0-200 (observed thresholds match the getter's
`/2` output, not necessarily the raw field) — doesn't affect the cheat, which keys off tier ID
regardless.

Assignment/hysteresis logic: `exe+32CA00` (e.g. tier 36 only clears once Stamina recovers back
above 50% of max, not just above the enter threshold). Consumer: `exe+5AFE0`, with two "store the
final ID" instructions the cheat hooks: `exe+5B065` (stores whatever `exe+32CA00` decided) and
`exe+5B08A` (hardcoded `42`, a persistent Fatigue≥200 enforcement independent of `exe+32CA00`).
Target: `[rbp+0xA54]` on the player's action-state object (a different object from
`PlayerStructBase`).

Getters (take the player struct in `rcx`): `exe+2F06B0`=raw Stamina, `exe+2F06C0`=Fatigue/2,
`exe+2F0700`=likely Max Stamina (unconfirmed).

Cheat implementation: zeroes `ax` right before the two store instructions, for any animation ID
checked in the Cheats dialog (per-animation toggles, all default checked, gated by one master
toggle default off).

## Open questions

- Tool Levels and Selected Inventory Item are still unmapped.
- "Current tool equipped" needs its own save-load-hook investigation, same process as Money/Time.

## Item ID enum (Highlighted Bag Item → Item ID, and "Item ID in Hand")

Wired into `src/memory_viewer_agent.js` as `ITEM_NAMES`.

| ID | Item | ID | Item | ID | Item |
|---|---|---|---|---|---|
| 0 | Turnip | 99 | SUGDW Apple | 220 | Bagna Cauda |
| 1 | Potato | 100 | HMSGB Apple | 221 | Carbonara |
| 2 | Cucumber | 101 | AEPFE Apple | 222 | Margherita |
| 3 | Strawberries | 102 | Buckwheat Flour | 223 | Cheese Risotto |
| 4 | Cabbage | 103 | Wild Grape Water | 224 | Fish Fritters |
| 5 | Tomato | 104 | Salad | 225 | Nasi Goreng |
| 6 | Corn | 105 | Curry Rice | 226 | Madeleines |
| 7 | Onion | 106 | Stew | 227 | Strawberry Cake |
| 8 | Pumpkin | 107 | Miso Soup | 228 | Bibimbap |
| 9 | Pineapple | 108 | Vegetable Stir Fry | 229 | Vegetable Pizza |
| 10 | Eggplant | 109 | Fried Rice | 230 | Mushroom Gratin |
| 11 | Carrot | 110 | Okonomiyaki | 231 | Ramen |
| 12 | Yam | 111 | Sandwich | 232 | Daifuku |
| 13 | Spinach | 112 | Fruit Juice | 233 | Dorayaki |
| 14 | Green Pepper | 113 | Veggie Juice | 234 | Orangettes |
| 21 | Adzuki Beans | 114 | Mixed Juice | 235 | Candied Peels |
| 22 | Chili Peppers | 115 | Fruit Smoothie | 236 | Orange Pastries |
| 23 | Regular Egg | 117 | Mixed Smoothie | 237 | Zenzai |
| 24 | Good Egg | 118 | Strawberry Milk | 238 | Chestnut Rice |
| 25 | Excellent Egg | 122 | French Fries | 239 | Roasted Chestnuts |
| 26 | Golden Egg | 124 | Ketchup | 240 | Mont Blanc |
| 27 | Platinum Egg | 125 | Popcorn | 241 | Spicy Pepper Steak |
| 28 | X Egg | 127 | Roasted Corn | 242 | Spicy Ramen |
| 29 | Hot Spring Egg | 128 | Pineapple Juice | 243 | Spicy Vegetable Stir Fry |
| 30 | Mayonnaise (S) | 132 | Yam Dessert | 244 | Spicy Sandwich |
| 31 | Mayonnaise (M) | 133 | Baked Yam | 245 | Spicy Margherita |
| 32 | Mayonnaise (L) | 135 | Tamagoyaki | 246 | Fish Soup |
| 33 | Mayonnaise (G) | 137 | Omelet Rice | 256 | Moondrop Flower |
| 34 | Mayonnaise (P) | 138 | Boiled Egg | 257 | Pink Cat Flower |
| 35 | Mayonnaise (X) | 140 | Butter | 258 | Blue Magic Red Flower |
| 36 | Milk (S) | 141 | Cheesecake | 259 | True Magic Red Flower |
| 37 | Milk (M) | 142 | Cheese Fondue | 260 | Toy Flower |
| 38 | Milk (L) | 143 | Apple Pie | 261 | Sunsweet Flower |
| 39 | Milk (G) | 145 | Baked Apple | 262 | Wool (S) |
| 40 | Milk (P) | 146 | Mushroom Rice | 263 | Wool (M) |
| 41 | Milk (X) | 147 | Bamboo Rice | 264 | Wool (L) |
| 42 | Coffee Milk (S) | 148 | Matsutake Rice | 265 | Wool (G) |
| 43 | Coffee Milk (M) | 149 | Sushi | 266 | Wool (P) |
| 44 | Coffee Milk (L) | 152 | Raisin Bread | 267 | Wool (X) |
| 45 | Coffee Milk (G) | 154 | Curry Bread | 268 | Alpaca Fleece (S) |
| 46 | Coffee Milk (P) | 155 | Sashimi | 269 | Alpaca Fleece (M) |
| 47 | Coffee Milk (X) | 156 | Grilled Fish | 270 | Alpaca Fleece (L) |
| 48 | Fruit Milk (S) | 158 | Pizza | 271 | Alpaca Fleece (G) |
| 49 | Fruit Milk (M) | 159 | Udon | 272 | Alpaca Fleece (P) |
| 50 | Fruit Milk (L) | 160 | Curry Udon | 273 | Alpaca Fleece (X) |
| 51 | Fruit Milk (G) | 161 | Tempura Udon | 274 | Angora Rabbit Fur (S) |
| 52 | Fruit Milk (P) | 163 | Zaru Soba | 275 | Angora Rabbit Fur (M) |
| 53 | Fruit Milk (X) | 164 | Tempura Soba | 276 | Angora Rabbit Fur (L) |
| 54 | Strawberry Milk (S) | 167 | Cookies | 277 | Angora Rabbit Fur (G) |
| 55 | Strawberry Milk (M) | 168 | Chocolate Cookies | 278 | Angora Rabbit Fur (P) |
| 56 | Strawberry Milk (L) | 169 | Tempura | 279 | Angora Rabbit Fur (X) |
| 57 | Strawberry Milk (G) | 170 | Ice Cream | 280 | Yarn (S) |
| 58 | Strawberry Milk (P) | 171 | Cake | 281 | Yarn (M) |
| 59 | Strawberry Milk (X) | 173 | Relax Tea | 282 | Yarn (L) |
| 60 | Cheese (S) | 175 | French Toast | 283 | Yarn (G) |
| 61 | Cheese (M) | 176 | Pudding | 284 | Yarn (P) |
| 62 | Cheese (L) | 178 | Moon Dumplings | 285 | Yarn (X) |
| 63 | Cheese (G) | 179 | Mochi | 286 | Scrap Ore |
| 64 | Cheese (P) | 181 | Elli Leaves | 287 | Copper |
| 65 | Cheese (X) | 189 | Small Fish | 288 | Silver |
| 66 | Apple | 190 | Medium Fish | 289 | Gold |
| 67 | Honey | 191 | Large Fish | 290 | Mithril |
| 68 | Orange | 197 | Pancakes | 291 | Orichalcum |
| 69 | Bamboo Shoot | 201 | Mashed Potatoes | 292 | Adamantite |
| 70 | Wild Grapes | 202 | Baumkuchen | 293 | Moonstone |
| 71 | Mushroom | 203 | Acqua Pazza | 294 | Sandrose |
| 72 | Poison Mushroom | 204 | Ajillo | 295 | Pink Diamond |
| 73 | Matsutake | 205 | Churros | 296 | Alexandrite |
| 74 | Chestnut | 206 | Paella | 297 | Mythic Ore |
| 75 | Blue Grass | 207 | Fish Soup | 298 | Diamond |
| 76 | Green Grass | 208 | Almond Tofu | 299 | Emerald |
| 77 | Red Grass | 209 | Palbochae | 300 | Ruby |
| 78 | Yellow Grass | 210 | Pepper Steak | 301 | Topaz |
| 79 | Orange Grass | 211 | Seafood Rice Bowl | 302 | Peridot |
| 80 | Purple Grass | 212 | Oden | 303 | Fluorite |
| 81 | Indigo Grass | 213 | Napolitan | 304 | Agate |
| 82 | Black Grass | 214 | Eggs Benedict | 305 | Amethyst |
| 83 | White Grass | 215 | Pumpkin Potage | 306 | Goddess Jewel |
| 84 | Mystery Flower | 216 | Pot-au-Feu | 307 | Kappa Jewel |
| 85 | Stamina Booster | 217 | Carpaccio | 308 | Truth Jewel |
| 86 | Stamina Booster XL | 218 | Quiche | 309 | Spring Sun |
| 87 | Caffeine | 219 | Cheese Souffle | 310 | Summer Sun |
| 88 | Super Caffeine | | | 311 | Autumn Sun |
| 89 | Premium Grape Juice | | | 312 | Winter Sun |
| 90 | Grape Juice | | | 313 | Bracelet |
| 91 | Onigiri | | | 314 | Necklace |
| 92 | Bread | | | 315 | Earrings |
| 93 | Oil | | | 316 | Brooch |
| 94 | Wheat Flour | | | 317 | Weed |
| 95 | Curry Powder | | | 318 | Stone |
| 96 | Dango Flour | | | 319 | Branch |
| 97 | Chocolate | | | 320 | Tomatosetta Stone |
| 98 | Relax Tea Leaves | | | 321 | Letter in a Bottle |

(continued: 322 Ball, 323 Pirate Treasure, 324 Ancient Fossil, 325 Empty Can, 326 Boot, 327
Fishbone, 328 Karen's Wine, 329 Popuri's Dorodango, 330 Ran's Music Box, 331 Marie's Book, 332
Elly's Pressed Flowers, 333 Van's Favorite, 334 Remote Control, 335 Master Shopper Award, 336 Quiz
Book, 337 Goddess' Present, 338–357 Record 1–20, 359 100 Win Streak Item Book, 361 All Letters,
362 Perfume, 363 Photo, 365 All Books, 366 Invitation, 367 Dress, 368 Face Pack, 369 Lotion, 370
Sunblock, 372 Lumber, 373 Material Stone, 374 Golden Lumber, 375 Fodder, 376 Chicken/Rabbit Feed,
377 RPS Master Certification, 378 Disc, 379 Rick's Wristwatch, 380 Kai's Sea Charm, 381 Cliff's
Flower Ornament, 382 Gray's Brooch, 383 Doctor's Therapy Merch, 384 Mysterious Ticket, 385
Jennifer's Potpourri, 386 Brandon's Mini Artwork, 387 Brandon's Love Sculpture, 388 Cluck-Cluck
Clash Trophy, 389 Moo-Moo Fest Trophy, 390 Fluffy Fest Trophy, 391 Kappa Statue, 392 Goddess
Statue, 393 Nature Sprite Statue, 396 Jade, 397 Sapphire, 398 Aquamarine, 399 Garnet, 400
Turquoise, 401 Pet Treat, 402 Gray's Present, 403 All BGM, 405 Power Berry, 419 Alexandrite — see
`src/memory_viewer_agent.js`'s `ITEM_NAMES` for the exact, authoritative machine-readable list.)

## Tool ID enum (Highlighted Tool Item → Item ID, and "current tool equipped")

Sourced from the original CT file, not yet cross-checked live entry-by-entry. Wired into
`src/memory_viewer_agent.js` as `TOOL_NAMES`.

| ID | Tool | ID | Tool |
|---|---|---|---|
| 0 | Iron Sickle | 48 | Cow Breeding Kit |
| 1 | Copper Sickle | 49 | Sheep Breeding Kit |
| 2 | Silver Sickle | 50 | Rabbit Breeding Kit |
| 3 | Golden Sickle | 51 | Alpaca Breeding Kit |
| 4 | Mithril Sickle | 52 | Turnip Seeds |
| 5 | Cursed Sickle | 53 | Potato Seeds |
| 6 | Blessed Sickle | 54 | Cucumber Seeds |
| 7 | Mythic Sickle | 55 | Strawberry Seeds |
| 8 | Iron Hoe | 56 | Cabbage Seeds |
| 9 | Copper Hoe | 57 | Tomato Seeds |
| 10 | Silver Hoe | 58 | Corn Seeds |
| 11 | Golden Hoe | 59 | Onion Seeds |
| 12 | Mithril Hoe | 60 | Pumpkin Seeds |
| 13 | Cursed Hoe | 61 | Pineapple Seeds |
| 14 | Blessed Hoe | 62 | Eggplant Seeds |
| 15 | Mythic Hoe | 63 | Carrot Seeds |
| 16 | Iron Axe | 64 | Yam Seeds |
| 17 | Copper Axe | 65 | Spinach Seeds |
| 18 | Silver Axe | 66 | Green Pepper Seeds |
| 19 | Golden Axe | 73 | Adzuki Bean Seeds |
| 20 | Mithril Axe | 74 | Chili Pepper Seeds |
| 21 | Cursed Axe | 75 | Grass Seedling |
| 22 | Blessed Axe | 76 | Moondrop Flower Seeds |
| 23 | Mythic Axe | 77 | Pink Cat Flower Seeds |
| 24 | Iron Hammer | 78 | Magic Red Flower Seeds |
| 25 | Copper Hammer | 79 | Toy Flower Seeds |
| 26 | Silver Hammer | 80 | Sunsweet Flower Seeds |
| 27 | Golden Hammer | 81 | Brush |
| 28 | Mithril Hammer | 82 | Milker |
| 29 | Cursed Hammer | 83 | Clippers |
| 30 | Blessed Hammer | 86 | Blue Feather |
| 31 | Mythic Hammer | 87 | Pedometer |
| 32 | Cheap Watering Can | 88 | Travel Stone |
| 33 | Copper Watering Can | 89 | Goddess Treasure |
| 34 | Silver Watering Can | 90 | Kappa Treasure |
| 35 | Golden Watering Can | 91 | Truth Treasure |
| 36 | Mithril Watering Can | 92 | Preserved Flower |
| 37 | Cursed Watering Can | | |
