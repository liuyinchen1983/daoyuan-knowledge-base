# -*- coding: utf-8 -*-
"""公众号正文解析：安全静态 HTML、纯文本、段落与偏移。"""
from __future__ import annotations

import hashlib
import html as html_mod
import re
from html.parser import HTMLParser

from ..core.errors import ArticleUnavailableError, ParseError, RiskControlError
from ..core.models import ArticleSegment, ParsedArticle

ZERO_WIDTH = "​‌‍﻿­"
BLOCK_TAGS = {"p", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "li", "section"}
INLINE_MAP = {
    "strong": "strong",
    "b": "strong",
    "em": "em",
    "i": "em",
    "u": "u",
    "span": "span",
    "sub": "sub",
    "sup": "sup",
    "code": "code",
}
SKIP_TAGS = {
    "script",
    "style",
    "iframe",
    "svg",
    "form",
    "button",
    "input",
    "audio",
    "video",
    "noscript",
    "canvas",
    "img",
    "object",
    "embed",
}
NOISE_MARKERS = (
    "预览时标签不可点",
    "微信扫一扫",
    "长按识别二维码",
    "点击上方蓝字关注我们",
    "点个在看",
)
SAFE_COLOR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)|"
    r"rgba\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*(0|1|0?\.\d+)\s*\)|"
    r"[a-zA-Z]{3,20})$"
)


def clean_text(value: str) -> str:
    for ch in ZERO_WIDTH:
        value = value.replace(ch, "")
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def sanitize_style(style: str | None) -> str:
    if not style:
        return ""
    props: list[str] = []
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        key, value = declaration.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        if key in ("color", "background-color") and SAFE_COLOR.match(value):
            props.append("%s:%s" % (key, value))
        elif key == "font-weight" and value.lower() in ("bold", "600", "700", "800", "900"):
            props.append("font-weight:bold")
        elif key == "font-style" and value.lower() == "italic":
            props.append("font-style:italic")
        elif key == "text-decoration" and "underline" in value.lower():
            props.append("text-decoration:underline")
    return ";".join(props)


class _ContentSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.capturing = False
        self.root_depth = 0
        self.skip_depth = 0
        self.segments: list[tuple[str, str]] = []
        self.text_buf: list[str] = []
        self.html_buf: list[str] = []
        self.tag_stack: list[str] = []
        self.block_open = False
        self.last_was_br = False

    # --- segment management ---
    def flush(self) -> None:
        while self.tag_stack:
            self.html_buf.append("</%s>" % self.tag_stack.pop())
        text = clean_text("".join(self.text_buf))
        fragment = "".join(self.html_buf).strip()
        if text and not (
            len(text) < 120 and any(marker in text for marker in NOISE_MARKERS)
        ):
            self.segments.append((text, fragment))
        self.text_buf = []
        self.html_buf = []
        self.tag_stack = []
        self.block_open = False
        self.last_was_br = False

    def _close_to(self, name: str) -> None:
        if name not in self.tag_stack:
            return
        while self.tag_stack:
            top = self.tag_stack.pop()
            self.html_buf.append("</%s>" % top)
            if top == name:
                break

    def _open_block(self, tag: str, attrs: dict) -> None:
        if self.block_open:
            self.flush()
        mapped = "p" if tag in ("li", "section") else tag
        style = sanitize_style(attrs.get("style"))
        attr = ' style="%s"' % style if style else ""
        self.html_buf.append("<%s%s>" % (mapped, attr))
        self.tag_stack.append(mapped)
        self.block_open = True
        self.last_was_br = False

    def _open_inline(self, tag: str, attrs: dict) -> None:
        mapped = INLINE_MAP[tag]
        style = sanitize_style(attrs.get("style"))
        attr = ' style="%s"' % style if style else ""
        self.html_buf.append("<%s%s>" % (mapped, attr))
        self.tag_stack.append(mapped)
        self.last_was_br = False

    # --- HTMLParser callbacks ---
    def handle_starttag(self, tag, attrs_list):
        tag = tag.lower()
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        if not self.capturing:
            if tag == "div" and attrs.get("id") == "js_content":
                self.capturing = True
                self.root_depth = 1
            return
        if self.skip_depth:
            if tag in SKIP_TAGS:
                self.skip_depth += 1
            return
        if tag in SKIP_TAGS:
            self.skip_depth = 1
            return
        if tag == "div":
            self.root_depth += 1
            return
        if tag == "br":
            if self.last_was_br:
                self.flush()
            else:
                self.html_buf.append("<br/>")
                self.text_buf.append("\n")
                self.last_was_br = True
            return
        if tag == "hr":
            self.flush()
            return
        if tag in BLOCK_TAGS:
            self._open_block(tag, attrs)
            return
        if tag in INLINE_MAP:
            self._open_inline(tag, attrs)
            return
        # a 等标签：保留文字，不保留链接与属性

    def handle_startendtag(self, tag, attrs_list):
        tag = tag.lower()
        if not self.capturing or self.skip_depth:
            return
        if tag == "br":
            if self.last_was_br:
                self.flush()
            else:
                self.html_buf.append("<br/>")
                self.text_buf.append("\n")
                self.last_was_br = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if not self.capturing:
            return
        if self.skip_depth:
            if tag in SKIP_TAGS:
                self.skip_depth -= 1
            return
        if tag == "div":
            self.root_depth -= 1
            if self.root_depth <= 0:
                self.flush()
                self.capturing = False
            return
        if tag in BLOCK_TAGS:
            mapped = "p" if tag in ("li", "section") else tag
            self._close_to(mapped)
            self.flush()
            return
        if tag in INLINE_MAP:
            self._close_to(INLINE_MAP[tag])

    def handle_data(self, data):
        if not self.capturing or self.skip_depth or not data:
            return
        self.text_buf.append(data)
        self.html_buf.append(html_mod.escape(data, quote=False))
        if data.strip():
            self.last_was_br = False


def classify_page(page_html: str) -> None:
    """先判页面类型，避免把验证页/删除页当正文。"""
    for marker in ("secitptpage/verify", "wappoc_appmsgcaptcha", "请在微信客户端打开链接"):
        if marker in page_html:
            raise RiskControlError("wechat_verify:%s" % marker)
    if len(page_html) < 200000:
        for marker in ("访问过于频繁", "操作过于频繁", "环境异常"):
            if marker in page_html:
                raise RiskControlError("wechat_rate_limit:%s" % marker)
        if "内容已删除" in page_html:
            raise ArticleUnavailableError("article_unavailable:内容已删除")
    for marker in ("该内容已被发布者删除", "此内容因违规无法查看", "该公众号已迁移"):
        if marker in page_html:
            raise ArticleUnavailableError("article_unavailable:%s" % marker)


def _pick(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else default


def _extract_title(page_html: str) -> str:
    title = _pick(r"var\s+msg_title\s*=\s*'([^']*)'", page_html)
    if not title:
        title = _pick(r'var\s+msg_title\s*=\s*"([^"]*)"', page_html)
    return html_mod.unescape(title).strip()


def _extract_author(page_html: str) -> str:
    author = _pick(r"var\s+author\s*=\s*'([^']*)'", page_html)
    if not author:
        author = _pick(r'var\s+author\s*=\s*"([^"]*)"', page_html)
    return html_mod.unescape(author).strip()


def _extract_title_date(title: str) -> str | None:
    match = re.search(r"(20\d{2})[.\-/年]\s*(\d{1,2})[.\-/月]\s*(\d{1,2})", title)
    if not match:
        return None
    try:
        return "%04d-%02d-%02d" % (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def parse_article(
    page_html: str,
    fallback_title: str = "",
    fallback_ts: int | None = None,
    source_url: str = "",
) -> ParsedArticle:
    classify_page(page_html)

    sanitizer = _ContentSanitizer()
    sanitizer.feed(page_html)
    sanitizer.close()
    sanitizer.flush()

    segments_raw = sanitizer.segments
    plain_text = "\n".join(text for text, _ in segments_raw)
    if len(re.sub(r"\s+", "", plain_text)) < 50:
        raise ParseError("empty_content")

    title = _extract_title(page_html) or fallback_title
    author = _extract_author(page_html) or None
    ct_raw = _pick(r"""var\s+ct\s*=\s*['"]?(\d{9,})['"]?""", page_html)
    try:
        publish_ts = int(ct_raw) if ct_raw else (int(fallback_ts) if fallback_ts else None)
    except (TypeError, ValueError):
        publish_ts = int(fallback_ts) if fallback_ts else None
    raw_time = _pick(r'id="publish_time"[^>]*>([^<]+)<', page_html).strip() or ct_raw or None

    segments: list[ArticleSegment] = []
    offset = 0
    for index, (text, fragment) in enumerate(segments_raw):
        start = offset
        end = start + len(text)
        segments.append(
            ArticleSegment(
                seq_no=index,
                plain_text=text,
                safe_html_fragment=fragment,
                char_start=start,
                char_end=end,
                segment_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
        offset = end + 1

    safe_html = "\n".join(fragment for _, fragment in segments_raw)
    text_hash = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()

    return ParsedArticle(
        title=title,
        page_author=author,
        publish_ts=publish_ts,
        publish_time_raw=raw_time,
        publish_time_source="page_ct" if ct_raw else ("album_create_time" if fallback_ts else None),
        safe_html=safe_html,
        plain_text=plain_text,
        title_date=_extract_title_date(title),
        segments=segments,
        source_url=source_url,
        text_hash=text_hash,
    )
