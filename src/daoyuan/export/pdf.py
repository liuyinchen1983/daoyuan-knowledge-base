# -*- coding: utf-8 -*-
"""使用 Qt 生成 PDF，保留段落与静态强调色。"""
from __future__ import annotations

import html
from pathlib import Path

from ..core.errors import ExportError


def build_pdf_html(articles: list[dict], doc_title: str = "道源知识库") -> str:
    blocks = []
    for article in articles:
        meta_parts = [
            article.get("collection_title") or "",
            article.get("page_author") or "",
            article.get("publish_date") or "",
            "来源：微信公众号",
        ]
        meta = " · ".join([part for part in meta_parts if part])
        body = article.get("safe_html") or ""
        if not body:
            paragraphs = (article.get("text_content") or "").split("\n")
            body = "".join(
                "<p>%s</p>" % html.escape(p) for p in paragraphs if p.strip()
            )
        blocks.append(
            "<div class=\"article\">"
            "<h1>%s</h1><p class=\"meta\">%s</p>"
            "%s</div>"
            % (
                html.escape(article.get("title") or "（无标题）"),
                html.escape(meta),
                body,
            )
        )
    return (
        "<html><head><meta charset=\"utf-8\"><style>"
        "body { font-family: 'Microsoft YaHei', 'SimSun'; color: #283540; "
        "font-size: 11pt; line-height: 1.7; }"
        "h1 { font-size: 17pt; text-align: center; margin: 0 0 6pt 0; }"
        ".meta { color: #6E7B82; font-size: 9pt; text-align: center; "
        "margin: 0 0 14pt 0; }"
        "p { margin: 0 0 8pt 0; }"
        "blockquote { color: #5E6B72; border-left: 3px solid #B49763; "
        "padding-left: 10pt; margin-left: 0; }"
        "hr { border: none; border-top: 1px solid #E2DACB; margin: 18pt 0; }"
        ".article { margin-bottom: 18pt; }"
        "</style></head><body><h1 style=\"font-size:12pt;color:#B49763;\">"
        "%s</h1>%s</body></html>"
        % (html.escape(doc_title), "<hr/>".join(blocks))
    )


def write_pdf(articles: list[dict], path: Path, doc_title: str = "道源知识库") -> None:
    try:
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - 运行环境已确认有 PySide6
        raise ExportError("缺少 PySide6，无法导出 PDF") from exc

    if QApplication.instance() is None:
        raise ExportError("PDF 导出需要图形界面环境")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(
        QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter
    )
    writer.setResolution(150)
    document = QTextDocument()
    document.setHtml(build_pdf_html(articles, doc_title))
    document.print_(writer)
