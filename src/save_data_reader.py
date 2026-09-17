"""
Live reader for the game's central save-data struct: Money, and (via a fixed offset) every player
character stat -- Current Stamina, Fatigue, Hoe Level, all six Tool EXP values, and both Mine
Depth Records. See src/save_data_reader_agent.js for the full derivation.

Cached between runs (app/hook_cache.py) -- if a previously-found address is on file and still
looks valid (the game process hasn't restarted since), it's used immediately, no reload needed.
Otherwise this just waits for the hook to fire naturally: get into a save (or reload one) and the
base is found, used, and saved for next time.

Usage:
    python src/save_data_reader.py "EXACT_PROCESS_NAME.exe" [--address 0xADDRESS]

--address forces a specific base by hand instead, skipping the cache for this run.

Press Ctrl+C to stop.
"""

import sys
import time
from pathlib import Path

import frida

# Run directly as a script (python src/save_data_reader.py), sys.path[0] is this file's own
# directory (src/), not the project root -- so the sibling app/src imports below need this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import hook_cache  # noqa: E402
from src.frida_utils import detach_async  # noqa: E402

AGENT_PATH = Path(__file__).parent / "save_data_reader_agent.js"
DATA_BASE_CACHE_KEY = "save_data_base_ptr"


def main() -> None:
    args = sys.argv[1:]
    address = None
    if "--address" in args:
        i = args.index("--address")
        address = args[i + 1]
        args = args[:i] + args[i + 2 :]

    if len(args) != 1:
        print('Usage: python src/save_data_reader.py "EXACT_PROCESS_NAME.exe" [--address 0xADDRESS]')
        sys.exit(1)

    process_name = args[0]

    print(f"Attaching to '{process_name}'...")
    session = frida.attach(process_name)

    if address is not None:
        override = f'ptr("{address}")'
    else:
        cached = hook_cache.get(DATA_BASE_CACHE_KEY)
        override = f'ptr("{cached}")' if cached else "null"

    agent_source = AGENT_PATH.read_text()
    agent_source = agent_source.replace("DATA_BASE_OVERRIDE", override)
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
        if kind == "baseFound":
            hook_cache.set(DATA_BASE_CACHE_KEY, payload["address"])
            print(f"[status] {payload['message']}")
        elif kind == "status":
            print(f"[status] {payload['message']}")
        elif kind == "value":
            print(f"{payload['label']}: {payload['value']}")

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
