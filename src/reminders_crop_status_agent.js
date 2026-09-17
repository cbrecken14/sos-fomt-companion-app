// Aggregates the farm's tile grid into crop-status counts for the Reminders window's "Crop
// Status" section and future-harvest predictions -- per crop-type id, how many
// planted tiles still need watering today (state != watered) vs need harvesting (occupant == 5)
// vs total planted, plus a raw list of still-growing tiles (crop id/stage/times-watered) so
// app/reminders_crop_data.py can predict "ready in N days" per data/tables/crop_growth.json.
// Mirrors src/farm_map_agent.js's grid layout (ROWS/COLS/strides, tile field offsets) rather than
// re-deriving it; receives grid_base from the shared session the same way app/farm_tile_debug.py's
// debug tools do, rather than hooking it a second time itself.
//
// Growth-table mechanics (stage == occupant, always tops out at 5, times-watered counter at
// +0xC drives the lookup) confirmed live day-by-day 2026-09-13 -- see data/pointer_map.md's
// "Growth table" section.

const ROWS = 25;
const COLS = 43;
const TILE_STRIDE = 0x10;
const ROW_STRIDE = 0x2b0;
const STATE_OFFSET = 0x0;
const CONTENT_OFFSET = 0x4;
const OCCUPANT_OFFSET = 0x8;
const WATER_COUNT_OFFSET = 0x0c;

const TILLED_WATERED_STATE = 2;
const EMPTY_OCCUPANT = 0;
const DEBRIS_OCCUPANT = 8;
const HARVEST_READY_STAGE = 5;
const POLL_INTERVAL_MS = 1000;
// Grass (content==23) is rendered directly on untilled ground (state==0), always at occupant==5
// -- it's not a real planted crop (data/pointer_map.md's "Tile struct fields" section), so it
// should never count toward needsWater/needsHarvest or the future-harvest "growing" list, even
// though its occupant value happens to look like a fully-grown crop.
const GRASS_CONTENT_ID = 23;

let gridBase = null;

function listenForSetFarmGridBase() {
    recv('setFarmGridBase', (message) => {
        gridBase = ptr(message.address);
        listenForSetFarmGridBase(); // recv() only fires once per registration -- re-arm
    });
}
listenForSetFarmGridBase();

function reportCropStatus() {
    if (gridBase === null) return;
    try {
        const counts = {}; // cropId (string key once sent as JSON) -> { total, needsWater, needsHarvest }
        const growing = []; // { cropId, stage, timesWatered } for every tile not yet at stage 5
        for (let row = 0; row < ROWS; row++) {
            for (let col = 0; col < COLS; col++) {
                const tileAddr = gridBase.add(row * ROW_STRIDE + col * TILE_STRIDE);
                const occupant = tileAddr.add(OCCUPANT_OFFSET).readS32();
                if (occupant === EMPTY_OCCUPANT || occupant === DEBRIS_OCCUPANT) continue;
                const cropId = tileAddr.add(CONTENT_OFFSET).readS32();
                const state = tileAddr.add(STATE_OFFSET).readS32();
                if (!counts[cropId]) counts[cropId] = { total: 0, needsWater: 0, needsHarvest: 0 };
                counts[cropId].total += 1;
                if (cropId === GRASS_CONTENT_ID) continue; // counted above, never needs water/harvest
                if (state !== TILLED_WATERED_STATE) counts[cropId].needsWater += 1;
                if (occupant >= HARVEST_READY_STAGE) {
                    counts[cropId].needsHarvest += 1;
                } else {
                    const timesWatered = tileAddr.add(WATER_COUNT_OFFSET).readU8();
                    growing.push({ cropId: cropId, stage: occupant, timesWatered: timesWatered });
                }
            }
        }
        send({ type: 'cropStatus', ok: true, counts: counts, growing: growing });
    } catch (e) {
        send({ type: 'cropStatus', ok: false, error: e.message });
    }
}

setInterval(reportCropStatus, POLL_INTERVAL_MS);
