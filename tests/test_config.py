# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from daoyuan.core.config import DEFAULT_DATA_DIR, load_config, validate_config
from daoyuan.core.errors import ConfigError


class ConfigTests(unittest.TestCase):
    def test_default_config_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            cfg = load_config(path)
            self.assertTrue(path.exists())
            self.assertEqual(cfg.data_dir, DEFAULT_DATA_DIR.resolve())
            self.assertEqual(cfg.collector.delay_min, 8.0)
            self.assertEqual(cfg.collector.max_fetch_per_run, 150)
            self.assertEqual(cfg.schedule.interval_days, 1)
            self.assertEqual(cfg.ai.max_answer_tokens, 8192)

    def test_relative_data_dir_resolves_to_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                '[app]\ndata_dir = "runtime"\n[collector]\ndelay_min = 9\ndelay_max = 12\n',
                encoding="utf-8",
            )
            cfg = load_config(path)
            self.assertEqual(cfg.data_dir, (Path(tmp) / "runtime").resolve())
            self.assertEqual(cfg.paths.content_db, cfg.data_dir / "content" / "knowledge.db")
            self.assertEqual(cfg.paths.state_db, cfg.data_dir / "state" / "user_state.db")

    def test_invalid_delay_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[collector]\ndelay_min = 1\ndelay_max = 2\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_answer_tokens_can_be_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[ai]\nmax_answer_tokens = 8000\n", encoding="utf-8")
            cfg = load_config(path)
            self.assertEqual(cfg.ai.max_answer_tokens, 8000)

    def test_tiny_answer_tokens_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[ai]\nmax_answer_tokens = 128\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_invalid_daily_time_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[schedule]\ndaily_time = "25:00"\n', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
