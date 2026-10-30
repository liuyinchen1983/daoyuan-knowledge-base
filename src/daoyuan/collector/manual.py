# -*- coding: utf-8 -*-
"""人工粘贴文章入库。

不发起任何网络请求。正文、段落、全文索引和专题库都复用自动采集的
ParsedArticle / ArticleRepository.mark_ready 链路，避免手工导入成为
另一套不受检索和备份保护的数据。
"""
from __future__ import annotations

import difflib
import hashlib
import html
import json
import logging
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta

from ..core.models import ArticleSegment, ListingItem, ParsedArticle
from ..core.text import (
    chapter_number,
    clean_title as _clean_title,
    strip_chapter_prefix,
)
from ..core.timeutil import BEIJING, now, now_iso
from ..storage.repositories import ArticleRepository

MAX_MANUAL_CHARS = 2_000_000
MIN_MANUAL_CHARS = 10
MAX_TITLE_CHARS = 200

# 手动导入历史存在内容库的 meta 表里，不需要新增表，也会随内容库备份。
MANUAL_HISTORY_KEY = "manual.import_history"
MANUAL_HISTORY_LIMIT = 50
MANUAL_HISTORY_FIELDS = (
    "article_id",
    "collection_id",
    "collection_title",
    "title",
    "chapter",
    "publish_date",
    "imported_at",
    "status",
    "matched_by",
    "previous_status",
    "segments",
    "chars",
    "source_url",
    "origin",
)


def _clean_text(raw: str) -> str:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("﻿", "").replace("​", "").replace("\xa0", " ")
    return text.strip()


def _paragraphs(text: str) -> list[str]:
    """把粘贴正文拆成段落。

    有空白行时按空白行分段，段内软换行合并为一个空格；没有空白行时
    每个非空行作为一段，方便直接粘贴微信里逐行复制的对话。
    """
    if re.search(r"\n[ \t]*\n", text):
        blocks = re.split(r"\n[ \t]*\n+", text)
        result = []
        for block in blocks:
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            if lines:
                result.append(" ".join(lines))
        return result
    return [line.strip() for line in text.split("\n") if line.strip()]


def _title_date(title: str) -> str | None:
    match = re.search(
        r"(?<!\d)(\d{4})[./\-年](\d{1,2})[./\-月](\d{1,2})日?\s*$",
        _clean_title(title),
    )
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


_TRAILING_DATE_RE = re.compile(
    r"\s*(?<!\d)\d{4}[./\-年]\d{1,2}[./\-月]\d{1,2}日?\s*$"
)


def normalize_chapter(value: str) -> str:
    """把“第87章”“87”等输入规范成纯数字；空输入返回空字符串。"""
    text = (value or "").strip()
    if not text:
        return ""
    match = re.search(r"\d{1,4}", text)
    if not match:
        raise ValueError("章节号应为数字，例如 87。")
    return str(int(match.group(0)))


def recent_manual_imports(
    conn: sqlite3.Connection, limit: int = 12
) -> list[dict]:
    """读取最近的手动导入记录，最新的排在最前面。

    历史保存在内容库 ``meta`` 表的 ``manual.import_history`` 键里，记录的是
    每一次成功导入（包括补全自动采集清单里已有的文章），因此不会像旧版
    “只查 msgid LIKE 'manual:%'” 那样漏掉更新类导入。
    """
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key=?", (MANUAL_HISTORY_KEY,)
        ).fetchone()
    except sqlite3.Error:
        return []
    if row is None or not row[0]:
        return []
    try:
        items = json.loads(row[0])
    except (TypeError, ValueError):
        logging.getLogger("daoyuan.manual").warning(
            "手动导入历史不是有效 JSON，已忽略", exc_info=True
        )
        return []
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if isinstance(item, dict):
            result.append(dict(item))
        if len(result) >= max(1, int(limit)):
            break
    return result


def _record_manual_import(
    conn: sqlite3.Connection, payload: dict
) -> dict:
    """把手动导入结果追加到历史，保留最近 ``MANUAL_HISTORY_LIMIT`` 条。"""
    record = {key: payload.get(key) for key in MANUAL_HISTORY_FIELDS}
    record["imported_at"] = payload.get("imported_at") or now_iso()
    history = [record]
    history.extend(recent_manual_imports(conn, MANUAL_HISTORY_LIMIT - 1))
    try:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (MANUAL_HISTORY_KEY, json.dumps(history, ensure_ascii=False)),
        )
        conn.commit()
    except Exception:
        # 历史只是提示，写失败不能影响文章入库。
        logging.getLogger("daoyuan.manual").exception("记录手动导入历史失败")
    return record


def _title_body(title: str) -> str:
    """去掉章节号和末尾日期后用于比较的标题主体。"""
    text = strip_chapter_prefix(_TRAILING_DATE_RE.sub("", _clean_title(title)))
    return re.sub(r"[\s　]+", "", text)


def with_chapter_prefix(title: str, chapter_key: str) -> str:
    """确保标题开头带章节号；标题已有不同章节号时拒绝覆盖。"""
    if not chapter_key:
        return title
    existing = chapter_number(title)
    if existing:
        if existing != chapter_key:
            raise ValueError(
                "标题开头是第 %s 章，与填写的第 %s 章不一致；请核对后再导入。"
                % (existing, chapter_key)
            )
        return title
    return "%s %s" % (chapter_key, title)


def _title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left.startswith(right) or right.startswith(left):
        return min(len(left), len(right)) / max(len(left), len(right))
    return difflib.SequenceMatcher(None, left, right).ratio()


def _match_by_chapter(
    conn: sqlite3.Connection,
    collection_id: int,
    parsed: ParsedArticle,
    chapter_no: str,
):
    """在同一文集内按章节号找原文章。

    返回 (文章行或 None, 章节号)。用户明确填写章节号时优先采用；
    自动识别时要求标题主体有一定相似度，避免把不同文章合并。
    """
    key = normalize_chapter(chapter_no) if chapter_no else chapter_number(parsed.title)
    if not key:
        return None, ""
    rows = conn.execute(
        "SELECT a.* FROM articles a "
        "JOIN article_collections ac ON ac.article_id=a.id "
        "WHERE ac.collection_id=? ORDER BY ac.serial_no ASC",
        (int(collection_id),),
    ).fetchall()
    candidates = [row for row in rows if chapter_number(row["title"]) == key]
    if not candidates:
        return None, key
    manual_body = _title_body(parsed.title)
    scored = sorted(
        (
            (_title_similarity(manual_body, _title_body(row["title"])), row)
            for row in candidates
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best = scored[0]
    if len(scored) > 1 and best_score - scored[1][0] < 0.15:
        raise ValueError(
            "同一文集里有多篇第 %s 章，无法自动判断；请填写章节号或粘贴原文链接。"
            % key
        )
    if best_score < 0.5 and not chapter_no:
        return None, key
    return best, key


_DATE_INPUT_RE = re.compile(
    r"^(?:(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
    r"|(\d{1,2})[-/.](\d{1,2}))"
    r"(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?$"
)


def _normalize_date_input(value: str) -> str:
    """把常见中文/微信日期写法统一成 2026-10-30 07:30 的形式。"""
    text = (value or "").strip()
    text = text.translate(
        str.maketrans("０１２３４５６７８９：．－／～", "0123456789:.-/~")
    )
    text = re.sub(
        r"^(?:发布(?:时间)?|发表(?:时间)?|更新(?:时间)?|时间)\s*[:：]?\s*", "", text
    )
    text = re.sub(r"(?:Z|[+-]\d{2}:?\d{2})$", "", text, flags=re.IGNORECASE)
    text = text.replace("年", "-").replace("月", "-").replace("日", " ")
    text = (
        text.replace("时", ":")
        .replace("点", ":")
        .replace("分", ":")
        .replace("秒", "")
    )
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"[:.\-]+$", "", text).strip()


def parse_publish_date(value: str) -> tuple[int, str, str]:
    """解析手工填写的发布时间；空值使用当前时间（北京时间）。

    支持 ``2026-10-30``、``2026.10.30``、``2026/10/30``、
    ``2026年10月30日``、``2026-10-30 17:30``、``2026年10月30日 17:30``
    等写法；没有年份时按当前年份推断，若因此落到未来则回退一年。
    返回值中的原始文本会统一成 ``YYYY-MM-DD`` 或 ``YYYY-MM-DD HH:MM``。
    """
    text = _normalize_date_input(value)
    if not text:
        current = now()
        return int(current.timestamp()), current.strftime("%Y-%m-%d %H:%M"), "manual_now"

    match = _DATE_INPUT_RE.match(text)
    if not match:
        raise ValueError(
            "发布日期无法识别，可用 2026-10-30、2026.10.30、2026年10月30日 "
            "或 2026-10-30 17:30。"
        )
    if match.group(1):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
    else:
        year = now().year
        month = int(match.group(4))
        day = int(match.group(5))
    hour = int(match.group(6) or 0)
    minute = int(match.group(7) or 0)
    second = int(match.group(8) or 0)
    try:
        parsed = datetime(year, month, day, hour, minute, second, tzinfo=BEIJING)
        if not match.group(1) and parsed > now() + timedelta(days=1):
            parsed = parsed.replace(year=year - 1)
    except ValueError as exc:
        raise ValueError("发布日期无效：%s" % value.strip()) from exc

    canonical = (
        parsed.strftime("%Y-%m-%d %H:%M")
        if match.group(6)
        else parsed.strftime("%Y-%m-%d")
    )
    return int(parsed.timestamp()), canonical, "manual"


def build_parsed_article(
    title: str,
    content: str,
    *,
    author: str = "",
    source_url: str = "",
    publish_date: str = "",
) -> ParsedArticle:
    title = _clean_title(title)
    if not title:
        raise ValueError("标题不能为空。")
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError("标题不能超过 %d 个字符。" % MAX_TITLE_CHARS)

    text = _clean_text(content)
    if len(text) < MIN_MANUAL_CHARS:
        raise ValueError("正文太短，至少需要 %d 个字符。" % MIN_MANUAL_CHARS)
    if len(text) > MAX_MANUAL_CHARS:
        raise ValueError("正文超过 %d 个字符，请分篇导入。" % MAX_MANUAL_CHARS)

    url = (source_url or "").strip()
    if url and not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise ValueError("原文链接必须以 http:// 或 https:// 开头。")

    paragraphs = _paragraphs(text)
    if not paragraphs:
        raise ValueError("正文没有可用段落。")

    segments: list[ArticleSegment] = []
    offset = 0
    for index, paragraph in enumerate(paragraphs):
        start = offset
        end = start + len(paragraph)
        segments.append(
            ArticleSegment(
                seq_no=index,
                plain_text=paragraph,
                safe_html_fragment="<p>%s</p>" % html.escape(paragraph, quote=False),
                char_start=start,
                char_end=end,
                segment_hash=hashlib.sha256(paragraph.encode("utf-8")).hexdigest(),
            )
        )
        offset = end + 1

    plain_text = "\n".join(paragraphs)
    title_date = _title_date(title)
    if not (publish_date or "").strip() and title_date:
        # 公众号标题通常以日期结尾；留空日期时用它，避免旧文被排成“今天”。
        parsed_date = datetime.strptime(title_date, "%Y-%m-%d").replace(tzinfo=BEIJING)
        publish_ts = int(parsed_date.timestamp())
        publish_raw = title_date
        publish_source = "manual_title_date"
    else:
        publish_ts, publish_raw, publish_source = parse_publish_date(publish_date)
    return ParsedArticle(
        title=title,
        page_author=(author or "").strip() or None,
        publish_ts=publish_ts,
        publish_time_raw=publish_raw,
        publish_time_source=publish_source,
        safe_html="\n".join(segment.safe_html_fragment for segment in segments),
        plain_text=plain_text,
        title_date=title_date,
        segments=segments,
        source_url=url,
        text_hash=hashlib.sha256(plain_text.encode("utf-8")).hexdigest(),
    )


def _find_existing(
    conn: sqlite3.Connection,
    source_id: int,
    collection_id: int,
    parsed: ParsedArticle,
    *,
    msgid: str = "",
    itemidx: str = "",
):
    """精确查找已存在文章，返回 (行, 匹配方式)。"""
    if parsed.source_url:
        row = conn.execute(
            "SELECT * FROM articles WHERE source_id=? AND source_url=? "
            "ORDER BY id LIMIT 1",
            (source_id, parsed.source_url),
        ).fetchone()
        if row is not None:
            return row, "url"
    # 单篇链接采集知道真实 msgid/itemidx；即使链接参数与清单里保存的
    # 地址略有差异，也先按微信文章主键命中，避免同一篇被重复入库。
    if msgid:
        row = conn.execute(
            "SELECT * FROM articles WHERE source_id=? AND msgid=? AND itemidx=? "
            "ORDER BY id LIMIT 1",
            (source_id, str(msgid), str(itemidx or "0")),
        ).fetchone()
        if row is not None:
            return row, "msgid"
    row = conn.execute(
        "SELECT a.* FROM articles a "
        "JOIN article_collections ac ON ac.article_id=a.id "
        "WHERE ac.collection_id=? AND a.title=? AND a.text_hash=? "
        "ORDER BY a.id LIMIT 1",
        (collection_id, parsed.title, parsed.text_hash),
    ).fetchone()
    return (row, "title") if row is not None else (None, "")


def _link_collection(conn: sqlite3.Connection, article_id: int, collection_id: int) -> None:
    ts = now_iso()
    serial = conn.execute(
        "SELECT COALESCE(MAX(serial_no), -1) + 1 FROM article_collections "
        "WHERE collection_id=?",
        (collection_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO article_collections(article_id, collection_id, serial_no, is_current, "
        "first_seen_at, last_seen_at) VALUES (?,?,?,1,?,?) "
        "ON CONFLICT(article_id, collection_id) DO UPDATE SET "
        "is_current=1, last_seen_at=excluded.last_seen_at",
        (article_id, collection_id, int(serial or 0), ts, ts),
    )
    conn.commit()


def _collection_info(conn: sqlite3.Connection, collection_id: int):
    row = conn.execute(
        "SELECT id, source_id, title FROM collections WHERE id=?",
        (int(collection_id),),
    ).fetchone()
    if row is None:
        raise ValueError("文集不存在，请先在“来源与文集”中登记。")
    return row


def _import_parsed_article(
    conn: sqlite3.Connection,
    collection_id: int,
    parsed: ParsedArticle,
    *,
    chapter_no: str = "",
    msgid: str = "",
    itemidx: str = "",
    origin: str = "manual",
) -> dict:
    """把已经构造好的 ParsedArticle 入库。

    人工粘贴和单篇链接采集共用这一段：匹配顺序为原文链接 → 真实
    msgid/itemidx → 标题+正文哈希 → 同一文集内的章节号。链接采集使用
    微信真实 msgid，后续合集清单再次扫到同一篇时不会重复创建。
    """
    row = _collection_info(conn, collection_id)
    collection_title = row["title"] or ""
    source_id = int(row["source_id"])
    msgid_value = str(msgid or "").strip()
    itemidx_value = str(itemidx or "0").strip() or "0"
    if msgid_value and not msgid_value.isdigit():
        raise ValueError("文章 msgid 应为数字，无法识别链接中的文章主键。")

    repo = ArticleRepository(conn)
    existing, matched_by = _find_existing(
        conn,
        source_id,
        int(collection_id),
        parsed,
        msgid=msgid_value,
        itemidx=itemidx_value,
    )
    chapter_key = normalize_chapter(chapter_no) if chapter_no else chapter_number(parsed.title)
    if existing is None and not msgid_value:
        # 链接采集已经拿到微信真实 msgid；按章节号再合并可能把两篇不同
        # 文章误判为同一篇，所以只在没有真实 msgid 时才走章节匹配。
        existing, chapter_key = _match_by_chapter(
            conn, int(collection_id), parsed, chapter_no
        )
        if existing is not None:
            matched_by = "chapter"
    elif existing is not None and not chapter_key:
        # 原文链接/标题匹配时，沿用原文章标题里的章节号，避免更新后丢失编号。
        chapter_key = chapter_number(existing["title"])

    previous_status = existing["status"] if existing is not None else ""
    if existing is not None:
        article_id = int(existing["id"])
        # 这篇文章已经存在，说明用户这次是主动重新收录，解除旧墓碑。
        repo.clear_deleted(
            source_id, str(existing["msgid"]), str(existing["itemidx"]), int(collection_id)
        )
        # 标题主体相同（忽略章节号和末尾日期）时保留原清单标题，
        # 避免更新正文时丢掉日期或章节号；未手填日期时沿用原发布时间。
        existing_title = existing["title"] or ""
        if existing_title and _title_body(existing_title) == _title_body(parsed.title):
            parsed = replace(
                parsed,
                title=existing_title,
                title_date=_title_date(existing_title) or parsed.title_date,
            )
        if parsed.publish_time_source != "manual" and existing["publish_ts"]:
            preserved_raw = existing["publish_time_raw"] or _title_date(existing_title)
            parsed = replace(
                parsed,
                publish_ts=int(existing["publish_ts"]),
                publish_time_raw=preserved_raw or parsed.publish_time_raw,
                publish_time_source=existing["publish_time_source"]
                or parsed.publish_time_source,
            )
        if not parsed.page_author and existing["page_author"]:
            parsed = replace(parsed, page_author=existing["page_author"])
        if not parsed.source_url and existing["source_url"]:
            parsed = replace(parsed, source_url=existing["source_url"])
        if not chapter_key:
            chapter_key = chapter_number(existing_title)
        if chapter_key:
            existing_chapter = chapter_number(existing["title"])
            if existing_chapter and existing_chapter != chapter_key:
                raise ValueError(
                    "原文章标题是第 %s 章，与填写的第 %s 章不一致；请核对章节号或原文链接。"
                    % (existing_chapter, chapter_key)
                )
            parsed = replace(
                parsed, title=with_chapter_prefix(parsed.title, chapter_key)
            )
        _link_collection(conn, article_id, int(collection_id))
        result = repo.mark_ready(article_id, parsed)
    else:
        if chapter_key:
            parsed = replace(
                parsed, title=with_chapter_prefix(parsed.title, chapter_key)
            )
        if msgid_value:
            item_msgid = msgid_value
            item_itemidx = itemidx_value
        else:
            digest = hashlib.sha1(
                (parsed.source_url or (parsed.title + "\n" + parsed.text_hash)).encode("utf-8")
            ).hexdigest()[:20]
            item_msgid = "manual:%s" % digest
            item_itemidx = "0"
        serial = conn.execute(
            "SELECT COALESCE(MAX(serial_no), -1) + 1 FROM article_collections "
            "WHERE collection_id=?",
            (int(collection_id),),
        ).fetchone()[0]
        article_id, _ = repo.upsert_discovered(
            source_id,
            ListingItem(
                msgid=item_msgid,
                itemidx=item_itemidx,
                title=parsed.title,
                url=parsed.source_url,
                create_time=parsed.publish_ts,
            ),
            int(collection_id),
            int(serial or 0),
        )
        repo.clear_deleted(source_id, item_msgid, item_itemidx, int(collection_id))
        result = repo.mark_ready(article_id, parsed)

    serial_row = conn.execute(
        "SELECT serial_no FROM article_collections "
        "WHERE article_id=? AND collection_id=?",
        (article_id, int(collection_id)),
    ).fetchone()
    result_data = {
        "article_id": article_id,
        "status": result,
        "title": parsed.title,
        "collection_id": int(collection_id),
        "collection_title": collection_title,
        "segments": len(parsed.segments),
        "chars": len(parsed.plain_text),
        "publish_ts": parsed.publish_ts,
        "publish_date": parsed.publish_time_raw,
        "source_url": parsed.source_url,
        "manual": origin == "manual",
        "origin": origin,
        "serial_no": int(serial_row["serial_no"]) if serial_row else None,
        "chapter": chapter_key,
        "matched_by": matched_by,
        "previous_status": previous_status,
    }
    result_data["imported_at"] = now_iso()
    _record_manual_import(conn, result_data)
    return result_data


def import_manual_article(
    conn: sqlite3.Connection,
    collection_id: int,
    title: str,
    content: str,
    *,
    author: str = "",
    source_url: str = "",
    publish_date: str = "",
    chapter_no: str = "",
) -> dict:
    """导入手工粘贴正文，返回入库结果。

    优先级：原文链接 → 标题 + 正文哈希 → 同一文集内的章节号。
    章节号能命中自动采集清单里的待采/失败文章时直接补全该文章，
    不会另外新建一条重复记录。
    """
    _collection_info(conn, collection_id)
    parsed = build_parsed_article(
        title,
        content,
        author=author,
        source_url=source_url,
        publish_date=publish_date,
    )
    return _import_parsed_article(
        conn,
        collection_id,
        parsed,
        chapter_no=chapter_no,
        origin="manual",
    )


def import_fetched_article(
    conn: sqlite3.Connection,
    collection_id: int,
    parsed: ParsedArticle,
    *,
    msgid: str = "",
    itemidx: str = "",
) -> dict:
    """把单篇链接抓取并解析好的文章入库。"""
    _collection_info(conn, collection_id)
    title = (parsed.title or "").strip()
    if not title:
        raise ValueError("文章标题为空，可能不是微信文章页；已停止入库。")
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError("标题超过 %d 个字符，已停止入库。" % MAX_TITLE_CHARS)
    text = parsed.plain_text or ""
    if len(text) < MIN_MANUAL_CHARS:
        raise ValueError("正文太短，至少需要 %d 个字符。" % MIN_MANUAL_CHARS)
    if len(text) > MAX_MANUAL_CHARS:
        raise ValueError("正文超过 %d 个字符，请分篇导入。" % MAX_MANUAL_CHARS)
    return _import_parsed_article(
        conn,
        collection_id,
        parsed,
        msgid=msgid,
        itemidx=itemidx,
        origin="link",
    )
