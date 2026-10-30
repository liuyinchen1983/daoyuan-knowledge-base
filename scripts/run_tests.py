# -*- coding: utf-8 -*-
"""运行测试：python scripts/run_tests.py [-v]"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    verbosity = 2 if "-v" in sys.argv else 1
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
