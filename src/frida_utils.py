"""Shared helper for console Frida scripts: detaching a session cleanly without ever blocking
Ctrl+C.

`session.detach()` / `script.unload()` are blocking calls into the game process that can take a
real moment or hang -- already seen once in this project for the mine map's GUI tab (see
app/windows/mine_map.py's cleanup()). Every console reader script had the same bug: Ctrl+C
correctly interrupted the sleep loop, but then the script hung waiting for a synchronous
session.detach() in its `finally` block before it could actually exit.

Usage:
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        detach_async(session, script)
"""
import threading


def detach_async(session, script=None) -> None:
    """Detach a Frida session (and optionally unload its script) on a daemon background thread,
    so a slow/hanging detach never blocks the caller -- the process can exit immediately
    regardless of how long the actual detach takes, since daemon threads don't block interpreter
    shutdown.
    """

    def _detach() -> None:
        try:
            if script is not None:
                script.unload()
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass

    threading.Thread(target=_detach, daemon=True).start()
