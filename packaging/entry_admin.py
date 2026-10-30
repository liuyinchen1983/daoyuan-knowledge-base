# -*- coding: utf-8 -*-
"""道源采集管理（维护端）PyInstaller 入口。

无参数启动 GUI；带第一个以 "-" 开头的参数时走 CLI。CLI 分支先恢复标准输出，
之后才导入 CLI/采集模块，避免计划任务启动 Qt 或弹出窗口。
"""
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


def _detach_console_for_gui() -> None:
    """控制台子系统保证 CLI 可被 PowerShell/cmd 等待并捕获输出；

    双击启动 GUI 时释放自动分配的黑色控制台窗口。从终端启动 GUI 时只脱离
    调用方控制台，不关闭、不修改用户终端。
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        ctypes.windll.kernel32.FreeConsole()
    except (AttributeError, OSError):
        pass


def main() -> int:
    _bootstrap_source_path()
    if os.environ.get("DAOYUAN_PACKAGE_SMOKE", "").strip().lower() == "admin":
        from smoke_admin import main as smoke_main

        return smoke_main()
    if len(sys.argv) > 1 and sys.argv[1].startswith("-"):
        from daoyuan.core.console import enable_cli_streams

        enable_cli_streams()
        from daoyuan.cli.main import main as cli_main

        return cli_main(sys.argv[1:])
    _detach_console_for_gui()
    from daoyuan.ui_admin.app import main as admin_main

    return admin_main()


if __name__ == "__main__":
    raise SystemExit(main())
