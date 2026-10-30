# -*- coding: utf-8 -*-
"""真实导出冒烟：TXT / DOCX / PDF，并尝试把 PDF 首页渲染为图片。

开发机脚本：读取当前配置的数据目录，并向 exports/ 写入导出文件。
请先备份或通过 DAOYUAN_CONFIG 指向测试数据，不要直接对生产数据运行。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from daoyuan.core.config import load_config
from daoyuan.export.service import (
    default_export_name,
    export_docx,
    export_pdf,
    export_txt,
    load_export_articles,
)
from daoyuan.reader.service import ReaderService


def main() -> int:
    config = load_config()
    config.paths.ensure()
    app = QApplication([])
    service = ReaderService(config)
    try:
        articles = load_export_articles(service)
        if not articles:
            print("没有可导出的文章")
            return 2
        out_dir = config.paths.exports / "m2_smoke"
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {}
        for fmt, func in (
            ("txt", export_txt),
            ("docx", export_docx),
            ("pdf", export_pdf),
        ):
            target = out_dir / default_export_name("道源知识库_全部", fmt)
            func(articles, target)
            paths[fmt] = target
            print("%s: %s (%d 字节)" % (fmt, target, target.stat().st_size))
        header = paths["pdf"].read_bytes()[:5]
        print("PDF 文件头：%r" % header)
        if header != b"%PDF-":
            return 3

        try:
            from PySide6.QtPdf import QPdfDocument
        except ImportError:
            print("QtPdf 不可用，跳过 PDF 渲染检查")
            return 0
        pdf = QPdfDocument()
        pdf.load(str(paths["pdf"]))
        print("PDF 页数：%d" % pdf.pageCount())
        if pdf.pageCount() > 0:
            image = pdf.render(0, QSize(1240, 1754))
            png = out_dir / "pdf_page1.png"
            image.save(str(png))
            print("PDF 首页：%s" % png)
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
