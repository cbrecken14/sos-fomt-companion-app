// Debug tool for developing/verifying the farm tile struct (see data/pointer_map.md's "Tile
// struct fields"/"Growth table" sections): clearing the whole field back to a blank untilled map,
// tilling soil, watering already-tilled soil, planting one full-height column per confirmed
// crop-type id, planting the current season's crops in tidy, gapped 9x9 blocks, planting EVERY
// crop-type id at every growth stage 1-5 (one column per id, one row per stage), planting one
// seed-stage tile per crop-type id for a day-by-day growth-tracking experiment, and exporting that
// experiment's live field data (a read, not a write) for recording once per real day.
//
// Independent, one-shot actions, not wired to any UI control. Mirrors src/farm_map_agent.js's grid
// layout (ROWS/COLS/strides, tile field offsets) rather than re-deriving it.

const ROWS = 25;
const COLS = 43;
const TILE_STRIDE = 0x10;
const ROW_STRIDE = 0x2b0;
const STATE_OFFSET = 0x0;
const CONTENT_OFFSET = 0x4;
const OCCUPANT_OFFSET = 0x8;
// "Times watered" counter + three more independent single-byte sub-fields -- see
// data/pointer_map.md's "Tile struct fields" section. Only read (never written) by this file now
// -- exportFieldData below.
const WATER_COUNT_OFFSET = 0x0c;
const BYTE_D_OFFSET = 0x0d;
const BYTE_E_OFFSET = 0x0e;
const BYTE_F_OFFSET = 0x0f;

const UNTILLED_STATE = 0;
const TILLED_DRY_STATE = 1;
const TILLED_WATERED_STATE = 2;
const DEBRIS_OCCUPANT = 8;
const PLANTED_SEED_STAGE = 1; // occupant value for a freshly-planted crop (lowest growth stage)
// All 23 confirmed crop-type ids (0-22, see data/tables/crops.json) -- NOT id 23 (Grass's own id
// via the separate state==0 path, tested once already as a one-off experiment; this tool only
// ever plants real, confirmed crops).
const CROP_COUNT = 23;

// plantSeasonCrops only -- each crop-type id's season, straight from data/tables/crops.json's own
// "season" field (index = crop-type id). Winter has no farmable crops -- every id 0-22 falls into
// Spring/Summer/Fall.
const SEASON_SPRING = 0;
const SEASON_SUMMER = 1;
const SEASON_FALL = 2;
const SEASON_NAMES = ['Spring', 'Summer', 'Fall', 'Winter'];
const CROP_SEASON = [
    SEASON_SPRING, SEASON_SPRING, SEASON_SPRING, SEASON_SPRING, SEASON_SPRING, // 0-4
    SEASON_SUMMER, SEASON_SUMMER, SEASON_SUMMER, SEASON_SUMMER, SEASON_SUMMER, // 5-9
    SEASON_FALL, SEASON_FALL, SEASON_FALL, SEASON_FALL, SEASON_FALL, SEASON_FALL, SEASON_FALL, // 10-16
    SEASON_SPRING, // 17
    SEASON_SUMMER, // 18
    SEASON_FALL, SEASON_FALL, // 19-20
    SEASON_SPRING, // 21
    SEASON_FALL, // 22
];
const SEASON_OFFSET = 0x08; // from TimeBasePtr -- see data/pointer_map.md's "Time/Weather struct"

// plantSeasonCrops only -- each crop gets its own 9x9 block with a 1-tile gap between blocks. The
// farm (25 rows x 43 cols) only has room for BLOCKS_PER_ROW x BLOCKS_PER_COL blocks laid out this
// way -- a season with more crops than that (Fall has 10) just doesn't get a block for its last
// few crop-type ids; see plantSeasonCrops's own skipped-count reporting.
const BLOCK_SIZE = 9;
const BLOCK_GAP = 1;
const BLOCK_STEP = BLOCK_SIZE + BLOCK_GAP;
const BLOCKS_PER_ROW = Math.floor((COLS + BLOCK_GAP) / BLOCK_STEP);
const BLOCKS_PER_COL = Math.floor((ROWS + BLOCK_GAP) / BLOCK_STEP);
const MAX_BLOCKS = BLOCKS_PER_ROW * BLOCKS_PER_COL;

// plantSeasonCrops only -- each block's tiles still get an individually random growth
// stage/watered state (not all planted identically), for exercising the Reminders window's
// crop-status tracking against a realistic, varied field. Each crop's true growth-stage ceiling
// isn't confirmed yet (Turnip topped out at occupant==10 in the wild, not the originally-assumed 5
// -- see data/pointer_map.md's "Tile struct fields" section), so 1-5 is a placeholder range for
// testing purposes only, not a claim about any real crop's actual max.
const RANDOM_STAGE_MIN = 1;
const RANDOM_STAGE_MAX = 5;
const RANDOM_WATERED_CHANCE = 0.5;

// plantAllCropStages only -- same row-0/col-0 corner every other planting tool in this file uses,
// so it's always easy to find in-game. One column per crop-type id (same convention as
// plantCropColumns), but only STAGE_COUNT rows tall instead of full farm height, since this is for
// comparing stages, not full-field coverage.
const TEST_COLUMN_START_ROW = 0;
const TEST_COLUMN_START_COL = 0;
const STAGE_COUNT = 5; // occupant 1-5 -- confirmed live: 5 is the universal harvestable stage for
// every crop regardless of how many visually distinct phases it has (a crop at stage 4 can look
// identical to stage 5 but still isn't harvestable until it's really 5).

// plantSeedRow / exportFieldData only -- one tile per crop-type id, same row-0/col-0 corner as
// every other tool here, column N = crop-type id N (one tracked tile per crop, not a whole
// column), for the day-by-day watering/growth-tracking experiment.
const TRACK_ROW = 0;
const TRACK_START_COL = 0;

let gridBase = null;
let timeBase = null;

function listenForSetFarmGridBase() {
    recv('setFarmGridBase', (message) => {
        gridBase = ptr(message.address);
        listenForSetFarmGridBase(); // recv() only fires once per registration -- re-arm
    });
}
listenForSetFarmGridBase();

function listenForSetTimeBase() {
    recv('setTimeBase', (message) => {
        timeBase = ptr(message.address);
        listenForSetTimeBase(); // re-arm
    });
}
listenForSetTimeBase();

function forEachTile(fn) {
    for (let row = 0; row < ROWS; row++) {
        for (let col = 0; col < COLS; col++) {
            fn(gridBase.add(row * ROW_STRIDE + col * TILE_STRIDE));
        }
    }
}

function countDebrisTiles() {
    let count = 0;
    forEachTile((tileAddr) => {
        if (tileAddr.add(OCCUPANT_OFFSET).readS32() === DEBRIS_OCCUPANT) count++;
    });
    return count;
}

function respond(action, ok, message) {
    send({ type: 'farmDebugToolResult', action: action, ok: ok, message: message });
}

// Registers one persistent, self-re-arming listener per action -- same recv()-only-fires-once
// pattern as every other agent script in this project, factored out once here since there are
// four of these now instead of one.
function registerAction(action, run) {
    function listen() {
        recv(action, () => {
            if (gridBase === null) {
                respond(action, false, "Farm grid base not found yet -- can't run this yet.");
                listen();
                return;
            }
            try {
                run();
            } catch (e) {
                respond(action, false, `Couldn't run ${action}: ${e.message}`);
            }
            listen(); // re-arm for the next click
        });
    }
    listen();
}

registerAction('clearField', () => {
    // Resets the ENTIRE farm to a blank, untilled map -- every debris object, every crop
    // (regardless of growth stage), and all tilled/watered soil, gone. Not just a debris sweep.
    let debrisCount = 0;
    let cropCount = 0;
    forEachTile((tileAddr) => {
        const occupant = tileAddr.add(OCCUPANT_OFFSET).readS32();
        if (occupant === DEBRIS_OCCUPANT) debrisCount++;
        else if (occupant !== 0) cropCount++;
        tileAddr.add(STATE_OFFSET).writeS32(UNTILLED_STATE);
        tileAddr.add(CONTENT_OFFSET).writeS32(0);
        tileAddr.add(OCCUPANT_OFFSET).writeS32(0);
    });
    respond(
        'clearField', true,
        `Cleared debris from ${debrisCount} tile(s), removed crops from ${cropCount} tile(s), `
        + `and reset the whole farm to an untilled blank map.`
    );
});

registerAction('tillSoil', () => {
    const debrisCount = countDebrisTiles();
    if (debrisCount > 0) {
        respond(
            'tillSoil', false,
            `Can't till -- ${debrisCount} farm tile(s) still have debris. Clear the field first.`
        );
        return;
    }
    // Tilling also clears any existing crops -- ploughing a grown/growing crop under, not just
    // refreshing the soil underneath it.
    forEachTile((tileAddr) => {
        tileAddr.add(STATE_OFFSET).writeS32(TILLED_DRY_STATE);
        tileAddr.add(CONTENT_OFFSET).writeS32(0);
        tileAddr.add(OCCUPANT_OFFSET).writeS32(0);
    });
    respond('tillSoil', true, `Tilled all ${ROWS * COLS} farm tiles and cleared any crops.`);
});

registerAction('waterTilledSoil', () => {
    let count = 0;
    forEachTile((tileAddr) => {
        if (tileAddr.add(STATE_OFFSET).readS32() !== UNTILLED_STATE) {
            tileAddr.add(STATE_OFFSET).writeS32(TILLED_WATERED_STATE);
            count++;
        }
    });
    respond('waterTilledSoil', true, `Watered ${count} already-tilled farm tile(s).`);
});

registerAction('plantCropColumns', () => {
    // Clears + tills the whole farm first.
    forEachTile((tileAddr) => {
        tileAddr.add(STATE_OFFSET).writeS32(TILLED_DRY_STATE);
        tileAddr.add(CONTENT_OFFSET).writeS32(0);
        tileAddr.add(OCCUPANT_OFFSET).writeS32(0);
    });
    // One FULL-HEIGHT column per crop-type id, 0 through 22, side by side from the farm's
    // row-0/col-0 corner -- column N = crop-type id N.
    for (let cropId = 0; cropId < CROP_COUNT; cropId++) {
        for (let row = 0; row < ROWS; row++) {
            const tileAddr = gridBase.add(row * ROW_STRIDE + cropId * TILE_STRIDE);
            tileAddr.add(CONTENT_OFFSET).writeS32(cropId);
            tileAddr.add(OCCUPANT_OFFSET).writeS32(PLANTED_SEED_STAGE);
        }
    }
    respond(
        'plantCropColumns', true,
        `Tilled all ${ROWS * COLS} farm tiles and planted full-height columns for crop-type ids 0-${CROP_COUNT - 1}.`
    );
});

registerAction('plantSeasonCrops', () => {
    // Tills the whole farm, then plants only the current season's crop-type ids, each in its own
    // 9x9 block with a 1-tile gap between blocks (row-major, starting at the farm's row-0/col-0
    // corner). Each block's own tiles still get an individually random growth stage and
    // watered/dry state, for exercising the Reminders window's crop-status tracking against a
    // realistic, varied field.
    if (timeBase === null) {
        respond('plantSeasonCrops', false, "Current season not known yet -- can't run this yet.");
        return;
    }
    const currentSeason = timeBase.add(SEASON_OFFSET).readS32();
    const seasonName = SEASON_NAMES[currentSeason] || `season ${currentSeason}`;

    forEachTile((tileAddr) => {
        tileAddr.add(STATE_OFFSET).writeS32(TILLED_DRY_STATE);
        tileAddr.add(CONTENT_OFFSET).writeS32(0);
        tileAddr.add(OCCUPANT_OFFSET).writeS32(0);
    });

    const seasonCropIds = [];
    for (let cropId = 0; cropId < CROP_COUNT; cropId++) {
        if (CROP_SEASON[cropId] === currentSeason) seasonCropIds.push(cropId);
    }
    const plantedIds = seasonCropIds.slice(0, MAX_BLOCKS);
    const skippedCount = seasonCropIds.length - plantedIds.length;

    plantedIds.forEach((cropId, i) => {
        const blockRow = Math.floor(i / BLOCKS_PER_ROW);
        const blockCol = i % BLOCKS_PER_ROW;
        const startRow = blockRow * BLOCK_STEP;
        const startCol = blockCol * BLOCK_STEP;
        for (let r = 0; r < BLOCK_SIZE; r++) {
            for (let c = 0; c < BLOCK_SIZE; c++) {
                const tileAddr = gridBase.add((startRow + r) * ROW_STRIDE + (startCol + c) * TILE_STRIDE);
                const stage = RANDOM_STAGE_MIN + Math.floor(Math.random() * (RANDOM_STAGE_MAX - RANDOM_STAGE_MIN + 1));
                const watered = Math.random() < RANDOM_WATERED_CHANCE;
                tileAddr.add(CONTENT_OFFSET).writeS32(cropId);
                tileAddr.add(OCCUPANT_OFFSET).writeS32(stage);
                tileAddr.add(STATE_OFFSET).writeS32(watered ? TILLED_WATERED_STATE : TILLED_DRY_STATE);
            }
        }
    });

    let message = `Tilled the whole farm and planted ${plantedIds.length} ${seasonName} crop(s), `
        + `each in its own 9x9 block (1-tile gap between blocks), tiles individually randomized `
        + `for growth stage and watered/dry state.`;
    if (skippedCount > 0) {
        message += ` Only ${MAX_BLOCKS} blocks fit on the farm, so ${skippedCount} more `
            + `${seasonName} crop(s) were left out.`;
    }
    respond('plantSeasonCrops', true, message);
});

registerAction('plantAllCropStages', () => {
    // Plants EVERY crop-type id at once, each in its own column, one tile per growth stage 1-5
    // (row N = stage N+1) -- for walking columns 0-22 and noting, for each crop, which stages look
    // visually identical (confirmed the harvestable stage is always 5 regardless of how many
    // DISTINCT visual phases a crop actually has). Clears + tills the whole farm first, same as
    // plantCropColumns.
    forEachTile((tileAddr) => {
        tileAddr.add(STATE_OFFSET).writeS32(TILLED_DRY_STATE);
        tileAddr.add(CONTENT_OFFSET).writeS32(0);
        tileAddr.add(OCCUPANT_OFFSET).writeS32(0);
    });
    for (let cropId = 0; cropId < CROP_COUNT; cropId++) {
        const col = TEST_COLUMN_START_COL + cropId;
        for (let i = 0; i < STAGE_COUNT; i++) {
            const row = TEST_COLUMN_START_ROW + i;
            const tileAddr = gridBase.add(row * ROW_STRIDE + col * TILE_STRIDE);
            tileAddr.add(CONTENT_OFFSET).writeS32(cropId);
            tileAddr.add(OCCUPANT_OFFSET).writeS32(i + 1);
        }
    }
    respond(
        'plantAllCropStages', true,
        `Tilled all ${ROWS * COLS} farm tiles and planted stages 1-${STAGE_COUNT} for every `
        + `crop-type id 0-${CROP_COUNT - 1}, one column per id, starting at row `
        + `${TEST_COLUMN_START_ROW}/col ${TEST_COLUMN_START_COL} (row N = stage N+1).`
    );
});

registerAction('plantSeedRow', () => {
    // One tile per crop-type id, all at the freshly-planted seed stage (occupant=1) and dry
    // (state=1, so each one needs to actually be watered starting day 1) -- for the day-by-day
    // growth-tracking experiment. Clears + tills the whole farm first, same as every other
    // planting action here.
    forEachTile((tileAddr) => {
        tileAddr.add(STATE_OFFSET).writeS32(TILLED_DRY_STATE);
        tileAddr.add(CONTENT_OFFSET).writeS32(0);
        tileAddr.add(OCCUPANT_OFFSET).writeS32(0);
    });
    for (let cropId = 0; cropId < CROP_COUNT; cropId++) {
        const col = TRACK_START_COL + cropId;
        const tileAddr = gridBase.add(TRACK_ROW * ROW_STRIDE + col * TILE_STRIDE);
        tileAddr.add(CONTENT_OFFSET).writeS32(cropId);
        tileAddr.add(OCCUPANT_OFFSET).writeS32(PLANTED_SEED_STAGE);
    }
    respond(
        'plantSeedRow', true,
        `Tilled all ${ROWS * COLS} farm tiles and planted one seed-stage tile per crop-type id `
        + `0-${CROP_COUNT - 1} along row ${TRACK_ROW}, starting at col ${TRACK_START_COL} `
        + `(col N = crop-type id N).`
    );
});

// Not a registerAction() -- carries structured per-tile data back (`rows`), not just a plain
// status message, so app/farm_tile_debug.py can hand it to the dialog to format/print. Reads
// every field this project currently knows about for each of the tracked tiles planted by
// plantSeedRow above, so nothing has to be re-derived from daily readings (data/pointer_map.md's
// "Tile struct fields"/"Growth table" sections).
function listenForExportFieldData() {
    recv('exportFieldData', () => {
        if (gridBase === null) {
            send({ type: 'exportFieldDataResult', ok: false, error: "Farm grid base not found yet -- can't run this yet." });
            listenForExportFieldData();
            return;
        }
        try {
            const rows = [];
            for (let cropId = 0; cropId < CROP_COUNT; cropId++) {
                const col = TRACK_START_COL + cropId;
                const tileAddr = gridBase.add(TRACK_ROW * ROW_STRIDE + col * TILE_STRIDE);
                rows.push({
                    col: col,
                    cropId: tileAddr.add(CONTENT_OFFSET).readS32(),
                    state: tileAddr.add(STATE_OFFSET).readS32(),
                    stage: tileAddr.add(OCCUPANT_OFFSET).readS32(),
                    timesWatered: tileAddr.add(WATER_COUNT_OFFSET).readU8(),
                    byteD: tileAddr.add(BYTE_D_OFFSET).readS8(),
                    byteE: tileAddr.add(BYTE_E_OFFSET).readS8(),
                    byteF: tileAddr.add(BYTE_F_OFFSET).readS8(),
                });
            }
            send({ type: 'exportFieldDataResult', ok: true, rows: rows });
        } catch (e) {
            send({ type: 'exportFieldDataResult', ok: false, error: e.message });
        }
        listenForExportFieldData(); // re-arm for the next click
    });
}
listenForExportFieldData();
