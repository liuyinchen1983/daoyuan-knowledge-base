# -*- coding: utf-8 -*-
"""道源采集管理（维护端）PySide6 主窗口。"""
from __future__ import annotations

import html
import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTime, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import ADMIN_NAME, __stage__, __version__
from ..collector.known import ACCOUNT_ID, ACCOUNT_NAME, BIZ, KNOWN_COLLECTIONS
from ..collector.service import CollectService
from ..collector.single import (
    CooldownActiveError,
    CollectorBusyError,
    LinkCollectError,
    RiskCooldownError,
    TooSoonError,
)
from ..core.config import Config, load_config
from ..core.lock import ProcessLock
from ..core.timeutil import format_epoch_date
from ..core.schedule import (
    build_task_command,
    compute_schedule_status,
    describe_status,
    display_command,
    effective_schedule,
    install_task,
    query_task,
    remove_task,
    save_schedule,
)
from ..storage.backup import create_backup, inspect_backup, list_backups, restore_backup
from ..storage.db import connect, health_report, initialize_content_db
from ..storage.repositories import CollectionRepository, SourceRepository
from ..storage.state_db import StateStore
from ..topics.catalog import KINDS, KIND_LABELS
from ..topics.store import STATUS_LABELS, TopicStore
from ..ui_common.brand import BrandHeader, app_icon, set_windows_app_id
from ..ui_common.theme import apply_theme


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    return frame


def _format_manual_time(value: str) -> str:
    """把 ISO 导入时间压成 2026-10-30 16:24 用于提示。"""
    text = (value or "").strip().replace("T", " ")
    return text[:16] if len(text) >= 16 else text


def configure_file_logging(config: Config) -> None:
    root = logging.getLogger("daoyuan")
    if any(isinstance(h, logging.FileHandler) for h in root.handlers):
        return
    handler = logging.FileHandler(
        config.paths.logs / "collect.log", encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class AdminService:
    def __init__(self, config: Config):
        self.config = config
        self.conn = None
        self._closed = False
        if config.paths.content_db.exists():
            self.conn = connect(config.paths.content_db)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.conn is not None:
            self.conn.close()

    def initialize(self) -> None:
        if self.conn is not None:
            self.conn.close()
        self.conn = initialize_content_db(self.config.paths.content_db)
        self._closed = False
        store = StateStore(self.config.paths.state_db)
        store.close()

    def health(self) -> dict:
        if self.conn is None:
            return {"available": False}
        report = health_report(self.conn)
        report["available"] = True
        return report

    def collections(self) -> list[dict]:
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT c.id, c.title, c.album_id, c.expected_count, c.sort_mode, c.enabled, "
            "count(ac.article_id) AS total, "
            "sum(CASE WHEN a.status='ready' THEN 1 ELSE 0 END) AS ready, "
            "sum(CASE WHEN a.status='failed' THEN 1 ELSE 0 END) AS failed, "
            "sum(CASE WHEN a.status='pending' THEN 1 ELSE 0 END) AS pending "
            "FROM collections c LEFT JOIN article_collections ac ON ac.collection_id=c.id "
            "LEFT JOIN articles a ON a.id=ac.article_id GROUP BY c.id ORDER BY c.id"
        ).fetchall()
        return [dict(r) for r in rows]

    def register_known(self) -> int:
        self.initialize()
        source_id = SourceRepository(self.conn).upsert(
            BIZ, ACCOUNT_NAME, ACCOUNT_ID, verified_state="verified"
        )
        repos = CollectionRepository(self.conn)
        for known in KNOWN_COLLECTIONS:
            repos.upsert(
                source_id,
                known.album_id,
                known.name,
                sort_mode=known.sort_mode
                if known.reverse_flag is not None
                else None,
                expected_count=known.expected_count,
            )
        return len(KNOWN_COLLECTIONS)

    def add_collection(self, title: str, album_id: str) -> int:
        """手动登记新文集；排序方向由首次采集时探测。"""
        title = (title or "").strip()
        album_id = (album_id or "").strip()
        if not title:
            raise ValueError("文集名称不能为空。")
        if not album_id.isdigit() or not (1 <= len(album_id) <= 32):
            raise ValueError("album_id 应为 1–32 位数字。")
        if self.conn is None:
            self.initialize()
        source_id = SourceRepository(self.conn).upsert(
            BIZ, ACCOUNT_NAME, ACCOUNT_ID, verified_state="verified"
        )
        return CollectionRepository(self.conn).upsert(
            source_id, album_id, title
        )

    def import_manual_article(
        self,
        collection_id: int,
        title: str,
        content: str,
        *,
        author: str = "",
        source_url: str = "",
        publish_date: str = "",
        chapter_no: str = "",
    ) -> dict:
        """把人工粘贴的正文写入内容库，并增量更新专题库。"""
        if self.conn is None:
            self.initialize()
        from ..collector.manual import import_manual_article as _import_manual

        result = _import_manual(
            self.conn,
            int(collection_id),
            title,
            content,
            author=author,
            source_url=source_url,
            publish_date=publish_date,
            chapter_no=chapter_no,
        )
        try:
            from ..topics.scanner import scan_after_collection

            topic_results = scan_after_collection(
                self.config,
                [int(collection_id)],
                logger=logging.getLogger("daoyuan.topics"),
            )
            if topic_results:
                result["topics"] = topic_results[0]
        except Exception as exc:
            logging.getLogger("daoyuan.topics").exception("手动导入后专题库更新失败")
            result["topics_error"] = str(exc)
        logging.getLogger("daoyuan.manual").info(
            "手动导入：文章=%s，状态=%s，文集=%s，段落=%s，字符=%s",
            result.get("article_id"),
            result.get("status"),
            collection_id,
            result.get("segments"),
            result.get("chars"),
        )
        return result

    def collect_link(self, url: str, collection_id: int | None = None) -> dict:
        """抓取并入库一条单篇微信文章链接。"""
        if self.conn is None:
            self.initialize()
        from ..collector.single import collect_article_by_url

        result = collect_article_by_url(
            self.conn,
            self.config,
            url,
            collection_id=collection_id,
        )
        try:
            from ..topics.scanner import scan_after_collection

            topic_results = scan_after_collection(
                self.config,
                [int(result["collection_id"])],
                logger=logging.getLogger("daoyuan.topics"),
            )
            if topic_results:
                result["topics"] = topic_results[0]
        except Exception as exc:
            logging.getLogger("daoyuan.topics").exception("链接采集后专题库更新失败")
            result["topics_error"] = str(exc)
        return result

    def recent_manual_imports(self, limit: int = 12) -> list[dict]:
        """最近手动导入记录（含补全自动采集清单里的待采文章）。"""
        if self.conn is None:
            self.initialize()
        from ..collector.manual import recent_manual_imports as _recent

        return _recent(self.conn, limit)

    def recent_manual_articles(self, limit: int = 12) -> list[dict]:
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT a.id, a.title, a.publish_ts, a.created_at, a.source_url, "
            "c.title AS collection_title FROM articles a "
            "LEFT JOIN (SELECT article_id, MIN(collection_id) AS collection_id "
            "FROM article_collections GROUP BY article_id) ac ON ac.article_id=a.id "
            "LEFT JOIN collections c ON c.id=ac.collection_id "
            "WHERE a.msgid LIKE 'manual:%' "
            "ORDER BY a.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["publish_date"] = format_epoch_date(item.get("publish_ts"))
            result.append(item)
        return result

    def recent_runs(self, limit: int = 8) -> list[dict]:
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT r.id, r.mode, r.status, r.started_at, r.finished_at, "
            "r.list_found, r.pending_count, r.success_count, r.failed_count, "
            "r.restricted_count, r.message, c.title AS collection_title "
            "FROM collect_runs r LEFT JOIN collections c ON c.id=r.collection_id "
            "ORDER BY r.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def log_files(self) -> list[Path]:
        root = self.config.paths.logs
        if not root.exists():
            return []
        return sorted(
            root.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:50]


class _SignalHandler(logging.Handler):
    def __init__(self, signal):
        super().__init__()
        self.signal = signal

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.signal.emit(self.format(record))
        except Exception:  # 日志不得影响采集
            pass


class CollectWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        config: Config,
        collection_ids: list[int] | None,
        mode: str,
        limit: int,
        max_fetch: int,
    ):
        super().__init__()
        self.config = config
        self.collection_ids = collection_ids
        self.mode = mode
        self.limit = limit
        self.max_fetch = max_fetch
        self.stop_event = threading.Event()

    @Slot()
    def run(self) -> None:
        conn = None
        lock = ProcessLock()
        logger = logging.getLogger("daoyuan.collector")
        handler = _SignalHandler(self.progress)
        handler.setFormatter(logging.Formatter("%(message)s"))
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            if not lock.acquire():
                self.failed.emit(
                    "另一个采集任务正在运行（可能是计划任务或另一个维护端窗口），"
                    "本次不重复执行。"
                )
                return
            conn = initialize_content_db(self.config.paths.content_db)

            def on_progress(kind: str, payload) -> None:
                if kind == "list_page":
                    self.progress.emit(
                        "清单第 %s 页：累计 %s 篇（继续翻页=%s）"
                        % (
                            payload.get("page"),
                            payload.get("items"),
                            payload.get("continue"),
                        )
                    )
                elif kind == "list_done":
                    self.progress.emit("清单读取完成：%s 篇" % payload.get("items"))
                elif kind == "article":
                    self.progress.emit(
                        "抓取 %s/%s：%s"
                        % (
                            payload.get("done"),
                            payload.get("total"),
                            payload.get("title"),
                        )
                    )

            service = CollectService(
                self.config,
                conn,
                logger=logger,
                stop_event=self.stop_event,
            )
            results = service.run(
                mode=self.mode,
                collection_ids=self.collection_ids,
                max_fetch=self.max_fetch,
                limit=self.limit,
                progress_cb=on_progress,
            )
            self.finished.emit(results)
        except Exception as exc:
            logger.exception("采集任务异常终止")
            self.failed.emit(str(exc))
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
            if conn is not None:
                conn.close()
            lock.release()


class OverviewPage(QWidget):
    def __init__(self, window: "AdminWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(14)

        title = QLabel("运行总览")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        subtitle = QLabel("环境、数据库与采集状态。采集入口在「采集任务」。")
        subtitle.setObjectName("PageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        card = _card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        card_layout.setSpacing(8)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setObjectName("Muted")
        card_layout.addWidget(self.info)
        layout.addWidget(card)

        buttons = QHBoxLayout()
        init_btn = QPushButton("初始化 / 检查数据库结构")
        init_btn.setObjectName("Primary")
        init_btn.clicked.connect(self.initialize)
        check_btn = QPushButton("检查数据库")
        check_btn.setObjectName("Ghost")
        check_btn.clicked.connect(self.refresh)
        open_btn = QPushButton("打开数据目录")
        open_btn.setObjectName("Ghost")
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.service.config.data_dir))
            )
        )
        buttons.addWidget(init_btn)
        buttons.addWidget(check_btn)
        buttons.addWidget(open_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.result = QLabel("")
        self.result.setObjectName("Muted")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch(1)
        self.refresh()

    def initialize(self) -> None:
        if self.window_ref.is_collecting():
            QMessageBox.information(self, "采集进行中", "请先等待当前采集结束或停止任务。")
            return
        try:
            self.service.initialize()
            self.result.setText("数据库结构已初始化/校验完成。")
        except Exception as exc:
            self.result.setText("初始化失败：%s" % exc)
        self.refresh()

    def refresh(self) -> None:
        cfg = self.service.config
        lines = [
            "程序版本：%s（%s）" % (__version__, __stage__),
            "Python：%s" % sys.version.split()[0],
            "配置文件：%s" % cfg.config_path,
            "数据目录：%s" % cfg.data_dir,
            "内容库：%s" % cfg.paths.content_db,
            "个人状态库：%s" % cfg.paths.state_db,
        ]
        report = self.service.health()
        if report.get("available"):
            lines.append(
                "数据库：schema v%s · 完整性 %s · 文集 %s · 文章 %s · 可阅读 %s · 待采 %s · 失败 %s"
                % (
                    report.get("schema_version"),
                    report.get("integrity"),
                    report.get("collections"),
                    report.get("articles"),
                    report.get("ready"),
                    report.get("pending"),
                    report.get("failed"),
                )
            )
        else:
            lines.append("数据库：尚未初始化（请点击下方“初始化 / 检查数据库结构”）")
        if self.service.log_files():
            lines.append(
                "日志：%s（共 %d 个文件）"
                % (cfg.paths.logs, len(self.service.log_files()))
            )
        self.info.setText("\n".join(lines))


class CollectionDialog(QDialog):
    """手动登记新文集；不访问网络。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动登记文集")
        self.setModal(True)
        self.resize(540, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        title = QLabel("手动登记新文集")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel(
            "从文集网页链接的 album_id= 参数中复制编号。"
            "登记后首次采集会探测排序方向；只登记，不会自动开始采集。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        form.setSpacing(10)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：新文集名称")
        self.album_edit = QLineEdit()
        self.album_edit.setPlaceholderText("例如：1000000000000000001")
        form.addRow("文集名称", self.title_edit)
        form.addRow("album_id", self.album_edit)
        layout.addLayout(form)
        self.error = QLabel("")
        self.error.setObjectName("Muted")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("登记")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def values(self) -> tuple[str, str]:
        return self.title_edit.text().strip(), self.album_edit.text().strip()

    def accept(self) -> None:  # noqa: D401
        title, album_id = self.values()
        if not title:
            self.error.setText("请填写文集名称。")
            return
        if not album_id.isdigit() or not (1 <= len(album_id) <= 32):
            self.error.setText("album_id 应为 1–32 位数字。")
            return
        super().accept()


class SourcesPage(QWidget):
    def __init__(self, window: "AdminWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(10)
        title = QLabel("来源与文集")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "登记公众号与已知合集，也可手动补充新文集；后续采集任务从这里读取。"
            "程序不会声称能自动发现 100% 的新文集。"
        )
        subtitle.setObjectName("PageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        buttons = QHBoxLayout()
        register_btn = QPushButton("登记 / 更新已知合集")
        register_btn.setObjectName("Primary")
        register_btn.clicked.connect(self.register_known)
        manual_btn = QPushButton("手动登记新文集…")
        manual_btn.setObjectName("Ghost")
        manual_btn.clicked.connect(self.add_manual)
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("Ghost")
        refresh_btn.clicked.connect(self.refresh)
        buttons.addWidget(register_btn)
        buttons.addWidget(manual_btn)
        buttons.addWidget(refresh_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.result = QLabel("")
        self.result.setObjectName("Muted")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.refresh()

    def register_known(self) -> None:
        if self.window_ref.is_collecting():
            QMessageBox.information(self, "采集进行中", "请先等待当前采集结束或停止任务。")
            return
        try:
            count = self.service.register_known()
            if count:
                self.result.setText("已登记/更新 %d 个已知合集。" % count)
            else:
                self.result.setText(
                    "未配置预置合集。请编辑 known.py，或使用「手动登记新文集」。"
                )
            self.refresh()
            self.window_ref.refresh_pages()
        except Exception as exc:
            self.result.setText("登记失败：%s" % exc)

    def add_manual(self) -> None:
        if self.window_ref.is_collecting():
            QMessageBox.information(self, "采集进行中", "请先等待当前采集结束或停止任务。")
            return
        dialog = CollectionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title, album_id = dialog.values()
        try:
            collection_id = self.service.add_collection(title, album_id)
        except Exception as exc:
            QMessageBox.warning(self, "登记失败", str(exc))
            return
        logging.getLogger("daoyuan.admin").info(
            "手动登记文集：id=%s，album_id=%s，title=%s",
            collection_id,
            album_id,
            title,
        )
        self.result.setText(
            "已登记文集「%s」（内部 ID %s）。首次采集时会探测排序方向，"
            "不会自动开始抓取。" % (title, collection_id)
        )
        self.refresh()
        self.window_ref.refresh_pages()

    def refresh(self) -> None:
        self.list.clear()
        rows = self.service.collections()
        if not rows:
            self.list.addItem("尚未登记文集。点击上方“登记 / 更新已知合集”。")
            return
        for row in rows:
            text = (
                "%s\nalbum_id=%s · 发现 %s / 应采 %s · 可读 %s · 待采 %s · 失败 %s"
                % (
                    row["title"] or "（无标题）",
                    row["album_id"],
                    row["total"],
                    row["expected_count"]
                    if row["expected_count"] is not None
                    else "?",
                    row["ready"] or 0,
                    row["pending"] or 0,
                    row["failed"] or 0,
                )
            )
            self.list.addItem(text)


class TasksPage(QWidget):
    STATUS_TEXT = {
        "completed": "完成",
        "stopped": "已停止",
        "restricted": "触发微信限制，已停止",
        "failed": "失败",
        "running": "运行中",
    }

    def __init__(self, window: "AdminWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        self._thread: QThread | None = None
        self._worker: CollectWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(10)
        title = QLabel("采集任务")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "保守限速：每篇 8–15 秒，每 20 篇暂停 3–5 分钟，单次默认最多 20 篇；"
            "遇到微信验证页立即停止。"
        )
        subtitle.setObjectName("PageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QHBoxLayout()
        form.setSpacing(10)
        form.addWidget(QLabel("文集"))
        self.collection_combo = QComboBox()
        self.collection_combo.setMinimumWidth(200)
        self.collection_combo.setMaximumWidth(280)
        form.addWidget(self.collection_combo)
        form.addWidget(QLabel("方式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("增量更新（只抓新文章）", "update")
        self.mode_combo.addItem("全量复核（含已读）", "full")
        self.mode_combo.setMinimumWidth(170)
        self.mode_combo.setMaximumWidth(210)
        form.addWidget(self.mode_combo)
        form.addWidget(QLabel("本次上限"))
        self.budget_spin = QSpinBox()
        self.budget_spin.setRange(0, 500)
        self.budget_spin.setValue(
            min(20, max(1, self.service.config.collector.max_fetch_per_run))
        )
        self.budget_spin.setSuffix(" 篇")
        self.budget_spin.setSpecialValueText("不限")
        self.budget_spin.setFixedWidth(110)
        self.budget_spin.setToolTip("0 表示不限；不建议一次抓取过多")
        form.addWidget(self.budget_spin)
        form.addWidget(QLabel("每文集上限"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 500)
        self.limit_spin.setValue(0)
        self.limit_spin.setSpecialValueText("不限")
        self.limit_spin.setFixedWidth(110)
        form.addWidget(self.limit_spin)
        form.addStretch(1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.start_btn = QPushButton("开始采集")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("Ghost")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.request_stop)
        actions.addWidget(self.start_btn)
        actions.addWidget(self.stop_btn)
        manual_btn = QPushButton("手动/链接导入")
        manual_btn.setObjectName("Ghost")
        manual_btn.clicked.connect(lambda: self.window_ref.switch("manual"))
        actions.addWidget(manual_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.summary = QLabel("请选择文集后开始。首次建议只抓 1–2 篇核对正文。")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("采集进度与错误会显示在这里。")
        layout.addWidget(self.log, 2)

        runs_title = QLabel("最近任务")
        runs_title.setObjectName("Gold")
        layout.addWidget(runs_title)
        self.runs = QListWidget()
        self.runs.setWordWrap(True)
        self.runs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.runs, 1)
        self.refresh()

    # --- state ---
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def refresh(self) -> None:
        if not self.is_running():
            current = self.collection_combo.currentData()
            self.collection_combo.clear()
            self.collection_combo.addItem("全部启用文集", None)
            for row in self.service.collections():
                label = "%s（可读 %s / 发现 %s）" % (
                    row["title"],
                    row["ready"] or 0,
                    row["total"],
                )
                self.collection_combo.addItem(label, row["id"])
            if current is not None:
                index = self.collection_combo.findData(current)
                if index >= 0:
                    self.collection_combo.setCurrentIndex(index)
            self.start_btn.setEnabled(self.collection_combo.count() > 1)
        self.refresh_runs()

    def refresh_runs(self) -> None:
        self.runs.clear()
        rows = self.service.recent_runs()
        if not rows:
            self.runs.addItem("暂无采集记录。")
            return
        for row in rows:
            message = row["message"] or ""
            text = (
                "#%s %s · %s\n清单 %s · 成功 %s · 失败 %s · 限制 %s · %s%s"
                % (
                    row["id"],
                    row["collection_title"] or "（已删除文集）",
                    self.STATUS_TEXT.get(row["status"], row["status"]),
                    row["list_found"] or 0,
                    row["success_count"] or 0,
                    row["failed_count"] or 0,
                    row["restricted_count"] or 0,
                    row["started_at"] or "",
                    (" · " + message) if message else "",
                )
            )
            self.runs.addItem(text)

    # --- run ---
    def start(self) -> None:
        if self.is_running():
            return
        collection_id = self.collection_combo.currentData()
        collection_ids = None if collection_id is None else [int(collection_id)]
        mode = self.mode_combo.currentData()
        budget = self.budget_spin.value()
        limit = self.limit_spin.value()
        goal = (
            "全部启用文集"
            if collection_ids is None
            else self.collection_combo.currentText()
        )
        if budget == 0 or budget > 5:
            estimate = (
                "不限篇数（不建议）"
                if budget == 0
                else "最多 %d 篇，约 %.0f–%.0f 分钟"
                % (
                    budget,
                    budget * 8 / 60,
                    budget * 15 / 60,
                )
            )
            reply = QMessageBox.question(
                self,
                "确认采集",
                "目标：%s\n方式：%s\n本次正文：%s\n\n"
                "采集期间请勿重复点击；遇到微信验证会立即停止。是否开始？"
                % (goal, self.mode_combo.currentText(), estimate),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.log.clear()
        self.append_log(
            "开始采集：%s · %s · 本篇上限 %s · 单次上限 %s"
            % (
                goal,
                self.mode_combo.currentText(),
                "不限" if limit == 0 else limit,
                "不限" if budget == 0 else budget,
            )
        )
        self.summary.setText("正在采集……离开本页不会中断任务。")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self._thread = QThread(self)
        self._worker = CollectWorker(
            self.service.config,
            collection_ids,
            mode,
            None if limit == 0 else limit,
            budget,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.append_log)
        self._worker.finished.connect(self.on_finished)
        self._worker.failed.connect(self.on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self.on_thread_finished)
        self._thread.start()

    def append_log(self, message: str) -> None:
        self.log.appendPlainText(message)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum()
        )

    @Slot(object)
    def on_finished(self, results) -> None:
        self.stop_btn.setEnabled(False)
        if not results:
            self.summary.setText("本次没有可执行的文集。")
            self.append_log("本次没有可执行的文集。")
        else:
            total_success = sum(r.success for r in results)
            total_failed = sum(r.failed for r in results)
            total_skipped = sum(r.skipped for r in results)
            restricted = any(r.status == "restricted" for r in results)
            self.summary.setText(
                "结束：成功 %d 篇，未变 %d 篇，失败/不可用 %d 篇%s。"
                % (
                    total_success,
                    total_skipped,
                    total_failed,
                    "；已触发微信限制，请至少冷却 2–4 小时" if restricted else "",
                )
            )
            for result in results:
                self.append_log(
                    "【%s】%s：清单 %d，待采 %d，抓取 %d，成功 %d，未变 %d，"
                    "失败 %d，不可用 %d，限制 %d%s"
                    % (
                        self.STATUS_TEXT.get(result.status, result.status),
                        result.title,
                        result.list_found,
                        result.pending,
                        result.fetched,
                        result.success,
                        result.skipped,
                        result.failed,
                        result.unavailable,
                        result.restricted,
                        ("（%s）" % result.message) if result.message else "",
                    )
                )
            # 采集结束后在本地做专题库增量更新；只处理新文章/正文变化。
            try:
                from ..topics.scanner import scan_after_collection

                topic_results = scan_after_collection(
                    self.service.config,
                    [result.collection_id for result in results],
                )
                for item in topic_results:
                    if item.get("error"):
                        self.append_log(
                            "专题库更新失败（文集 %s）：%s"
                            % (item.get("collection_id"), item.get("error"))
                        )
                    else:
                        self.append_log(
                            "专题库：文集 %s，扫描 %s 篇，跳过 %s 篇，"
                            "新增实体 %s，新增出处 %s"
                            % (
                                item.get("collection_id"),
                                item.get("scanned"),
                                item.get("skipped"),
                                item.get("entities_created"),
                                item.get("mentions_added"),
                            )
                        )
            except Exception as exc:
                self.append_log("专题库更新失败：%s" % exc)
            if restricted:
                QMessageBox.warning(
                    self,
                    "已触发微信访问限制",
                    "采集已立即停止。请至少冷却 2–4 小时，期间不要重复采集。",
                )
        self.window_ref.refresh_pages()

    @Slot(str)
    def on_failed(self, message: str) -> None:
        self.stop_btn.setEnabled(False)
        self.summary.setText("任务异常：%s" % message)
        self.append_log("任务异常：%s" % message)

    @Slot()
    def on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.refresh()

    def request_stop(self) -> None:
        if self._worker is not None:
            self._worker.stop_event.set()
            self.stop_btn.setEnabled(False)
            self.summary.setText("已请求停止：当前请求结束后停止。")
            self.append_log("已请求停止：当前请求结束后停止，不再发起新请求。")

    def shutdown(self, timeout_ms: int = 15000) -> bool:
        if not self.is_running():
            return True
        self.request_stop()
        if self._thread is not None:
            return self._thread.wait(timeout_ms)
        return True


class TopicManualDialog(QDialog):
    """手动新增专题实体。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动新增专题实体")
        self.setModal(True)
        self.resize(520, 280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        title = QLabel("手动新增专题实体")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel(
            "手动新增会直接进入「已收录」，不需要再审核。"
            "名称和备注只保存在专题库，不会修改文章正文。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.kind_combo = QComboBox()
        for kind in KINDS:
            self.kind_combo.addItem(KIND_LABELS.get(kind, kind), kind)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：人物姓名或地点名称")
        self.summary_edit = QLineEdit()
        self.summary_edit.setPlaceholderText("可选：人物身份、地点说明、物品用途")
        form.addRow("类型", self.kind_combo)
        form.addRow("名称", self.name_edit)
        form.addRow("备注", self.summary_edit)
        layout.addLayout(form)

        self.error = QLabel("")
        self.error.setObjectName("Muted")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("新增")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def values(self) -> tuple[str, str, str]:
        return (
            self.kind_combo.currentData(),
            self.name_edit.text().strip(),
            self.summary_edit.text().strip(),
        )

    def accept(self) -> None:  # noqa: D401
        _, name, _ = self.values()
        if len(name) < 2:
            self.error.setText("名称至少 2 个字。")
            return
        super().accept()


class TopicReclassifyDialog(QDialog):
    """调整专题实体的类型。"""

    def __init__(self, parent=None, name: str = "", current_kind: str = "person"):
        super().__init__(parent)
        self.setWindowTitle("调整专题类型")
        self.setModal(True)
        self.resize(520, 250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        title = QLabel("调整专题类型")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel(
            "把「%s」从%s移到新的分类。别名、备注和原文出处都会保留，"
            "不修改文章正文；如果新分类下已有同名实体，会自动合并。"
            % (name, KIND_LABELS.get(current_kind, current_kind))
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.kind_combo = QComboBox()
        for kind in KINDS:
            if kind != current_kind:
                self.kind_combo.addItem(KIND_LABELS.get(kind, kind), kind)
        form.addRow("新类型", self.kind_combo)
        layout.addLayout(form)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("调整")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def values(self) -> str:
        return self.kind_combo.currentData()


class TopicPage(QWidget):
    """专题库审核与增量更新。"""

    def __init__(self, window: "AdminWindow"):
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
            "从已采集正文中零 Token 抽取人物、地点、物品候选；"
            "组织与势力、概念与设定、剧情阶段、关键事件和伏笔谜题由人工补录或脚本整理。"
            "所有条目必须收录后才会进入阅读端。更新中的文集会在每次采集后增量更新。"
        )
        subtitle.setObjectName("PageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        filters.addWidget(QLabel("文集"))
        self.collection_combo = QComboBox()
        self.collection_combo.setMinimumWidth(170)
        self.collection_combo.setMaximumWidth(260)
        self.collection_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.collection_combo)
        filters.addWidget(QLabel("类型"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("全部类型", None)
        for kind in KINDS:
            self.kind_combo.addItem(KIND_LABELS.get(kind, kind), kind)
        self.kind_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.kind_combo)
        filters.addWidget(QLabel("状态"))
        self.status_combo = QComboBox()
        self.status_combo.addItem("待审核", "pending")
        self.status_combo.addItem("已收录", "approved")
        self.status_combo.addItem("已忽略", "rejected")
        self.status_combo.addItem("全部状态", None)
        self.status_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status_combo)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索名称或别名")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self.refresh)
        filters.addWidget(self.search_edit, 1)
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("Ghost")
        refresh_btn.clicked.connect(self.refresh)
        filters.addWidget(refresh_btn)
        layout.addLayout(filters)

        actions = QHBoxLayout()
        self.scan_btn = QPushButton("增量更新专题库")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.clicked.connect(lambda: self.scan(full=False))
        self.full_btn = QPushButton("全量重扫")
        self.full_btn.setObjectName("Ghost")
        self.full_btn.clicked.connect(lambda: self.scan(full=True))
        self.manual_btn = QPushButton("手动新增…")
        self.manual_btn.setObjectName("Ghost")
        self.manual_btn.clicked.connect(self.manual_add)
        actions.addWidget(self.scan_btn)
        actions.addWidget(self.full_btn)
        actions.addWidget(self.manual_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("Muted")
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)
        self.scan_result = QLabel("")
        self.scan_result.setObjectName("Muted")
        self.scan_result.setWordWrap(True)
        layout.addWidget(self.scan_result)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(320)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(8)
        self.detail = QTextBrowser()
        self.detail.setPlaceholderText("选择左侧分类下的实体，查看别名、统计和原文证据。")
        right_layout.addWidget(self.detail, 1)
        buttons = QHBoxLayout()
        self.approve_btn = QPushButton("收录")
        self.approve_btn.setObjectName("Primary")
        self.approve_btn.setEnabled(False)
        self.approve_btn.clicked.connect(lambda: self.review("approved"))
        self.reject_btn = QPushButton("忽略")
        self.reject_btn.setObjectName("Ghost")
        self.reject_btn.setEnabled(False)
        self.reject_btn.clicked.connect(lambda: self.review("rejected"))
        self.reclassify_btn = QPushButton("改类型…")
        self.reclassify_btn.setObjectName("Ghost")
        self.reclassify_btn.setEnabled(False)
        self.reclassify_btn.clicked.connect(self.reclassify)
        self.merge_btn = QPushButton("合并到…")
        self.merge_btn.setObjectName("Ghost")
        self.merge_btn.setEnabled(False)
        self.merge_btn.clicked.connect(self.merge)
        buttons.addWidget(self.approve_btn)
        buttons.addWidget(self.reject_btn)
        buttons.addWidget(self.reclassify_btn)
        buttons.addWidget(self.merge_btn)
        buttons.addStretch(1)
        right_layout.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self.refresh()

    def _fill_collections(self) -> None:
        current = self.collection_combo.currentData()
        self.collection_combo.blockSignals(True)
        try:
            self.collection_combo.clear()
            self.collection_combo.addItem("全部启用文集", None)
            for row in self.service.collections():
                if row.get("enabled", 1):
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
            store = TopicStore.open(self.service.config.paths.topics_db, readonly=True)
            try:
                stats = store.stats(collection_id)
                rows = store.entities(
                    collection_id=collection_id,
                    kind=self.kind_combo.currentData(),
                    status=self.status_combo.currentData(),
                    query=self.search_edit.text().strip(),
                    limit=800,
                )
            finally:
                store.close()
            by_kind = stats.get("by_kind") or {}
            last = stats.get("last_scan") or {}
            kind_text = " / ".join(
                "%s %s" % (KIND_LABELS.get(kind, kind), by_kind.get(kind, 0))
                for kind in KINDS
            )
            self.stats_label.setText(
                "实体 %s（%s）· 待审 %s · 已收录 %s · "
                "已忽略 %s · 原文出处 %s · 已扫描文章 %s%s"
                % (
                    stats.get("entities", 0),
                    kind_text,
                    stats.get("pending", 0),
                    stats.get("approved", 0),
                    stats.get("rejected", 0),
                    stats.get("mentions", 0),
                    stats.get("scanned_articles", 0),
                    (
                        " · 上次扫描：新增/变更 %s 篇，跳过 %s 篇"
                        % (last.get("scanned"), last.get("skipped"))
                        if last
                        else ""
                    ),
                )
            )
            selected_id = self._selected_id
            self.tree.blockSignals(True)
            self.tree.clear()
            groups: dict[str, list] = {kind: [] for kind in KINDS}
            for row in rows:
                groups.setdefault(row.get("kind"), []).append(row)
            target_item = None
            for kind in KINDS:
                group_rows = groups.get(kind) or []
                if not group_rows:
                    continue
                top = QTreeWidgetItem(
                    ["%s (%d)" % (KIND_LABELS.get(kind, kind), len(group_rows))]
                )
                top.setData(0, Qt.ItemDataRole.UserRole, None)
                top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                top.setExpanded(True)
                self.tree.addTopLevelItem(top)
                for row in group_rows:
                    status = row.get("status") or ""
                    label = row.get("name") or ""
                    if self.status_combo.currentData() is None and status != "approved":
                        label += " · %s" % STATUS_LABELS.get(status, status)
                    child = QTreeWidgetItem([label])
                    child.setData(0, Qt.ItemDataRole.UserRole, int(row["id"]))
                    child.setToolTip(
                        0,
                        "%s · %s · 出处 %s 段 / %s 篇"
                        % (
                            KIND_LABELS.get(row.get("kind"), row.get("kind")),
                            STATUS_LABELS.get(status, status),
                            row.get("mention_count") or 0,
                            row.get("article_count") or 0,
                        ),
                    )
                    top.addChild(child)
                    if selected_id is not None and int(row["id"]) == int(selected_id):
                        target_item = child
            if not rows:
                empty = QTreeWidgetItem(["暂无符合条件的实体"])
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.tree.addTopLevelItem(empty)
            self.tree.blockSignals(False)
            if target_item is None:
                target_item = self._first_entity_item()
            if target_item is not None:
                self.tree.setCurrentItem(target_item)
                self.on_selection_changed()
            else:
                self._selected_id = None
                self._set_detail_actions(False)
                self.detail.setHtml(
                    "<p style='color:#73838A;'>暂无实体。请先采集文章，再点击"
                    "「增量更新专题库」。</p>"
                )
        finally:
            self._loading = False

    def _first_entity_item(self) -> QTreeWidgetItem | None:
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            if top is not None and top.childCount():
                return top.child(0)
        return None

    def on_selection_changed(self) -> None:
        item = self.tree.currentItem()
        entity_id = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if entity_id is None:
            self._selected_id = None
            self._set_detail_actions(False)
            self.detail.clear()
            return
        self.show_detail(int(entity_id))

    def _article_titles(self, article_ids: list[int]) -> dict[int, str]:
        ids = sorted({int(value) for value in article_ids if value})
        if not ids or self.service.conn is None:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.service.conn.execute(
            "SELECT id, title FROM articles WHERE id IN (%s)" % placeholders,
            ids,
        ).fetchall()
        return {int(row["id"]): row["title"] or "（无标题）" for row in rows}

    def show_detail(self, entity_id: int) -> None:
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=True)
        try:
            entity = store.entity(entity_id)
            mentions = store.mentions(entity_id, limit=60)
        finally:
            store.close()
        if not entity:
            self._selected_id = None
            self.detail.clear()
            self._set_detail_actions(False)
            return
        self._selected_id = entity_id
        self._set_detail_actions(True)
        titles = self._article_titles([m["article_id"] for m in mentions])
        parts = [
            "<h2 style='margin-bottom:2px;'>%s</h2>" % html.escape(entity["name"]),
            "<p style='color:#73838A;'>%s · %s · 来源 %s · 出处 %s 段 / %s 篇</p>"
            % (
                html.escape(entity.get("kind_label") or ""),
                html.escape(entity.get("status_label") or ""),
                html.escape(entity.get("source") or ""),
                entity.get("mention_count") or 0,
                entity.get("article_count") or 0,
            ),
        ]
        aliases = entity.get("aliases") or []
        if aliases:
            parts.append(
                "<p><b>别名：</b>%s</p>"
                % html.escape("、".join(aliases))
            )
        if entity.get("summary"):
            parts.append("<p><b>备注：</b>%s</p>" % html.escape(entity["summary"]))
        parts.append("<hr><h3>原文证据</h3>")
        if not mentions:
            parts.append("<p style='color:#73838A;'>暂无原文出处。</p>")
        for mention in mentions:
            title = titles.get(int(mention["article_id"]), "文章 #%s" % mention["article_id"])
            parts.append(
                "<p style='margin-bottom:2px;'><b>%s</b>"
                "<span style='color:#73838A;'> · %s</span></p>"
                % (html.escape(title), html.escape(mention["surface"]))
            )
            parts.append("<blockquote>%s</blockquote>" % html.escape(mention["quote"]))
        self.detail.setHtml("".join(parts))

    def _set_detail_actions(self, enabled: bool) -> None:
        self.approve_btn.setEnabled(enabled)
        self.reject_btn.setEnabled(enabled)
        self.reclassify_btn.setEnabled(enabled)
        self.merge_btn.setEnabled(enabled)

    def review(self, status: str) -> None:
        if self._selected_id is None:
            return
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=False)
        try:
            store.review(self._selected_id, status)
        finally:
            store.close()
        self.refresh()

    def reclassify(self) -> None:
        if self._selected_id is None:
            return
        old_id = int(self._selected_id)
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=True)
        try:
            current = store.entity(old_id)
        finally:
            store.close()
        if not current:
            return
        dialog = TopicReclassifyDialog(
            self, current.get("name") or "", current.get("kind") or "person"
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind = dialog.values()
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=False)
        try:
            new_id = store.reclassify(old_id, kind)
        except Exception as exc:
            QMessageBox.warning(self, "调整失败", str(exc))
            return
        finally:
            store.close()
        name = current.get("name") or ""
        if int(new_id) != old_id:
            self.scan_result.setText(
                "已并入%s「%s」。" % (KIND_LABELS.get(kind, kind), name)
            )
        else:
            self.scan_result.setText(
                "已将「%s」改为%s。" % (name, KIND_LABELS.get(kind, kind))
            )
        self._selected_id = int(new_id)
        self.refresh()

    def merge(self) -> None:
        if self._selected_id is None:
            return
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=True)
        try:
            current = store.entity(self._selected_id)
            if not current:
                return
            others = store.entities(
                collection_id=current["collection_id"],
                kind=current["kind"],
                limit=800,
            )
        finally:
            store.close()
        options = []
        mapping: dict[str, int] = {}
        for row in others:
            if int(row["id"]) == int(self._selected_id):
                continue
            label = "%s（出处 %s）" % (row["name"], row.get("mention_count") or 0)
            options.append(label)
            mapping[label] = int(row["id"])
        if not options:
            QMessageBox.information(self, "无法合并", "同一文集、同一类型下没有其他实体。")
            return
        label, ok = QInputDialog.getItem(
            self,
            "合并实体",
            "将「%s」并入：" % current["name"],
            options,
            0,
            False,
        )
        if not ok or label not in mapping:
            return
        target_id = mapping[label]
        reply = QMessageBox.question(
            self,
            "确认合并",
            "合并后，「%s」的别名和原文出处会并入目标实体，来源实体会被删除。\n"
            "此操作不涉及文章正文。是否继续？" % current["name"],
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=False)
        try:
            store.merge(self._selected_id, target_id)
        finally:
            store.close()
        self.refresh()

    def manual_add(self) -> None:
        collection_id = self.collection_combo.currentData()
        if collection_id is None:
            QMessageBox.information(self, "请选择文集", "手动新增前，请先在左上方选择一个具体文集。")
            return
        dialog = TopicManualDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind, name, summary = dialog.values()
        store = TopicStore.open(self.service.config.paths.topics_db, readonly=False)
        try:
            store.manual_add(int(collection_id), kind, name, summary)
        except Exception as exc:
            QMessageBox.warning(self, "新增失败", str(exc))
            return
        finally:
            store.close()
        self.scan_result.setText("已手动新增：%s（%s）" % (name, KIND_LABELS.get(kind, kind)))
        self.refresh()

    def scan(self, full: bool = False) -> None:
        if self.window_ref.is_collecting():
            QMessageBox.information(self, "采集进行中", "请先等待当前采集结束或停止任务。")
            return
        collection_id = self.collection_combo.currentData()
        if collection_id is None:
            ids = [
                int(row["id"])
                for row in self.service.collections()
                if row.get("enabled", 1)
            ]
        else:
            ids = [int(collection_id)]
        if not ids:
            QMessageBox.information(self, "没有文集", "尚未登记任何可用文集。")
            return
        if full:
            reply = QMessageBox.question(
                self,
                "全量重扫专题库",
                "将重新抽取所选文集的全部可读文章。已收录/已忽略的人工审核状态会保留，"
                "但会重建原文出处。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from ..topics.scanner import scan_after_collection

            results = scan_after_collection(
                self.service.config, ids, full=full, logger=logging.getLogger("daoyuan.topics")
            )
        finally:
            QApplication.restoreOverrideCursor()
        lines = []
        for item in results:
            if item.get("error"):
                lines.append(
                    "文集 %s：失败（%s）" % (item.get("collection_id"), item.get("error"))
                )
            else:
                lines.append(
                    "文集 %s：扫描 %s 篇，跳过 %s 篇，新增实体 %s，新增出处 %s"
                    % (
                        item.get("collection_id"),
                        item.get("scanned"),
                        item.get("skipped"),
                        item.get("entities_created"),
                        item.get("mentions_added"),
                    )
                )
        self.scan_result.setText("专题库更新完成。" + "；".join(lines))
        self.refresh()


class ManualImportPage(QWidget):
    """人工复制粘贴文章，不发起任何网络请求。"""

    STATUS_TEXT = {
        "created": "新建并入库",
        "updated": "已更新原文章",
        "unchanged": "内容未变化，已更新元数据",
    }

    def __init__(self, window: "AdminWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(10)
        title = QLabel("手动与链接导入")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "粘贴一条公众号文章链接可直接采集单篇；也可以复制正文粘贴导入。"
            "链接采集只请求这一个页面，遇到验证页立即停止；手动粘贴始终不联网。"
            "入库后与自动采集的文章一样可阅读、检索、导出和智问。"
        )
        subtitle.setObjectName("PageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.last_import_label = QLabel("")
        self.last_import_label.setObjectName("Gold")
        self.last_import_label.setWordWrap(True)
        self.last_import_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.last_import_label)

        link_card = _card()
        link_layout = QVBoxLayout(link_card)
        link_layout.setContentsMargins(18, 14, 18, 14)
        link_layout.setSpacing(8)
        link_heading = QLabel("按链接采集单篇")
        link_heading.setObjectName("Gold")
        link_layout.addWidget(link_heading)
        link_hint = QLabel(
            "只接受本公众号的单篇文章链接，自动识别文集、章节号和发布日期。"
            "冷却期间或自动采集正在运行时不会发请求。"
        )
        link_hint.setObjectName("Muted")
        link_hint.setWordWrap(True)
        link_layout.addWidget(link_hint)
        link_row = QHBoxLayout()
        link_row.setSpacing(10)
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText(
            "粘贴文章链接，例如 https://mp.weixin.qq.com/s?__biz=...&mid=...&idx=..."
        )
        self.link_edit.returnPressed.connect(self.collect_link)
        link_row.addWidget(self.link_edit, 1)
        self.link_btn = QPushButton("采集链接")
        self.link_btn.setObjectName("Primary")
        self.link_btn.clicked.connect(self.collect_link)
        link_row.addWidget(self.link_btn)
        link_layout.addLayout(link_row)
        self.link_status = QLabel("")
        self.link_status.setObjectName("Muted")
        self.link_status.setWordWrap(True)
        link_layout.addWidget(self.link_status)
        layout.addWidget(link_card)

        card = _card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        card_layout.setSpacing(8)
        form = QFormLayout()
        form.setSpacing(8)
        self.collection_combo = QComboBox()
        self.collection_combo.setMinimumWidth(300)
        form.addRow("文集", self.collection_combo)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("必填，建议保留文章原题和日期")
        form.addRow("标题", self.title_edit)
        self.chapter_edit = QLineEdit()
        self.chapter_edit.setPlaceholderText("可选，例如 87 或 第87章；留空自动从标题识别")
        form.addRow("章节号", self.chapter_edit)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("可选，例如 https://mp.weixin.qq.com/s/...")
        form.addRow("原文链接", self.url_edit)
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText(
            "可选，2026.10.30 / 2026年10月30日 / 2026-10-30 17:30 均可；"
            "留空时优先用标题末尾日期，否则用当前时间"
        )
        form.addRow("发布日期", self.date_edit)
        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText("可选")
        form.addRow("作者", self.author_edit)
        card_layout.addLayout(form)
        layout.addWidget(card)

        content_header = QHBoxLayout()
        content_label = QLabel("正文")
        content_label.setObjectName("Gold")
        content_header.addWidget(content_label)
        self.count_label = QLabel("0 字符")
        self.count_label.setObjectName("Muted")
        content_header.addStretch(1)
        content_header.addWidget(self.count_label)
        layout.addLayout(content_header)
        self.content_edit = QPlainTextEdit()
        self.content_edit.setPlaceholderText(
            "把微信文章正文粘贴到这里。有空白行时按空白行分段；没有空白行时每个非空行作为一段。"
        )
        self.content_edit.textChanged.connect(self._update_count)
        layout.addWidget(self.content_edit, 2)

        hint = QLabel(
            "只保存文字，不保存图片。相同原文链接会更新原文章；没有链接时，"
            "同一文集内标题和正文都相同会视为同一篇。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        actions = QHBoxLayout()
        self.save_btn = QPushButton("保存并入库")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self.save)
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("Ghost")
        clear_btn.clicked.connect(self.clear_form)
        actions.addWidget(self.save_btn)
        actions.addWidget(clear_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        recent_title = QLabel("最近导入")
        recent_title.setObjectName("Gold")
        layout.addWidget(recent_title)
        self.recent = QListWidget()
        self.recent.setMaximumHeight(150)
        layout.addWidget(self.recent)
        self.refresh()

    def _update_count(self) -> None:
        self.count_label.setText("%d 字符" % len(self.content_edit.toPlainText()))

    def refresh(self) -> None:
        current = self.collection_combo.currentData()
        self.collection_combo.blockSignals(True)
        try:
            self.collection_combo.clear()
            rows = self.service.collections()
            for row in rows:
                label = "%s（可读 %s / 发现 %s）" % (
                    row["title"],
                    row.get("ready") or 0,
                    row.get("total") or 0,
                )
                self.collection_combo.addItem(label, row["id"])
            if current is not None:
                index = self.collection_combo.findData(current)
                if index >= 0:
                    self.collection_combo.setCurrentIndex(index)
            if not rows:
                self.collection_combo.addItem("尚未登记文集", None)
        finally:
            self.collection_combo.blockSignals(False)

        self._refresh_last_import()
        self.recent.clear()
        history = self.service.recent_manual_imports(12)
        if history:
            for item in history:
                chapter = item.get("chapter") or ""
                chapter_text = "第 %s 章" % chapter if chapter else "未标章节"
                imported_at = _format_manual_time(item.get("imported_at"))
                publish_date = item.get("publish_date") or "未填日期"
                origin_text = "链接" if item.get("origin") == "link" else "粘贴"
                parts = [
                    imported_at or "导入时间未知",
                    origin_text,
                    chapter_text,
                    item.get("title") or "（无标题）",
                    item.get("collection_title") or "",
                    "发布 %s" % publish_date,
                ]
                self.recent.addItem(" · ".join(part for part in parts if part))
            return
        recent_rows = self.service.recent_manual_articles(12)
        if not recent_rows:
            self.recent.addItem("暂无手动导入记录。")
            return
        for row in recent_rows:
            date_text = row.get("publish_date") or "未填日期"
            self.recent.addItem(
                "%s · %s · %s"
                % (date_text, row.get("title") or "（无标题）", row.get("collection_title") or "")
            )

    def _refresh_last_import(self) -> None:
        history = self.service.recent_manual_imports(1)
        if not history:
            self.last_import_label.setText(
                "上次导入：暂无记录。导入成功后，这里会显示标题和章节号。"
            )
            return
        item = history[0]
        chapter = item.get("chapter") or ""
        chapter_text = "第 %s 章" % chapter if chapter else "未标章节"
        imported_at = _format_manual_time(item.get("imported_at"))
        origin_text = "链接采集" if item.get("origin") == "link" else "手动导入"
        pieces = [
            html.escape(origin_text),
            html.escape(chapter_text),
            "<b>%s</b>" % html.escape(item.get("title") or "（无标题）"),
            html.escape(item.get("collection_title") or ""),
            html.escape(imported_at or ""),
        ]
        self.last_import_label.setText(
            "上次导入：%s" % " · ".join(piece for piece in pieces if piece)
        )

    def clear_form(self) -> None:
        if self.content_edit.toPlainText().strip():
            reply = QMessageBox.question(
                self,
                "清空粘贴内容",
                "当前正文还没有入库，确定清空？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.title_edit.clear()
        self.chapter_edit.clear()
        self.url_edit.clear()
        self.date_edit.clear()
        self.author_edit.clear()
        self.content_edit.clear()
        self.status_label.clear()

    def _show_link_error(self, error: Exception) -> None:
        if isinstance(error, CooldownActiveError):
            QMessageBox.information(self, "采集冷却中", str(error))
        elif isinstance(error, TooSoonError):
            QMessageBox.information(self, "请稍等", str(error))
        elif isinstance(error, CollectorBusyError):
            QMessageBox.information(self, "自动采集正在运行", str(error))
        elif isinstance(error, RiskCooldownError):
            QMessageBox.warning(self, "触发微信验证", str(error))
        elif isinstance(error, LinkCollectError):
            QMessageBox.warning(self, "无法采集链接", str(error))
        elif isinstance(error, ValueError):
            QMessageBox.warning(self, "无法入库", str(error))
        else:
            logging.getLogger("daoyuan.link").error(
                "链接采集失败：%s",
                error,
                exc_info=(type(error), error, error.__traceback__),
            )
            QMessageBox.critical(self, "链接采集失败", str(error))

    def collect_link(self) -> None:
        raw = self.link_edit.text().strip()
        if not raw:
            QMessageBox.information(
                self,
                "请粘贴链接",
                "请先粘贴一条本公众号的单篇文章链接。",
            )
            self.link_edit.setFocus()
            return
        collection_id = self.collection_combo.currentData()
        self.link_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        result = None
        error = None
        try:
            result = self.service.collect_link(
                raw,
                collection_id=collection_id,
            )
        except Exception as exc:  # 分类后统一提示，避免弹窗期间还显示等待光标
            error = exc
        finally:
            QApplication.restoreOverrideCursor()
            self.link_btn.setEnabled(True)

        if error is not None:
            self._show_link_error(error)
            return
        if not result:
            QMessageBox.warning(
                self,
                "无法采集链接",
                "程序没有返回采集结果，请稍后重试；如果反复出现，请查看日志。",
            )
            return

        status = self.STATUS_TEXT.get(
            result.get("status"), result.get("status") or "已入库"
        )
        chapter = result.get("chapter") or ""
        if result.get("previous_status") and result["previous_status"] != "ready":
            status = "已补全原待采文章" + ("（第 %s 章）" % chapter if chapter else "")
        elif result.get("matched_by") == "chapter" and chapter:
            status = "已按第 %s 章更新原文章" % chapter
        topic_note = ""
        topics = result.get("topics") or {}
        if topics:
            topic_note = "专题库：新增实体 %s，新增出处 %s。" % (
                topics.get("entities_created", 0),
                topics.get("mentions_added", 0),
            )
        elif result.get("topics_error"):
            topic_note = "专题库更新失败：%s" % result["topics_error"]
        album_note = ""
        if not result.get("album_matched"):
            album_note = "\n提示：链接没有可识别的合辑，已存入当前选择的文集。"
        message = (
            "标题：%s\n文集：%s\n状态：%s\n章节：%s\n发布日期：%s\n段落：%s\n字符：%s\n%s%s"
            % (
                result.get("title") or "（无标题）",
                result.get("collection_title") or "",
                status,
                chapter or "未识别",
                result.get("publish_date") or "未知",
                result.get("segments"),
                result.get("chars"),
                topic_note,
                album_note,
            )
        )
        self.link_status.setText("已采集：%s" % message.replace("\n", " · "))
        QMessageBox.information(self, "链接采集完成", message)
        self.link_edit.clear()
        self.refresh()
        self.window_ref.refresh_pages()

    def save(self) -> None:
        collection_id = self.collection_combo.currentData()
        if collection_id is None:
            QMessageBox.information(self, "请选择文集", "请先选择文章所属的文集。")
            return
        title = self.title_edit.text().strip()
        content = self.content_edit.toPlainText()
        if not title:
            QMessageBox.warning(self, "缺少标题", "请填写文章标题。")
            self.title_edit.setFocus()
            return
        if not content.strip():
            QMessageBox.warning(self, "缺少正文", "请粘贴文章正文。")
            self.content_edit.setFocus()
            return

        self.save_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = self.service.import_manual_article(
                int(collection_id),
                title,
                content,
                author=self.author_edit.text(),
                source_url=self.url_edit.text(),
                publish_date=self.date_edit.text(),
                chapter_no=self.chapter_edit.text(),
            )
        except ValueError as exc:
            QApplication.restoreOverrideCursor()
            self.save_btn.setEnabled(True)
            QMessageBox.warning(self, "无法入库", str(exc))
            return
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self.save_btn.setEnabled(True)
            logging.getLogger("daoyuan.manual").exception("手动导入失败")
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.save_btn.setEnabled(True)

        status = self.STATUS_TEXT.get(result.get("status"), result.get("status") or "已入库")
        chapter = result.get("chapter") or ""
        if result.get("previous_status") and result["previous_status"] != "ready":
            status = "已补全原待采文章" + ("（第 %s 章）" % chapter if chapter else "")
        elif result.get("matched_by") == "chapter" and chapter:
            status = "已按第 %s 章更新原文章" % chapter
        topic_note = ""
        topics = result.get("topics") or {}
        if topics:
            topic_note = "专题库：新增实体 %s，新增出处 %s。" % (
                topics.get("entities_created", 0),
                topics.get("mentions_added", 0),
            )
        elif result.get("topics_error"):
            topic_note = "专题库更新失败：%s" % result["topics_error"]
        message = (
            "文章 ID：%s\n状态：%s\n章节：%s\n发布日期：%s\n段落：%s\n字符：%s\n%s"
            % (
                result.get("article_id"),
                status,
                chapter or "未识别",
                result.get("publish_date") or "未知",
                result.get("segments"),
                result.get("chars"),
                topic_note,
            )
        )
        self.status_label.setText("已保存：%s" % message.replace("\n", " · "))
        QMessageBox.information(self, "导入完成", message)
        self.title_edit.clear()
        self.chapter_edit.clear()
        self.url_edit.clear()
        self.date_edit.clear()
        self.author_edit.clear()
        self.content_edit.clear()
        self.refresh()
        self.window_ref.refresh_pages()


class SchedulePage(QWidget):
    def __init__(self, window: "AdminWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        self._dirty = False
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(12)

        title = QLabel("自动采集")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "控制每日检查的周期，并可查看或管理 Windows 计划任务。"
            "程序不会在后台自动注册任务，注册与删除都需要你在这里明确确认。"
        )
        subtitle.setObjectName("PageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        settings = _card()
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(22, 18, 22, 18)
        settings_layout.setSpacing(10)
        heading = QLabel("采集周期")
        heading.setObjectName("Gold")
        settings_layout.addWidget(heading)
        self.enabled_box = QCheckBox("启用自动检查（到点只检查是否有新文章，不抓旧文）")
        self.enabled_box.toggled.connect(self._mark_dirty)
        settings_layout.addWidget(self.enabled_box)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("检查周期"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 365)
        self.interval_spin.setSuffix(" 天")
        self.interval_spin.setFixedWidth(110)
        self.interval_spin.valueChanged.connect(self._mark_dirty)
        row.addWidget(self.interval_spin)
        row.addWidget(QLabel("每天检查时刻"))
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(QTime(9, 0))
        self.time_edit.timeChanged.connect(self._mark_dirty)
        row.addWidget(self.time_edit)
        save_btn = QPushButton("保存周期设置")
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(self.save_settings)
        row.addWidget(save_btn)
        row.addStretch(1)
        settings_layout.addLayout(row)

        scope_row = QHBoxLayout()
        scope_row.setSpacing(10)
        scope_row.addWidget(QLabel("自动采集范围"))
        self.scope_combo = QComboBox()
        self.scope_combo.setMinimumWidth(240)
        self.scope_combo.setMaximumWidth(360)
        self.scope_combo.currentIndexChanged.connect(self._mark_dirty)
        scope_row.addWidget(self.scope_combo)
        scope_row.addStretch(1)
        settings_layout.addLayout(scope_row)
        self.scope_hint = QLabel(
            "只影响计划任务的自动检查；手动采集、链接采集和导入不受限制。"
            "可以只自动检查更新中的文集，其他文集改为手动导入。"
        )
        self.scope_hint.setObjectName("Muted")
        self.scope_hint.setWordWrap(True)
        settings_layout.addWidget(self.scope_hint)
        self.settings_hint = QLabel(
            "修改周期不会自动改动已注册的计划任务；注册/更新任务时会使用这里的最新设置。"
        )
        self.settings_hint.setObjectName("Muted")
        self.settings_hint.setWordWrap(True)
        settings_layout.addWidget(self.settings_hint)
        layout.addWidget(settings)

        status_card = _card()
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(22, 18, 22, 18)
        status_layout.setSpacing(8)
        status_heading = QLabel("运行状态")
        status_heading.setObjectName("Gold")
        status_layout.addWidget(status_heading)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        layout.addWidget(status_card)

        task_card = _card()
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(22, 18, 22, 18)
        task_layout.setSpacing(8)
        task_heading = QLabel("Windows 计划任务")
        task_heading.setObjectName("Gold")
        task_layout.addWidget(task_heading)
        self.task_label = QLabel(
            "任务名称：道源知识库-自动采集\n"
            "状态：尚未查询。点击“查询任务”读取本机计划任务；程序启动时不会自动查询或注册。"
        )
        self.task_label.setObjectName("Muted")
        self.task_label.setWordWrap(True)
        task_layout.addWidget(self.task_label)
        buttons = QHBoxLayout()
        query_btn = QPushButton("查询任务")
        query_btn.setObjectName("Ghost")
        query_btn.clicked.connect(self.query_task_status)
        preview_btn = QPushButton("复制安装命令")
        preview_btn.setObjectName("Ghost")
        preview_btn.clicked.connect(self.copy_command)
        install_btn = QPushButton("注册 / 更新计划任务…")
        install_btn.setObjectName("Primary")
        install_btn.clicked.connect(self.install_task)
        remove_btn = QPushButton("删除计划任务…")
        remove_btn.setObjectName("Ghost")
        remove_btn.clicked.connect(self.remove_task)
        buttons.addWidget(query_btn)
        buttons.addWidget(preview_btn)
        buttons.addWidget(install_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        task_layout.addLayout(buttons)
        note = QLabel(
            "计划任务以当前 Windows 用户身份运行，只做增量检查（--update --if-due，"
            "单次最多 20 篇，范围按上方“自动采集范围”）；未到期立即退出。"
            "删除任务不会删除任何文章或备份。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        task_layout.addWidget(note)
        layout.addWidget(task_card)
        layout.addStretch(1)
        self.refresh()

    def _mark_dirty(self) -> None:
        if not self._loading:
            self._dirty = True

    def _selected_scope(self) -> list[int]:
        value = self.scope_combo.currentData()
        return [int(value)] if value is not None else []

    def _scope_label(self, ids) -> str:
        ids = list(ids or [])
        if not ids:
            return "全部启用文集"
        names = [
            str(row["title"])
            for row in self.service.collections()
            if int(row["id"]) in ids
        ]
        if names:
            return "、".join(names)
        return "已失效的文集 ID %s" % "、".join(str(i) for i in ids)

    def _load_scope(self, selected_ids: list[int] | None) -> None:
        """填充文集下拉框；selected_ids 为 None 时保留当前选择。"""
        current = self.scope_combo.currentData()
        target = selected_ids[0] if selected_ids else current
        self.scope_combo.blockSignals(True)
        try:
            self.scope_combo.clear()
            self.scope_combo.addItem("全部启用文集", None)
            for row in self.service.collections():
                if not row.get("enabled"):
                    continue
                self.scope_combo.addItem(
                    "%s（%d 篇）" % (row["title"], int(row.get("total") or 0)),
                    int(row["id"]),
                )
            index = self.scope_combo.findData(target)
            self.scope_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.scope_combo.blockSignals(False)

    def refresh(self) -> None:
        schedule = effective_schedule(self.service.config)
        if self._dirty:
            self._load_scope(None)
        else:
            self._load_scope(schedule["collection_ids"])
        if not self._dirty:
            self._loading = True
            try:
                self.enabled_box.setChecked(schedule["enabled"])
                self.interval_spin.setValue(int(schedule["interval_days"]))
                self.time_edit.setTime(
                    QTime(
                        int(schedule["daily_time"].split(":")[0]),
                        int(schedule["daily_time"].split(":")[1]),
                    )
                )
            finally:
                self._loading = False
                self._dirty = False
        status = compute_schedule_status(
            self.service.config, conn=self.service.conn, schedule=schedule
        )
        lines = [
            "自动采集：%s" % ("启用" if status["enabled"] else "关闭"),
            "周期：每 %d 天，检查时刻 %s"
            % (status["interval_days"], status["daily_time"]),
            "采集范围：%s" % self._scope_label(status["collection_ids"]),
            "上次完成：%s" % (status["last_run_at"] or "无记录"),
            "下次到期：%s" % (status["next_due_at"] or "—"),
            describe_status(status),
        ]
        if status["due"] and status["enabled"]:
            lines.append("当前已到检查时间，计划任务下次运行时会执行补采。")
        if self._dirty:
            lines.append("上方有尚未保存的周期修改，状态仍按已保存设置计算。")
        self.status_label.setText("\n".join(lines))

    def save_settings(self) -> None:
        time_value = self.time_edit.time().toString("HH:mm")
        scope = self._selected_scope()
        try:
            save_schedule(
                self.service.config,
                self.enabled_box.isChecked(),
                self.interval_spin.value(),
                time_value,
                scope,
            )
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._dirty = False
        self.refresh()
        logging.getLogger("daoyuan.schedule").info(
            "保存采集计划：启用=%s，周期=%s天，时刻=%s，范围=%s",
            self.enabled_box.isChecked(),
            self.interval_spin.value(),
            time_value,
            scope or "全部",
        )
        self.window_ref.statusBar().showMessage("采集周期已保存。", 5000)
        QMessageBox.information(
            self,
            "已保存",
            "周期设置已保存。\n\n"
            "如果已经注册计划任务，请点击“注册 / 更新计划任务…”让触发时间同步更新。",
        )

    def _action_available(self, action: str) -> bool:
        if self.window_ref.is_collecting():
            QMessageBox.information(self, "采集进行中", "请先等待当前采集结束或停止任务。")
            return False
        return True

    def query_task_status(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = query_task()
        finally:
            QApplication.restoreOverrideCursor()
        if result["ok"]:
            self.task_label.setText(
                "任务名称：道源知识库-自动采集\n查询结果：已注册\n\n%s"
                % (result["output"] or "（无输出）")
            )
        else:
            self.task_label.setText(
                "任务名称：道源知识库-自动采集\n"
                "查询结果：未注册或无法读取。\n\n%s"
                % (result["output"] or "schtasks 未返回信息")
            )

    def copy_command(self) -> None:
        command = build_task_command(
            self.service.config,
            {
                "enabled": self.enabled_box.isChecked(),
                "interval_days": self.interval_spin.value(),
                "daily_time": self.time_edit.time().toString("HH:mm"),
                "collection_ids": self._selected_scope(),
            },
        )
        QGuiApplication.clipboard().setText(display_command(command))
        self.window_ref.statusBar().showMessage("安装命令已复制；粘贴到 PowerShell 才会真正注册。", 6000)

    def install_task(self) -> None:
        if not self._action_available("install"):
            return
        if self._dirty:
            try:
                save_schedule(
                    self.service.config,
                    self.enabled_box.isChecked(),
                    self.interval_spin.value(),
                    self.time_edit.time().toString("HH:mm"),
                    self._selected_scope(),
                )
            except Exception as exc:
                QMessageBox.warning(self, "保存周期失败", str(exc))
                return
            self._dirty = False
            self.refresh()
        schedule = effective_schedule(self.service.config)
        command = display_command(build_task_command(self.service.config, schedule))
        if not schedule["enabled"]:
            QMessageBox.warning(
                self,
                "自动采集已关闭",
                "当前周期设置为“关闭”。注册任务后它仍会每天启动，但会立即退出，"
                "直到你重新启用自动采集。",
            )
        reply = QMessageBox.question(
            self,
            "确认注册计划任务",
            "将在当前 Windows 用户下创建/覆盖计划任务：\n\n"
            "任务名：道源知识库-自动采集\n"
            "周期：每 %d 天，%s\n"
            "采集范围：%s\n\n"
            "将执行以下命令：\n%s\n\n"
            "这会在系统中创建计划任务，但不会立即采集。是否继续？"
            % (
                schedule["interval_days"],
                schedule["daily_time"],
                self._scope_label(schedule["collection_ids"]),
                command,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = install_task(self.service.config, schedule)
        finally:
            QApplication.restoreOverrideCursor()
        logging.getLogger("daoyuan.schedule").info(
            "注册计划任务：ok=%s，周期=%s天，时刻=%s，输出=%s",
            result["ok"],
            schedule["interval_days"],
            schedule["daily_time"],
            result["output"],
        )
        if result["ok"]:
            self.task_label.setText(
                "任务名称：道源知识库-自动采集\n"
                "状态：已注册/更新\n\n%s"
                % (result["output"] or "命令执行成功")
            )
            QMessageBox.information(self, "计划任务已注册", result["output"] or "命令执行成功。")
        else:
            self.task_label.setText(
                "任务名称：道源知识库-自动采集\n"
                "状态：注册失败\n\n%s"
                % (result["output"] or "命令执行失败")
            )
            QMessageBox.warning(
                self,
                "注册失败",
                "%s\n\n可复制安装命令，在管理员 PowerShell 中手动执行。"
                % (result["output"] or "schtasks 执行失败"),
            )

    def remove_task(self) -> None:
        if not self._action_available("remove"):
            return
        reply = QMessageBox.question(
            self,
            "确认删除计划任务",
            "将删除 Windows 计划任务“道源知识库-自动采集”。\n\n"
            "只删除任务，不会删除文章、数据库、备份或日志。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = remove_task()
        finally:
            QApplication.restoreOverrideCursor()
        logging.getLogger("daoyuan.schedule").info(
            "删除计划任务：ok=%s，输出=%s", result["ok"], result["output"]
        )
        if result["ok"]:
            self.task_label.setText(
                "任务名称：道源知识库-自动采集\n状态：已删除\n\n%s"
                % (result["output"] or "命令执行成功")
            )
            QMessageBox.information(self, "计划任务已删除", "计划任务已删除，数据未受影响。")
        else:
            QMessageBox.warning(
                self,
                "删除失败",
                result["output"] or "未找到任务或 schtasks 执行失败。",
            )


class MaintenancePage(QWidget):
    def __init__(self, window: "AdminWindow"):
        super().__init__()
        self.window_ref = window
        self.service = window.service
        self.backups: list[dict] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(10)

        title = QLabel("数据维护")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "备份使用 SQLite 一致性快照，包含内容库和个人状态库，不含 API Key、"
            "日志、HTML 快照或导出文件。恢复前会自动再备份当前数据。"
        )
        subtitle.setObjectName("PageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        db_card = _card()
        db_layout = QVBoxLayout(db_card)
        db_layout.setContentsMargins(22, 16, 22, 16)
        db_layout.setSpacing(6)
        db_heading = QLabel("数据库状态")
        db_heading.setObjectName("Gold")
        db_layout.addWidget(db_heading)
        self.db_label = QLabel("")
        self.db_label.setWordWrap(True)
        self.db_label.setObjectName("Muted")
        db_layout.addWidget(self.db_label)
        layout.addWidget(db_card)

        actions = QHBoxLayout()
        backup_btn = QPushButton("立即创建备份")
        backup_btn.setObjectName("Primary")
        backup_btn.clicked.connect(self.create_backup)
        refresh_btn = QPushButton("刷新备份列表")
        refresh_btn.setObjectName("Ghost")
        refresh_btn.clicked.connect(self.refresh)
        open_btn = QPushButton("打开备份目录")
        open_btn.setObjectName("Ghost")
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.service.config.paths.backups))
            )
        )
        actions.addWidget(backup_btn)
        actions.addWidget(refresh_btn)
        actions.addWidget(open_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self.show_preview)
        layout.addWidget(self.list, 1)

        preview_card = _card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(22, 14, 22, 14)
        preview_layout.setSpacing(6)
        preview_heading = QLabel("备份预览")
        preview_heading.setObjectName("Gold")
        preview_layout.addWidget(preview_heading)
        self.preview = QLabel("选择上方备份查看内容。")
        self.preview.setWordWrap(True)
        self.preview.setObjectName("Muted")
        preview_layout.addWidget(self.preview)
        layout.addWidget(preview_card)

        restore_row = QHBoxLayout()
        restore_btn = QPushButton("恢复内容库（保留个人状态）…")
        restore_btn.setObjectName("Ghost")
        restore_btn.clicked.connect(lambda: self.restore(False))
        restore_all_btn = QPushButton("连同个人状态一起恢复…")
        restore_all_btn.setObjectName("Ghost")
        restore_all_btn.clicked.connect(lambda: self.restore(True))
        restore_row.addWidget(restore_btn)
        restore_row.addWidget(restore_all_btn)
        restore_row.addStretch(1)
        layout.addLayout(restore_row)
        warning = QLabel(
            "恢复前请关闭阅读端。默认只恢复文章内容，保留当前阅读进度和问答记录；"
            "选择“连同个人状态”会用备份中的状态覆盖当前状态。"
        )
        warning.setObjectName("Muted")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.refresh()

    def refresh(self) -> None:
        config = self.service.config
        report = self.service.health()
        if report.get("available"):
            self.db_label.setText(
                "内容库：%s\nschema v%s · 完整性 %s · 文集 %s · 文章 %s · 可读 %s · "
                "段落 %s · 待采 %s · 失败 %s"
                % (
                    config.paths.content_db,
                    report.get("schema_version"),
                    report.get("integrity"),
                    report.get("collections"),
                    report.get("articles"),
                    report.get("ready"),
                    report.get("segments"),
                    report.get("pending"),
                    report.get("failed"),
                )
            )
        else:
            self.db_label.setText(
                "内容库：%s\n尚未初始化，无法备份。" % config.paths.content_db
            )
        current = self.selected_path()
        self.backups = list_backups(config)
        self.list.clear()
        if not self.backups:
            self.list.addItem("暂无备份。点击“立即创建备份”。")
        for info in self.backups:
            content = info.get("content") or {}
            text = "%s  %s\n文章 %s · 可读 %s · %s" % (
                "✔" if info.get("valid") else "✘ 校验未通过",
                info.get("name") or "备份",
                content.get("articles"),
                content.get("ready"),
                info.get("created_at") or "未知时间",
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, info.get("path"))
            self.list.addItem(item)
            if current and info.get("path") == current:
                self.list.setCurrentItem(item)
        if self.backups and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
        if not self.backups:
            self.preview.setText("暂无备份。")

    def selected_path(self) -> str | None:
        item = self.list.currentItem() if getattr(self, "list", None) else None
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def selected_info(self) -> dict | None:
        path = self.selected_path()
        if not path:
            return None
        for info in self.backups:
            if info.get("path") == path:
                return info
        return None

    def show_preview(self, current=None, previous=None) -> None:
        info = self.selected_info()
        if info is None:
            self.preview.setText("选择上方备份查看内容。")
            return
        content = info.get("content") or {}
        state = info.get("state") or {}
        counts = (state or {}).get("counts") or {}
        lines = [
            "备份目录：%s" % (info.get("path") or "—"),
            "状态：%s" % ("校验通过，可以恢复" if info.get("valid") else "校验未通过，禁止恢复"),
            "创建时间：%s · 备注：%s · 程序版本：%s"
            % (
                info.get("created_at") or "—",
                info.get("note") or "—",
                info.get("app_version") or "—",
            ),
            "内容库：文章 %s · 可读 %s · 段落 %s · schema v%s · 完整性 %s"
            % (
                content.get("articles"),
                content.get("ready"),
                content.get("segments"),
                content.get("schema_version"),
                content.get("integrity"),
            ),
            "个人状态：进度 %s · 书签 %s · 问答会话 %s"
            % (
                counts.get("reading_progress", 0),
                counts.get("bookmarks", 0),
                counts.get("qa_sessions", 0),
            ),
        ]
        if info.get("errors"):
            lines.append("问题：" + "；".join(info["errors"]))
        self.preview.setText("\n".join(lines))

    def _busy(self) -> bool:
        if self.window_ref.is_collecting():
            QMessageBox.information(self, "采集进行中", "采集期间不能执行该操作，请先停止采集。")
            return True
        return False

    def create_backup(self) -> None:
        if self._busy():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = create_backup(self.service.config, note="manual_ui")
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "备份失败", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.refresh()
        content = info.get("content") or {}
        logging.getLogger("daoyuan.maintenance").info(
            "创建备份：%s，文章=%s，段落=%s",
            info.get("path"),
            content.get("articles"),
            content.get("segments"),
        )
        QMessageBox.information(
            self,
            "备份完成",
            "备份位置：\n%s\n\n文章 %s 篇，可读 %s 篇，段落 %s 段。\n"
            "备份包不含 API Key。" % (
                info.get("path"),
                content.get("articles"),
                content.get("ready"),
                content.get("segments"),
            ),
        )

    def restore(self, restore_state: bool) -> None:
        if self._busy():
            return
        info = self.selected_info()
        if info is None:
            QMessageBox.information(self, "未选择备份", "请先在列表中选择一个备份。")
            return
        if not info.get("valid"):
            QMessageBox.warning(
                self,
                "备份不可用",
                "该备份未通过校验，禁止恢复：\n%s"
                % "；".join(info.get("errors") or ["未知错误"]),
            )
            return
        content = info.get("content") or {}
        extra = (
            "\n\n注意：你选择了同时恢复个人状态。当前阅读进度、书签和问答记录"
            "会被备份中的状态覆盖。"
            if restore_state
            else "\n\n个人状态（阅读进度、书签、问答记录）将保留，不会回退。"
        )
        reply = QMessageBox.question(
            self,
            "确认恢复数据",
            "将从以下备份恢复内容库：\n%s\n\n"
            "文章 %s 篇，可读 %s 篇。\n"
            "恢复前会自动备份当前内容库，以便回退。\n"
            "请确认阅读端已经关闭。%s\n\n是否继续？"
            % (
                info.get("path"),
                content.get("articles"),
                content.get("ready"),
                extra,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        lock = ProcessLock()
        if not lock.acquire():
            QMessageBox.warning(
                self,
                "采集正在运行",
                "计划任务或另一个维护端正在采集，不能同时恢复数据库。"
                "请等采集结束或先停止任务。",
            )
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.service.close()
            try:
                result = restore_backup(
                    self.service.config, info["path"], restore_state=restore_state
                )
                self.service.initialize()
            except Exception as exc:
                try:
                    self.service.initialize()
                except Exception:
                    logging.getLogger("daoyuan.ui").exception("恢复失败后重新打开数据库失败")
                QMessageBox.warning(self, "恢复失败", str(exc))
                self.refresh()
                return
            self.refresh()
            self.window_ref.refresh_pages()
            logging.getLogger("daoyuan.maintenance").info(
                "恢复备份：%s，恢复个人状态=%s，安全备份=%s",
                info["path"],
                restore_state,
                result.get("safety_backup"),
            )
            QMessageBox.information(
                self,
                "恢复完成",
                "内容库已恢复。\n\n恢复前安全备份：\n%s\n%s"
                % (
                    result.get("safety_backup") or "无",
                    "个人状态已一并恢复。"
                    if result.get("state_restored")
                    else "个人状态已保留。",
                ),
            )
        finally:
            QApplication.restoreOverrideCursor()
            lock.release()


class LogsPage(QWidget):
    def __init__(self, window: "AdminWindow"):
        super().__init__()
        self.service = window.service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(10)
        title = QLabel("日志与诊断")
        title.setObjectName("PageTitle")
        subtitle = QLabel("日志只记录任务统计与错误，不记录完整正文和密钥。")
        subtitle.setObjectName("PageSub")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.open_file)
        layout.addWidget(self.list, 1)
        open_dir = QPushButton("打开日志目录")
        open_dir.setObjectName("Ghost")
        open_dir.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.service.config.paths.logs))
            )
        )
        layout.addWidget(open_dir, 0, Qt.AlignLeft)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        files = self.service.log_files()
        if not files:
            self.list.addItem("暂无日志文件。")
            return
        for path in files:
            self.list.addItem(str(path))

    def open_file(self, item: QListWidgetItem) -> None:
        path = Path(item.text())
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class AdminWindow(QMainWindow):
    NAV = [
        ("运行总览", "overview"),
        ("来源与文集", "sources"),
        ("采集任务", "tasks"),
        ("手动/链接导入", "manual"),
        ("专题库", "topics"),
        ("自动采集", "schedule"),
        ("数据维护", "maintenance"),
        ("日志与诊断", "logs"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.service = AdminService(config)
        self.setWindowTitle("%s %s" % (ADMIN_NAME, __version__))
        self.resize(1240, 820)
        self.setMinimumSize(1040, 680)

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
        sidebar_layout.addWidget(BrandHeader(ADMIN_NAME, "道源知识库 · 维护端"))
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = {}
        for text, key in self.NAV:
            btn = QPushButton(text)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
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

        self.tasks_page = TasksPage(self)
        self.pages: dict[str, QWidget] = {
            "overview": OverviewPage(self),
            "sources": SourcesPage(self),
            "tasks": self.tasks_page,
            "manual": ManualImportPage(self),
            "topics": TopicPage(self),
            "schedule": SchedulePage(self),
            "maintenance": MaintenancePage(self),
            "logs": LogsPage(self),
        }
        self.stack = QStackedWidget()
        for _, key in self.NAV:
            self.stack.addWidget(self.pages[key])
        root.addWidget(self.stack, 1)
        self.statusBar().showMessage("就绪")
        self.switch("overview")

    def is_collecting(self) -> bool:
        return self.tasks_page.is_running()

    def refresh_pages(self) -> None:
        for page in self.pages.values():
            if hasattr(page, "refresh"):
                try:
                    page.refresh()
                except Exception:
                    logging.getLogger("daoyuan.ui").exception("刷新页面失败")

    def switch(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.nav_buttons[key].setChecked(True)
        if hasattr(page, "refresh"):
            page.refresh()

    def closeEvent(self, event):  # noqa: N802
        if self.is_collecting():
            reply = QMessageBox.question(
                self,
                "采集进行中",
                "采集仍在运行。退出会中断本次采集（已入库文章不会丢失）。确定退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if not self.tasks_page.shutdown(20000):
                QMessageBox.warning(
                    self,
                    "仍在抓取",
                    "当前网络请求尚未结束，请稍后再退出，避免任务被强制中断。",
                )
                event.ignore()
                return
        self.service.close()
        super().closeEvent(event)


def main() -> int:
    set_windows_app_id("Daoyuan.KnowledgeBase.Admin")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    config = load_config()
    config.paths.ensure()
    configure_file_logging(config)
    app = QApplication(sys.argv)
    app.setApplicationName(ADMIN_NAME)
    app.setOrganizationName("Daoyuan")
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    apply_theme(app)
    window = AdminWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
