"""Small Qt widgets shared by more than one feature window."""
from __future__ import annotations

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QTableWidget


class CopyableTableWidget(QTableWidget):
    """A QTableWidget where Ctrl+C (and a right-click "Copy") copy the current selection to the
    clipboard as tab/newline-separated text, spreadsheet-style. Plain QTableWidget doesn't support
    this out of the box -- cells look like they might be selectable text but nothing happens on
    Ctrl+C without this.
    """

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(event)

    def copy_selection(self) -> None:
        ranges = self.selectedRanges()
        if not ranges:
            return
        r = ranges[0]  # a rectangular multi-select across disjoint ranges is rare enough to skip
        lines = []
        for row in range(r.topRow(), r.bottomRow() + 1):
            cells = []
            for col in range(r.leftColumn(), r.rightColumn() + 1):
                item = self.item(row, col)
                cells.append(item.text() if item is not None else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))
