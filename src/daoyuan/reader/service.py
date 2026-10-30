# -*- coding: utf-8 -*-
"""阅读端只读服务。内容库只读打开，个人状态库独立可写。"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..core.config import Config
from ..core.errors import StorageError
from ..core.text import (
    chapter_number,
    chapter_sort_key,
    strip_chapter_prefix,
    strip_title_date,
)
from ..core.timeutil import date_to_epoch, format_epoch_date, now, now_iso
from ..storage.db import connect, health_report, initialize_content_db
from ..storage.repositories import ArticleRepository
from ..storage.state_db import StateStore
from ..topics.store import TopicStore


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _like_pattern(term: str) -> str:
    escaped = (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return "%" + escaped + "%"


def _fts_phrase(term: str) -> str:
    return '"%s"' % term.replace('"', '""')


def _excerpt(text: str, term: str, width: int = 60) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    pos = text.lower().find(term.lower())
    if pos < 0:
        cut = width * 2
        return text[:cut] + ("…" if len(text) > cut else "")
    start = max(0, pos - width)
    end = min(len(text), pos + len(term) + width)
    return (
        ("…" if start > 0 else "")
        + text[start:end]
        + ("…" if end < len(text) else "")
    )


class ReaderService:
    def __init__(self, config: Config):
        self.config = config
        self.conn: sqlite3.Connection | None = None
        self.content_error: str | None = None
        if config.paths.content_db.exists():
            try:
                self.conn = connect(config.paths.content_db, readonly=True)
            except Exception as exc:  # 页面需要友好显示，不在这里崩溃
                self.content_error = str(exc)
        self.state = StateStore(config.paths.state_db)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.state.close()

    def reopen(self) -> None:
        """内容库被导入/替换后，重新打开只读连接，页面继续共用本对象。"""
        self.close()
        self._closed = False
        self.conn = None
        self.content_error = None
        if self.config.paths.content_db.exists():
            try:
                self.conn = connect(self.config.paths.content_db, readonly=True)
            except Exception as exc:
                self.content_error = str(exc)
        self.state = StateStore(self.config.paths.state_db)

    @property
    def available(self) -> bool:
        return self.conn is not None

    def health(self) -> dict:
        if self.conn is None:
            return {"available": False, "error": self.content_error or "内容库尚未初始化"}
        report = health_report(self.conn)
        report["available"] = True
        return report

    def home_stats(self) -> dict:
        if self.conn is None:
            return {"collections": 0, "articles": 0, "ready": 0, "recent": []}
        collections = self.conn.execute(
            "SELECT count(*) FROM collections WHERE enabled=1"
        ).fetchone()[0]
        articles = self.conn.execute("SELECT count(*) FROM articles").fetchone()[0]
        ready = self.conn.execute(
            "SELECT count(*) FROM articles WHERE status='ready'"
        ).fetchone()[0]
        recent = self.conn.execute(
            "SELECT a.id, a.title, a.publish_ts, c.title AS collection_title "
            "FROM articles a JOIN article_collections ac ON ac.article_id=a.id "
            "JOIN collections c ON c.id=ac.collection_id "
            "WHERE a.status='ready' ORDER BY a.publish_ts DESC, a.id DESC LIMIT 8"
        ).fetchall()
        recent_list = []
        for r in recent:
            d = dict(r)
            d["publish_date"] = format_epoch_date(d.get("publish_ts"))
            recent_list.append(d)
        return {
            "collections": int(collections),
            "articles": int(articles),
            "ready": int(ready),
            "recent": recent_list,
        }

    def collections(self) -> list[dict]:
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT c.id, c.title, c.album_id, c.expected_count, c.sort_mode, "
            "count(ac.article_id) AS total, "
            "sum(CASE WHEN a.status='ready' THEN 1 ELSE 0 END) AS ready "
            "FROM collections c "
            "LEFT JOIN article_collections ac ON ac.collection_id=c.id "
            "LEFT JOIN articles a ON a.id=ac.article_id "
            "WHERE c.enabled=1 GROUP BY c.id ORDER BY c.id"
        ).fetchall()
        return _rows(rows)

    def ready_articles(self, collection_id: int | None = None) -> list[dict]:
        if self.conn is None:
            return []
        sql = (
            "SELECT a.id, a.title, a.page_author, a.publish_ts, a.title_date, "
            "a.source_url, c.title AS collection_title "
            "FROM articles a "
            "LEFT JOIN (SELECT article_id, MIN(collection_id) AS collection_id "
            "FROM article_collections GROUP BY article_id) ac ON ac.article_id=a.id "
            "LEFT JOIN collections c ON c.id=ac.collection_id "
            "WHERE a.status='ready'"
        )
        params: list = []
        if collection_id is not None:
            sql += (
                " AND EXISTS (SELECT 1 FROM article_collections ac2 "
                "WHERE ac2.article_id=a.id AND ac2.collection_id=?)"
            )
            params.append(collection_id)
        sql += " ORDER BY a.publish_ts DESC, a.id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["publish_date"] = format_epoch_date(item.get("publish_ts"))
            result.append(item)
        return result

    def search(
        self,
        query: str,
        collection_id: int | None = None,
        limit: int = 100,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        """标题 + 正文检索。>=3 字走 trigram FTS，短词走 LIKE 兜底。"""
        term = (query or "").strip()
        if not term or self.conn is None:
            return {
                "query": term,
                "mode": "empty",
                "results": [],
                "total_articles": 0,
                "truncated": False,
            }
        start_ts = date_to_epoch(date_from) if date_from else None
        end_ts = date_to_epoch(date_to, end_of_day=True) if date_to else None
        fetch_limit = min(max(limit * 4, 80), 600)
        rows = []
        mode = "like"
        if len(term) >= 3:
            try:
                rows = self._search_fts(
                    term, collection_id, fetch_limit, start_ts, end_ts
                )
                mode = "fts"
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            rows = self._search_like(
                term, collection_id, fetch_limit, start_ts, end_ts
            )
            mode = "like" if len(term) < 3 else "like_fallback"

        grouped = {}
        order = []
        for row in rows:
            article_id = int(row["article_id"])
            if article_id not in grouped:
                grouped[article_id] = self._hit_from_row(row, term)
                order.append(article_id)
            grouped[article_id]["hit_count"] += 1
        results = [grouped[a] for a in order]

        seen = {r["article_id"] for r in results}
        for row in self._search_titles(
            term, collection_id, fetch_limit, start_ts, end_ts
        ):
            article_id = int(row["article_id"])
            if article_id in seen:
                for result in results:
                    if result["article_id"] == article_id:
                        result["matched_title"] = True
                        break
                continue
            item = {
                "article_id": article_id,
                "title": row["title"] or "",
                "collection_id": row["collection_id"],
                "collection_title": row["collection_title"] or "",
                "publish_ts": row["publish_ts"],
                "publish_date": format_epoch_date(row["publish_ts"]),
                "segment_id": None,
                "seq_no": None,
                "excerpt": row["title"] or "",
                "hit_count": 1,
                "matched_title": True,
            }
            results.append(item)
            seen.add(article_id)

        total = len(results)
        return {
            "query": term,
            "mode": mode,
            "results": results[:limit],
            "total_articles": total,
            "truncated": total > limit,
        }

    @staticmethod
    def _date_sql(
        sql: str, params: list, start_ts: int | None, end_ts: int | None
    ) -> tuple[str, list]:
        if start_ts is not None:
            sql += " AND a.publish_ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND a.publish_ts < ?"
            params.append(end_ts)
        return sql, params

    def _search_fts(
        self,
        term: str,
        collection_id: int | None,
        limit: int,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ):
        sql = (
            "SELECT s.id AS segment_id, s.article_id, s.seq_no, s.plain_text, "
            "a.title, a.publish_ts, ac.collection_id, c.title AS collection_title "
            "FROM segments_fts "
            "JOIN article_segments s ON s.id = segments_fts.rowid "
            "JOIN articles a ON a.id = s.article_id "
            "LEFT JOIN (SELECT article_id, MIN(collection_id) AS collection_id "
            "FROM article_collections GROUP BY article_id) ac ON ac.article_id=a.id "
            "LEFT JOIN collections c ON c.id = ac.collection_id "
            "WHERE segments_fts MATCH ? AND a.status='ready'"
        )
        params: list = [_fts_phrase(term)]
        if collection_id is not None:
            sql += (
                " AND EXISTS (SELECT 1 FROM article_collections ac2 "
                "WHERE ac2.article_id=a.id AND ac2.collection_id=?)"
            )
            params.append(collection_id)
        sql, params = self._date_sql(sql, params, start_ts, end_ts)
        sql += " ORDER BY bm25(segments_fts), a.publish_ts DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def _search_like(
        self,
        term: str,
        collection_id: int | None,
        limit: int,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ):
        sql = (
            "SELECT s.id AS segment_id, s.article_id, s.seq_no, s.plain_text, "
            "a.title, a.publish_ts, ac.collection_id, c.title AS collection_title "
            "FROM article_segments s "
            "JOIN articles a ON a.id = s.article_id "
            "LEFT JOIN (SELECT article_id, MIN(collection_id) AS collection_id "
            "FROM article_collections GROUP BY article_id) ac ON ac.article_id=a.id "
            "LEFT JOIN collections c ON c.id = ac.collection_id "
            "WHERE a.status='ready' AND s.plain_text LIKE ? ESCAPE '\\'"
        )
        params: list = [_like_pattern(term)]
        if collection_id is not None:
            sql += (
                " AND EXISTS (SELECT 1 FROM article_collections ac2 "
                "WHERE ac2.article_id=a.id AND ac2.collection_id=?)"
            )
            params.append(collection_id)
        sql, params = self._date_sql(sql, params, start_ts, end_ts)
        sql += " ORDER BY a.publish_ts DESC, s.seq_no ASC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def _search_titles(
        self,
        term: str,
        collection_id: int | None,
        limit: int,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ):
        sql = (
            "SELECT a.id AS article_id, a.title, a.publish_ts, "
            "ac.collection_id, c.title AS collection_title "
            "FROM articles a "
            "LEFT JOIN (SELECT article_id, MIN(collection_id) AS collection_id "
            "FROM article_collections GROUP BY article_id) ac ON ac.article_id=a.id "
            "LEFT JOIN collections c ON c.id = ac.collection_id "
            "WHERE a.status='ready' AND a.title LIKE ? ESCAPE '\\'"
        )
        params: list = [_like_pattern(term)]
        if collection_id is not None:
            sql += (
                " AND EXISTS (SELECT 1 FROM article_collections ac2 "
                "WHERE ac2.article_id=a.id AND ac2.collection_id=?)"
            )
            params.append(collection_id)
        sql, params = self._date_sql(sql, params, start_ts, end_ts)
        sql += " ORDER BY a.publish_ts DESC, a.id DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def _hit_from_row(self, row, term: str) -> dict:
        return {
            "article_id": int(row["article_id"]),
            "title": row["title"] or "",
            "collection_id": row["collection_id"],
            "collection_title": row["collection_title"] or "",
            "publish_ts": row["publish_ts"],
            "publish_date": format_epoch_date(row["publish_ts"]),
            "segment_id": int(row["segment_id"])
            if row["segment_id"] is not None
            else None,
            "seq_no": row["seq_no"],
            "text": row["plain_text"] or "",
            "excerpt": _excerpt(row["plain_text"] or "", term),
            "hit_count": 1,
            "matched_title": False,
        }

    def segment_hits(
        self, term: str, limit: int = 30, collection_id: int | None = None
    ) -> list[dict]:
        """返回段落级命中（不做按文章归并），供智问召回证据。"""
        term = (term or "").strip()
        if not term or self.conn is None:
            return []
        rows = []
        if len(term) >= 3:
            try:
                rows = self._search_fts(term, collection_id, limit)
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            rows = self._search_like(term, collection_id, limit)
        return [self._hit_from_row(row, term) for row in rows]

    def segment_window(
        self, article_id: int, seq_no: int, radius: int = 1
    ) -> list[dict]:
        if self.conn is None:
            return []
        start = max(0, int(seq_no) - int(radius))
        end = int(seq_no) + int(radius)
        rows = self.conn.execute(
            "SELECT id, seq_no, plain_text FROM article_segments "
            "WHERE article_id=? AND seq_no BETWEEN ? AND ? ORDER BY seq_no",
            (article_id, start, end),
        ).fetchall()
        return _rows(rows)

    def articles(self, collection_id: int) -> list[dict]:
        """按章节号升序返回文集文章。

        自动采集的 ``serial_no`` 是清单收录顺序，不是故事顺序；手动补录的
        章节会追加在清单后面，直接按 ``serial_no`` 排会把“01”排到“52”
        后面。这里统一按标题开头的章节号数字排序，没有章节号的文章保持
        收录顺序并排在最后。
        """
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT a.id, a.title, a.page_author, a.publish_ts, a.status, ac.serial_no "
            "FROM articles a JOIN article_collections ac ON ac.article_id=a.id "
            "WHERE ac.collection_id=?",
            (collection_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["publish_date"] = format_epoch_date(d.get("publish_ts"))
            d["chapter"] = chapter_number(d.get("title") or "")
            d["display_title"] = (
                strip_title_date(strip_chapter_prefix(d.get("title") or ""))
                or "（无标题）"
            )
            result.append(d)
        result.sort(
            key=lambda d: chapter_sort_key(
                d.get("title") or "",
                d.get("serial_no"),
                d["id"],
                d.get("publish_ts"),
            )
        )
        return result

    def article(self, article_id: int) -> dict | None:
        if self.conn is None:
            return None
        row = self.conn.execute(
            "SELECT a.*, c.title AS collection_title FROM articles a "
            "LEFT JOIN article_collections ac ON ac.article_id=a.id "
            "LEFT JOIN collections c ON c.id=ac.collection_id WHERE a.id=? LIMIT 1",
            (article_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["publish_date"] = format_epoch_date(d.get("publish_ts"))
        return d

    def segments(self, article_id: int) -> list[dict]:
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT id, seq_no, plain_text, safe_html_fragment, char_start, char_end "
            "FROM article_segments WHERE article_id=? ORDER BY seq_no",
            (article_id,),
        ).fetchall()
        return _rows(rows)

    def neighbors(self, article_id: int) -> dict:
        """同文集内按章节顺序的上一篇/下一篇（仅 ready 文章）。"""
        result: dict[str, dict | None] = {"prev": None, "next": None}
        if self.conn is None:
            return result
        link = self.conn.execute(
            "SELECT collection_id FROM article_collections "
            "WHERE article_id=? ORDER BY collection_id LIMIT 1",
            (article_id,),
        ).fetchone()
        if link is None:
            return result
        collection_id = int(link["collection_id"])
        rows = self.conn.execute(
            "SELECT a.id, a.title, a.publish_ts, ac.serial_no "
            "FROM articles a JOIN article_collections ac ON ac.article_id=a.id "
            "WHERE ac.collection_id=? AND a.status='ready'",
            (collection_id,),
        ).fetchall()
        ordered = sorted(
            (dict(row) for row in rows),
            key=lambda row: chapter_sort_key(
                row.get("title") or "",
                row.get("serial_no"),
                row["id"],
                row.get("publish_ts"),
            ),
        )
        for index, row in enumerate(ordered):
            if row["id"] != article_id:
                continue
            if index > 0:
                result["prev"] = {
                    "id": ordered[index - 1]["id"],
                    "title": ordered[index - 1]["title"],
                }
            if index + 1 < len(ordered):
                result["next"] = {
                    "id": ordered[index + 1]["id"],
                    "title": ordered[index + 1]["title"],
                }
            break
        return result

    def delete_article(self, article_id: int) -> dict:
        """删除一篇文章，并清理全文索引、专题库出处和个人阅读状态。

        删除前会把正文、段落和文集信息写到 ``deleted_articles`` 快照目录；
        内容库本身只删除这一篇，外键级联清理段落、清单和 FTS。墓碑保证
        后续自动采集不会把用户主动删除的文章重新收录。
        """
        if not self.config.paths.content_db.exists():
            raise StorageError("内容库尚未初始化，无法删除文章。")
        conn = initialize_content_db(self.config.paths.content_db)
        try:
            repo = ArticleRepository(conn)
            article = repo.get(int(article_id))
            if article is None:
                raise StorageError("文章不存在或已被删除。")
            links = conn.execute(
                "SELECT ac.collection_id, c.title AS collection_title, ac.serial_no "
                "FROM article_collections ac JOIN collections c ON c.id=ac.collection_id "
                "WHERE ac.article_id=? ORDER BY ac.collection_id",
                (int(article_id),),
            ).fetchall()
            segments = conn.execute(
                "SELECT seq_no, plain_text, safe_html_fragment, char_start, char_end, "
                "segment_hash FROM article_segments WHERE article_id=? ORDER BY seq_no",
                (int(article_id),),
            ).fetchall()
            snapshot_path = self._write_deleted_snapshot(article, links, segments)
            collection_ids = repo.delete_with_tombstone(
                int(article_id), now_iso(), str(snapshot_path)
            )
        finally:
            conn.close()
        warnings = self._cleanup_deleted_article(int(article_id), collection_ids)
        return {
            "article_id": int(article_id),
            "title": article["title"] or "",
            "collection_ids": collection_ids,
            "collection_titles": [row["collection_title"] or "" for row in links],
            "segments": len(segments),
            "snapshot_path": str(snapshot_path),
            "warnings": warnings,
        }

    def _write_deleted_snapshot(self, article, links, segments) -> Path:
        folder = self.config.paths.root / "deleted_articles"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (
                "article_%s_%s.json"
                % (article["id"], now().strftime("%Y%m%d_%H%M%S"))
            )
            payload = {
                "deleted_at": now_iso(),
                "article": dict(article),
                "collections": [dict(row) for row in links],
                "segments": [dict(row) for row in segments],
                "note": "阅读端删除文章前自动保存的正文快照",
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise StorageError("无法保存删除前快照，已取消删除：%s" % exc) from exc
        return path

    def _cleanup_deleted_article(
        self, article_id: int, collection_ids: list[int]
    ) -> list[str]:
        warnings = []
        if collection_ids and self.config.paths.topics_db.exists():
            try:
                store = TopicStore.open(self.config.paths.topics_db, readonly=False)
                try:
                    for collection_id in collection_ids:
                        store.remove_article_mentions(collection_id, article_id)
                        store.remove_article_context(collection_id, article_id)
                        store.delete_scan_state(collection_id, article_id)
                        store.prune_empty_pending(collection_id)
                finally:
                    store.close()
            except Exception as exc:  # 正文已删；派生层失败只提示，不回滚
                warnings.append("专题库出处清理失败：%s" % exc)
        try:
            self.state.delete_article_state(article_id)
        except Exception as exc:
            warnings.append("阅读进度/书签清理失败：%s" % exc)
        return warnings

    def save_progress(self, article_id: int, segment_id: int | None, position: int, percent: float):
        self.state.save_progress(article_id, segment_id, position, percent)

    def continue_reading(self) -> dict | None:
        rows = self.state.recent_progress(5)
        if not rows or self.conn is None:
            return None
        for row in rows:
            article = self.article(int(row["article_id"]))
            if article and article.get("status") == "ready":
                article["progress_percent"] = row["percent"]
                article["progress_position"] = row["position"]
                return article
        return None
