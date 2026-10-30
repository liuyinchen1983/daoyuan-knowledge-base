# -*- coding: utf-8 -*-
"""按单篇文章链接采集。

用户在维护端粘贴一条公众号文章链接并点击一次，程序只请求这一篇：

- 链接必须属于本项目登记的公众号，不接受合辑页；
- 自动从 ``cur_album_id`` 识别文集，识别不到时才使用页面当前选择；
- 使用真实 ``mid``/``idx`` 入库，之后合辑清单再次扫到不会重复建文章；
- 冷却期间、自动采集运行期间拒绝发请求；
- 遇到验证页 / 429 / 403 立即停止，并按同一套 2h/4h/8h 策略写入冷却。
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..core.errors import (
    ArticleUnavailableError,
    CollectorError,
    ParseError,
    RequestBusyError,
    RiskControlError,
)
from ..core.timeutil import BEIJING
from .cooldown import (
    active_cooldown,
    clear_cooldown,
    default_paths,
    load_cooldown,
    register_restriction,
    request_wait_seconds,
)
from .http import HttpClient
from .known import BIZ
from .manual import import_fetched_article
from .parser import parse_article

logger = logging.getLogger("daoyuan.link")

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class LinkCollectError(Exception):
    """单篇链接采集的可预期错误。"""


class InvalidLinkError(LinkCollectError):
    """链接不是本项目支持的单篇微信文章链接。"""


class CooldownActiveError(LinkCollectError):
    """微信冷却期还没结束，不允许发请求。"""

    def __init__(self, resume_at: datetime, streak: int):
        self.resume_at = resume_at
        self.streak = streak
        super().__init__(
            "采集冷却中，预计 %s 恢复；为避免再次触发微信验证，现在不发起请求。"
            % resume_at.strftime("%Y-%m-%d %H:%M")
        )


class CollectorBusyError(LinkCollectError):
    """自动采集正在运行，不能同时抢请求。"""


class TooSoonError(LinkCollectError):
    """距上一次微信请求太近，连点会触发风控，必须先等够间隔。"""

    def __init__(self, wait_seconds: int):
        self.wait_seconds = wait_seconds
        super().__init__(
            "刚刚已经请求过微信，为避免触发验证，请等 %d 秒后再采集下一篇。"
            % wait_seconds
        )


class RiskCooldownError(LinkCollectError):
    """链接请求触发了微信验证页，已写入冷却。"""

    def __init__(self, detail: str, resume_at: datetime, streak: int):
        self.detail = detail
        self.resume_at = resume_at
        self.streak = streak
        super().__init__(
            "微信要求验证或限制了访问（%s）。已停止并冷却到 %s（连续第 %d 次）。"
            % (detail, resume_at.strftime("%Y-%m-%d %H:%M"), streak)
        )


def extract_url(raw: str) -> str:
    """从粘贴内容中提取第一条 http(s) 链接并清理常见尾随标点。"""
    text = (raw or "").strip().replace("&amp;", "&")
    match = _URL_RE.search(text)
    if not match:
        raise InvalidLinkError(
            "请粘贴完整的微信文章链接，例如 "
            "https://mp.weixin.qq.com/s?__biz=...&mid=...&idx=..."
        )
    return match.group(0).rstrip("，。；;、）)】]}>")


def parse_article_link(url: str) -> dict:
    """解析并校验单篇微信文章链接。

    返回 ``biz`` / ``mid`` / ``itemidx`` / ``album_id``；短链接可能没有
    mid 和 idx，抓取后会用最终地址补全。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidLinkError("链接必须以 http:// 或 https:// 开头。")
    if (parsed.hostname or "").lower() != "mp.weixin.qq.com":
        raise InvalidLinkError("目前只支持 mp.weixin.qq.com 的公众号文章链接。")
    if parsed.path.startswith("/mp/appmsgalbum"):
        raise InvalidLinkError("这是合辑链接，不是单篇文章链接；请打开具体文章后复制地址。")
    if parsed.path not in ("/s", "/s/") and not parsed.path.startswith("/s/"):
        raise InvalidLinkError("这不是微信单篇文章链接，请复制以 /s 开头的文章地址。")

    query = parse_qs(parsed.query, keep_blank_values=True)

    def first(*keys: str) -> str:
        for key in keys:
            value = query.get(key)
            if value:
                return str(value[0]).strip()
        return ""

    biz = first("__biz")
    if biz and biz != BIZ:
        raise InvalidLinkError("这条链接不属于当前知识库登记的公众号。")

    return {
        "url": url,
        "biz": biz,
        "mid": first("mid"),
        "itemidx": first("idx"),
        "album_id": first("cur_album_id", "album_id"),
    }


def _safe_link_info(url: str) -> dict:
    try:
        return parse_article_link(url)
    except InvalidLinkError:
        return {}


def _resolve_collection(
    conn, album_id: str, fallback_id: int | None
) -> tuple[int, bool]:
    """优先按链接里的合辑 ID 找文集，找不到再用页面当前选择。"""
    if album_id:
        row = conn.execute(
            "SELECT id FROM collections WHERE album_id=? ORDER BY id LIMIT 1",
            (str(album_id),),
        ).fetchone()
        if row is not None:
            return int(row["id"]), True
    if fallback_id is not None:
        row = conn.execute(
            "SELECT id FROM collections WHERE id=?",
            (int(fallback_id),),
        ).fetchone()
        if row is not None:
            return int(row["id"]), False
    raise InvalidLinkError(
        "链接里没有可识别的合辑，请在页面选择文章所属文集后再采集。"
    )


def collect_article_by_url(
    conn,
    config,
    raw_url: str,
    *,
    collection_id: int | None = None,
    http_client=None,
    cooldown_path: Path | None = None,
    interval_path: Path | None = None,
) -> dict:
    """抓取并入库一条文章链接，返回入库结果。

    ``http_client`` 和冷却文件路径可注入，便于离线测试；正式调用使用
    配置中的数据目录，不把运行状态写进源码目录。
    """
    default_cooldown, default_interval = default_paths(config.paths.root)
    cooldown_path = cooldown_path or default_cooldown
    interval_path = interval_path or default_interval

    url = extract_url(raw_url)
    info = parse_article_link(url)

    active = active_cooldown(cooldown_path)
    if active is not None:
        raise CooldownActiveError(*active)

    # 手动连点也不会连续发请求；这里立即提示等待，不阻塞 GUI 线程。
    # 真正发出请求后由 HttpClient 写入下一次允许时间，与自动采集共用。
    wait_seconds = request_wait_seconds(interval_path)
    if wait_seconds > 0:
        raise TooSoonError(wait_seconds)

    client = http_client or HttpClient(
        config.collector, pacing=False, state_dir=config.paths.root
    )
    try:
        final_url, page_html = client.get_text(url)
        parsed = parse_article(page_html, source_url=final_url)
    except RequestBusyError as exc:
        raise CollectorBusyError(
            "自动采集正在请求微信，暂时无法同时发链接请求；请几秒后再试。"
        ) from exc
    except RiskControlError as exc:
        resume_at, streak = register_restriction(cooldown_path)
        logger.error(
            "链接采集触发访问限制，冷却到 %s（连续第 %d 次）：%s",
            resume_at.strftime("%Y-%m-%d %H:%M"),
            streak,
            exc,
        )
        raise RiskCooldownError(str(exc), resume_at, streak) from exc
    except (ArticleUnavailableError, ParseError, CollectorError):
        raise

    if parsed.publish_ts and (
        not parsed.publish_time_raw or str(parsed.publish_time_raw).isdigit()
    ):
        # 微信页面里的 ct 是秒级时间戳；直接存进“发布日期”会很难读。
        # 页面没有 id="publish_time" 文本时，用北京时间格式化一份。
        parsed = replace(
            parsed,
            publish_time_raw=datetime.fromtimestamp(
                int(parsed.publish_ts), BEIJING
            ).strftime("%Y-%m-%d %H:%M"),
        )

    final_info = _safe_link_info(final_url)
    if final_info.get("biz") and final_info["biz"] != BIZ:
        raise InvalidLinkError("文章跳转到了其他公众号，已停止入库。")

    album_id = final_info.get("album_id") or info.get("album_id") or ""
    mid = final_info.get("mid") or info.get("mid") or ""
    itemidx = final_info.get("itemidx") or info.get("itemidx") or ""
    target_collection_id, album_matched = _resolve_collection(
        conn, album_id, collection_id
    )

    result = import_fetched_article(
        conn,
        target_collection_id,
        parsed,
        msgid=mid,
        itemidx=itemidx,
    )
    result["request_url"] = url
    result["final_url"] = final_url
    result["album_id"] = album_id
    result["album_matched"] = album_matched

    # 如果文件里只是一段已经过期的旧冷却，本次成功请求说明微信已恢复，
    # 清掉它，避免持续采集进程重新开始后还读到旧状态。
    if load_cooldown(cooldown_path)[0] is not None:
        clear_cooldown(cooldown_path)

    logger.info(
        "链接采集：文章=%s，状态=%s，文集=%s，章节=%s，字符=%s，msgid=%s",
        result.get("article_id"),
        result.get("status"),
        result.get("collection_title"),
        result.get("chapter") or "-",
        result.get("chars"),
        mid or "-",
    )
    return result
