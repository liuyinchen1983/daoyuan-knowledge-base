# -*- coding: utf-8 -*-
import hashlib
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

from daoyuan.core.config import load_config
from daoyuan.core.lock import ProcessLock
from daoyuan.core.models import ListingItem, ParsedArticle
from daoyuan.core.schedule import (
    build_task_command,
    compute_schedule_status,
    display_command,
    effective_schedule,
    save_schedule,
)
from daoyuan.core.timeutil import BEIJING
from daoyuan.reader.service import ReaderService
from daoyuan.storage.backup import (
    CONTENT_FILE,
    MANIFEST,
    STATE_FILE,
    create_backup,
    inspect_backup,
    list_backups,
    restore_backup,
)
from daoyuan.storage.db import connect, initialize_content_db
from daoyuan.storage.repositories import (
    ArticleRepository,
    CollectionRepository,
    SourceRepository,
)
from daoyuan.storage.state_db import StateStore


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


class BackupRestoreTests(unittest.TestCase):
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
        self.article_id, _ = ArticleRepository(conn).upsert_discovered(
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
            self.article_id, make_parsed("修行之路漫长。\n见深与天哥同行。")
        )
        conn.close()
        store = StateStore(self.config.paths.state_db)
        store.save_progress(self.article_id, None, 10, 42.0)
        store.close()
        # 密钥文件绝不能被备份包收录。
        (self.config.paths.secrets / "deepseek.key").write_bytes(b"dummy-secret")

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_preview_and_restore_content_preserves_state(self):
        backup = create_backup(self.config, note="test")
        package = Path(backup["path"])
        self.assertTrue(backup["valid"], backup["errors"])
        self.assertEqual(
            {p.name for p in package.iterdir()},
            {MANIFEST, CONTENT_FILE, STATE_FILE},
        )
        self.assertNotIn(b"dummy-secret", (package / CONTENT_FILE).read_bytes())
        self.assertEqual(inspect_backup(package)["content"]["ready"], 1)
        self.assertEqual(list_backups(self.config)[0]["path"], str(package))

        conn = connect(self.config.paths.content_db)
        conn.execute("UPDATE articles SET title='已被修改' WHERE id=?", (self.article_id,))
        conn.commit()
        conn.close()
        store = StateStore(self.config.paths.state_db)
        store.save_progress(self.article_id, None, 20, 77.0)
        store.close()

        result = restore_backup(self.config, package, restore_state=False)
        self.assertTrue(result["safety_backup"])
        service = ReaderService(self.config)
        try:
            self.assertEqual(service.article(self.article_id)["title"], "见深初见 2026.10.05")
            self.assertEqual(service.search("见深")["total_articles"], 1)
            progress = service.state.get_progress(self.article_id)
            self.assertEqual(progress["percent"], 77.0)
        finally:
            service.close()

    def test_reader_service_reopen_after_content_import(self):
        backup = create_backup(self.config)
        service = ReaderService(self.config)
        try:
            self.assertTrue(service.available)
            self.assertEqual(
                service.article(self.article_id)["title"], "见深初见 2026.10.05"
            )
            conn = connect(self.config.paths.content_db)
            try:
                conn.execute(
                    "UPDATE articles SET title='导入前临时标题' WHERE id=?",
                    (self.article_id,),
                )
                conn.commit()
            finally:
                conn.close()
            service.close()
            restore_backup(self.config, backup["path"], restore_state=False)
            service.reopen()
            self.assertTrue(service.available)
            self.assertEqual(
                service.article(self.article_id)["title"], "见深初见 2026.10.05"
            )
            self.assertEqual(service.search("见深")["total_articles"], 1)
        finally:
            service.close()

    def test_restore_state_when_requested(self):
        backup = create_backup(self.config)
        store = StateStore(self.config.paths.state_db)
        store.save_progress(self.article_id, None, 30, 5.0)
        store.close()
        restore_backup(self.config, backup["path"], restore_state=True)
        store = StateStore(self.config.paths.state_db)
        try:
            self.assertEqual(store.get_progress(self.article_id)["percent"], 42.0)
        finally:
            store.close()

    def test_invalid_manifest_hash_is_rejected(self):
        backup = create_backup(self.config)
        package = Path(backup["path"])
        manifest = package / MANIFEST
        text = manifest.read_text(encoding="utf-8").replace(
            backup["content"]["sha256"], "0" * 64
        )
        manifest.write_text(text, encoding="utf-8")
        info = inspect_backup(package)
        self.assertFalse(info["valid"])
        self.assertTrue(any("哈希" in error for error in info["errors"]))


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config_path = root / "config.toml"
        config_path.write_text(
            '[app]\ndata_dir = "%s"\n[schedule]\nenabled = true\n'
            'interval_days = 1\ndaily_time = "09:00"\n'
            % (root / "runtime").as_posix(),
            encoding="utf-8",
        )
        self.config = load_config(config_path)
        self.config.paths.ensure()
        self.conn = initialize_content_db(self.config.paths.content_db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add_run(self, finished_at):
        self.conn.execute(
            "INSERT INTO collect_runs(mode, status, started_at, finished_at) "
            "VALUES ('update','completed',?,?)",
            (finished_at, finished_at),
        )
        self.conn.commit()

    def test_due_calculation(self):
        now = datetime(2026, 10, 30, 10, 0, tzinfo=BEIJING)
        status = compute_schedule_status(self.config, self.conn, now)
        self.assertTrue(status["due"])
        self.add_run("2026-10-30T08:00:00+08:00")
        status = compute_schedule_status(self.config, self.conn, now)
        self.assertFalse(status["due"])
        self.assertTrue(status["next_due_at"].startswith("2026-10-31T09:00:00"))

        other = tempfile.TemporaryDirectory()
        try:
            root = Path(other.name)
            config_path = root / "config.toml"
            config_path.write_text(
                '[app]\ndata_dir = "%s"\n' % (root / "runtime").as_posix(),
                encoding="utf-8",
            )
            config2 = load_config(config_path)
            config2.paths.ensure()
            conn2 = initialize_content_db(config2.paths.content_db)
            conn2.execute(
                "INSERT INTO collect_runs(mode, status, started_at, finished_at) "
                "VALUES ('update','completed',?,?)",
                ("2026-10-29T08:00:00+08:00", "2026-10-29T08:00:00+08:00"),
            )
            conn2.commit()
            status2 = compute_schedule_status(config2, conn2, now)
            self.assertTrue(status2["due"])
            self.assertTrue(status2["next_due_at"].startswith("2026-10-30T09:00:00"))
            conn2.close()
        finally:
            other.cleanup()

    def test_disabled_and_settings_persistence(self):
        save_schedule(self.config, False, 3, "08:30")
        effective = effective_schedule(self.config)
        self.assertFalse(effective["enabled"])
        self.assertEqual(effective["interval_days"], 3)
        self.assertEqual(effective["daily_time"], "08:30")
        self.assertEqual(effective["collection_ids"], [])
        status = compute_schedule_status(
            self.config,
            self.conn,
            datetime(2026, 10, 30, 10, 0, tzinfo=BEIJING),
            schedule=effective,
        )
        self.assertFalse(status["due"])

    def test_task_command_is_preview_only(self):
        command = build_task_command(
            self.config,
            {"enabled": True, "interval_days": 1, "daily_time": "09:00"},
        )
        joined = " ".join(command)
        self.assertIn("schtasks", command[0])
        self.assertIn("--update", joined)
        self.assertIn("--if-due", joined)
        self.assertIn("--max-fetch 20", joined)
        self.assertIn("/ST 09:00", joined)
        self.assertIn("/MO 1", joined)
        self.assertNotIn("--collection-id", joined)
        preview = display_command(command)
        self.assertIn("/TR '", preview)
        self.assertNotIn('\\"', preview)

    def test_schedule_scope_persists_and_limits_task(self):
        save_schedule(self.config, True, 2, "17:30", [1])
        effective = effective_schedule(self.config)
        self.assertTrue(effective["enabled"])
        self.assertEqual(effective["interval_days"], 2)
        self.assertEqual(effective["daily_time"], "17:30")
        self.assertEqual(effective["collection_ids"], [1])
        command = build_task_command(self.config)
        joined = " ".join(command)
        self.assertIn("--collection-id 1", joined)
        self.assertIn("/ST 17:30", joined)
        self.assertIn("/MO 2", joined)
        # 改回全部文集后，任务命令不再带范围限制
        save_schedule(self.config, True, 2, "17:30", [])
        self.assertEqual(effective_schedule(self.config)["collection_ids"], [])
        self.assertNotIn(
            "--collection-id", " ".join(build_task_command(self.config))
        )

    @unittest.skipUnless(os.name == "nt", "命名 Mutex 仅 Windows")
    def test_process_lock_excludes_second_instance(self):
        name = "Local\\DaoyuanTestLock_%s" % uuid.uuid4().hex
        first = ProcessLock(name)
        second = ProcessLock(name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            first.release()
            second.release()
        third = ProcessLock(name)
        try:
            self.assertTrue(third.acquire())
        finally:
            third.release()


if __name__ == "__main__":
    unittest.main()
