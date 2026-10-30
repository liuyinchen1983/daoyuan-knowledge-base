# -*- coding: utf-8 -*-
"""智问服务编排：加载密钥、召回、调用、引用核验与会话保存。"""
from __future__ import annotations

import re
import sqlite3

from ..core.config import Config
from ..core.errors import AiError, ConfigError, SecurityError
from ..reader.service import ReaderService
from .deepseek import DeepSeekClient
from .key_store import SecretStore
from .rag import answer_question

KEY_FILENAME = "deepseek.key"
CONSENT_KEY = "ai.consent.v1"
MODEL_KEY = "ai.model.v1"
MODEL_CHOICES = ("deepseek-flash", "deepseek-v4-pro")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")


def key_store(config: Config) -> SecretStore:
    return SecretStore(config.paths.secrets / KEY_FILENAME)


def has_api_key(config: Config) -> bool:
    return key_store(config).exists()


def load_api_key(config: Config) -> str | None:
    return key_store(config).load()


def save_api_key(config: Config, key: str) -> None:
    key_store(config).save(key)


def clear_api_key(config: Config) -> None:
    key_store(config).clear()


def validate_model(model: str) -> str:
    value = (model or "").strip()
    if not MODEL_RE.match(value):
        raise ConfigError("模型名称只能包含字母、数字、点、下划线、冒号和短横线，长度 1–80。")
    return value


def effective_model(config: Config, service: ReaderService | None = None) -> str:
    if service is not None:
        saved = service.state.get_setting(MODEL_KEY)
        if saved:
            try:
                return validate_model(saved)
            except ConfigError:
                pass
    return config.ai.model


def save_model_choice(service: ReaderService, model: str) -> str:
    value = validate_model(model)
    service.state.set_setting(MODEL_KEY, value)
    return value


def has_ai_consent(service: ReaderService) -> bool:
    return service.state.get_setting(CONSENT_KEY) == "1"


def set_ai_consent(service: ReaderService, agreed: bool) -> None:
    service.state.set_setting(CONSENT_KEY, "1" if agreed else "0")


def clear_qa_history(service: ReaderService) -> None:
    service.state.clear_qa()


def _next_seq(service: ReaderService, session_id: int) -> int:
    row = service.state.conn.execute(
        "SELECT COALESCE(MAX(seq_no), -1) + 1 FROM qa_messages WHERE session_id=?",
        (session_id,),
    ).fetchone()
    return int(row[0] or 0)


def save_exchange(
    service: ReaderService,
    question: str,
    result: dict,
    session_id: int | None = None,
) -> dict:
    """把一次真实模型回答写入个人状态库；不保存 API Key。"""
    if not result.get("model_used") or not result.get("answer"):
        return {"session_id": session_id}
    session_id = session_id or service.state.create_qa_session(question[:60])
    seq_no = _next_seq(service, session_id)
    service.state.add_qa_message(session_id, "user", question, seq_no)
    usage = result.get("usage") or {}
    message_id = service.state.add_qa_message(
        session_id,
        "assistant",
        result["answer"],
        seq_no + 1,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )
    service.state.log_ai_usage(
        "ask",
        result.get("model") or "",
        usage,
        session_id=session_id,
    )
    evidence = {item["label"]: item for item in result.get("evidence") or []}
    for citation in result.get("citations") or []:
        item = evidence.get(citation.get("label"))
        service.state.add_qa_citation(
            message_id,
            citation.get("label") or "",
            citation.get("article_id"),
            citation.get("segment_id"),
            (item or {}).get("text", citation.get("snippet"))[:240]
            if (item or citation.get("snippet"))
            else None,
        )
    return {"session_id": session_id, "message_id": message_id}


def run_ask(
    config: Config,
    question: str,
    history: list[dict] | None = None,
    previous_question: str | None = None,
    session_id: int | None = None,
    client: DeepSeekClient | None = None,
    local_only: bool = False,
) -> dict:
    """在线程中使用的完整入口：自建数据库连接，结束后关闭。"""
    key_error = ""
    api_key = None
    try:
        api_key = load_api_key(config)
    except SecurityError as exc:
        key_error = str(exc)

    service = ReaderService(config)
    try:
        result = answer_question(
            service,
            config,
            question,
            api_key,
            history=history,
            previous_question=previous_question,
            client=client,
            local_only=local_only,
            model=effective_model(config, service),
        )
        if key_error:
            result["key_error"] = key_error
            if not result.get("error"):
                result["error"] = key_error
        if result.get("model_used"):
            try:
                saved = save_exchange(service, result["question"], result, session_id)
                result.update(saved)
            except (sqlite3.Error, OSError) as exc:
                result["save_warning"] = "回答已生成，但本地问答记录保存失败：%s" % exc
        return result
    finally:
        service.close()


def test_api_key(config: Config, api_key: str, model: str | None = None) -> dict:
    client = DeepSeekClient(
        base_url=config.ai.base_url,
        model=validate_model(model or config.ai.model),
        api_key=api_key,
        timeout=min(float(config.ai.timeout), 30.0),
    )
    try:
        return client.ping()
    except AiError:
        raise
