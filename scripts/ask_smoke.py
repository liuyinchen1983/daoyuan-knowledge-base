# -*- coding: utf-8 -*-
"""智问 UI 冒烟：真实本地召回 + 模拟引用渲染，不访问网络、不写入 API Key。

开发机脚本：读取当前配置的数据目录，并向 snapshots/ 写入截图。
请先备份或通过 DAOYUAN_CONFIG 指向测试数据，不要直接对生产数据运行。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daoyuan.ai.service import has_api_key  # noqa: E402
from daoyuan.core.config import load_config  # noqa: E402
from daoyuan.ui_common.theme import apply_theme  # noqa: E402
from daoyuan.ui_reader.app import ApiKeyDialog, ReaderWindow  # noqa: E402


def pump(app: QApplication, seconds: float = 0.6) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.03)


def main() -> int:
    config = load_config()
    config.paths.ensure()
    app = QApplication([])
    apply_theme(app)

    window = ReaderWindow(config)
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.show()
    pump(app)
    window.switch("ask")
    pump(app)

    page = window.pages["ask"]
    snapshots = config.data_dir / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(snapshots / "reader_ask.png"))
    print("智问首页截图：%s" % (snapshots / "reader_ask.png"))
    print("Key 状态：%s" % ("已配置" if has_api_key(config) else "未配置"))

    question = "守门老者的故事说明了什么"
    page.question.setText(question)
    page.start_ask(local_only=True)
    deadline = time.time() + 30
    while page.worker is not None and time.time() < deadline:
        pump(app, 0.05)
    pump(app, 0.6)
    print("本地智问状态：%s" % page.status.text())
    print("本地候选段数：%d" % page.evidence_list.count())
    print("本地回答：%s" % page.answer_view.toPlainText()[:120].replace("\n", " "))
    if page.evidence_list.count() == 0:
        print("本地召回为空，无法继续验证")
        window.close()
        return 2
    window.grab().save(str(snapshots / "reader_ask_local.png"))
    print("本地召回截图：%s" % (snapshots / "reader_ask_local.png"))

    # 直接构造一个和 answer_question 返回结构一致的结果，验证安全渲染与引用。
    first = page.last_result["evidence"][0]
    fake = {
        "question": question,
        "terms": page.last_result.get("terms") or [],
        "evidence": page.last_result["evidence"],
        "answer": "从现有段落看，守门老者在山门前等候。[C1]\n依据等级：原文直接支持\n<script>alert(1)</script>",
        "citations": [
            {
                "label": "C1",
                "article_id": first["article_id"],
                "segment_id": first["segment_id"],
                "title": first["title"],
                "publish_date": first["publish_date"],
                "snippet": first["text"][:120],
            }
        ],
        "invalid_citations": ["C9"],
        "uncited": False,
        "confidence": "原文直接支持",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
        "model_used": True,
        "failure_reason": "",
        "error": "",
    }
    page._render_result(fake)
    pump(app, 0.5)
    rendered = page.answer_view.toHtml()
    print("引用链接：%s" % ("含 cite:C1" if "cite:C1" in rendered else "缺失"))
    print("脚本是否被转义：%s" % ("是" if "&lt;script&gt;" in rendered or "script" not in rendered.lower() else "否"))
    if "cite:C1" not in rendered:
        window.close()
        return 3
    if "[C9]" in page.answer_view.toPlainText() and "cite:C9" in rendered:
        print("无效引用被错误链接")
        window.close()
        return 4
    window.grab().save(str(snapshots / "reader_ask_answer.png"))
    print("模拟回答截图：%s" % (snapshots / "reader_ask_answer.png"))

    dialog = ApiKeyDialog(config, window, service=window.service)
    dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
    dialog.show()
    pump(app, 0.5)
    dialog.grab().save(str(snapshots / "reader_api_key.png"))
    print("Key 配置截图：%s" % (snapshots / "reader_api_key.png"))
    dialog.close()
    pump(app, 0.3)

    window.close()
    pump(app)
    print("ASK_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
