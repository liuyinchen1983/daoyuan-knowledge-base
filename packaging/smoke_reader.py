# -*- coding: utf-8 -*-
"""普通用户端打包回归：真实 EXE 内完成阅读、双字检索、三格式导出。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import traceback
from pathlib import Path

from smoke_common import TEST_TEXT, TEST_TITLE, prepare_runtime, result_path, seed


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    out = result_path("reader_result.json")
    report: dict = {
        "mode": "reader",
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "ok": False,
    }
    try:
        from daoyuan import __stage__, __version__

        report["version"] = __version__
        report["stage"] = __stage__
        config, runtime_root = prepare_runtime("reader")
        article_id = seed(config)
        report["runtime_root"] = str(runtime_root)
        report["article_id"] = article_id

        # 专题库：扫描合成文章，收录一个实体，供阅读端专题页验证。
        conn = sqlite3.connect(str(config.paths.content_db))
        try:
            row = conn.execute(
                "SELECT collection_id FROM article_collections WHERE article_id=?",
                (article_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            raise AssertionError("合成文章没有关联文集")
        collection_id = int(row[0])

        from daoyuan.topics.scanner import scan_collection
        from daoyuan.topics.store import TopicStore

        topic_scan = scan_collection(config, collection_id)
        store = TopicStore.open(config.paths.topics_db)
        try:
            entity = store.find_entity(collection_id, "person", "林见深")
            if not entity:
                raise AssertionError("专题扫描没有生成合成人物：%s" % topic_scan)
            store.review(entity["id"], "approved")
            topic_stats = store.approved_stats(collection_id)
        finally:
            store.close()
        if topic_stats["entities"] != 1 or topic_stats["mentions"] < 1:
            raise AssertionError("专题审核结果异常：%s" % topic_stats)
        report["topic_scan"] = topic_scan
        report["topic_stats"] = topic_stats

        if getattr(sys, "frozen", False):
            for excluded in ("daoyuan.collector", "daoyuan.ui_admin", "daoyuan.cli"):
                if importlib.util.find_spec(excluded) is not None:
                    raise AssertionError("普通用户包不应包含模块：%s" % excluded)
            report["excluded_modules_absent"] = True
        else:
            # 源码方式只验证功能，模块隔离由打包后的 PYZ 检查负责。
            report["module_isolation"] = "skipped_source_mode"

        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication

        from daoyuan.ui_common.theme import apply_theme
        from daoyuan.ui_reader.app import ArticleDialog, ReaderWindow

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication([])
        apply_theme(app)
        window = ReaderWindow(config)
        try:
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            window.resize(1280, 840)
            window.show()
            app.processEvents()

            if not window.service.available:
                raise AssertionError("打包后端未能打开合成内容库")
            article = window.service.article(article_id)
            if not article or article.get("title") != TEST_TITLE:
                raise AssertionError("未能读取合成文章")
            segments = window.service.segments(article_id)
            if len(segments) != 3:
                raise AssertionError("段落数不符：%s" % len(segments))
            report["reading"] = {
                "title": article.get("title"),
                "segments": len(segments),
            }

            window.switch("library")
            app.processEvents()
            window.switch("search")
            page = window.pages["search"]
            page.query.setText("修行")
            page.run_search()
            app.processEvents()
            if not page.last_rows:
                raise AssertionError("双字检索没有结果")
            opened_from_search = []
            original_search_open = window.open_article
            window.open_article = lambda aid, sid=None: opened_from_search.append(
                (aid, sid)
            )
            try:
                result_item = page.results.item(0)
                # 真实双击在 Windows 上会先后发出这两个信号；两者不能各开一次。
                page.results.itemDoubleClicked.emit(result_item)
                page.results.itemActivated.emit(result_item)
            finally:
                window.open_article = original_search_open
            if len(opened_from_search) != 1:
                raise AssertionError(
                    "双击检索结果打开了 %d 次阅读窗口：%s"
                    % (len(opened_from_search), opened_from_search)
                )
            report["search"] = {
                "term": "修行",
                "hits": len(page.last_rows),
                "status": page.status.text(),
                "open_once": True,
            }

            window.switch("topics")
            app.processEvents()
            topics = window.pages.get("topics")
            if topics is None:
                raise AssertionError("阅读端缺少专题库页面")
            tree_groups = [
                topics.tree.topLevelItem(index).text(0)
                for index in range(topics.tree.topLevelItemCount())
            ]
            topic_count = sum(
                topics.tree.topLevelItem(index).childCount()
                for index in range(topics.tree.topLevelItemCount())
            )
            if topic_count < 1:
                raise AssertionError("阅读端专题库没有已收录条目")
            if not any("人物" in label for label in tree_groups):
                raise AssertionError("阅读端专题树缺少人物节点：%s" % tree_groups)
            detail_text = topics.detail.toPlainText()
            if "原文证据" not in detail_text or not (
                "打开原文" in detail_text or "打开文章" in detail_text
            ):
                raise AssertionError("专题详情缺少原文证据或文章入口")
            if "已收录 1 条" not in topics.stats_label.text():
                raise AssertionError("专题统计不符：%s" % topics.stats_label.text())
            topics_shot = out.parent / "reader_package_topics.png"
            if not window.grab().save(str(topics_shot)):
                raise AssertionError("专题库截图保存失败")
            opened = []
            original_open = window.open_article
            window.open_article = lambda aid, sid=None: opened.append((aid, sid))
            try:
                topics.on_anchor(
                    QUrl(
                        "daoyuan-article:///%s/%s"
                        % (article_id, segments[0]["id"])
                    )
                )
            finally:
                window.open_article = original_open
            if opened != [(article_id, segments[0]["id"])]:
                raise AssertionError("专题文章跳转参数不符：%s" % opened)
            report["topics"] = {
                "list_items": topic_count,
                "tree_groups": tree_groups,
                "stats": topics.stats_label.text(),
                "has_evidence": True,
                "anchor_ok": True,
                "screenshot": str(topics_shot),
            }

            dialog = ArticleDialog(
                window.service, article, window, segment_id=segments[0]["id"]
            )
            dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            dialog.show()
            app.processEvents()
            if not dialog.segments:
                raise AssertionError("阅读窗口没有载入段落")
            article_shot = out.parent / "reader_package_article.png"
            if not dialog.grab().save(str(article_shot)):
                raise AssertionError("阅读窗口截图保存失败")
            dialog.close()
            app.processEvents()
            report["article_screenshot"] = str(article_shot)

            from daoyuan.export.service import export_articles

            exports_dir = runtime_root / "exports"
            exports_dir.mkdir(parents=True, exist_ok=True)
            payload = dict(article)
            payload["segments"] = segments
            export_sizes = {}
            for fmt in ("txt", "docx", "pdf"):
                target = exports_dir / ("package_smoke.%s" % fmt)
                export_articles([payload], target, fmt)
                size = target.stat().st_size
                if size <= 0:
                    raise AssertionError("%s 导出为空" % fmt)
                export_sizes[fmt] = size
            report["exports"] = export_sizes

            from daoyuan.storage.backup import create_backup

            backup = create_backup(config, note="package_smoke_import")
            if not backup.get("valid"):
                raise AssertionError("资料包准备失败")
            conn = sqlite3.connect(str(config.paths.content_db))
            try:
                conn.execute(
                    "UPDATE articles SET title='导入前临时标题' WHERE id=?",
                    (article_id,),
                )
                conn.commit()
            finally:
                conn.close()

            window.switch("settings")
            app.processEvents()
            settings_shot = out.parent / "reader_package_settings.png"
            if not window.grab().save(str(settings_shot)):
                raise AssertionError("设置页截图保存失败")
            report["settings_screenshot"] = str(settings_shot)

            from PySide6.QtWidgets import QMessageBox

            original_question = QMessageBox.question
            original_information = QMessageBox.information
            QMessageBox.question = staticmethod(
                lambda *args, **kwargs: QMessageBox.StandardButton.Yes
            )
            QMessageBox.information = staticmethod(
                lambda *args, **kwargs: QMessageBox.StandardButton.Ok
            )
            try:
                imported = window.pages["settings"].import_package(backup["path"])
            finally:
                QMessageBox.question = original_question
                QMessageBox.information = original_information
            if not imported:
                raise AssertionError("资料包导入返回失败")
            restored = window.service.article(article_id)
            if not restored or restored.get("title") != TEST_TITLE:
                raise AssertionError("资料包导入后文章标题未恢复")
            if not window.service.available:
                raise AssertionError("资料包导入后内容库不可用")
            report["import"] = {
                "backup": backup["path"],
                "title_after_import": restored.get("title"),
                "text_matches": restored.get("text_content") == TEST_TEXT,
            }
            if not report["import"]["text_matches"]:
                raise AssertionError("资料包导入后正文不一致")

            shot = out.parent / "reader_package_home.png"
            window.switch("home")
            app.processEvents()
            if not window.grab().save(str(shot)):
                raise AssertionError("首页截图保存失败")
            report["home_screenshot"] = str(shot)
        finally:
            window.close()
            app.processEvents()
        report["ok"] = True
        _write(out, report)
        return 0
    except Exception as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["traceback"] = traceback.format_exc()
        _write(out, report)
        return 1
