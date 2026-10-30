# -*- coding: utf-8 -*-
"""标题清理、章节号识别与章节排序。

手动导入、链接采集和阅读端文库共用这一套规则，避免“01 前导”被排到
“52 绝境”后面，也避免零宽字符导致章节号识别失败。
"""
from __future__ import annotations

import re

_INVISIBLE_RE = re.compile(
    "[%s]" % "".join(chr(code) for code in (0x200B, 0xFEFF, 0x200C, 0x200D, 0x2060))
)

_LEADING_CHAPTER_RE = re.compile(
    r"^\s*(?:第\s*)?(\d{1,4})\s*(?:章|回|节|篇)?(?=\s|$|[、.．:：\-—])"
)
_TRAILING_DATE_RE = re.compile(
    r"\s*(?<!\d)\d{4}[./\-年]\d{1,2}[./\-月]\d{1,2}日?\s*$"
)


def clean_title(text: str) -> str:
    """去掉标题里常见的零宽字符，避免章节号识别失败。"""
    return _INVISIBLE_RE.sub("", text or "").strip()


def chapter_number(title: str) -> str:
    """从标题开头识别章节号；无法识别时返回空字符串。"""
    match = _LEADING_CHAPTER_RE.match(clean_title(title))
    if not match:
        return ""
    number = int(match.group(1))
    if 1900 <= number <= 2100:  # 开头的年份不是章节号
        return ""
    return str(number)


def strip_title_date(title: str) -> str:
    """去掉标题末尾的发布日期，列表里日期单独显示，避免重复。"""
    return _TRAILING_DATE_RE.sub("", clean_title(title)).strip()


def strip_chapter_prefix(title: str) -> str:
    """去掉标题开头的章节号，供文库列表和去重比较使用。"""
    cleaned = clean_title(title)
    if not chapter_number(cleaned):
        return cleaned
    return _LEADING_CHAPTER_RE.sub("", cleaned, count=1).strip()


def chapter_sort_key(
    title: str,
    serial_no: int | None = 0,
    article_id: int = 0,
    publish_ts: int | None = 0,
) -> tuple:
    """文库排序键：有章节号按数字升序，没有章节号的排在后面并按收录顺序。"""
    number = chapter_number(title)
    if number:
        return (0, int(number), int(publish_ts or 0), int(article_id or 0))
    return (1, int(serial_no or 0), int(article_id or 0), 0)
