# -*- coding: utf-8 -*-
"""采集冷却状态。

与 ``scripts/collect_all_pending.py`` 共用同一个文件和 JSON 格式，保证
单篇链接采集与持续采集遵守同一段冷却时间，不会因为多了一个入口就绕过
微信验证页保护。
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_STATE_DIR = Path.home() / "DaoYuanData"
COOLDOWN_NAME = "collect_all.cooldown"
INTERVAL_NAME = "collect_all.last_request"

# 单篇请求之间的间隔与持续采集一致：10–18 秒随机。
MIN_REQUEST_GAP = 10
MAX_REQUEST_GAP = 18


def default_paths(state_dir: Path | None = None) -> tuple[Path, Path]:
    """返回冷却与请求间隔文件的默认位置。

    调用方应优先传入配置中的数据目录，避免把运行状态写到源码目录。
    """
    base = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    return base / COOLDOWN_NAME, base / INTERVAL_NAME


def request_wait_seconds(path: Path | None = None) -> int:
    """距离允许下一次请求还差几秒；0 表示现在可以请求。"""
    path = Path(path) if path else default_paths()[1]
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        next_at = datetime.fromisoformat(str(payload["next_allowed_at"]))
    except (OSError, ValueError, TypeError, KeyError):
        return 0
    remain = (next_at - datetime.now()).total_seconds()
    return max(0, int(math.ceil(remain)))


def note_request_start(path: Path | None = None) -> None:
    """记录刚刚发出一次微信请求，并写入下一次最早允许的时间。"""
    path = Path(path) if path else default_paths()[1]
    now = datetime.now()
    payload = {
        "last_request_at": now.isoformat(timespec="seconds"),
        "next_allowed_at": (
            now + timedelta(seconds=random.randint(MIN_REQUEST_GAP, MAX_REQUEST_GAP))
        ).isoformat(timespec="seconds"),
    }
    destination = Path(path)
    temp = destination.with_name(destination.name + ".tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, destination)
    except OSError:
        # 间隔记录失败时宁可放慢：冷却逻辑本身仍然生效。
        logging.getLogger("daoyuan.link").exception("请求间隔记录写入失败。")


def load_cooldown(path: Path | None = None) -> tuple[datetime | None, int]:
    """读取冷却截止时间和连续受限次数；文件缺失或损坏时按无冷却处理。"""
    path = Path(path) if path else default_paths()[0]
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        resume_at = datetime.fromisoformat(str(payload["not_before"]))
        streak = max(0, int(payload.get("restricted_streak") or 0))
        return resume_at, streak
    except (OSError, ValueError, TypeError, KeyError):
        return None, 0


def write_cooldown(
    resume_at: datetime,
    restricted_streak: int,
    path: Path | None = None,
) -> None:
    """原子写入冷却状态，供关机/重启后的续跑进程继续遵守。"""
    destination = Path(path) if path else default_paths()[0]
    payload = {
        "not_before": resume_at.isoformat(timespec="seconds"),
        "restricted_streak": int(restricted_streak),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    temp = destination.with_name(destination.name + ".tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, destination)
    except OSError:
        logging.getLogger("daoyuan.supervisor").exception(
            "冷却状态写入失败；本次仍会等待，但关机后可能无法保留冷却时间。"
        )


def clear_cooldown(path: Path | None = None) -> None:
    path = Path(path) if path else default_paths()[0]
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def active_cooldown(path: Path | None = None) -> tuple[datetime, int] | None:
    """返回仍在生效的 (截止时间, 连续受限次数)，没有冷却时返回 None。"""
    resume_at, streak = load_cooldown(path)
    if resume_at is not None and resume_at > datetime.now():
        return resume_at, streak
    return None


def register_restriction(path: Path | None = None) -> tuple[datetime, int]:
    """遇到验证页/429/403 后登记下一次冷却，返回 (截止时间, 新次数)。

    退避与持续采集脚本一致：2 小时、4 小时、8 小时，最多 8 小时，
    另加 0–30 分钟随机抖动。
    """
    _, previous_streak = load_cooldown(path)
    streak = max(0, int(previous_streak)) + 1
    hours = min(8.0, 2.0 * (2 ** (streak - 1)))
    seconds = hours * 3600 + random.randint(0, 1800)
    resume_at = datetime.now() + timedelta(seconds=seconds)
    write_cooldown(resume_at, streak, path)
    return resume_at, streak
