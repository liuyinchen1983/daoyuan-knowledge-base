# -*- coding: utf-8 -*-
"""阅读端文库：章节号排序与上一篇/下一篇。"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from daoyuan.core.config import load_config
from daoyuan.core.models import ArticleSegment, ListingItem, ParsedArticle
from daoyuan.reader.service import ReaderService
from daoyuan.storage.db import initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)


def make_parsed(title: str, text: str = "正文内容。") -> ParsedArticle:
    segment = ArticleSegment(
        seq_no=0,
        plain_text=text,
        safe_html_fragment="<p>%s</p>" % text,
        char_start=0,
        char_end=len(text),
        segment_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    return ParsedArticle(
        title=title,
        page_author="测试作者",
        publish_ts=1793078970,
        publish_time_raw="2026-10-26 12:00",
        publish_time_source="page_ct",
        safe_html=segment.safe_html_fragment,
        plain_text=text,
        title_date="2026-10-26",
        segments=[segment],
        source_url="https://mp.weixin.qq.com/s?__biz=x&mid=1",
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


class ReaderLibraryOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config_path = root / "config.toml"
        config_path.write_text(
            '[app]\ndata_dir = "%s"\n' % (root / "runtime").as_posix(),
            encoding="utf-8",
        )
        self.config = load_config(config_path)
        self.config.paths.ensure()
        conn = initialize_content_db(self.config.paths.content_db)
        source_id = SourceRepository(conn).upsert("biz", "示例公众号")
        collection_id = CollectionRepository(conn).upsert(
            source_id, "album", "测试传"
        )
        repository = ArticleRepository(conn)
        self.ids = {}
        titles = [
            ("02 第二段", 0),
            ("01 第一段", 1),
            ("52 第五十二段 2026.07.18", 2),
            (chr(0x200B) + "03 第三段", 3),
            ("没有章节号的文章", 4),
        ]
        for index, (title, serial_no) in enumerate(titles):
            article_id, _ = repository.upsert_discovered(
                source_id,
                ListingItem(
                    msgid=str(index + 1),
                    itemidx="0",
                    title=title,
                    url="https://mp.weixin.qq.com/s?__biz=x&mid=%d" % (index + 1),
                ),
                collection_id,
                serial_no,
            )
            repository.mark_ready(article_id, make_parsed(title))
            self.ids[title] = article_id
        conn.close()
        self.collection_id = collection_id
        self.service = ReaderService(self.config)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_library_sorts_by_chapter_number(self):
        rows = self.service.articles(self.collection_id)
        self.assertEqual(
            [row["chapter"] for row in rows],
            ["1", "2", "3", "52", ""],
        )
        self.assertEqual(rows[0]["display_title"], "第一段")
        self.assertEqual(rows[3]["display_title"], "第五十二段")
        self.assertEqual(
            [row["title"] for row in rows[:4]],
            [
                "01 第一段",
                "02 第二段",
                chr(0x200B) + "03 第三段",
                "52 第五十二段 2026.07.18",
            ],
        )

    def test_neighbors_follow_chapter_order(self):
        article_id = self.ids["02 第二段"]
        neighbors = self.service.neighbors(article_id)
        self.assertEqual(neighbors["prev"]["id"], self.ids["01 第一段"])
        self.assertEqual(
            neighbors["next"]["id"], self.ids[chr(0x200B) + "03 第三段"]
        )
        last = self.service.neighbors(self.ids["52 第五十二段 2026.07.18"])
        self.assertEqual(
            last["prev"]["id"], self.ids[chr(0x200B) + "03 第三段"]
        )
        self.assertEqual(
            last["next"]["id"], self.ids["没有章节号的文章"]
        )


if __name__ == "__main__":
    unittest.main()
