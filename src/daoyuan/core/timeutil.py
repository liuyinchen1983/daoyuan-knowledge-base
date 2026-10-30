# -*- coding: utf-8 -*-
"""统一时间处理：存储 ISO 字符串，展示按北京时间。"""
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def now() -> datetime:
    return datetime.now(BEIJING)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def epoch_to_datetime(ts) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), BEIJING)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def format_epoch(ts, fmt: str = "%Y-%m-%d %H:%M") -> str:
    dt = epoch_to_datetime(ts)
    return dt.strftime(fmt) if dt else ""


def format_epoch_date(ts) -> str:
    return format_epoch(ts, "%Y-%m-%d")


def date_to_epoch(value: str, end_of_day: bool = False) -> int | None:
    """把 YYYY-MM-DD 转为北京时间当天 00:00（end_of_day 时为次日 00:00）。"""
    try:
        dt = datetime.strptime((value or "").strip(), "%Y-%m-%d").replace(
            tzinfo=BEIJING
        )
    except (ValueError, AttributeError):
        return None
    if end_of_day:
        dt += timedelta(days=1)
    return int(dt.timestamp())


def parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_s, minute_s = value.strip().split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
    except (ValueError, AttributeError) as exc:
        raise ValueError("时间格式应为 HH:MM，例如 09:00") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间超出范围：%s" % value)
    return hour, minute
