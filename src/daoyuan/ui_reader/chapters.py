# -*- coding: utf-8 -*-
"""文库章节列表的卡片式绘制。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..ui_common.theme import BORDER, CARD, GOLD, INK, MIST

ROLE_CHAPTER = int(Qt.ItemDataRole.UserRole) + 1
ROLE_TITLE = int(Qt.ItemDataRole.UserRole) + 2
ROLE_DATE = int(Qt.ItemDataRole.UserRole) + 3
ROLE_READY = int(Qt.ItemDataRole.UserRole) + 4

HOVER_BG = "#FBF8F1"
SELECTED_BG = "#F4ECDC"
SELECTED_BORDER = "#D8C4A0"
ROW_BORDER = "#EBE4D7"
CHEVRON = "#BCB09A"
PENDING = "#A9865B"


class ChapterListDelegate(QStyledItemDelegate):
    """每章一张卡片：章节号徽标、标题、日期，右侧箭头。"""

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, 76)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        ready = bool(index.data(ROLE_READY))
        chapter = str(index.data(ROLE_CHAPTER) or "")
        title = str(index.data(ROLE_TITLE) or "（无标题）")
        date = str(index.data(ROLE_DATE) or "日期未知")

        rect = QRectF(option.rect).adjusted(2.0, 4.0, -2.0, -4.0)
        if selected:
            background = QColor(SELECTED_BG)
            border = QColor(SELECTED_BORDER)
        elif hovered:
            background = QColor(HOVER_BG)
            border = QColor(BORDER)
        else:
            background = QColor(CARD)
            border = QColor(ROW_BORDER)

        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 10, 10)

        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(GOLD))
            painter.drawRoundedRect(
                QRectF(rect.left() + 1.5, rect.top() + 16, 3, rect.height() - 32),
                1.5,
                1.5,
            )

        # 章节号徽标；没有编号的文章用一个小圆点表示。
        if chapter:
            try:
                label = "%02d" % int(chapter)
            except ValueError:
                label = chapter
            badge = QRectF(rect.left() + 16, rect.center().y() - 17, 46, 34)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F1E9DA" if not selected else "#EEE2CC"))
            painter.drawRoundedRect(badge, 8, 8)
            badge_font = QFont(option.font)
            badge_font.setPixelSize(14)
            badge_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(badge_font)
            painter.setPen(QColor(INK if ready else PENDING))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(GOLD))
            painter.drawEllipse(
                QRectF(rect.left() + 33, rect.center().y() - 3, 6, 6)
            )

        text_left = rect.left() + 76
        text_width = max(20.0, rect.right() - text_left - 34)
        title_font = QFont(option.font)
        title_font.setPixelSize(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(INK if ready else "#59636A"))
        title_text = QFontMetrics(title_font).elidedText(
            title, Qt.TextElideMode.ElideRight, int(text_width)
        )
        painter.drawText(
            QRectF(text_left, rect.top() + 12, text_width, 22),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title_text,
        )

        meta_font = QFont(option.font)
        meta_font.setPixelSize(12)
        painter.setFont(meta_font)
        painter.setPen(QColor(MIST if ready else PENDING))
        meta = date if ready else "%s · 待采集" % date
        meta_text = QFontMetrics(meta_font).elidedText(
            meta, Qt.TextElideMode.ElideRight, int(text_width)
        )
        painter.drawText(
            QRectF(text_left, rect.top() + 38, text_width, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            meta_text,
        )

        chevron_font = QFont(option.font)
        chevron_font.setPixelSize(20)
        painter.setFont(chevron_font)
        painter.setPen(QColor(CHEVRON if ready else "#D5C8B2"))
        painter.drawText(
            QRectF(rect.right() - 30, rect.top(), 20, rect.height()),
            Qt.AlignmentFlag.AlignCenter,
            "›",
        )
        painter.restore()
