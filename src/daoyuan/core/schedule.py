# -*- coding: utf-8 -*-
"""采集计划：到期判断、设置持久化与 Windows 任务计划命令。

设置保存在个人状态库的 settings 表中，避免改写带注释的 config.toml。
任务注册必须由用户在维护端明确确认；本模块只生成/执行被调用的命令。
"""
from __future__ import annotations

import locale
import os
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

from .config import Config
from .errors import ConfigError, StorageError
from .timeutil import BEIJING, now, parse_hhmm
from ..storage.db import connect
from ..storage.state_db import StateStore

TASK_NAME = "道源知识库-自动采集"
SETTING_ENABLED = "schedule.enabled"
SETTING_INTERVAL = "schedule.interval_days"
SETTING_TIME = "schedule.daily_time"
SETTING_COLLECTION_IDS = "schedule.collection_ids"


def _parse_collection_ids(value: str | None) -> list[int]:
    """状态库中的文集范围是逗号分隔的 ID；空值表示全部启用文集。"""
    result: list[int] = []
    for part in str(value or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            number = int(part)
        except ValueError:
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def _read_state_settings(config: Config) -> dict:
    if not config.paths.state_db.exists():
        return {}
    store = StateStore(config.paths.state_db)
    try:
        return {
            "enabled": store.get_setting(SETTING_ENABLED),
            "interval_days": store.get_setting(SETTING_INTERVAL),
            "daily_time": store.get_setting(SETTING_TIME),
            "collection_ids": store.get_setting(SETTING_COLLECTION_IDS),
        }
    finally:
        store.close()


def effective_schedule(config: Config) -> dict:
    """状态库中的用户设置优先，缺省回落到 config.toml。"""
    values = _read_state_settings(config)
    enabled = config.schedule.enabled
    interval = config.schedule.interval_days
    daily_time = config.schedule.daily_time
    if values.get("enabled") in ("0", "1"):
        enabled = values["enabled"] == "1"
    try:
        if values.get("interval_days") is not None:
            interval = int(values["interval_days"])
    except (TypeError, ValueError):
        pass
    if values.get("daily_time"):
        try:
            parse_hhmm(values["daily_time"])
            daily_time = values["daily_time"]
        except ValueError:
            pass
    return {
        "enabled": bool(enabled),
        "interval_days": max(1, int(interval)),
        "daily_time": daily_time,
        "collection_ids": _parse_collection_ids(values.get("collection_ids")),
    }


def save_schedule(
    config: Config,
    enabled: bool,
    interval_days: int,
    daily_time: str,
    collection_ids: list[int] | None = None,
) -> None:
    """保存采集计划。

    ``collection_ids`` 为空或 None 表示全部启用文集；只对计划任务自动
    检查生效，手动采集不受限制。
    """
    interval_days = int(interval_days)
    if interval_days < 1 or interval_days > 365:
        raise ConfigError("采集周期应在 1–365 天之间。")
    parse_hhmm(daily_time)
    ids = []
    for value in collection_ids or []:
        number = int(value)
        if number > 0 and number not in ids:
            ids.append(number)
    config.paths.ensure()
    store = StateStore(config.paths.state_db)
    try:
        store.set_setting(SETTING_ENABLED, "1" if enabled else "0")
        store.set_setting(SETTING_INTERVAL, str(interval_days))
        store.set_setting(SETTING_TIME, daily_time)
        store.set_setting(SETTING_COLLECTION_IDS, ",".join(str(i) for i in ids))
    finally:
        store.close()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING)
    return parsed.astimezone(BEIJING)


def _last_run_at(config: Config, conn=None) -> datetime | None:
    own = False
    if conn is None:
        if not config.paths.content_db.exists():
            return None
        conn = connect(config.paths.content_db, readonly=True)
        own = True
    try:
        row = conn.execute(
            "SELECT finished_at FROM collect_runs "
            "WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _parse_iso(row[0] if row else None)
    except Exception:
        return None
    finally:
        if own:
            conn.close()


def compute_schedule_status(
    config: Config,
    conn=None,
    now_dt: datetime | None = None,
    schedule: dict | None = None,
) -> dict:
    schedule = schedule or effective_schedule(config)
    current = (now_dt or now()).astimezone(BEIJING)
    last = _last_run_at(config, conn)
    result = {
        "enabled": schedule["enabled"],
        "interval_days": int(schedule["interval_days"]),
        "daily_time": schedule["daily_time"],
        "collection_ids": list(schedule.get("collection_ids") or []),
        "last_run_at": last.isoformat(timespec="seconds") if last else None,
        "next_due_at": None,
        "due": False,
        "overdue_hours": 0.0,
    }
    if not schedule["enabled"]:
        return result
    if last is None:
        result["due"] = True
        result["next_due_at"] = current.isoformat(timespec="seconds")
        return result
    hour, minute = parse_hhmm(schedule["daily_time"])
    next_date = last.date() + timedelta(days=int(schedule["interval_days"]))
    next_due = datetime.combine(next_date, time(hour, minute), BEIJING)
    result["next_due_at"] = next_due.isoformat(timespec="seconds")
    result["due"] = current >= next_due
    if result["due"]:
        result["overdue_hours"] = round(
            (current - next_due).total_seconds() / 3600.0, 2
        )
    return result


def is_due(config: Config, conn=None, now_dt: datetime | None = None) -> bool:
    return bool(compute_schedule_status(config, conn=conn, now_dt=now_dt)["due"])


def task_action(collection_ids: list[int] | None = None) -> str:
    # 无人值守默认更保守：即使历史待采很多，每次计划任务最多抓 20 篇。
    suffix = " --update --if-due --max-fetch 20"
    for collection_id in collection_ids or []:
        suffix += " --collection-id %d" % int(collection_id)
    if getattr(sys, "frozen", False):
        return '"%s"%s' % (sys.executable, suffix)
    root = Path(__file__).resolve().parents[3]
    run_admin = root / "scripts" / "run_admin.py"
    return '"%s" "%s"%s' % (sys.executable, run_admin, suffix)


def build_task_command(config: Config, schedule: dict | None = None) -> list[str]:
    schedule = schedule or effective_schedule(config)
    return [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/SC",
        "DAILY",
        "/MO",
        str(int(schedule["interval_days"])),
        "/ST",
        schedule["daily_time"],
        "/TR",
        task_action(schedule.get("collection_ids")),
        "/F",
    ]


def display_command(command: list[str]) -> str:
    """生成可复制到 PowerShell 的预览命令（/TR 内层引号用单引号保护）。"""
    parts = [str(part) for part in command]
    rendered = []
    for index, part in enumerate(parts):
        if index == 0:
            rendered.append(part)
        elif index > 0 and parts[index - 1] == "/TR":
            rendered.append("'" + part.replace("'", "''") + "'")
        elif any(ch.isspace() for ch in part):
            rendered.append('"' + part.replace('"', '\\"') + '"')
        else:
            rendered.append(part)
    return " ".join(rendered)


def _run_schtasks(args: list[str], timeout: int = 30) -> dict:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    encoding = locale.getpreferredencoding(False) or "utf-8"
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "returncode": -1, "output": str(exc)}
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": output,
    }


def query_task() -> dict:
    return _run_schtasks(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"], timeout=15)


def install_task(config: Config, schedule: dict | None = None) -> dict:
    command = build_task_command(config, schedule)
    result = _run_schtasks(command)
    result["command"] = display_command(command)
    return result


def remove_task() -> dict:
    return _run_schtasks(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=15)


def describe_status(status: dict) -> str:
    if not status["enabled"]:
        return "自动采集已关闭。"
    if status["due"]:
        return "已到检查时间，可执行补采。"
    if status["next_due_at"]:
        return "下次检查：%s" % status["next_due_at"]
    return "等待首次检查。"
