"""
First overlay prototype: a small always-on-top window showing live values over the game.

Usage:
    python src/overlay.py "EXACT_PROCESS_NAME.exe"

Run from an Administrator PowerShell/terminal window, with the game already running.

The overlay automatically snaps to the top-left corner of the game's window and follows it if
the game window moves. Dragging the overlay manually turns off auto-follow for the rest of the
session, in case you'd rather position it yourself.

Controls:
    - Click and drag anywhere on the overlay to reposition it (turns off auto-follow).
    - Press Escape (while the overlay has focus) to close it.
"""

import sys
import tkinter as tk
from pathlib import Path

import frida
import win32gui
import win32process

AGENT_PATH = Path(__file__).parent / "agent.js"

# Must match the "name" fields sent from agent.js exactly.
LABELS = [
    "Money",
    "Stamina",
    "Fatigue",
    "Spring Mine Depth Record",
    "Winter Mine Depth Record",
    "Hoe EXP",
    "Sickle EXP",
    "Axe EXP",
    "Hammer EXP",
    "Watering Can EXP",
    "Fishing Rod EXP",
    "Heart Level (Last Person Spoken With)",
    "Friendship Level (Last Person Spoken With)",
    "Heart Level (Last Animal Petted)",
]

values = {}


def on_message(message, data):
    if message["type"] == "send":
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "value":
            values[payload["label"]] = payload["value"]
        elif kind == "error":
            print(f"[error] {payload.get('label')}: {payload.get('message')}")
    elif message["type"] == "error":
        print(f"[frida error] {message.get('description')}")
        if message.get("stack"):
            print(message["stack"])


def find_game_window_rect(pid):
    """Return (left, top, right, bottom) of the largest visible top-level window owned by
    this process, or None if we can't find one."""
    candidates = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == pid:
                candidates.append(win32gui.GetWindowRect(hwnd))
        return True

    win32gui.EnumWindows(callback, None)
    if not candidates:
        return None

    def area(rect):
        left, top, right, bottom = rect
        return (right - left) * (bottom - top)

    return max(candidates, key=area)


def main():
    if len(sys.argv) != 2:
        print('Usage: python src/overlay.py "EXACT_PROCESS_NAME.exe"')
        sys.exit(1)

    process_name = sys.argv[1]

    print(f"Attaching to '{process_name}'...")
    device = frida.get_local_device()
    pid = device.get_process(process_name).pid
    session = frida.attach(pid)
    script = session.create_script(AGENT_PATH.read_text())
    script.on("message", on_message)
    script.load()

    root = tk.Tk()
    root.title("SoS Overlay")
    root.attributes("-topmost", True)
    root.overrideredirect(True)  # no titlebar/border
    root.configure(bg="black")
    root.attributes("-alpha", 0.85)
    root.geometry("+20+20")

    row_widgets = {}
    for i, label in enumerate(LABELS):
        widget = tk.Label(
            root,
            text=f"{label}: --",
            fg="white",
            bg="black",
            font=("Consolas", 11),
            anchor="w",
        )
        widget.grid(row=i, column=0, sticky="w", padx=6, pady=1)
        row_widgets[label] = widget

    def refresh():
        for label, widget in row_widgets.items():
            widget.config(text=f"{label}: {values.get(label, '--')}")
        root.after(200, refresh)

    refresh()

    auto_follow = {"on": True}

    def follow_game_window():
        if auto_follow["on"]:
            rect = find_game_window_rect(pid)
            if rect is not None:
                left, top, _, _ = rect
                root.geometry(f"+{left + 20}+{top + 40}")
        root.after(1000, follow_game_window)

    follow_game_window()

    def close(_event=None):
        session.detach()
        root.destroy()

    def start_move(event):
        auto_follow["on"] = False
        root._drag_x = event.x
        root._drag_y = event.y

    def do_move(event):
        x = root.winfo_pointerx() - root._drag_x
        y = root.winfo_pointery() - root._drag_y
        root.geometry(f"+{x}+{y}")

    root.bind("<ButtonPress-1>", start_move)
    root.bind("<B1-Motion>", do_move)
    root.bind("<Escape>", close)
    root.protocol("WM_DELETE_WINDOW", close)

    root.mainloop()


if __name__ == "__main__":
    main()
