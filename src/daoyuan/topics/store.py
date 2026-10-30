# -*- coding: utf-8 -*-
"""专题库存储层：实体、别名、原文出处、审核状态和增量扫描状态。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..core.errors import StorageError
from ..core.timeutil import now_iso
from ..storage.db import connect, initialize_topic_db
from .catalog import KIND_LABELS, KINDS

STATUS_LABELS = {
    "pending": "待审核",
    "approved": "已收录",
    "rejected": "已忽略",
}
STATUSES = tuple(STATUS_LABELS)
SOURCES = ("rule", "manual", "ai")


def _clean(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


class TopicStore:
    """专题库连接。只读模式下数据库不存在时返回空库，不创建文件。"""

    def __init__(self, conn: sqlite3.Connection | None):
        self.conn = conn
        self._closed = False

    @classmethod
    def open(cls, path: str | Path, readonly: bool = False) -> "TopicStore":
        path = Path(path)
        if readonly:
            if not path.exists():
                return cls(None)
            try:
                return cls(connect(path, readonly=True))
            except StorageError:
                return cls(None)
        return cls(initialize_topic_db(path))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    @property
    def available(self) -> bool:
        return self.conn is not None

    # --- meta ---
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        if self.conn is None:
            return default
        row = self.conn.execute(
            "SELECT value FROM topic_meta WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else default

    def meta_set(self, key: str, value: str) -> None:
        if self.conn is None:
            return
        self.conn.execute(
            "INSERT INTO topic_meta(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def last_scan(self, collection_id: int) -> dict:
        import json

        raw = self.meta_get("last_scan:%s" % int(collection_id))
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def set_last_scan(self, collection_id: int, stats: dict) -> None:
        import json

        self.meta_set(
            "last_scan:%s" % int(collection_id),
            json.dumps(stats, ensure_ascii=False),
        )

    # --- lookup ---
    def find_entity(self, collection_id: int, kind: str, name: str):
        if self.conn is None:
            return None
        name = _clean(name, 80)
        return self.conn.execute(
            "SELECT e.* FROM topic_entities e "
            "LEFT JOIN topic_aliases a ON a.entity_id=e.id "
            "WHERE e.collection_id=? AND e.kind=? AND (e.name=? OR a.alias=?) "
            "ORDER BY CASE WHEN e.name=? THEN 0 ELSE 1 END, e.id LIMIT 1",
            (int(collection_id), kind, name, name, name),
        ).fetchone()

    def known_terms(self, collection_id: int) -> tuple[tuple[str, str, str], ...]:
        """返回已收录实体的 (词, 类型, 规范名)，包含规范名和全部别名。

        只使用已收录实体，避免把上一轮规则产生的待审核噪声当成“已知词”
        反复喂回抽取器。待审核候选会由正文规则自然重新生成。
        """
        if self.conn is None:
            return ()
        rows = self.conn.execute(
            "SELECT e.name, e.kind, a.alias FROM topic_entities e "
            "LEFT JOIN topic_aliases a ON a.entity_id=e.id "
            "WHERE e.collection_id=? AND e.status='approved'",
            (int(collection_id),),
        ).fetchall()
        terms: set[tuple[str, str, str]] = set()
        for row in rows:
            canonical = row["name"] or ""
            kind = row["kind"] or ""
            if not canonical or kind not in KINDS:
                continue
            # 剧情阶段/事件/伏笔的出处来自章节标签，不靠正文同名词匹配。
            if kind in ("arc", "event", "mystery"):
                continue
            terms.add((canonical, kind, canonical))
            alias = (row["alias"] or "").strip()
            if alias and alias != canonical:
                terms.add((alias, kind, canonical))
        return tuple(sorted(terms, key=lambda item: len(item[0]), reverse=True))

    # --- write ---
    def upsert_candidate(
        self,
        collection_id: int,
        kind: str,
        name: str,
        aliases: tuple[str, ...] | list[str] = (),
        mentions: tuple[object, ...] | list[object] = (),
        source: str = "rule",
    ) -> tuple[int | None, bool, int]:
        """写入一个候选实体及其原文提及。

        返回 (entity_id, 是否新建, 新增提及数)。
        已忽略的实体不会因为再次扫描而复活；已收录/待审核实体只累积出处。
        """
        if self.conn is None:
            return None, False, 0
        if kind not in KINDS:
            raise ValueError("未知专题类型：%s" % kind)
        name = _clean(name, 80)
        if len(name) < 2:
            return None, False, 0
        source = source if source in SOURCES else "rule"
        ts = now_iso()
        row = self.find_entity(collection_id, kind, name)
        created = False
        if row is None and kind == "person" and len(name) >= 3:
            # 先出现「清川」，后来出现「顾清川」：自动升级为全名，旧名保留为别名。
            row = self.conn.execute(
                "SELECT * FROM topic_entities WHERE collection_id=? AND kind='person' "
                "AND status IN ('pending','approved') AND length(name)>=2 "
                "AND ? LIKE '%' || name ORDER BY length(name) DESC, id LIMIT 1",
                (int(collection_id), name),
            ).fetchone()
            if row is not None:
                old_name = row["name"]
                self.conn.execute(
                    "UPDATE topic_entities SET name=?, updated_at=? WHERE id=?",
                    (name, ts, row["id"]),
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO topic_aliases(entity_id, alias) VALUES (?,?)",
                    (row["id"], old_name),
                )
                row = self.conn.execute(
                    "SELECT * FROM topic_entities WHERE id=?", (row["id"],)
                ).fetchone()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO topic_entities(collection_id, kind, name, summary, status, "
                "source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    int(collection_id),
                    kind,
                    name,
                    "",
                    "pending",
                    source,
                    ts,
                    ts,
                ),
            )
            entity_id = int(cur.lastrowid)
            created = True
        else:
            entity_id = int(row["id"])
            if row["status"] == "rejected":
                return entity_id, False, 0

        alias_values = {name}
        for alias in aliases:
            alias = _clean(alias, 80)
            if len(alias) >= 2 and alias != name:
                alias_values.add(alias)
        for alias in alias_values:
            if alias == name:
                continue
            conflict = self.conn.execute(
                "SELECT a.entity_id FROM topic_aliases a "
                "JOIN topic_entities e ON e.id=a.entity_id "
                "WHERE e.collection_id=? AND e.kind=? AND a.alias=? AND a.entity_id<>? "
                "LIMIT 1",
                (int(collection_id), kind, alias, entity_id),
            ).fetchone()
            if conflict is None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO topic_aliases(entity_id, alias) VALUES (?,?)",
                    (entity_id, alias),
                )

        added = 0
        for mention in mentions:
            segment_id = getattr(mention, "segment_id", None)
            quote = _clean(getattr(mention, "quote", ""), 300)
            surface = _clean(getattr(mention, "surface", name), 80) or name
            if not quote:
                continue
            exists = self.conn.execute(
                "SELECT 1 FROM topic_mentions WHERE entity_id=? AND article_id=? "
                "AND segment_id IS ? AND quote=? LIMIT 1",
                (entity_id, int(getattr(mention, "article_id", 0)), segment_id, quote),
            ).fetchone()
            if exists:
                continue
            self.conn.execute(
                "INSERT INTO topic_mentions(entity_id, article_id, segment_id, surface, "
                "quote, created_at) VALUES (?,?,?,?,?,?)",
                (
                    entity_id,
                    int(getattr(mention, "article_id", 0)),
                    segment_id,
                    surface,
                    quote,
                    ts,
                ),
            )
            added += 1
        self.conn.execute(
            "UPDATE topic_entities SET updated_at=? WHERE id=?", (ts, entity_id)
        )
        self.conn.commit()
        return entity_id, created, added

    def review(self, entity_id: int, status: str) -> None:
        if self.conn is None:
            return
        if status not in STATUSES:
            raise ValueError("未知审核状态：%s" % status)
        self.conn.execute(
            "UPDATE topic_entities SET status=?, updated_at=? WHERE id=?",
            (status, now_iso(), int(entity_id)),
        )
        self.conn.commit()

    def manual_add(
        self,
        collection_id: int,
        kind: str,
        name: str,
        summary: str = "",
    ) -> int | None:
        if self.conn is None:
            return None
        if kind not in KINDS:
            raise ValueError("未知专题类型：%s" % kind)
        name = _clean(name, 80)
        if len(name) < 2:
            raise ValueError("名称至少 2 个字。")
        ts = now_iso()
        row = self.find_entity(collection_id, kind, name)
        if row is not None:
            self.conn.execute(
                "UPDATE topic_entities SET status='approved', summary=?, updated_at=? "
                "WHERE id=?",
                (_clean(summary, 500), ts, row["id"]),
            )
            self.conn.commit()
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO topic_entities(collection_id, kind, name, summary, status, "
            "source, created_at, updated_at) VALUES (?,?,?,?, 'approved', 'manual', ?, ?)",
            (int(collection_id), kind, name, _clean(summary, 500), ts, ts),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def curate_entity(
        self,
        collection_id: int,
        kind: str,
        name: str,
        summary: str = "",
        aliases: tuple[str, ...] | list[str] = (),
        group_key: str = "",
        sort_order: int = 0,
        meta: dict | None = None,
    ) -> int:
        """补录/更新一个已收录实体，保留已有出处和人工备注。

        与 ``manual_add`` 的区别：可以写入分组、排序和结构化元数据，也允许
        一个字的专名（例如「殄」）。只更新实体本身，不删除任何出处。
        """
        if self.conn is None:
            raise ValueError("专题库不可用。")
        if kind not in KINDS:
            raise ValueError("未知专题类型：%s" % kind)
        name = _clean(name, 80)
        if len(name) < 1:
            raise ValueError("名称不能为空。")
        ts = now_iso()
        row = self.find_entity(collection_id, kind, name)
        if row is not None:
            entity_id = int(row["id"])
            old_name = row["name"] or ""
            if old_name and old_name != name:
                conflict = self.conn.execute(
                    "SELECT id FROM topic_entities WHERE collection_id=? AND kind=? "
                    "AND name=? AND id<>?",
                    (int(collection_id), kind, name, entity_id),
                ).fetchone()
                if conflict is not None:
                    raise ValueError("同一类型下已有同名实体：%s" % name)
                self.conn.execute(
                    "UPDATE topic_entities SET name=?, updated_at=? WHERE id=?",
                    (name, ts, entity_id),
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO topic_aliases(entity_id, alias) VALUES (?,?)",
                    (entity_id, old_name),
                )
            summary_value = _clean(summary, 500) or (row["summary"] or "")
            group_value = (group_key or row["group_key"] or "").strip()
            sort_value = int(sort_order or row["sort_order"] or 0)
            meta_value = (
                json.dumps(meta, ensure_ascii=False)
                if meta is not None
                else (row["meta_json"] or "")
            )
            self.conn.execute(
                "UPDATE topic_entities SET summary=?, group_key=?, sort_order=?, "
                "meta_json=?, status='approved', source='manual', updated_at=? "
                "WHERE id=?",
                (
                    summary_value,
                    group_value,
                    sort_value,
                    meta_value,
                    ts,
                    entity_id,
                ),
            )
        else:
            cur = self.conn.execute(
                "INSERT INTO topic_entities(collection_id, kind, name, summary, status, "
                "source, group_key, sort_order, meta_json, created_at, updated_at) "
                "VALUES (?,?,?,?, 'approved', 'manual', ?,?,?,?,?)",
                (
                    int(collection_id),
                    kind,
                    name,
                    _clean(summary, 500),
                    (group_key or "").strip(),
                    int(sort_order or 0),
                    json.dumps(meta, ensure_ascii=False) if meta is not None else "",
                    ts,
                    ts,
                ),
            )
            entity_id = int(cur.lastrowid)

        alias_values = {name}
        for alias in aliases:
            alias = _clean(alias, 80)
            if len(alias) >= 1 and alias != name:
                alias_values.add(alias)
        for alias in alias_values:
            if alias == name:
                continue
            conflict = self.conn.execute(
                "SELECT a.entity_id FROM topic_aliases a "
                "JOIN topic_entities e ON e.id=a.entity_id "
                "WHERE e.collection_id=? AND e.kind=? AND a.alias=? AND a.entity_id<>? "
                "LIMIT 1",
                (int(collection_id), kind, alias, entity_id),
            ).fetchone()
            if conflict is None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO topic_aliases(entity_id, alias) VALUES (?,?)",
                    (entity_id, alias),
                )
        self.conn.commit()
        return entity_id

    def merge(
        self,
        source_id: int,
        target_id: int,
        *,
        allow_cross_kind: bool = False,
    ) -> None:
        """把来源实体并入目标实体，保留别名与全部原文出处。

        allow_cross_kind 仅供「改类型」时合并同名实体使用；普通合并仍要求
        同一文集、同一类型，避免把人物和地点误并在一起。
        """
        if self.conn is None:
            return
        if int(source_id) == int(target_id):
            return
        source = self.conn.execute(
            "SELECT * FROM topic_entities WHERE id=?", (int(source_id),)
        ).fetchone()
        target = self.conn.execute(
            "SELECT * FROM topic_entities WHERE id=?", (int(target_id),)
        ).fetchone()
        if source is None or target is None:
            raise ValueError("要合并的实体不存在。")
        if source["collection_id"] != target["collection_id"]:
            raise ValueError("只能合并同一文集内的实体。")
        if not allow_cross_kind and source["kind"] != target["kind"]:
            raise ValueError("只能合并同一文集、同一类型的实体。")
        ts = now_iso()
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO topic_aliases(entity_id, alias) "
                "SELECT ?, alias FROM topic_aliases WHERE entity_id=?",
                (target_id, source_id),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO topic_aliases(entity_id, alias) VALUES (?,?)",
                (target_id, source["name"]),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO topic_mentions(entity_id, article_id, segment_id, "
                "surface, quote, created_at) SELECT ?, article_id, segment_id, surface, "
                "quote, created_at FROM topic_mentions WHERE entity_id=?",
                (target_id, source_id),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO topic_article_tags(collection_id, article_id, "
                "entity_id, note, sort_order, created_at) "
                "SELECT collection_id, article_id, ?, note, sort_order, created_at "
                "FROM topic_article_tags WHERE entity_id=?",
                (target_id, source_id),
            )
            summary = target["summary"] or ""
            if source["summary"] and source["summary"] not in summary:
                summary = (summary + "；" + source["summary"]).strip("；")
            # 合并时保留更强的审核状态：已收录 > 待审核 > 已忽略。
            order = {"rejected": 0, "pending": 1, "approved": 2}
            status = target["status"]
            if order.get(source["status"], 0) > order.get(status, 0):
                status = source["status"]
            self.conn.execute(
                "UPDATE topic_entities SET summary=?, status=?, updated_at=? WHERE id=?",
                (_clean(summary, 500), status, ts, target_id),
            )
            self.conn.execute("DELETE FROM topic_entities WHERE id=?", (source_id,))
            self.conn.commit()
        except sqlite3.Error as exc:
            self.conn.rollback()
            raise StorageError("合并专题实体失败：%s" % exc) from exc

    def reclassify(self, entity_id: int, kind: str) -> int:
        """调整实体类型，保留别名、备注和全部原文出处。

        如果目标类型下已有同名实体，会自动并入该实体，返回目标实体 ID；
        如果名称或别名已被目标类型下的其他实体占用，则拒绝并提示先处理冲突。
        """
        if self.conn is None:
            raise ValueError("专题库不可用。")
        if kind not in KINDS:
            raise ValueError("未知专题类型：%s" % kind)
        entity_id = int(entity_id)
        source = self.conn.execute(
            "SELECT * FROM topic_entities WHERE id=?", (entity_id,)
        ).fetchone()
        if source is None:
            raise ValueError("要改类型的实体不存在。")
        if source["kind"] == kind:
            return entity_id
        target = self.conn.execute(
            "SELECT * FROM topic_entities WHERE collection_id=? AND kind=? AND name=?",
            (source["collection_id"], kind, source["name"]),
        ).fetchone()
        if target is not None:
            self.merge(entity_id, int(target["id"]), allow_cross_kind=True)
            return int(target["id"])

        aliases = [
            row[0]
            for row in self.conn.execute(
                "SELECT alias FROM topic_aliases WHERE entity_id=?", (entity_id,)
            ).fetchall()
        ]
        names = sorted({source["name"], *aliases})
        placeholders = ",".join("?" for _ in names)
        conflict = self.conn.execute(
            "SELECT e.name FROM topic_entities e WHERE e.collection_id=? AND e.kind=? "
            "AND (e.name IN (%s) OR EXISTS (SELECT 1 FROM topic_aliases a "
            "WHERE a.entity_id=e.id AND a.alias IN (%s))) LIMIT 1"
            % (placeholders, placeholders),
            (source["collection_id"], kind, *names, *names),
        ).fetchone()
        if conflict is not None:
            raise ValueError(
                "目标类型下已有实体「%s」使用相同名称或别名，请先合并或忽略冲突实体。"
                % conflict["name"]
            )
        self.conn.execute(
            "UPDATE topic_entities SET kind=?, updated_at=? WHERE id=?",
            (kind, now_iso(), entity_id),
        )
        self.conn.commit()
        return entity_id

    # --- read ---
    def entities(
        self,
        collection_id: int | None = None,
        kind: str | None = None,
        status: str | None = None,
        query: str = "",
        limit: int = 500,
    ) -> list[dict]:
        if self.conn is None:
            return []
        sql = (
            "SELECT e.*, "
            "(SELECT count(*) FROM topic_mentions m WHERE m.entity_id=e.id) AS mention_count, "
            "(SELECT count(DISTINCT m.article_id) FROM topic_mentions m "
            " WHERE m.entity_id=e.id) AS article_count, "
            "(SELECT count(*) FROM topic_article_tags t WHERE t.entity_id=e.id) "
            " AS tag_count, "
            "(SELECT max(m.created_at) FROM topic_mentions m WHERE m.entity_id=e.id) "
            " AS last_mention_at "
            "FROM topic_entities e WHERE 1=1"
        )
        params: list = []
        if collection_id is not None:
            sql += " AND e.collection_id=?"
            params.append(int(collection_id))
        if kind:
            sql += " AND e.kind=?"
            params.append(kind)
        if status:
            sql += " AND e.status=?"
            params.append(status)
        term = (query or "").strip()
        if term:
            pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            sql += (
                " AND (e.name LIKE ? ESCAPE '\\' OR EXISTS ("
                "SELECT 1 FROM topic_aliases a WHERE a.entity_id=e.id "
                "AND a.alias LIKE ? ESCAPE '\\'))"
            )
            params.extend([pattern, pattern])
        sql += " ORDER BY mention_count DESC, e.name ASC LIMIT ?"
        params.append(max(1, int(limit)))
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def entity(self, entity_id: int) -> dict | None:
        if self.conn is None:
            return None
        row = self.conn.execute(
            "SELECT e.*, "
            "(SELECT count(*) FROM topic_mentions m WHERE m.entity_id=e.id) AS mention_count, "
            "(SELECT count(DISTINCT m.article_id) FROM topic_mentions m "
            " WHERE m.entity_id=e.id) AS article_count, "
            "(SELECT count(*) FROM topic_article_tags t WHERE t.entity_id=e.id) "
            " AS tag_count "
            "FROM topic_entities e WHERE e.id=?",
            (int(entity_id),),
        ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["aliases"] = [
            r[0]
            for r in self.conn.execute(
                "SELECT alias FROM topic_aliases WHERE entity_id=? ORDER BY alias",
                (int(entity_id),),
            ).fetchall()
            if r[0] and r[0] != value.get("name")
        ]
        value["kind_label"] = KIND_LABELS.get(value.get("kind"), value.get("kind"))
        value["status_label"] = STATUS_LABELS.get(
            value.get("status"), value.get("status")
        )
        try:
            value["meta"] = json.loads(value.get("meta_json") or "{}")
        except ValueError:
            value["meta"] = {}
        return value

    def mentions(self, entity_id: int, limit: int = 100) -> list[dict]:
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT id, entity_id, article_id, segment_id, surface, quote, created_at "
            "FROM topic_mentions WHERE entity_id=? ORDER BY article_id DESC, id DESC LIMIT ?",
            (int(entity_id), max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- 章节标签与“本章专题” ---
    def set_article_note(
        self,
        collection_id: int,
        article_id: int,
        summary: str,
    ) -> None:
        if self.conn is None:
            return
        self.conn.execute(
            "INSERT INTO topic_article_notes(article_id, collection_id, summary, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(article_id) DO UPDATE SET "
            "summary=excluded.summary, collection_id=excluded.collection_id, "
            "updated_at=excluded.updated_at",
            (
                int(article_id),
                int(collection_id),
                _clean(summary, 500),
                now_iso(),
            ),
        )
        self.conn.commit()

    def tag_article(
        self,
        collection_id: int,
        article_id: int,
        entity_id: int,
        note: str = "",
        sort_order: int = 0,
    ) -> None:
        if self.conn is None:
            return
        entity = self.conn.execute(
            "SELECT id, collection_id, status FROM topic_entities WHERE id=?",
            (int(entity_id),),
        ).fetchone()
        if entity is None:
            raise ValueError("要标注的专题实体不存在。")
        if int(entity["collection_id"]) != int(collection_id):
            raise ValueError("章节标签只能使用同一文集的专题实体。")
        if entity["status"] != "approved":
            raise ValueError("只有已收录的专题实体才能标注到章节。")
        self.conn.execute(
            "INSERT INTO topic_article_tags(collection_id, article_id, entity_id, note, "
            "sort_order, created_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(article_id, entity_id) DO UPDATE SET "
            "note=excluded.note, sort_order=excluded.sort_order",
            (
                int(collection_id),
                int(article_id),
                int(entity_id),
                _clean(note, 300),
                int(sort_order or 0),
                now_iso(),
            ),
        )
        self.conn.commit()

    def article_context(self, article_id: int) -> dict:
        """阅读页右侧栏需要的章节摘要、标签和已收录实体。"""
        empty = {"note": None, "tags": [], "entities": [], "collection_id": None}
        if self.conn is None:
            return empty
        note_row = self.conn.execute(
            "SELECT article_id, collection_id, summary, updated_at "
            "FROM topic_article_notes WHERE article_id=?",
            (int(article_id),),
        ).fetchone()
        note = dict(note_row) if note_row else None
        tag_rows = self.conn.execute(
            "SELECT t.id, t.article_id, t.collection_id, t.entity_id, t.note, "
            "t.sort_order, e.name, e.kind, e.group_key, e.summary, e.meta_json "
            "FROM topic_article_tags t JOIN topic_entities e ON e.id=t.entity_id "
            "WHERE t.article_id=? AND e.status='approved' "
            "ORDER BY t.sort_order, e.kind, e.name",
            (int(article_id),),
        ).fetchall()
        entity_rows = self.conn.execute(
            "SELECT e.id, e.name, e.kind, e.group_key, e.summary, "
            "count(*) AS mention_count, "
            "(SELECT m2.quote FROM topic_mentions m2 WHERE m2.entity_id=e.id "
            " AND m2.article_id=? ORDER BY m2.id LIMIT 1) AS sample "
            "FROM topic_mentions m JOIN topic_entities e ON e.id=m.entity_id "
            "WHERE m.article_id=? AND e.status='approved' "
            "GROUP BY e.id ORDER BY mention_count DESC, e.name",
            (int(article_id), int(article_id)),
        ).fetchall()
        return {
            "note": note,
            "tags": [dict(row) for row in tag_rows],
            "entities": [dict(row) for row in entity_rows],
            "collection_id": (
                int(note["collection_id"])
                if note
                else (
                    int(tag_rows[0]["collection_id"]) if tag_rows else None
                )
            ),
        }

    def entity_tag_articles(self, entity_id: int) -> list[dict]:
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT t.article_id, t.collection_id, t.note, t.sort_order, "
            "n.summary AS chapter_summary "
            "FROM topic_article_tags t "
            "LEFT JOIN topic_article_notes n ON n.article_id=t.article_id "
            "WHERE t.entity_id=? ORDER BY t.sort_order, t.article_id",
            (int(entity_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def remove_article_context(self, collection_id: int, article_id: int) -> None:
        if self.conn is None:
            return
        self.conn.execute(
            "DELETE FROM topic_article_tags WHERE collection_id=? AND article_id=?",
            (int(collection_id), int(article_id)),
        )
        self.conn.execute(
            "DELETE FROM topic_article_notes WHERE collection_id=? AND article_id=?",
            (int(collection_id), int(article_id)),
        )
        self.conn.commit()

    def stats(self, collection_id: int | None = None) -> dict:
        if self.conn is None:
            return {
                "entities": 0,
                "pending": 0,
                "approved": 0,
                "rejected": 0,
                "mentions": 0,
                "scanned_articles": 0,
                "by_kind": {},
                "last_scan": {},
            }
        where = ""
        params: list = []
        if collection_id is not None:
            where = " WHERE e.collection_id=?"
            params.append(int(collection_id))
        rows = self.conn.execute(
            "SELECT e.status, e.kind, count(*) AS n FROM topic_entities e"
            + where
            + " GROUP BY e.status, e.kind",
            params,
        ).fetchall()
        pending = approved = rejected = entities = 0
        by_kind: dict[str, int] = {}
        for row in rows:
            count = int(row["n"] or 0)
            entities += count
            by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + count
            if row["status"] == "pending":
                pending += count
            elif row["status"] == "approved":
                approved += count
            elif row["status"] == "rejected":
                rejected += count
        mention_sql = "SELECT count(*) FROM topic_mentions m"
        mention_params: list = []
        if collection_id is not None:
            mention_sql += " JOIN topic_entities e ON e.id=m.entity_id WHERE e.collection_id=?"
            mention_params.append(int(collection_id))
        mentions = int(
            self.conn.execute(mention_sql, mention_params).fetchone()[0] or 0
        )
        if collection_id is not None:
            scanned = int(
                self.conn.execute(
                    "SELECT count(*) FROM topic_article_scan WHERE collection_id=?",
                    (int(collection_id),),
                ).fetchone()[0]
                or 0
            )
        else:
            scanned = int(
                self.conn.execute(
                    "SELECT count(*) FROM topic_article_scan"
                ).fetchone()[0]
                or 0
            )
        return {
            "entities": entities,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "mentions": mentions,
            "scanned_articles": scanned,
            "by_kind": by_kind,
            "last_scan": self.last_scan(collection_id) if collection_id is not None else {},
        }

    def approved_stats(self, collection_id: int | None = None) -> dict:
        """阅读端统计：只统计已收录实体及其出处。"""
        if self.conn is None:
            return {"entities": 0, "mentions": 0, "by_kind": {}}
        where = " WHERE e.status='approved'"
        params: list = []
        if collection_id is not None:
            where += " AND e.collection_id=?"
            params.append(int(collection_id))
        rows = self.conn.execute(
            "SELECT e.kind, count(*) AS n FROM topic_entities e"
            + where
            + " GROUP BY e.kind",
            params,
        ).fetchall()
        by_kind = {row["kind"]: int(row["n"] or 0) for row in rows}
        mention_sql = (
            "SELECT count(*) FROM topic_mentions m "
            "JOIN topic_entities e ON e.id=m.entity_id"
            + where
        )
        mentions = int(self.conn.execute(mention_sql, params).fetchone()[0] or 0)
        return {
            "entities": sum(by_kind.values()),
            "mentions": mentions,
            "by_kind": by_kind,
        }

    def prune_empty_pending(self, collection_id: int) -> int:
        """删除没有任何原文出处的待审核实体；保留已收录和已忽略状态。"""
        if self.conn is None:
            return 0
        cur = self.conn.execute(
            "DELETE FROM topic_entities WHERE collection_id=? AND status='pending' "
            "AND NOT EXISTS (SELECT 1 FROM topic_mentions m "
            "WHERE m.entity_id=topic_entities.id)",
            (int(collection_id),),
        )
        self.conn.commit()
        return int(cur.rowcount)

    # --- scan state ---
    def scan_state(self, collection_id: int, article_id: int):
        if self.conn is None:
            return None
        return self.conn.execute(
            "SELECT * FROM topic_article_scan WHERE collection_id=? AND article_id=?",
            (int(collection_id), int(article_id)),
        ).fetchone()

    def set_scan_state(
        self,
        collection_id: int,
        article_id: int,
        text_hash: str,
        extractor_version: int,
    ) -> None:
        if self.conn is None:
            return
        self.conn.execute(
            "INSERT INTO topic_article_scan(collection_id, article_id, text_hash, "
            "extractor_version, scanned_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(collection_id, article_id) DO UPDATE SET "
            "text_hash=excluded.text_hash, extractor_version=excluded.extractor_version, "
            "scanned_at=excluded.scanned_at",
            (
                int(collection_id),
                int(article_id),
                text_hash or "",
                int(extractor_version),
                now_iso(),
            ),
        )
        self.conn.commit()

    def delete_scan_state(self, collection_id: int, article_id: int) -> None:
        if self.conn is None:
            return
        self.conn.execute(
            "DELETE FROM topic_article_scan WHERE collection_id=? AND article_id=?",
            (int(collection_id), int(article_id)),
        )
        self.conn.commit()

    def remove_article_mentions(self, collection_id: int, article_id: int) -> int:
        if self.conn is None:
            return 0
        cur = self.conn.execute(
            "DELETE FROM topic_mentions WHERE article_id=? AND entity_id IN ("
            "SELECT id FROM topic_entities WHERE collection_id=?)",
            (int(article_id), int(collection_id)),
        )
        self.conn.commit()
        return int(cur.rowcount)

    def scan_state_ids(self, collection_id: int) -> set[int]:
        if self.conn is None:
            return set()
        rows = self.conn.execute(
            "SELECT article_id FROM topic_article_scan WHERE collection_id=?",
            (int(collection_id),),
        ).fetchall()
        return {int(r[0]) for r in rows}
