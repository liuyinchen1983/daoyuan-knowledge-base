# -*- coding: utf-8 -*-
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

from daoyuan.ai.deepseek import DeepSeekClient
from daoyuan.ai.key_store import SecretStore
from daoyuan.ai.rag import (
    answer_question,
    build_messages,
    collect_evidence,
    extract_confidence,
    extract_terms,
    validate_citations,
)
from daoyuan.ai.service import (
    MODEL_KEY,
    clear_api_key,
    effective_model,
    save_api_key,
    save_exchange,
    save_model_choice,
    validate_model,
)
from daoyuan.core.config import load_config
from daoyuan.core.errors import ConfigError
from daoyuan.core.models import ListingItem, ParsedArticle
from daoyuan.reader.service import ReaderService
from daoyuan.storage.db import initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)


def make_parsed(text):
    paragraphs = [p for p in text.split("\n") if p.strip()]
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
        safe_html="",
        plain_text="\n".join(paragraphs),
        title_date="2026-10-05",
        segments=segments,
        source_url="https://mp.weixin.qq.com/s?__biz=x&mid=1&chksm=abc",
        text_hash=hashlib.sha256(
            "\n".join(paragraphs).encode("utf-8")
        ).hexdigest(),
    )


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.request = None
        self.timeout = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        return FakeResponse(self.payload)


class FakeClient:
    def __init__(self, content, finish_reason=""):
        self.content = content
        self.finish_reason = finish_reason
        self.messages = None
        self.kwargs = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return {
            "content": self.content,
            "model": "fake-model",
            "finish_reason": self.finish_reason,
            "usage": {
                "prompt_tokens": 123,
                "completion_tokens": 45,
                "total_tokens": 168,
            },
        }


class AiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config_path = root / "config.toml"
        config_path.write_text(
            '[app]\ndata_dir = "%s"\n[ai]\nmax_context_chars = 4000\n'
            % (root / "runtime").as_posix(),
            encoding="utf-8",
        )
        self.config = load_config(config_path)
        self.config.paths.ensure()
        conn = initialize_content_db(self.config.paths.content_db)
        source_id = SourceRepository(conn).upsert("biz", "示例公众号")
        collection_id = CollectionRepository(conn).upsert(
            source_id, "album", "测试文集"
        )
        repo = ArticleRepository(conn)
        article_id, _ = repo.upsert_discovered(
            source_id,
            ListingItem(
                msgid="1",
                itemidx="0",
                title="见深初见 2026.10.05",
                url="https://mp.weixin.qq.com/s?__biz=x&mid=1&chksm=abc",
            ),
            collection_id,
            0,
        )
        repo.mark_ready(
            article_id,
            make_parsed(
                "修行之路漫长。\n见深与天哥同行。\n他们继续向前。\n"
                "守门老者在山门前等候。"
            ),
        )
        conn.close()
        self.service = ReaderService(self.config)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_extract_terms(self):
        terms = extract_terms("见深和天哥是什么关系？")
        self.assertIn("见深", terms)
        self.assertIn("天哥", terms)
        self.assertNotIn("什么", terms)
        story = extract_terms("守门老者的故事说明了什么")
        self.assertIn("守门老者", story)
        self.assertIn("故事", story)
        follow = extract_terms("示例公众号里有没有讲修行的方法")
        self.assertIn("修行", follow)

    def test_collect_evidence_and_context(self):
        evidence = collect_evidence(
            self.service, "见深和天哥是什么关系", max_items=4, max_chars=2000
        )
        self.assertTrue(evidence)
        first = evidence[0]
        self.assertEqual(first["label"], "C1")
        self.assertIn("见深", first["text"])
        self.assertIn("天哥", first["text"])
        self.assertGreater(first["segment_id"], 0)
        self.assertIn("matched_terms", first)

    def test_no_evidence_does_not_call_model(self):
        result = answer_question(
            self.service,
            self.config,
            "完全不存在的甲乙丙丁",
            "sk-test",
            client=FakeClient("不应被调用"),
        )
        self.assertFalse(result["model_used"])
        self.assertEqual(result["failure_reason"], "no_evidence")
        self.assertEqual(result["evidence"], [])

    def test_no_key_returns_local_candidates(self):
        result = answer_question(
            self.service,
            self.config,
            "见深和天哥是什么关系",
            None,
        )
        self.assertFalse(result["model_used"])
        self.assertEqual(result["failure_reason"], "no_key")
        self.assertTrue(result["evidence"])

    def test_valid_citation_maps_to_segment(self):
        client = FakeClient("见深与天哥同行。[C1]\n依据等级：原文直接支持")
        result = answer_question(
            self.service,
            self.config,
            "见深和天哥是什么关系",
            "sk-test",
            client=client,
        )
        self.assertTrue(result["model_used"])
        self.assertEqual(result["confidence"], "原文直接支持")
        self.assertTrue(result["citations"])
        self.assertEqual(result["citations"][0]["label"], "C1")
        self.assertGreater(result["citations"][0]["segment_id"], 0)
        self.assertFalse(result["invalid_citations"])
        self.assertEqual(result["usage"]["total_tokens"], 168)
        self.assertIn("【原文证据】", client.messages[-1]["content"])
        self.assertEqual(client.kwargs["max_tokens"], 8192)

    def test_truncated_answer_is_flagged(self):
        result = answer_question(
            self.service,
            self.config,
            "见深和天哥是什么关系",
            "sk-test",
            client=FakeClient(
                "部分回答[C1]\n依据等级：部分支持", finish_reason="length"
            ),
        )
        self.assertTrue(result["model_used"])
        self.assertTrue(result["truncated"])

    def test_invalid_citation_is_not_reliable(self):
        result = answer_question(
            self.service,
            self.config,
            "见深和天哥是什么关系",
            "sk-test",
            client=FakeClient("这是编造的出处。[C9]\n依据等级：原文直接支持"),
        )
        self.assertEqual(result["invalid_citations"], ["C9"])
        self.assertEqual(result["citations"], [])
        self.assertTrue(result["uncited"])

    def test_follow_up_uses_previous_question(self):
        result = answer_question(
            self.service,
            self.config,
            "他后来怎么样了",
            None,
            previous_question="见深和天哥是什么关系",
        )
        self.assertTrue(result["evidence"])
        self.assertTrue(any("见深" in item["text"] for item in result["evidence"]))

    def test_history_citations_are_stripped(self):
        messages = build_messages(
            "后来呢",
            [{"label": "C1", "title": "标题", "publish_date": "2026-10-05", "context": "证据"}],
            [{"role": "assistant", "content": "旧回答[C1]"}],
        )
        history_message = messages[1]["content"]
        self.assertNotIn("[C1]", history_message)
        self.assertIn("历史对话", history_message)

    def test_history_keeps_recent_four_rounds(self):
        history = []
        for index in range(6):
            history.append({"role": "user", "content": "问题%d" % index})
            history.append({"role": "assistant", "content": "回答%d" % index})
        messages = build_messages(
            "继续",
            [{"label": "C1", "title": "标题", "publish_date": "2026-10-05", "context": "证据"}],
            history,
        )
        history_messages = messages[1:-1]
        self.assertEqual(len(history_messages), 8)
        self.assertIn("问题2", history_messages[0]["content"])
        self.assertIn("回答5", history_messages[-1]["content"])

    def test_citation_helpers(self):
        evidence = [{"label": "C1"}, {"label": "C2"}]
        result = validate_citations("[C2][C1][C9]", evidence)
        self.assertEqual(result["cited"], ["C2", "C1"])
        self.assertEqual(result["invalid"], ["C9"])
        self.assertEqual(
            extract_confidence("……\n依据等级：部分支持"),
            "部分支持",
        )

    def test_save_exchange_and_clear(self):
        result = answer_question(
            self.service,
            self.config,
            "见深和天哥是什么关系",
            "sk-test",
            client=FakeClient("见深与天哥同行。[C1]\n依据等级：原文直接支持"),
        )
        saved = save_exchange(self.service, result["question"], result)
        self.assertGreater(saved["session_id"], 0)
        sessions = self.service.state.list_qa_sessions()
        self.assertEqual(len(sessions), 1)
        messages = self.service.state.list_qa_messages(saved["session_id"])
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        citations = self.service.state.conn.execute(
            "SELECT citation_label, article_id, segment_id FROM qa_citations"
        ).fetchall()
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["citation_label"], "C1")
        self.assertGreater(citations[0]["segment_id"], 0)
        usage_rows = self.service.state.conn.execute(
            "SELECT kind, model, total_tokens FROM ai_usage"
        ).fetchall()
        self.assertEqual(len(usage_rows), 1)
        self.assertEqual(usage_rows[0]["kind"], "ask")
        self.assertEqual(usage_rows[0]["model"], "fake-model")
        self.assertEqual(usage_rows[0]["total_tokens"], 168)
        self.service.state.clear_qa()
        self.assertEqual(self.service.state.list_qa_sessions(), [])
        self.assertEqual(
            self.service.state.ai_usage_stats()["all"]["calls"], 1
        )

    def test_ai_usage_stats_and_clear(self):
        self.service.state.log_ai_usage(
            "ask",
            "model-a",
            {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            session_id=1,
        )
        self.service.state.log_ai_usage(
            "test",
            "model-b",
            {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
        )
        self.assertIsNone(self.service.state.log_ai_usage("ask", "model-a", {}))
        stats = self.service.state.ai_usage_stats()
        self.assertEqual(stats["today"]["calls"], 2)
        self.assertEqual(stats["last7"]["total_tokens"], 160)
        self.assertEqual(stats["last30"]["prompt_tokens"], 130)
        self.assertEqual(stats["all"]["completion_tokens"], 30)
        self.assertEqual(
            [item["model"] for item in stats["by_model"]], ["model-a", "model-b"]
        )
        self.assertEqual(len(stats["recent"]), 2)
        self.service.state.clear_ai_usage()
        self.assertEqual(self.service.state.ai_usage_stats()["all"]["calls"], 0)

    @unittest.skipUnless(os.name == "nt", "DPAPI 仅 Windows")
    def test_secret_store_roundtrip(self):
        store = SecretStore(Path(self.tmp.name) / "secrets" / "deepseek.key")
        store.save("sk-test-not-a-real-key")
        self.assertTrue(store.exists())
        self.assertEqual(store.load(), "sk-test-not-a-real-key")
        raw = store.path.read_bytes()
        self.assertNotIn(b"sk-test-not-a-real-key", raw)
        store.clear()
        self.assertFalse(store.exists())

    @unittest.skipUnless(os.name == "nt", "DPAPI 仅 Windows")
    def test_service_key_wrappers(self):
        save_api_key(self.config, "sk-local-test")
        self.assertTrue(
            (self.config.paths.secrets / "deepseek.key").exists()
        )
        clear_api_key(self.config)
        self.assertFalse(
            (self.config.paths.secrets / "deepseek.key").exists()
        )

    def test_model_choice_persists_and_validates(self):
        self.assertEqual(
            effective_model(self.config, self.service), self.config.ai.model
        )
        save_model_choice(self.service, "deepseek-v4-pro")
        self.assertEqual(
            self.service.state.get_setting(MODEL_KEY), "deepseek-v4-pro"
        )
        self.assertEqual(
            effective_model(self.config, self.service), "deepseek-v4-pro"
        )
        self.service.state.set_setting(MODEL_KEY, "bad model!")
        self.assertEqual(
            effective_model(self.config, self.service), self.config.ai.model
        )
        with self.assertRaises(ConfigError):
            validate_model("bad model!")


class DeepSeekClientTests(unittest.TestCase):
    payload = {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": "回答正文"}}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }

    def test_request_shape_and_parsing(self):
        transport = FakeTransport(self.payload)
        client = DeepSeekClient(
            "https://api.deepseek.com/",
            "deepseek-flash",
            "sk-secret",
            timeout=12,
            transport=transport,
        )
        result = client.chat([{"role": "user", "content": "你好"}])
        self.assertEqual(result["content"], "回答正文")
        self.assertEqual(result["usage"]["total_tokens"], 15)
        self.assertTrue(transport.request.full_url.endswith("/chat/completions"))
        self.assertEqual(
            transport.request.get_header("Authorization"), "Bearer sk-secret"
        )
        body = json.loads(transport.request.data.decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertFalse(body["stream"])
        self.assertEqual(transport.timeout, 12)

    def test_http_401_friendly_error(self):
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions",
            401,
            "Unauthorized",
            None,
            io.BytesIO(b'{"error":{"message":"invalid key"}}'),
        )
        client = DeepSeekClient(
            "https://api.deepseek.com",
            "deepseek-flash",
            "sk-bad",
            transport=FakeTransport(error=error),
        )
        with self.assertRaises(Exception) as context:
            client.chat([{"role": "user", "content": "你好"}])
        self.assertIn("API Key 无效", str(context.exception))

    def test_http_402_friendly_error(self):
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions",
            402,
            "Payment Required",
            None,
            io.BytesIO(b'{"error":{"message":"insufficient balance"}}'),
        )
        client = DeepSeekClient(
            "https://api.deepseek.com",
            "deepseek-flash",
            "sk-test",
            transport=FakeTransport(error=error),
        )
        with self.assertRaises(Exception) as context:
            client.chat([{"role": "user", "content": "你好"}])
        self.assertIn("余额不足", str(context.exception))

    def test_length_finish_with_empty_content_is_explicit(self):
        client = DeepSeekClient(
            "https://api.deepseek.com",
            "deepseek-flash",
            "sk-test",
            transport=FakeTransport(
                {
                    "choices": [
                        {
                            "message": {"content": "", "reasoning_content": "思考"},
                            "finish_reason": "length",
                        }
                    ]
                }
            ),
        )
        with self.assertRaises(Exception) as context:
            client.chat([{"role": "user", "content": "你好"}])
        self.assertIn("思考过程占满", str(context.exception))

    def test_missing_choices_is_friendly(self):
        client = DeepSeekClient(
            "https://api.deepseek.com",
            "deepseek-flash",
            "sk-test",
            transport=FakeTransport({"choices": []}),
        )
        with self.assertRaises(Exception) as context:
            client.chat([{"role": "user", "content": "你好"}])
        self.assertIn("未返回有效回答", str(context.exception))


if __name__ == "__main__":
    unittest.main()
