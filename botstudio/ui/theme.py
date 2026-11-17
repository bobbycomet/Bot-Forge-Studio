"""Griffin Dark — Steam-inspired palette used across Bot Forge Studio."""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

BG = "#171a21"
PANEL = "#1b2838"
RAISED = "#2a475e"
CANVAS = "#10161f"
GRID = "#16202c"
GRID_MAJOR = "#1c2a3a"
TEXT = "#c7d5e0"
MUTED = "#8f98a0"
ACCENT = "#66c0f4"
ACCENT_DEEP = "#1a9fff"
GOOD = "#5ba32b"
WARN = "#f0ad4e"
BAD = "#d9534f"

PORT_COLORS = {
    "exec": "#e8eaed", "str": "#7fc1ff", "int": "#9be37f", "bool": "#ff8d8d", "user": "#ffd166",
    "channel": "#c792ea", "guild": "#5fd3bc", "message": "#ff9e64", "any": "#aab4be",
}

QSS = f"""
* {{ font-family: "Noto Sans", "Segoe UI", "Ubuntu", sans-serif; font-size: 13px; }}
QMainWindow, QDialog, QWidget {{ background: {BG}; color: {TEXT}; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {PANEL}; padding: 6px 10px; border-bottom: 1px solid {RAISED}; }}
QMenuBar {{ background: {BG}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {RAISED}; }}
QMenu {{ background: {PANEL}; border: 1px solid {RAISED}; }}
QMenu::item {{ padding: 5px 24px; }}
QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {RAISED}; spacing: 4px; padding: 4px; }}
QToolButton {{ background: transparent; padding: 5px 10px; border-radius: 3px; }}
QToolButton:hover {{ background: {RAISED}; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTreeWidget, QTableWidget {{
    background: {CANVAS}; border: 1px solid {RAISED}; border-radius: 3px; padding: 3px 5px; color: {TEXT};
    selection-background-color: {ACCENT_DEEP}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {MUTED}; background: {BG}; }}
QPushButton {{ background: {RAISED}; border: 1px solid #3d6c8d; border-radius: 3px; padding: 5px 14px; }}
QPushButton:hover {{ background: #35607d; }}
QPushButton:disabled {{ color: {MUTED}; background: {PANEL}; border-color: {RAISED}; }}
QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {ACCENT_DEEP}, stop:1 #0f7bc7); border-color: {ACCENT}; color: white; }}
QPushButton#primary:hover {{ background: {ACCENT}; }}
QPushButton#danger {{ background: #7a2d2b; border-color: {BAD}; }}
QTabWidget::pane {{ border: 1px solid {RAISED}; }}
QTabBar::tab {{ background: {PANEL}; padding: 6px 14px; border: 1px solid {RAISED}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {RAISED}; color: white; }}
QHeaderView::section {{ background: {PANEL}; border: none; padding: 4px; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {RAISED}; border-radius: 4px; min-height: 24px; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {RAISED}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QGroupBox {{ border: 1px solid {RAISED}; border-radius: 4px; margin-top: 10px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {ACCENT}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#h {{ color: white; font-size: 15px; font-weight: bold; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {ACCENT}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(CANVAS))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(RAISED))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT_DEEP))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
