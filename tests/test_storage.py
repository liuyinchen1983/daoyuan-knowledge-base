# -*- coding: utf-8 -*-
import hashlib
import tempfile
import unittest
from pathlib import Path

from daoyuan.core.models import ListingItem, ParsedArticle
from daoyuan.storage.db import health_report, initialize_content_db, schema_version
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)
from daoyuan.storage.state_db import StateStore


def make_parsed(text):
    paragraphs = [p for p in text.split("\n") if p.strip()]
    safe = "".join("<p>%s</p>" % p for p in paragraphs)
    segs = []
    pos = 0
    for i, p in enumerate(paragraphs):
        segs.append(
            type(
                "S",
                (),
                {
                    "seq_no": i,
                    "plain_text": p,
                    "safe_html_fragment": "<p>%s</p>" % p,
                    "char_start": pos,
                    "char_end": pos + len(p),
                    "segment_hash": hashlib.sha256(p.encode("utf-8")).hexdigest(),
                },
            )()
        )
        pos += len(p) + 1
    return ParsedArticle(
        title="测试文章",
        page_author="测试作者",
        publish_ts=1700000000,
        publish_time_raw="2023-11-15",
        publish_time_source="page_ct",
        safe_html=safe,
        plain_text="\n".join(paragraphs),
        segments=segs,
        source_url="https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=1&chksm=abc",
        text_hash=hashlib.sha256("\n".join(paragraphs).encode("utf-8")).hexdigest(),
    )


class ContentStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "content" / "knowledge.db"
        self.conn = initialize_content_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_schema_and_health(self):
        self.assertEqual(schema_version(self.conn), 2)
        tables = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        for name in (
            "sources",
            "collections",
            "articles",
            "article_collections",
            "article_segments",
            "article_revisions",
            "collection_listings",
            "collect_runs",
            "collect_items",
            "discover_candidates",
            "deleted_articles",
        ):
            self.assertIn(name, tables)
        report = health_report(self.conn)
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["articles"], 0)

    def test_fts_trigram_and_two_char_limitation(self):
        src = SourceRepository(self.conn).upsert("Yml6X3BsYWNlaG9sZGVy", "示例公众号")
        col = CollectionRepository(self.conn).upsert(src, "album1", "测试合集")
        repo = ArticleRepository(self.conn)
        item = ListingItem(msgid="100", itemidx="1", title="测试", url="https://example.com/a")
        article_id, is_new = repo.upsert_discovered(src, item, col, 0)
        self.assertTrue(is_new)
        parsed = make_parsed("修行之路漫长。\n见深与天哥同行。")
        self.assertEqual(repo.mark_ready(article_id, parsed), "created")

        n3 = self.conn.execute(
            "SELECT count(*) FROM articles_fts WHERE articles_fts MATCH ?", ("修行之路",)
        ).fetchone()[0]
        self.assertEqual(n3, 1)
        n2 = self.conn.execute(
            "SELECT count(*) FROM articles_fts WHERE articles_fts MATCH ?", ("见深",)
        ).fetchone()[0]
        self.assertEqual(n2, 0, "trigram 对 2 字不命中，必须由 LIKE 兜底")
        like = self.conn.execute(
            "SELECT count(*) FROM articles WHERE text_content LIKE ?", ("%见深%",)
        ).fetchone()[0]
        self.assertEqual(like, 1)

        nseg = self.conn.execute(
            "SELECT count(*) FROM segments_fts WHERE segments_fts MATCH ?", ("见深与天哥",)
        ).fetchone()[0]
        self.assertEqual(nseg, 1)

    def test_upsert_is_idempotent_and_marks_ready(self):
        src = SourceRepository(self.conn).upsert("biz", "号")
        col = CollectionRepository(self.conn).upsert(src, "album", "合集")
        repo = ArticleRepository(self.conn)
        item = ListingItem(msgid="1", itemidx="0", title="标题", url="https://e/x")
        first_id, first_new = repo.upsert_discovered(src, item, col, 0)
        second_id, second_new = repo.upsert_discovered(src, item, col, 1)
        self.assertEqual(first_id, second_id)
        self.assertTrue(first_new)
        self.assertFalse(second_new)
        self.assertEqual(repo.stats_for_collection(col)["pending"], 1)

        parsed = make_parsed("第一段。\n第二段。")
        self.assertEqual(repo.mark_ready(first_id, parsed), "created")
        self.assertEqual(repo.mark_ready(first_id, parsed), "unchanged")
        meta = make_parsed("第一段。\n第二段。")
        meta.publish_ts = 1800000000
        meta.publish_time_raw = "1800000000"
        meta.publish_time_source = "page_ct"
        meta.title_date = "2027-01-01"
        self.assertEqual(repo.mark_ready(first_id, meta), "unchanged")
        row = repo.get(first_id)
        self.assertEqual(row["publish_ts"], 1800000000)
        self.assertEqual(row["title_date"], "2027-01-01")
        stats = repo.stats_for_collection(col)
        self.assertEqual(stats["ready"], 1)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM article_segments").fetchone()[0], 2
        )

        changed = make_parsed("第一段。\n第二段改了。")
        self.assertEqual(repo.mark_ready(first_id, changed), "updated")
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM article_revisions").fetchone()[0], 1
        )


class StateStorageTests(unittest.TestCase):
    def test_progress_and_bookmarks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "user_state.db")
            try:
                store.save_progress(12, 3, 100, 0.25)
                self.assertEqual(store.get_progress(12)["percent"], 0.25)
                bid = store.add_bookmark(12, 3, "标记")
                self.assertEqual(len(store.list_bookmarks(12)), 1)
                store.delete_bookmark(bid)
                self.assertEqual(len(store.list_bookmarks(12)), 0)
                sid = store.create_qa_session("问题")
                store.add_qa_message(sid, "user", "你好", 0)
                store.add_qa_citation(1, "C1", 12, 3, "片段")
                self.assertEqual(len(store.list_qa_messages(sid)), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
