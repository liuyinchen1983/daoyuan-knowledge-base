# -*- coding: utf-8 -*-
"""离屏启动两套窗口，验证 M0 骨架可实例化。不访问网络，不触碰真实数据目录。"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from daoyuan.core.config import load_config  # noqa: E402
from daoyuan.storage.db import initialize_content_db  # noqa: E402
from daoyuan.storage.state_db import StateStore  # noqa: E402
from daoyuan.ui_common.theme import apply_theme  # noqa: E402
from daoyuan.ui_admin.app import AdminWindow  # noqa: E402
from daoyuan.ui_reader.app import ReaderWindow  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.toml"
        runtime = Path(tmp) / "runtime"
        config_path.write_text(
            '[app]\ndata_dir = "%s"\n' % str(runtime).replace("\\", "/"),
            encoding="utf-8",
        )
        cfg = load_config(config_path)
        cfg.paths.ensure()
        initialize_content_db(cfg.paths.content_db).close()
        StateStore(cfg.paths.state_db).close()

        app = QApplication([])
        apply_theme(app)

        reader = ReaderWindow(cfg)
        admin = AdminWindow(cfg)
        try:
            reader.show()
            app.processEvents()
            assert reader.pages["home"] is not None
            assert reader.stack.count() == len(ReaderWindow.NAV)
            reader.close()

            admin.show()
            app.processEvents()
            assert admin.stack.count() == len(AdminWindow.NAV)
            admin.close()
            app.processEvents()
        finally:
            reader.service.close()
            admin.service.close()
    print("UI_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
