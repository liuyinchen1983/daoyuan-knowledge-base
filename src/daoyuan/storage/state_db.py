# -*- coding: utf-8 -*-
"""个人状态库：阅读进度、书签、设置与问答会话。与内容库分离。"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from ..core.timeutil import now, now_iso
from .db import initialize_state_db


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = initialize_state_db(self.path)

    def close(self) -> None:
        self.conn.close()

    # --- settings ---
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now_iso()),
        )
        self.conn.commit()

    # --- reading progress ---
    def save_progress(
        self, article_id: int, segment_id: int | None, position: int, percent: float
    ) -> None:
        self.conn.execute(
            "INSERT INTO reading_progress(article_id, segment_id, position, percent, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(article_id) DO UPDATE SET "
            "segment_id=excluded.segment_id, position=excluded.position, "
            "percent=excluded.percent, updated_at=excluded.updated_at",
            (article_id, segment_id, position, percent, now_iso()),
        )
        self.conn.commit()

    def get_progress(self, article_id: int):
        return self.conn.execute(
            "SELECT * FROM reading_progress WHERE article_id=?", (article_id,)
        ).fetchone()

    def recent_progress(self, limit: int = 10):
        return self.conn.execute(
            "SELECT * FROM reading_progress ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def delete_article_state(self, article_id: int) -> None:
        """文章从内容库删除后，清掉只属于它的进度和书签。"""
        self.conn.execute(
            "DELETE FROM reading_progress WHERE article_id=?", (int(article_id),)
        )
        self.conn.execute(
            "DELETE FROM bookmarks WHERE article_id=?", (int(article_id),)
        )
        self.conn.commit()

    # --- bookmarks ---
    def add_bookmark(self, article_id: int, segment_id: int | None, note: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO bookmarks(article_id, segment_id, note, created_at) VALUES (?,?,?,?)",
            (article_id, segment_id, note, now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_bookmarks(self, article_id: int | None = None):
        if article_id is None:
            return self.conn.execute(
                "SELECT * FROM bookmarks ORDER BY created_at DESC"
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM bookmarks WHERE article_id=? ORDER BY created_at DESC", (article_id,)
        ).fetchall()

    def delete_bookmark(self, bookmark_id: int) -> None:
        self.conn.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))
        self.conn.commit()

    # --- qa sessions ---
    def create_qa_session(self, title: str = "") -> int:
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO qa_sessions(title, created_at, updated_at) VALUES (?,?,?)",
            (title, ts, ts),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_qa_sessions(self, limit: int = 50):
        return self.conn.execute(
            "SELECT * FROM qa_sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def add_qa_message(
        self,
        session_id: int,
        role: str,
        content: str,
        seq_no: int,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> int:
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO qa_messages(session_id, seq_no, role, content, prompt_tokens, "
            "completion_tokens, total_tokens, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                session_id,
                seq_no,
                role,
                content,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                ts,
            ),
        )
        self.conn.execute("UPDATE qa_sessions SET updated_at=? WHERE id=?", (ts, session_id))
        self.conn.commit()
        return int(cur.lastrowid)

    def add_qa_citation(
        self,
        message_id: int,
        citation_label: str,
        article_id: int | None,
        segment_id: int | None,
        snippet: str | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO qa_citations(message_id, citation_label, article_id, segment_id, "
            "snippet, created_at) VALUES (?,?,?,?,?,?)",
            (message_id, citation_label, article_id, segment_id, snippet, now_iso()),
        )
        self.conn.commit()

    def list_qa_messages(self, session_id: int):
        return self.conn.execute(
            "SELECT * FROM qa_messages WHERE session_id=? ORDER BY seq_no", (session_id,)
        ).fetchall()

    def clear_qa(self) -> None:
        self.conn.execute("DELETE FROM qa_citations")
        self.conn.execute("DELETE FROM qa_messages")
        self.conn.execute("DELETE FROM qa_sessions")
        self.conn.commit()

    # --- AI token usage ---
    @staticmethod
    def _token_int(value) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def log_ai_usage(
        self,
        kind: str,
        model: str,
        usage: dict | None,
        session_id: int | None = None,
    ) -> int | None:
        """记录一次真实 API 调用的 Token 用量；本地检索不产生记录。"""
        if not isinstance(usage, dict):
            return None
        prompt = self._token_int(usage.get("prompt_tokens"))
        completion = self._token_int(usage.get("completion_tokens"))
        total = self._token_int(usage.get("total_tokens"))
        if total <= 0:
            total = prompt + completion
        if total <= 0:
            return None
        cur = self.conn.execute(
            "INSERT INTO ai_usage(kind, model, prompt_tokens, completion_tokens, "
            "total_tokens, session_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                (kind or "ask")[:20],
                (model or "")[:80],
                prompt,
                completion,
                total,
                session_id,
                now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _usage_bucket(self, since_iso: str | None = None) -> dict:
        sql = (
            "SELECT COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens FROM ai_usage"
        )
        params: tuple = ()
        if since_iso is not None:
            sql += " WHERE created_at >= ?"
            params = (since_iso,)
        row = self.conn.execute(sql, params).fetchone()
        return {
            "calls": int(row["calls"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
        }

    def ai_usage_stats(self, recent_limit: int = 20) -> dict:
        """返回今日/近 7 天/近 30 天/累计用量、按模型汇总和最近记录。"""
        today_start = now().replace(hour=0, minute=0, second=0, microsecond=0)

        def days_ago(days: int) -> str:
            return (today_start - timedelta(days=days - 1)).isoformat(timespec="seconds")

        by_model = [
            dict(row)
            for row in self.conn.execute(
                "SELECT model, COUNT(*) AS calls, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
                "COALESCE(SUM(total_tokens), 0) AS total_tokens "
                "FROM ai_usage GROUP BY model ORDER BY total_tokens DESC, model"
            )
        ]
        recent = []
        if recent_limit and recent_limit > 0:
            recent = [
                dict(row)
                for row in self.conn.execute(
                    "SELECT id, kind, model, prompt_tokens, completion_tokens, "
                    "total_tokens, created_at FROM ai_usage ORDER BY id DESC LIMIT ?",
                    (int(recent_limit),),
                )
            ]
        return {
            "today": self._usage_bucket(today_start.isoformat(timespec="seconds")),
            "last7": self._usage_bucket(days_ago(7)),
            "last30": self._usage_bucket(days_ago(30)),
            "all": self._usage_bucket(),
            "by_model": by_model,
            "recent": recent,
        }

    def clear_ai_usage(self) -> None:
        self.conn.execute("DELETE FROM ai_usage")
        self.conn.commit()
