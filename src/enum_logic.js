// Location-name lookup: the partial ENUMS.LOCATION table (see data/tables/enums.json) plus the
// mine-floor range rules -- each mine has its own floor-0 location id, floor number = id - that
// base. This is logic, not data, so it stays hand-written here rather than moving into the JSON
// table like ENUMS itself did.
//
// Spring Mine: floor 0 = location 61, 256 floors up to floor 255 (confirmed live at floor 190 =
// location 251). Lake Mine: floor 0 = location 317, 255 floors. See data/pointer_map.md's
// "Current location ID enum" section.
function locationCalc(id) {
    if (ENUMS.LOCATION[id] !== undefined) return ENUMS.LOCATION[id];
    if (id >= 61 && id <= 316) return 'Mine floor ' + (id - 61);
    if (id >= 317 && id <= 571) return 'Lake Mine floor ' + (id - 317);
    return null;
}
