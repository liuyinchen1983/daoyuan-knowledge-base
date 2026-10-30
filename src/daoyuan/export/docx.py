# -*- coding: utf-8 -*-
"""零依赖 DOCX 生成（OOXML），保留段落、加粗、斜体、下划线与强调色。"""
from __future__ import annotations

import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

COLOR_RE = re.compile(r"color\s*:\s*(#[0-9a-fA-F]{3,8})")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(text: str) -> str:
    return CONTROL_RE.sub("", text or "")


def _color_value(value: str) -> str | None:
    if not value.startswith("#"):
        return None
    raw = value[1:]
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) == 8:
        raw = raw[:6]
    if len(raw) != 6:
        return None
    return raw.upper()


def _run_xml(text: str, style: dict) -> str:
    text = _clean(text)
    if not text:
        return ""
    props = []
    if style.get("bold"):
        props.append("<w:b/>")
    if style.get("italic"):
        props.append("<w:i/>")
    if style.get("underline"):
        props.append('<w:u w:val="single"/>')
    color = _color_value(style.get("color") or "")
    if color:
        props.append('<w:color w:val="%s"/>' % color)
    props.append('<w:sz w:val="%d"/>' % int(style.get("size") or 24))
    rpr = "<w:rPr>%s</w:rPr>" % "".join(props)
    parts = text.split("\n")
    pieces = []
    for index, part in enumerate(parts):
        if index:
            pieces.append("<w:br/>")
        if part:
            pieces.append(
                '<w:t xml:space="preserve">%s</w:t>' % escape(part)
            )
    return "<w:r>%s%s</w:r>" % (rpr, "".join(pieces))


class _InlineParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.runs: list[tuple[str, dict]] = []
        self.bold = 0
        self.italic = 0
        self.underline = 0
        self.colors: list[str | None] = []
        self.heading = 0
        self.buffer: list[str] = []

    @property
    def style(self) -> dict:
        color = None
        for value in reversed(self.colors):
            if value:
                color = value
                break
        if self.heading:
            size = {1: 32, 2: 28, 3: 26}.get(self.heading, 24)
            return {
                "bold": True,
                "italic": self.italic > 0,
                "underline": self.underline > 0,
                "color": color,
                "size": size,
            }
        return {
            "bold": self.bold > 0,
            "italic": self.italic > 0,
            "underline": self.underline > 0,
            "color": color,
            "size": 24,
        }

    def flush(self) -> None:
        text = "".join(self.buffer)
        self.buffer = []
        if text:
            self.runs.append((text, self.style.copy()))

    def handle_starttag(self, tag, attrs_list):
        tag = tag.lower()
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        if tag in ("strong", "b"):
            self.flush()
            self.bold += 1
        elif tag in ("em", "i"):
            self.flush()
            self.italic += 1
        elif tag == "u":
            self.flush()
            self.underline += 1
        elif tag == "span":
            self.flush()
            match = COLOR_RE.search(attrs.get("style", ""))
            self.colors.append(_color_value(match.group(1)) if match else None)
        elif tag in ("h1", "h2", "h3"):
            self.flush()
            self.heading = int(tag[1])
        elif tag == "br":
            self.flush()
            self.runs.append(("\n", self.style.copy()))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("strong", "b"):
            self.flush()
            self.bold = max(0, self.bold - 1)
        elif tag in ("em", "i"):
            self.flush()
            self.italic = max(0, self.italic - 1)
        elif tag == "u":
            self.flush()
            self.underline = max(0, self.underline - 1)
        elif tag == "span":
            self.flush()
            if self.colors:
                self.colors.pop()
        elif tag in ("h1", "h2", "h3"):
            self.flush()
            self.heading = 0

    def handle_data(self, data):
        self.buffer.append(data)


def _paragraph_from_fragment(fragment: str, fallback_text: str) -> str:
    parser = _InlineParser()
    try:
        parser.feed(fragment or "")
        parser.close()
        parser.flush()
    except Exception:
        parser.runs = [(fallback_text, {"size": 24})]
    runs = parser.runs or [(fallback_text, {"size": 24})]
    if not any(text.strip() for text, _ in runs):
        return ""
    body = "".join(_run_xml(text, style) for text, style in runs)
    return (
        "<w:p><w:pPr><w:spacing w:after=\"160\" w:line=\"360\" "
        "w:lineRule=\"auto\"/></w:pPr>%s</w:p>" % body
    )


def _meta_paragraph(text: str) -> str:
    return (
        "<w:p><w:pPr><w:spacing w:after=\"40\"/></w:pPr>"
        "<w:r><w:rPr><w:color w:val=\"6E7B82\"/><w:sz w:val=\"18\"/></w:rPr>"
        "<w:t xml:space=\"preserve\">%s</w:t></w:r></w:p>" % escape(_clean(text))
    )


def _title_paragraph(text: str) -> str:
    return (
        "<w:p><w:pPr><w:jc w:val=\"center\"/><w:spacing w:after=\"200\"/></w:pPr>"
        "<w:r><w:rPr><w:b/><w:sz w:val=\"36\"/></w:rPr>"
        "<w:t xml:space=\"preserve\">%s</w:t></w:r></w:p>" % escape(_clean(text))
    )


def build_document_xml(articles: list[dict]) -> str:
    paragraphs: list[str] = []
    for index, article in enumerate(articles):
        if index:
            paragraphs.append(
                "<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>"
            )
        title = article.get("title") or "（无标题）"
        paragraphs.append(_title_paragraph(title))
        meta_parts = [
            "文集：%s" % (article.get("collection_title") or "未归类"),
            "作者：%s" % (article.get("page_author") or "未署名"),
            "发布时间：%s" % (article.get("publish_date") or "未知"),
            "来源：微信公众号",
        ]
        for part in meta_parts:
            paragraphs.append(_meta_paragraph(part))
        if article.get("source_url"):
            paragraphs.append(_meta_paragraph("原文链接：%s" % article["source_url"]))
        paragraphs.append(
            "<w:p><w:pPr><w:pBdr><w:bottom w:val=\"single\" w:sz=\"6\" "
            "w:space=\"1\" w:color=\"B49763\"/></w:pBdr><w:spacing w:after=\"160\"/>"
            "</w:pPr></w:p>"
        )
        segments = article.get("segments") or []
        if segments:
            for segment in segments:
                paragraph = _paragraph_from_fragment(
                    segment.get("safe_html_fragment") or "",
                    segment.get("plain_text") or "",
                )
                if paragraph:
                    paragraphs.append(paragraph)
        else:
            for line in (article.get("text_content") or "").split("\n"):
                if line.strip():
                    paragraphs.append(_paragraph_from_fragment("", line))
    body = "".join(paragraphs)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="%s"><w:body>%s'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>" % (W_NS, body)
    )


def write_docx(articles: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document_xml = build_document_xml(articles)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("word/document.xml", document_xml)
