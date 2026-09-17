"""
Live reader for current Day/Hour/Minute/Season/Year/Weather.

Finds TimeBasePtr by hooking a known save-load instruction (see src/time_reader_agent.js for the
full derivation) that fires exactly once each time a save is loaded (game launch, or reloading a
save mid-session). After that hook fires, values are read directly (polled) since there's no
separate "getter" function to hook for ongoing updates.

TimeBasePtr is cached between runs (app/hook_cache.py) -- if a previously-found address is on file
and still looks valid (the game process hasn't restarted since), it's used immediately, no reload
needed. Otherwise (first run, or the cached address no longer looks right) this just waits for the
permanent hook to fire naturally: load or reload a save in-game once and the address is found,
used, and saved for next time. Unlike the farm map's grid_base, there's no separate "listen" step
to trigger -- the hook stays installed for the whole session and re-fires (updating the cache
again) any time the player reloads another save later.

Usage:
    python src/time_reader.py "EXACT_PROCESS_NAME.exe" [--address 0xADDRESS]

--address forces a specific TimeBasePtr by hand (found via Cheat Engine) instead, skipping the
cache for this run.

Press Ctrl+C to stop.
"""

import sys
import time
from pathlib import Path

import frida

# Run directly as a script (python src/time_reader.py), sys.path[0] is this file's own directory
# (src/), not the project root -- so the sibling `app` package (project root/app/) isn't importable
# without this. Same fix as src/farm_map.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import hook_cache  # noqa: E402
from src.frida_utils import detach_async  # noqa: E402

AGENT_PATH = Path(__file__).parent / "time_reader_agent.js"
TIME_BASE_CACHE_KEY = "time_base_ptr"

SEASON_NAMES = {0: "Spring", 1: "Summer", 2: "Fall", 3: "Winter"}
WEATHER_NAMES = {0: "Sunny", 1: "Rainy", 2: "Snowy", 3: "Typhoon", 4: "Blizzard"}


def format_value(label: str, value: int) -> str:
    if label == "Season":
        return f"{value} ({SEASON_NAMES.get(value, '?')})"
    if label in ("Weather", "Next Weather"):
        return f"{value} ({WEATHER_NAMES.get(value, '?')})"
    return str(value)


def main() -> None:
    args = sys.argv[1:]
    address = None
    if "--address" in args:
        i = args.index("--address")
        address = args[i + 1]
        args = args[:i] + args[i + 2 :]

    if len(args) != 1:
        print('Usage: python src/time_reader.py "EXACT_PROCESS_NAME.exe" [--address 0xADDRESS]')
        sys.exit(1)

    process_name = args[0]

    print(f"Attaching to '{process_name}'...")
    session = frida.attach(process_name)

    if address is not None:
        override = f'ptr("{address}")'
    else:
        cached = hook_cache.get(TIME_BASE_CACHE_KEY)
        override = f'ptr("{cached}")' if cached else "null"

    agent_source = AGENT_PATH.read_text()
    agent_source = agent_source.replace("TIME_BASE_OVERRIDE", override)
    script = session.create_script(agent_source)

    def on_message(message, data):
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[frida error] {message.get('description')}")
                if message.get("stack"):
                    print(message["stack"])
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "status":
            print(f"[status] {payload['message']}")
        elif kind == "timeBaseFound":
            hook_cache.set(TIME_BASE_CACHE_KEY, payload["address"])
            print(f"[status] found and saved TimeBasePtr: {payload['address']}")
        elif kind == "value":
            print(f"{payload['label']}: {format_value(payload['label'], payload['value'])}")

    script.on("message", on_message)
    script.load()

    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        detach_async(session, script)


if __name__ == "__main__":
    main()
