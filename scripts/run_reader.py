# -*- coding: utf-8 -*-
"""源码方式启动普通用户端。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from daoyuan.ui_reader.app import main  # noqa: E402

raise SystemExit(main())
