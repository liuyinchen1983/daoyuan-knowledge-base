# -*- coding: utf-8 -*-
"""合集列表接口：方向判定、翻页、清单持久化前置。"""
from __future__ import annotations

import html
import json
import random
import time
import urllib.parse

from ..core.errors import CollectorError, RiskControlError
from ..core.models import SORT_NEWEST_FIRST, SORT_UNKNOWN, ListingItem
from .http import HttpClient

ALBUM_ENDPOINT = "https://mp.weixin.qq.com/mp/appmsgalbum"
RISK_MARKERS = ("secitptpage/verify", "wappoc_appmsgcaptcha", "访问过于频繁")


def build_album_url(
    biz: str,
    album_id: str,
    count: int = 20,
    is_reverse: int = 0,
    begin_msgid: str | None = None,
    begin_itemidx: str | None = None,
) -> str:
    params = {
        "__biz": biz,
        "action": "getalbum",
        "album_id": str(album_id),
        "count": str(count),
        "is_reverse": str(is_reverse),
        "f": "json",
    }
    if begin_msgid is not None:
        params["begin_msgid"] = str(begin_msgid)
        params["begin_itemidx"] = str(begin_itemidx)
    return ALBUM_ENDPOINT + "?" + urllib.parse.urlencode(params, safe="=")


def normalize_article_list(value) -> list[dict]:
    """count=1 时接口把单篇返回为 dict。"""
    if isinstance(value, dict):
        return [value] if value.get("msgid") else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _to_listing(item: dict) -> ListingItem:
    try:
        create_time = int(item.get("create_time") or 0) or None
    except (TypeError, ValueError):
        create_time = None
    return ListingItem(
        msgid=str(item.get("msgid") or ""),
        itemidx=str(item.get("itemidx") or ""),
        title=html.unescape(str(item.get("title") or "")).strip(),
        url=html.unescape(str(item.get("url") or "")),
        create_time=create_time,
        is_deleted=bool(item.get("is_deleted")),
    )


class AlbumClient:
    def __init__(
        self,
        http: HttpClient,
        sleeper=time.sleep,
        rand=random.random,
        list_delay: float = 2.0,
        logger=None,
    ):
        self.http = http
        self.sleeper = sleeper
        self.rand = rand
        self.list_delay = list_delay
        self.logger = logger

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger.info(message)

    def fetch_page(
        self,
        biz: str,
        album_id: str,
        count: int = 20,
        is_reverse: int = 0,
        begin_msgid: str | None = None,
        begin_itemidx: str | None = None,
    ) -> dict:
        url = build_album_url(biz, album_id, count, is_reverse, begin_msgid, begin_itemidx)
        _, text = self.http.get_text(url)
        for marker in RISK_MARKERS:
            if marker in text:
                raise RiskControlError("album_api_verify:%s" % marker)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CollectorError("合集接口返回非 JSON（长度 %d）" % len(text)) from exc
        base_resp = data.get("base_resp") or {}
        if base_resp.get("ret") not in (0, None):
            raise CollectorError("合集接口错误：%s" % base_resp)
        return data

    def detect_direction(self, biz: str, album_id: str) -> tuple[int, dict]:
        probes = {}
        for reverse in (0, 1):
            data = self.fetch_page(biz, album_id, count=1, is_reverse=reverse)
            resp = data.get("getalbum_resp", {})
            info = resp.get("base_info", {})
            items = normalize_article_list(resp.get("article_list"))
            ct = int(items[0].get("create_time") or 0) if items else 0
            probes[reverse] = {"ct": ct, "info": info, "title": items[0].get("title") if items else ""}
            self._log(
                "方向探测 is_reverse=%d -> 首篇 ct=%s %s"
                % (reverse, ct, probes[reverse]["title"] or "")
            )
            if reverse == 0:
                self.sleeper(self.list_delay * (0.8 + self.rand() * 0.4))
        if probes[1]["ct"] > probes[0]["ct"]:
            return 1, probes[1]["info"]
        if probes[0]["ct"] > 0:
            return 0, probes[0]["info"]
        return 0, probes[0]["info"]

    def fetch_all(
        self,
        biz: str,
        album_id: str,
        reverse_flag: int,
        progress_cb=None,
    ) -> tuple[list[ListingItem], dict]:
        items: list[ListingItem] = []
        seen: set[tuple[str, str]] = set()
        begin_msgid = None
        begin_itemidx = None
        page = 0
        base_info: dict = {}
        while True:
            page += 1
            data = self.fetch_page(
                biz,
                album_id,
                count=20,
                is_reverse=reverse_flag,
                begin_msgid=begin_msgid,
                begin_itemidx=begin_itemidx,
            )
            resp = data.get("getalbum_resp", {})
            if not base_info:
                base_info = resp.get("base_info", {}) or {}
            batch = normalize_article_list(resp.get("article_list"))
            added = 0
            for raw in batch:
                item = _to_listing(raw)
                key = (item.msgid, item.itemidx)
                if not item.msgid or key in seen:
                    continue
                seen.add(key)
                items.append(item)
                added += 1
            cont = str(resp.get("continue_flag", "0"))
            self._log("列表第 %d 页：%d 条，新增 %d，continue_flag=%s" % (page, len(batch), added, cont))
            if progress_cb:
                progress_cb(
                    "list_page", {"page": page, "items": len(items), "continue": cont}
                )
            if not batch or cont != "1":
                break
            last = batch[-1]
            begin_msgid = last.get("msgid")
            begin_itemidx = last.get("itemidx")
            self.sleeper(self.list_delay * (0.8 + self.rand() * 0.4))
        return items, base_info

    def fetch_recent(
        self,
        biz: str,
        album_id: str,
        reverse_flag: int,
        known_keys: set[tuple[str, str]],
        max_pages: int = 5,
        progress_cb=None,
    ) -> tuple[list[ListingItem], dict, bool]:
        """增量：从最新页向前翻，直到整页都是已知文章或达到页数上限。

        返回 (items, base_info, reached_stable_boundary)。
        """
        items: list[ListingItem] = []
        seen: set[tuple[str, str]] = set()
        begin_msgid = None
        begin_itemidx = None
        base_info: dict = {}
        stable = False
        for page in range(1, max_pages + 1):
            data = self.fetch_page(
                biz,
                album_id,
                count=20,
                is_reverse=reverse_flag,
                begin_msgid=begin_msgid,
                begin_itemidx=begin_itemidx,
            )
            resp = data.get("getalbum_resp", {})
            if not base_info:
                base_info = resp.get("base_info", {}) or {}
            batch = normalize_article_list(resp.get("article_list"))
            page_new = 0
            for raw in batch:
                item = _to_listing(raw)
                key = (item.msgid, item.itemidx)
                if not item.msgid or key in seen:
                    continue
                seen.add(key)
                items.append(item)
                if key not in known_keys:
                    page_new += 1
            cont = str(resp.get("continue_flag", "0"))
            self._log(
                "增量列表第 %d 页：%d 条，其中新文章 %d，continue_flag=%s"
                % (page, len(batch), page_new, cont)
            )
            if progress_cb:
                progress_cb(
                    "list_page", {"page": page, "items": len(items), "continue": cont}
                )
            if not batch or cont != "1":
                stable = True
                break
            if known_keys and page_new == 0:
                stable = True
                break
            last = batch[-1]
            begin_msgid = last.get("msgid")
            begin_itemidx = last.get("itemidx")
            self.sleeper(self.list_delay * (0.8 + self.rand() * 0.4))
        return items, base_info, stable
