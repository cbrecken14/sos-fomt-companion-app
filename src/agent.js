// Frida agent injected into the running game process.
//
// For each group of values, this finds the exact bytes of the game's own accessor function for
// that group (same byte patterns the original Cheat Engine table hooks — see
// data/pointer_map.md), hooks its entry point, and reads value = *(register + offset) for each
// value in that group, since several values share the same captured object pointer. Only sends a
// message back to Python when a value actually changes.
//
// IMPORTANT: this only works against an unpatched game process. If a Cheat Engine table has been
// enabled/attached in the current game session, its own code-cave injection overwrites these exact
// bytes with a JMP, which breaks this scan for the rest of that process's life. Always test against
// a freshly launched game with no Cheat Engine table attached.
//
// All patterns are scanned for in a single pass over memory (rather than one full pass per
// pattern) and the pass stops as soon as everything's been found — this is what keeps the
// one-time startup scan from taking several times longer than it needs to.

function installHooks(specs) {
    const pending = specs.map((spec) => ({
        ...spec,
        foundAddresses: [],
        resolved: false,
    }));

    // Scan all readable memory, not just execute-only pages — dynamically compiled game
    // code (e.g. a Mono/.NET-style scripting runtime) is often allocated as read+write+
    // execute rather than strictly read+execute, so filtering too narrowly can miss it.
    const ranges = Process.enumerateRanges('r--');

    for (const range of ranges) {
        let allResolved = true;
        for (const spec of pending) {
            if (spec.resolved) continue;

            // The game is live and constantly allocating/freeing memory while we scan,
            // so a range we just enumerated can become invalid before we get to it —
            // skip it and move on rather than letting that crash the whole scan.
            try {
                const matches = Memory.scanSync(range.base, range.size, spec.patternHex);
                for (const m of matches) {
                    spec.foundAddresses.push(m.address);
                }
            } catch (e) {
                // ignore and continue
            }

            const wantAll = spec.options && spec.options.hookAll;
            if (spec.foundAddresses.length > 0 && !wantAll) {
                spec.resolved = true;
            } else {
                allResolved = false;
            }
        }
        if (allResolved) break;
    }

    for (const spec of pending) {
        const label = spec.values.map((v) => v.name).join(' / ');

        if (spec.foundAddresses.length === 0) {
            send({ type: 'error', label: label, message: 'pattern not found anywhere in process memory' });
            continue;
        }

        // Short/generic byte patterns can legitimately match more than one place in the
        // game's code (e.g. a shared "increment and wrap" idiom used for unrelated
        // counters). When options.hookAll is set, hook every match and tag each one by
        // address, so we can compare live output and figure out which is the real one.
        const wantAll = spec.options && spec.options.hookAll;
        const addressesToHook = wantAll ? spec.foundAddresses : [spec.foundAddresses[0]];

        send({
            type: 'info',
            label: label,
            message: `found ${spec.foundAddresses.length} match(es), hooking ${addressesToHook.length}`,
        });

        addressesToHook.forEach((found, idx) => {
            const tag = wantAll ? ` [candidate ${idx} @ ${found}]` : '';
            const lastValues = {};
            Interceptor.attach(found, {
                onEnter(args) {
                    const base = this.context[spec.registerName];
                    for (const v of spec.values) {
                        const addr = base.add(v.offset);
                        let value;
                        switch (v.size) {
                            case 1:
                                value = addr.readU8();
                                break;
                            case 2:
                                value = addr.readU16();
                                break;
                            default:
                                value = v.float ? addr.readFloat() : addr.readU32();
                                break;
                        }
                        if (lastValues[v.name] !== value) {
                            lastValues[v.name] = value;
                            send({ type: 'value', label: v.name + tag, value: value });
                        }
                    }
                },
            });
        });
    }
}

installHooks([
    {
        // GetMoney(): mov eax, dword ptr [rcx+0xBC50]; ret
        patternHex: '8B 81 50 BC 00 00 C3',
        registerName: 'rcx',
        values: [{ name: 'Money', offset: 0xbc50, size: 4 }],
    },
    {
        // GetStamina() and friends — all read off the same player-struct pointer captured here.
        patternHex: '0F B7 81 B6 03 00 00 C3',
        registerName: 'rcx',
        values: [
            { name: 'Stamina', offset: 0x3b6, size: 2 },
            { name: 'Fatigue', offset: 0x3b8, size: 2 },
            { name: 'Spring Mine Depth Record', offset: 0x3c8, size: 2 },
            { name: 'Winter Mine Depth Record', offset: 0x3ca, size: 2 },
            { name: 'Hoe EXP', offset: 0x350, size: 4 },
            { name: 'Sickle EXP', offset: 0x360, size: 4 },
            { name: 'Axe EXP', offset: 0x370, size: 4 },
            { name: 'Hammer EXP', offset: 0x380, size: 4 },
            { name: 'Watering Can EXP', offset: 0x390, size: 4 },
            { name: 'Fishing Rod EXP', offset: 0x3a0, size: 4 },
            { name: 'Mole Hit Count', offset: 0x3cc, size: 4 },
        ],
    },
    {
        // Heart Level of the last person you spoke with.
        patternHex: '89 44 24 40 48 8D 54 24 40 E9 9C 84 00 00',
        registerName: 'rcx',
        values: [
            { name: 'Heart Level (Last Person Spoken With)', offset: 0x38, size: 4 },
            { name: 'Friendship Level (Last Person Spoken With)', offset: 0x10, size: 4 },
        ],
    },
    {
        // Heart Level of the last animal you petted.
        patternHex: '0F B6 81 A2 01 00 00 C3',
        registerName: 'rcx',
        values: [{ name: 'Heart Level (Last Animal Petted)', offset: 0x1a4, size: 4 }],
    },

    // Time and weather — PARKED, not currently working. This AOB only matches one place in
    // memory, but the object it captures isn't the real calendar-time struct: Year (x1) and
    // Year (x7) came back as 80 and 100 on a clean run, both way outside their real max of 6
    // and 29. Best guess: this is a generic "increment and wrap" utility the game reuses for
    // more than one purpose (an outdoors-only visual/audio effect, most likely, not the
    // calendar clock), and this happens to be the only occurrence of these exact bytes even
    // though it isn't the right one. Needs a fresh, more specific signature derived directly
    // from the current game build to fix properly — see PROGRESS.md.
    //
    // {
    //     patternHex: '0F B6 41 08 FF C0 83 F8 19 73 06',
    //     registerName: 'rcx',
    //     values: [
    //         { name: 'Season', offset: 0x08, size: 1 },
    //         { name: 'Day', offset: 0x0c, size: 1 },
    //         { name: 'Hour', offset: 0x10, size: 1 },
    //         { name: 'Minute', offset: 0x11, size: 1 },
    //         { name: 'Year (x1)', offset: 0x00, size: 1 },
    //         { name: 'Year (x7)', offset: 0x04, size: 1 },
    //         { name: 'Weather', offset: -0x08, size: 1 },
    //         { name: 'Next Weather', offset: -0x04, size: 1 },
    //     ],
    //     options: { hookAll: true },
    // },
]);

// Player position (X/Y). Confirmed correct by directly editing both values in Cheat Engine and
// watching the character warp on both axes. Found via: value-scan an unknown float while walking,
// then "find out what writes to this address".
//
// The write site is this whole block: add velocity to X, then to a second float at +4
// (unconfirmed, probably Z/elevation — not used yet), then to Y at +8 — three near-identical
// vmovss/vaddss/vmovss triples in a row. We use the whole ~80-byte block as the signature
// rather than any single 4-byte vmovss instruction, which is far too short/generic to be safe
// on its own (same lesson as the Time/Weather pattern collision — see the parked block above).
//
// We hook onEnter of the FIRST write (X's) — before any of the three writes for this frame have
// happened yet, so [rdx] and [rdx+8] still hold fully-settled values from the previous instant.
// That's a fraction of a second behind live, which is irrelevant for a HUD-style readout, and it
// means we only need onEnter (no need to reason about onLeave timing for a hook placed
// mid-function rather than at a true function entry).
{
    const patternHex =
        '4C 8D 85 18 01 00 00 48 8D 95 28 01 00 00 48 8D 4C 24 50 E8 ?? ?? ?? ?? ' +
        '48 8B 44 24 50 48 8B 10 48 8B 44 24 58 48 8B 08 ' +
        'C5 FA 10 01 C5 FA 58 0A C5 FA 11 0A ' +
        'C5 FA 10 41 04 C5 FA 58 4A 04 C5 FA 11 4A 04 ' +
        'C5 FA 10 41 08 C5 FA 58 4A 08 C5 FA 11 4A 08';
    const X_WRITE_OFFSET = 0x30; // offset of the X vmovss instruction within the pattern above

    const ranges = Process.enumerateRanges('r--');
    let found = null;
    for (const range of ranges) {
        try {
            const matches = Memory.scanSync(range.base, range.size, patternHex);
            if (matches.length > 0) {
                found = matches[0].address;
                break;
            }
        } catch (e) {
            // ignore and continue
        }
    }

    if (found === null) {
        send({ type: 'error', label: 'Player Position', message: 'pattern not found anywhere in process memory' });
    } else {
        const hookAddr = found.add(X_WRITE_OFFSET);
        send({ type: 'info', label: 'Player Position', message: 'hooked at ' + hookAddr });

        const last = {};
        Interceptor.attach(hookAddr, {
            onEnter(args) {
                const rdx = this.context.rdx;
                const x = rdx.readFloat();
                const y = rdx.add(8).readFloat();
                if (last.x !== x) {
                    last.x = x;
                    send({ type: 'value', label: 'Player X', value: x });
                }
                if (last.y !== y) {
                    last.y = y;
                    send({ type: 'value', label: 'Player Y', value: y });
                }
            },
        });
    }
}
