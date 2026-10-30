# -*- coding: utf-8 -*-
"""维护端 M3 冒烟：计划页、备份列表、恢复流程与截图。不访问网络。"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from daoyuan.core.config import load_config  # noqa: E402
from daoyuan.core.models import ListingItem, ParsedArticle  # noqa: E402
from daoyuan.core.schedule import effective_schedule, save_schedule  # noqa: E402
from daoyuan.storage.backup import create_backup  # noqa: E402
from daoyuan.storage.db import connect, initialize_content_db  # noqa: E402
from daoyuan.storage.repositories import (  # noqa: E402
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)
from daoyuan.storage.state_db import StateStore  # noqa: E402
from daoyuan.ui_admin.app import AdminWindow, CollectionDialog  # noqa: E402
from daoyuan.ui_common.theme import apply_theme  # noqa: E402


def make_parsed(text: str) -> ParsedArticle:
    paragraphs = [p for p in text.split("\n") if p.strip()]
    segments = []
    pos = 0
    for index, paragraph in enumerate(paragraphs):
        segments.append(
            type(
                "S",
                (),
                {
                    "seq_no": index,
                    "plain_text": paragraph,
                    "safe_html_fragment": "<p>%s</p>" % paragraph,
                    "char_start": pos,
                    "char_end": pos + len(paragraph),
                    "segment_hash": hashlib.sha256(
                        paragraph.encode("utf-8")
                    ).hexdigest(),
                },
            )()
        )
        pos += len(paragraph) + 1
    return ParsedArticle(
        title="修行札记 2026.10.30",
        page_author="测试作者",
        publish_ts=1793078970,
        publish_time_raw="1793078970",
        publish_time_source="page_ct",
        safe_html="",
        plain_text="\n".join(paragraphs),
        title_date="2026-10-30",
        segments=segments,
        source_url="https://mp.weixin.qq.com/s?__biz=x&mid=1&chksm=abc",
        text_hash=hashlib.sha256("\n".join(paragraphs).encode("utf-8")).hexdigest(),
    )


def seed(config) -> tuple[int, Path]:
    conn = initialize_content_db(config.paths.content_db)
    source_id = SourceRepository(conn).upsert("biz", "示例公众号")
    collection_id = CollectionRepository(conn).upsert(source_id, "album", "测试文集")
    article_id, _ = ArticleRepository(conn).upsert_discovered(
        source_id,
        ListingItem(
            msgid="1",
            itemidx="0",
            title="修行札记 2026.10.30",
            url="https://mp.weixin.qq.com/s?__biz=x&mid=1&chksm=abc",
        ),
        collection_id,
        0,
    )
    ArticleRepository(conn).mark_ready(
        article_id, make_parsed("修行之路漫长。\n见深与天哥同行。")
    )
    conn.close()
    store = StateStore(config.paths.state_db)
    store.save_progress(article_id, None, 10, 42.0)
    store.close()
    backup = create_backup(config, note="smoke_seed")
    return article_id, Path(backup["path"])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "config.toml"
        config_path.write_text(
            '[app]\ndata_dir = "%s"\n' % (root / "runtime").as_posix(),
            encoding="utf-8",
        )
        config = load_config(config_path)
        config.paths.ensure()
        article_id, backup_path = seed(config)
        assert backup_path.exists()

        app = QApplication([])
        apply_theme(app)
        window = AdminWindow(config)
        try:
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            window.resize(1240, 820)
            window.show()
            app.processEvents()

            manual_id = window.service.add_collection(
                "手动登记测试文集", "1234567890123456789"
            )
            titles = [row["title"] for row in window.service.collections()]
            assert manual_id > 0
            assert "手动登记测试文集" in titles, titles

            schedule_page = window.pages["schedule"]
            window.switch("schedule")
            app.processEvents()
            save_schedule(config, True, 2, "08:30")
            schedule_page.refresh()
            assert schedule_page.interval_spin.value() == 2, schedule_page.interval_spin.value()
            assert schedule_page.time_edit.time().toString("HH:mm") == "08:30"
            assert effective_schedule(config)["interval_days"] == 2
            shots = load_config().paths.snapshots
            shots.mkdir(parents=True, exist_ok=True)
            schedule_shot = shots / "admin_schedule.png"
            assert window.grab().save(str(schedule_shot))

            window.switch("sources")
            app.processEvents()
            dialog = CollectionDialog(window)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            dialog.show()
            app.processEvents()
            collection_shot = shots / "admin_collection_dialog.png"
            assert dialog.grab().save(str(collection_shot))
            dialog.close()
            app.processEvents()

            maintenance_page = window.pages["maintenance"]
            window.switch("maintenance")
            app.processEvents()
            assert maintenance_page.list.count() >= 1
            assert maintenance_page.selected_info() is not None
            assert "校验通过" in maintenance_page.preview.text()
            maintenance_shot = shots / "admin_maintenance.png"
            assert window.grab().save(str(maintenance_shot))

            conn = connect(config.paths.content_db)
            conn.execute("UPDATE articles SET title='已被修改' WHERE id=?", (article_id,))
            conn.commit()
            conn.close()
            store = StateStore(config.paths.state_db)
            store.save_progress(article_id, None, 20, 77.0)
            store.close()

            original_question = QMessageBox.question
            original_information = QMessageBox.information
            QMessageBox.question = staticmethod(
                lambda *args, **kwargs: QMessageBox.StandardButton.Yes
            )
            QMessageBox.information = staticmethod(
                lambda *args, **kwargs: QMessageBox.StandardButton.Ok
            )
            try:
                maintenance_page.refresh()
                maintenance_page.restore(False)
            finally:
                QMessageBox.question = original_question
                QMessageBox.information = original_information

            conn = connect(config.paths.content_db, readonly=True)
            try:
                title = conn.execute(
                    "SELECT title FROM articles WHERE id=?", (article_id,)
                ).fetchone()[0]
            finally:
                conn.close()
            assert title == "修行札记 2026.10.30", title
            store = StateStore(config.paths.state_db)
            try:
                progress = store.get_progress(article_id)
            finally:
                store.close()
            assert progress["percent"] == 77.0, progress
            assert len(maintenance_page.backups) >= 2, maintenance_page.backups
            assert window.service.health()["integrity"] == "ok"
        finally:
            window.close()
            app.processEvents()
            window.service.close()
    print("计划页截图：%s" % schedule_shot)
    print("手动登记对话框截图：%s" % collection_shot)
    print("维护页截图：%s" % maintenance_shot)
    print("恢复后文章标题：%s" % title)
    print("恢复后个人进度：%.1f%%（默认保留当前状态）" % progress["percent"])
    print("备份数量（含恢复前安全备份）：%d" % len(maintenance_page.backups))
    print("手动登记文集：ID %s（未访问网络）" % manual_id)
    print("ADMIN_M3_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
