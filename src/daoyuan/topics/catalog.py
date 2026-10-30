# -*- coding: utf-8 -*-
"""专题库的展示目录：类型、六个顶层维度、分组和配色。

这里只放“怎么展示”，不放抽取规则。抽取器仍然只自动生成人物、地点、
物品；组织、概念、剧情阶段、关键事件和伏笔由维护端人工整理或脚本补录。
"""
from __future__ import annotations

KIND_LABELS = {
    "person": "人物",
    "organization": "组织与势力",
    "place": "地点与场景",
    "concept": "概念与设定",
    "item": "物件与技术",
    "arc": "剧情阶段",
    "event": "关键事件",
    "mystery": "伏笔与谜题",
}
KINDS = tuple(KIND_LABELS)

# 阅读端树状图的一级、二级目录。一个维度可以包含多个 kind（剧情与伏笔）。
DIMENSIONS = (
    {
        "key": "person",
        "label": "人物",
        "glyph": "人",
        "kinds": ("person",),
        "hint": "主要人物、盟友与对手",
        "color": "#A65D4E",
    },
    {
        "key": "organization",
        "label": "组织与势力",
        "glyph": "势",
        "kinds": ("organization",),
        "hint": "机构、家族与组织",
        "color": "#7B6A4A",
    },
    {
        "key": "place",
        "label": "地点与场景",
        "glyph": "地",
        "kinds": ("place",),
        "hint": "城市、区域、设施与梦境",
        "color": "#5E7C6B",
    },
    {
        "key": "concept",
        "label": "概念与设定",
        "glyph": "念",
        "kinds": ("concept",),
        "hint": "概念、意识、修行与设定",
        "color": "#6A6E8C",
    },
    {
        "key": "item",
        "label": "物件与技术",
        "glyph": "物",
        "kinds": ("item",),
        "hint": "装置、样本、信物与食物",
        "color": "#8C6B3F",
    },
    {
        "key": "story",
        "label": "剧情与伏笔",
        "glyph": "卷",
        "kinds": ("arc", "event", "mystery"),
        "hint": "阶段、关键事件与未解之谜",
        "color": "#8C5A6B",
    },
)

DIMENSION_BY_KIND = {
    kind: dimension for dimension in DIMENSIONS for kind in dimension["kinds"]
}
DIMENSION_BY_KEY = {dimension["key"]: dimension for dimension in DIMENSIONS}

GROUPS = {
    "person": (
        ("core", "核心人物"),
        ("admin", "机构人物"),
        ("family", "家族人物"),
        ("old_town", "旧城人物"),
        ("prequel", "前传人物"),
        ("other", "其他人物"),
    ),
    "organization": (
        ("official", "官方机构"),
        ("capital", "家族与资本"),
        ("lab", "研究机构"),
        ("ai_program", "智体与程序"),
        ("other", "其他势力"),
    ),
    "place": (
        ("city", "城市与老街"),
        ("zones", "三区与险地"),
        ("facilities", "设施与舱室"),
        ("dream", "梦境与精神空间"),
        ("other", "其他地点"),
    ),
    "concept": (
        ("governance", "智体治理"),
        ("consciousness", "意识与感知"),
        ("dream", "梦境与修行"),
        ("carbon", "碳硅与变异"),
        ("identity", "身世与记忆"),
        ("other", "其他设定"),
    ),
    "item": (
        ("ai_device", "智体与装置"),
        ("bio_energy", "生物与能量样本"),
        ("token", "信物与文献"),
        ("life", "药物与食物"),
        ("other", "其他物件"),
    ),
    "story": (
        ("arc", "剧情阶段"),
        ("event", "关键事件"),
        ("mystery_origin", "身世与来历"),
        ("mystery_power", "势力与阴谋"),
        ("mystery_ai", "智体与觉醒"),
        ("other", "其他剧情"),
    ),
}
GROUP_LABELS = {
    key: label for entries in GROUPS.values() for key, label in entries
}
GROUP_ORDER = {
    dimension["key"]: tuple(key for key, _ in GROUPS.get(dimension["key"], ()))
    for dimension in DIMENSIONS
}

# 某个 kind 的默认分组：补录时没有显式分组也能落到“其他”。
KIND_DEFAULT_GROUP = {
    "person": "other",
    "organization": "other",
    "place": "other",
    "concept": "other",
    "item": "other",
    "arc": "arc",
    "event": "event",
    "mystery": "other",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind or "")


def kind_color(kind: str) -> str:
    dimension = DIMENSION_BY_KIND.get(kind)
    return dimension["color"] if dimension else "#73838A"


def group_label(group_key: str, kind: str = "") -> str:
    """按实体类型所在维度取分组名，避免 dream/other 等重名键串组。"""
    dimension = DIMENSION_BY_KIND.get(kind)
    if dimension and group_key:
        for key, label in GROUPS.get(dimension["key"], ()):
            if key == group_key:
                return label
    if group_key and group_key in GROUP_LABELS:
        return GROUP_LABELS[group_key]
    if group_key:
        return group_key
    if dimension:
        for key, label in GROUPS.get(dimension["key"], ()):
            if key == KIND_DEFAULT_GROUP.get(kind):
                return label
    return GROUP_LABELS.get(KIND_DEFAULT_GROUP.get(kind, ""), "其他")
