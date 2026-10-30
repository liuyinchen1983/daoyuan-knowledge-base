# -*- coding: utf-8 -*-
"""删除文章：内容/FTS/专题/个人状态清理，以及采集防复活。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from daoyuan.collector.manual import import_fetched_article
from daoyuan.collector.service import CollectService
from daoyuan.core.config import load_config
from daoyuan.core.models import ArticleSegment, ListingItem, ParsedArticle
from daoyuan.reader.service import ReaderService
from daoyuan.storage.db import connect, initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)
from daoyuan.topics import extractor
from daoyuan.topics.store import TopicStore


def make_parsed(title: str, text: str, msgid: str) -> ParsedArticle:
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
        source_url="https://mp.weixin.qq.com/s?__biz=x&mid=%s" % msgid,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


class DeletedArticleTests(unittest.TestCase):
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
            source_id, "album", "测试传"
        )
        repo = ArticleRepository(conn)
        self.keep_id, _ = repo.upsert_discovered(
            source_id,
            ListingItem(
                msgid="100",
                itemidx="0",
                title="第一章 正文",
                url="https://mp.weixin.qq.com/s?__biz=x&mid=100",
            ),
            self.collection_id,
            0,
        )
        repo.mark_ready(
            self.keep_id, make_parsed("第一章 正文", "测试传正文，保留这一篇。", "100")
        )
        self.delete_id, _ = repo.upsert_discovered(
            source_id,
            ListingItem(
                msgid="200",
                itemidx="0",
                title="所有顿悟 2024.09.06",
                url="https://mp.weixin.qq.com/s?__biz=x&mid=200",
            ),
            self.collection_id,
            1,
        )
        repo.mark_ready(
            self.delete_id,
            make_parsed("误采文章 2024.09.06", "误采文章正文，不属于测试传。", "200"),
        )
        self.source_id = source_id
        self.segment_id = conn.execute(
            "SELECT id FROM article_segments WHERE article_id=?",
            (self.delete_id,),
        ).fetchone()[0]
        conn.close()

        store = TopicStore.open(self.config.paths.topics_db)
        store.upsert_candidate(
            self.collection_id,
            "person",
            "误采人物",
            mentions=[
                extractor.Mention(
                    article_id=self.delete_id,
                    segment_id=self.segment_id,
                    surface="误采人物",
                    quote="误采文章正文",
                )
            ],
        )
        store.set_scan_state(self.collection_id, self.delete_id, "hash", 9)
        store.close()

        self.service = ReaderService(self.config)
        self.service.save_progress(self.delete_id, None, 0, 0.5)
        self.service.state.add_bookmark(self.delete_id, None, "待核对")
        self.service.save_progress(self.keep_id, None, 0, 0.2)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_delete_cleans_content_search_topics_and_state(self):
        result = self.service.delete_article(self.delete_id)

        self.assertEqual(result["warnings"], [])
        self.assertIsNone(self.service.article(self.delete_id))
        self.assertIsNotNone(self.service.article(self.keep_id))
        self.assertEqual(
            [row["id"] for row in self.service.articles(self.collection_id)],
            [self.keep_id],
        )
        self.assertEqual(self.service.search("误采文章")["total_articles"], 0)
        self.assertEqual(self.service.search("测试传正文")["total_articles"], 1)

        snapshot = Path(result["snapshot_path"])
        self.assertTrue(snapshot.exists())
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        self.assertEqual(payload["article"]["id"], self.delete_id)
        self.assertIn("误采文章正文", payload["article"]["text_content"])
        self.assertEqual(len(payload["segments"]), 1)

        conn = connect(self.config.paths.content_db, readonly=True)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM article_segments WHERE article_id=?",
                    (self.delete_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM articles_fts"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM collection_listings WHERE article_id=?",
                    (self.delete_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

        store = TopicStore.open(self.config.paths.topics_db, readonly=True)
        try:
            self.assertEqual(
                store.conn.execute(
                    "SELECT count(*) FROM topic_mentions WHERE article_id=?",
                    (self.delete_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                store.conn.execute(
                    "SELECT count(*) FROM topic_article_scan WHERE article_id=?",
                    (self.delete_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                store.conn.execute(
                    "SELECT count(*) FROM topic_entities WHERE name=?",
                    ("误采人物",),
                ).fetchone()[0],
                0,
            )
        finally:
            store.close()

        self.assertIsNone(self.service.state.get_progress(self.delete_id))
        self.assertIsNotNone(self.service.state.get_progress(self.keep_id))
        self.assertEqual(self.service.state.list_bookmarks(self.delete_id), [])

    def test_collector_skips_tombstoned_article(self):
        self.service.delete_article(self.delete_id)
        conn = connect(self.config.paths.content_db)
        try:
            collector = CollectService(self.config, conn)
            collection = collector.collections.get(self.collection_id)
            collector._save_listing(
                collection,
                [
                    ListingItem(
                        msgid="200",
                        itemidx="0",
                        title="所有顿悟 2024.09.06",
                        url="https://mp.weixin.qq.com/s?__biz=x&mid=200",
                    )
                ],
                {},
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM articles WHERE msgid='200'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM collection_listings WHERE msgid='200'"
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

    def test_manual_reimport_clears_tombstone(self):
        self.service.delete_article(self.delete_id)
        conn = connect(self.config.paths.content_db)
        try:
            import_fetched_article(
                conn,
                self.collection_id,
                make_parsed(
                    "所有顿悟 2024.09.06",
                    "误采文章正文，重新人工收录。",
                    "200",
                ),
                msgid="200",
                itemidx="0",
            )
            self.assertEqual(
                ArticleRepository(conn).deleted_keys(self.collection_id), set()
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM articles WHERE msgid='200'"
                ).fetchone()[0],
                1,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
