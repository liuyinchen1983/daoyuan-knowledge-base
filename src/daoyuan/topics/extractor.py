# -*- coding: utf-8 -*-
"""零 Token 的本地规则抽取器。

目标不是替代人工，而是把“值得看一眼”的人物、地点、物品候选找出来，
并保留原文句子作为证据。所有结果在阅读端可见前都必须经过人工审核。

规则分三层：
1. 已知实体/别名：保证持续更新时，已收录的人物不会因为新文章而丢失。
2. 通用词法：中文姓名、称呼、地点后缀、物品后缀。
3. 标题提示：标题里反复出现、正文也能找到的词，作为额外候选。

抽取器刻意保守：宁可少提，也不把普通动词、代词、数量词当成实体。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .catalog import KIND_LABELS, KINDS

# 常见姓氏。只用于缩小候选范围，不要求姓名一定从这里开头。
SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊於惠甄麴家封芮羿储靳汲邴糜松井段富巫乌焦巴弓"
    "牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙"
    "叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻"
    "莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温"
    "别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡"
    "国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾"
    "毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)

# 故事人物姓名集中在常见姓氏里。用“常见姓氏”而不是全部姓氏做通用姓名
# 识别，可以显著减少「连忙追」「冷静反」「从总」这类动词/副词碎片。
COMMON_SURNAMES = set(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖"
    "田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付"
    "方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向"
    "汤阮裴傅温楚萧"
)

SPEECH_VERBS = (
    "点了点头",
    "摇了摇头",
    "开口说道",
    "接着说道",
    "继续说道",
    "低声说道",
    "轻声说道",
    "冷冷说道",
    "笑着问道",
    "反问道",
    "追问道",
    "回答道",
    "解释道",
    "嘟囔道",
    "嘀咕道",
    "吩咐道",
    "提醒道",
    "安慰道",
    "道歉道",
    "感谢道",
    "答应道",
    "拒绝道",
    "问道",
    "说道",
    "笑道",
    "吼道",
    "嚷道",
    "喝道",
    "应道",
    "叹道",
    "叫道",
    "喊道",
    "开口",
    "接话",
    "说",
    "道",
    "问",
    "答",
    "喊",
    "叫",
    "笑",
    "叹",
)

ROLE_SUFFIXES = (
    "副组长",
    "副队长",
    "副部长",
    "董事长",
    "总裁",
    "经理",
    "处长",
    "科长",
    "部长",
    "组长",
    "队长",
    "局长",
    "主任",
    "医生",
    "博士",
    "教授",
    "先生",
    "女士",
    "小姐",
    "夫人",
    "老板",
    "师傅",
    "师父",
    "管家",
    "公子",
    "哥",
    "姐",
    "爷",
    "叔",
    "姨",
    "婶",
    "伯",
    "妈",
    "嫂",
    "舅",
    "姑",
    "婆",
    "总",
)
ROLE_REQUIRES_SURNAME = {
    "副组长",
    "副队长",
    "副部长",
    "董事长",
    "总裁",
    "经理",
    "处长",
    "科长",
    "部长",
    "组长",
    "队长",
    "局长",
    "主任",
    "医生",
    "博士",
    "教授",
    "先生",
    "女士",
    "小姐",
    "夫人",
    "老板",
    "师傅",
    "师父",
    "管家",
    "公子",
}

LOCATION_SUFFIXES = (
    "开发区",
    "高新区",
    "工业区",
    "实验室",
    "基地",
    "公司",
    "集团",
    "医院",
    "学校",
    "学院",
    "大楼",
    "广场",
    "公园",
    "小区",
    "老街",
    "码头",
    "车站",
    "机场",
    "街",
    "路",
    "城",
    "镇",
    "村",
    "山",
    "河",
    "湖",
    "江",
    "海",
    "岛",
    "谷",
    "林",
    "园",
    "楼",
    "堂",
    "馆",
    "店",
    "厂",
    "房",
    "屋",
    "洞",
    "潭",
    "池",
    "井",
    "桥",
    "站",
    "港",
    "舱",
    "区",
    "市",
    "县",
    "省",
    "国",
    "溪",
    "流",
    "峰",
    "岭",
    "坡",
    "原",
    "洲",
    "湾",
)

LOCATION_MULTI_SUFFIXES = {
    "开发区",
    "高新区",
    "工业区",
    "实验室",
    "基地",
    "公司",
    "集团",
    "医院",
    "学校",
    "学院",
    "大楼",
    "广场",
    "公园",
    "小区",
    "老街",
    "码头",
    "车站",
    "机场",
}

ITEM_SEEDS = (
    "碳硅",
    "机甲",
    "机器人",
    "芯片",
    "病毒",
    "孢子",
    "血清",
    "疫苗",
    "样本",
    "晶体",
    "能源",
    "系统",
    "装置",
    "设备",
    "X7 Future",
    "捋面",
    "休眠舱",
    "苏醒舱",
    "脑机接口",
    "卷宗",
)

ITEM_SUFFIXES = (
    "脑机接口",
    "机器人",
    "反应堆",
    "休眠舱",
    "苏醒舱",
    "机甲",
    "芯片",
    "装置",
    "设备",
    "仪器",
    "终端",
    "系统",
    "样本",
    "晶体",
    "病毒",
    "孢子",
    "血清",
    "疫苗",
    "药剂",
    "丹药",
    "钥匙",
    "令牌",
    "屏幕",
    "手机",
    "电脑",
    "能源",
    "电池",
    "武器",
    "飞船",
    "战车",
    "装甲",
    "卷宗",
    "档案",
)

# 这些词即使符合姓名/地点形状，也几乎不可能是实体。
STOPWORDS = set(
    """
    时候 自己 什么 现在 已经 知道 一个 这个 那个 我们 他们 你们 因为 所以
    但是 如果 可是 然后 还是 就是 不是 没有 可以 可能 应该 觉得 看着 说道
    问道 回答 起来 出来 过来 进去 出去 回来 回去 下来 上去 上来 一下 一直
    一定 一起 一样 一般 一些 这些 那些 哪些 哪里 这里 那里 心里 手里 嘴里
    眼里 脸上 身上 头上 身后 面前 旁边 中间 外面 里面 上面 下面 前面 后面
    两人 三人 四人 众人 大家 有人 这人 那人 一人 二人 父女 母女 父子 母子
    兄弟 姐妹 管家 组长 队长 局长 主任 医生 博士 教授 先生 女士 小姐 夫人
    老板 师傅 师父 大哥 小哥 老哥 大姐 大爷 大叔 阿姨 大伯 老伯 阿姐 阿哥
    马上 高大 高兴 高手 马路 江湖 江水 沈默 沉默 沉思 苏醒 醒来 沉睡 睡去
    声音 语气 目光 眼神 脸色 神情 表情 事情 东西 地方 时间 时候 空间 世界
    物品 问题 答案 结果 原因 经过 开始 结束 继续 停止 离开 回来 进去 出去
    说话 聊天 告诉 询问 回答
    包括 全程 全部 能量 通讯 强行 连忙 从未 尤其 明明 周围 封闭 平稳 郑重
    从容 全员 周遭 平静 怀疑 步步 温柔 空气 轻声 周身 终于 能够 管理局
    水熊 少爷 江氏 指纹 纹路 链路 脑海 片区 一路 此地 余地 原地 目的地
    各地 当地 外地 内地 场地 大地 天地 遍地 满地 实地 所在 公司 集团
    医院 学校 学院 基地 实验室
    交流 水流 电流 气流 工作流 线路 思路 漏洞 城市 街区 交流 水流
    连忙 轻声 冷静 开口 刚刚 别再 别笑 从今 曾经 甚至 相当于 能不能
    能理解 相信 申请 满心 管理 养护 安排 容易 封存 习惯 干扰 权限 比如
    水蛭 连夜 连连 连通 项目 严格 乌黑 危险 双双 古树 常态 常规 干净
    康复 强劲 强横 强烈 强迫 成为 成群 明白 查清 从总 从阮 元素 全数
    全权 关键 凌空 万万 万无一失 一字一顿 富甲一方 从头到尾 全方 全域
    全世界 全程监 全部由 冷静反 刚刚要 别再追 别笑别 开口发 轻声询
    连忙追 林素机甲 林素猛 林素眼底 林素获救 林素一直 林素与 林素心
    姨妈 大妈 小姑 帅哥 子妈 爸妈 叔叔 婶婶 伯伯 婆婆 舅舅 姑姑 嫂子
    哥哥 姐姐 弟弟 妹妹 爷爷 奶奶 暗流 阮顾 但江 可江 将江
    天爷 老爷 妈妈 亲哥 白哥 住姨 将姨
    级风险区 级危险区 级禁区 风险区 与精神觉知场馆 差差异推演场馆
    人类最原 苏晚晴原 祂原 魏先生侧身引路 推开房 复原 沿路 赶路 跋山
    满堂 瞬间海 隐秘交流 能量流 精神能量流 三条路 该区 第一区 特殊区
    探查区 区域原 座林 山川河流 水底暗流 钻研饭菜这条路 擅自闯入作战区
    阮小 阮大 阮大小 阮大顾 沈小 老先生 温润 但姨 呀哥 林素继续 素继续
    刚踏入办公区 部分区 栋大楼 全屋 套流 车流 乱流 人体感知复原
    低声交流 动作流 周遭流 彼此交流 思绪流 核心原 缓缓流 返程路 顺路
    山河 盲区 梦境之桥 高危区 分区 大楼 休息舱 医疗舱 座舱 病房 禁区
    危险区 安全区
    """.split()
)

BAD_NAME_END = set(
    "的了着过是在有和跟对向把被让给就都也又还却才只很太更最不没无个位名"
    "岁年月日时候里外上下中内前后左右边面间等们这那哪什么谁我你他她它咱您"
    "说问道答喊叫笑叹看想听觉得来去出进回到走坐站开关做用拿送买卖吃喝睡"
    "醒死活打杀找等住知留探获炸带低轻冷淡沉缓急怒惊喜悲痛累饿渴困所将又"
    "遗属氏地得然一机眼声身步员部量讯括忙未其围闭稳遭疑柔够"
)
BAD_NAME_START = set(
    "的了着过是在有和跟对向把被让给就都也又还却才只很太更最不没无个位名"
    "岁年月日时候里外上下中内前后左右边面间等们这那哪什么谁我你他她它咱您"
    "叫请带找见任到说答谢信去咐望"
)
BAD_ROLE_PREFIX = set(
    "的了着过是在有和跟对向把被让给就都也又还却才只很太更最不没无个位名"
    "岁年月日时候里外上下中内前后左右边面间等们这那哪什么谁我你他她它咱您家"
    "住将亲白但可而呀"
)
BAD_LOCATION_START = set(
    "的了着过是在有和跟对向把被让给就都也又还却才只很太更最不没无个位名"
    "岁年月日时候里外上下中内前后左右边面间等们这那哪什么谁我你他她它咱您"
    "条片些号回入见刻救请为以从自起身远重持数普离家属参观逃坠投但可"
)
# 「但江寒川」「可江寒川」「将江寒川」这类句首连词 + 姓氏，会被地点后缀
# 「江」误识别成地名。只在单字连词 + 姓氏后缀时拦截，不影响「长江」等
# 正常的江名。
LOCATION_CONNECTOR_PREFIXES = set("但可将会却仍竟便又也还就都才只而于比且并")

# 「其实江寒川」「包括江寒川」「反观江寒川」是连词短语 + 姓氏，不是地名。
# 多字连词无法用单字表拦截，这里按“江”字前的完整连词判断。
LOCATION_CONNECTOR_WORDS = (
    "其实",
    "包括",
    "反观",
    "唯独",
    "肯定",
    "而且",
    "然而",
    "虽然",
    "因为",
    "所以",
    "如果",
    "但是",
    "可是",
    "于是",
    "对于",
    "关于",
    "由于",
    "终于",
    "甚至",
    "尤其",
    "并且",
    "以及",
    "同时",
    "似乎",
    "仿佛",
    "原本",
    "本来",
    "已经",
    "曾经",
    "只要",
    "只有",
    "无论",
    "不管",
    "除了",
    "随着",
    "按照",
    "根据",
    "经过",
    "通过",
    "针对",
    "面对",
    "至于",
)

LOCATION_TRIM_PREFIXES = (
    "复刻的",
    "他们",
    "她们",
    "我们",
    "你们",
    "大家",
    "众人",
    "有人",
    "两人",
    "三人",
    "们",
    "来到了",
    "来到",
    "走进了",
    "走进",
    "前往了",
    "前往",
    "返回了",
    "返回",
    "离开了",
    "离开",
    "进入了",
    "进入",
    "走出了",
    "走出",
    "穿过了",
    "穿过",
    "赶到了",
    "赶到",
    "回到了",
    "回到",
    "打开了",
    "打开",
    "公开了",
    "公开",
    "靠近",
    "远离",
    "驶入",
    "飞入",
    "落入",
    "坠入",
    "跳入",
    "逃入",
    "刚踏入",
    "刚走进",
    "刚进入",
    "刚来到",
    "刚回到",
    "刚走出",
    "踏入",
    "入住",
    "直奔",
    "深入",
    "潜入",
    "住在",
    "位于",
    "乌黑",
    "漆黑",
    "直朝",
    "朝着",
    "直接",
    "慢慢",
    "缓缓",
    "轻轻",
    "悄悄",
    "安静",
    "寂静",
    "空旷",
    "巨大",
    "小小",
    "整个",
    "一整",
    "这个",
    "那个",
    "一个",
    "每个",
    "任何",
    "所有",
    "周围",
    "附近",
    "远处",
    "近处",
    "家庭专属",
    "私立",
    "高级",
    "豪华",
    "专属",
    "废弃",
    "破旧",
    "的",
    "了",
    "在",
    "到",
    "去",
    "往",
    "进",
    "出",
    "从",
    "把",
    "被",
    "让",
    "给",
    "和",
    "跟",
    "对",
    "向",
    "这",
    "那",
    "一",
    "整",
    "条",
    "座",
    "个",
    "片",
    "处",
    "间",
    "所",
)
ITEM_TRIM_PREFIXES = (
    "一个",
    "一台",
    "一套",
    "一把",
    "一颗",
    "一枚",
    "一块",
    "这种",
    "那种",
    "这个",
    "那个",
    "的",
    "了",
    "在",
    "到",
    "把",
    "被",
    "让",
    "给",
    "和",
    "跟",
    "对",
    "向",
)

SPEECH_RE = "|".join(re.escape(v) for v in SPEECH_VERBS)
ROLE_RE = "|".join(re.escape(v) for v in ROLE_SUFFIXES)
LOCATION_RE = "|".join(re.escape(v) for v in LOCATION_SUFFIXES)
ITEM_RE = "|".join(re.escape(v) for v in ITEM_SUFFIXES)
HAN_RUN_RE = re.compile(r"[一-龥]{2,}")
HAN_CHAR_RE = re.compile(r"[一-龥]")
SENTENCE_END = "。！？!?；;\n"
SENTENCE_START = "。！？!?；;\n"
TITLE_SPLIT_RE = re.compile(r"[，,。！？；;、\s]+")
DATE_RE = re.compile(r"\d{4}\.\d{1,2}\.\d{1,2}")


@dataclass
class Mention:
    article_id: int
    segment_id: int | None
    surface: str
    quote: str


@dataclass
class Candidate:
    kind: str
    name: str
    aliases: set[str] = field(default_factory=set)
    mentions: list[Mention] = field(default_factory=list)
    count: int = 0
    strong: int = 0
    title_hit: bool = False
    article_ids: set[int] = field(default_factory=set)


def _valid_name(name: str, max_len: int = 8) -> bool:
    if not name:
        return False
    name = name.strip()
    if len(name) < 2 or len(name) > max_len:
        return False
    if name in STOPWORDS:
        return False
    if name[0] in BAD_NAME_START or name[-1] in BAD_NAME_END:
        return False
    if not all(HAN_CHAR_RE.match(ch) for ch in name):
        return False
    return True


def _valid_known_term(term: str) -> bool:
    """已知词允许拉丁字母、空格和数字（例如 X7 Future）。"""
    term = (term or "").strip()
    if len(term) < 2 or len(term) > 40:
        return False
    if term in STOPWORDS:
        return False
    return True


def _valid_approved_term(term: str) -> bool:
    """人工已收录的词直接信任，不再用通用停用词表二次拦截。

    「姨妈」这类词在通用抽取里是停用词，但一旦被人工收录，就应该继续
    累积它在正文里的出处，而不是在下一次重扫时被规则丢掉。
    """
    term = (term or "").strip()
    return 2 <= len(term) <= 40


MULTI_ROLE_SUFFIXES = tuple(suffix for suffix in ROLE_SUFFIXES if len(suffix) >= 2)


def _valid_place(name: str) -> bool:
    if not _valid_known_term(name):
        return False
    if len(name) > 8:
        return False
    if not all(HAN_CHAR_RE.match(ch) for ch in name):
        return False
    # 地名里出现这些字，通常是「站在原地」「直朝废料池」这类动作/介词短语。
    if any(
        ch in "的了着过是和跟对把被让给就都也又还只个位在从到往进出站坐走来去"
        "看说想拿放停靠躺蹲跪趴朝冲奔走飞落升沉浮转绕穿愣呆"
        for ch in name
    ):
        return False
    # 「级风险区」「第一区」「区域原」是上一级地名被截断的碎片。
    if name[0] in "级第" or "区域" in name:
        return False
    # 「魏先生侧身引路」这类“人物称呼 + 动作”不是地名。
    if any(role in name for role in MULTI_ROLE_SUFFIXES):
        return False
    if any(word in name for word in ("能量", "数据", "信号")):
        return False
    if any(
        bad in name
        for bad in ("脑海", "链路", "专属", "私立", "家庭", "高级", "豪华", "电池", "通讯")
    ):
        return False
    return True


def _valid_item(name: str) -> bool:
    if not _valid_known_term(name):
        return False
    if len(name) > 12:
        return False
    if not all(HAN_CHAR_RE.match(ch) for ch in name):
        return False
    if any(ch in "的了着过是和跟对把被让给就都也又还只个位" for ch in name):
        return False
    return True


def _is_han(ch: str) -> bool:
    return bool(ch) and bool(HAN_CHAR_RE.match(ch))


def _quote_around(text: str, start: int, end: int, max_len: int = 220) -> str:
    left = 0
    for index in range(start - 1, -1, -1):
        if text[index] in SENTENCE_END:
            left = index + 1
            break
    right = len(text)
    for index in range(end, len(text)):
        if text[index] in SENTENCE_END:
            right = index + 1
            break
    quote = text[left:right].strip()
    if len(quote) > max_len:
        clip_start = max(left, start - max_len // 2)
        clip_end = min(right, clip_start + max_len)
        clip_start = max(left, clip_end - max_len)
        quote = text[clip_start:clip_end].strip()
    return quote


def _starts_with_any(value: str, values) -> bool:
    return any(value.startswith(item) for item in values)


def _find_occurrences(text: str, term: str) -> list[tuple[int, int]]:
    term = term or ""
    if len(term) < 1:
        return []
    found: list[tuple[int, int]] = []
    if all(HAN_CHAR_RE.match(ch) for ch in term):
        if len(term) == 1:
            for index, ch in enumerate(text):
                if ch != term:
                    continue
                before = text[index - 1] if index > 0 else ""
                after = text[index + 1] if index + 1 < len(text) else ""
                if not _is_han(before) and not _is_han(after):
                    found.append((index, index + 1))
            return found
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            found.append((index, index + len(term)))
            start = index + max(1, len(term))
        return found
    for match in re.finditer(re.escape(term), text, re.I):
        found.append((match.start(), match.end()))
    return found


class _Accumulator:
    def __init__(self, article_id: int, title: str, alias_lookup: dict):
        self.article_id = article_id
        self.title = title
        self.alias_lookup = alias_lookup
        self.items: dict[tuple[str, str], Candidate] = {}

    def add(
        self,
        kind: str,
        name: str,
        surface: str,
        segment_id: int | None,
        segment_text: str,
        start: int,
        end: int,
        strong: bool = False,
        aliases: tuple[str, ...] = (),
        trusted: bool = False,
    ) -> None:
        name = (name or "").strip()
        surface = (surface or name).strip()
        validator = _valid_approved_term if trusted else _valid_known_term
        if not validator(name):
            return
        canonical = self.alias_lookup.get((kind, name), name)
        if canonical != name:
            aliases = tuple(aliases) + (name,)
            name = canonical
        if not validator(name):
            return
        key = (kind, name)
        entry = self.items.get(key)
        if entry is None:
            entry = Candidate(kind=kind, name=name)
            self.items[key] = entry
        entry.count += 1
        entry.article_ids.add(self.article_id)
        if strong:
            entry.strong += 1
        if surface != name:
            entry.aliases.add(surface)
        for alias in aliases:
            alias = (alias or "").strip()
            if alias and alias != name:
                entry.aliases.add(alias)
        if (name in self.title) or (surface in self.title):
            entry.title_hit = True
        if len(entry.mentions) >= 2:
            return
        quote = _quote_around(segment_text, start, end)
        if not quote:
            return
        if any(m.segment_id == segment_id and m.quote == quote for m in entry.mentions):
            return
        entry.mentions.append(
            Mention(
                article_id=self.article_id,
                segment_id=segment_id,
                surface=surface,
                quote=quote,
            )
        )


def _scan_known_terms(
    acc: _Accumulator,
    segments: list[dict],
    known_terms: tuple[tuple[str, str, str], ...],
) -> None:
    if not known_terms:
        return
    # 长词优先，避免「老街」先于「云栖老街」产生重复候选。
    ordered = sorted(known_terms, key=lambda item: len(item[0]), reverse=True)
    for term, kind, canonical in ordered:
        if kind not in KINDS:
            continue
        if not _valid_approved_term(term) or not _valid_approved_term(canonical):
            continue
        for segment in segments:
            text = segment.get("plain_text") or ""
            if not text:
                continue
            for start, end in _find_occurrences(text, term):
                acc.add(
                    kind,
                    canonical,
                    term,
                    segment.get("id"),
                    text,
                    start,
                    end,
                    strong=True,
                    aliases=(term,),
                    trusted=True,
                )


def _scan_person(acc: _Accumulator, segment: dict, title: str) -> None:
    text = segment.get("plain_text") or ""
    if not text:
        return
    segment_id = segment.get("id")

    # 1) 姓氏 + 1~3 字名：林见深、苏晚晴、林素、江寒川、顾清川、刘副。
    for run_match in HAN_RUN_RE.finditer(text):
        run = run_match.group(0)
        base = run_match.start()
        for start in range(len(run)):
            # 只从连续汉字的开头取姓名，避免「于苏晚晴」「盖林素」这类
            # 把前一个动词/介词吞进来的碎片。正文中段的姓名由引语、称呼、
            # 标题和已收录别名负责补充。
            if start > 0 and _is_han(run[start - 1]):
                continue
            for length in (4, 3, 2):
                if start + length > len(run):
                    continue
                name = run[start : start + length]
                if name[0] not in COMMON_SURNAMES or not _valid_name(name):
                    continue
                after = run[start + length : start + length + 5]
                # 「阮大小姐」「阮大组长」「沈小姐」里的阮大/阮小/沈小是
                # 称呼的一部分，不是姓名。
                if name[-1] in "大小老副正" and _starts_with_any(
                    after, ROLE_SUFFIXES
                ):
                    continue
                strong = _starts_with_any(after, SPEECH_VERBS) or _starts_with_any(
                    after, ROLE_SUFFIXES
                )
                acc.add(
                    "person",
                    name,
                    name,
                    segment_id,
                    text,
                    base + start,
                    base + start + length,
                    strong=strong,
                )

    # 2) 直接引语前的姓名：清川说、屹燃问、林素回答。
    speech_pattern = re.compile(
        r"(?:^|[，。！？；：“”‘’、·\s（(])([一-龥]{2,4})(?=(?:%s))"
        % SPEECH_RE
    )
    for match in speech_pattern.finditer(text):
        name = match.group(1)
        if len(name) > 3:
            continue
        if name[0] not in COMMON_SURNAMES:
            continue
        if _valid_name(name):
            start = match.start(1)
            acc.add(
                "person",
                name,
                name,
                segment_id,
                text,
                start,
                start + len(name),
                strong=True,
            )

    # 3) 称呼/身份：天哥、陆总、老管家、刘副组长。
    role_pattern = re.compile(
        r"([一-龥])(%s)" % ROLE_RE
    )
    for match in role_pattern.finditer(text):
        prefix, suffix = match.group(1), match.group(2)
        if not prefix or any(ch in BAD_ROLE_PREFIX for ch in prefix):
            continue
        if suffix in ROLE_REQUIRES_SURNAME:
            if prefix == "老" and suffix in ("管家", "先生", "太太", "夫人", "爷"):
                pass
            elif prefix[0] not in COMMON_SURNAMES:
                continue
        if suffix == "总" and (len(prefix) != 1 or prefix[0] not in COMMON_SURNAMES):
            continue
        # 「老管家」里的「老」可以；「家老管家」已经在上面的坏前缀里排除。
        name = prefix + suffix
        if not _valid_known_term(name):
            continue
        start = match.start()
        acc.add(
            "person",
            name,
            name,
            segment_id,
            text,
            start,
            start + len(name),
            strong=True,
        )

    # 4) 标题里的短姓名：只作为提示，正文必须至少出现一次。
    if title:
        clause_text = DATE_RE.sub("", title)
        for clause in TITLE_SPLIT_RE.split(clause_text):
            clause = clause.strip()
            if not clause:
                continue
            for length in (4, 3, 2):
                if len(clause) < length:
                    continue
                name = clause[:length]
                if not _valid_name(name):
                    continue
                if name[0] not in COMMON_SURNAMES and not name.endswith(
                    ROLE_SUFFIXES
                ):
                    continue
                if name not in text:
                    continue
                start = text.find(name)
                acc.add(
                    "person",
                    name,
                    name,
                    segment_id,
                    text,
                    start,
                    start + len(name),
                )


# 地点窗口可能从人物名末尾开始，例如「汐刚踏入办公区」。
# 动词短语不只在开头出现，出现在窗口中间时也要把前面的残字一起裁掉。
LOCATION_INFIX_TRIM = (
    "刚踏入",
    "刚走进",
    "刚进入",
    "刚来到",
    "刚回到",
    "刚走出",
    "走进了",
    "走进",
    "踏入了",
    "踏入",
    "进入了",
    "进入",
    "来到了",
    "来到",
    "返回了",
    "返回",
    "离开了",
    "离开",
    "走出了",
    "走出",
    "穿过了",
    "穿过",
    "赶到了",
    "赶到",
    "前往了",
    "前往",
    "回到了",
    "回到",
)


def _trim_location(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in LOCATION_TRIM_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix) + 1:
                name = name[len(prefix) :]
                changed = True
                break
        if changed:
            continue
        for marker in LOCATION_INFIX_TRIM:
            index = name.find(marker)
            if index <= 0:
                continue
            rest = name[index + len(marker) :]
            if len(rest) >= 2:
                name = rest
                changed = True
                break
    return name


def _scan_place(
    acc: _Accumulator,
    segment: dict,
    person_terms: tuple[str, ...] = (),
) -> None:
    text = segment.get("plain_text") or ""
    if not text:
        return
    segment_id = segment.get("id")
    for match in re.finditer(r"(%s)" % LOCATION_RE, text):
        suffix = match.group(1)
        end = match.start()
        left = end
        while left > 0 and end - left < 6 and _is_han(text[left - 1]):
            left -= 1
        # 只尝试最长前缀，再由 _trim_location 去掉动词/数量词。
        # 不再逐级缩短，避免「离开老街」退化成「开老街」、
        # 「工作流」退化成「作流」这类碎片。
        prefix = text[left:end]
        if (
            len(prefix) == 1
            and prefix in LOCATION_CONNECTOR_PREFIXES
            and suffix in COMMON_SURNAMES
        ):
            # 「但江寒川」「将江寒川」是连词 + 姓氏，不是「但江」地名。
            continue
        if suffix in COMMON_SURNAMES and any(
            prefix.endswith(word) for word in LOCATION_CONNECTOR_WORDS
        ):
            # 「其实江寒川」「包括江寒川」是连词短语 + 姓氏。
            continue
        name = _trim_location(prefix + suffix)
        if not _valid_place(name):
            continue
        if name[0] in BAD_LOCATION_START:
            continue
        if person_terms and name.startswith(person_terms):
            # 「苏晚晴原」「林素与江」是已收录人物名被地点后缀截出来的碎片。
            continue
        full_end = end + len(suffix)
        acc.add(
            "place",
            name,
            name,
            segment_id,
            text,
            full_end - len(name),
            full_end,
        )


def _trim_item(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in ITEM_TRIM_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix) + 1:
                name = name[len(prefix) :]
                changed = True
                break
    return name


def _scan_item(acc: _Accumulator, segment: dict, title: str) -> None:
    text = segment.get("plain_text") or ""
    if not text:
        return
    segment_id = segment.get("id")

    # 物品只取“词表里的完整名词”，不再向左吞并人物/动作。
    # 这样能避免「顾清川机甲」「驾驶机甲」「两台机甲」这类短语。
    terms = list(dict.fromkeys(ITEM_SEEDS + ITEM_SUFFIXES))
    for term in terms:
        for start, end in _find_occurrences(text, term):
            acc.add(
                "item",
                term,
                term,
                segment_id,
                text,
                start,
                end,
                strong=True,
            )


def extract_article(
    article: dict,
    segments: list[dict],
    known_terms: tuple[tuple[str, str, str], ...] = (),
) -> list[Candidate]:
    """抽取一篇文章的候选实体。

    known_terms: (词, 类型, 规范名) 的集合，用于让已收录实体/别名持续累积出处。
    segments: 至少包含 id / plain_text。
    """
    article_id = int(article.get("id") or 0)
    title = article.get("title") or ""
    alias_lookup = {
        (kind, term): canonical
        for term, kind, canonical in known_terms
        if kind in KINDS
    }
    acc = _Accumulator(article_id, title, alias_lookup)
    _scan_known_terms(acc, segments, known_terms)
    person_terms = tuple(
        sorted(
            {
                term
                for term, kind, canonical in known_terms
                if kind == "person" and len(term) >= 2
            },
            key=len,
            reverse=True,
        )
    )
    for segment in segments:
        _scan_person(acc, segment, title)
        _scan_place(acc, segment, person_terms)
        _scan_item(acc, segment, title)
    return list(acc.items.values())


def _candidate_suffix(name: str, suffixes) -> str:
    for suffix in suffixes:
        if name.endswith(suffix):
            return suffix
    return ""


def accept_candidate(candidate: Candidate, article_count: int = 1) -> bool:
    """批量抽取后的保守阈值。已知实体在扫描器中直接放行。"""
    if not candidate.mentions:
        return False
    name = candidate.name
    if candidate.kind == "person":
        # 人物只认“有称呼/引语证据”的候选。单纯出现在句首的 2–3 字词
        # 很可能是普通动词或副词，不再仅凭“像姓名”就放行。
        if candidate.strong < 1:
            return False
        role = _candidate_suffix(name, ROLE_SUFFIXES)
        if role and len(name) >= 3 and candidate.strong < 2:
            return False
        if role:
            # 「天哥」「林姨」这类昵称不以常见姓氏开头，需要至少两次
            # 称呼证据，避免把「像姨」「听姐」这类一次性碎片收进来。
            if name[0] in COMMON_SURNAMES or name[0] == "老":
                return True
            return candidate.strong >= 2
        if len(name) <= 2:
            return candidate.count >= 2
        # 三字以上全名：至少 3 次称呼/引语证据且累计出现 5 次，
        # 过滤「连忙追」「冷静反」这类只出现两三次的动词碎片。
        return candidate.strong >= 3 and candidate.count >= 5
    if candidate.kind == "place":
        if candidate.count >= 2:
            return True
        return bool(candidate.title_hit)
    if candidate.kind == "item":
        if candidate.count >= 2:
            return True
        if candidate.title_hit:
            return True
        return name in ITEM_SEEDS
    return False


def merge_candidate(target: Candidate, source: Candidate) -> None:
    target.count += source.count
    target.strong += source.strong
    target.title_hit = target.title_hit or source.title_hit
    target.article_ids |= source.article_ids
    target.aliases |= source.aliases
    target.aliases.add(source.name)
    for mention in source.mentions:
        if len(target.mentions) >= 20:
            break
        if any(
            item.segment_id == mention.segment_id and item.quote == mention.quote
            for item in target.mentions
        ):
            continue
        target.mentions.append(mention)


def postprocess(candidates: list[Candidate]) -> list[Candidate]:
    """合并同批次里的全名/简称，并去掉明显的前缀碎片。"""
    by_key = {(c.kind, c.name): c for c in candidates}
    removed: set[tuple[str, str]] = set()

    # 人物：顾清川 -> 清川；林见深 -> 见深；江寒川 -> 寒川。
    person_names = [
        c.name for c in candidates if c.kind == "person" and len(c.name) >= 3
    ]
    for full_name in person_names:
        full = by_key.get(("person", full_name))
        if full is None:
            continue
        short = full_name[1:]
        # 「范部长」去掉姓只剩「部长」、「老管家」只剩「管家」，都是
        # 通用称呼，不能再作为别名挂回具体人物身上。
        role_short = bool(_candidate_suffix(short, ROLE_SUFFIXES))
        if len(short) >= 2 and not role_short:
            full.aliases.add(short)
        # 把同批次的简称并入全名。
        short_candidate = by_key.get(("person", short))
        if (
            short_candidate is not None
            and short_candidate is not full
            and not role_short
        ):
            merge_candidate(full, short_candidate)
            removed.add(("person", short))
        # 去掉「林见」「江寒」这类全名前缀碎片。
        for cut in range(2, len(full_name)):
            prefix = full_name[:cut]
            prefix_candidate = by_key.get(("person", prefix))
            if prefix_candidate is not None and prefix_candidate is not full:
                if full.count >= prefix_candidate.count:
                    removed.add(("person", prefix))

    # 去掉「顾清川遗」「江寒川所」这类“完整姓名 + 动词/助词”的长碎片。
    for candidate in list(candidates):
        if candidate.kind != "person" or len(candidate.name) < 4:
            continue
        if candidate.name.endswith(ROLE_SUFFIXES):
            continue
        for cut in range(3, len(candidate.name)):
            prefix = candidate.name[:cut]
            prefix_candidate = by_key.get(("person", prefix))
            if prefix_candidate is None or prefix_candidate is candidate:
                continue
            if prefix_candidate.count >= candidate.count:
                removed.add(("person", candidate.name))
                break

    # 地点：云栖老街 吸收 云栖 / 老街。
    place_names = sorted(
        [c.name for c in candidates if c.kind == "place"], key=len, reverse=True
    )
    for long_name in place_names:
        long = by_key.get(("place", long_name))
        if long is None:
            continue
        if long.count < 2:
            continue
        for short_name in place_names:
            if short_name == long_name or len(short_name) < 2:
                continue
            if short_name not in long_name:
                continue
            if not (long_name.startswith(short_name) or long_name.endswith(short_name)):
                continue
            short = by_key.get(("place", short_name))
            if short is None or short is long:
                continue
            merge_candidate(long, short)
            removed.add(("place", short_name))

    result = []
    for candidate in candidates:
        key = (candidate.kind, candidate.name)
        if key in removed:
            continue
        # 合并后可能已经变成另一个候选的别名，去掉空壳。
        if not candidate.mentions:
            continue
        result.append(candidate)
    return result
