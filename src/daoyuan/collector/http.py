# -*- coding: utf-8 -*-
"""公众号公开页面 HTTP 客户端（仅标准库）。"""
from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..core.config import CollectorSettings
from ..core.errors import (
    ArticleUnavailableError,
    CollectorError,
    RequestBusyError,
    RiskControlError,
)
from ..core.lock import RequestGate
from .cooldown import default_paths, note_request_start, request_wait_seconds

REFERER = "https://mp.weixin.qq.com/"


class HttpClient:
    def __init__(
        self,
        settings: CollectorSettings,
        request_gate: RequestGate | None = None,
        *,
        pacing: bool = True,
        state_dir: Path | None = None,
    ):
        self.settings = settings
        self.request_gate = request_gate or RequestGate()
        # 自动采集在请求前等满 10–18 秒间隔；单篇链接采集由界面先检查，
        # 避免在 GUI 线程里 sleep，这里只传 pacing=False。
        self.pacing = pacing
        self.cooldown_path, self.interval_path = default_paths(state_dir)

    def get(self, url: str) -> tuple[str, bytes]:
        if self.pacing:
            wait = request_wait_seconds(self.interval_path)
            if wait > 0:
                time.sleep(wait)
        # 自动采集、合辑列表和单篇链接采集共用同一个请求互斥锁。
        # 拿到锁才发请求；等待超时说明另一路正在请求，宁可稍后重试。
        if not self.request_gate.acquire():
            raise RequestBusyError("另一个采集请求正在进行，请稍后再试。")
        try:
            # 无论自动还是手动，只要真正发出请求，就刷新全局间隔，
            # 让另一个入口也不会紧跟在这条请求后面。
            note_request_start(self.interval_path)
            return self._get(url)
        finally:
            self.request_gate.release()

    def _get(self, url: str) -> tuple[str, bytes]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.settings.user_agent,
                "Referer": REFERER,
                "Accept": "text/html,application/json,*/*",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.settings.timeout) as resp:
                raw = resp.read()
                if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
                    raw = gzip.decompress(raw)
                return resp.geturl(), raw
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise RiskControlError("HTTP %s: %s" % (exc.code, exc.reason)) from exc
            if exc.code in (404, 410):
                raise ArticleUnavailableError(
                    "HTTP %s: %s" % (exc.code, exc.reason)
                ) from exc
            raise CollectorError("HTTP %s: %s" % (exc.code, exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise CollectorError("网络错误：%s" % exc.reason) from exc
        except TimeoutError as exc:
            raise CollectorError("请求超时") from exc

    def get_text(self, url: str) -> tuple[str, str]:
        final_url, raw = self.get(url)
        return final_url, raw.decode("utf-8", errors="replace")
