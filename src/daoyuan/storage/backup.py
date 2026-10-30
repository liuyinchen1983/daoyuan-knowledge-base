# -*- coding: utf-8 -*-
"""知识库一致性备份、备份预览与恢复。

备份包是独立目录：内容库、个人状态库、专题库和 manifest.json；不包含
API Key、文章 HTML 快照、日志或导出文件。恢复前自动再备份当前库，
不覆盖备份原包。专题库是派生数据，但人工审核状态无法重建，因此一并备份。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .. import __version__
from ..core.config import Config
from ..core.errors import StorageError
from ..core.timeutil import now, now_iso
from .db import (
    connect,
    health_report,
    initialize_content_db,
    initialize_state_db,
    initialize_topic_db,
    integrity_check,
    rebuild_fts,
    schema_version,
)

BACKUP_FORMAT = "daoyuan-backup-v1"
MANIFEST = "manifest.json"
CONTENT_FILE = "knowledge.db"
STATE_FILE = "user_state.db"
TOPICS_FILE = "topics.db"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_sqlite(source_path: Path, target_path: Path) -> None:
    if not source_path.exists():
        raise StorageError("源数据库不存在：%s" % source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = connect(source_path, readonly=True)
    target = sqlite3.connect(str(target_path))
    try:
        source.backup(target)
        target.commit()
    except sqlite3.Error as exc:
        raise StorageError("SQLite 一致性备份失败：%s" % exc) from exc
    finally:
        target.close()
        source.close()
    check = connect(target_path, readonly=True)
    try:
        if integrity_check(check) != "ok":
            raise StorageError("备份文件完整性检查失败：%s" % target_path)
    finally:
        check.close()


def _content_summary(path: Path) -> dict:
    conn = connect(path, readonly=True)
    try:
        report = health_report(conn)
        return {
            "schema_version": schema_version(conn),
            "integrity": integrity_check(conn),
            "collections": report.get("collections"),
            "articles": report.get("articles"),
            "ready": report.get("ready"),
            "segments": report.get("segments"),
        }
    finally:
        conn.close()


def _state_summary(path: Path) -> dict:
    conn = connect(path, readonly=True)
    try:
        rows = {
            "reading_progress": conn.execute(
                "SELECT count(*) FROM reading_progress"
            ).fetchone()[0],
            "bookmarks": conn.execute("SELECT count(*) FROM bookmarks").fetchone()[0],
            "qa_sessions": conn.execute("SELECT count(*) FROM qa_sessions").fetchone()[0],
        }
        return {
            "schema_version": schema_version(conn),
            "integrity": integrity_check(conn),
            "counts": {key: int(value) for key, value in rows.items()},
        }
    finally:
        conn.close()


def _topics_summary(path: Path) -> dict:
    conn = connect(path, readonly=True)
    try:
        rows = {
            "entities": "SELECT count(*) FROM topic_entities",
            "pending": "SELECT count(*) FROM topic_entities WHERE status='pending'",
            "approved": "SELECT count(*) FROM topic_entities WHERE status='approved'",
            "rejected": "SELECT count(*) FROM topic_entities WHERE status='rejected'",
            "mentions": "SELECT count(*) FROM topic_mentions",
            "scanned_articles": "SELECT count(*) FROM topic_article_scan",
        }
        counts = {}
        for key, sql in rows.items():
            try:
                counts[key] = int(conn.execute(sql).fetchone()[0] or 0)
            except sqlite3.Error:
                counts[key] = 0
        return {
            "schema_version": schema_version(conn),
            "integrity": integrity_check(conn),
            "counts": counts,
        }
    finally:
        conn.close()


def _unique_backup_dir(config: Config) -> Path:
    base = "backup_" + now().strftime("%Y%m%d_%H%M%S")
    candidate = config.paths.backups / base
    suffix = 1
    while candidate.exists():
        candidate = config.paths.backups / ("%s_%d" % (base, suffix))
        suffix += 1
    return candidate


def create_backup(config: Config, note: str = "manual") -> dict:
    """用 SQLite backup API 生成一致性备份；返回预览信息。"""
    config.paths.ensure()
    if not config.paths.content_db.exists():
        raise StorageError("内容库尚未初始化，无法备份。")
    target = _unique_backup_dir(config)
    target.mkdir(parents=True, exist_ok=False)
    try:
        content_path = target / CONTENT_FILE
        _backup_sqlite(config.paths.content_db, content_path)
        state_info = None
        state_path = target / STATE_FILE
        if config.paths.state_db.exists():
            _backup_sqlite(config.paths.state_db, state_path)
            state_info = {
                "file": STATE_FILE,
                "bytes": state_path.stat().st_size,
                "sha256": _sha256(state_path),
                **_state_summary(state_path),
            }
        topics_info = None
        topics_path = target / TOPICS_FILE
        if config.paths.topics_db.exists():
            _backup_sqlite(config.paths.topics_db, topics_path)
            topics_info = {
                "file": TOPICS_FILE,
                "bytes": topics_path.stat().st_size,
                "sha256": _sha256(topics_path),
                **_topics_summary(topics_path),
            }
        manifest = {
            "format": BACKUP_FORMAT,
            "app_version": __version__,
            "created_at": now_iso(),
            "note": note,
            "content": {
                "file": CONTENT_FILE,
                "bytes": content_path.stat().st_size,
                "sha256": _sha256(content_path),
                **_content_summary(content_path),
            },
            "state": state_info,
            "topics": topics_info,
        }
        manifest_path = target / MANIFEST
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        # 不删除备份目录，保留现场供用户/开发者检查；原库不受影响。
        raise
    info = inspect_backup(target)
    if not info["valid"]:
        raise StorageError(
            "备份已生成但校验失败：%s" % "；".join(info["errors"] or ["未知错误"])
        )
    return info


def _resolve_package(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_file():
        if candidate.name in (MANIFEST, CONTENT_FILE, STATE_FILE, TOPICS_FILE):
            return candidate.parent
        return candidate.parent
    return candidate


def inspect_backup(path: str | Path) -> dict:
    package = _resolve_package(path)
    errors: list[str] = []
    manifest = None
    manifest_path = package / MANIFEST
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append("无法读取 manifest.json：%s" % exc)
    else:
        errors.append("缺少 manifest.json")
    content_path = package / CONTENT_FILE
    state_path = package / STATE_FILE
    topics_path = package / TOPICS_FILE
    content_info = None
    state_info = None
    topics_info = None
    if content_path.exists():
        try:
            content_info = {
                "file": CONTENT_FILE,
                "bytes": content_path.stat().st_size,
                "sha256": _sha256(content_path),
                **_content_summary(content_path),
            }
            if content_info["integrity"] != "ok":
                errors.append("内容库完整性检查不是 ok")
        except Exception as exc:
            errors.append("内容库不可读：%s" % exc)
    else:
        errors.append("缺少 knowledge.db")
    if state_path.exists():
        try:
            state_info = {
                "file": STATE_FILE,
                "bytes": state_path.stat().st_size,
                "sha256": _sha256(state_path),
                **_state_summary(state_path),
            }
            if state_info["integrity"] != "ok":
                errors.append("个人状态库完整性检查不是 ok")
        except Exception as exc:
            errors.append("个人状态库不可读：%s" % exc)
    if topics_path.exists():
        try:
            topics_info = {
                "file": TOPICS_FILE,
                "bytes": topics_path.stat().st_size,
                "sha256": _sha256(topics_path),
                **_topics_summary(topics_path),
            }
            if topics_info["integrity"] != "ok":
                errors.append("专题库完整性检查不是 ok")
        except Exception as exc:
            errors.append("专题库不可读：%s" % exc)
    elif manifest and manifest.get("topics"):
        errors.append("缺少 topics.db")
    if manifest:
        if manifest.get("format") != BACKUP_FORMAT:
            errors.append("备份格式不匹配")
        for key, actual in (
            ("content", content_info),
            ("state", state_info),
            ("topics", topics_info),
        ):
            declared = manifest.get(key)
            if not declared or not actual:
                continue
            if declared.get("sha256") and declared["sha256"] != actual.get("sha256"):
                errors.append("%s 文件哈希与 manifest 不一致" % key)
    created_at = (manifest or {}).get("created_at")
    return {
        "path": str(package),
        "name": package.name,
        "created_at": created_at,
        "note": (manifest or {}).get("note"),
        "app_version": (manifest or {}).get("app_version"),
        "content": content_info,
        "state": state_info,
        "topics": topics_info,
        "manifest": manifest,
        "valid": not errors,
        "errors": errors,
    }


def list_backups(config: Config) -> list[dict]:
    if not config.paths.backups.exists():
        return []
    result = []
    for entry in config.paths.backups.iterdir():
        if entry.is_dir() and (entry / CONTENT_FILE).exists():
            try:
                result.append(inspect_backup(entry))
            except Exception as exc:
                result.append(
                    {
                        "path": str(entry),
                        "name": entry.name,
                        "valid": False,
                        "errors": [str(exc)],
                    }
                )
    result.sort(
        key=lambda item: (item.get("created_at") or "", item.get("name") or ""),
        reverse=True,
    )
    return result


def _restore_one(source_path: Path, target_path: Path) -> None:
    if not source_path.exists():
        raise StorageError("备份中缺少数据库：%s" % source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = connect(source_path, readonly=True)
    target = connect(target_path)
    try:
        source.backup(target)
        target.commit()
        if integrity_check(target) != "ok":
            raise StorageError("恢复目标完整性检查失败：%s" % target_path)
    except sqlite3.Error as exc:
        raise StorageError("恢复数据库失败：%s" % exc) from exc
    finally:
        target.close()
        source.close()


def restore_backup(
    config: Config,
    backup_path: str | Path,
    restore_state: bool = False,
) -> dict:
    """恢复内容库；默认保留当前个人状态库。调用前必须关闭维护端数据库连接。"""
    info = inspect_backup(backup_path)
    if not info["valid"] or not info["content"]:
        raise StorageError(
            "备份校验未通过：%s" % "；".join(info["errors"] or ["未知错误"])
        )
    package = Path(info["path"])
    safety = None
    if config.paths.content_db.exists():
        try:
            safety = create_backup(config, note="restore_safety")["path"]
        except StorageError as exc:
            raise StorageError(
                "恢复前无法备份当前数据，已停止恢复：%s" % exc
            ) from exc
    try:
        _restore_one(package / CONTENT_FILE, config.paths.content_db)
        # 应用最新迁移并重建 FTS，确保恢复后检索可用。
        conn = initialize_content_db(config.paths.content_db)
        try:
            rebuild_fts(conn)
            if integrity_check(conn) != "ok":
                raise StorageError("恢复后内容库完整性检查失败。")
        finally:
            conn.close()
        topics_restored = False
        if (package / TOPICS_FILE).exists():
            _restore_one(package / TOPICS_FILE, config.paths.topics_db)
            topic_conn = initialize_topic_db(config.paths.topics_db)
            try:
                if integrity_check(topic_conn) != "ok":
                    raise StorageError("恢复后专题库完整性检查失败。")
            finally:
                topic_conn.close()
            topics_restored = True
        state_restored = False
        if restore_state and (package / STATE_FILE).exists():
            _restore_one(package / STATE_FILE, config.paths.state_db)
            store_conn = initialize_state_db(config.paths.state_db)
            store_conn.close()
            state_restored = True
    except Exception as exc:
        raise StorageError(
            "恢复失败：%s。恢复前备份：%s" % (exc, safety or "无")
        ) from exc
    return {
        "path": str(package),
        "safety_backup": safety,
        "state_restored": state_restored,
        "topics_restored": topics_restored,
        "content": inspect_backup(package)["content"],
        "topics": inspect_backup(package)["topics"],
    }
