# -*- coding: utf-8 -*-
"""道源知识库（普通用户端）PyInstaller 入口。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_source_path() -> None:
    if getattr(sys, "frozen", False):
        return
    root = Path(__file__).resolve().parents[1]
    for folder in (root / "src", root / "packaging"):
        if folder.is_dir() and str(folder) not in sys.path:
            sys.path.insert(0, str(folder))


def main() -> int:
    _bootstrap_source_path()
    if os.environ.get("DAOYUAN_PACKAGE_SMOKE", "").strip().lower() == "reader":
        from smoke_reader import main as smoke_main

        return smoke_main()
    from daoyuan.ui_reader.app import main as reader_main

    return reader_main()


if __name__ == "__main__":
    raise SystemExit(main())
