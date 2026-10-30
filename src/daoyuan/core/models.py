# -*- coding: utf-8 -*-
"""领域模型（轻量 dataclass，供各层通过 DTO 交换数据）。"""
from __future__ import annotations

from dataclasses import dataclass, field

STATUS_PENDING = "pending"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
STATUS_UNAVAILABLE = "unavailable"

SORT_NEWEST_FIRST = "newest_first"
SORT_OLDEST_FIRST = "oldest_first"
SORT_UNKNOWN = "unknown"


@dataclass
class Source:
    id: int | None = None
    biz: str | None = None
    account_name: str = ""
    account_id: str | None = None
    source_type: str = "wechat_mp"
    verified_state: str = "unverified"
    last_checked_at: str | None = None


@dataclass
class Collection:
    id: int | None = None
    source_id: int | None = None
    album_id: str = ""
    title: str = ""
    description: str | None = None
    sort_mode: str = SORT_UNKNOWN
    expected_count: int | None = None
    is_updating: bool | None = None
    enabled: bool = True
    last_checked_at: str | None = None


@dataclass
class ListingItem:
    msgid: str
    itemidx: str
    title: str = ""
    url: str = ""
    create_time: int | None = None
    is_deleted: bool = False


@dataclass
class Article:
    id: int | None = None
    source_id: int | None = None
    msgid: str = ""
    itemidx: str = ""
    title: str = ""
    page_author: str | None = None
    publish_ts: int | None = None
    publish_time_raw: str | None = None
    publish_time_source: str | None = None
    title_date: str | None = None
    source_url: str = ""
    text_content: str = ""
    safe_html: str = ""
    text_hash: str | None = None
    status: str = STATUS_PENDING
    first_collected_at: str | None = None
    last_checked_at: str | None = None
    last_error: str | None = None


@dataclass
class ArticleSegment:
    id: int | None = None
    article_id: int | None = None
    seq_no: int = 0
    plain_text: str = ""
    safe_html_fragment: str = ""
    char_start: int = 0
    char_end: int = 0
    segment_hash: str = ""


@dataclass
class ParsedArticle:
    title: str
    page_author: str | None
    publish_ts: int | None
    publish_time_raw: str | None
    publish_time_source: str | None
    safe_html: str
    plain_text: str
    title_date: str | None = None
    segments: list[ArticleSegment] = field(default_factory=list)
    source_url: str = ""
    text_hash: str = ""


@dataclass
class CollectionStats:
    collection_id: int
    title: str
    discovered: int = 0
    ready: int = 0
    pending: int = 0
    failed: int = 0
    unavailable: int = 0
    last_checked_at: str | None = None
