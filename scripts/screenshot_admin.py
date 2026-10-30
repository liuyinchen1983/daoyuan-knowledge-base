# -*- coding: utf-8 -*-
"""离屏渲染管理端页面，供界面检查。不访问网络。

开发机脚本：读取当前配置的数据目录，并向 snapshots/ 写入截图。
请先备份或通过 DAOYUAN_CONFIG 指向测试数据，不要直接对生产数据运行。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daoyuan.core.config import load_config  # noqa: E402
from daoyuan.ui_admin.app import AdminWindow  # noqa: E402
from daoyuan.ui_common.theme import apply_theme  # noqa: E402


def main() -> int:
    config = load_config()
    config.paths.ensure()
    app = QApplication([])
    apply_theme(app)
    window = AdminWindow(config)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.resize(1240, 820)
    window.show()
    app.processEvents()
    window.switch("tasks")
    app.processEvents()
    target = config.paths.snapshots / "admin_tasks.png"
    window.grab().save(str(target))
    print("saved:", target)
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
