# -*- coding: utf-8 -*-
"""云山书境视觉主题。"""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

PAPER = "#F7F5F0"
INK = "#283540"
GOLD = "#B49763"
MIST = "#73838A"
CLOUD = "#E7E3DA"
SIDEBAR = "#F0ECE3"
CARD = "#FFFDF8"
BORDER = "#E2DACB"
MUTED = "#6E7B82"
DANGER = "#A44A3F"

LIGHT_QSS = """
QWidget {
    background: %(paper)s;
    color: %(ink)s;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "SimSun";
    font-size: 14px;
}
QMainWindow, QDialog { background: %(paper)s; }
QLabel { background: transparent; }
#Sidebar { background: %(sidebar)s; border-right: 1px solid %(border)s; }
#SidebarTitle { font-size: 18px; font-weight: 600; color: %(ink)s; }
#SidebarSub { font-size: 12px; color: %(mist)s; }
QPushButton#NavButton {
    text-align: left;
    padding: 10px 18px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: #4A565E;
    font-size: 14px;
}
QPushButton#NavButton:hover { background: #E8E2D5; }
QPushButton#NavButton:checked { background: #E2D9C6; color: %(ink)s; font-weight: 600; }
QPushButton#NavButton:disabled { color: #A9A296; }
#PageTitle { font-size: 26px; font-weight: 600; color: %(ink)s; }
#PageSub { font-size: 13px; color: %(mist)s; }
#HeroTitle { font-size: 34px; font-weight: 600; color: %(ink)s; }
#HeroSub { font-size: 15px; color: %(mist)s; }
#Card {
    background: %(card)s;
    border: 1px solid %(border)s;
    border-radius: 12px;
}
#StatValue { font-size: 28px; font-weight: 600; color: %(ink)s; }
#StatLabel { font-size: 12px; color: %(mist)s; }
#ListHeading { font-size: 15px; font-weight: 600; color: %(ink)s; }
#ListHint { font-size: 12px; color: %(mist)s; }
#Muted { color: %(muted)s; font-size: 12px; }
#Gold { color: %(gold)s; }
QPushButton#Primary {
    background: %(ink)s;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 9px 20px;
    font-size: 14px;
}
QPushButton#Primary:hover { background: #3A4A55; }
QPushButton#Primary:disabled { background: #B9B2A5; }
QPushButton#Ghost {
    background: transparent;
    color: %(ink)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#Ghost:hover { background: #EFE9DD; }
QPushButton#Danger {
    background: transparent;
    color: #A8443A;
    border: 1px solid #D8B4AC;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#Danger:hover { background: #F7E8E4; }
QPushButton#Danger:disabled { color: #BCA9A3; border-color: #E4D9D5; }
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    padding: 10px 12px;
    border-radius: 8px;
    color: %(ink)s;
}
QListWidget::item:hover { background: #EFE9DD; }
QListWidget::item:selected { background: #E2D9C6; color: %(ink)s; }
QListWidget#CollectionList::item { padding: 7px 10px; border-radius: 8px; }
QListWidget#ChapterList::item,
QListWidget#ChapterList::item:hover,
QListWidget#ChapterList::item:selected {
    padding: 0;
    background: transparent;
    border: none;
}
QTextBrowser {
    background: %(card)s;
    border: 1px solid %(border)s;
    border-radius: 10px;
    padding: 24px;
    selection-background-color: #D9C9A6;
}
QTreeWidget#TopicTree {
    background: transparent;
    border: none;
    outline: 0;
}
QTreeWidget#TopicTree::item {
    padding: 0;
    border: none;
    background: transparent;
}
QTreeWidget#TopicTree::item:hover,
QTreeWidget#TopicTree::item:selected { background: transparent; }
QTreeWidget#TopicTree::branch { background: transparent; }
QFrame#TopicPanel {
    background: #FBF8F1;
    border: 1px solid %(border)s;
    border-radius: 12px;
}
QLabel#TopicPanelTitle {
    color: #8C6B3F;
    font-size: 13px;
    font-weight: 600;
}
QTextBrowser#TopicSidebar {
    background: transparent;
    border: none;
    padding: 2px;
    selection-background-color: #D9C9A6;
}
QLineEdit, QComboBox, QSpinBox {
    background: %(card)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
    padding: 8px 10px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid %(gold)s; }
QPlainTextEdit {
    background: %(card)s;
    border: 1px solid %(border)s;
    border-radius: 10px;
    padding: 10px;
    color: %(ink)s;
    selection-background-color: #D9C9A6;
}
QProgressBar {
    background: #E7E3DA;
    border: none;
    border-radius: 3px;
}
QProgressBar::chunk { background: %(gold)s; border-radius: 3px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #CFC6B5; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar { background: %(sidebar)s; color: %(mist)s; }
QSplitter::handle { background: transparent; }
"""


def _pick_font() -> QFont:
    families = set(QFontDatabase.families())
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC"):
        if name in families:
            return QFont(name, 10)
    return QFont()


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(_pick_font())
    app.setStyleSheet(
        LIGHT_QSS
        % {
            "paper": PAPER,
            "ink": INK,
            "gold": GOLD,
            "mist": MIST,
            "sidebar": SIDEBAR,
            "card": CARD,
            "border": BORDER,
            "muted": MUTED,
        }
    )
