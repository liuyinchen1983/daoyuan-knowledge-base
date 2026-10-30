# -*- coding: utf-8 -*-
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from daoyuan.core.config import load_config
from daoyuan.core.models import ListingItem, ParsedArticle
from daoyuan.export.service import (
    build_txt,
    export_docx,
    export_txt,
    load_export_articles,
    safe_filename,
)
from daoyuan.reader.service import ReaderService
from daoyuan.storage.db import initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)


def make_parsed(text):
    paragraphs = [p for p in text.split("\n") if p.strip()]
    safe = "".join("<p>%s</p>" % p for p in paragraphs)
    segments = []
    pos = 0
    for index, paragraph in enumerate(paragraphs):
        segments.append(
            type(
                "S",
                (),
                {
                    "seq_no": index,
                    "plain_text": paragraph,
                    "safe_html_fragment": "<p>%s</p>" % paragraph,
                    "char_start": pos,
                    "char_end": pos + len(paragraph),
                    "segment_hash": hashlib.sha256(
                        paragraph.encode("utf-8")
                    ).hexdigest(),
                },
            )()
        )
        pos += len(paragraph) + 1
    return ParsedArticle(
        title="见深初见 2026.10.05",
        page_author="测试作者",
        publish_ts=1793078970,
        publish_time_raw="1793078970",
        publish_time_source="page_ct",
        safe_html=safe,
        plain_text="\n".join(paragraphs),
        title_date="2026-10-05",
        segments=segments,
        source_url="https://mp.weixin.qq.com/s?__biz=x&mid=1&chksm=abc",
        text_hash=hashlib.sha256(
            "\n".join(paragraphs).encode("utf-8")
        ).hexdigest(),
    )


class SearchExportTests(unittest.TestCase):
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
        self.collection_id = CollectionRepository(conn).upsert(
            source_id, "album", "测试文集"
        )
        article_id, _ = ArticleRepository(conn).upsert_discovered(
            source_id,
            ListingItem(
                msgid="1",
                itemidx="0",
                title="见深初见 2026.10.05",
                url="https://mp.weixin.qq.com/s?__biz=x&mid=1&chksm=abc",
            ),
            self.collection_id,
            0,
        )
        ArticleRepository(conn).mark_ready(
            article_id, make_parsed("修行之路漫长。\n见深与天哥同行。")
        )
        conn.close()
        self.service = ReaderService(self.config)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_search_two_char_like_fallback(self):
        result = self.service.search("见深")
        self.assertEqual(result["mode"], "like")
        self.assertEqual(result["total_articles"], 1)
        hit = result["results"][0]
        self.assertIsNotNone(hit["segment_id"])
        self.assertIn("见深", hit["excerpt"])

    def test_search_long_query_and_no_result(self):
        result = self.service.search("修行之路")
        self.assertIn(result["mode"], ("fts", "like_fallback"))
        self.assertEqual(result["total_articles"], 1)
        empty = self.service.search("绝对不存在的词")
        self.assertEqual(empty["total_articles"], 0)
        self.assertEqual(empty["results"], [])

    def test_search_title_match(self):
        result = self.service.search("见深初见")
        self.assertEqual(result["total_articles"], 1)
        hit = result["results"][0]
        self.assertTrue(hit["matched_title"])
        self.assertIsNone(hit["segment_id"])

    def test_search_collection_filter(self):
        self.assertEqual(
            self.service.search("见深", self.collection_id)["total_articles"], 1
        )
        self.assertEqual(
            self.service.search("见深", self.collection_id + 999)["total_articles"],
            0,
        )

    def test_search_date_range(self):
        self.assertEqual(
            self.service.search(
                "见深", date_from="2026-10-01", date_to="2026-10-31"
            )["total_articles"],
            1,
        )
        self.assertEqual(
            self.service.search(
                "见深", date_from="2025-01-01", date_to="2025-12-31"
            )["total_articles"],
            0,
        )
        self.assertEqual(
            self.service.search("见深", date_from="2026-10-28")["total_articles"], 0
        )
        self.assertEqual(
            self.service.search("见深", date_to="2026-10-27")["total_articles"], 1
        )

    def test_txt_and_docx_export(self):
        articles = load_export_articles(self.service)
        self.assertEqual(len(articles), 1)
        text = build_txt(articles)
        self.assertIn("见深与天哥同行", text)
        self.assertIn("原文链接：https://mp.weixin.qq.com", text)
        self.assertIn("发布时间：", text)

        txt_path = Path(self.tmp.name) / "out" / "a.txt"
        export_txt(articles, txt_path)
        raw = txt_path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("见深与天哥同行", raw.decode("utf-8-sig"))

        docx_path = Path(self.tmp.name) / "out" / "a.docx"
        export_docx(articles, docx_path)
        with zipfile.ZipFile(docx_path) as zf:
            names = set(zf.namelist())
            self.assertIn("word/document.xml", names)
            xml = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("见深与天哥同行", xml)
        self.assertIn("<w:b/>", xml)
        self.assertIn("见深初见", xml)

    def test_safe_filename(self):
        self.assertEqual(safe_filename("a/b:c*?"), "a_b_c")


if __name__ == "__main__":
    unittest.main()
