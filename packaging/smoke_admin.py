# -*- coding: utf-8 -*-
"""维护端打包回归：真实 EXE 内打开管理页、备份、计划设置。不访问网络。"""
from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path

from smoke_common import prepare_runtime, result_path, seed


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    out = result_path("admin_result.json")
    report: dict = {
        "mode": "admin",
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "ok": False,
    }
    try:
        from daoyuan import __stage__, __version__

        report["version"] = __version__
        report["stage"] = __stage__
        config, runtime_root = prepare_runtime("admin")
        article_id = seed(config)
        report["runtime_root"] = str(runtime_root)
        report["article_id"] = article_id

        if getattr(sys, "frozen", False):
            for required in ("daoyuan.collector", "daoyuan.cli", "daoyuan.ui_admin"):
                if importlib.util.find_spec(required) is None:
                    raise AssertionError("维护包缺少模块：%s" % required)
            if importlib.util.find_spec("daoyuan.ui_reader") is not None:
                raise AssertionError("维护包不应包含普通用户端界面模块")
            report["required_modules_present"] = True
        else:
            report["module_isolation"] = "skipped_source_mode"

        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication

        from daoyuan.core.schedule import effective_schedule, save_schedule
        from daoyuan.storage.backup import create_backup
        from daoyuan.ui_admin.app import AdminWindow
        from daoyuan.ui_common.theme import apply_theme

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication([])
        apply_theme(app)
        window = AdminWindow(config)
        try:
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            window.resize(1240, 820)
            window.show()
            app.processEvents()

            for key in ("sources", "tasks", "manual", "topics", "schedule", "maintenance"):
                if key not in window.pages:
                    raise AssertionError("缺少管理页：%s" % key)
            health = window.service.health()
            if health.get("integrity") != "ok":
                raise AssertionError("内容库完整性异常：%s" % health)
            report["health"] = health

            collection_id = window.service.add_collection(
                "打包回归维护文集", "1234567890123456789"
            )
            if collection_id <= 0:
                raise AssertionError("手动登记文集失败")
            report["manual_collection_id"] = collection_id

            save_schedule(config, True, 3, "07:45")
            window.switch("schedule")
            page = window.pages["schedule"]
            page.refresh()
            app.processEvents()
            schedule = effective_schedule(config)
            if schedule["interval_days"] != 3 or schedule["daily_time"] != "07:45":
                raise AssertionError("计划设置未保存：%s" % schedule)
            report["schedule"] = schedule

            window.switch("topics")
            topics = window.pages["topics"]
            app.processEvents()
            if topics.collection_combo.currentData() is None:
                raise AssertionError("专题页默认没有选中具体文集")
            if not hasattr(topics, "reclassify_btn") or topics.reclassify_btn.text() != "改类型…":
                raise AssertionError("专题页缺少改类型按钮")
            topics.scan(full=False)
            app.processEvents()
            scan_text = topics.scan_result.text()
            if "专题库更新完成" not in scan_text:
                raise AssertionError("维护端专题库更新失败：%s" % scan_text)
            pending_before = 0
            target_item = None
            tree_groups = []
            for top_index in range(topics.tree.topLevelItemCount()):
                top = topics.tree.topLevelItem(top_index)
                tree_groups.append(top.text(0))
                for child_index in range(top.childCount()):
                    child = top.child(child_index)
                    if child.data(0, Qt.ItemDataRole.UserRole) is None:
                        continue
                    pending_before += 1
                    if "林见深" in child.text(0):
                        target_item = child
            if pending_before < 1 or target_item is None:
                raise AssertionError("维护端专题候选不足：%s" % scan_text)
            topics.tree.setCurrentItem(target_item)
            app.processEvents()
            topics.review("approved")
            app.processEvents()
            if "已收录 1" not in topics.stats_label.text():
                raise AssertionError("维护端专题收录失败：%s" % topics.stats_label.text())
            topics_shot = out.parent / "admin_package_topics.png"
            if not window.grab().save(str(topics_shot)):
                raise AssertionError("专题库截图保存失败")
            report["topics"] = {
                "scan": scan_text,
                "pending_before": pending_before,
                "approved": 1,
                "default_collection": topics.collection_combo.currentText(),
                "reclassify": topics.reclassify_btn.text(),
                "tree_groups": tree_groups,
                "stats": topics.stats_label.text(),
                "screenshot": str(topics_shot),
            }

            from PySide6.QtWidgets import QMessageBox

            window.switch("manual")
            app.processEvents()
            manual = window.pages["manual"]
            index = manual.collection_combo.findData(collection_id)
            if index < 0:
                raise AssertionError("手动导入页没有打包回归文集")
            manual.collection_combo.setCurrentIndex(index)
            if not hasattr(manual, "chapter_edit") or "第87章" not in (
                manual.chapter_edit.placeholderText()
            ):
                raise AssertionError("手动导入页缺少章节号输入框")
            if not hasattr(manual, "link_btn") or manual.link_btn.text() != "采集链接":
                raise AssertionError("手动导入页缺少链接采集按钮")
            if "mp.weixin.qq.com" not in manual.link_edit.placeholderText():
                raise AssertionError("链接采集输入框提示不正确")
            manual.title_edit.setText("手动导入回归文章")
            manual.chapter_edit.setText("1")
            manual.date_edit.setText("2026.10.11")
            manual.url_edit.setText("https://mp.weixin.qq.com/s/package-manual-smoke")
            manual.content_edit.setPlainText(
                "手动导入正文足够长，用于验证检索和专题扫描。\n"
                "第二段：林见深说，天哥，机甲准备好了。"
            )
            original_info = QMessageBox.information
            QMessageBox.information = staticmethod(
                lambda *args, **kwargs: QMessageBox.StandardButton.Ok
            )
            try:
                manual.save()
            finally:
                QMessageBox.information = original_info
            app.processEvents()
            if manual.recent.count() < 1:
                raise AssertionError("手动导入后最近记录为空")
            last_text = manual.last_import_label.text()
            if "第 1 章" not in last_text or "手动导入回归文章" not in last_text:
                raise AssertionError("上次手动导入提示不正确：%s" % last_text)
            imported = window.service.conn.execute(
                "SELECT id, title, status, text_content, publish_time_raw "
                "FROM articles WHERE msgid LIKE 'manual:%' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if imported is None or imported["status"] != "ready":
                raise AssertionError("手动导入文章未进入可读状态")
            if "林见深" not in (imported["text_content"] or ""):
                raise AssertionError("手动导入正文不完整")
            if imported["title"] != "1 手动导入回归文章":
                raise AssertionError("章节号未写入标题：%s" % imported["title"])
            if imported["publish_time_raw"] != "2026-10-11":
                raise AssertionError(
                    "日期未自动规范化：%s" % imported["publish_time_raw"]
                )
            manual_shot = out.parent / "admin_package_manual.png"
            if not window.grab().save(str(manual_shot)):
                raise AssertionError("手动导入截图保存失败")
            report["manual_import"] = {
                "article_id": int(imported["id"]),
                "title": imported["title"],
                "status": imported["status"],
                "publish_time_raw": imported["publish_time_raw"],
                "recent": manual.recent.count(),
                "last_import": manual.last_import_label.text(),
                "link_button": manual.link_btn.text(),
                "screenshot": str(manual_shot),
            }

            # 用假服务只验证按钮接线和提示，不发起任何网络请求；真实链接
            # 解析/入库/冷却由 tests/test_single_link.py 覆盖。
            fake_link_result = {
                "article_id": 999,
                "status": "created",
                "title": "99 链接采集回归文章",
                "collection_id": collection_id,
                "collection_title": "打包回归维护文集",
                "segments": 2,
                "chars": 128,
                "publish_date": "2026-10-12",
                "chapter": "99",
                "matched_by": "msgid",
                "previous_status": "",
                "origin": "link",
                "album_matched": True,
                "topics": {},
            }
            original_collect_link = manual.service.collect_link
            manual.service.collect_link = (
                lambda url, collection_id=None: dict(fake_link_result)
            )
            manual.link_edit.setText("https://mp.weixin.qq.com/s/package-link-smoke")
            original_info = QMessageBox.information
            QMessageBox.information = staticmethod(
                lambda *args, **kwargs: QMessageBox.StandardButton.Ok
            )
            try:
                manual.collect_link()
            finally:
                manual.service.collect_link = original_collect_link
                QMessageBox.information = original_info
            app.processEvents()
            if "已采集" not in manual.link_status.text():
                raise AssertionError(
                    "链接采集按钮未显示成功状态：%s" % manual.link_status.text()
                )
            report["link_button_smoke"] = {
                "status": manual.link_status.text(),
                "input_cleared": manual.link_edit.text() == "",
            }

            backup = create_backup(config, note="package_smoke")
            if not backup.get("valid"):
                raise AssertionError("备份校验失败：%s" % backup)
            report["backup"] = {
                "path": backup["path"],
                "articles": (backup.get("content") or {}).get("articles"),
                "ready": (backup.get("content") or {}).get("ready"),
                "topic_entities": (backup.get("topics") or {})
                .get("counts", {})
                .get("entities"),
            }
            maintenance = window.pages["maintenance"]
            window.switch("maintenance")
            app.processEvents()
            if maintenance.list.count() < 1:
                raise AssertionError("维护页没有显示备份")
            if "校验通过" not in maintenance.preview.text():
                raise AssertionError("备份预览未通过")

            shot = out.parent / "admin_package_maintenance.png"
            if not window.grab().save(str(shot)):
                raise AssertionError("维护页截图保存失败")
            report["maintenance_screenshot"] = str(shot)

            window.switch("schedule")
            app.processEvents()
            schedule_shot = out.parent / "admin_package_schedule.png"
            if not window.grab().save(str(schedule_shot)):
                raise AssertionError("计划页截图保存失败")
            report["schedule_screenshot"] = str(schedule_shot)
        finally:
            window.close()
            app.processEvents()
            window.service.close()
        report["ok"] = True
        _write(out, report)
        return 0
    except Exception as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["traceback"] = traceback.format_exc()
        _write(out, report)
        return 1
