# -*- coding: utf-8 -*-
"""SQLite 连接、迁移与健康检查。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..core.errors import StorageError
from ..core.timeutil import now_iso
from .migrations import CONTENT_MIGRATIONS, STATE_MIGRATIONS, TOPIC_MIGRATIONS

BUSY_TIMEOUT_MS = 5000


def connect(path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly and not path.exists():
        raise StorageError("数据库不存在：%s" % path)
    if not readonly:
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0)
    except sqlite3.Error as exc:
        raise StorageError("无法打开数据库 %s：%s" % (path, exc)) from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = %d" % BUSY_TIMEOUT_MS)
    if readonly:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def apply_migrations(conn: sqlite3.Connection, migrations) -> list[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, "
        "description TEXT NOT NULL, "
        "applied_at TEXT NOT NULL)"
    )
    applied = {int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")}
    newly = []
    for version, description, script in migrations:
        if version in applied:
            continue
        try:
            conn.executescript(script)
            conn.execute(
                "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?,?,?)",
                (version, description, now_iso()),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise StorageError("数据库迁移 v%s 失败：%s" % (version, exc)) from exc
        newly.append(version)
    return newly


def initialize_content_db(path: str | Path) -> sqlite3.Connection:
    conn = connect(path)
    try:
        apply_migrations(conn, CONTENT_MIGRATIONS)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise StorageError("内容库完整性检查失败：%s" % (row[0] if row else "unknown"))
        return conn
    except Exception:
        conn.close()
        raise


def initialize_state_db(path: str | Path) -> sqlite3.Connection:
    conn = connect(path)
    try:
        apply_migrations(conn, STATE_MIGRATIONS)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise StorageError("个人状态库完整性检查失败：%s" % (row[0] if row else "unknown"))
        return conn
    except Exception:
        conn.close()
        raise


def initialize_topic_db(path: str | Path) -> sqlite3.Connection:
    conn = connect(path)
    try:
        apply_migrations(conn, TOPIC_MIGRATIONS)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise StorageError("专题库完整性检查失败：%s" % (row[0] if row else "unknown"))
        return conn
    except Exception:
        conn.close()
        raise


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def integrity_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "unknown"


def health_report(conn: sqlite3.Connection) -> dict:
    report = {
        "schema_version": schema_version(conn),
        "integrity": integrity_check(conn),
    }
    try:
        report["collections"] = int(conn.execute("SELECT count(*) FROM collections").fetchone()[0])
        report["articles"] = int(conn.execute("SELECT count(*) FROM articles").fetchone()[0])
        report["ready"] = int(
            conn.execute("SELECT count(*) FROM articles WHERE status='ready'").fetchone()[0]
        )
        report["pending"] = int(
            conn.execute("SELECT count(*) FROM articles WHERE status='pending'").fetchone()[0]
        )
        report["failed"] = int(
            conn.execute("SELECT count(*) FROM articles WHERE status='failed'").fetchone()[0]
        )
        report["segments"] = int(conn.execute("SELECT count(*) FROM article_segments").fetchone()[0])
        report["last_collect"] = conn.execute(
            "SELECT finished_at FROM collect_runs WHERE finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        report["last_collect"] = (
            report["last_collect"][0] if report["last_collect"] else None
        )
    except sqlite3.Error as exc:
        report["error"] = str(exc)
    return report


def rebuild_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise StorageError("重建全文索引失败：%s" % exc) from exc
