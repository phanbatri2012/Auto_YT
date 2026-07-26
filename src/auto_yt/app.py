"""Application entrypoint."""

import sys

from PyQt6.QtWidgets import QApplication

from auto_yt.ui.login_window import LoginWindow


def main():
    app = QApplication(sys.argv)
    window = LoginWindow()
    window.show()
    sys.exit(app.exec())
