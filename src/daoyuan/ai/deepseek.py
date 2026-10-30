# -*- coding: utf-8 -*-
"""DeepSeek Chat Completions 客户端（标准库实现，便于打包）。"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Callable

from ..core.errors import AiError


def chat_endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = "https://api.deepseek.com"
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _server_message(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message.strip()[:200]
    message = payload.get("message")
    return message.strip()[:200] if isinstance(message, str) else ""


def _http_error(exc: urllib.error.HTTPError) -> AiError:
    code = int(getattr(exc, "code", 0) or 0)
    try:
        detail = _server_message(exc.read())
    except Exception:
        detail = ""
    if code == 401:
        return AiError("API Key 无效或已失效，请在「配置 Key」中重新填写。")
    if code == 402:
        return AiError("DeepSeek 账户余额不足，请充值后重试。")
    if code == 403:
        return AiError("DeepSeek 拒绝了本次请求（HTTP 403），请检查账号或模型权限。")
    if code == 404:
        return AiError("DeepSeek 接口地址或模型名称不正确（HTTP 404）。")
    if code == 429:
        return AiError("DeepSeek 请求过于频繁，请稍后再试。")
    if 500 <= code <= 599:
        return AiError("DeepSeek 服务暂时不可用（HTTP %d），请稍后重试。" % code)
    suffix = "：%s" % detail if detail else ""
    return AiError("DeepSeek 请求失败（HTTP %d）%s" % (code, suffix))


class DeepSeekClient:
    """只负责一次 chat/completions 调用；提示词与证据由 rag 层构造。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = 60.0,
        transport: Callable | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout)
        self._transport = transport or urllib.request.urlopen

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> dict:
        if not self.api_key:
            raise AiError("尚未配置 DeepSeek API Key。")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        request = urllib.request.Request(
            chat_endpoint(self.base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer " + self.api_key,
                # 固定 User-Agent，便于服务端识别；不包含任何本机信息。
                "User-Agent": "DaoyuanKnowledgeBase/1.0",
            },
            method="POST",
        )
        try:
            with self._transport(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise AiError("连接 DeepSeek 超时，请检查网络后重试。") from exc
            raise AiError("无法连接 DeepSeek，请检查网络、代理或接口地址。") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise AiError("连接 DeepSeek 超时，请检查网络后重试。") from exc
        except OSError as exc:
            raise AiError("网络请求失败：%s" % exc) from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AiError("DeepSeek 返回的内容无法解析。") from exc
        if not isinstance(data, dict):
            raise AiError("DeepSeek 返回格式无法识别。")
        if data.get("error"):
            message = _server_message(raw)
            raise AiError(
                "DeepSeek 返回错误%s"
                % ("：%s" % message if message else "，请稍后重试。")
            )
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiError("DeepSeek 未返回有效回答。") from exc
        finish_reason = choice.get("finish_reason") or ""
        if not isinstance(content, str) or not content.strip():
            if finish_reason == "length":
                raise AiError(
                    "模型思考过程占满了本次回答额度，正文未能生成。"
                    "请重试，或在 config.toml 中调大 max_answer_tokens。"
                )
            raise AiError("DeepSeek 未返回有效回答。")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return {
            "content": content.strip(),
            "model": data.get("model") or self.model,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
        }

    def ping(self) -> dict:
        """低 Token 的连通性测试，不发送本地文章内容。

        deepseek-flash 会先输出 reasoning_content；16 tokens 可能被思考过程
        全部占用，导致 content 为空。留出余量，确保连接测试能拿到正文。
        """
        return self.chat(
            [
                {
                    "role": "user",
                    "content": "这是连接测试，请只回复四个字：连接正常",
                }
            ],
            temperature=0,
            max_tokens=128,
        )
