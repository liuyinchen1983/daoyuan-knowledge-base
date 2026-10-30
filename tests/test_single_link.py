# -*- coding: utf-8 -*-
"""单篇链接采集：URL 校验、文集识别、真实 msgid 入库和冷却保护。"""
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from daoyuan.collector.cooldown import (
    active_cooldown,
    load_cooldown,
    note_request_start,
    request_wait_seconds,
    write_cooldown,
)
from daoyuan.collector.single import (
    CooldownActiveError,
    InvalidLinkError,
    RiskCooldownError,
    TooSoonError,
    collect_article_by_url,
    parse_article_link,
)
from daoyuan.core.config import load_config
from daoyuan.core.lock import ProcessLock, RequestGate, is_lock_held
from daoyuan.core.models import ListingItem
from daoyuan.storage.db import initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)

BIZ = "Yml6X3BsYWNlaG9sZGVy"
ALBUM = "1000000000000000001"
LINK = (
    "https://mp.weixin.qq.com/s?__biz=%s&mid=2247487550&idx=1&sn=abc"
    "&cur_album_id=%s&scene=189#wechat_redirect" % (BIZ, ALBUM)
)

SAMPLE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<script>
var msg_title = '42 测试文章标题 2026.07.07';
var author = '示例公众号';
var ct = "1783353600";
</script>
<div id="js_content">
<p>苏晚晴收回目光，抬手推门，准备踏入测试体验区。整个通道空无一人，只有极低的通风声。</p>
<p>林见深站在通道尽头，提醒她注意脚下；这一段正文用于验证单篇链接采集的标题、段落和发布时间。</p>
</div>
</body></html>"""

RISK_HTML = "<html><body><div>secitptpage/verify</div></body></html>"


class FakeHttp:
    def __init__(self, page_html: str, final_url: str = ""):
        self.page_html = page_html
        self.final_url = final_url
        self.calls = []

    def get_text(self, url: str):
        self.calls.append(url)
        return self.final_url or url, self.page_html


class SingleLinkTests(unittest.TestCase):
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
        self.album_collection = CollectionRepository(self.conn).upsert(
            self.source_id, ALBUM, "测试传"
        )
        self.other_collection = CollectionRepository(self.conn).upsert(
            self.source_id, "1234567890", "其他文集"
        )
        self.cooldown_path = root / "collect_all.cooldown"
        self.interval_path = root / "collect_all.last_request"

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def _collect(self, url=LINK, collection_id=None, http=None):
        return collect_article_by_url(
            self.conn,
            self.config,
            url,
            collection_id=collection_id,
            http_client=http or FakeHttp(SAMPLE_HTML),
            cooldown_path=self.cooldown_path,
            interval_path=self.interval_path,
        )

    def test_link_creates_article_with_real_msgid_and_album_collection(self):
        result = self._collect(collection_id=self.other_collection)
        self.assertEqual(result["collection_id"], self.album_collection)
        self.assertTrue(result["album_matched"])
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["chapter"], "42")
        self.assertEqual(result["origin"], "link")
        article = self.conn.execute(
            "SELECT * FROM articles WHERE id=?", (result["article_id"],)
        ).fetchone()
        self.assertEqual(article["status"], "ready")
        self.assertEqual(article["msgid"], "2247487550")
        self.assertEqual(article["itemidx"], "1")
        self.assertEqual(article["title"], "42 测试文章标题 2026.07.07")
        self.assertEqual(article["source_url"], LINK)
        self.assertIn("苏晚晴", article["text_content"])
        self.assertTrue(article["publish_time_raw"].startswith("2026-07-"))
        self.assertEqual(
            self.conn.execute(
                "SELECT value FROM meta WHERE key='manual.import_history'"
            ).fetchone()[0].count('"origin": "link"'),
            1,
        )

    def test_existing_msgid_is_updated_instead_of_duplicated(self):
        url_old = LINK.replace("sn=abc", "sn=old")
        article_id, _ = ArticleRepository(self.conn).upsert_discovered(
            self.source_id,
            ListingItem(
                msgid="2247487550",
                itemidx="1",
                title="42 旧标题 2026.07.07",
                url=url_old,
                create_time=1783353600,
            ),
            self.album_collection,
            0,
        )
        ArticleRepository(self.conn).mark_failed(article_id, "restricted")
        result = self._collect(collection_id=self.other_collection)
        self.assertEqual(result["article_id"], article_id)
        self.assertEqual(result["matched_by"], "msgid")
        self.assertEqual(result["status"], "created")
        count = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE msgid='2247487550'"
        ).fetchone()[0]
        self.assertEqual(count, 1)
        title = self.conn.execute(
            "SELECT title FROM articles WHERE id=?", (article_id,)
        ).fetchone()[0]
        self.assertIn("测试文章标题", title)

    def test_link_without_album_uses_selected_collection(self):
        url = (
            "https://mp.weixin.qq.com/s?__biz=%s&mid=2247487550&idx=1&sn=xyz"
            % BIZ
        )
        result = self._collect(url=url, collection_id=self.other_collection)
        self.assertEqual(result["collection_id"], self.other_collection)
        self.assertFalse(result["album_matched"])

    def test_short_link_uses_final_url_for_msgid_and_album(self):
        http = FakeHttp(SAMPLE_HTML, final_url=LINK)
        result = self._collect(
            url="https://mp.weixin.qq.com/s/short",
            collection_id=self.other_collection,
            http=http,
        )
        self.assertEqual(result["collection_id"], self.album_collection)
        self.assertTrue(result["album_matched"])
        self.assertEqual(result["source_url"], LINK)

    def test_link_validation(self):
        with self.assertRaises(InvalidLinkError):
            parse_article_link("https://example.com/s?__biz=%s" % BIZ)
        with self.assertRaises(InvalidLinkError):
            parse_article_link(
                "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=%s&album_id=%s"
                % (BIZ, ALBUM)
            )
        with self.assertRaises(InvalidLinkError):
            parse_article_link(
                "https://mp.weixin.qq.com/s?__biz=OTHER&mid=1&idx=1"
            )
        parsed = parse_article_link("https://mp.weixin.qq.com/s/short")
        self.assertEqual(parsed["mid"], "")
        self.assertEqual(parsed["album_id"], "")

    @unittest.skipUnless(os.name == "nt", "命名互斥锁只在 Windows 上验证")
    def test_lock_probe_does_not_steal_collector_lock(self):
        name = "Local\\DaoyuanSingleLinkTest_%s" % os.getpid()
        self.assertFalse(is_lock_held(name))
        lock = ProcessLock(name)
        self.assertTrue(lock.acquire())
        try:
            self.assertTrue(is_lock_held(name))
        finally:
            lock.release()
        self.assertFalse(is_lock_held(name))

    @unittest.skipUnless(os.name == "nt", "命名互斥锁只在 Windows 上验证")
    def test_request_gate_serializes_requests_across_threads(self):
        name = "Local\\DaoyuanRequestGateTest_%s" % os.getpid()
        first = RequestGate(name)
        self.assertTrue(first.acquire(50))
        results = []

        def contender():
            gate = RequestGate(name)
            results.append(gate.acquire(100))
            if results[-1]:
                gate.release()

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join()
        self.assertEqual(results, [False])
        first.release()
        second = RequestGate(name)
        self.assertTrue(second.acquire(100))
        second.release()

    def test_active_cooldown_blocks_request(self):
        write_cooldown(datetime.now() + timedelta(hours=1), 2, self.cooldown_path)
        http = FakeHttp(SAMPLE_HTML)
        with self.assertRaises(CooldownActiveError):
            self._collect(http=http)
        self.assertEqual(http.calls, [])
        self.assertIsNotNone(active_cooldown(self.cooldown_path))

    def test_interval_blocks_second_link_without_http(self):
        self.assertEqual(request_wait_seconds(self.interval_path), 0)
        note_request_start(self.interval_path)
        self.assertGreater(request_wait_seconds(self.interval_path), 0)
        http = FakeHttp(SAMPLE_HTML)
        with self.assertRaises(TooSoonError):
            self._collect(http=http)
        self.assertEqual(http.calls, [])

    def test_expired_interval_allows_request(self):
        self.interval_path.write_text(
            '{"last_request_at": "2026-01-01T00:00:00", '
            '"next_allowed_at": "2026-01-01T00:00:05"}',
            encoding="utf-8",
        )
        self.assertEqual(request_wait_seconds(self.interval_path), 0)
        result = self._collect()
        self.assertEqual(result["status"], "created")

    def test_verification_page_writes_cooldown(self):
        http = FakeHttp(RISK_HTML)
        with self.assertRaises(RiskCooldownError) as caught:
            self._collect(http=http)
        self.assertEqual(len(http.calls), 1)
        self.assertGreater(caught.exception.resume_at, datetime.now())
        resume_at, streak = load_cooldown(self.cooldown_path)
        self.assertIsNotNone(resume_at)
        self.assertEqual(streak, 1)


if __name__ == "__main__":
    unittest.main()
