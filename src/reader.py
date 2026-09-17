"""
First milestone: attach to the running game and print live Money + Stamina to the console.

Usage:
    python src/reader.py "EXACT_PROCESS_NAME.exe"

Run this from an Administrator PowerShell/terminal window, with the game already running.
"""

import sys
import time
from pathlib import Path

import frida

AGENT_PATH = Path(__file__).parent / "agent.js"


def on_message(message, data):
    if message["type"] == "send":
        payload = message["payload"]
        kind = payload.get("type")
        label = payload.get("label")
        if kind == "value":
            print(f"{label}: {payload['value']}")
        elif kind == "info":
            print(f"[info] {label}: {payload['message']}")
        elif kind == "error":
            print(f"[error] {label}: {payload['message']}")
    elif message["type"] == "error":
        print(f"[frida error] {message.get('description')}")
        if message.get("stack"):
            print(message["stack"])


def main():
    if len(sys.argv) != 2:
        print("Usage: python src/reader.py \"EXACT_PROCESS_NAME.exe\"")
        sys.exit(1)

    process_name = sys.argv[1]

    print(f"Attaching to '{process_name}'...")
    session = frida.attach(process_name)

    script = session.create_script(AGENT_PATH.read_text())
    script.on("message", on_message)
    script.load()

    print("Hooked. Change your Money or Stamina in-game to see values print below.")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        session.detach()


if __name__ == "__main__":
    main()
