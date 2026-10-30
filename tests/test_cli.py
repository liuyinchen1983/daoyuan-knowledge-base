# -*- coding: utf-8 -*-
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from daoyuan.cli.main import main
from daoyuan.core.config import load_config
from daoyuan.core.lock import ProcessLock
from daoyuan.core.schedule import save_schedule
from daoyuan.core.timeutil import now_iso
from daoyuan.storage.db import connect, initialize_content_db, initialize_state_db


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config_path = root / "config.toml"
        self.config_path.write_text(
            '[app]\ndata_dir = "%s"\n' % (root / "runtime").as_posix(),
            encoding="utf-8",
        )
        self.config = load_config(self.config_path)
        self.config.paths.ensure()
        initialize_content_db(self.config.paths.content_db).close()
        initialize_state_db(self.config.paths.state_db).close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(list(args) + ["--config", str(self.config_path)])
        return code, output.getvalue() + errors.getvalue()

    def test_health_and_schedule_status(self):
        code, output = self.run_cli("--health")
        self.assertEqual(code, 0, output)
        self.assertIn("RESULT: OK", output)
        code, output = self.run_cli("--schedule-status")
        self.assertEqual(code, 0, output)
        self.assertIn("自动采集：", output)

    def test_backup_list_and_restore_preview(self):
        code, output = self.run_cli("--backup")
        self.assertEqual(code, 0, output)
        self.assertIn("备份完成", output)
        backups = list((self.config.paths.backups).iterdir())
        self.assertEqual(len(backups), 1)
        backup = backups[0]
        self.assertTrue((backup / "manifest.json").exists())

        code, output = self.run_cli("--list-backups")
        self.assertEqual(code, 0, output)
        self.assertIn("OK", output)
        self.assertIn(backup.name, output)

        code, output = self.run_cli("--restore", str(backup))
        self.assertEqual(code, 2, output)
        self.assertIn("预览", output)

    def test_if_due_skips_without_network_when_not_due(self):
        save_schedule(self.config, True, 1, "00:00")
        conn = connect(self.config.paths.content_db)
        stamp = now_iso()
        conn.execute(
            "INSERT INTO collect_runs(mode, status, started_at, finished_at) "
            "VALUES ('update','completed',?,?)",
            (stamp, stamp),
        )
        conn.commit()
        conn.close()
        code, output = self.run_cli("--update", "--if-due")
        self.assertEqual(code, 0, output)
        self.assertIn("未到检查时间", output)

    def test_pacing_overrides_are_validated(self):
        code, output = self.run_cli(
            "--update", "--delay-min", "7", "--max-fetch", "1"
        )
        self.assertEqual(code, 2, output)
        self.assertIn("不能小于 8 秒", output)
        code, output = self.run_cli(
            "--update", "--delay-min", "12", "--delay-max", "10", "--max-fetch", "1"
        )
        self.assertEqual(code, 2, output)
        self.assertIn("不能小于 --delay-min", output)

    def test_conflicting_modes_are_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("--update", "--full")
        self.assertEqual(raised.exception.code, 2)

    @unittest.skipUnless(os.name == "nt", "命名 Mutex 仅 Windows")
    def test_collection_refuses_when_lock_is_held(self):
        lock = ProcessLock()
        self.assertTrue(lock.acquire())
        try:
            code, output = self.run_cli("--list-only")
        finally:
            lock.release()
        self.assertEqual(code, 3, output)
        self.assertIn("另一个采集任务", output)

    @unittest.skipUnless(os.name == "nt", "命名 Mutex 仅 Windows")
    def test_restore_refuses_when_lock_is_held(self):
        self.run_cli("--backup")
        backup = next(self.config.paths.backups.iterdir())
        lock = ProcessLock()
        self.assertTrue(lock.acquire())
        try:
            code, output = self.run_cli("--restore", str(backup), "--yes")
        finally:
            lock.release()
        self.assertEqual(code, 3, output)
        self.assertIn("不能同时恢复", output)


if __name__ == "__main__":
    unittest.main()
