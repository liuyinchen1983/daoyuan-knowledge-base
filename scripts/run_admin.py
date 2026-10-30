# -*- coding: utf-8 -*-
"""源码方式启动维护端：无参数打开界面，带参数按命令行模式执行。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    args = sys.argv[1:]
    if args and args[0].startswith("-"):
        from daoyuan.cli.main import main as cli_main

        return cli_main(args)
    from daoyuan.ui_admin.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
