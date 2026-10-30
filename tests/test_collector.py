# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from daoyuan.collector.album import AlbumClient, normalize_article_list
from daoyuan.collector.parser import parse_article
from daoyuan.collector.service import CollectService
from daoyuan.core.config import load_config
from daoyuan.core.errors import ArticleUnavailableError, ParseError, RiskControlError
from daoyuan.storage.db import initialize_content_db
from daoyuan.storage.repositories import CollectionRepository, SourceRepository


def article_html(
    title="测试文章 2025.08.14", author="张三", ct="1755000000", body=None
):
    if body is None:
        body = (
            "<p>第一段，修行之路漫长，需要耐心与观察。"
            "<strong>重点句</strong>与<span style=\"color:#B49763\">金色强调</span>。</p>"
            "<p>第二段，见深与天哥继续前行，讨论所见的种种。<br/>换行一句。"
            "<br/><br/>新的段落，记录另一段对话与思考。</p>"
            "<section><p>第三段，引用一段旧文，说明前后的关联。</p></section>"
            "<p>预览时标签不可点</p>"
        )
    return (
        "<html><head><script>"
        "var msg_title = '%s'; var author = '%s'; var ct = '%s';"
        "</script></head><body>"
        '<div id="js_content" style="visibility:hidden">%s</div>'
        "</body></html>"
    ) % (title, author, ct, body)


def album_json(items, cont="0", title="测试合集", count=99):
    return json.dumps(
        {
            "getalbum_resp": {
                "base_info": {
                    "title": title,
                    "article_count": count,
                    "is_reverse": 1,
                    "isupdating": 1,
                    "description": "简介",
                },
                "article_list": items,
                "continue_flag": cont,
            }
        },
        ensure_ascii=False,
    )


class ParserTests(unittest.TestCase):
    def test_parse_structure_styles_and_segments(self):
        parsed = parse_article(article_html())
        self.assertEqual(parsed.title, "测试文章 2025.08.14")
        self.assertEqual(parsed.page_author, "张三")
        self.assertEqual(parsed.publish_ts, 1755000000)
        self.assertEqual(parsed.title_date, "2025-08-14")
        self.assertIn("<strong>重点句</strong>", parsed.safe_html)
        self.assertIn('style="color:#B49763"', parsed.safe_html)
        self.assertNotIn("<script", parsed.safe_html)
        self.assertNotIn("alert", parsed.safe_html)
        self.assertGreaterEqual(len(parsed.segments), 4)
        self.assertIn("见深与天哥", parsed.plain_text)
        # 双 <br> 应切成独立段落
        self.assertTrue(any("新的段落" in seg.plain_text for seg in parsed.segments))
        # 偏移连续
        for seg in parsed.segments:
            self.assertEqual(parsed.plain_text[seg.char_start : seg.char_end], seg.plain_text)

    def test_section_paragraphs_and_nbsp_spacers(self):
        body = (
            '<section><span leaf="">第一段内容，长度足够用于测试段落切分，包含标点。</span></section>'
            '<section><span leaf="">&nbsp;</span></section>'
            '<section><span leaf="">第二段内容，长度足够用于测试段落切分，包含标点。</span></section>'
            '<section><span leaf="">&nbsp;</span></section>'
            '<section><span leaf="">第三段内容，长度足够用于测试段落切分，包含标点。</span></section>'
        )
        parsed = parse_article(article_html(body=body))
        self.assertEqual(len(parsed.segments), 3)
        self.assertNotIn("&nbsp;", parsed.safe_html)
        self.assertNotIn(chr(0xA0), parsed.plain_text)
        self.assertEqual(parsed.segments[0].seq_no, 0)
        self.assertEqual(parsed.segments[-1].seq_no, 2)

    def test_double_quoted_ct_is_parsed(self):
        page = article_html().replace(
            "var ct = '1755000000'", 'var ct = "1793078970"'
        )
        parsed = parse_article(page)
        self.assertEqual(parsed.publish_ts, 1793078970)
        self.assertEqual(parsed.publish_time_source, "page_ct")

    def test_verification_page_stops(self):
        with self.assertRaises(RiskControlError):
            parse_article(article_html(body="<p>正文</p>") + "wappoc_appmsgcaptcha")

    def test_deleted_article_is_unavailable(self):
        with self.assertRaises(ArticleUnavailableError):
            parse_article("<html><body>该内容已被发布者删除</body></html>")

    def test_empty_content(self):
        with self.assertRaises(ParseError):
            parse_article(article_html(body="<p>太短</p>"))


class FakeHttp:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get_text(self, url):
        self.calls.append(url)
        return self.handler(url)


class AlbumTests(unittest.TestCase):
    def test_normalize_count_one_dict(self):
        self.assertEqual(normalize_article_list({"msgid": "1", "title": "x"}), [{"msgid": "1", "title": "x"}])
        self.assertEqual(normalize_article_list({"foo": "x"}), [])
        self.assertEqual(normalize_article_list(None), [])

    def test_listing_unescapes_html_entities(self):
        from daoyuan.collector.album import _to_listing

        item = _to_listing(
            {
                "msgid": "1",
                "itemidx": "0",
                "title": "标题&amp;副题",
                "url": "https://mp.weixin.qq.com/s?__biz=x&amp;chksm=2",
            }
        )
        self.assertEqual(item.title, "标题&副题")
        self.assertEqual(item.url, "https://mp.weixin.qq.com/s?__biz=x&chksm=2")

    def test_direction_and_pagination(self):
        def handler(url):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            reverse = query["is_reverse"][0]
            count = query["count"][0]
            if count == "1":
                item = {"msgid": "1" if reverse == "0" else "2", "itemidx": "0",
                        "title": "t", "url": "u", "create_time": 100 if reverse == "0" else 200}
                return url, album_json(item)
            if "begin_msgid" in query:
                item = {"msgid": "3", "itemidx": "0", "title": "三", "url": "u3", "create_time": 3}
                return url, album_json([item], cont="0")
            items = [
                {"msgid": "1", "itemidx": "0", "title": "一", "url": "u1", "create_time": 5},
                {"msgid": "2", "itemidx": "0", "title": "二", "url": "u2", "create_time": 4},
            ]
            return url, album_json(items, cont="1")

        http = FakeHttp(handler)
        client = AlbumClient(http, sleeper=lambda s: None, rand=lambda: 0.5)
        reverse, info = client.detect_direction("biz", "album")
        self.assertEqual(reverse, 1)
        self.assertEqual(info["title"], "测试合集")
        items, info = client.fetch_all("biz", "album", reverse)
        self.assertEqual([i.msgid for i in items], ["1", "2", "3"])
        self.assertEqual(len(http.calls), 2 + 2)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "config.toml"
        self.config_path.write_text(
            '[app]\ndata_dir = "%s"\n[collector]\ndelay_min = 3\ndelay_max = 3\n'
            "batch_size = 20\nbatch_pause_min = 0\nbatch_pause_max = 0\n"
            % (Path(self.tmp.name) / "runtime").as_posix(),
            encoding="utf-8",
        )
        self.config = load_config(self.config_path)
        self.config.paths.ensure()
        self.conn = initialize_content_db(self.config.paths.content_db)
        self.source_id = SourceRepository(self.conn).upsert("Yml6X3BsYWNlaG9sZGVy", "示例公众号")
        self.collection_id = CollectionRepository(self.conn).upsert(
            self.source_id,
            "1000000000000000002",
            "示例文集一",
            sort_mode="newest_first:rev=1",
            expected_count=2,
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _http(self, article_body=None, album_items=None):
        if album_items is None:
            album_items = [
                {"msgid": "1", "itemidx": "0", "title": "第一篇", "url": "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=1&chksm=aa", "create_time": 1755000000},
                {"msgid": "2", "itemidx": "0", "title": "第二篇", "url": "https://mp.weixin.qq.com/s?__biz=x&mid=2&idx=1&sn=2&chksm=bb", "create_time": 1754000000},
            ]

        def handler(url):
            if "appmsgalbum" in url:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                count = query.get("count", ["20"])[0]
                if count == "1":
                    item = album_items[0]
                    return url, album_json(item)
                return url, album_json(album_items, cont="0")
            return url, article_html(body=article_body)

        return FakeHttp(handler)

    def _service(self, http):
        return CollectService(
            self.config,
            self.conn,
            http=http,
            sleeper=lambda s: None,
            rand=lambda: 0.5,
        )

    def test_full_then_update_then_full_idempotent(self):
        http = self._http()
        service = self._service(http)
        first = service.run(mode="update", collection_ids=[self.collection_id])
        self.assertEqual(first[0].status, "completed")
        self.assertEqual(first[0].list_found, 2)
        self.assertEqual(first[0].success, 2)
        self.assertEqual(first[0].fetched, 2)

        second = service.run(mode="update", collection_ids=[self.collection_id])
        self.assertEqual(second[0].status, "completed")
        self.assertEqual(second[0].fetched, 0)
        self.assertEqual(second[0].success, 0)

        third = service.run(mode="full", collection_ids=[self.collection_id])
        self.assertEqual(third[0].status, "completed")
        self.assertEqual(third[0].fetched, 2)
        self.assertEqual(third[0].skipped, 2)
        self.assertEqual(third[0].success, 0)

        ready = self.conn.execute("SELECT count(*) FROM articles WHERE status='ready'").fetchone()[0]
        self.assertEqual(ready, 2)
        runs = self.conn.execute("SELECT count(*) FROM collect_runs").fetchone()[0]
        self.assertEqual(runs, 3)

    def test_max_fetch_stops_and_next_run_resumes(self):
        service = self._service(self._http())
        first = service.run(
            mode="update", collection_ids=[self.collection_id], max_fetch=1
        )[0]
        self.assertEqual(first.status, "stopped")
        self.assertEqual(first.message, "max_fetch_reached")
        self.assertEqual(first.fetched, 1)
        self.assertEqual(first.success, 1)
        pending = self.conn.execute(
            "SELECT count(*) FROM articles WHERE status='pending'"
        ).fetchone()[0]
        self.assertEqual(pending, 1)

        second = service.run(
            mode="update", collection_ids=[self.collection_id], max_fetch=1
        )[0]
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.success, 1)
        self.assertEqual(second.fetched, 1)
        ready = self.conn.execute(
            "SELECT count(*) FROM articles WHERE status='ready'"
        ).fetchone()[0]
        self.assertEqual(ready, 2)

    def test_risk_stops_batch(self):
        http = self._http(article_body="<p>正文内容足够长足够长足够长足够长足够长足够长足够长足够长。</p>")
        # 让第一篇返回验证页
        def handler(url):
            if "appmsgalbum" in url:
                return http.handler(url)
            return url, "<html><body>wappoc_appmsgcaptcha</body></html>"

        service = self._service(FakeHttp(handler))
        result = service.run(mode="update", collection_ids=[self.collection_id])[0]
        self.assertEqual(result.status, "restricted")
        self.assertEqual(result.fetched, 1)
        self.assertEqual(result.restricted, 1)

    def test_unavailable_skips_and_continues(self):
        calls = {"n": 0}
        base = self._http()

        def handler(url):
            if "appmsgalbum" in url:
                return base.handler(url)
            calls["n"] += 1
            if calls["n"] == 1:
                return url, "<html><body>该内容已被发布者删除</body></html>"
            return url, article_html()

        service = self._service(FakeHttp(handler))
        result = service.run(mode="update", collection_ids=[self.collection_id])[0]
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.unavailable, 1)
        self.assertEqual(result.success, 1)
        self.assertEqual(result.fetched, 2)


if __name__ == "__main__":
    unittest.main()
