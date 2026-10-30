# -*- coding: utf-8 -*-
"""阅读端 M2 冒烟：真实检索、命中跳转、上下篇与线程导出（不访问网络）。

开发机脚本：读取当前配置的数据目录，并向 snapshots/、exports/ 写入截图和导出文件。
请先备份或通过 DAOYUAN_CONFIG 指向测试数据，不要直接对生产数据运行。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QDate, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daoyuan.core.config import load_config  # noqa: E402
from daoyuan.ui_common.theme import apply_theme  # noqa: E402
from daoyuan.ui_reader.app import ArticleDialog, ExportWorker, ReaderWindow  # noqa: E402


def pump(app: QApplication, seconds: float = 0.6) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.03)


def main() -> int:
    config = load_config()
    config.paths.ensure()
    app = QApplication([])
    apply_theme(app)

    window = ReaderWindow(config)
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.show()
    pump(app)

    search_page = window.pages["search"]
    search_page.query.setText("修行")
    search_page.run_search()
    pump(app)
    print("检索状态：%s" % search_page.status.text())
    print("结果数：%d" % search_page.results.count())
    if search_page.results.count() == 0:
        print("真实数据中没有命中，停止冒烟")
        window.close()
        return 2

    search_page.from_check.setChecked(True)
    search_page.from_date.setDate(QDate(2026, 10, 1))
    search_page.to_check.setChecked(True)
    search_page.to_date.setDate(QDate(2026, 10, 31))
    search_page.run_search()
    pump(app)
    print("日期筛选：%s（结果 %d）" % (search_page.status.text(), search_page.results.count()))
    october_count = search_page.results.count()
    if october_count < 1:
        print("日期筛选结果异常：本月没有结果")
        window.close()
        return 6
    # 用未来时间段验证确实排除范围外文章，不依赖真实库里恰好有几篇。
    search_page.from_date.setDate(QDate(2030, 1, 1))
    search_page.to_date.setDate(QDate(2030, 12, 31))
    search_page.run_search()
    pump(app)
    if search_page.results.count() != 0:
        print("日期筛选结果异常：未来时间段仍有结果")
        window.close()
        return 6
    search_page.from_date.setDate(QDate(2026, 10, 1))
    search_page.to_date.setDate(QDate(2026, 10, 31))
    search_page.run_search()
    pump(app)
    if search_page.results.count() != october_count:
        print("日期筛选结果异常：恢复月份后结果数不一致")
        window.close()
        return 6
    search_page.from_check.setChecked(False)
    search_page.to_check.setChecked(False)
    search_page.run_search()
    pump(app)

    rows = search_page.last_rows
    target = next((row for row in rows if (row.get("seq_no") or 0) >= 5), rows[0])
    print(
        "选择命中：%s / 段 %s / 段号 %s"
        % (target["title"], target["segment_id"], target["seq_no"])
    )

    snapshots = config.data_dir / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    window.switch("home")
    pump(app)
    window.grab().save(str(snapshots / "reader_home.png"))
    print("首页截图：%s" % (snapshots / "reader_home.png"))
    window.switch("library")
    pump(app)
    window.grab().save(str(snapshots / "reader_library.png"))
    print("文库截图：%s" % (snapshots / "reader_library.png"))
    window.switch("search")
    pump(app)
    window.grab().save(str(snapshots / "reader_search.png"))
    print("检索截图：%s" % (snapshots / "reader_search.png"))

    window.switch("export")
    pump(app)
    export_page = window.pages["export"]
    print("导出范围项：%s" % export_page.scope.currentText())
    print("导出状态：%s" % export_page.status.text())
    window.grab().save(str(snapshots / "reader_export.png"))
    print("导出截图：%s" % (snapshots / "reader_export.png"))
    window.switch("search")
    pump(app)

    article = window.service.article(int(target["article_id"]))
    dialog = ArticleDialog(
        window.service, article, window, segment_id=target["segment_id"]
    )
    dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
    dialog.show()
    pump(app, 1.0)
    bar = dialog.browser.verticalScrollBar()
    print("命中段落滚动位置：%d / %d" % (bar.value(), bar.maximum()))
    if bar.maximum() > 0 and bar.value() <= 0:
        print("警告：未能滚动到命中段落")
    print(
        "上/下篇按钮：%s / %s"
        % (dialog.prev_btn.isEnabled(), dialog.next_btn.isEnabled())
    )
    dialog.grab().save(str(snapshots / "reader_article_hit.png"))
    print("阅读截图：%s" % (snapshots / "reader_article_hit.png"))

    dialog.apply_reader_prefs(
        theme="night", mode="comfort", font_size=20, line_height="2.0", width=980
    )
    pump(app, 0.6)
    dialog.grab().save(str(snapshots / "reader_article_night.png"))
    print("夜读截图：%s" % (snapshots / "reader_article_night.png"))
    print("阅读偏好：%s" % dialog.prefs)
    settings_dialog = dialog.build_reading_settings_dialog()
    settings_dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
    settings_dialog.show()
    pump(app, 0.5)
    settings_dialog.grab().save(str(snapshots / "reader_settings.png"))
    print("阅读设置截图：%s" % (snapshots / "reader_settings.png"))
    settings_dialog.close()
    dialog.apply_reader_prefs(
        theme="paper", mode="original", font_size=18, line_height="1.8", width=820
    )
    pump(app, 0.4)

    out_dir = config.paths.exports / "m2_ui_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)

    dialog_pdf = out_dir / "dialog_article.pdf"
    if not dialog.export_article_to(str(dialog_pdf), "pdf"):
        print("本篇导出失败：%s" % dialog.export_status.text())
        dialog.close()
        window.close()
        return 7
    print(
        "本篇导出：%s（%d 字节）"
        % (dialog_pdf, dialog_pdf.stat().st_size)
    )

    page_path = out_dir / "page_export.txt"
    export_page.begin_export(str(page_path), "txt", None, len(rows))
    deadline = time.time() + 30
    while export_page.worker is not None and time.time() < deadline:
        pump(app, 0.05)
    pump(app, 0.5)
    print("导出页状态：%s" % export_page.status.text())
    if not page_path.exists() or page_path.stat().st_size == 0:
        print("导出页未生成文件")
        dialog.close()
        window.close()
        return 5
    print("导出页文件：%s（%d 字节）" % (page_path, page_path.stat().st_size))

    for fmt in ("docx", "pdf"):
        path = out_dir / ("reader_worker_%d.%s" % (target["article_id"], fmt))
        state = {"done": None, "error": None}
        worker = ExportWorker(
            config, int(target.get("collection_id") or 0) or None, str(path), fmt
        )
        worker.done.connect(lambda p, n: state.update(done=(p, n)))
        worker.failed.connect(lambda message: state.update(error=message))
        worker.start()
        deadline = time.time() + 60
        while worker.isRunning() and time.time() < deadline:
            pump(app, 0.05)
        worker.wait(5000)
        pump(app, 0.3)
        if state["error"]:
            print("%s 导出失败：%s" % (fmt, state["error"]))
            dialog.close()
            window.close()
            return 3
        if not state["done"]:
            print("%s 导出超时" % fmt)
            dialog.close()
            window.close()
            return 4
        print(
            "%s 线程导出：%s（%d 字节）"
            % (fmt, state["done"][0], Path(state["done"][0]).stat().st_size)
        )

    dialog.close()
    window.close()
    pump(app)
    print("READER_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
