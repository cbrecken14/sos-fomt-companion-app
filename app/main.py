"""Entry point for the SoS: FoMT companion app shell.

Run from the project folder with:

    python -m app.main
"""
import sys

from PySide6.QtWidgets import QApplication

from .shell import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    # No unconditional resize() here -- MainWindow.__init__ already restores the last saved size
    # (or falls back to its own default if there's nothing saved yet). Resizing here would
    # clobber that on every launch.
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
