# -*- coding: utf-8 -*-
"""阅读端专题树的自绘与详情 HTML。"""
from __future__ import annotations

import html

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..topics.catalog import (
    DIMENSION_BY_KIND,
    group_label,
    kind_color,
    kind_label,
)
from ..ui_common.theme import BORDER, CARD, GOLD, INK, MIST

ROLE_NODE = int(Qt.ItemDataRole.UserRole) + 20
ROLE_KEY = int(Qt.ItemDataRole.UserRole) + 21
ROLE_TITLE = int(Qt.ItemDataRole.UserRole) + 22
ROLE_SUBTITLE = int(Qt.ItemDataRole.UserRole) + 23
ROLE_COUNT = int(Qt.ItemDataRole.UserRole) + 24
ROLE_COUNT_SUFFIX = int(Qt.ItemDataRole.UserRole) + 25
ROLE_COLOR = int(Qt.ItemDataRole.UserRole) + 26
ROLE_DEPTH = int(Qt.ItemDataRole.UserRole) + 27
ROLE_HAS_CHILDREN = int(Qt.ItemDataRole.UserRole) + 28
ROLE_EXPANDED = int(Qt.ItemDataRole.UserRole) + 29
ROLE_ENTITY_ID = int(Qt.ItemDataRole.UserRole) + 30
ROLE_ENTITY_KIND = int(Qt.ItemDataRole.UserRole) + 31

HOVER_BG = "#FBF8F1"
SELECTED_BG = "#F4ECDC"
SELECTED_BORDER = "#D8C4A0"
ROW_BORDER = "#EBE4D7"
CHEVRON = "#A99D88"


class TopicTreeDelegate(QStyledItemDelegate):
    """三层次绘制：维度卡、分组行、条目卡。"""

    def sizeHint(self, option, index) -> QSize:
        node = index.data(ROLE_NODE)
        if node == "dimension":
            return QSize(0, 66)
        if node == "group":
            return QSize(0, 38)
        return QSize(0, 60)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        node = str(index.data(ROLE_NODE) or "entity")
        if node == "dimension":
            self._paint_dimension(painter, option, index)
        elif node == "group":
            self._paint_group(painter, option, index)
        else:
            self._paint_entity(painter, option, index)
        painter.restore()

    def _paint_dimension(self, painter, option, index) -> None:
        rect = QRectF(option.rect).adjusted(4.0, 4.0, -6.0, -4.0)
        color = QColor(str(index.data(ROLE_COLOR) or GOLD))
        background = QColor(color)
        background.setAlpha(18)
        border = QColor(color)
        border.setAlpha(55)
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 12, 12)

        icon = QRectF(rect.left() + 12, rect.center().y() - 16, 32, 32)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(icon, 9, 9)
        glyph_font = QFont(option.font)
        glyph_font.setPixelSize(15)
        glyph_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(glyph_font)
        painter.setPen(QColor("#FFFDF8"))
        painter.drawText(icon, Qt.AlignmentFlag.AlignCenter, str(index.data(ROLE_TITLE) or "")[:1])

        title = str(index.data(ROLE_TITLE) or "")
        subtitle = str(index.data(ROLE_SUBTITLE) or "")
        count = int(index.data(ROLE_COUNT) or 0)
        pill_rect = QRectF(rect.right() - 78, rect.center().y() - 12, 50, 24)
        text_width = max(40.0, pill_rect.left() - (icon.right() + 12) - 8)

        title_font = QFont(option.font)
        title_font.setPixelSize(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(INK))
        painter.drawText(
            QRectF(icon.right() + 12, rect.top() + 10, text_width, 22),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(title_font).elidedText(
                title, Qt.TextElideMode.ElideRight, int(text_width)
            ),
        )
        sub_font = QFont(option.font)
        sub_font.setPixelSize(11)
        painter.setFont(sub_font)
        painter.setPen(QColor(MIST))
        painter.drawText(
            QRectF(icon.right() + 12, rect.top() + 32, text_width, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(sub_font).elidedText(
                subtitle, Qt.TextElideMode.ElideRight, int(text_width)
            ),
        )
        self._draw_pill(painter, option, pill_rect, count, "", color)
        self._draw_chevron(painter, option, rect, index)

    def _paint_group(self, painter, option, index) -> None:
        rect = QRectF(option.rect).adjusted(18.0, 1.0, -6.0, -1.0)
        color = QColor(str(index.data(ROLE_COLOR) or GOLD))
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F7F3EA"))
            painter.drawRoundedRect(rect, 8, 8)

        x = rect.left() + 26
        painter.setPen(QPen(QColor("#E5DDCE"), 1))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(x - 3.5, rect.center().y() - 3.5, 7, 7))

        count = int(index.data(ROLE_COUNT) or 0)
        pill_rect = QRectF(rect.right() - 66, rect.center().y() - 10, 40, 20)
        title = str(index.data(ROLE_TITLE) or "")
        text_left = x + 14
        text_width = max(30.0, pill_rect.left() - text_left - 8)
        font = QFont(option.font)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#4D5A62"))
        painter.drawText(
            QRectF(text_left, rect.top(), text_width, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(font).elidedText(
                title, Qt.TextElideMode.ElideRight, int(text_width)
            ),
        )
        self._draw_pill(painter, option, pill_rect, count, "", color.lighter(115), compact=True)
        self._draw_chevron(painter, option, rect, index, small=True)

    def _paint_entity(self, painter, option, index) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(32.0, 3.0, -6.0, -3.0)
        if selected:
            painter.setPen(QPen(QColor(SELECTED_BORDER), 1))
            painter.setBrush(QColor(SELECTED_BG))
            painter.drawRoundedRect(rect, 9, 9)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(GOLD))
            painter.drawRoundedRect(
                QRectF(rect.left() + 1.5, rect.top() + 12, 3, rect.height() - 24),
                1.5,
                1.5,
            )
        elif hovered:
            painter.setPen(QPen(QColor(BORDER), 1))
            painter.setBrush(QColor(HOVER_BG))
            painter.drawRoundedRect(rect, 9, 9)
        else:
            painter.setPen(QPen(QColor(ROW_BORDER), 1))
            painter.setBrush(QColor(CARD))
            painter.drawRoundedRect(rect, 9, 9)

        kind = str(index.data(ROLE_ENTITY_KIND) or "")
        color = QColor(kind_color(kind))
        left = rect.left() + 20
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(left - 3, rect.center().y() - 3, 6, 6))

        count = int(index.data(ROLE_COUNT) or 0)
        suffix = str(index.data(ROLE_COUNT_SUFFIX) or "篇")
        pill_rect = QRectF(rect.right() - 72, rect.center().y() - 11, 50, 22)
        text_left = left + 12
        text_width = max(60.0, pill_rect.left() - text_left - 8)

        name = str(index.data(ROLE_TITLE) or "")
        summary = str(index.data(ROLE_SUBTITLE) or "")
        name_font = QFont(option.font)
        name_font.setPixelSize(14)
        name_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(QColor(INK))
        painter.drawText(
            QRectF(text_left, rect.top() + 8, text_width, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(name_font).elidedText(
                name, Qt.TextElideMode.ElideRight, int(text_width)
            ),
        )
        if summary:
            sub_font = QFont(option.font)
            sub_font.setPixelSize(11)
            painter.setFont(sub_font)
            painter.setPen(QColor(MIST))
            painter.drawText(
                QRectF(text_left, rect.top() + 30, text_width, 18),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                QFontMetrics(sub_font).elidedText(
                    summary, Qt.TextElideMode.ElideRight, int(text_width)
                ),
            )
        self._draw_pill(painter, option, pill_rect, count, suffix, color, compact=True)

    def _draw_pill(self, painter, option, rect, count, suffix, color, compact=False):
        painter.setPen(Qt.PenStyle.NoPen)
        tint = QColor(color)
        tint.setAlpha(28)
        painter.setBrush(tint)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        font = QFont(option.font)
        font.setPixelSize(10 if compact else 11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(INK))
        text = "%s%s" % (count, suffix)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_chevron(self, painter, option, rect, index, small=False):
        if not bool(index.data(ROLE_HAS_CHILDREN)):
            return
        expanded = bool(index.data(ROLE_EXPANDED))
        font = QFont(option.font)
        font.setPixelSize(12 if small else 14)
        painter.setFont(font)
        painter.setPen(QColor(CHEVRON))
        painter.drawText(
            QRectF(rect.right() - 22, rect.top(), 16, rect.height()),
            Qt.AlignmentFlag.AlignCenter,
            "▾" if expanded else "▸",
        )


def _elide(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _chapter_label(meta: dict | None) -> str:
    if not meta:
        return "章节"
    chapter = meta.get("chapter")
    if chapter:
        return "第 %s 章" % chapter
    return meta.get("title") or "章节"


def _kind_chip(kind: str) -> str:
    color = kind_color(kind)
    return (
        "<span style='color:%s;font-size:12px;font-weight:600;'>%s</span>"
        % (color, html.escape(kind_label(kind)))
    )


def entity_detail_html(
    entity: dict,
    mentions: list[dict] | None = None,
    article_meta: dict[int, dict] | None = None,
    tag_articles: list[dict] | None = None,
    *,
    links: bool = True,
) -> str:
    """实体详情：彩色头部、简介、别名、章节标签和原文证据。"""
    article_meta = article_meta or {}

    def chapter_order(article_id: int) -> int:
        value = (article_meta.get(int(article_id)) or {}).get("chapter")
        try:
            return int(value)
        except (TypeError, ValueError):
            return 9999

    mentions = sorted(
        mentions or [],
        key=lambda row: (
            chapter_order(int(row.get("article_id") or 0)),
            int(row.get("article_id") or 0),
            int(row.get("id") or 0),
        ),
    )
    tag_articles = sorted(
        tag_articles or [],
        key=lambda row: (
            chapter_order(int(row.get("article_id") or 0)),
            int(row.get("article_id") or 0),
        ),
    )
    kind = entity.get("kind") or ""
    color = kind_color(kind)
    name = html.escape(entity.get("name") or "")
    summary = html.escape(entity.get("summary") or "")
    meta = entity.get("meta") or {}
    status = meta.get("status") if isinstance(meta, dict) else ""
    aliases = entity.get("aliases") or []
    tag_count = int(entity.get("tag_count") or len(tag_articles) or 0)
    mention_count = int(entity.get("mention_count") or 0)
    article_count = int(entity.get("article_count") or 0)

    parts = [
        "<table width='100%%' cellspacing='0' cellpadding='14' "
        "style='background-color:%s;'>" % _tint(color, 18),
        "<tr><td>",
        "<p style='margin:0 0 4px 0;'>%s</p>" % _kind_chip(kind),
        "<h1 style='margin:0 0 6px 0;font-size:24px;color:#283540;'>%s</h1>" % name,
    ]
    if summary:
        parts.append(
            "<p style='margin:0;color:#4B585F;font-size:13px;line-height:1.65;'>%s</p>"
            % summary
        )
    if status:
        parts.append(
            "<p style='margin:8px 0 0 0;color:%s;font-size:12px;'>状态：%s</p>"
            % (color, html.escape(str(status)))
        )
    parts.append("</td></tr></table>")

    facts = []
    if aliases:
        facts.append("<b>别名：</b>%s" % html.escape("、".join(aliases)))
    group = entity.get("group_key") or ""
    if group:
        facts.append("<b>分组：</b>%s" % html.escape(group_label(group, kind)))
    if kind in ("arc", "event", "mystery"):
        facts.append("<b>关联章节：</b>%s 章" % tag_count)
    else:
        facts.append("<b>原文出处：</b>%s 段 / %s 篇" % (mention_count, article_count))
    parts.append(
        "<p style='color:#73838A;font-size:12px;margin:12px 0 4px 0;'>%s</p>"
        % "　·　".join(facts)
    )

    if tag_articles:
        heading = "覆盖章节" if kind == "arc" else "关联章节"
        parts.append("<h3 style='margin:16px 0 6px 0;font-size:15px;'>%s</h3>" % heading)
        for index, row in enumerate(tag_articles[:120], 1):
            meta_row = article_meta.get(int(row["article_id"])) or {}
            label = _chapter_label(meta_row)
            title = _elide(meta_row.get("title") or "文章 #%s" % row["article_id"], 30)
            line = "<b>%s</b>　%s" % (html.escape(label), html.escape(title))
            if row.get("note"):
                line += "　<span style='color:#73838A;'>%s</span>" % html.escape(
                    _elide(row["note"], 48)
                )
            if links:
                href = "daoyuan-article:///%s/0" % int(row["article_id"])
                line += "　<a href='%s'>打开</a>" % href
            parts.append("<p style='margin:3px 0;font-size:12px;line-height:1.6;'>%s</p>" % line)

    if kind not in ("arc", "event") or mentions:
        parts.append("<h3 style='margin:16px 0 6px 0;font-size:15px;'>原文证据</h3>")
        if not mentions:
            parts.append("<p style='color:#73838A;'>暂无原文出处。</p>")
        for mention in mentions[:60]:
            article_id = int(mention.get("article_id") or 0)
            meta_row = article_meta.get(article_id) or {}
            label = _chapter_label(meta_row)
            title = _elide(meta_row.get("title") or "文章 #%s" % article_id, 26)
            head = "<b>%s</b>　%s" % (html.escape(label), html.escape(title))
            if links:
                href = "daoyuan-article:///%s/%s" % (
                    article_id,
                    mention.get("segment_id") or 0,
                )
                head += "　<a href='%s'>打开原文</a>" % href
            parts.append("<p style='margin:10px 0 2px 0;font-size:12px;'>%s</p>" % head)
            quote = html.escape(_elide(mention.get("quote") or "", 220))
            parts.append(
                "<p style='margin:0 0 8px 16px;color:#5E6B72;font-size:12px;"
                "line-height:1.65;'>「%s」</p>" % quote
            )
        if len(mentions) > 60:
            parts.append(
                "<p style='color:#73838A;font-size:12px;'>仅显示前 60 条，共 %s 条。</p>"
                % len(mentions)
            )
    return "".join(parts)


def article_context_html(
    context: dict,
    article_meta: dict | None = None,
) -> str:
    """阅读页右侧「本章专题」HTML。"""
    context = context or {}
    note = context.get("note") or {}
    tags = context.get("tags") or []
    entities = context.get("entities") or []
    meta = article_meta or {}
    chapter = meta.get("chapter")
    title = html.escape(meta.get("title") or "")

    parts = [
        "<h2 style='margin:0 0 2px 0;font-size:19px;color:#283540;'>%s</h2>"
        % (("第 %s 章" % chapter) if chapter else "当前章节"),
    ]
    if title:
        parts.append(
            "<p style='margin:0 0 10px 0;color:#73838A;font-size:11px;line-height:1.5;'>%s</p>"
            % title
        )
    summary = (note.get("summary") or "").strip()
    if summary:
        parts.append(
            "<p style='background-color:#F4EFE4;padding:9px 10px;color:#4B585F;"
            "font-size:12px;line-height:1.65;margin:0 0 12px 0;'>%s</p>"
            % html.escape(summary)
        )

    arcs = [row for row in tags if row.get("kind") == "arc"]
    events = [row for row in tags if row.get("kind") == "event"]
    mysteries = [row for row in tags if row.get("kind") == "mystery"]
    if arcs or events or mysteries:
        parts.append("<p style='margin:0 0 4px 0;font-size:12px;color:#73838A;'>剧情定位</p>")
        for row in arcs + events + mysteries:
            color = kind_color(row.get("kind") or "")
            parts.append(
                "<p style='margin:2px 0;font-size:12px;line-height:1.55;'>"
                "<span style='color:%s;font-weight:600;'>%s</span>　%s</p>"
                % (
                    color,
                    html.escape(kind_label(row.get("kind") or "")),
                    html.escape(row.get("name") or ""),
                )
            )
        parts.append("<div style='height:8px;'></div>")

    order = ("person", "organization", "place", "concept", "item")
    grouped: dict[str, list[dict]] = {kind: [] for kind in order}
    for row in entities:
        grouped.setdefault(row.get("kind") or "", []).append(row)
    for kind in order:
        rows = grouped.get(kind) or []
        if not rows:
            continue
        color = kind_color(kind)
        parts.append(
            "<p style='margin:10px 0 3px 0;font-size:12px;font-weight:600;color:%s;'>%s</p>"
            % (color, html.escape(kind_label(kind)))
        )
        links = []
        for row in rows[:10]:
            links.append(
                "<a href='daoyuan-topic:///%s' style='color:#283540;text-decoration:none;'>"
                "%s</a>"
                % (int(row["id"]), html.escape(row.get("name") or ""))
            )
        line = "　".join(links)
        if len(rows) > 10:
            line += "　<span style='color:#73838A;'>等 %s 个</span>" % len(rows)
        parts.append("<p style='margin:0 0 2px 0;font-size:12px;line-height:1.8;'>%s</p>" % line)

    if not entities and not tags:
        parts.append(
            "<p style='color:#73838A;font-size:12px;line-height:1.7;'>"
            "本章暂无专题标注。可在维护端补充章节摘要或标签。</p>"
        )
    else:
        parts.append(
            "<p style='color:#9A9488;font-size:11px;line-height:1.6;margin-top:14px;'>"
            "点击条目查看简介与原文证据。</p>"
        )
    return "".join(parts)


def _tint(color: str, alpha: int) -> str:
    """把主题色轻轻混进纸色，返回 QTextBrowser 支持的不透明 hex。"""
    value = QColor(color)
    paper = QColor("#FFFDF8")
    ratio = max(0, min(255, alpha)) / 255.0
    red = int(value.red() * ratio + paper.red() * (1 - ratio))
    green = int(value.green() * ratio + paper.green() * (1 - ratio))
    blue = int(value.blue() * ratio + paper.blue() * (1 - ratio))
    return "#%02X%02X%02X" % (red, green, blue)
