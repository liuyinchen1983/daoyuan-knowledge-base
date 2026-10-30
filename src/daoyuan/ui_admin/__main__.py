# -*- coding: utf-8 -*-
"""维护端入口：带参数时走命令行，无参数时启动图形界面。"""
import sys

# 先分发 CLI，避免计划任务运行时加载 Qt / 创建窗口。
if len(sys.argv) > 1 and sys.argv[1].startswith("-"):
    from ..cli.main import main as cli_main

    raise SystemExit(cli_main(sys.argv[1:]))

from .app import main  # noqa: E402

raise SystemExit(main())
