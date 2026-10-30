# -*- coding: utf-8 -*-
"""本地证据召回、提示词构造与 [Cx] 引用核验。

原则：先检索本地段落，只把限量证据发给模型；模型不能凭空创造可靠出处。
"""
from __future__ import annotations

import re

from ..core.config import Config
from ..core.errors import AiError
from .deepseek import DeepSeekClient

MAX_QUESTION_CHARS = 1000
CITATION_RE = re.compile(r"\[(C(?:[1-9]\d*))\]")
CONFIDENCE_RE = re.compile(
    r"依据等级[：:]\s*(原文直接支持|部分支持|模型推断|无可靠依据)"
)

# 长词优先，避免“怎么样”被“怎么”截断。
STOP_PHRASES = (
    "是什么意思",
    "是什么关系",
    "是什么样的",
    "讲了什么",
    "说明了什么",
    "意味着什么",
    "介绍了什么",
    "主要内容",
    "我想知道",
    "我想了解",
    "我想问一下",
    "我想问",
    "请问一下",
    "想知道",
    "想了解",
    "公众号",
    "这篇文章",
    "原文里",
    "原文中",
    "文中",
    "是什么",
    "是谁",
    "为什么",
    "怎么样",
    "怎么",
    "如何",
    "什么时候",
    "何时",
    "是一个",
    "什么样",
    "的人",
    "人物",
    "有哪些",
    "哪些",
    "哪个",
    "有什么",
    "有没有",
    "的关系",
    "关系",
    "的意思",
    "意思",
    "内容",
    "一下",
    "一些",
    "这个",
    "那个",
    "我们",
    "你们",
    "他们",
    "她们",
    "它们",
    "后来",
    "然后",
    "那么",
    "这里",
    "那里",
    "这样",
    "那样",
    "什么",
    "请问",
    "老师",
    "文章",
    "原文",
    "吗",
    "呢",
    "吧",
    "啊",
    "呀",
    "嘛",
)
SPLIT_RE = re.compile(
    r"[\s，。！？；：、,.!?;:（）()【】\[\]《》\"'“”‘’…—]+"
    r"|以及|或者|还是|和|与|跟|及|的"
)
CLEAN_RE = re.compile(r"[^\w一-鿿]+")
COMMON_TERMS = {
    "一个",
    "一种",
    "一些",
    "没有",
    "就是",
    "可以",
    "可能",
    "应该",
    "需要",
    "进行",
    "因为",
    "所以",
    "但是",
    "如果",
    "虽然",
    "已经",
    "正在",
    "之后",
    "之前",
    "时候",
    "问题",
    "意思",
    "关系",
    "内容",
    "一下",
    "什么",
    "怎么",
}


def _clean_chunks(question: str) -> list[str]:
    text = question or ""
    for phrase in STOP_PHRASES:
        text = text.replace(phrase, " ")
    chunks: list[str] = []
    for part in SPLIT_RE.split(text):
        part = CLEAN_RE.sub("", part).strip()
        if len(part) >= 3 and part[0] in "里中":
            part = part[1:]
        if len(part) < 2 or part.isdigit() or part in COMMON_TERMS:
            continue
        if part not in chunks:
            chunks.append(part)
    return chunks


def extract_terms(question: str, max_terms: int = 14) -> list[str]:
    """把中文问句拆成适合 FTS/LIKE 的关键词，并补充短语片段。

    这是关键词召回，不是语义向量检索。两字词会走 LIKE 兜底。
    """
    chunks = _clean_chunks(question)
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        term = (term or "").strip()
        if len(term) < 2 or term in COMMON_TERMS or term in seen:
            return
        if term.isdigit():
            return
        seen.add(term)
        terms.append(term)

    for chunk in chunks:
        add(chunk)
    # 长词内部的两字/四字片段，用于覆盖“见深初见”这类组合查询。
    for chunk in chunks:
        if len(chunk) >= 4 and len(chunk) > 4:
            for index in range(0, min(len(chunk) - 3, 4)):
                add(chunk[index : index + 4])
        if len(chunk) >= 3:
            for index in range(len(chunk) - 1):
                add(chunk[index : index + 2])
    return terms[:max_terms]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _context_text(service, entry: dict, center_limit: int = 600) -> str:
    center = (entry.get("text") or "").strip()[:center_limit]
    try:
        window = service.segment_window(
            entry["article_id"], int(entry["seq_no"]), radius=1
        )
    except Exception:
        window = []
    prev_text = ""
    next_text = ""
    for row in window:
        text = (row.get("plain_text") or "").strip()
        if not text:
            continue
        if int(row.get("seq_no", -1)) < int(entry["seq_no"]):
            prev_text = text[-240:]
        elif int(row.get("seq_no", -1)) > int(entry["seq_no"]):
            next_text = text[:240]
            break
    parts = [part for part in (prev_text, center, next_text) if part]
    return "\n".join(parts)


def collect_evidence(
    service,
    question: str,
    max_items: int = 8,
    max_chars: int = 12000,
    limit_per_term: int = 20,
) -> list[dict]:
    """按关键词在段落级召回，聚合并按命中强度排序。"""
    terms = extract_terms(question)
    if not terms or not getattr(service, "available", False):
        return []

    ranked: dict[int, dict] = {}
    seen_texts: set[str] = set()
    for term in terms:
        try:
            hits = service.segment_hits(term, limit=limit_per_term)
        except Exception:
            continue
        for hit in hits:
            segment_id = hit.get("segment_id")
            if segment_id is None:
                continue
            text = (hit.get("text") or "").strip()
            if not text:
                continue
            normalized = _normalize_text(text)
            segment_id = int(segment_id)
            entry = ranked.get(segment_id)
            if entry is None:
                if normalized in seen_texts:
                    continue
                seen_texts.add(normalized)
                entry = {
                    "article_id": int(hit["article_id"]),
                    "segment_id": segment_id,
                    "seq_no": int(hit.get("seq_no") or 0),
                    "title": hit.get("title") or "",
                    "collection_title": hit.get("collection_title") or "",
                    "publish_ts": hit.get("publish_ts"),
                    "publish_date": hit.get("publish_date") or "",
                    "text": text,
                    "score": 0,
                    "matched_terms": [],
                }
                ranked[segment_id] = entry
            if term not in entry["matched_terms"]:
                entry["matched_terms"].append(term)
                weight = max(2, min(8, len(term)))
                occurrences = text.count(term)
                entry["score"] += weight * (2 if occurrences > 1 else 1)

    if not ranked:
        return []

    ordered = sorted(
        ranked.values(),
        key=lambda item: (
            -int(item["score"]),
            -len(item["matched_terms"]),
            -(item.get("publish_ts") or 0),
            item["article_id"],
            item["seq_no"],
        ),
    )

    selected: list[dict] = []
    per_article: dict[int, int] = {}
    for entry in ordered:
        if len(selected) >= max_items:
            break
        article_id = entry["article_id"]
        if per_article.get(article_id, 0) >= 2:
            continue
        selected.append(entry)
        per_article[article_id] = per_article.get(article_id, 0) + 1
    if len(selected) < max_items:
        chosen = {item["segment_id"] for item in selected}
        for entry in ordered:
            if len(selected) >= max_items:
                break
            if entry["segment_id"] in chosen:
                continue
            selected.append(entry)
            chosen.add(entry["segment_id"])

    evidence: list[dict] = []
    used_chars = 0
    for entry in selected:
        context = _context_text(service, entry)
        if not context:
            continue
        remaining = max_chars - used_chars
        if remaining < 80:
            break
        if len(context) > remaining:
            context = context[:remaining] + "…"
        label = "C%d" % (len(evidence) + 1)
        used_chars += len(context)
        item = dict(entry)
        item["label"] = label
        item["context"] = context
        item["matched_terms"] = list(entry["matched_terms"])
        evidence.append(item)
    return evidence


def validate_citations(answer: str, evidence: list[dict]) -> dict:
    """核验模型引用的编号是否来自当前证据集合。"""
    known = {item["label"] for item in evidence}
    cited: list[str] = []
    invalid: list[str] = []
    for label in CITATION_RE.findall(answer or ""):
        if label in known:
            if label not in cited:
                cited.append(label)
        elif label not in invalid:
            invalid.append(label)
    return {"cited": cited, "invalid": invalid}


def extract_confidence(answer: str) -> str:
    match = CONFIDENCE_RE.search(answer or "")
    return match.group(1) if match else ""


SYSTEM_PROMPT = """你是“道源知识库”的原文问答助手。知识库保存的是公众号整理发布的文字资料，公众号是发布渠道，不要把公众号说成作者本人。

必须遵守：
1. 只能依据用户消息里的【原文证据】回答，不得使用证据之外的知识，不得编造人物、情节、日期或出处。
2. 每条事实性结论后必须标注证据编号，如 [C1] 或 [C1][C3]。编号只能使用当前证据中实际存在的编号。
3. 证据只能部分支持时，要明确写“从现有段落看”或“可能”，不得把推测说成原文事实。
4. 如果证据不足，直接回答“现有原文证据不足”，并说明还缺什么信息；不要猜测。
5. 不要输出 HTML、脚本、Markdown 链接，只用纯文本和 [Cx] 编号。
6. 回答要完整充分：先给结论，再分点说明，必要时补充原文细节、上下文和人物关系；篇幅服从问题需要，不要因为简短而省略关键信息。

回答末尾单独一行写：
依据等级：原文直接支持 / 部分支持 / 模型推断 / 无可靠依据
"""


def _history_messages(history: list[dict] | None, limit: int = 8) -> list[dict]:
    """保留最近 4 轮（8 条）对话，并按条截断，避免上下文无限增长。"""
    if not history:
        return []
    prepared = []
    for item in history[-limit:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        # 历史回答里的 [Cx] 对应旧证据，不能让模型误当成当前证据。
        content = CITATION_RE.sub("", content)
        prepared.append(
            {
                "role": role,
                "content": "[历史对话，仅用于理解上下文，不能作为证据]\n"
                + content[:1000],
            }
        )
    return prepared


def build_messages(
    question: str, evidence: list[dict], history: list[dict] | None = None
) -> list[dict]:
    blocks = []
    for item in evidence:
        blocks.append(
            "[%s] 《%s》%s\n%s"
            % (
                item["label"],
                item["title"] or "（无标题）",
                " · %s" % item["publish_date"] if item.get("publish_date") else "",
                item["context"],
            )
        )
    user_content = (
        "问题：%s\n\n【原文证据】\n%s\n\n请严格依据以上证据回答。"
        % (question.strip(), "\n\n".join(blocks))
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": user_content})
    return messages


def _local_result(
    question: str,
    terms: list[str],
    evidence: list[dict],
    message: str,
    reason: str,
) -> dict:
    return {
        "question": question,
        "terms": terms,
        "evidence": evidence,
        "answer": message,
        "citations": [],
        "invalid_citations": [],
        "uncited": False,
        "confidence": "无可靠依据" if evidence else "",
        "usage": {},
        "model_used": False,
        "failure_reason": reason,
        "error": message if reason not in ("no_evidence",) else "",
        "truncated": False,
    }


def answer_question(
    service,
    config: Config,
    question: str,
    api_key: str | None,
    history: list[dict] | None = None,
    previous_question: str | None = None,
    client: DeepSeekClient | None = None,
    local_only: bool = False,
    model: str | None = None,
) -> dict:
    model_name = (model or config.ai.model).strip()
    question = (question or "").strip()
    if not question:
        raise AiError("问题不能为空。")
    if len(question) > MAX_QUESTION_CHARS:
        raise AiError("问题过长，请控制在 %d 字以内。" % MAX_QUESTION_CHARS)

    retrieval_question = question
    terms = extract_terms(question)
    if (
        previous_question
        and (
            len(terms) < 2
            or re.search(r"他|她|它|这|那|该|其|后来|然后|那么|还有", question)
        )
    ):
        retrieval_question = question + " " + previous_question
        terms = extract_terms(retrieval_question)

    evidence = collect_evidence(
        service,
        retrieval_question,
        max_chars=max(1000, int(config.ai.max_context_chars)),
    )
    if not evidence:
        return _local_result(
            question,
            terms,
            [],
            "没有在本机原文中找到与这个问题相关的段落。可以换用更短、更具体的关键词，"
            "或先到「全文检索」中确认原文是否收录。",
            "no_evidence",
        )
    if local_only:
        return _local_result(
            question,
            terms,
            evidence,
            "已按你的选择仅在本机检索，没有向第三方发送任何内容。"
            "以下是从原文召回的候选段落。",
            "local_only",
        )
    if not api_key:
        return _local_result(
            question,
            terms,
            evidence,
            "尚未配置 DeepSeek API Key。以下是从本机原文召回的候选段落；"
            "配置 Key 后可在不改变原文数据的前提下生成带引用的回答。",
            "no_key",
        )

    if client is None:
        client = DeepSeekClient(
            base_url=config.ai.base_url,
            model=model_name,
            api_key=api_key,
            timeout=config.ai.timeout,
        )
    try:
        response = client.chat(
            build_messages(question, evidence, history),
            temperature=config.ai.temperature,
            max_tokens=max(256, int(config.ai.max_answer_tokens)),
        )
    except AiError as exc:
        result = _local_result(
            question,
            terms,
            evidence,
            "暂时无法生成 AI 回答：%s\n以下本地候选段落仍可阅读。" % exc,
            "api_error",
        )
        result["error"] = str(exc)
        return result

    answer = response.get("content") or ""
    truncated = response.get("finish_reason") == "length"
    validation = validate_citations(answer, evidence)
    by_label = {item["label"]: item for item in evidence}
    citations = []
    for label in validation["cited"]:
        item = by_label[label]
        citations.append(
            {
                "label": label,
                "article_id": item["article_id"],
                "segment_id": item["segment_id"],
                "title": item["title"],
                "publish_date": item["publish_date"],
                "snippet": (item.get("text") or "")[:240],
            }
        )
    return {
        "question": question,
        "terms": terms,
        "evidence": evidence,
        "answer": answer,
        "citations": citations,
        "invalid_citations": validation["invalid"],
        "uncited": not validation["cited"],
        "confidence": extract_confidence(answer),
        "usage": response.get("usage") or {},
        "model_used": True,
        "model": response.get("model") or model_name,
        "failure_reason": "",
        "error": "",
        "truncated": truncated,
    }
