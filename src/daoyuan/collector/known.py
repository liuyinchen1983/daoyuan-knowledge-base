# -*- coding: utf-8 -*-
"""公众号来源配置（使用前必须替换成你自己的信息）。

本文件不包含真实账号、合集 ID 或文章内容。首次运行前请把 BIZ、
ACCOUNT_NAME、ACCOUNT_ID 改成你在微信公众平台看到的实际值，并按需
在 KNOWN_COLLECTIONS 中登记文集；也可以先留空，改在维护端手动登记。

合集 ID 是文章链接里 ``cur_album_id`` 或合辑页 ``album_id`` 的值。
``reverse_flag`` 记录“最新在前”时使用的 ``is_reverse`` 参数，未确认时
填 None，程序会在首次采集时探测。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.models import SORT_NEWEST_FIRST, SORT_UNKNOWN

# TODO: 替换为你的公众号 __biz（文章链接里的 __biz 参数）。
BIZ = "Yml6X3BsYWNlaG9sZGVy"
ACCOUNT_NAME = "示例公众号"
ACCOUNT_ID = "gh_000000000000"


@dataclass(frozen=True)
class KnownCollection:
    name: str
    album_id: str
    expected_count: int | None
    reverse_flag: int | None = None  # 最新在前时使用的 is_reverse

    @property
    def sort_mode(self) -> str:
        if self.reverse_flag is None:
            return SORT_UNKNOWN
        return "%s:rev=%d" % (SORT_NEWEST_FIRST, self.reverse_flag)


# 示例（请替换为真实值后再启用；空列表表示不使用预置文集）：
# KNOWN_COLLECTIONS = [
#     KnownCollection("我的文集", "0000000000000000001", None, None),
# ]
KNOWN_COLLECTIONS: list[KnownCollection] = []
