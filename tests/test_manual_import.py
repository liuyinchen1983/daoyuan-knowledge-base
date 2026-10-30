# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from daoyuan.collector.manual import (
    chapter_number,
    import_manual_article,
    parse_publish_date,
    recent_manual_imports,
)
from daoyuan.core.config import load_config
from daoyuan.core.models import ListingItem
from daoyuan.storage.db import initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)


class ManualImportTests(unittest.TestCase):
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
        self.conn = initialize_content_db(self.config.paths.content_db)
        self.source_id = SourceRepository(self.conn).upsert("biz", "示例公众号")
        self.collection_id = CollectionRepository(self.conn).upsert(
            self.source_id, "album", "测试文集"
        )

    def _seed_discovered(self, title, msgid, serial, publish_ts, url=""):
        article_id, _ = ArticleRepository(self.conn).upsert_discovered(
            self.source_id,
            ListingItem(
                msgid=msgid,
                itemidx="1",
                title=title,
                url=url or "https://mp.weixin.qq.com/s?__biz=x&mid=%s&chksm=y" % msgid,
                create_time=publish_ts,
            ),
            self.collection_id,
            serial,
        )
        ArticleRepository(self.conn).mark_failed(article_id, "restricted")
        return int(article_id)

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def test_import_creates_ready_searchable_article(self):
        result = import_manual_article(
            self.conn,
            self.collection_id,
            "第一章 修行之路 2026.10.30",
            "修行之路漫长。\n\n林见深说：“天哥，机甲准备好了。”",
            author="示例公众号",
            source_url="https://mp.weixin.qq.com/s/manual-test",
            publish_date="2026-10-30 17:30",
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["segments"], 2)
        article = self.conn.execute(
            "SELECT * FROM articles WHERE id=?", (result["article_id"],)
        ).fetchone()
        self.assertEqual(article["status"], "ready")
        self.assertEqual(article["title"], "第一章 修行之路 2026.10.30")
        self.assertEqual(article["title_date"], "2026-10-30")
        self.assertEqual(article["page_author"], "示例公众号")
        self.assertEqual(article["publish_time_source"], "manual")
        self.assertEqual(article["source_url"], "https://mp.weixin.qq.com/s/manual-test")
        self.assertIn("修行之路", article["text_content"])
        segments = self.conn.execute(
            "SELECT * FROM article_segments WHERE article_id=? ORDER BY seq_no",
            (result["article_id"],),
        ).fetchall()
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["char_start"], 0)
        self.assertIn("修行之路", segments[0]["plain_text"])
        self.assertTrue(segments[0]["safe_html_fragment"].startswith("<p>"))
        listing = self.conn.execute(
            "SELECT * FROM collection_listings WHERE article_id=?",
            (result["article_id"],),
        ).fetchone()
        self.assertIsNotNone(listing)
        fts = self.conn.execute(
            "SELECT rowid FROM segments_fts WHERE segments_fts MATCH ?",
            ('"修行之路"',),
        ).fetchall()
        self.assertEqual([int(row["rowid"]) for row in fts], [int(segments[0]["id"])])

    def test_same_url_updates_article_and_keeps_one_revision(self):
        first = import_manual_article(
            self.conn,
            self.collection_id,
            "标题",
            "第一版正文内容足够长。\n第二段内容。",
            source_url="https://mp.weixin.qq.com/s/same",
            publish_date="2026-10-30",
        )
        second = import_manual_article(
            self.conn,
            self.collection_id,
            "标题（修订）",
            "第二版正文内容足够长。\n新的第二段。",
            source_url="https://mp.weixin.qq.com/s/same",
            publish_date="2026-10-30",
        )
        self.assertEqual(first["article_id"], second["article_id"])
        self.assertEqual(second["status"], "updated")
        third = import_manual_article(
            self.conn,
            self.collection_id,
            "标题（修订）",
            "第二版正文内容足够长。\n新的第二段。",
            source_url="https://mp.weixin.qq.com/s/same",
            publish_date="2026-10-30",
        )
        self.assertEqual(third["article_id"], first["article_id"])
        self.assertEqual(third["status"], "unchanged")
        revisions = self.conn.execute(
            "SELECT * FROM article_revisions WHERE article_id=?",
            (first["article_id"],),
        ).fetchall()
        self.assertEqual(len(revisions), 1)
        article = self.conn.execute(
            "SELECT title, text_content FROM articles WHERE id=?",
            (first["article_id"],),
        ).fetchone()
        self.assertEqual(article["title"], "标题（修订）")
        self.assertIn("第二版", article["text_content"])

    def test_same_title_and_text_without_url_is_not_duplicated(self):
        first = import_manual_article(
            self.conn,
            self.collection_id,
            "无链接文章",
            "这是一段足够长的正文，用于测试无链接去重。",
        )
        second = import_manual_article(
            self.conn,
            self.collection_id,
            "无链接文章",
            "这是一段足够长的正文，用于测试无链接去重。",
        )
        self.assertEqual(first["article_id"], second["article_id"])
        self.assertEqual(second["status"], "unchanged")
        count = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        self.assertEqual(count, 1)

    def test_validation_and_publish_date(self):
        with self.assertRaises(ValueError):
            import_manual_article(self.conn, self.collection_id, "", "足够长的正文内容。")
        with self.assertRaises(ValueError):
            import_manual_article(self.conn, self.collection_id, "标题", "短")
        with self.assertRaises(ValueError):
            import_manual_article(
                self.conn,
                self.collection_id,
                "标题",
                "这是一段足够长的正文内容。",
                source_url="mp.weixin.qq.com/s/x",
            )
        with self.assertRaises(ValueError):
            import_manual_article(
                self.conn,
                self.collection_id,
                "标题",
                "这是一段足够长的正文内容。",
                publish_date="2026/13/40",
            )
        result = import_manual_article(
            self.conn,
            self.collection_id,
            "标题",
            "这是一段足够长的正文内容。",
        )
        article = self.conn.execute(
            "SELECT publish_time_source FROM articles WHERE id=?",
            (result["article_id"],),
        ).fetchone()
        self.assertEqual(article["publish_time_source"], "manual_now")

    def test_blank_date_uses_date_at_end_of_title(self):
        result = import_manual_article(
            self.conn,
            self.collection_id,
            "87 旧章节标题 2026.10.13",
            "标题带日期时，留空发布日期应自动采用标题日期。",
        )
        article = self.conn.execute(
            "SELECT publish_time_source, title_date, publish_ts FROM articles WHERE id=?",
            (result["article_id"],),
        ).fetchone()
        self.assertEqual(article["publish_time_source"], "manual_title_date")
        self.assertEqual(article["title_date"], "2026-10-13")
        self.assertEqual(result["publish_ts"], article["publish_ts"])

    def test_chapter_matching_fills_existing_discovered_article(self):
        listing_ts = 1791975832
        existing_id = self._seed_discovered(
            "87 水蛭孢子构建屏障，蛛触传信静候佳音 2026.10.13",
            "2247487959",
            12,
            listing_ts,
        )
        result = import_manual_article(
            self.conn,
            self.collection_id,
            "87 水蛭孢子构建屏障，蛛触传信静候佳音",
            "第一段：水蛭孢子构建屏障。\n第二段：蛛触传信，静候佳音。",
        )
        self.assertEqual(result["article_id"], existing_id)
        self.assertEqual(result["matched_by"], "chapter")
        self.assertEqual(result["chapter"], "87")
        self.assertEqual(result["previous_status"], "failed")
        self.assertEqual(result["status"], "created")
        count = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        self.assertEqual(count, 1)
        article = self.conn.execute(
            "SELECT * FROM articles WHERE id=?", (existing_id,)
        ).fetchone()
        self.assertEqual(article["status"], "ready")
        self.assertIn("2026.10.13", article["title"])
        self.assertEqual(article["publish_ts"], listing_ts)
        segments = self.conn.execute(
            "SELECT COUNT(*) FROM article_segments WHERE article_id=?",
            (existing_id,),
        ).fetchone()[0]
        self.assertGreaterEqual(segments, 2)

    def test_manual_import_history_records_new_and_chapter_updates(self):
        created = import_manual_article(
            self.conn,
            self.collection_id,
            "第1章 初入测试传 2026.10.01",
            "第一章正文，内容足够长。\n第二段用于分段。",
        )
        history = recent_manual_imports(self.conn, 10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["article_id"], created["article_id"])
        self.assertEqual(history[0]["title"], "第1章 初入测试传 2026.10.01")
        self.assertEqual(history[0]["chapter"], "1")
        self.assertEqual(history[0]["collection_title"], "测试文集")
        self.assertTrue(history[0]["imported_at"])

        existing_id = self._seed_discovered(
            "87 旧标题 2026.10.13", "auto-history-87", 12, 1791975832
        )
        updated = import_manual_article(
            self.conn,
            self.collection_id,
            "87 水蛭孢子构建屏障",
            "补全第八十七章的正文内容。\n第二段用于确认入库。",
            chapter_no="87",
        )
        self.assertEqual(updated["article_id"], existing_id)
        history = recent_manual_imports(self.conn, 10)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["article_id"], existing_id)
        self.assertEqual(history[0]["chapter"], "87")
        self.assertEqual(history[0]["matched_by"], "chapter")
        self.assertEqual(history[0]["previous_status"], "failed")
        self.assertEqual(history[1]["article_id"], created["article_id"])

    def test_explicit_chapter_no_matches_when_title_has_no_number(self):
        existing_id = self._seed_discovered(
            "88 原来的标题 2026.10.14", "auto-88", 13, 1792062232
        )
        result = import_manual_article(
            self.conn,
            self.collection_id,
            "我重新整理的标题",
            "这是第八十八章的正文内容，足够长。",
            chapter_no="第88章",
        )
        self.assertEqual(result["article_id"], existing_id)
        self.assertEqual(result["matched_by"], "chapter")
        self.assertEqual(result["chapter"], "88")

    def test_ambiguous_chapter_raises(self):
        self._seed_discovered("87 甲标题 2026.10.13", "auto-87a", 12, 1791975832)
        self._seed_discovered("87 乙标题 2026.10.13", "auto-87b", 13, 1791975833)
        with self.assertRaises(ValueError):
            import_manual_article(
                self.conn,
                self.collection_id,
                "87 丙标题",
                "这是丙标题的正文内容，足够长。",
                chapter_no="87",
            )

    def test_chapter_number_parser(self):
        self.assertEqual(chapter_number("87 标题 2026.10.13"), "87")
        self.assertEqual(chapter_number("第87章 标题"), "87")
        self.assertEqual(chapter_number("2026.10.13 标题"), "")
        self.assertEqual(chapter_number("没有章节号"), "")
        self.assertEqual(chapter_number(chr(0x200B) + "03 标题"), "3")
        self.assertEqual(chapter_number(chr(0xFEFF) + "第46章 标题"), "46")

    def test_url_match_adds_missing_chapter_to_title(self):
        url = "https://mp.weixin.qq.com/s/long86"
        existing_id = self._seed_discovered(
            "测试章节标题",
            "2247487951",
            13,
            1791648000,
            url=url,
        )
        result = import_manual_article(
            self.conn,
            self.collection_id,
            "测试章节标题",
            "林见深与苏晚晴在测试场景中核对线索，正文内容足够长。",
            source_url=url,
            publish_date="2026.10.11",
            chapter_no="86",
        )
        self.assertEqual(result["article_id"], existing_id)
        self.assertEqual(result["matched_by"], "url")
        self.assertEqual(result["chapter"], "86")
        article = self.conn.execute(
            "SELECT title, publish_time_raw FROM articles WHERE id=?", (existing_id,)
        ).fetchone()
        self.assertEqual(article["title"], "86 测试章节标题")
        self.assertEqual(article["publish_time_raw"], "2026-10-11")

    def test_url_match_keeps_existing_chapter_when_title_omits_it(self):
        url = "https://mp.weixin.qq.com/s/keep87"
        existing_id = self._seed_discovered(
            "87 旧标题 2026.10.13", "auto-87d", 12, 1791975832, url=url
        )
        result = import_manual_article(
            self.conn,
            self.collection_id,
            "旧标题",
            "这是旧标题的正文内容，足够长。",
            source_url=url,
        )
        self.assertEqual(result["article_id"], existing_id)
        self.assertEqual(result["chapter"], "87")
        title = self.conn.execute(
            "SELECT title FROM articles WHERE id=?", (existing_id,)
        ).fetchone()[0]
        self.assertEqual(title, "87 旧标题 2026.10.13")
        article = self.conn.execute(
            "SELECT publish_ts, publish_time_raw FROM articles WHERE id=?", (existing_id,)
        ).fetchone()
        self.assertEqual(article["publish_ts"], 1791975832)
        self.assertEqual(article["publish_time_raw"], "2026-10-13")

    def test_chapter_conflict_with_existing_title_raises(self):
        url = "https://mp.weixin.qq.com/s/conflict"
        self._seed_discovered("87 旧标题 2026.10.13", "auto-87c", 12, 1791975832, url=url)
        with self.assertRaises(ValueError):
            import_manual_article(
                self.conn,
                self.collection_id,
                "旧标题",
                "这是旧标题的正文内容，足够长。",
                source_url=url,
                chapter_no="86",
            )

    def test_chapter_conflict_with_manual_title_raises(self):
        with self.assertRaises(ValueError):
            import_manual_article(
                self.conn,
                self.collection_id,
                "87 新标题",
                "这是新标题的正文内容，足够长。",
                chapter_no="86",
            )

    def test_parse_publish_date_accepts_common_formats(self):
        expected_ts, expected_raw, source = parse_publish_date("2026.10.11")
        self.assertEqual(expected_raw, "2026-10-11")
        self.assertEqual(source, "manual")
        for value in ("2026-10-11", "2026/10/11", "2026年10月11日", "发布时间：2026.10.11"):
            ts, raw, source = parse_publish_date(value)
            self.assertEqual(raw, expected_raw, value)
            self.assertEqual(ts, expected_ts, value)
            self.assertEqual(source, "manual", value)
        for value in ("2026年10月11日 07:30", "2026/10/11 7:30:00", "2026-10-11 07时30分"):
            ts, raw, source = parse_publish_date(value)
            self.assertEqual(raw, "2026-10-11 07:30", value)
            self.assertEqual(source, "manual", value)
        _, raw, _ = parse_publish_date("10月11日")
        self.assertTrue(raw.endswith("-10-11"), raw)
        with self.assertRaises(ValueError):
            parse_publish_date("2026年2月30日")


if __name__ == "__main__":
    unittest.main()
