# -*- coding: utf-8 -*-
"""道源知识库（普通用户端）PySide6 主窗口。"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from PySide6.QtCore import QDate, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __stage__, __version__
from ..ai.service import (
    MODEL_CHOICES,
    clear_api_key,
    clear_qa_history,
    effective_model,
    has_ai_consent,
    has_api_key,
    load_api_key,
    run_ask,
    save_api_key,
    save_model_choice,
    set_ai_consent,
    test_api_key,
    validate_model,
)
from ..core.config import Config, load_config
from ..core.errors import ConfigError, SecurityError
from ..core.lock import ProcessLock
from ..core.text import chapter_number, strip_chapter_prefix, strip_title_date
from ..export.service import (
    default_export_name,
    export_articles,
    load_export_articles,
    safe_filename,
)
from ..reader.service import ReaderService
from ..storage.backup import inspect_backup, restore_backup
from ..topics.catalog import (
    DIMENSIONS,
    DIMENSION_BY_KEY,
    GROUPS,
    group_label,
    kind_color,
    kind_label,
)
from ..topics.store import TopicStore
from ..ui_common.brand import BrandHeader, app_icon, set_windows_app_id
from ..ui_common.theme import GOLD, INK, MUTED, apply_theme
from .chapters import (
    ROLE_CHAPTER,
    ROLE_DATE,
    ROLE_READY,
    ROLE_TITLE,
    ChapterListDelegate,
)
from .topics_view import (
    ROLE_COUNT,
    ROLE_COUNT_SUFFIX,
    ROLE_COLOR,
    ROLE_DEPTH,
    ROLE_ENTITY_ID,
    ROLE_ENTITY_KIND,
    ROLE_EXPANDED,
    ROLE_HAS_CHILDREN,
    ROLE_KEY,
    ROLE_NODE,
    ROLE_SUBTITLE,
    ROLE_TITLE as TOPIC_ROLE_TITLE,
    TopicTreeDelegate,
    article_context_html,
    entity_detail_html,
)

EXPORT_FILTERS = {
    "txt": "TXT 纯文本 (*.txt)",
    "docx": "Word 文档 (*.docx)",
    "pdf": "PDF 文档 (*.pdf)",
}
EXPORT_FORMATS = (
    ("TXT 纯文本", "txt"),
    ("Word 文档（.docx）", "docx"),
    ("PDF 文档", "pdf"),
)
READING_DEFAULTS = {
    "mode": "original",
    "theme": "paper",
    "font_size": 18,
    "line_height": "1.8",
    "width": 820,
}
COLOR_STYLE_RE = re.compile(
    r"(?<!-)(?:background-)?color\s*:\s*[^;\"']+;?", re.I
)
SEARCH_MODES = {
    "fts": "全文索引",
    "like": "短词匹配",
    "like_fallback": "全文索引未命中，已用 LIKE 兜底",
    "empty": "空查询",
}
ANSWER_CITATION_RE = re.compile(r"\[(C(?:[1-9]\d*))\]")


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    return frame


class StatCard(QFrame):
    def __init__(self, label: str, value: str = "0"):
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(4)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("StatValue")
        self.name_label = QLabel(label)
        self.name_label.setObjectName("StatLabel")
        layout.addWidget(self.value_label)
        layout.addWidget(self.name_label)

    def set_value(self, value) -> None:
        self.value_label.setText(str(value))


class HomePage(QWidget):
    def __init__(self, window: "ReaderWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 24)
        outer.setSpacing(18)

        hero = _card()
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(28, 24, 28, 24)
        text_col = QVBoxLayout()
        text_col.setSpacing(8)
        title = QLabel(APP_NAME)
        title.setObjectName("HeroTitle")
        subtitle = QLabel("云山书境 · 文字典藏 · 本地检索")
        subtitle.setObjectName("HeroSub")
        desc = QLabel("保留原文与来源，支持离线阅读、全文检索与导出。")
        desc.setObjectName("Muted")
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        text_col.addWidget(desc)
        hero_layout.addLayout(text_col)
        hero_layout.addStretch(1)
        self.hero_status = QLabel("")
        self.hero_status.setObjectName("Muted")
        hero_layout.addWidget(self.hero_status, 0, Qt.AlignBottom)
        outer.addWidget(hero)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        self.card_collections = StatCard("文集")
        self.card_articles = StatCard("文章")
        self.card_ready = StatCard("可阅读")
        for card in (self.card_collections, self.card_articles, self.card_ready):
            stats_row.addWidget(card)
        outer.addLayout(stats_row)

        section = QHBoxLayout()
        section_title = QLabel("最近收录")
        section_title.setObjectName("PageTitle")
        section.addWidget(section_title)
        section.addStretch(1)
        self.continue_btn = QPushButton("继续阅读")
        self.continue_btn.setObjectName("Ghost")
        self.continue_btn.clicked.connect(self.open_continue)
        section.addWidget(self.continue_btn)
        outer.addLayout(section)

        recent_card = _card()
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(10, 10, 10, 10)
        self.recent = QListWidget()
        self.recent.setAlternatingRowColors(False)
        self.recent.itemDoubleClicked.connect(self.open_recent)
        recent_layout.addWidget(self.recent)
        outer.addWidget(recent_card, 1)

        self.empty_hint = QLabel("")
        self.empty_hint.setObjectName("Muted")
        outer.addWidget(self.empty_hint)
        self.refresh()

    def refresh(self) -> None:
        stats = self.service.home_stats()
        self.card_collections.set_value(stats["collections"])
        self.card_articles.set_value(stats["articles"])
        self.card_ready.set_value(stats["ready"])
        self.recent.clear()
        for item in stats["recent"]:
            text = "%s\n%s · %s" % (
                item.get("title") or "（无标题）",
                item.get("collection_title") or "",
                item.get("publish_date") or "日期未知",
            )
            li = QListWidgetItem(text)
            li.setData(Qt.UserRole, item["id"])
            self.recent.addItem(li)
        if not self.service.available:
            self.empty_hint.setText(
                "尚未找到知识库内容。请先运行维护端「道源采集管理」初始化数据库并采集文章。"
            )
            self.hero_status.setText("内容库未初始化")
        elif stats["ready"] == 0:
            self.empty_hint.setText("知识库已初始化，但还没有成功采集的正文。请在维护端执行采集。")
            self.hero_status.setText("暂无正文")
        else:
            self.empty_hint.setText("")
            health = self.service.health()
            self.hero_status.setText("数据库 schema v%s · %s" % (health.get("schema_version"), health.get("integrity")))
        progress = self.service.continue_reading()
        self.continue_btn.setEnabled(progress is not None)
        if progress is not None:
            self.continue_btn.setToolTip(progress.get("title") or "")

    def open_recent(self, item: QListWidgetItem) -> None:
        self.window_ref.open_article(int(item.data(Qt.UserRole)))

    def open_continue(self) -> None:
        progress = self.service.continue_reading()
        if progress:
            self.window_ref.open_article(int(progress["id"]))


class LibraryPage(QWidget):
    def __init__(self, window: "ReaderWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 24)
        outer.setSpacing(12)

        title = QLabel("文库")
        title.setObjectName("PageTitle")
        subtitle = QLabel("按文集浏览已收录文章；章节按序号排列，双击打开阅读。")
        subtitle.setObjectName("PageSub")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(14)
        splitter.setChildrenCollapsible(False)

        left = _card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 12)
        left_layout.setSpacing(8)
        left_head = QHBoxLayout()
        left_head.setSpacing(6)
        left_label = QLabel("文集")
        left_label.setObjectName("ListHeading")
        self.collection_count = QLabel("")
        self.collection_count.setObjectName("ListHint")
        left_head.addWidget(left_label)
        left_head.addStretch(1)
        left_head.addWidget(self.collection_count)
        self.collections = QListWidget()
        self.collections.setObjectName("CollectionList")
        self.collections.setSpacing(1)
        self.collections.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.collections.currentItemChanged.connect(self.on_collection)
        left_layout.addLayout(left_head)
        left_layout.addWidget(self.collections, 1)

        right = _card()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 12)
        right_layout.setSpacing(4)
        right_head = QHBoxLayout()
        right_head.setSpacing(8)
        self.article_title = QLabel("文章")
        self.article_title.setObjectName("ListHeading")
        self.article_hint = QLabel("")
        self.article_hint.setObjectName("ListHint")
        right_head.addWidget(self.article_title)
        right_head.addStretch(1)
        right_head.addWidget(self.article_hint)
        self.delete_btn = QPushButton("删除文章")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setEnabled(False)
        self.delete_btn.setToolTip("选中一篇文章后，可从文库中删除")
        self.delete_btn.clicked.connect(self.delete_selected_article)
        right_head.addWidget(self.delete_btn)
        self.article_note = QLabel("按章节序号排列 · 双击打开")
        self.article_note.setObjectName("ListHint")
        self.articles = QListWidget()
        self.articles.setObjectName("ChapterList")
        self.articles.setSpacing(0)
        self.articles.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.articles.setMouseTracking(True)
        self.articles.setItemDelegate(ChapterListDelegate(self.articles))
        # Windows 双击会同时发出 itemDoubleClicked 和 itemActivated；
        # 只连接 Activated，双击和回车都只打开一次。
        self.articles.itemActivated.connect(self.open_article)
        self.articles.currentItemChanged.connect(self.on_article_selected)
        right_layout.addLayout(right_head)
        right_layout.addWidget(self.article_note)
        right_layout.addWidget(self.articles, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([286, 738])
        outer.addWidget(splitter, 1)
        self.refresh()

    def refresh(self) -> None:
        self.delete_btn.setEnabled(False)
        current_id = None
        if self.collections.currentItem() is not None:
            current_id = self.collections.currentItem().data(Qt.UserRole)
        self.collections.clear()
        self.articles.clear()
        first_ready_row = 0
        collections = self.service.collections()
        self.collection_count.setText("%d 个文集" % len(collections))
        for index, col in enumerate(collections):
            ready = int(col["ready"] or 0)
            total = int(col["total"] or 0)
            text = "%s\n%d / %d 篇可读" % (
                col["title"] or col["album_id"],
                ready,
                total,
            )
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(0, 56))
            item.setData(Qt.UserRole, int(col["id"]))
            item.setData(Qt.UserRole + 1, ready)
            item.setData(Qt.UserRole + 2, col["title"] or col["album_id"])
            item.setToolTip(
                "%s\n%d 篇可读 / 共 %d 篇" % (col["title"] or "", ready, total)
            )
            self.collections.addItem(item)
            if ready and first_ready_row == 0:
                first_ready_row = index
        if self.collections.count():
            target = 0
            if current_id is not None:
                for index in range(self.collections.count()):
                    if self.collections.item(index).data(Qt.UserRole) == current_id:
                        target = index
                        break
            else:
                target = first_ready_row
            self.collections.setCurrentRow(target)
        else:
            self.article_title.setText("文章")
            self.article_hint.setText("暂无文集")
            self.article_note.setText("请先在维护端采集或导入文章")

    def on_collection(self, current: QListWidgetItem | None, previous=None) -> None:
        self.articles.clear()
        if current is None:
            return
        collection_id = int(current.data(Qt.UserRole))
        self.article_title.setText(
            str(current.data(Qt.UserRole + 2) or "文章")
        )
        rows = self.service.articles(collection_id)
        ready = sum(1 for row in rows if row["status"] == "ready")
        if rows:
            self.article_hint.setText(
                "共 %d 篇 · %d 篇可读" % (len(rows), ready)
                if ready != len(rows)
                else "共 %d 篇" % len(rows)
            )
            numbered = any(row.get("chapter") for row in rows)
            self.article_note.setText(
                "按章节序号排列 · 双击打开"
                if numbered
                else "按收录顺序排列 · 双击打开"
            )
        else:
            self.article_hint.setText("暂无收录")
            self.article_note.setText("该文集还没有可显示的文章")
        for row in rows:
            item = QListWidgetItem(row["title"] or "（无标题）")
            item.setData(Qt.UserRole, row["id"])
            item.setData(
                ROLE_CHAPTER, row.get("chapter") or ""
            )
            item.setData(
                ROLE_TITLE, row.get("display_title") or "（无标题）"
            )
            item.setData(ROLE_DATE, row.get("publish_date") or "日期未知")
            item.setData(ROLE_READY, row["status"] == "ready")
            item.setToolTip(
                "%s\n%s%s"
                % (
                    row["title"] or "（无标题）",
                    row.get("publish_date") or "日期未知",
                    "" if row["status"] == "ready" else " · 待采集",
                )
            )
            self.articles.addItem(item)

    def on_article_selected(
        self, current: QListWidgetItem | None, previous=None
    ) -> None:
        self.delete_btn.setEnabled(current is not None)

    def delete_selected_article(self) -> None:
        item = self.articles.currentItem()
        if item is None:
            return
        article_id = int(item.data(Qt.UserRole))
        article = self.service.article(article_id)
        if article is None:
            self.refresh()
            return
        title = article.get("title") or "（无标题）"
        collection_title = article.get("collection_title") or "未知文集"
        chapter = item.data(ROLE_CHAPTER) or ""
        chapter_text = ("第 %s 章 · " % chapter) if chapter else ""
        box = QMessageBox(self)
        box.setWindowTitle("删除文章")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("确定要从文库中删除这篇文章吗？")
        box.setInformativeText(
            "《%s》\n%s%s\n\n"
            "删除后会同时从阅读列表、全文检索和专题库出处中移除，"
            "阅读进度与书签也会清理。\n\n"
            "删除前会自动保存一份正文快照到数据目录的 deleted_articles 文件夹；"
            "如果这篇文章仍在合集清单里，后续自动采集会跳过它。"
            % (collection_title, chapter_text, title)
        )
        delete_button = box.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        if box.clickedButton() is not delete_button:
            return
        try:
            result = self.service.delete_article(article_id)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.refresh()
        home = self.window_ref.pages.get("home")
        if home is not None and hasattr(home, "refresh"):
            home.refresh()
        self.window_ref.statusBar().showMessage(
            "已删除《%s》· 快照：%s"
            % (result.get("title") or title, result.get("snapshot_path") or ""),
            8000,
        )
        warnings = result.get("warnings") or []
        if warnings:
            QMessageBox.warning(
                self,
                "删除完成，但有关联数据未清理",
                "文章已从内容库删除，但以下派生数据未能处理：\n\n%s\n\n"
                "可以稍后在维护端执行一次专题库增量更新或手动清理。"
                % "\n".join(warnings),
            )

    def open_article(self, item: QListWidgetItem) -> None:
        self.window_ref.open_article(int(item.data(Qt.UserRole)))


class SearchPage(QWidget):
    """标题 + 正文检索，支持文集筛选、排序与命中段落跳转。"""

    def __init__(self, window: "ReaderWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        self.last_rows: list[dict] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 24)
        outer.setSpacing(12)
        title = QLabel("全文检索")
        title.setObjectName("PageTitle")
        subtitle = QLabel("检索标题与正文，双击结果可跳转到命中的原文段落。")
        subtitle.setObjectName("PageSub")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        card = _card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(10)
        first_row = QHBoxLayout()
        first_row.setSpacing(10)
        self.query = QLineEdit()
        self.query.setPlaceholderText("输入关键词，如：人物、地点、主题")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.run_search)
        self.collection = QComboBox()
        self.collection.setMinimumWidth(190)
        self.order = QComboBox()
        self.order.addItem("相关度优先", "rank")
        self.order.addItem("时间优先", "time")
        self.order.setMinimumWidth(120)
        self.order.currentIndexChanged.connect(self.on_order_changed)
        self.search_btn = QPushButton("检索")
        self.search_btn.setObjectName("Primary")
        self.search_btn.clicked.connect(self.run_search)
        first_row.addWidget(self.query, 1)
        first_row.addWidget(self.collection)
        first_row.addWidget(self.order)
        first_row.addWidget(self.search_btn)
        card_layout.addLayout(first_row)

        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        date_label = QLabel("发布日期")
        date_label.setObjectName("Muted")
        self.from_check = QCheckBox("从")
        self.from_date = QDateEdit(QDate.currentDate().addYears(-5))
        self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.from_date.setCalendarPopup(True)
        self.from_date.setEnabled(False)
        self.from_check.toggled.connect(self.from_date.setEnabled)
        self.to_check = QCheckBox("至")
        self.to_date = QDateEdit(QDate.currentDate())
        self.to_date.setDisplayFormat("yyyy-MM-dd")
        self.to_date.setCalendarPopup(True)
        self.to_date.setEnabled(False)
        self.to_check.toggled.connect(self.to_date.setEnabled)
        date_row.addWidget(date_label)
        date_row.addWidget(self.from_check)
        date_row.addWidget(self.from_date)
        date_row.addWidget(self.to_check)
        date_row.addWidget(self.to_date)
        date_row.addStretch(1)
        date_note = QLabel("不勾选则不限日期")
        date_note.setObjectName("Muted")
        date_row.addWidget(date_note)
        card_layout.addLayout(date_row)
        outer.addWidget(card)

        self.status = QLabel("输入关键词开始检索。")
        self.status.setObjectName("Muted")
        outer.addWidget(self.status)

        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.setSpacing(2)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setTextElideMode(Qt.ElideNone)
        # Windows 下双击会同时发出 itemDoubleClicked 和 itemActivated；
        # 两个信号都连到 open_item 会叠开两个阅读窗口，表现为要关闭两次。
        # 只连 itemActivated，双击和键盘回车都能打开，且只打开一次。
        self.results.itemActivated.connect(self.open_item)
        outer.addWidget(self.results, 1)

        hint = QLabel("提示：两字以内关键词会自动使用 LIKE 兜底；结果默认按相关度与发布时间综合排序。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        outer.addWidget(hint)

    def refresh(self) -> None:
        current = self.collection.currentData()
        self.collection.blockSignals(True)
        self.collection.clear()
        self.collection.addItem("全部文集", None)
        for col in self.service.collections():
            self.collection.addItem(
                "%s（%d）" % (col["title"] or col["album_id"], int(col["ready"] or 0)),
                int(col["id"]),
            )
        index = self.collection.findData(current)
        self.collection.setCurrentIndex(index if index >= 0 else 0)
        self.collection.blockSignals(False)
        if not self.service.available:
            self.status.setText("内容库尚未初始化或不可用，请先在维护端采集文章。")

    def run_search(self) -> None:
        term = self.query.text().strip()
        self.results.clear()
        self.last_rows = []
        if not term:
            self.status.setText("请输入关键词。")
            return
        if not self.service.available:
            self.status.setText("内容库尚未初始化或不可用，请先在维护端采集文章。")
            return
        date_from = (
            self.from_date.date().toString("yyyy-MM-dd")
            if self.from_check.isChecked()
            else None
        )
        date_to = (
            self.to_date.date().toString("yyyy-MM-dd")
            if self.to_check.isChecked()
            else None
        )
        if (
            self.from_check.isChecked()
            and self.to_check.isChecked()
            and self.from_date.date() > self.to_date.date()
        ):
            self.status.setText("起始日期不能晚于截止日期。")
            return
        result = self.service.search(
            term,
            collection_id=self.collection.currentData(),
            limit=200,
            date_from=date_from,
            date_to=date_to,
        )
        rows = list(result["results"])
        if self.order.currentData() == "time":
            rows.sort(
                key=lambda row: (row.get("publish_ts") or 0, row.get("article_id") or 0),
                reverse=True,
            )
        self.last_rows = rows
        for row in rows:
            self.results.addItem(self._make_item(row))
        mode = SEARCH_MODES.get(result["mode"], result["mode"])
        range_text = ""
        if date_from or date_to:
            range_text = " · 日期 %s ~ %s" % (
                date_from or "不限",
                date_to or "不限",
            )
        if rows:
            text = "%s · 命中 %d 篇%s" % (mode, result["total_articles"], range_text)
            if result["truncated"]:
                text += "（仅显示前 %d 篇，可缩小范围）" % len(rows)
            self.status.setText(text)
        else:
            self.status.setText(
                "没有找到包含「%s」的文章%s。可尝试更短的关键词或切换文集。"
                % (term, range_text)
            )

    def on_order_changed(self) -> None:
        if self.query.text().strip():
            self.run_search()

    def _make_item(self, row: dict) -> QListWidgetItem:
        flags = []
        if row.get("matched_title"):
            flags.append("标题命中")
        if int(row.get("hit_count") or 0) > 1:
            flags.append("正文命中 %d 处" % int(row["hit_count"]))
        first = row.get("title") or "（无标题）"
        if flags:
            first += "　· " + "，".join(flags)
        meta = " · ".join(
            part for part in (row.get("collection_title"), row.get("publish_date")) if part
        )
        excerpt = (row.get("excerpt") or "").strip()
        if excerpt == (row.get("title") or "").strip():
            excerpt = ""
        lines = [first]
        if meta:
            lines.append(meta)
        if excerpt:
            lines.append(excerpt)
        item = QListWidgetItem("\n".join(lines))
        item.setData(Qt.UserRole, (int(row["article_id"]), row.get("segment_id")))
        item.setToolTip("双击跳转到命中段落")
        return item

    def open_item(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if not data:
            return
        article_id, segment_id = data
        self.window_ref.open_article(int(article_id), segment_id)


class ExportWorker(QThread):
    """在独立线程中装载并导出，避免大文集导出时界面卡顿。"""

    done = Signal(str, int)
    failed = Signal(str)

    def __init__(self, config: Config, collection_id, path: str, fmt: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.collection_id = collection_id
        self.path = path
        self.fmt = fmt

    def run(self) -> None:  # noqa: D401
        service = ReaderService(self.config)
        try:
            articles = load_export_articles(service, self.collection_id)
            if not articles:
                raise RuntimeError("没有可导出的文章")
            export_articles(articles, self.path, self.fmt)
            self.done.emit(self.path, len(articles))
        except Exception as exc:  # 线程内异常必须转成信号
            self.failed.emit(str(exc))
        finally:
            service.close()


class ExportPage(QWidget):
    """按文集与格式导出，默认写入运行数据目录 exports。"""

    def __init__(self, window: "ReaderWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        self.worker: ExportWorker | None = None
        self.last_path: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 24)
        outer.setSpacing(12)
        title = QLabel("导出")
        title.setObjectName("PageTitle")
        subtitle = QLabel("将已采集文章导出为 TXT、Word 或 PDF，保留标题、发布时间与原文链接。")
        subtitle.setObjectName("PageSub")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        card = _card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        card_layout.setSpacing(12)

        scope_row = QHBoxLayout()
        scope_label = QLabel("导出范围")
        scope_label.setMinimumWidth(72)
        self.scope = QComboBox()
        self.scope.setMinimumWidth(320)
        self.scope.setMaximumWidth(520)
        scope_row.addWidget(scope_label)
        scope_row.addWidget(self.scope)
        scope_row.addStretch(1)
        card_layout.addLayout(scope_row)

        format_row = QHBoxLayout()
        format_label = QLabel("导出格式")
        format_label.setMinimumWidth(72)
        self.format = QComboBox()
        for label, fmt in EXPORT_FORMATS:
            self.format.addItem(label, fmt)
        format_row.addWidget(format_label)
        format_row.addWidget(self.format)
        format_row.addStretch(1)
        card_layout.addLayout(format_row)

        action_row = QHBoxLayout()
        self.export_btn = QPushButton("开始导出")
        self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self.start_export)
        self.open_btn = QPushButton("打开所在文件夹")
        self.open_btn.setObjectName("Ghost")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_folder)
        action_row.addWidget(self.export_btn)
        action_row.addWidget(self.open_btn)
        action_row.addStretch(1)
        card_layout.addLayout(action_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        card_layout.addWidget(self.progress)
        outer.addWidget(card)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.status)
        outer.addStretch(1)

        note = QLabel(
            "说明：导出文件默认保存在数据目录的 exports 文件夹；PDF 使用本机中文字体排版。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        outer.addWidget(note)

    def refresh(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        current = self.scope.currentData()
        self.scope.blockSignals(True)
        self.scope.clear()
        total = len(self.service.ready_articles())
        self.scope.addItem("全部文集（%d 篇已采集）" % total, None)
        for col in self.service.collections():
            self.scope.addItem(
                "%s（%d 篇）" % (col["title"] or col["album_id"], int(col["ready"] or 0)),
                int(col["id"]),
            )
        index = self.scope.findData(current)
        self.scope.setCurrentIndex(index if index >= 0 else 0)
        self.scope.blockSignals(False)
        if self.last_path is None:
            self.status.setText(
                "当前可导出 %d 篇文章。" % total
                if total
                else "暂无可导出文章，请先在维护端采集。"
            )

    def start_export(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        collection_id = self.scope.currentData()
        fmt = self.format.currentData()
        articles = self.service.ready_articles(collection_id)
        if not articles:
            self.status.setText("当前范围没有可导出的文章。")
            return
        index = self.scope.currentIndex()
        scope_title = self.scope.itemText(index).split("（")[0]
        prefix = "道源知识库_全部" if collection_id is None else "道源知识库_%s" % safe_filename(scope_title)
        exports_dir = self.service.config.paths.exports
        exports_dir.mkdir(parents=True, exist_ok=True)
        default_path = exports_dir / default_export_name(prefix, fmt)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出文章", str(default_path), EXPORT_FILTERS[fmt]
        )
        if not path:
            return
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        self.begin_export(path, fmt, collection_id, len(articles))

    def begin_export(
        self, path: str, fmt: str, collection_id, article_count: int
    ) -> None:
        """对话框确认后启动线程，便于测试直接调用。"""
        self.export_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.status.setText(
            "正在导出 %d 篇到 %s，请稍候…" % (article_count, Path(path).name)
        )
        self.worker = ExportWorker(self.service.config, collection_id, path, fmt, self)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_thread_finished)
        self.worker.start()

    def on_done(self, path: str, count: int) -> None:
        self.last_path = Path(path)
        self.open_btn.setEnabled(True)
        self.status.setText("已导出 %d 篇：%s" % (count, path))
        self.status.setToolTip(path)

    def on_failed(self, message: str) -> None:
        self.status.setText("导出失败：%s" % message)
        QMessageBox.warning(self, "导出失败", message)

    def on_thread_finished(self) -> None:
        self.export_btn.setEnabled(True)
        self.progress.setVisible(False)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def open_folder(self) -> None:
        if self.last_path is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_path.parent)))

    def wait_for_export(self, timeout_ms: int = 15000) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(timeout_ms)


class AskWorker(QThread):
    """在线提问在独立线程完成，避免网络超时卡住阅读界面。"""

    done = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        config: Config,
        question: str,
        history: list[dict],
        previous_question: str | None,
        session_id: int | None,
        local_only: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.question = question
        self.history = list(history)
        self.previous_question = previous_question
        self.session_id = session_id
        self.local_only = local_only

    def run(self) -> None:  # noqa: D401
        try:
            result = run_ask(
                self.config,
                self.question,
                history=self.history,
                previous_question=self.previous_question,
                session_id=self.session_id,
                local_only=self.local_only,
            )
            self.done.emit(result)
        except Exception as exc:  # 线程内异常必须转为信号
            self.failed.emit(str(exc) or "智问请求失败")


class KeyTestWorker(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, config: Config, key: str, model: str | None = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.key = key
        self.model = model

    def run(self) -> None:  # noqa: D401
        try:
            key = self.key or load_api_key(self.config)
            if not key:
                raise RuntimeError("请先输入 API Key。")
            self.done.emit(test_api_key(self.config, key, model=self.model))
        except Exception as exc:
            self.failed.emit(str(exc) or "连接测试失败")


class ApiKeyDialog(QDialog):
    """API Key 与模型配置：Key 只写入 Windows 用户级安全存储。"""

    def __init__(self, config: Config, parent=None, service: ReaderService | None = None):
        super().__init__(parent)
        self.config = config
        self.service = service
        self.worker: KeyTestWorker | None = None
        self.setWindowTitle("配置 DeepSeek API Key")
        self.setModal(True)
        self.resize(660, 410)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel("DeepSeek API Key")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel(
            "Key 使用 Windows 用户级安全存储（DPAPI）加密，仅当前 Windows 用户可解密；"
            "不会写入源码、配置文件、文章数据库或日志。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.state_label = QLabel("")
        self.state_label.setObjectName("Muted")
        layout.addWidget(self.state_label)

        model_row = QHBoxLayout()
        model_row.setSpacing(10)
        model_row.addWidget(QLabel("模型"))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.addItems(MODEL_CHOICES)
        self.model_combo.setCurrentText(effective_model(config, service))
        self.model_combo.setMinimumWidth(250)
        self.model_combo.setEnabled(service is not None)
        model_row.addWidget(self.model_combo)
        model_row.addStretch(1)
        layout.addLayout(model_row)
        model_hint = QLabel(
            "默认 deepseek-flash（成本较低）；可切换 deepseek-v4-pro，或填写官方文档中的"
            "新模型标识。模型只影响在线问答，本地阅读、检索和导出不受影响。"
        )
        model_hint.setObjectName("Muted")
        model_hint.setWordWrap(True)
        layout.addWidget(model_hint)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setClearButtonEnabled(True)
        layout.addWidget(self.key_edit)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setObjectName("Ghost")
        self.test_btn.clicked.connect(self.test_connection)
        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self.save_key)
        self.clear_btn = QPushButton("清除 Key")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.clicked.connect(self.clear_key)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("Ghost")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(self.test_btn)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self.refresh_state()

    def refresh_state(self) -> None:
        exists = has_api_key(self.config)
        model = effective_model(self.config, self.service)
        self.state_label.setText(
            (
                "当前状态：已保存（不显示原 Key）· 模型 %s" % model
                if exists
                else "当前状态：未配置 · 模型 %s\n"
                "没有 Key 仍可正常阅读、检索和导出。" % model
            )
        )
        self.key_edit.setPlaceholderText(
            "已保存；留空表示不修改" if exists else "粘贴 DeepSeek API Key（sk-...）"
        )
        self.clear_btn.setEnabled(exists)

    def save_key(self) -> None:
        try:
            model = validate_model(self.model_combo.currentText())
        except ConfigError as exc:
            self.status_label.setText(str(exc))
            return
        if self.service is not None:
            try:
                save_model_choice(self.service, model)
            except Exception as exc:
                self.status_label.setText("模型设置保存失败：%s" % exc)
                return
        key = self.key_edit.text().strip()
        if not key:
            if has_api_key(self.config):
                self.status_label.setText(
                    "模型已保存为 %s；API Key 未修改。" % model
                )
            elif self.service is not None:
                self.status_label.setText(
                    "模型已保存为 %s；还需填写 API Key 才能在线问答。" % model
                )
            else:
                self.status_label.setText("请输入 API Key。")
            self.refresh_state()
            return
        try:
            save_api_key(self.config, key)
        except SecurityError as exc:
            self.status_label.setText(str(exc))
            return
        self.key_edit.clear()
        self.status_label.setText(
            "已保存到 Windows 安全存储，当前模型 %s。建议点击「测试连接」确认可用。"
            % model
        )
        self.refresh_state()

    def clear_key(self) -> None:
        if not has_api_key(self.config):
            self.status_label.setText("当前没有已保存的 Key。")
            return
        answer = QMessageBox.question(
            self,
            "清除 API Key",
            "将删除本机保存的 DeepSeek API Key。清除后仍可阅读、检索和导出。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            clear_api_key(self.config)
        except SecurityError as exc:
            self.status_label.setText(str(exc))
            return
        self.status_label.setText("已清除本机保存的 API Key。")
        self.refresh_state()

    def test_connection(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        key = self.key_edit.text().strip()
        if not key and not has_api_key(self.config):
            self.status_label.setText("请先输入 API Key。")
            return
        try:
            model = validate_model(self.model_combo.currentText())
        except ConfigError as exc:
            self.status_label.setText(str(exc))
            return
        self._set_busy(True)
        self.status_label.setText("正在用最低 Token 请求测试连接…")
        self.worker = KeyTestWorker(self.config, key, model, self)
        self.worker.done.connect(self.on_test_done)
        self.worker.failed.connect(self.on_test_failed)
        self.worker.finished.connect(self.on_test_finished)
        self.worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.test_btn.setEnabled(not busy)
        self.save_btn.setEnabled(not busy)
        self.clear_btn.setEnabled(not busy and has_api_key(self.config))
        self.key_edit.setEnabled(not busy)
        self.model_combo.setEnabled(not busy and self.service is not None)

    def on_test_done(self, result: dict) -> None:
        usage = result.get("usage") or {}
        if self.service is not None and usage:
            try:
                self.service.state.log_ai_usage(
                    "test", result.get("model") or "", usage
                )
            except Exception:
                pass
        if usage.get("total_tokens"):
            token_text = " · Tokens %s" % format(
                int(usage["total_tokens"]), ","
            )
        else:
            token_text = ""
        self.status_label.setText(
            "连接成功 · 模型 %s%s" % (result.get("model") or "DeepSeek", token_text)
        )

    def on_test_failed(self, message: str) -> None:
        self.status_label.setText("测试失败：%s" % message)

    def on_test_finished(self) -> None:
        self._set_busy(False)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def reject(self) -> None:  # noqa: D401
        self.close()

    def accept(self) -> None:  # noqa: D401
        self.close()

    def closeEvent(self, event):  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(35000)
            if self.worker.isRunning():
                self.status_label.setText("连接测试仍在进行，请稍候再关闭。")
                event.ignore()
                return
        super().closeEvent(event)


class UsageDialog(QDialog):
    """Token 用量统计：读取个人状态库中的 ai_usage 流水。"""

    def __init__(self, service: ReaderService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Token 用量统计")
        self.setModal(True)
        self.resize(780, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel("Token 用量统计")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel(
            "统计本机向 DeepSeek 发起的真实请求；只记录模型、Token 数和时间，"
            "不记录问题、回答或 API Key。本地检索不计入。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(False)
        layout.addWidget(self.body, 1)

        buttons = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("Ghost")
        refresh_btn.clicked.connect(self.refresh)
        clear_btn = QPushButton("清空用量统计")
        clear_btn.setObjectName("Ghost")
        clear_btn.clicked.connect(self.clear_stats)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("Primary")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(refresh_btn)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        try:
            stats = self.service.state.ai_usage_stats()
        except Exception as exc:
            self.body.setHtml(
                "<p style='color:#9A5B45;'>读取 Token 统计失败：%s</p>"
                % html.escape(str(exc))
            )
            return
        self.body.setHtml(self._usage_html(stats))

    def clear_stats(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空用量统计",
            "将删除本机保存的全部 Token 用量流水，不影响文章、问答记录和 API Key。"
            "是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.service.state.clear_ai_usage()
        except Exception as exc:
            QMessageBox.warning(self, "清空失败", str(exc))
            return
        self.refresh()

    @staticmethod
    def _num(value) -> str:
        try:
            return format(int(value or 0), ",")
        except (TypeError, ValueError):
            return "0"

    @classmethod
    def _usage_html(cls, stats: dict) -> str:
        periods = (
            ("今日", stats.get("today") or {}),
            ("近 7 天", stats.get("last7") or {}),
            ("近 30 天", stats.get("last30") or {}),
            ("累计", stats.get("all") or {}),
        )
        rows = []
        for label, bucket in periods:
            rows.append(
                "<tr>"
                "<td style='padding:7px 10px; font-weight:600;'>%s</td>"
                "<td align='right' style='padding:7px 10px;'>%s</td>"
                "<td align='right' style='padding:7px 10px;'>%s</td>"
                "<td align='right' style='padding:7px 10px;'>%s</td>"
                "<td align='right' style='padding:7px 10px; font-weight:600; "
                "color:#8A6A32;'>%s</td>"
                "</tr>"
                % (
                    label,
                    cls._num(bucket.get("calls")),
                    cls._num(bucket.get("prompt_tokens")),
                    cls._num(bucket.get("completion_tokens")),
                    cls._num(bucket.get("total_tokens")),
                )
            )
        total_row = (
            "<table width='100%%' cellspacing='0' cellpadding='0' "
            "style='border-collapse:collapse;'>"
            "<tr style='color:#6E7B87;'>"
            "<td style='padding:6px 10px;'>区间</td>"
            "<td align='right' style='padding:6px 10px;'>请求次数</td>"
            "<td align='right' style='padding:6px 10px;'>输入 Tokens</td>"
            "<td align='right' style='padding:6px 10px;'>输出 Tokens</td>"
            "<td align='right' style='padding:6px 10px;'>合计 Tokens</td>"
            "</tr>%s</table>" % "".join(rows)
        )

        model_rows = []
        for item in stats.get("by_model") or []:
            model_rows.append(
                "<tr>"
                "<td style='padding:6px 10px;'>%s</td>"
                "<td align='right' style='padding:6px 10px;'>%s</td>"
                "<td align='right' style='padding:6px 10px;'>%s</td>"
                "<td align='right' style='padding:6px 10px;'>%s</td>"
                "<td align='right' style='padding:6px 10px;'>%s</td>"
                "</tr>"
                % (
                    html.escape(item.get("model") or "未知模型"),
                    cls._num(item.get("calls")),
                    cls._num(item.get("prompt_tokens")),
                    cls._num(item.get("completion_tokens")),
                    cls._num(item.get("total_tokens")),
                )
            )
        model_table = (
            "<table width='100%%' cellspacing='0' cellpadding='0' "
            "style='border-collapse:collapse;'>"
            "<tr style='color:#6E7B87;'>"
            "<td style='padding:6px 10px;'>模型</td>"
            "<td align='right' style='padding:6px 10px;'>请求次数</td>"
            "<td align='right' style='padding:6px 10px;'>输入</td>"
            "<td align='right' style='padding:6px 10px;'>输出</td>"
            "<td align='right' style='padding:6px 10px;'>合计</td>"
            "</tr>%s</table>" % ("".join(model_rows) or "<tr><td colspan='5' style='padding:8px 10px; color:#6E7B87;'>暂无记录</td></tr>")
        )

        kind_names = {"ask": "智问", "test": "连接测试"}
        recent_rows = []
        for item in stats.get("recent") or []:
            created = (item.get("created_at") or "").replace("T", " ")[:19]
            recent_rows.append(
                "<tr>"
                "<td style='padding:5px 10px;'>%s</td>"
                "<td style='padding:5px 10px;'>%s</td>"
                "<td style='padding:5px 10px;'>%s</td>"
                "<td align='right' style='padding:5px 10px;'>%s</td>"
                "<td align='right' style='padding:5px 10px;'>%s</td>"
                "<td align='right' style='padding:5px 10px;'>%s</td>"
                "</tr>"
                % (
                    html.escape(created),
                    html.escape(kind_names.get(item.get("kind"), item.get("kind") or "")),
                    html.escape(item.get("model") or "未知模型"),
                    cls._num(item.get("prompt_tokens")),
                    cls._num(item.get("completion_tokens")),
                    cls._num(item.get("total_tokens")),
                )
            )
        recent_table = (
            "<table width='100%%' cellspacing='0' cellpadding='0' "
            "style='border-collapse:collapse;'>"
            "<tr style='color:#6E7B87;'>"
            "<td style='padding:6px 10px;'>时间</td>"
            "<td style='padding:6px 10px;'>类型</td>"
            "<td style='padding:6px 10px;'>模型</td>"
            "<td align='right' style='padding:6px 10px;'>输入</td>"
            "<td align='right' style='padding:6px 10px;'>输出</td>"
            "<td align='right' style='padding:6px 10px;'>合计</td>"
            "</tr>%s</table>" % ("".join(recent_rows) or "<tr><td colspan='6' style='padding:8px 10px; color:#6E7B87;'>暂无记录</td></tr>")
        )

        all_bucket = stats.get("all") or {}
        if int(all_bucket.get("calls") or 0) == 0:
            hint = (
                "<p style='padding:10px 12px; background:#F4EFE4; color:#6E7B87;'>"
                "还没有 Token 用量记录。发起一次在线提问或连接测试后，这里会自动累计。</p>"
            )
        else:
            hint = ""
        return (
            "<div style='font-size:14px; line-height:1.65; color:#283540;'>"
            "%s"
            "<h3 style='margin:12px 0 4px 0;'>用量汇总</h3>%s"
            "<h3 style='margin:16px 0 4px 0;'>按模型</h3>%s"
            "<h3 style='margin:16px 0 4px 0;'>最近 20 次请求</h3>%s"
            "<p style='color:#6E7B87; margin-top:12px;'>说明：Token 数为 DeepSeek 接口返回值；"
            "不同模型单价不同，实际费用以 DeepSeek 官方账单为准。</p>"
            "</div>"
            % (hint, total_row, model_table, recent_table)
        )


class AskPage(QWidget):
    """道源智问：本地召回 → 用户确认 → DeepSeek 依据证据回答。"""

    def __init__(self, window: "ReaderWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        self.worker: AskWorker | None = None
        self.session_id: int | None = None
        self.history: list[dict] = []
        self.last_result: dict | None = None
        self.cumulative_tokens = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 24)
        outer.setSpacing(12)
        title = QLabel("道源智问")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "先在本地召回原文段落，再由 DeepSeek 依据证据回答；引用编号可点击回到原文核对。"
        )
        subtitle.setObjectName("PageSub")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        top_card = _card()
        top_layout = QVBoxLayout(top_card)
        top_layout.setContentsMargins(18, 14, 18, 14)
        top_layout.setSpacing(8)
        key_row = QHBoxLayout()
        self.key_state = QLabel("")
        self.key_state.setObjectName("Muted")
        self.key_btn = QPushButton("配置 API Key")
        self.key_btn.setObjectName("Ghost")
        self.key_btn.clicked.connect(self.open_key_dialog)
        self.new_btn = QPushButton("新会话")
        self.new_btn.setObjectName("Ghost")
        self.new_btn.clicked.connect(self.new_session)
        self.clear_btn = QPushButton("清除问答记录")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.clicked.connect(self.clear_records)
        self.usage_btn = QPushButton("Token 用量")
        self.usage_btn.setObjectName("Ghost")
        self.usage_btn.clicked.connect(self.open_usage_dialog)
        key_row.addWidget(self.key_state, 1)
        key_row.addWidget(self.usage_btn)
        key_row.addWidget(self.key_btn)
        key_row.addWidget(self.new_btn)
        key_row.addWidget(self.clear_btn)
        top_layout.addLayout(key_row)
        privacy = QLabel(
            "隐私提示：在线提问会把本次问题和本地召回的必要原文片段发送给第三方 DeepSeek；"
            "不会上传整个数据库、全部文章或个人阅读笔记。可选择「仅本地检索」。"
        )
        privacy.setObjectName("Muted")
        privacy.setWordWrap(True)
        top_layout.addWidget(privacy)
        self.usage_summary = QLabel("")
        self.usage_summary.setObjectName("Muted")
        self.usage_summary.setWordWrap(True)
        top_layout.addWidget(self.usage_summary)
        outer.addWidget(top_card)

        ask_card = _card()
        ask_layout = QVBoxLayout(ask_card)
        ask_layout.setContentsMargins(18, 14, 18, 14)
        ask_layout.setSpacing(8)
        question_row = QHBoxLayout()
        self.question = QLineEdit()
        self.question.setPlaceholderText(
            "输入一个关于原文的问题，例如：主要人物之间是什么关系？"
        )
        self.question.setClearButtonEnabled(True)
        self.question.returnPressed.connect(self.start_ask)
        self.ask_btn = QPushButton("依据原文提问")
        self.ask_btn.setObjectName("Primary")
        self.ask_btn.clicked.connect(self.start_ask)
        self.local_btn = QPushButton("仅本地检索")
        self.local_btn.setObjectName("Ghost")
        self.local_btn.clicked.connect(lambda: self.start_ask(local_only=True))
        question_row.addWidget(self.question, 1)
        question_row.addWidget(self.ask_btn)
        question_row.addWidget(self.local_btn)
        ask_layout.addLayout(question_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        ask_layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        ask_layout.addWidget(self.status)
        outer.addWidget(ask_card)

        answer_card = _card()
        answer_layout = QVBoxLayout(answer_card)
        answer_layout.setContentsMargins(18, 14, 18, 14)
        answer_layout.setSpacing(6)
        answer_header = QHBoxLayout()
        answer_title = QLabel("回答")
        answer_title.setObjectName("StatLabel")
        self.confidence_label = QLabel("")
        self.confidence_label.setObjectName("Muted")
        answer_header.addWidget(answer_title)
        answer_header.addStretch(1)
        answer_header.addWidget(self.confidence_label)
        answer_layout.addLayout(answer_header)
        self.answer_view = QTextBrowser()
        self.answer_view.setOpenExternalLinks(False)
        self.answer_view.setOpenLinks(False)
        self.answer_view.setMinimumHeight(170)
        self.answer_view.anchorClicked.connect(self.on_anchor_clicked)
        answer_layout.addWidget(self.answer_view, 1)
        footer = QHBoxLayout()
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #9A5B45;")
        self.usage = QLabel("")
        self.usage.setObjectName("Muted")
        self.fallback_btn = QPushButton("转到全文检索")
        self.fallback_btn.setObjectName("Ghost")
        self.fallback_btn.clicked.connect(self.open_local_search)
        footer.addWidget(self.warning, 1)
        footer.addWidget(self.usage)
        footer.addWidget(self.fallback_btn)
        answer_layout.addLayout(footer)
        outer.addWidget(answer_card, 1)

        evidence_card = _card()
        evidence_layout = QVBoxLayout(evidence_card)
        evidence_layout.setContentsMargins(18, 14, 18, 14)
        evidence_layout.setSpacing(6)
        evidence_header = QHBoxLayout()
        evidence_title = QLabel("本地候选原文")
        evidence_title.setObjectName("StatLabel")
        evidence_hint = QLabel("按命中强度排序；双击可回到原文该段")
        evidence_hint.setObjectName("Muted")
        evidence_header.addWidget(evidence_title)
        evidence_header.addStretch(1)
        evidence_header.addWidget(evidence_hint)
        evidence_layout.addLayout(evidence_header)
        self.evidence_list = QListWidget()
        self.evidence_list.setWordWrap(True)
        self.evidence_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.evidence_list.setTextElideMode(Qt.ElideNone)
        self.evidence_list.itemDoubleClicked.connect(self.open_evidence)
        self.evidence_list.itemActivated.connect(self.open_evidence)
        evidence_layout.addWidget(self.evidence_list, 1)
        outer.addWidget(evidence_card, 1)
        self.refresh()

    def refresh(self) -> None:
        exists = has_api_key(self.service.config)
        model = effective_model(self.service.config, self.service)
        self.key_state.setText(
            (
                "API Key：已保存到 Windows 安全存储 · 模型 %s" % model
                if exists
                else "API Key：未配置（仍可使用本地检索；AI 回答需要 Key）· 默认模型 %s"
                % model
            )
        )
        available = self.service.available
        self.ask_btn.setEnabled(available)
        self.local_btn.setEnabled(available)
        if not available:
            self.status.setText("内容库尚未初始化或不可用，请先在维护端采集文章。")
        self.refresh_usage()

    def refresh_usage(self) -> None:
        try:
            stats = self.service.state.ai_usage_stats(recent_limit=0)
        except Exception:
            self.usage_summary.setText("")
            return
        today = int((stats.get("today") or {}).get("total_tokens") or 0)
        last30 = int((stats.get("last30") or {}).get("total_tokens") or 0)
        all_bucket = stats.get("all") or {}
        total = int(all_bucket.get("total_tokens") or 0)
        calls = int(all_bucket.get("calls") or 0)
        self.usage_summary.setText(
            "Token 用量：今日 %s · 近 30 天 %s · 累计 %s（共 %s 次请求）"
            % (
                format(today, ","),
                format(last30, ","),
                format(total, ","),
                format(calls, ","),
            )
        )

    def open_usage_dialog(self) -> None:
        dialog = UsageDialog(self.service, self)
        dialog.exec()
        self.refresh_usage()

    def open_key_dialog(self) -> None:
        dialog = ApiKeyDialog(self.service.config, self, service=self.service)
        dialog.exec()
        self.refresh()

    def _has_key(self) -> bool:
        try:
            return has_api_key(self.service.config)
        except Exception:
            return False

    def _ask_consent(self) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("在线智问确认")
        box.setText("是否把本次问题和必要原文片段发送给第三方 DeepSeek？")
        box.setInformativeText(
            "只发送本地召回后的少量段落，不会上传整个数据库、全部文章或个人笔记。"
            "如果不同意，可选择仅在本机检索。"
        )
        agree = box.addButton("同意并提问", QMessageBox.AcceptRole)
        local = box.addButton("仅本地检索", QMessageBox.ActionRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is agree:
            return "agree"
        if clicked is local:
            return "local"
        return "cancel"

    def start_ask(self, local_only: bool = False) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.service.available:
            self.status.setText("内容库尚未初始化或不可用，请先在维护端采集文章。")
            return
        question = self.question.text().strip()
        if not question:
            self.status.setText("请输入问题。")
            return
        if not local_only and self._has_key() and not has_ai_consent(self.service):
            choice = self._ask_consent()
            if choice == "cancel":
                return
            if choice == "local":
                local_only = True
            elif choice == "agree":
                set_ai_consent(self.service, True)

        previous = next(
            (
                item["content"]
                for item in reversed(self.history)
                if item.get("role") == "user"
            ),
            None,
        )
        self.last_result = None
        self.answer_view.clear()
        self.evidence_list.clear()
        self.warning.setText("")
        self.usage.setText("")
        self.confidence_label.setText("")
        self.fallback_btn.setEnabled(True)
        self._set_busy(True)
        self.status.setText(
            "正在本地召回原文段落…" if local_only else "正在召回本机原文并请求 DeepSeek…"
        )
        self.worker = AskWorker(
            self.service.config,
            question,
            self.history,
            previous,
            self.session_id,
            local_only,
            self,
        )
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_thread_finished)
        self.worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.ask_btn.setEnabled(not busy and self.service.available)
        self.local_btn.setEnabled(not busy and self.service.available)
        self.question.setEnabled(not busy)
        self.progress.setVisible(busy)

    def on_done(self, result: dict) -> None:
        self.last_result = result
        self._set_busy(False)
        self._render_result(result)
        self.refresh_usage()

    def on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.warning.setText("智问请求失败：%s" % message)
        self.status.setText("请求失败；本地阅读和全文检索不受影响。")
        self.fallback_btn.setEnabled(True)

    def on_thread_finished(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def _render_result(self, result: dict) -> None:
        evidence = result.get("evidence") or []
        model_used = bool(result.get("model_used"))
        answer = result.get("answer") or ""
        if model_used:
            self.answer_view.setHtml(self._answer_html(answer, evidence))
            citations = result.get("citations") or []
            confidence = result.get("confidence") or (
                "原文直接支持" if citations else "未标注"
            )
            self.confidence_label.setText(
                "依据等级：%s · 可核验引用 %d 条" % (confidence, len(citations))
            )
        else:
            safe = html.escape(answer or "").replace("\n", "<br>")
            self.answer_view.setHtml(
                '<div style="font-size:15px; line-height:1.8; color:#283540;">%s</div>'
                % safe
            )
            self.confidence_label.setText("本地检索结果")
        self.answer_view.verticalScrollBar().setValue(0)

        notices = []
        if result.get("key_error"):
            notices.append(result["key_error"])
        if result.get("invalid_citations"):
            notices.append(
                "模型引用了不存在的编号 %s，已拒绝标记为可靠出处。"
                % "、".join("[%s]" % label for label in result["invalid_citations"])
            )
        if model_used and result.get("uncited"):
            notices.append("模型没有给出可核验的引用，请以下方原文段落自行核对。")
        if result.get("save_warning"):
            notices.append(result["save_warning"])
        if result.get("truncated"):
            notices.append(
                "本次回答达到长度上限，末尾可能被截断；"
                "可在 config.toml 中调大 max_answer_tokens。"
            )
        if result.get("failure_reason") == "no_key":
            notices.append("未配置 API Key，本次只展示本地候选；配置后可生成回答。")
        elif result.get("failure_reason") == "local_only":
            notices.append("本次仅本地检索，未向第三方发送任何内容。")
        elif result.get("failure_reason") == "api_error":
            notices.append("DeepSeek 暂时不可用，本地候选段落仍可阅读。")
        self.warning.setText(" · ".join(notices))

        usage = result.get("usage") or {}
        if usage.get("total_tokens"):
            self.cumulative_tokens += int(usage["total_tokens"])
            self.usage.setText(
                "本次 Tokens %s · 本会话累计 %s"
                % (usage["total_tokens"], self.cumulative_tokens)
            )
        else:
            self.usage.setText("")

        self.evidence_list.clear()
        for item in evidence:
            title = item.get("title") or "（无标题）"
            date_text = " · %s" % item["publish_date"] if item.get("publish_date") else ""
            text = "%s 《%s》%s\n%s" % (
                item.get("label") or "",
                title,
                date_text,
                (item.get("text") or "")[:220],
            )
            li = QListWidgetItem(text)
            li.setData(
                Qt.UserRole,
                (int(item["article_id"]), item.get("segment_id")),
            )
            li.setToolTip("双击回到原文该段")
            self.evidence_list.addItem(li)

        if model_used and result.get("session_id"):
            self.session_id = int(result["session_id"])
            self.history.append({"role": "user", "content": result["question"]})
            self.history.append({"role": "assistant", "content": answer})
            self.history = self.history[-8:]
        terms = result.get("terms") or []
        term_text = "、".join(terms[:8]) if terms else "无明确关键词"
        if evidence:
            self.status.setText(
                "本地召回 %d 段 · 关键词：%s%s"
                % (
                    len(evidence),
                    term_text,
                    " · 回答已保存到本地问答记录"
                    if result.get("session_id")
                    else "",
                )
            )
        elif result.get("failure_reason") == "no_evidence":
            self.status.setText("本地没有召回相关段落。可尝试更短的关键词后重新提问。")
        else:
            self.status.setText("本地召回 %d 段 · 关键词：%s" % (0, term_text))

    @staticmethod
    def _answer_html(answer: str, evidence: list[dict]) -> str:
        valid = {item.get("label") for item in evidence}
        safe = html.escape(answer or "")
        safe = safe.replace("\n", "<br>")

        def repl(match: re.Match) -> str:
            label = match.group(1)
            if label not in valid:
                return match.group(0)
            return (
                '<a href="cite:%s" style="color:#B49763; font-weight:600; '
                'text-decoration:none;">[%s]</a>' % (label, label)
            )

        safe = ANSWER_CITATION_RE.sub(repl, safe)
        return (
            '<div style="font-size:15px; line-height:1.85; color:#283540;">%s</div>'
            % safe
        )

    def on_anchor_clicked(self, url: QUrl) -> None:
        if url.scheme() != "cite" or not self.last_result:
            return
        label = url.toString().split(":", 1)[-1].strip()
        for item in self.last_result.get("evidence") or []:
            if item.get("label") == label:
                self.window_ref.open_article(
                    int(item["article_id"]), item.get("segment_id")
                )
                return

    def open_evidence(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if not data:
            return
        article_id, segment_id = data
        self.window_ref.open_article(int(article_id), segment_id)

    def open_local_search(self) -> None:
        term = self.question.text().strip()
        self.window_ref.search_for(term)

    def new_session(self) -> None:
        self.session_id = None
        self.history = []
        self.last_result = None
        self.cumulative_tokens = 0
        self.answer_view.clear()
        self.evidence_list.clear()
        self.warning.setText("")
        self.usage.setText("")
        self.confidence_label.setText("")
        self.status.setText("已开始新的问答会话。")

    def clear_records(self) -> None:
        answer = QMessageBox.question(
            self,
            "清除问答记录",
            "将删除本机个人状态库中的全部智问会话、回答和引用记录，不会删除文章。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            clear_qa_history(self.service)
        except Exception as exc:
            self.warning.setText("清除问答记录失败：%s" % exc)
            return
        self.new_session()
        self.status.setText("已清除本地问答记录。")

    def wait_for_worker(self, timeout_ms: int = 65000) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(timeout_ms)


class TopicsPage(QWidget):
    """阅读端专题库：只展示维护端已收录的实体和原文证据。"""

    def __init__(self, window: "ReaderWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        self._loading = False
        self._selected_id: int | None = None
        self._initial_collection_selected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(10)
        title = QLabel("专题库")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "按六个维度整理当前文集：人物、组织与势力、地点与场景、"
            "概念与设定、物件与技术、剧情与伏笔。这里只展示维护端已收录的条目，"
            "每条都能回到文章原文；更新中的文集会随采集持续补充。"
        )
        subtitle.setObjectName("PageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        filters.addWidget(QLabel("文集"))
        self.collection_combo = QComboBox()
        self.collection_combo.setMinimumWidth(180)
        self.collection_combo.setMaximumWidth(280)
        self.collection_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.collection_combo)
        filters.addWidget(QLabel("维度"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("全部维度", None)
        for dimension in DIMENSIONS:
            self.kind_combo.addItem(dimension["label"], dimension["key"])
        self.kind_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.kind_combo)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索名称或别名")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self.refresh)
        filters.addWidget(self.search_edit, 1)
        self.expand_btn = QPushButton("展开")
        self.expand_btn.setObjectName("Ghost")
        self.expand_btn.clicked.connect(self.expand_all)
        filters.addWidget(self.expand_btn)
        self.collapse_btn = QPushButton("收起")
        self.collapse_btn.setObjectName("Ghost")
        self.collapse_btn.clicked.connect(self.collapse_all)
        filters.addWidget(self.collapse_btn)
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("Ghost")
        refresh_btn.clicked.connect(self.refresh)
        filters.addWidget(refresh_btn)
        layout.addLayout(filters)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("Muted")
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setObjectName("TopicTree")
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(390)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(False)
        self.tree.setMouseTracking(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tree.setItemDelegate(TopicTreeDelegate(self.tree))
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)
        self.tree.itemClicked.connect(self._on_item_clicked)
        splitter.addWidget(self.tree)

        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        self.detail.anchorClicked.connect(self.on_anchor)
        self.detail.setPlaceholderText("选择左侧条目，查看简介、别名与原文出处。")
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self._expanded_groups: set[str] = set()
        self.refresh()

    def _fill_collections(self) -> None:
        current = self.collection_combo.currentData()
        self.collection_combo.blockSignals(True)
        try:
            self.collection_combo.clear()
            self.collection_combo.addItem("全部启用文集", None)
            for row in self.service.collections():
                label = "%s（可读 %s）" % (row["title"], row.get("ready") or 0)
                self.collection_combo.addItem(label, row["id"])
            if current is not None:
                index = self.collection_combo.findData(current)
                if index >= 0:
                    self.collection_combo.setCurrentIndex(index)
            elif not self._initial_collection_selected and self.collection_combo.count() > 1:
                # 首次进入默认选中第一个具体文集，避免列表混在一起。
                self.collection_combo.setCurrentIndex(self._default_collection_index())
                self._initial_collection_selected = True
        finally:
            self.collection_combo.blockSignals(False)

    def _default_collection_index(self) -> int:
        for index in range(self.collection_combo.count()):
            if self.collection_combo.itemData(index) is not None:
                return index
        return 0

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            self._fill_collections()
            collection_id = self.collection_combo.currentData()
            dimension_key = self.kind_combo.currentData()
            store = TopicStore.open(self.service.config.paths.topics_db, readonly=True)
            try:
                stats = store.approved_stats(collection_id)
                rows = store.entities(
                    collection_id=collection_id,
                    kind=None,
                    status="approved",
                    query=self.search_edit.text().strip(),
                    limit=3000,
                )
            finally:
                store.close()
            dimension = DIMENSION_BY_KEY.get(dimension_key) if dimension_key else None
            if dimension:
                kinds = set(dimension["kinds"])
                rows = [row for row in rows if row.get("kind") in kinds]
            by_kind = stats.get("by_kind") or {}
            parts = []
            for item in DIMENSIONS:
                count = sum(int(by_kind.get(kind, 0) or 0) for kind in item["kinds"])
                parts.append("%s %s" % (item["label"], count))
            stats_text = "已收录 %s 条 · %s · 原文出处 %s" % (
                stats.get("entities", 0),
                " / ".join(parts),
                stats.get("mentions", 0),
            )
            if dimension:
                stats_text = "当前维度「%s」%s 条 · %s" % (
                    dimension["label"],
                    len(rows),
                    stats_text,
                )
            self.stats_label.setText(stats_text)
            selected_id = self._selected_id
            self._build_tree(rows, selected_id)
            target_item = None
            if selected_id is not None:
                target_item = self._find_entity_item(int(selected_id))
            if target_item is None:
                target_item = self._first_entity_item()
            if target_item is not None:
                parent = target_item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.setCurrentItem(target_item)
                self.on_selection_changed()
            else:
                self._selected_id = None
                self.detail.setHtml(
                    "<p style='color:#73838A;'>暂无已收录条目。"
                    "请先在维护端「专题库」中审核候选，或用脚本补录概念与剧情。</p>"
                )
        finally:
            self._loading = False

    def _build_tree(self, rows: list[dict], selected_id: int | None) -> None:
        search_active = bool(self.search_edit.text().strip())
        dimension_filter = self.kind_combo.currentData()
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            for dimension in DIMENSIONS:
                if dimension_filter and dimension_filter != dimension["key"]:
                    continue
                dim_rows = [
                    row for row in rows if row.get("kind") in dimension["kinds"]
                ]
                if not dim_rows:
                    continue
                top = QTreeWidgetItem([dimension["label"]])
                top.setData(0, ROLE_NODE, "dimension")
                top.setData(0, ROLE_KEY, "dim:%s" % dimension["key"])
                top.setData(0, ROLE_DEPTH, 0)
                top.setData(0, TOPIC_ROLE_TITLE, dimension["label"])
                top.setData(0, ROLE_SUBTITLE, dimension["hint"])
                top.setData(0, ROLE_COUNT, len(dim_rows))
                top.setData(0, ROLE_COLOR, dimension["color"])
                top.setData(0, ROLE_HAS_CHILDREN, True)
                top.setData(0, ROLE_EXPANDED, True)
                top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.tree.addTopLevelItem(top)

                used_groups: set[str] = set()
                for group_key, label in GROUPS.get(dimension["key"], ()):
                    group_rows = [
                        row
                        for row in dim_rows
                        if (row.get("group_key") or "") == group_key
                    ]
                    if not group_rows:
                        continue
                    used_groups.add(group_key)
                    self._add_group(
                        top,
                        dimension,
                        group_key,
                        label,
                        group_rows,
                        search_active,
                        bool(dimension_filter),
                    )
                rest = [
                    row
                    for row in dim_rows
                    if (row.get("group_key") or "") not in used_groups
                ]
                if rest:
                    self._add_group(
                        top,
                        dimension,
                        "other",
                        "其他",
                        rest,
                        search_active,
                        bool(dimension_filter),
                    )
                top.setExpanded(True)
            if self.tree.topLevelItemCount() == 0:
                empty = QTreeWidgetItem(["暂无已收录的专题条目"])
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.tree.addTopLevelItem(empty)
        finally:
            self.tree.blockSignals(False)
        self._apply_expanded_roles()

    def _add_group(
        self,
        parent: QTreeWidgetItem,
        dimension: dict,
        group_key: str,
        label: str,
        rows: list[dict],
        search_active: bool,
        filter_active: bool,
    ) -> None:
        group_item = QTreeWidgetItem([label])
        group_item.setData(0, ROLE_NODE, "group")
        group_item.setData(0, ROLE_KEY, "group:%s:%s" % (dimension["key"], group_key))
        group_item.setData(0, ROLE_DEPTH, 1)
        group_item.setData(0, TOPIC_ROLE_TITLE, label)
        group_item.setData(0, ROLE_COUNT, len(rows))
        group_item.setData(0, ROLE_COLOR, dimension["color"])
        group_item.setData(0, ROLE_HAS_CHILDREN, True)
        expanded = (
            search_active
            or filter_active
            or group_key in self._expanded_groups
            or len(rows) <= 8
        )
        group_item.setData(0, ROLE_EXPANDED, expanded)
        group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        parent.addChild(group_item)
        ordered = sorted(
            rows,
            key=lambda row: (
                0 if int(row.get("sort_order") or 0) > 0 else 1,
                int(row.get("sort_order") or 0),
                row.get("name") or "",
            ),
        )
        for row in ordered:
            group_item.addChild(self._make_entity_item(row))
        group_item.setExpanded(expanded)

    def _make_entity_item(self, row: dict) -> QTreeWidgetItem:
        kind = row.get("kind") or ""
        story_kind = kind in ("arc", "event", "mystery")
        count = int(
            (row.get("tag_count") if story_kind else row.get("article_count"))
            or 0
        )
        suffix = "章" if story_kind else "篇"
        name = row.get("name") or ""
        summary = row.get("summary") or group_label(row.get("group_key") or "", kind)
        item = QTreeWidgetItem([name])
        item.setData(0, ROLE_NODE, "entity")
        item.setData(0, ROLE_ENTITY_ID, int(row["id"]))
        item.setData(0, ROLE_ENTITY_KIND, kind)
        item.setData(0, ROLE_DEPTH, 2)
        item.setData(0, TOPIC_ROLE_TITLE, name)
        item.setData(0, ROLE_SUBTITLE, summary)
        item.setData(0, ROLE_COUNT, count)
        item.setData(0, ROLE_COUNT_SUFFIX, suffix)
        item.setData(0, ROLE_COLOR, kind_color(kind))
        item.setData(0, ROLE_HAS_CHILDREN, False)
        item.setData(0, ROLE_EXPANDED, False)
        item.setToolTip(
            0,
            "%s · %s · %s %s"
            % (
                kind_label(kind),
                group_label(row.get("group_key") or "", kind),
                count,
                suffix,
            ),
        )
        return item

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        item.setData(0, ROLE_EXPANDED, True)
        key = item.data(0, ROLE_KEY)
        if key and str(key).startswith("group:"):
            self._expanded_groups.add(str(key))
        self.tree.viewport().update()

    def _on_item_collapsed(self, item: QTreeWidgetItem) -> None:
        item.setData(0, ROLE_EXPANDED, False)
        key = item.data(0, ROLE_KEY)
        if key and str(key).startswith("group:"):
            self._expanded_groups.discard(str(key))
        self.tree.viewport().update()

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        node = item.data(0, ROLE_NODE)
        if node in ("dimension", "group"):
            item.setExpanded(not item.isExpanded())

    def expand_all(self) -> None:
        self.tree.expandAll()
        self._expanded_groups = {
            str(item.data(0, ROLE_KEY))
            for item in self._iter_items()
            if str(item.data(0, ROLE_NODE) or "") == "group"
            and item.data(0, ROLE_KEY)
        }
        self._apply_expanded_roles()
        self.tree.viewport().update()

    def collapse_all(self) -> None:
        self.tree.collapseAll()
        self._expanded_groups.clear()
        self._apply_expanded_roles()
        self.tree.viewport().update()

    def _iter_items(self):
        stack = [
            self.tree.topLevelItem(index)
            for index in range(self.tree.topLevelItemCount() - 1, -1, -1)
        ]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            yield item
            for index in range(item.childCount() - 1, -1, -1):
                stack.append(item.child(index))

    def _apply_expanded_roles(self) -> None:
        for item in self._iter_items():
            item.setData(0, ROLE_EXPANDED, item.isExpanded())

    def _first_entity_item(self) -> QTreeWidgetItem | None:
        for item in self._iter_items():
            if item.data(0, ROLE_ENTITY_ID) is not None:
                return item
        return None

    def _find_entity_item(self, entity_id: int) -> QTreeWidgetItem | None:
        for item in self._iter_items():
            value = item.data(0, ROLE_ENTITY_ID)
            if value is not None and int(value) == int(entity_id):
                return item
        return None

    def on_selection_changed(self) -> None:
        item = self.tree.currentItem()
        entity_id = item.data(0, ROLE_ENTITY_ID) if item else None
        if entity_id is None:
            self._selected_id = None
            self.detail.clear()
            return
        self.show_detail(int(entity_id))

    def _article_meta(self, article_ids: list[int]) -> dict[int, dict]:
        ids = sorted({int(value) for value in article_ids if value})
        if not ids or self.service.conn is None:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.service.conn.execute(
            "SELECT id, title, title_date, publish_ts FROM articles "
            "WHERE id IN (%s)" % placeholders,
            ids,
        ).fetchall()
        result = {}
        for row in rows:
            title = row["title"] or "（无标题）"
            result[int(row["id"])] = {
                "title": strip_title_date(strip_chapter_prefix(title)) or title,
                "raw_title": title,
                "chapter": chapter_number(title),
                "date": row["title_date"] or "",
            }
        return result

    def show_detail(self, entity_id: int) -> None:
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=True)
        try:
            entity = store.entity(entity_id)
            mentions = store.mentions(entity_id, limit=120)
            tag_articles = store.entity_tag_articles(entity_id)
        finally:
            store.close()
        if not entity or entity.get("status") != "approved":
            self._selected_id = None
            self.detail.clear()
            return
        self._selected_id = entity_id
        article_ids = [m["article_id"] for m in mentions]
        article_ids.extend(row["article_id"] for row in tag_articles)
        article_meta = self._article_meta(article_ids)
        self.detail.setHtml(
            entity_detail_html(
                entity,
                mentions=mentions,
                article_meta=article_meta,
                tag_articles=tag_articles,
                links=True,
            )
        )

    def on_anchor(self, url: QUrl) -> None:
        parts = [value for value in url.path().split("/") if value]
        if not parts:
            return
        if url.scheme() == "daoyuan-topic":
            try:
                entity_id = int(parts[0])
            except ValueError:
                return
            TopicEntityDialog(self.service, entity_id, self).exec()
            return
        if url.scheme() != "daoyuan-article":
            return
        try:
            article_id = int(parts[0])
            segment_id = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return
        self.window_ref.open_article(article_id, segment_id or None)


class PlaceholderPage(QWidget):
    def __init__(self, title: str, message: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        body = QLabel(message)
        body.setObjectName("PageSub")
        body.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addStretch(1)


class SettingsPage(QWidget):
    def __init__(self, window: "ReaderWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(14)
        title = QLabel("设置与关于")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        card = _card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        card_layout.setSpacing(8)
        self.labels = {}
        for key, name in (
            ("data", "数据目录"),
            ("content", "内容库"),
            ("state", "个人状态库"),
            ("config", "配置文件"),
        ):
            label = QLabel("")
            label.setWordWrap(True)
            label.setObjectName("Muted")
            self.labels[key] = label
            card_layout.addWidget(QLabel(name))
            card_layout.addWidget(label)
        layout.addWidget(card)

        buttons = QHBoxLayout()
        open_btn = QPushButton("打开数据目录")
        open_btn.setObjectName("Ghost")
        open_btn.clicked.connect(self.open_data_dir)
        import_btn = QPushButton("导入知识库资料包…")
        import_btn.setObjectName("Ghost")
        import_btn.clicked.connect(self.choose_package)
        check_btn = QPushButton("检查数据库")
        check_btn.setObjectName("Primary")
        check_btn.clicked.connect(self.check)
        buttons.addWidget(open_btn)
        buttons.addWidget(import_btn)
        buttons.addWidget(check_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        import_note = QLabel(
            "资料包为维护端生成的备份目录（含 manifest.json）。导入会替换当前内容库，"
            "个人阅读进度与问答记录默认保留，替换前自动生成安全备份。"
        )
        import_note.setObjectName("Muted")
        import_note.setWordWrap(True)
        layout.addWidget(import_note)

        self.result = QLabel("")
        self.result.setObjectName("Muted")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        cfg = self.service.config
        self.labels["data"].setText(str(cfg.data_dir))
        self.labels["content"].setText(str(cfg.paths.content_db))
        self.labels["state"].setText(str(cfg.paths.state_db))
        self.labels["config"].setText(str(cfg.config_path))

    def open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.service.config.data_dir)))

    def choose_package(self) -> None:
        start = self.service.config.paths.backups
        if not start.exists():
            start = self.service.config.data_dir
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择知识库资料包（包含 manifest.json 的备份目录）",
            str(start),
        )
        if folder:
            self.import_package(folder)

    def import_package(self, folder: str) -> bool:
        """导入维护端备份目录，替换内容库并保留个人状态。"""
        ask_page = self.window_ref.pages.get("ask")
        worker = getattr(ask_page, "worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.warning(
                self, "正在智问", "请等待当前智问结束，再导入知识库资料包。"
            )
            return False
        export_page = self.window_ref.pages.get("export")
        if export_page is not None and hasattr(export_page, "wait_for_export"):
            export_page.wait_for_export(30000)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = inspect_backup(folder)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "无法读取资料包", str(exc))
            return False
        QApplication.restoreOverrideCursor()
        content = info.get("content") or {}
        if not info.get("valid") or not content:
            QMessageBox.warning(
                self,
                "资料包不可用",
                "资料包校验未通过，未修改当前内容库：\n%s"
                % "；".join(info.get("errors") or ["缺少内容库"]),
            )
            return False

        current = "当前内容库将被替换"
        if not self.service.config.paths.content_db.exists():
            current = "当前尚未安装内容库"
        reply = QMessageBox.question(
            self,
            "导入知识库资料包",
            "%s。\n\n资料包：%s\n文章 %s 篇，可读 %s 篇，段落 %s 段\n\n"
            "个人阅读进度、书签和问答记录默认保留；替换前会自动生成安全备份。"
            "请先关闭其他正在阅读的窗口。是否继续？"
            % (
                current,
                info.get("path"),
                content.get("articles"),
                content.get("ready"),
                content.get("segments"),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        lock = ProcessLock()
        if not lock.acquire():
            QMessageBox.warning(
                self,
                "暂时不能导入",
                "维护端采集或数据库恢复正在进行，请结束后再导入。",
            )
            return False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.service.close()
            try:
                result = restore_backup(
                    self.service.config, folder, restore_state=False
                )
            finally:
                # 无论恢复成功与否都重新打开；失败时页面会显示内容库不可用。
                self.service.reopen()
            ask_page = self.window_ref.pages.get("ask")
            if hasattr(ask_page, "new_session"):
                ask_page.new_session()
            self.window_ref.refresh_all()
            self.window_ref.switch("home")
            QMessageBox.information(
                self,
                "导入完成",
                "知识库内容已更新。\n恢复前安全备份：%s"
                % (result.get("safety_backup") or "无（此前没有内容库）"),
            )
            return True
        except Exception as exc:
            QMessageBox.warning(
                self,
                "导入失败",
                "资料包导入失败，原有数据未被主动删除：\n%s" % exc,
            )
            return False
        finally:
            QApplication.restoreOverrideCursor()
            lock.release()

    def check(self) -> None:
        health = self.service.health()
        if not health.get("available"):
            self.result.setText("内容库不可用：%s" % health.get("error"))
            return
        self.result.setText(
            "schema v%s · 完整性 %s · 文集 %s · 文章 %s · 可阅读 %s"
            % (
                health.get("schema_version"),
                health.get("integrity"),
                health.get("collections"),
                health.get("articles"),
                health.get("ready"),
            )
        )


class ReaderWindow(QMainWindow):
    NAV = [
        ("首页", "home"),
        ("文库", "library"),
        ("全文检索", "search"),
        ("专题库", "topics"),
        ("道源智问", "ask"),
        ("导出", "export"),
        ("设置", "settings"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.service = ReaderService(config)
        self.setWindowTitle("%s %s" % (APP_NAME, __version__))
        self.resize(1280, 840)
        self.setMinimumSize(1080, 700)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(228)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        sidebar_layout.addWidget(BrandHeader(APP_NAME, "道源知识库 · 阅读端"))
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = {}
        for text, key in self.NAV:
            btn = QPushButton(text)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self.switch(k))
            sidebar_layout.addWidget(btn)
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
        sidebar_layout.addStretch(1)
        stage = QLabel("版本 %s · %s" % (__version__, __stage__))
        stage.setObjectName("Muted")
        stage.setContentsMargins(18, 0, 18, 16)
        sidebar_layout.addWidget(stage)
        root.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.pages["home"] = HomePage(self)
        self.pages["library"] = LibraryPage(self)
        self.pages["search"] = SearchPage(self)
        self.pages["topics"] = TopicsPage(self)
        self.pages["ask"] = AskPage(self)
        self.pages["export"] = ExportPage(self)
        self.pages["settings"] = SettingsPage(self)
        for _, key in self.NAV:
            self.stack.addWidget(self.pages[key])
        root.addWidget(self.stack, 1)

        self.statusBar().showMessage("就绪")
        self.switch("home")

    def switch(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.nav_buttons[key].setChecked(True)
        if hasattr(page, "refresh"):
            page.refresh()

    def refresh_all(self) -> None:
        for page in self.pages.values():
            if hasattr(page, "refresh"):
                page.refresh()

    def prompt_missing_content(self) -> None:
        """首次启动未找到内容库时，给出导入资料包或打开目录的入口。"""
        if self.service.available:
            return
        box = QMessageBox(self)
        box.setWindowTitle("未找到知识库数据")
        box.setText(
            "当前数据目录中没有可用的知识库内容。\n\n"
            "可以导入维护端生成的资料包（包含 manifest.json 的备份目录），"
            "也可以打开数据目录检查配置。"
        )
        import_btn = box.addButton("导入资料包…", QMessageBox.ButtonRole.ActionRole)
        open_btn = box.addButton("打开数据目录", QMessageBox.ButtonRole.ActionRole)
        box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is import_btn:
            self.pages["settings"].choose_package()
        elif box.clickedButton() is open_btn:
            self.pages["settings"].open_data_dir()

    def open_article(self, article_id: int, segment_id: int | None = None) -> None:
        article = self.service.article(article_id)
        if not article or article.get("status") != "ready":
            self.statusBar().showMessage("该文章尚未采集正文", 4000)
            return
        dialog = ArticleDialog(self.service, article, self, segment_id=segment_id)
        dialog.exec()

    def search_for(self, term: str) -> None:
        page = self.pages.get("search")
        if page is None:
            return
        self.switch("search")
        page.query.setText(term or "")
        if term:
            page.run_search()

    def closeEvent(self, event):  # noqa: N802
        export_page = self.pages.get("export")
        if export_page is not None and hasattr(export_page, "wait_for_export"):
            export_page.wait_for_export()
        ask_page = self.pages.get("ask")
        if ask_page is not None and hasattr(ask_page, "wait_for_worker"):
            ask_page.wait_for_worker()
        self.service.close()
        super().closeEvent(event)


class ArticleDialog(QDialog):
    def __init__(
        self,
        service: ReaderService,
        article: dict,
        parent=None,
        segment_id: int | None = None,
    ):
        super().__init__(parent)
        self.service = service
        self.article: dict = {}
        self.article_id = 0
        self.segments: list[dict] = []
        self.target_segment = segment_id
        self.prefs = self._load_preferences()
        self.rendered = False
        self.setWindowTitle(article.get("title") or "阅读")
        self.resize(1320, 860)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.title_label = QLabel(article.get("title") or "（无标题）")
        self.title_label.setObjectName("PageTitle")
        self.title_label.setWordWrap(True)
        header.addWidget(self.title_label, 1)
        self.prev_btn = QPushButton("上一篇")
        self.prev_btn.setObjectName("Ghost")
        self.prev_btn.clicked.connect(lambda: self.go_neighbor("prev"))
        self.next_btn = QPushButton("下一篇")
        self.next_btn.setObjectName("Ghost")
        self.next_btn.clicked.connect(lambda: self.go_neighbor("next"))
        header.addWidget(self.prev_btn)
        header.addWidget(self.next_btn)
        layout.addLayout(header)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("Muted")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.source_btn = QPushButton("查看原文")
        self.source_btn.setObjectName("Ghost")
        self.source_btn.clicked.connect(self.open_source)
        toolbar.addWidget(self.source_btn)
        self.topic_btn = QPushButton("本章专题")
        self.topic_btn.setObjectName("Ghost")
        self.topic_btn.setCheckable(True)
        self.topic_btn.setChecked(True)
        self.topic_btn.toggled.connect(self.toggle_topic_panel)
        toolbar.addWidget(self.topic_btn)
        settings_btn = QPushButton("阅读设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.clicked.connect(self.open_reading_settings)
        toolbar.addWidget(settings_btn)
        toolbar.addStretch(1)
        smaller = QPushButton("A-")
        smaller.setObjectName("Ghost")
        smaller.clicked.connect(lambda: self.change_font(-1))
        larger = QPushButton("A+")
        larger.setObjectName("Ghost")
        larger.clicked.connect(lambda: self.change_font(1))
        toolbar.addWidget(smaller)
        toolbar.addWidget(larger)
        export_btn = QPushButton("导出本篇")
        export_btn.setObjectName("Ghost")
        export_menu = QMenu(export_btn)
        for label, fmt in EXPORT_FORMATS:
            action = export_menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, f=fmt: self.export_article(f)
            )
        export_btn.setMenu(export_menu)
        toolbar.addWidget(export_btn)
        layout.addLayout(toolbar)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.on_anchor_clicked)
        self.browser.setMaximumWidth(int(self.prefs["width"]))
        self.browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        content_row = QHBoxLayout()
        content_row.setSpacing(14)
        reader_area = QWidget()
        reader_layout = QHBoxLayout(reader_area)
        reader_layout.setContentsMargins(0, 0, 0, 0)
        reader_layout.setSpacing(0)
        reader_layout.addStretch(1)
        reader_layout.addWidget(self.browser, 10)
        reader_layout.addStretch(1)
        content_row.addWidget(reader_area, 1)

        self.topic_panel = QFrame()
        self.topic_panel.setObjectName("TopicPanel")
        self.topic_panel.setMinimumWidth(290)
        self.topic_panel.setMaximumWidth(350)
        panel_layout = QVBoxLayout(self.topic_panel)
        panel_layout.setContentsMargins(14, 14, 14, 12)
        panel_layout.setSpacing(6)
        panel_title = QLabel("本章专题")
        panel_title.setObjectName("TopicPanelTitle")
        panel_layout.addWidget(panel_title)
        self.topic_browser = QTextBrowser()
        self.topic_browser.setObjectName("TopicSidebar")
        self.topic_browser.setOpenLinks(False)
        self.topic_browser.setOpenExternalLinks(False)
        self.topic_browser.anchorClicked.connect(self.on_topic_anchor)
        panel_layout.addWidget(self.topic_browser, 1)
        content_row.addWidget(self.topic_panel)
        layout.addLayout(content_row, 1)
        self.apply_browser_theme()

        footer = QHBoxLayout()
        self.export_status = QLabel("")
        self.export_status.setObjectName("Muted")
        self.export_status.setWordWrap(True)
        footer.addWidget(self.export_status, 1)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("Ghost")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self.progress_timer = QTimer(self)
        self.progress_timer.setSingleShot(True)
        self.progress_timer.setInterval(900)
        self.progress_timer.timeout.connect(self.save_progress)
        self.browser.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self.load_article(
            article,
            segment_id=segment_id,
            restore_progress=segment_id is None,
        )

    def _load_preferences(self) -> dict:
        state = self.service.state
        prefs = dict(READING_DEFAULTS)
        try:
            font = int(
                state.get_setting("reader.font_size")
                or self.service.config.ui.font_size
            )
        except (TypeError, ValueError):
            font = READING_DEFAULTS["font_size"]
        prefs["font_size"] = min(32, max(14, font))
        mode = state.get_setting("reader.mode")
        if mode in ("original", "comfort"):
            prefs["mode"] = mode
        theme = state.get_setting("reader.theme")
        if theme in ("paper", "night"):
            prefs["theme"] = theme
        try:
            line_height = float(
                state.get_setting("reader.line_height")
                or READING_DEFAULTS["line_height"]
            )
        except (TypeError, ValueError):
            line_height = float(READING_DEFAULTS["line_height"])
        prefs["line_height"] = "%.1f" % min(2.4, max(1.3, line_height))
        try:
            width = int(
                state.get_setting("reader.width")
                or self.service.config.ui.page_width
            )
        except (TypeError, ValueError):
            width = READING_DEFAULTS["width"]
        prefs["width"] = min(1100, max(560, width))
        return prefs

    def _save_preferences(self) -> None:
        for key, value in self.prefs.items():
            self.service.state.set_setting("reader.%s" % key, str(value))

    def apply_reader_prefs(self, **changes) -> None:
        updated = False
        for key, value in changes.items():
            if key in self.prefs and self.prefs[key] != value:
                self.prefs[key] = value
                updated = True
        if not updated:
            return
        self._save_preferences()
        self.browser.setMaximumWidth(int(self.prefs["width"]))
        self.apply_browser_theme()
        self._rebuild_html()

    def apply_browser_theme(self) -> None:
        if self.prefs["theme"] == "night":
            self.browser.setStyleSheet(
                "QTextBrowser { background: #20262B; border: 1px solid #39424A; "
                "border-radius: 10px; padding: 24px; color: #D8D2C4; "
                "selection-background-color: #5A4A2E; }"
            )
        else:
            self.browser.setStyleSheet("")

    def _rebuild_html(self) -> None:
        bar = self.browser.verticalScrollBar()
        maximum = bar.maximum()
        ratio = (bar.value() / maximum) if maximum else 0.0
        self.rendered = False
        self.browser.setHtml(self.build_html())
        self.rendered = True
        QTimer.singleShot(0, lambda: bar.setValue(int(ratio * bar.maximum())))

    def open_reading_settings(self) -> None:
        dialog = self.build_reading_settings_dialog()
        dialog.exec()

    def build_reading_settings_dialog(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle("阅读设置")
        dialog.setMinimumWidth(380)
        form = QFormLayout(dialog)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(10)

        mode = QComboBox()
        mode.addItem("原貌阅读（保留重点色）", "original")
        mode.addItem("舒适阅读（统一文字颜色）", "comfort")
        mode.setCurrentIndex(max(0, mode.findData(self.prefs["mode"])))
        theme = QComboBox()
        theme.addItem("纸白", "paper")
        theme.addItem("夜读", "night")
        theme.setCurrentIndex(max(0, theme.findData(self.prefs["theme"])))
        size = QSpinBox()
        size.setRange(14, 32)
        size.setValue(int(self.prefs["font_size"]))
        line = QComboBox()
        for label, value in (
            ("紧凑 1.5", "1.5"),
            ("标准 1.8", "1.8"),
            ("宽松 2.0", "2.0"),
            ("舒展 2.2", "2.2"),
        ):
            line.addItem(label, value)
        line.setCurrentIndex(max(0, line.findData(self.prefs["line_height"])))
        width = QComboBox()
        for label, value in (
            ("适中 720", 720),
            ("标准 820", 820),
            ("宽屏 980", 980),
            ("超宽 1100", 1100),
        ):
            width.addItem(label, value)
        width_index = width.findData(int(self.prefs["width"]))
        width.setCurrentIndex(width_index if width_index >= 0 else 1)

        form.addRow("阅读模式", mode)
        form.addRow("阅读主题", theme)
        form.addRow("正文字号", size)
        form.addRow("行高", line)
        form.addRow("阅读宽度", width)

        note = QLabel("以上设置只改变版式与颜色，不改变原文文字。")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        form.addRow(note)

        mode.currentIndexChanged.connect(
            lambda: self.apply_reader_prefs(mode=mode.currentData())
        )
        theme.currentIndexChanged.connect(
            lambda: self.apply_reader_prefs(theme=theme.currentData())
        )
        size.valueChanged.connect(
            lambda value: self.apply_reader_prefs(font_size=int(value))
        )
        line.currentIndexChanged.connect(
            lambda: self.apply_reader_prefs(line_height=line.currentData())
        )
        width.currentIndexChanged.connect(
            lambda: self.apply_reader_prefs(width=int(width.currentData()))
        )

        buttons = QHBoxLayout()
        reset = QPushButton("恢复默认")
        reset.setObjectName("Ghost")

        def reset_defaults() -> None:
            mode.setCurrentIndex(max(0, mode.findData(READING_DEFAULTS["mode"])))
            theme.setCurrentIndex(max(0, theme.findData(READING_DEFAULTS["theme"])))
            size.setValue(READING_DEFAULTS["font_size"])
            line.setCurrentIndex(
                max(0, line.findData(READING_DEFAULTS["line_height"]))
            )
            width.setCurrentIndex(max(0, width.findData(READING_DEFAULTS["width"])))

        reset.clicked.connect(reset_defaults)
        close = QPushButton("完成")
        close.setObjectName("Primary")
        close.clicked.connect(dialog.accept)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        buttons.addWidget(close)
        form.addRow(buttons)
        return dialog

    def load_article(
        self,
        article: dict,
        segment_id: int | None = None,
        restore_progress: bool = True,
    ) -> None:
        self.progress_timer.stop()
        if self.article_id:
            self.save_progress()
        self.article = article
        self.article_id = int(article["id"])
        self.segments = self.service.segments(self.article_id)
        self.target_segment = segment_id
        title = article.get("title") or "（无标题）"
        self.setWindowTitle(title)
        self.title_label.setText(title)
        meta_parts = [
            article.get("collection_title") or "",
            article.get("page_author") or "",
            article.get("publish_date") or "",
            "来源：微信公众号",
        ]
        self.meta_label.setText(" · ".join([p for p in meta_parts if p]))

        neighbors = self.service.neighbors(self.article_id)
        prev_item = neighbors.get("prev")
        next_item = neighbors.get("next")
        self.prev_btn.setEnabled(prev_item is not None)
        self.prev_btn.setToolTip((prev_item or {}).get("title") or "")
        self.next_btn.setEnabled(next_item is not None)
        self.next_btn.setToolTip((next_item or {}).get("title") or "")
        self.source_btn.setEnabled(bool(article.get("source_url")))
        self.export_status.setText("")

        self.rendered = False
        self.browser.setHtml(self.build_html())
        self.rendered = True
        if segment_id is not None:
            QTimer.singleShot(0, lambda: self.scroll_to_segment(segment_id))
        elif restore_progress:
            progress = self.service.state.get_progress(self.article_id)
            position = int(progress["position"] or 0) if progress else 0
            QTimer.singleShot(
                0, lambda: self.browser.verticalScrollBar().setValue(position)
            )
        else:
            QTimer.singleShot(0, lambda: self.browser.verticalScrollBar().setValue(0))
        self.refresh_topic_panel()

    def toggle_topic_panel(self, visible: bool) -> None:
        self.topic_panel.setVisible(bool(visible))
        if visible:
            self.refresh_topic_panel()

    def refresh_topic_panel(self) -> None:
        if not self.article_id or not hasattr(self, "topic_browser"):
            return
        try:
            store = TopicStore.open(
                self.service.config.paths.topics_db, readonly=True
            )
            try:
                context = store.article_context(self.article_id)
            finally:
                store.close()
        except Exception:
            context = {}
        title = self.article.get("title") or ""
        meta = {
            "chapter": chapter_number(title),
            "title": strip_title_date(strip_chapter_prefix(title)) or title,
        }
        self.topic_browser.setHtml(article_context_html(context, meta))

    def on_topic_anchor(self, url: QUrl) -> None:
        parts = [value for value in url.path().split("/") if value]
        if not parts:
            return
        if url.scheme() == "daoyuan-topic":
            try:
                entity_id = int(parts[0])
            except ValueError:
                return
            TopicEntityDialog(self.service, entity_id, self).exec()
        elif url.scheme() == "daoyuan-article":
            try:
                article_id = int(parts[0])
                segment_id = int(parts[1]) if len(parts) > 1 else 0
            except ValueError:
                return
            article = self.service.article(article_id)
            if article and article.get("status") == "ready":
                self.load_article(
                    article,
                    segment_id=segment_id or None,
                    restore_progress=not segment_id,
                )

    def build_html(self) -> str:
        fragments = []
        comfort = self.prefs["mode"] == "comfort"
        for segment in self.segments:
            anchor = '<a name="seg-%d"></a>' % int(segment["id"])
            fragment = (segment.get("safe_html_fragment") or "").strip()
            if not fragment:
                fragment = "<p>%s</p>" % html.escape(
                    (segment.get("plain_text") or "").strip()
                )
            if comfort:
                fragment = COLOR_STYLE_RE.sub("", fragment)
            if (
                self.target_segment is not None
                and int(segment["id"]) == int(self.target_segment)
            ):
                fragment = '<div class="hit">%s</div>' % fragment
            fragments.append(anchor + fragment)
        body = "".join(fragments)
        if not body:
            body = self.article.get("safe_html") or ""
        if not body:
            paragraphs = (self.article.get("text_content") or "").split("\n")
            body = "".join(
                "<p>%s</p>" % html.escape(p) for p in paragraphs if p.strip()
            )
        night = self.prefs["theme"] == "night"
        text_color = "#D8D2C4" if night else "#283540"
        quote_color = "#A9B3B8" if night else "#5E6B72"
        link_color = "#D8B77C" if night else "#B49763"
        hit_background = "#3A3426" if night else "#F8EFD9"
        return (
            "<html><head><style>"
            "body { font-family: 'Microsoft YaHei', 'SimSun'; color: %(text)s; }"
            "p { margin: 0 0 1.0em 0; }"
            "blockquote { color: %(quote)s; border-left: 3px solid #B49763; "
            "padding-left: 12px; margin-left: 0; }"
            "h1,h2,h3 { color: %(text)s; }"
            "a { color: %(link)s; }"
            ".hit { background-color: %(hit)s; border-left: 3px solid #B49763; "
            "padding: 6px 10px; margin: 0 0 1.0em 0; }"
            "</style></head><body style=\"font-size:%(size)dpx; "
            "line-height:%(line)s;\">%(body)s</body></html>"
            % {
                "text": text_color,
                "quote": quote_color,
                "link": link_color,
                "hit": hit_background,
                "size": int(self.prefs["font_size"]),
                "line": self.prefs["line_height"],
                "body": body,
            }
        )

    def scroll_to_segment(self, segment_id: int) -> None:
        self.browser.scrollToAnchor("seg-%d" % int(segment_id))
        bar = self.browser.verticalScrollBar()
        bar.setValue(max(0, bar.value() - 48))

    def go_neighbor(self, key: str) -> None:
        target = self.service.neighbors(self.article_id).get(key)
        if not target:
            return
        article = self.service.article(int(target["id"]))
        if article and article.get("status") == "ready":
            self.load_article(article, segment_id=None, restore_progress=True)

    def open_source(self) -> None:
        url = (self.article or {}).get("source_url")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def on_anchor_clicked(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def change_font(self, delta: int) -> None:
        size = min(32, max(14, int(self.prefs["font_size"]) + delta))
        self.apply_reader_prefs(font_size=size)

    def on_scroll(self, value) -> None:
        if self.rendered:
            self.progress_timer.start()

    def save_progress(self) -> None:
        if not self.article_id:
            return
        bar = self.browser.verticalScrollBar()
        maximum = bar.maximum()
        percent = (bar.value() / maximum) if maximum else 0.0
        self.service.save_progress(self.article_id, None, bar.value(), percent)

    def export_article(self, fmt: str) -> None:
        if not self.article:
            return
        self.save_progress()
        title = self.article.get("title") or "道源文章"
        exports_dir = self.service.config.paths.exports
        exports_dir.mkdir(parents=True, exist_ok=True)
        default = exports_dir / default_export_name(title, fmt)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出本篇", str(default), EXPORT_FILTERS[fmt]
        )
        if not path:
            return
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        self.export_article_to(path, fmt)

    def export_article_to(self, path: str, fmt: str) -> bool:
        payload = dict(self.article)
        payload["segments"] = self.segments
        try:
            export_articles([payload], path, fmt)
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return False
        self.export_status.setText("已导出：%s" % path)
        self.export_status.setToolTip(path)
        return True

    def closeEvent(self, event):  # noqa: N802
        self.save_progress()
        super().closeEvent(event)


class TopicEntityDialog(QDialog):
    """从阅读页侧栏打开的单条专题详情。"""

    def __init__(self, service: ReaderService, entity_id: int, parent=None):
        super().__init__(parent)
        self.service = service
        self.entity_id = int(entity_id)
        self.setWindowTitle("专题条目")
        self.resize(900, 780)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        kicker = QLabel("专题条目")
        kicker.setObjectName("PageSub")
        header.addWidget(kicker)
        header.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("Ghost")
        close_btn.clicked.connect(self.accept)
        header.addWidget(close_btn)
        layout.addLayout(header)

        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self.on_anchor)
        layout.addWidget(self.browser, 1)
        self.load_entity()

    def _article_meta(self, article_ids: list[int]) -> dict[int, dict]:
        ids = sorted({int(value) for value in article_ids if value})
        if not ids or self.service.conn is None:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.service.conn.execute(
            "SELECT id, title, title_date FROM articles "
            "WHERE id IN (%s)" % placeholders,
            ids,
        ).fetchall()
        result = {}
        for row in rows:
            title = row["title"] or "（无标题）"
            result[int(row["id"])] = {
                "title": strip_title_date(strip_chapter_prefix(title)) or title,
                "raw_title": title,
                "chapter": chapter_number(title),
                "date": row["title_date"] or "",
            }
        return result

    def load_entity(self) -> None:
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=True)
        try:
            entity = store.entity(self.entity_id)
            mentions = store.mentions(self.entity_id, limit=100)
            tag_articles = store.entity_tag_articles(self.entity_id)
        finally:
            store.close()
        if not entity or entity.get("status") != "approved":
            self.browser.setHtml(
                "<p style='color:#73838A;'>这条专题内容尚未收录或已被移除。</p>"
            )
            return
        self.setWindowTitle("%s · 专题" % (entity.get("name") or ""))
        article_ids = [m["article_id"] for m in mentions]
        article_ids.extend(row["article_id"] for row in tag_articles)
        self.browser.setHtml(
            entity_detail_html(
                entity,
                mentions=mentions,
                article_meta=self._article_meta(article_ids),
                tag_articles=tag_articles,
                links=True,
            )
        )

    def on_anchor(self, url: QUrl) -> None:
        if url.scheme() != "daoyuan-article":
            return
        parts = [value for value in url.path().split("/") if value]
        if not parts:
            return
        try:
            article_id = int(parts[0])
            segment_id = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return
        article = self.service.article(article_id)
        if not article or article.get("status") != "ready":
            return
        parent = self.parent()
        if isinstance(parent, ArticleDialog):
            parent.load_article(
                article,
                segment_id=segment_id or None,
                restore_progress=not segment_id,
            )
            self.accept()
        else:
            ArticleDialog(
                self.service,
                article,
                self,
                segment_id=segment_id or None,
            ).exec()


def main() -> int:
    set_windows_app_id("Daoyuan.KnowledgeBase.Reader")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    config = load_config()
    config.paths.ensure()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Daoyuan")
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    apply_theme(app)
    window = ReaderWindow(config)
    window.show()
    QTimer.singleShot(0, window.prompt_missing_content)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
