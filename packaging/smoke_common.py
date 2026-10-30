# -*- coding: utf-8 -*-
"""打包回归用的合成数据。内容全部虚构，不读取真实文章或用户数据。"""
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

from daoyuan.core.config import Config, load_config
from daoyuan.core.models import ArticleSegment, ListingItem, ParsedArticle
from daoyuan.storage.db import initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)

TEST_TITLE = "打包回归测试文章"
TEST_TEXT = (
    "修行之路漫长。林见深说：“天哥，X7 Future 的机甲准备好了。”\n"
    "顾清川问道：“机甲舱在哪？”他们走进云栖老街。\n"
    "林见深说：“天哥，跟上。”林见深说：“顾清川，走。”"
    "林见深说：“去老街。”林见深说：“好。”"
)


def result_path(default_name: str) -> Path:
    value = os.environ.get("DAOYUAN_SMOKE_OUT", "").strip()
    base = Path(
        os.environ.get("DAOYUAN_SMOKE_BASE")
        or (Path(tempfile.gettempdir()) / "daoyuan_package_smoke")
    )
    path = Path(value) if value else base / default_name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def prepare_runtime(prefix: str) -> tuple[Config, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = result_path(prefix + "_result.json").parent
    root = base / ("%s_data_%s" % (prefix, stamp))
    config_path = root / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '[app]\ndata_dir = "%s"\n' % root.as_posix(), encoding="utf-8"
    )
    config = load_config(config_path)
    config.paths.ensure()
    return config, root


def make_parsed() -> ParsedArticle:
    paragraphs = [line for line in TEST_TEXT.split("\n") if line.strip()]
    segments = []
    position = 0
    for index, paragraph in enumerate(paragraphs):
        segments.append(
            ArticleSegment(
                seq_no=index,
                plain_text=paragraph,
                safe_html_fragment="<p>%s</p>" % paragraph,
                char_start=position,
                char_end=position + len(paragraph),
                segment_hash=hashlib.sha256(paragraph.encode("utf-8")).hexdigest(),
            )
        )
        position += len(paragraph) + 1
    return ParsedArticle(
        title=TEST_TITLE,
        page_author="测试作者",
        publish_ts=1793078970,
        publish_time_raw="1793078970",
        publish_time_source="page_ct",
        safe_html="".join(segment.safe_html_fragment for segment in segments),
        plain_text="\n".join(paragraphs),
        title_date="2026-10-30",
        segments=segments,
        source_url="https://mp.weixin.qq.com/s?__biz=package-smoke&mid=1&chksm=test",
        text_hash=hashlib.sha256(TEST_TEXT.encode("utf-8")).hexdigest(),
    )


def seed(config: Config) -> int:
    conn = initialize_content_db(config.paths.content_db)
    try:
        source_id = SourceRepository(conn).upsert(
            "package-smoke", "示例公众号（合成测试）", verified_state="verified"
        )
        collection_id = CollectionRepository(conn).upsert(
            source_id, "package-smoke-album", "打包回归测试文集"
        )
        article_id, _ = ArticleRepository(conn).upsert_discovered(
            source_id,
            ListingItem(
                msgid="package-smoke-1",
                itemidx="0",
                title=TEST_TITLE,
                url="https://mp.weixin.qq.com/s?__biz=package-smoke&mid=1&chksm=test",
            ),
            collection_id,
            0,
        )
        ArticleRepository(conn).mark_ready(article_id, make_parsed())
        return int(article_id)
    finally:
        conn.close()
