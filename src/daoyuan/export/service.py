# -*- coding: utf-8 -*-
"""导出服务：数据装载、TXT / DOCX / PDF 分发。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..core.errors import ExportError
from .docx import write_docx
from .pdf import write_pdf

INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, fallback: str = "道源知识库") -> str:
    cleaned = INVALID_FILENAME.sub("_", (name or "").strip())
    cleaned = cleaned.strip(" ._")
    return cleaned[:80] or fallback


def default_export_name(prefix: str, fmt: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return "%s_%s.%s" % (safe_filename(prefix), stamp, fmt)


def load_export_articles(service, collection_id: int | None = None) -> list[dict]:
    items = service.ready_articles(collection_id)
    result = []
    for item in items:
        full = service.article(int(item["id"])) or {}
        merged = dict(item)
        for key in (
            "title",
            "page_author",
            "publish_date",
            "title_date",
            "source_url",
            "text_content",
            "safe_html",
        ):
            merged[key] = full.get(key)
        merged["segments"] = service.segments(int(item["id"]))
        result.append(merged)
    return result


def build_txt(articles: list[dict]) -> str:
    blocks = []
    for article in articles:
        header = [
            article.get("title") or "（无标题）",
            "文集：%s" % (article.get("collection_title") or "未归类"),
            "作者：%s" % (article.get("page_author") or "未署名"),
            "发布时间：%s" % (article.get("publish_date") or "未知"),
            "来源：微信公众号",
        ]
        if article.get("source_url"):
            header.append("原文链接：%s" % article["source_url"])
        body = article.get("text_content") or "\n".join(
            segment.get("plain_text") or ""
            for segment in article.get("segments") or []
        )
        blocks.append(
            "\n".join(header) + "\n" + ("-" * 40) + "\n\n" + body.strip()
        )
    if not blocks:
        raise ExportError("没有可导出的文章")
    return ("\n\n\n" + ("=" * 40) + "\n\n").join(blocks) + "\n"


def export_txt(articles: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_txt(articles), encoding="utf-8-sig")


def export_docx(articles: list[dict], path: Path) -> None:
    if not articles:
        raise ExportError("没有可导出的文章")
    write_docx(articles, Path(path))


def export_pdf(articles: list[dict], path: Path, title: str = "道源知识库") -> None:
    if not articles:
        raise ExportError("没有可导出的文章")
    write_pdf(articles, Path(path), doc_title=title)


def export_articles(articles: list[dict], path: str | Path, fmt: str) -> None:
    fmt = (fmt or "").lower()
    if fmt not in ("txt", "docx", "pdf"):
        raise ExportError("不支持的导出格式：%s" % fmt)
    if not articles:
        raise ExportError("没有可导出的文章")
    if fmt == "txt":
        export_txt(articles, Path(path))
    elif fmt == "docx":
        export_docx(articles, Path(path))
    else:
        export_pdf(articles, Path(path))
