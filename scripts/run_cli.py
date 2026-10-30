# -*- coding: utf-8 -*-
"""源码方式运行维护端命令行。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from daoyuan.cli.main import main  # noqa: E402

raise SystemExit(main())
