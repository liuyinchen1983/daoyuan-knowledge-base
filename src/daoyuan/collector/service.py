# -*- coding: utf-8 -*-
"""采集编排：清单 → 待采 → 正文 → 入库，含保守限速与风险停止。"""
from __future__ import annotations

import logging
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from ..core.config import Config
from ..core.errors import (
    ArticleUnavailableError,
    CollectorError,
    ParseError,
    RiskControlError,
)
from ..core.models import (
    SORT_UNKNOWN,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_UNAVAILABLE,
)
from ..core.timeutil import now_iso
from ..storage.repositories import ArticleRepository, CollectionRepository, SourceRepository
from .album import AlbumClient
from .http import HttpClient
from .known import ACCOUNT_ID, ACCOUNT_NAME, BIZ, KNOWN_COLLECTIONS
from .parser import parse_article


@dataclass
class CollectionRunResult:
    collection_id: int
    title: str
    run_id: int
    status: str = "completed"
    list_found: int = 0
    pending: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    unavailable: int = 0
    restricted: int = 0
    fetched: int = 0
    message: str = ""
    errors: list[str] = field(default_factory=list)


class CollectService:
    def __init__(
        self,
        config: Config,
        conn: sqlite3.Connection,
        http: HttpClient | None = None,
        sleeper=time.sleep,
        rand=random.random,
        logger: logging.Logger | None = None,
        stop_event: threading.Event | None = None,
    ):
        self.config = config
        self.conn = conn
        self.http = http or HttpClient(config.collector, state_dir=config.paths.root)
        self.sleeper = sleeper
        self.rand = rand
        self.logger = logger or logging.getLogger("daoyuan.collector")
        self.stop_event = stop_event or threading.Event()
        self.sources = SourceRepository(conn)
        self.collections = CollectionRepository(conn)
        self.articles = ArticleRepository(conn)
        self.album = AlbumClient(
            self.http,
            sleeper=sleeper,
            rand=rand,
            list_delay=2.0,
            logger=self.logger,
        )

    # --- registration ---
    def ensure_source(self) -> int:
        return self.sources.upsert(
            BIZ, ACCOUNT_NAME, ACCOUNT_ID, verified_state="verified"
        )

    def register_known(self) -> list[int]:
        source_id = self.ensure_source()
        ids = []
        for known in KNOWN_COLLECTIONS:
            collection_id = self.collections.upsert(
                source_id,
                known.album_id,
                known.name,
                sort_mode=known.sort_mode if known.reverse_flag is not None else None,
                expected_count=known.expected_count,
            )
            ids.append(collection_id)
        return ids

    # --- run records ---
    def _start_run(self, mode: str, collection_id: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO collect_runs(mode, collection_id, status, started_at) "
            "VALUES (?,?,?,?)",
            (mode, collection_id, "running", now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _finish_run(self, run_id: int, result: CollectionRunResult) -> None:
        self.conn.execute(
            "UPDATE collect_runs SET status=?, finished_at=?, list_found=?, pending_count=?, "
            "success_count=?, failed_count=?, skipped_count=?, restricted_count=?, message=? "
            "WHERE id=?",
            (
                result.status,
                now_iso(),
                result.list_found,
                result.pending,
                result.success,
                result.failed,
                result.skipped,
                result.restricted,
                result.message[:1000] if result.message else None,
                run_id,
            ),
        )
        self.conn.commit()

    def _record_item(
        self,
        run_id: int,
        article_id: int | None,
        msgid: str,
        itemidx: str,
        status: str,
        error_type: str | None = None,
        message: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO collect_items(run_id, article_id, msgid, itemidx, status, "
            "error_type, error_message, retry_count, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                article_id,
                msgid,
                itemidx,
                status,
                error_type,
                (message or "")[:1000] or None,
                0,
                now_iso(),
            ),
        )
        self.conn.commit()

    # --- direction ---
    def _resolve_reverse(self, collection, result: CollectionRunResult) -> int:
        mode = collection.sort_mode or SORT_UNKNOWN
        if mode.startswith("newest_first:rev="):
            try:
                return int(mode.rsplit("=", 1)[1])
            except (ValueError, IndexError):
                pass
        reverse, info = self.album.detect_direction(BIZ, collection.album_id)
        self.collections.set_sort_mode(collection.id, "newest_first:rev=%d" % reverse)
        if info.get("title"):
            self.collections.upsert(
                collection.source_id,
                collection.album_id,
                info.get("title"),
                description=info.get("description"),
                sort_mode="newest_first:rev=%d" % reverse,
                expected_count=_int_or_none(info.get("article_count")),
                is_updating=_bool_or_none(info.get("isupdating")),
            )
        return reverse

    # --- main ---
    def run(
        self,
        mode: str = "update",
        collection_ids: list[int] | None = None,
        max_fetch: int | None = None,
        limit: int | None = None,
        list_only: bool = False,
        progress_cb=None,
    ) -> list[CollectionRunResult]:
        if mode not in ("full", "update"):
            raise ValueError("mode 必须是 full 或 update")
        if collection_ids is None:
            collections = self.collections.list_all(enabled_only=True)
        else:
            wanted = set(collection_ids)
            collections = [c for c in self.collections.list_all() if c.id in wanted]
        budget = (
            self.config.collector.max_fetch_per_run if max_fetch is None else max_fetch
        )
        if budget is not None and budget <= 0:
            budget = None
        results = []
        for collection in collections:
            if self.stop_event.is_set():
                break
            result = self._run_one(
                collection,
                mode=mode,
                limit=limit,
                budget=budget,
                list_only=list_only,
                progress_cb=progress_cb,
            )
            results.append(result)
            if budget is not None:
                budget -= result.fetched
                if budget <= 0:
                    budget = 0
            if result.status == "restricted":
                break
        return results

    def _run_one(
        self,
        collection,
        mode: str,
        limit: int | None,
        budget: int | None,
        list_only: bool,
        progress_cb,
    ) -> CollectionRunResult:
        result = CollectionRunResult(
            collection_id=collection.id,
            title=collection.title,
            run_id=0,
        )
        run_id = self._start_run(mode, collection.id)
        result.run_id = run_id
        try:
            reverse = self._resolve_reverse(collection, result)
            known_keys = self._known_keys(collection.id)
            if mode == "full" or not known_keys:
                items, base_info = self.album.fetch_all(
                    BIZ, collection.album_id, reverse, progress_cb=progress_cb
                )
            else:
                items, base_info, stable = self.album.fetch_recent(
                    BIZ, collection.album_id, reverse, known_keys, progress_cb=progress_cb
                )
                if not stable:
                    self.logger.warning("增量列表未到达稳定边界，继续读取完整列表")
                    items, base_info = self.album.fetch_all(
                        BIZ, collection.album_id, reverse, progress_cb=progress_cb
                    )
            result.list_found = len(items)
            self._save_listing(collection, items, base_info, run_id)
            self.collections.touch_checked(collection.id)

            if list_only:
                result.status = "completed"
                result.message = "list_only"
                progress_cb and progress_cb("list_done", {"items": len(items)})
                return result

            pending = self._pending_articles(collection.id, mode)
            if limit is not None and limit >= 0:
                pending = pending[:limit]
            result.pending = len(pending)
            self.logger.info(
                "%s：清单 %d 篇，待采 %d 篇，本次预算 %s"
                % (collection.title, len(items), len(pending), budget)
            )

            fetched = 0
            batch_count = 0
            stop_reason = ""
            for row in pending:
                if self.stop_event.is_set():
                    stop_reason = "cancelled"
                    result.status = "stopped"
                    break
                if budget is not None and fetched >= budget:
                    stop_reason = "max_fetch_reached"
                    result.status = "stopped"
                    break
                article_id = int(row["id"])
                msgid = str(row["msgid"])
                itemidx = str(row["itemidx"])
                url = row["source_url"] or ""
                if "chksm=" not in url:
                    self.articles.mark_failed(article_id, "missing_chksm")
                    result.failed += 1
                    self._record_item(
                        run_id, article_id, msgid, itemidx, "failed", "missing_chksm",
                        "列表链接缺少 chksm",
                    )
                    continue

                self.sleeper(self._delay())
                fetched += 1
                batch_count += 1
                result.fetched += 1
                if progress_cb:
                    progress_cb(
                        "article",
                        {"title": row["title"], "done": fetched, "total": len(pending)},
                    )
                try:
                    final_url, page_html = self._fetch_with_retry(url)
                    parsed = self._parse_with_retry(
                        page_html,
                        fallback_title=row["title"] or "",
                        fallback_ts=row["publish_ts"],
                        source_url=final_url,
                    )
                    outcome = self.articles.mark_ready(article_id, parsed)
                    if outcome == "unchanged":
                        result.skipped += 1
                        self._record_item(run_id, article_id, msgid, itemidx, "skipped")
                    else:
                        result.success += 1
                        self._record_item(run_id, article_id, msgid, itemidx, "success")
                except RiskControlError as exc:
                    result.restricted = 1
                    result.failed += 1
                    result.status = "restricted"
                    stop_reason = "restricted:%s" % exc
                    self._record_item(
                        run_id, article_id, msgid, itemidx, "restricted", "risk", str(exc)
                    )
                    self.articles.mark_failed(article_id, "restricted:%s" % exc)
                    break
                except ArticleUnavailableError as exc:
                    self.articles.mark_unavailable(article_id, str(exc))
                    result.unavailable += 1
                    self._record_item(
                        run_id, article_id, msgid, itemidx, "unavailable",
                        "unavailable", str(exc),
                    )
                except (ParseError, CollectorError) as exc:
                    self.articles.mark_failed(article_id, str(exc))
                    result.failed += 1
                    result.errors.append(str(exc))
                    self._record_item(
                        run_id, article_id, msgid, itemidx, "failed",
                        exc.__class__.__name__, str(exc),
                    )
                except Exception as exc:  # 单篇异常不应终止整批
                    self.logger.exception("单篇处理异常：%s", exc)
                    self.articles.mark_failed(article_id, "unexpected:%s" % exc)
                    result.failed += 1
                    result.errors.append(str(exc))
                    self._record_item(
                        run_id, article_id, msgid, itemidx, "failed", "unexpected", str(exc)
                    )

                if batch_count >= self.config.collector.batch_size:
                    pause = self._batch_pause()
                    self.logger.info("已连续抓取 %d 篇，暂停 %.0f 秒", batch_count, pause)
                    self.sleeper(pause)
                    batch_count = 0

            if result.status == "running":
                result.status = "completed"
            if stop_reason:
                result.message = stop_reason
            return result
        except RiskControlError as exc:
            result.status = "restricted"
            result.message = str(exc)
            self.logger.error("触发访问限制，立即停止：%s", exc)
            return result
        except Exception as exc:
            result.status = "failed"
            result.message = str(exc)
            self.logger.exception("采集任务失败：%s", exc)
            return result
        finally:
            self._finish_run(run_id, result)

    # --- helpers ---
    def _known_keys(self, collection_id: int) -> set[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT msgid, itemidx FROM collection_listings WHERE collection_id=?",
            (collection_id,),
        ).fetchall()
        return {(str(r[0]), str(r[1])) for r in rows}

    def _save_listing(self, collection, items, base_info: dict, run_id: int) -> None:
        # 用户主动删除过的文章保留墓碑；合集清单再次扫到时不重新收录。
        deleted = self.articles.deleted_keys(collection.id)
        for order, item in enumerate(items):
            if (str(item.msgid), str(item.itemidx)) in deleted:
                self.logger.info(
                    "跳过已删除文章：%s（%s/%s）",
                    item.title or "",
                    item.msgid,
                    item.itemidx,
                )
                continue
            self.articles.upsert_discovered(
                collection.source_id, item, collection.id, order, run_id
            )
        title = (base_info.get("title") or collection.title or "").strip()
        if title:
            self.collections.upsert(
                collection.source_id,
                collection.album_id,
                title,
                description=base_info.get("description"),
                sort_mode=None,
                expected_count=_int_or_none(base_info.get("article_count")),
                is_updating=_bool_or_none(base_info.get("isupdating")),
            )

    def _pending_articles(self, collection_id: int, mode: str):
        if mode == "full":
            statuses = (STATUS_PENDING, STATUS_FAILED, STATUS_READY)
        else:
            statuses = (STATUS_PENDING, STATUS_FAILED)
        placeholders = ",".join("?" for _ in statuses)
        return self.conn.execute(
            "SELECT a.* FROM articles a JOIN article_collections ac ON ac.article_id=a.id "
            "WHERE ac.collection_id=? AND a.status IN (%s) "
            "ORDER BY ac.serial_no ASC, a.id ASC" % placeholders,
            (collection_id, *statuses),
        ).fetchall()

    def _delay(self) -> float:
        c = self.config.collector
        return c.delay_min + (c.delay_max - c.delay_min) * self.rand()

    def _batch_pause(self) -> float:
        c = self.config.collector
        return c.batch_pause_min + (c.batch_pause_max - c.batch_pause_min) * self.rand()

    def _fetch_with_retry(self, url: str) -> tuple[str, str]:
        attempts = self.config.collector.max_retries + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self.http.get_text(url)
            except (RiskControlError, ArticleUnavailableError):
                raise
            except CollectorError as exc:
                last_error = exc
                self.logger.warning("抓取失败（%d/%d）：%s", attempt, attempts, exc)
                if attempt < attempts:
                    self.sleeper(self.config.collector.retry_backoff * attempt)
        raise last_error or CollectorError("抓取失败")

    def _parse_with_retry(self, page_html: str, **kwargs):
        try:
            return parse_article(page_html, **kwargs)
        except ParseError as exc:
            if str(exc) == "empty_content":
                self.logger.warning("正文为空，10 秒后重试一次")
                self.sleeper(10.0)
                return parse_article(page_html, **kwargs)
            raise


def _int_or_none(value):
    try:
        return int(value) if value is not None and str(value) != "" else None
    except (TypeError, ValueError):
        return None


def _bool_or_none(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None
