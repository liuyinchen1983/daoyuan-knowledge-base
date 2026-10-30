# -*- coding: utf-8 -*-
"""内容库仓储。所有写操作显式提交，逐篇事务。"""
from __future__ import annotations

import sqlite3

from ..core.models import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    Collection,
    ListingItem,
    ParsedArticle,
    Source,
)
from ..core.timeutil import now_iso


def _source_from_row(row) -> Source:
    return Source(
        id=row["id"],
        biz=row["biz"],
        account_name=row["account_name"],
        account_id=row["account_id"],
        source_type=row["source_type"],
        verified_state=row["verified_state"],
        last_checked_at=row["last_checked_at"],
    )


def _collection_from_row(row) -> Collection:
    return Collection(
        id=row["id"],
        source_id=row["source_id"],
        album_id=row["album_id"],
        title=row["title"],
        description=row["description"],
        sort_mode=row["sort_mode"],
        expected_count=row["expected_count"],
        is_updating=None if row["is_updating"] is None else bool(row["is_updating"]),
        enabled=bool(row["enabled"]),
        last_checked_at=row["last_checked_at"],
    )


class SourceRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(
        self,
        biz: str | None,
        account_name: str,
        account_id: str | None = None,
        source_type: str = "wechat_mp",
        verified_state: str = "verified",
    ) -> int:
        ts = now_iso()
        row = None
        if biz:
            row = self.conn.execute("SELECT id FROM sources WHERE biz = ?", (biz,)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE sources SET account_name=?, account_id=?, source_type=?, "
                "verified_state=?, updated_at=? WHERE id=?",
                (account_name, account_id, source_type, verified_state, ts, row["id"]),
            )
            self.conn.commit()
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO sources(biz, account_name, account_id, source_type, verified_state, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (biz, account_name, account_id, source_type, verified_state, ts, ts),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get(self, source_id: int) -> Source | None:
        row = self.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return _source_from_row(row) if row else None

    def get_by_biz(self, biz: str) -> Source | None:
        row = self.conn.execute("SELECT * FROM sources WHERE biz=?", (biz,)).fetchone()
        return _source_from_row(row) if row else None

    def list_all(self) -> list[Source]:
        rows = self.conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
        return [_source_from_row(r) for r in rows]

    def touch_checked(self, source_id: int) -> None:
        self.conn.execute(
            "UPDATE sources SET last_checked_at=?, updated_at=? WHERE id=?",
            (now_iso(), now_iso(), source_id),
        )
        self.conn.commit()


class CollectionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(
        self,
        source_id: int,
        album_id: str,
        title: str,
        description: str | None = None,
        sort_mode: str | None = None,
        expected_count: int | None = None,
        is_updating: bool | None = None,
    ) -> int:
        ts = now_iso()
        row = self.conn.execute(
            "SELECT id FROM collections WHERE source_id=? AND album_id=?",
            (source_id, album_id),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE collections SET title=?, description=COALESCE(?, description), "
                "sort_mode=COALESCE(?, sort_mode), expected_count=COALESCE(?, expected_count), "
                "is_updating=?, updated_at=? WHERE id=?",
                (
                    title,
                    description,
                    sort_mode,
                    expected_count,
                    None if is_updating is None else int(is_updating),
                    ts,
                    row["id"],
                ),
            )
            self.conn.commit()
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO collections(source_id, album_id, title, description, sort_mode, "
            "expected_count, is_updating, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,1,?,?)",
            (
                source_id,
                album_id,
                title,
                description,
                sort_mode or "unknown",
                expected_count,
                None if is_updating is None else int(is_updating),
                ts,
                ts,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get(self, collection_id: int) -> Collection | None:
        row = self.conn.execute(
            "SELECT * FROM collections WHERE id=?", (collection_id,)
        ).fetchone()
        return _collection_from_row(row) if row else None

    def get_by_album(self, source_id: int, album_id: str) -> Collection | None:
        row = self.conn.execute(
            "SELECT * FROM collections WHERE source_id=? AND album_id=?",
            (source_id, album_id),
        ).fetchone()
        return _collection_from_row(row) if row else None

    def list_all(self, enabled_only: bool = False) -> list[Collection]:
        sql = "SELECT * FROM collections"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id"
        rows = self.conn.execute(sql).fetchall()
        return [_collection_from_row(r) for r in rows]

    def set_sort_mode(self, collection_id: int, sort_mode: str) -> None:
        self.conn.execute(
            "UPDATE collections SET sort_mode=?, updated_at=? WHERE id=?",
            (sort_mode, now_iso(), collection_id),
        )
        self.conn.commit()

    def touch_checked(self, collection_id: int) -> None:
        self.conn.execute(
            "UPDATE collections SET last_checked_at=?, updated_at=? WHERE id=?",
            (now_iso(), now_iso(), collection_id),
        )
        self.conn.commit()


class ArticleRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, article_id: int):
        return self.conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()

    def get_by_key(self, source_id: int, msgid: str, itemidx: str):
        return self.conn.execute(
            "SELECT * FROM articles WHERE source_id=? AND msgid=? AND itemidx=?",
            (source_id, msgid, itemidx),
        ).fetchone()

    def list_for_collection(
        self, collection_id: int, status: str | None = None, limit: int = 100, offset: int = 0
    ):
        sql = (
            "SELECT a.* FROM articles a "
            "JOIN article_collections ac ON ac.article_id = a.id "
            "WHERE ac.collection_id = ?"
        )
        params: list = [collection_id]
        if status:
            sql += " AND a.status = ?"
            params.append(status)
        sql += " ORDER BY ac.serial_no ASC, a.publish_ts ASC, a.id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self.conn.execute(sql, params).fetchall()

    def stats_for_collection(self, collection_id: int) -> dict:
        row = self.conn.execute(
            "SELECT count(*) AS total, "
            "sum(CASE WHEN a.status='ready' THEN 1 ELSE 0 END) AS ready, "
            "sum(CASE WHEN a.status='pending' THEN 1 ELSE 0 END) AS pending, "
            "sum(CASE WHEN a.status='failed' THEN 1 ELSE 0 END) AS failed, "
            "sum(CASE WHEN a.status='unavailable' THEN 1 ELSE 0 END) AS unavailable "
            "FROM articles a JOIN article_collections ac ON ac.article_id=a.id "
            "WHERE ac.collection_id=?",
            (collection_id,),
        ).fetchone()
        return {k: int(row[k] or 0) for k in ("total", "ready", "pending", "failed", "unavailable")}

    def recent(self, limit: int = 20):
        return self.conn.execute(
            "SELECT a.*, c.title AS collection_title FROM articles a "
            "JOIN article_collections ac ON ac.article_id=a.id "
            "JOIN collections c ON c.id=ac.collection_id "
            "WHERE a.status='ready' ORDER BY a.publish_ts DESC, a.id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def upsert_discovered(
        self,
        source_id: int,
        item: ListingItem,
        collection_id: int,
        list_order: int,
        run_id: int | None = None,
    ) -> tuple[int, bool]:
        """保存清单项。返回 (article_id, 是否新发现)。不改变已成功正文。"""
        ts = now_iso()
        row = self.get_by_key(source_id, item.msgid, item.itemidx)
        is_new = row is None
        if row:
            article_id = int(row["id"])
            self.conn.execute(
                "UPDATE articles SET title=?, source_url=?, updated_at=? WHERE id=?",
                (item.title or row["title"], item.url or row["source_url"], ts, article_id),
            )
        else:
            cur = self.conn.execute(
                "INSERT INTO articles(source_id, msgid, itemidx, title, source_url, "
                "publish_ts, publish_time_source, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    item.msgid,
                    item.itemidx,
                    item.title,
                    item.url,
                    item.create_time,
                    "album_create_time" if item.create_time else None,
                    STATUS_PENDING,
                    ts,
                    ts,
                ),
            )
            article_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO article_collections(article_id, collection_id, serial_no, is_current, "
            "first_seen_at, last_seen_at) VALUES (?,?,?,1,?,?) "
            "ON CONFLICT(article_id, collection_id) DO UPDATE SET "
            "serial_no=excluded.serial_no, is_current=1, last_seen_at=excluded.last_seen_at",
            (article_id, collection_id, list_order, ts, ts),
        )
        self.conn.execute(
            "INSERT INTO collection_listings(collection_id, article_id, msgid, itemidx, "
            "list_order, first_seen_at, last_seen_at, last_scan_run_id) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(collection_id, article_id) DO UPDATE SET "
            "list_order=excluded.list_order, last_seen_at=excluded.last_seen_at, "
            "last_scan_run_id=excluded.last_scan_run_id",
            (
                collection_id,
                article_id,
                item.msgid,
                item.itemidx,
                list_order,
                ts,
                ts,
                run_id,
            ),
        )
        self.conn.commit()
        return article_id, is_new

    def mark_ready(self, article_id: int, parsed: ParsedArticle, title_fallback: str = "") -> str:
        """写入正文。返回 created / updated / unchanged。"""
        row = self.get(article_id)
        if not row:
            raise ValueError("文章不存在：%s" % article_id)
        old_hash = row["text_hash"]
        ts = now_iso()
        if row["status"] == STATUS_READY and old_hash == parsed.text_hash:
            self.conn.execute(
                "UPDATE articles SET title=COALESCE(NULLIF(?, ''), title), "
                "page_author=COALESCE(?, page_author), "
                "publish_ts=COALESCE(?, publish_ts), "
                "publish_time_raw=COALESCE(?, publish_time_raw), "
                "publish_time_source=COALESCE(?, publish_time_source), "
                "title_date=COALESCE(?, title_date), "
                "source_url=COALESCE(NULLIF(?, ''), source_url), "
                "last_checked_at=?, last_error=NULL, updated_at=? WHERE id=?",
                (
                    parsed.title or title_fallback,
                    parsed.page_author,
                    parsed.publish_ts,
                    parsed.publish_time_raw,
                    parsed.publish_time_source,
                    parsed.title_date,
                    parsed.source_url,
                    ts,
                    ts,
                    article_id,
                ),
            )
            self.conn.commit()
            return "unchanged"

        result = "updated" if row["status"] == STATUS_READY else "created"
        if row["status"] == STATUS_READY and old_hash != parsed.text_hash:
            self.conn.execute(
                "INSERT INTO article_revisions(article_id, text_hash, content_text, "
                "captured_at, change_reason) VALUES (?,?,?,?,?)",
                (article_id, old_hash, row["text_content"], ts, "content_changed"),
            )
        self.conn.execute(
            "UPDATE articles SET title=COALESCE(NULLIF(?, ''), title), "
            "page_author=COALESCE(?, page_author), "
            "publish_ts=COALESCE(?, publish_ts), "
            "publish_time_raw=COALESCE(?, publish_time_raw), "
            "publish_time_source=COALESCE(?, publish_time_source), "
            "title_date=COALESCE(?, title_date), "
            "source_url=COALESCE(NULLIF(?, ''), source_url), "
            "text_content=?, safe_html=?, text_hash=?, "
            "status=?, first_collected_at=COALESCE(first_collected_at, ?), "
            "last_checked_at=?, last_error=NULL, updated_at=? WHERE id=?",
            (
                parsed.title or title_fallback,
                parsed.page_author,
                parsed.publish_ts,
                parsed.publish_time_raw,
                parsed.publish_time_source,
                parsed.title_date,
                parsed.source_url,
                parsed.plain_text,
                parsed.safe_html,
                parsed.text_hash,
                STATUS_READY,
                ts,
                ts,
                ts,
                article_id,
            ),
        )
        self.conn.execute("DELETE FROM article_segments WHERE article_id=?", (article_id,))
        for seg in parsed.segments:
            self.conn.execute(
                "INSERT INTO article_segments(article_id, seq_no, plain_text, safe_html_fragment, "
                "char_start, char_end, segment_hash) VALUES (?,?,?,?,?,?,?)",
                (
                    article_id,
                    seg.seq_no,
                    seg.plain_text,
                    seg.safe_html_fragment,
                    seg.char_start,
                    seg.char_end,
                    seg.segment_hash,
                ),
            )
        self.conn.commit()
        return result

    def mark_failed(self, article_id: int, error: str) -> None:
        ts = now_iso()
        self.conn.execute(
            "UPDATE articles SET status=?, last_error=?, last_checked_at=?, updated_at=? "
            "WHERE id=?",
            (STATUS_FAILED, error[:1000], ts, ts, article_id),
        )
        self.conn.commit()

    def mark_unavailable(self, article_id: int, error: str) -> None:
        ts = now_iso()
        self.conn.execute(
            "UPDATE articles SET status=?, last_error=?, last_checked_at=?, updated_at=? "
            "WHERE id=?",
            (STATUS_UNAVAILABLE, error[:1000], ts, ts, article_id),
        )
        self.conn.commit()

    # --- 删除与防复活 ---
    def deleted_keys(self, collection_id: int) -> set[tuple[str, str]]:
        """该文集里被用户删除过的文章键，自动采集时跳过。"""
        rows = self.conn.execute(
            "SELECT msgid, itemidx FROM deleted_articles WHERE collection_id=?",
            (int(collection_id),),
        ).fetchall()
        return {(str(row[0]), str(row[1])) for row in rows}

    def clear_deleted(
        self, source_id: int, msgid: str, itemidx: str, collection_id: int
    ) -> None:
        """人工重新收录时解除墓碑。"""
        self.conn.execute(
            "DELETE FROM deleted_articles WHERE source_id=? AND msgid=? AND itemidx=? "
            "AND collection_id=?",
            (int(source_id), str(msgid), str(itemidx), int(collection_id)),
        )
        self.conn.commit()

    def delete_with_tombstone(
        self, article_id: int, deleted_at: str, snapshot_path: str | None = None
    ) -> list[int]:
        """删除文章并写入墓碑。返回受影响的文集 ID。

        正文、段落、清单、修订等由外键级联删除；墓碑独立保存，避免下一次
        合集扫描把用户主动删除的文章重新收录。
        """
        row = self.get(article_id)
        if row is None:
            raise ValueError("文章不存在：%s" % article_id)
        links = self.conn.execute(
            "SELECT collection_id FROM article_collections WHERE article_id=? "
            "ORDER BY collection_id",
            (int(article_id),),
        ).fetchall()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for link in links:
                self.conn.execute(
                    "INSERT INTO deleted_articles(source_id, msgid, itemidx, "
                    "collection_id, title, source_url, deleted_at, snapshot_path) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(source_id, msgid, itemidx, collection_id) DO UPDATE SET "
                    "title=excluded.title, source_url=excluded.source_url, "
                    "deleted_at=excluded.deleted_at, "
                    "snapshot_path=excluded.snapshot_path",
                    (
                        int(row["source_id"]),
                        str(row["msgid"]),
                        str(row["itemidx"]),
                        int(link["collection_id"]),
                        row["title"] or "",
                        row["source_url"],
                        deleted_at,
                        snapshot_path,
                    ),
                )
            self.conn.execute("DELETE FROM articles WHERE id=?", (int(article_id),))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return [int(link["collection_id"]) for link in links]

    def reset_failed_to_pending(self, collection_id: int | None = None) -> int:
        if collection_id is None:
            cur = self.conn.execute(
                "UPDATE articles SET status=? WHERE status=?",
                (STATUS_PENDING, STATUS_FAILED),
            )
        else:
            cur = self.conn.execute(
                "UPDATE articles SET status=? WHERE status=? AND id IN ("
                "SELECT article_id FROM article_collections WHERE collection_id=?)",
                (STATUS_PENDING, STATUS_FAILED, collection_id),
            )
        self.conn.commit()
        return int(cur.rowcount)
