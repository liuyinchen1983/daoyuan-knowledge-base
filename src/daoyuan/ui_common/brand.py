# -*- coding: utf-8 -*-
"""品牌资源入口。有授权 Logo 时放入 assets/brand/logo.png 即自动使用。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QLabel, QWidget

from .theme import GOLD, INK


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[3]


def asset_path(*parts: str) -> Path | None:
    root = project_root()
    path = root.joinpath("assets", "brand", *parts)
    return path if path.exists() else None


def logo_pixmap(size: int = 64) -> QPixmap | None:
    path = asset_path("logo.png")
    if not path:
        return None
    pix = QPixmap(str(path))
    if pix.isNull():
        return None
    return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def app_icon() -> QIcon:
    """EXE/任务栏图标：优先用户放置的 app_icon.ico，否则用项目占位图标。"""
    for name in ("app_icon.ico", "logo.png"):
        path = asset_path(name)
        if path:
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def set_windows_app_id(app_id: str) -> None:
    """让任务栏把两套程序识别为独立应用；不影响系统设置。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass


class BrandMark(QWidget):
    """无 Logo 素材时绘制一个可替换的文字印记，不使用任何外部图片。"""

    def __init__(self, size: int = 56, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self._size, self._size)
        pix = logo_pixmap(self._size)
        if pix:
            painter.drawPixmap(0, 0, pix)
            return
        gradient = QRadialGradient(rect.center(), self._size / 2)
        gradient.setColorAt(0, QColor("#D8C49A"))
        gradient.setColorAt(1, QColor(GOLD))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
        font = QFont("Microsoft YaHei UI", max(12, int(self._size * 0.36)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#FFFDF8"))
        painter.drawText(rect, Qt.AlignCenter, "道")


class BrandHeader(QWidget):
    """侧边栏顶部的品牌区。"""

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 18, 12, 18)
        layout.setSpacing(12)
        layout.addWidget(BrandMark(48))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(title)
        name.setObjectName("SidebarTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("SidebarSub")
        texts.addWidget(name)
        texts.addWidget(sub)
        layout.addLayout(texts)
        layout.addStretch(1)
