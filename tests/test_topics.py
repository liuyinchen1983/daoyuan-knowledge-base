# -*- coding: utf-8 -*-
import hashlib
import tempfile
import unittest
from pathlib import Path

from daoyuan.core.config import load_config
from daoyuan.core.models import ListingItem, ParsedArticle
from daoyuan.storage.backup import TOPICS_FILE, create_backup, inspect_backup, restore_backup
from daoyuan.storage.db import connect, initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)
from daoyuan.topics import extractor
from daoyuan.topics.scanner import scan_collection
from daoyuan.topics.store import TopicStore


def make_parsed(title, text):
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
        title=title,
        page_author="测试作者",
        publish_ts=1793078970,
        publish_time_raw="1793078970",
        publish_time_source="page_ct",
        safe_html="",
        plain_text="\n".join(paragraphs),
        title_date="2026-10-05",
        segments=segments,
        source_url="https://mp.weixin.qq.com/s?__biz=x&mid=1&chksm=abc",
        text_hash=hashlib.sha256("\n".join(paragraphs).encode("utf-8")).hexdigest(),
    )


class TopicFixture(unittest.TestCase):
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
        source_id = SourceRepository(self.conn).upsert("biz", "示例公众号")
        self.collection_id = CollectionRepository(self.conn).upsert(
            source_id, "album", "测试文集"
        )
        self.source_id = source_id

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def add_article(self, msgid, title, text, serial=0):
        article_id, _ = ArticleRepository(self.conn).upsert_discovered(
            self.source_id,
            ListingItem(
                msgid=str(msgid),
                itemidx="0",
                title=title,
                url="https://mp.weixin.qq.com/s?__biz=x&mid=%s&idx=0&sn=1"
                % msgid,
            ),
            self.collection_id,
            serial,
        )
        ArticleRepository(self.conn).mark_ready(
            article_id, make_parsed(title, text)
        )
        self.conn.commit()
        return article_id

    def scan(self, full=False):
        return scan_collection(self.config, self.collection_id, full=full)


class ExtractorTests(TopicFixture):
    def test_extracts_people_places_items_with_exact_evidence(self):
        self.add_article(
            "1",
            "云栖老街",
            "林见深说：“天哥，X7 Future 的机甲准备好了。”\n"
            "顾清川问道：“机甲舱在哪？”\n"
            "他们走进云栖老街。\n"
            "林见深又说：“机甲留在老街。”\n"
            "林见深说：“顾清川，跟上。”\n"
            "顾清川问道：“天哥呢？”\n"
            "林见深说：“天哥在机甲舱。”\n"
            "顾清川说：“我去老街。”\n"
            "林见深说：“走。”\n"
            "顾清川说：“好。”",
        )
        content = connect(self.config.paths.content_db, readonly=True)
        try:
            articles = content.execute(
                "SELECT id, title, text_hash, text_content FROM articles"
            ).fetchall()
            segments = content.execute(
                "SELECT id, plain_text FROM article_segments ORDER BY seq_no"
            ).fetchall()
        finally:
            content.close()
        article = dict(articles[0])
        segs = [dict(row) for row in segments]
        candidates = extractor.extract_article(
            article, segs, known_terms=()
        )
        accepted = [
            candidate
            for candidate in candidates
            if extractor.accept_candidate(candidate)
        ]
        by_name = {(candidate.kind, candidate.name): candidate for candidate in accepted}
        self.assertIn(("person", "林见深"), by_name)
        self.assertIn(("person", "天哥"), by_name)
        self.assertIn(("person", "顾清川"), by_name)
        self.assertIn(("place", "云栖老街"), by_name)
        self.assertIn(("item", "机甲"), by_name)
        self.assertIn(("item", "X7 Future"), by_name)
        self.assertNotIn(("item", "顾清川机甲"), by_name)
        text = article["text_content"]
        for candidate in accepted:
            for mention in candidate.mentions:
                self.assertIn(mention.quote, text)
                self.assertIn(mention.surface, mention.quote)

    def test_rejects_verb_and_common_word_fragments(self):
        candidates = extractor.extract_article(
            {"id": 1, "title": "测试"},
            [
                {
                    "id": 1,
                    "plain_text": "连忙追问：“怎么回事？”管理说：“别总这样。”"
                    "从总说道：“先走。”林素问道：“机甲呢？”林素又说：“走。”",
                }
            ],
            known_terms=(),
        )
        accepted = [
            candidate
            for candidate in candidates
            if extractor.accept_candidate(candidate)
        ]
        names = {candidate.name for candidate in accepted}
        self.assertNotIn("连忙追", names)
        self.assertNotIn("管理", names)
        self.assertNotIn("别总", names)
        self.assertNotIn("从总", names)
        self.assertIn("林素", names)

    def test_person_surname_after_conjunction_is_not_a_place(self):
        candidates = extractor.extract_article(
            {"id": 1, "title": "测试"},
            [
                {
                    "id": 1,
                    "plain_text": "但江寒川没说话。可江寒川又说了一句。"
                    "将江寒川休息舱包围在当中。",
                }
            ],
            known_terms=(),
        )
        places = {candidate.name for candidate in candidates if candidate.kind == "place"}
        self.assertNotIn("但江", places)
        self.assertNotIn("可江", places)
        self.assertNotIn("将江", places)

    def test_rejects_place_fragments_from_truncation_and_phrases(self):
        candidates = extractor.extract_article(
            {"id": 1, "title": "测试"},
            [
                {
                    "id": 1,
                    "plain_text": "局势进入一级风险区。其实江寒川没说话。"
                    "魏先生侧身引路，走进实验基地。苏晚晴原地一怔。",
                }
            ],
            known_terms=(("苏晚晴", "person", "苏晚晴"),),
        )
        places = {
            candidate.name
            for candidate in candidates
            if candidate.kind == "place"
        }
        self.assertNotIn("级风险区", places)
        self.assertNotIn("其实江", places)
        self.assertNotIn("魏先生侧身引路", places)
        self.assertNotIn("苏晚晴原", places)
        self.assertIn("实验基地", places)

    def test_verb_prefix_is_trimmed_from_place(self):
        candidates = extractor.extract_article(
            {"id": 1, "title": "测试"},
            [
                {
                    "id": 1,
                    "plain_text": "苏晚晴刚踏入办公区，刘副组长迎了上来。"
                    "她刚走进办公区，就听见争吵声。",
                }
            ],
            known_terms=(),
        )
        places = {
            candidate.name
            for candidate in candidates
            if candidate.kind == "place"
        }
        self.assertIn("办公区", places)
        self.assertNotIn("刚踏入办公区", places)
        self.assertNotIn("刚走进办公区", places)

    def test_approved_stopword_entity_keeps_evidence(self):
        candidates = extractor.extract_article(
            {"id": 1, "title": "测试"},
            [{"id": 1, "plain_text": "苏晚晴扶住姨妈的手臂，姨妈笑了笑。"}],
            known_terms=(
                ("姨妈", "person", "姨妈"),
                ("苏晚晴", "person", "苏晚晴"),
            ),
        )
        aunts = [
            candidate
            for candidate in candidates
            if candidate.kind == "person" and candidate.name == "姨妈"
        ]
        self.assertTrue(aunts)
        self.assertTrue(aunts[0].mentions)

    def test_role_suffix_short_name_is_not_added_as_alias(self):
        candidates = []
        for name in ("范部长", "老管家", "顾清川"):
            candidate = extractor.Candidate(kind="person", name=name)
            candidate.mentions.append(
                extractor.Mention(
                    article_id=1,
                    segment_id=1,
                    surface=name,
                    quote="%s说：“走。”" % name,
                )
            )
            candidates.append(candidate)
        processed = extractor.postprocess(candidates)
        by_name = {candidate.name: candidate for candidate in processed}
        self.assertNotIn("部长", by_name["范部长"].aliases)
        self.assertNotIn("管家", by_name["老管家"].aliases)
        self.assertIn("清川", by_name["顾清川"].aliases)


    def test_known_alias_is_attached_to_canonical_entity(self):
        candidates = extractor.extract_article(
            {"id": 1, "title": "测试"},
            [{"id": 1, "plain_text": "见深说：“天哥，走。”"}],
            known_terms=(("见深", "person", "林见深"),),
        )
        person = next(
            candidate
            for candidate in candidates
            if candidate.kind == "person" and candidate.name == "林见深"
        )
        self.assertTrue(person.strong >= 1)
        self.assertIn("见深", person.aliases)


class TopicStoreTests(TopicFixture):
    def test_upsert_review_reject_and_alias(self):
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            mention = extractor.Mention(
                article_id=1, segment_id=1, surface="林见深", quote="林见深说：“走。”"
            )
            entity_id, created, added = store.upsert_candidate(
                self.collection_id,
                "person",
                "林见深",
                aliases=("见深",),
                mentions=[mention],
            )
            self.assertTrue(created)
            self.assertEqual(added, 1)
            found = store.find_entity(self.collection_id, "person", "见深")
            self.assertIsNotNone(found)
            self.assertEqual(found["id"], entity_id)

            _, created_again, added_again = store.upsert_candidate(
                self.collection_id,
                "person",
                "林见深",
                mentions=[
                    extractor.Mention(
                        article_id=1,
                        segment_id=2,
                        surface="林见深",
                        quote="林见深又说：“走。”",
                    )
                ],
            )
            self.assertFalse(created_again)
            self.assertEqual(added_again, 1)

            store.review(entity_id, "rejected")
            _, _, rejected_added = store.upsert_candidate(
                self.collection_id,
                "person",
                "林见深",
                mentions=[
                    extractor.Mention(
                        article_id=2,
                        segment_id=3,
                        surface="林见深",
                        quote="林见深在第二章出现。",
                    )
                ],
            )
            self.assertEqual(rejected_added, 0)
            self.assertEqual(store.entity(entity_id)["status"], "rejected")
        finally:
            store.close()

    def test_manual_add_merge_and_approved_stats(self):
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            source_id = store.manual_add(self.collection_id, "place", "老街")
            target_id = store.manual_add(self.collection_id, "place", "云栖老街")
            store.upsert_candidate(
                self.collection_id,
                "place",
                "云栖老街",
                mentions=[
                    extractor.Mention(
                        article_id=1,
                        segment_id=1,
                        surface="老街",
                        quote="他们走进老街。",
                    )
                ],
            )
            store.merge(source_id, target_id)
            self.assertIsNone(store.entity(source_id))
            target = store.entity(target_id)
            self.assertEqual(target["status"], "approved")
            self.assertIn("老街", target["aliases"])
            stats = store.approved_stats(self.collection_id)
            self.assertEqual(stats["entities"], 1)
            self.assertEqual(stats["mentions"], 1)
        finally:
            store.close()

    def test_reclassify_keeps_aliases_and_mentions(self):
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            entity_id, _, _ = store.upsert_candidate(
                self.collection_id,
                "place",
                "江寒川",
                aliases=("江哥",),
                mentions=[
                    extractor.Mention(
                        article_id=1,
                        segment_id=1,
                        surface="江寒川",
                        quote="江寒川说：“走。”",
                    )
                ],
            )
            new_id = store.reclassify(entity_id, "person")
            self.assertEqual(new_id, entity_id)
            entity = store.entity(entity_id)
            self.assertEqual(entity["kind"], "person")
            self.assertIn("江哥", entity["aliases"])
            self.assertEqual(len(store.mentions(entity_id)), 1)
        finally:
            store.close()

    def test_reclassify_merges_same_name_and_keeps_approved_status(self):
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            target_id = store.manual_add(self.collection_id, "person", "江寒川")
            source_id, _, _ = store.upsert_candidate(
                self.collection_id,
                "place",
                "江寒川",
                mentions=[
                    extractor.Mention(
                        article_id=1,
                        segment_id=1,
                        surface="江寒川",
                        quote="江寒川走进休息舱。",
                    )
                ],
            )
            new_id = store.reclassify(source_id, "person")
            self.assertEqual(new_id, target_id)
            self.assertIsNone(store.entity(source_id))
            target = store.entity(target_id)
            self.assertEqual(target["status"], "approved")
            self.assertEqual(len(store.mentions(target_id)), 1)
        finally:
            store.close()

    def test_reclassify_rejects_conflicting_alias(self):
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            target_id = store.manual_add(self.collection_id, "person", "林见深")
            store.conn.execute(
                "INSERT INTO topic_aliases(entity_id, alias) VALUES (?,?)",
                (target_id, "江哥"),
            )
            store.conn.commit()
            source_id, _, _ = store.upsert_candidate(
                self.collection_id,
                "place",
                "江寒川",
                aliases=("江哥",),
            )
            with self.assertRaises(ValueError):
                store.reclassify(source_id, "person")
        finally:
            store.close()


class TopicTreeTests(TopicFixture):
    def test_curate_allows_single_char_and_keeps_group_meta(self):
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            entity_id = store.curate_entity(
                self.collection_id,
                "organization",
                "殄",
                summary="猎杀型程序。",
                aliases=("殄灭",),
                group_key="ai_program",
                sort_order=3,
                meta={"kind": "program"},
            )
            entity = store.entity(entity_id)
            self.assertEqual(entity["kind"], "organization")
            self.assertEqual(entity["group_key"], "ai_program")
            self.assertEqual(entity["sort_order"], 3)
            self.assertEqual(entity["meta"]["kind"], "program")
            self.assertEqual(entity["status"], "approved")
            self.assertIn("殄灭", entity["aliases"])
        finally:
            store.close()

    def test_article_note_and_tags_drive_chapter_context(self):
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            person_id = store.curate_entity(
                self.collection_id,
                "person",
                "苏晚晴",
                summary="主视角。",
                group_key="core",
            )
            mystery_id = store.curate_entity(
                self.collection_id,
                "mystery",
                "天书的来历",
                summary="天书是什么？",
                group_key="mystery_origin",
            )
            store.upsert_candidate(
                self.collection_id,
                "person",
                "苏晚晴",
                mentions=[
                    extractor.Mention(
                        article_id=1,
                        segment_id=7,
                        surface="苏晚晴",
                        quote="苏晚晴翻开天书。",
                    )
                ],
            )
            store.set_article_note(self.collection_id, 1, "苏晚晴初窥天书。")
            store.tag_article(
                self.collection_id, 1, mystery_id, note="天书首次出现。", sort_order=20
            )
            context = store.article_context(1)
            self.assertEqual(context["note"]["summary"], "苏晚晴初窥天书。")
            self.assertEqual(context["tags"][0]["name"], "天书的来历")
            self.assertEqual(context["tags"][0]["note"], "天书首次出现。")
            names = {row["name"] for row in context["entities"]}
            self.assertIn("苏晚晴", names)
            self.assertEqual(store.entity(person_id)["tag_count"], 0)
            self.assertGreaterEqual(store.entity(mystery_id)["tag_count"], 1)
            store.remove_article_context(self.collection_id, 1)
            empty = store.article_context(1)
            self.assertIsNone(empty["note"])
            self.assertEqual(empty["tags"], [])
            self.assertIsNotNone(store.entity(person_id))
        finally:
            store.close()

    def test_schema_v3_has_tree_and_chapter_tables(self):
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            columns = {
                row[1]
                for row in store.conn.execute("PRAGMA table_info(topic_entities)")
            }
            self.assertTrue(
                {"parent_id", "group_key", "sort_order", "meta_json"} <= columns
            )
            tables = {
                row[0]
                for row in store.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("topic_article_tags", tables)
            self.assertIn("topic_article_notes", tables)
        finally:
            store.close()


class TopicHtmlTests(unittest.TestCase):
    def test_entity_detail_html_renders_percent_width(self):
        from daoyuan.ui_reader.topics_view import entity_detail_html

        html = entity_detail_html(
            {
                "name": "苏晚晴",
                "kind": "person",
                "summary": "主视角。",
                "aliases": ["玲汐"],
                "group_key": "core",
                "mention_count": 1,
                "article_count": 1,
            },
            mentions=[
                {
                    "article_id": 1,
                    "segment_id": 2,
                    "quote": "苏晚晴翻开天书。",
                }
            ],
            article_meta={1: {"chapter": "1", "title": "前导故事"}},
        )
        self.assertIn("苏晚晴", html)
        self.assertIn("width='100%'", html)
        self.assertIn("第 1 章", html)

    def test_article_context_html_renders_story_and_entity_links(self):
        from daoyuan.ui_reader.topics_view import article_context_html

        html = article_context_html(
            {
                "note": {"summary": "苏晚晴初窥天书。"},
                "tags": [
                    {"kind": "arc", "name": "卷一 前传"},
                    {"kind": "mystery", "name": "天书的来历"},
                ],
                "entities": [
                    {"id": 7, "kind": "person", "name": "苏晚晴"},
                    {"id": 8, "kind": "concept", "name": "意识"},
                ],
            },
            {"chapter": "1", "title": "前导故事"},
        )
        self.assertIn("第 1 章", html)
        self.assertIn("苏晚晴初窥天书", html)
        self.assertIn("daoyuan-topic:///7", html)
        self.assertIn("天书的来历", html)


class TopicScannerTests(TopicFixture):
    def setUp(self):
        super().setUp()
        self.article_id = self.add_article(
            "1",
            "云栖老街",
            "林见深说：“天哥，机甲准备好了。”\n"
            "林素问道：“机甲舱在哪？”\n"
            "林素又说：“我去老街。”\n"
            "他们走进云栖老街。\n"
            "林见深说：“天哥，跟上。”\n"
            "林见深说：“林素，走。”\n"
            "林见深说：“去老街。”\n"
            "林见深说：“好。”",
        )

    def test_incremental_scan_skips_unchanged_and_picks_up_changes(self):
        first = self.scan(full=True)
        self.assertEqual(first["ready"], 1)
        self.assertEqual(first["scanned"], 1)
        self.assertEqual(first["skipped"], 0)
        self.assertGreater(first["entities_created"], 0)

        second = self.scan()
        self.assertEqual(second["scanned"], 0)
        self.assertEqual(second["skipped"], 1)

        ArticleRepository(self.conn).mark_ready(
            self.article_id,
            make_parsed(
                "云栖老街",
                "林见深说：“天哥，机甲和芯片都准备好了。”\n"
                "林素问道：“机甲舱在哪？”\n"
                "他们走进云栖老街。",
            ),
        )
        self.conn.commit()
        third = self.scan()
        self.assertEqual(third["scanned"], 1)
        self.assertEqual(third["skipped"], 0)

        store = TopicStore.open(self.config.paths.topics_db, readonly=True)
        try:
            item = store.find_entity(self.collection_id, "item", "芯片")
            self.assertIsNotNone(item)
            stats = store.stats(self.collection_id)
            self.assertEqual(stats["scanned_articles"], 1)
        finally:
            store.close()

    def test_reclassified_known_term_does_not_create_duplicate(self):
        self.add_article(
            "2",
            "X7 Future 公告",
            "X7 Future 发布了新芯片。\nX7 Future 的工程师也在场。",
        )
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            entity_id = store.curate_entity(
                self.collection_id,
                "organization",
                "X7 Future",
                "智体公司。",
            )
        finally:
            store.close()

        self.scan(full=True)

        store = TopicStore.open(self.config.paths.topics_db, readonly=True)
        try:
            rows = store.conn.execute(
                "SELECT id, kind, status FROM topic_entities "
                "WHERE collection_id=? AND name='X7 Future'",
                (self.collection_id,),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "organization")
            self.assertEqual(rows[0]["id"], entity_id)
            self.assertNotIn("X7 Future", store.entity(entity_id)["aliases"])
            self.assertGreater(len(store.mentions(entity_id)), 0)
        finally:
            store.close()

    def test_rejected_entity_stays_rejected_after_rescan(self):
        self.scan(full=True)
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            entity = store.find_entity(self.collection_id, "person", "林素")
            self.assertIsNotNone(entity)
            store.review(entity["id"], "rejected")
            entity_id = entity["id"]
        finally:
            store.close()
        self.scan(full=True)
        store = TopicStore.open(self.config.paths.topics_db, readonly=True)
        try:
            entity = store.entity(entity_id)
            self.assertEqual(entity["status"], "rejected")
            self.assertEqual(store.mentions(entity_id), [])
        finally:
            store.close()

    def test_removed_article_mentions_and_context_are_cleaned(self):
        self.scan(full=True)
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            before = store.stats(self.collection_id)["mentions"]
            self.assertGreater(before, 0)
            arc_id = store.curate_entity(
                self.collection_id,
                "arc",
                "卷一 前传",
                group_key="arc",
            )
            store.set_article_note(self.collection_id, self.article_id, "章节摘要")
            store.tag_article(self.collection_id, self.article_id, arc_id)
        finally:
            store.close()
        conn = connect(self.config.paths.content_db)
        try:
            conn.execute(
                "UPDATE articles SET status='failed' WHERE id=?", (self.article_id,)
            )
            conn.commit()
        finally:
            conn.close()
        result = self.scan()
        self.assertEqual(result["scanned"], 0)
        self.assertGreater(result["mentions_removed"], 0)
        store = TopicStore.open(self.config.paths.topics_db, readonly=True)
        try:
            self.assertEqual(store.stats(self.collection_id)["mentions"], 0)
            self.assertEqual(store.stats(self.collection_id)["scanned_articles"], 0)
            context = store.article_context(self.article_id)
            self.assertIsNone(context["note"])
            self.assertEqual(context["tags"], [])
            self.assertEqual(context["entities"], [])
        finally:
            store.close()

    def test_extractor_version_change_forces_rescan(self):
        self.scan(full=True)
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            store.conn.execute(
                "UPDATE topic_article_scan SET extractor_version=0"
            )
            store.conn.commit()
        finally:
            store.close()
        result = self.scan()
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["skipped"], 0)

    def test_all_collections_stats_count_scanned_articles(self):
        self.scan(full=True)
        store = TopicStore.open(self.config.paths.topics_db, readonly=True)
        try:
            stats = store.stats()
            self.assertEqual(stats["scanned_articles"], 1)
        finally:
            store.close()


class TopicBackupTests(TopicFixture):
    def test_backup_includes_topics_and_restores_review_status(self):
        self.add_article(
            "1",
            "测试",
            "林见深说：“天哥，机甲准备好了。”\n"
            "林见深说：“天哥，跟上。”\n"
            "林见深说：“去老街。”\n"
            "林见深说：“走。”\n"
            "林见深说：“好。”",
        )
        self.scan(full=True)
        store = TopicStore.open(self.config.paths.topics_db)
        try:
            entity = store.find_entity(self.collection_id, "person", "林见深")
            store.review(entity["id"], "approved")
            entity_id = entity["id"]
        finally:
            store.close()

        backup = create_backup(self.config, note="topics")
        package = Path(backup["path"])
        self.assertTrue(backup["valid"], backup["errors"])
        self.assertTrue((package / TOPICS_FILE).exists())
        self.assertEqual(backup["topics"]["counts"]["approved"], 1)
        self.assertEqual(
            inspect_backup(package)["topics"]["counts"]["entities"],
            backup["topics"]["counts"]["entities"],
        )

        store = TopicStore.open(self.config.paths.topics_db)
        try:
            store.review(entity_id, "rejected")
        finally:
            store.close()

        # restore_backup 会替换内容库文件，测试中先释放写连接。
        self.conn.close()
        self.conn = None
        result = restore_backup(self.config, package, restore_state=False)
        self.assertTrue(result["topics_restored"])
        store = TopicStore.open(self.config.paths.topics_db, readonly=True)
        try:
            self.assertEqual(store.entity(entity_id)["status"], "approved")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
