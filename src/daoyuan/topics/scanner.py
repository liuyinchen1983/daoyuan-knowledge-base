# -*- coding: utf-8 -*-
"""专题库增量扫描器。

关键行为：
- 每篇文章记录正文哈希和抽取器版本；哈希没变就不重复抽取。
- 新增文章、正文修订、抽取规则升级时才重新处理对应文章。
- 重新处理前只删除该文章在专题库里的旧提及，不影响其他文章和人工审核状态。
- 已审核通过的实体不会被扫描覆盖；新文章只会为它追加出处。
"""
from __future__ import annotations

import hashlib
import logging
import time

from ..core.config import Config
from ..core.errors import StorageError
from ..storage.db import connect
from . import extractor
from .store import TopicStore

EXTRACTOR_VERSION = 9


def _text_hash(article: dict) -> str:
    value = (article.get("text_hash") or "").strip()
    if value:
        return value
    text = article.get("text_content") or ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _canonicalize_candidates(
    candidates: list[extractor.Candidate],
    known_terms: tuple[tuple[str, str, str], ...],
) -> None:
    """把同名的通用候选归并到人工已收录的类型和规范名。

    人工把「X7 Future」从物品改成组织后，物品规则仍可能从正文里再生成一个
    同名物品候选。这里按名称统一回已收录实体，避免扫描凭空造出重复待审条目，
    也保证原文出处继续落在同一个实体上。
    """
    mapping: dict[str, tuple[str, str]] = {}
    for term, kind, canonical in known_terms:
        mapping[term] = (kind, canonical)
        mapping.setdefault(canonical, (kind, canonical))
    for candidate in candidates:
        target = mapping.get(candidate.name)
        if target is None:
            continue
        candidate.kind, candidate.name = target


def _merge_candidates(candidates: list[extractor.Candidate]) -> dict:
    merged: dict[tuple[str, str], extractor.Candidate] = {}
    for candidate in candidates:
        key = (candidate.kind, candidate.name)
        target = merged.get(key)
        if target is None:
            merged[key] = candidate
            continue
        target.count += candidate.count
        target.strong += candidate.strong
        target.title_hit = target.title_hit or candidate.title_hit
        target.article_ids |= candidate.article_ids
        target.aliases |= candidate.aliases
        for mention in candidate.mentions:
            if any(
                item.segment_id == mention.segment_id and item.quote == mention.quote
                for item in target.mentions
            ):
                continue
            target.mentions.append(mention)
    return merged


def _ready_articles(content, collection_id: int) -> list[dict]:
    rows = content.execute(
        "SELECT a.id, a.title, a.text_hash, a.text_content, a.publish_ts, "
        "ac.serial_no FROM articles a "
        "JOIN article_collections ac ON ac.article_id=a.id "
        "WHERE ac.collection_id=? AND a.status='ready' "
        "AND a.text_content IS NOT NULL AND length(trim(a.text_content))>0 "
        "ORDER BY ac.serial_no ASC, a.id ASC",
        (int(collection_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _segments(content, article_id: int) -> list[dict]:
    rows = content.execute(
        "SELECT id, seq_no, plain_text FROM article_segments "
        "WHERE article_id=? ORDER BY seq_no",
        (int(article_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def scan_collection(
    config: Config,
    collection_id: int,
    full: bool = False,
    logger: logging.Logger | None = None,
) -> dict:
    """扫描一个文集。full=False 时只处理新文章/正文变化/规则升级的文章。"""
    logger = logger or logging.getLogger("daoyuan.topics")
    started = time.monotonic()
    if not config.paths.content_db.exists():
        raise StorageError("内容库尚未初始化，无法生成专题库。")
    content = connect(config.paths.content_db, readonly=True)
    store = TopicStore.open(config.paths.topics_db, readonly=False)
    stats = {
        "collection_id": int(collection_id),
        "full": bool(full),
        "ready": 0,
        "scanned": 0,
        "skipped": 0,
        "candidates": 0,
        "entities_created": 0,
        "entities_pruned": 0,
        "mentions_added": 0,
        "mentions_removed": 0,
        "errors": [],
        "duration_ms": 0,
    }
    try:
        articles = _ready_articles(content, collection_id)
        stats["ready"] = len(articles)
        to_scan: list[dict] = []
        for article in articles:
            digest = _text_hash(article)
            state = store.scan_state(collection_id, int(article["id"]))
            if (
                not full
                and state is not None
                and (state["text_hash"] or "") == digest
                and int(state["extractor_version"] or 0) == EXTRACTOR_VERSION
            ):
                stats["skipped"] += 1
                continue
            article["_hash"] = digest
            to_scan.append(article)

        # 文章已不可读/被移除时，清掉它在专题库里的旧出处。
        ready_ids = {int(a["id"]) for a in articles}
        stale_ids = store.scan_state_ids(collection_id) - ready_ids
        for article_id in sorted(stale_ids):
            stats["mentions_removed"] += store.remove_article_mentions(
                collection_id, article_id
            )
            store.remove_article_context(collection_id, article_id)
            store.delete_scan_state(collection_id, article_id)

        if to_scan:
            known_terms = store.known_terms(collection_id)
            known_keys = {(kind, canonical) for _, kind, canonical in known_terms}
            batch: list[extractor.Candidate] = []
            for article in to_scan:
                segments = _segments(content, int(article["id"]))
                if not segments:
                    # 没有段落时退化为整篇正文，仍保留文章级出处。
                    segments = [
                        {
                            "id": None,
                            "seq_no": 0,
                            "plain_text": article.get("text_content") or "",
                        }
                    ]
                try:
                    batch.extend(
                        extractor.extract_article(article, segments, known_terms)
                    )
                except Exception as exc:  # 单篇失败不拖垮整个文集
                    stats["errors"].append("文章 %s 抽取失败：%s" % (article["id"], exc))
                    logger.exception("专题抽取失败 article=%s", article["id"])

            _canonicalize_candidates(batch, known_terms)
            merged = _merge_candidates(batch)
            accepted = []
            for candidate in merged.values():
                if (candidate.kind, candidate.name) in known_keys:
                    accepted.append(candidate)
                elif extractor.accept_candidate(candidate, article_count=len(to_scan)):
                    accepted.append(candidate)
            accepted = extractor.postprocess(accepted)
            stats["candidates"] = len(accepted)

            # 先清掉本次要重扫文章里的旧出处，再按本批结果重建。
            for article in to_scan:
                stats["mentions_removed"] += store.remove_article_mentions(
                    collection_id, int(article["id"])
                )

            for candidate in accepted:
                if not candidate.mentions:
                    continue
                _, created, added = store.upsert_candidate(
                    collection_id,
                    candidate.kind,
                    candidate.name,
                    aliases=tuple(candidate.aliases),
                    mentions=candidate.mentions,
                    source="rule",
                )
                if created:
                    stats["entities_created"] += 1
                stats["mentions_added"] += added

            for article in to_scan:
                store.set_scan_state(
                    collection_id,
                    int(article["id"]),
                    article.get("_hash") or "",
                    EXTRACTOR_VERSION,
                )

        # 规则升级或正文修订后，上一轮留下的待审核空壳不再保留；
        # 但已收录/已忽略状态不动，避免人工审核结果被扫描覆盖。
        stats["entities_pruned"] = store.prune_empty_pending(collection_id)
        stats["scanned"] = len(to_scan)
        stats["duration_ms"] = int((time.monotonic() - started) * 1000)
        store.set_last_scan(collection_id, stats)
        return stats
    finally:
        store.close()
        content.close()


def scan_after_collection(
    config: Config,
    collection_ids,
    logger: logging.Logger | None = None,
    full: bool = False,
) -> list[dict]:
    """采集完成后自动做增量专题更新；失败只记录，不影响采集结果。"""
    logger = logger or logging.getLogger("daoyuan.topics")
    results = []
    seen = set()
    for collection_id in collection_ids or []:
        try:
            cid = int(collection_id)
        except (TypeError, ValueError):
            continue
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        try:
            result = scan_collection(config, cid, full=full, logger=logger)
            results.append(result)
            logger.info(
                "专题库增量更新：文集 %s，扫描 %s 篇，跳过 %s 篇，新增实体 %s，新增出处 %s",
                cid,
                result.get("scanned"),
                result.get("skipped"),
                result.get("entities_created"),
                result.get("mentions_added"),
            )
        except Exception as exc:
            logger.exception("专题库增量更新失败 collection=%s", cid)
            results.append({"collection_id": cid, "error": str(exc)})
    return results


def scan_all_collections(
    config: Config, full: bool = False, logger: logging.Logger | None = None
) -> list[dict]:
    if not config.paths.content_db.exists():
        raise StorageError("内容库尚未初始化，无法生成专题库。")
    content = connect(config.paths.content_db, readonly=True)
    try:
        ids = [
            int(row[0])
            for row in content.execute(
                "SELECT id FROM collections WHERE enabled=1 ORDER BY id"
            ).fetchall()
        ]
    finally:
        content.close()
    return [
        scan_collection(config, collection_id, full=full, logger=logger)
        for collection_id in ids
    ]
