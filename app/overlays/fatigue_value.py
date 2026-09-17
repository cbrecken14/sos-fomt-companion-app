"""'Fatigue Value' overlay -- Phase 1 of the overlay feature. Shows the player's live Fatigue as
plain outlined text, no shape/panel behind it.

Wired to real game memory: src/fatigue_value_agent.js polls Fatigue (SaveDataBase +
0xBCE0 player-substruct offset + 0x3B8, see data/pointer_map.md's "Player-state struct" section)
on the app's shared Frida session (app/game_session.py), same attach-a-small-extra-script-on-the-
shared-session pattern every other feature window uses (see app/reminders_live_data.py). The
game's own getter (exe+2F06C0) divides the raw stored value by 2 before comparing it against the
Tired Animation thresholds -- this shows that same halved value (0-100), not the raw stored number.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen

from ..game_session import get_shared_session
from ..overlay_registry import OverlaySpec, register
from ..overlay_window import OverlayWindow

AGENT_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "fatigue_value_agent.js"

MAX_FATIGUE = 100

# Confirmed live for "windowed" mode specifically; no confirmed "fullscreen_or_borderless"
# default yet, so that mode still falls back to these same numbers.
_DEFAULT_OFFSET = (298, 28)
_DEFAULT_SIZE = (241, 50)

_FONT_SIZE_RATIO = 0.5 # of the window's height -- 0.4 too small, 0.55 too big
# QFont.setStretch() compresses/expands glyphs horizontally without touching point size (so
# height is untouched) -- less horizontal space without a shorter/smaller
# font. 100 is normal width; below 100 is condensed.
_FONT_STRETCH_PERCENT = 80
# The stretch above squeezes characters together enough that e.g. "100"'s digits started
# clipping into each other -- a bit of absolute letter spacing pulls them back
# apart without undoing the horizontal compression.
_LETTER_SPACING_PX = 1.5

_TEXT_FILL_COLOR = QColor("#fff6e6")
_TEXT_OUTLINE_COLOR = QColor("#3a2410")
# Started at 1px now that the font carries more weight of its own; bumped to 2px same day once that read as enough contrast to try more.
_OUTLINE_THICKNESS_PX = 2
# Only shown when the Transparent Background toggle is OFF -- otherwise there's nothing behind
# the text at all.
_PANEL_BACKING_COLOR = QColor(30, 30, 30, 180)

# Switched from Candara: forcing italic on it made the "N" in "FAT" render
# noticeably lower than the rest of the string -- looked like an italic-hinting quirk specific to
# that glyph, not something caused by our own drawing (the whole string is one drawText() call,
# so we're not introducing any per-character offset ourselves). Segoe Print is naturally tilted by
# design, so it's used WITHOUT forcing italic -- forcing italic on top of an already-slanted
# typeface synthesizes an extra shear and can look/behave oddly.
_FONT_FAMILY = "Segoe Print"
_FONT_ITALIC = False


class FatigueValueOverlay(OverlayWindow):
    fatigue_updated = Signal(int) # emitted from the Frida message-callback thread -- see _on_message

    def __init__(self, parent=None):
        super().__init__(
            "fatigue_value",
            "Fatigue Value",
            default_offset=_DEFAULT_OFFSET,
            default_size=_DEFAULT_SIZE,
            default_transparent=True,
            parent=parent,
        )
        self._fatigue_value: Optional[int] = None
        self._script = None
        self.fatigue_updated.connect(self._on_fatigue_value)
        threading.Thread(target=self._attach_worker, daemon=True).start()

    # --- live memory attach (same pattern app/reminders_live_data.py's classes use) -------------

    def _attach_worker(self) -> None:
        try:
            game_session = get_shared_session()
            session = game_session.wait_for_session() # background thread only, never Qt main
            script = session.create_script(AGENT_PATH.read_text())
            script.on("message", self._on_message)
            script.load()
        except Exception as exc:
            print(f"[fatigue value overlay] couldn't attach to the game: {exc}")
            return
        self._script = script
        game_session.save_data_base_found.connect(self._on_save_data_base)
        if game_session.current_save_data_base:
            self._on_save_data_base(game_session.current_save_data_base)

    def _on_save_data_base(self, address: str) -> None:
        if self._script is not None:
            self._script.post({"type": "setSaveDataBase", "address": address})

    def _on_message(self, message, data) -> None:
        if message["type"] != "send":
            if message["type"] == "error":
                print(f"[fatigue value overlay] frida error: {message.get('description')}")
            return
        payload = message["payload"]
        kind = payload.get("type")
        if kind == "fatigue":
            self.fatigue_updated.emit(payload["raw"] // 2)
        elif kind == "status":
            print(f"[fatigue value overlay] {payload.get('message')}")

    def _on_fatigue_value(self, value: int) -> None:
        self._fatigue_value = value
        self.update()

    def cleanup(self) -> None:
        """Only unloads this window's OWN script -- the shared session itself is app-lifetime,
        detached once from the shell on app shutdown (see game_session.shutdown_shared_session)."""
        super().cleanup()
        script = self._script
        self._script = None

        def detach() -> None:
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass

        threading.Thread(target=detach, daemon=True).start()

    # --- painting --------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self._transparent:
            painter.fillRect(self.rect(), _PANEL_BACKING_COLOR)

        font = QFont(_FONT_FAMILY)
        font.setItalic(_FONT_ITALIC)
        font.setWeight(QFont.Weight.DemiBold) # Segoe Print at Normal weight read too thin, 2026-09-13
        font.setPointSize(max(8, int(self.height() * _FONT_SIZE_RATIO)))
        font.setStretch(_FONT_STRETCH_PERCENT)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, _LETTER_SPACING_PX)

        value_text = str(self._fatigue_value) if self._fatigue_value is not None else "--"
        text = f"FAT {value_text}/{MAX_FATIGUE}"
        metrics = QFontMetrics(font)
        baseline_x = (self.width() - metrics.horizontalAdvance(text)) / 2
        baseline_y = (self.height() + metrics.ascent() - metrics.descent()) / 2

        # Outline's back: plain white text got lost over busy game backgrounds
        # without one. Stroking the actual glyph path keeps it clean at any thickness; the heavier
        # DemiBold weight above should keep the outline from re-thinning the letters the way it
        # did at Normal weight.
        path = QPainterPath()
        path.addText(baseline_x, baseline_y, font, text)

        pen = QPen(_TEXT_OUTLINE_COLOR, _OUTLINE_THICKNESS_PX)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(_TEXT_FILL_COLOR)
        painter.drawPath(path)

        self._draw_resize_handle(painter)


def _factory() -> FatigueValueOverlay:
    return FatigueValueOverlay()


register(OverlaySpec("fatigue_value", "Fatigue Value", _factory))
