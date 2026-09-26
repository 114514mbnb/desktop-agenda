"""QQ 群通知文本 → 候选日程。

输入是粘贴进 inbox 的群通知（可含多条、来自不同群），输出是 Candidate 列表。
判定逻辑全部是确定性规则：谁在什么时间、什么地方、和谁、有什么备注。
"""

from __future__ import annotations

import os
import re
from datetime import date as Date, datetime

from . import parsing as P
from .models import Candidate, NoticeMessage, ParseOutcome
from .parsing import DATE_WORD_RE, TIME_FIND_RE

# ---------------------------------------------------------------------------
# 关键词
# ---------------------------------------------------------------------------

GROUP_HEADER_RE = re.compile(
    r"^\s*[\[【(（]\s*(?:群|群聊|群名|来自|所属群|QQ群)\s*[:：]?\s*(?P<g>[^\]】)）]{1,40})\s*[\]】)）]\s*(?P<rest>.*)$"
)
BARE_GROUP_HEADER_RE = re.compile(r"^\s*(?:群名|群聊|群|来自)\s*[:：]\s*(?P<g>.{1,40})\s*$")
SENDER_HEADER_RE = re.compile(r"^\s*(?:发送人|发件人|发言人|来源人|管理员)\s*[:：]\s*(?P<s>.{1,30})\s*$")
TIME_HEADER_RE = re.compile(r"^\s*(?:时间|发送时间|日期|落档|粘贴时间)\s*[:：]\s*(?P<t>.{4,40})\s*$")
SEPARATOR_RE = re.compile(r"^\s*(?:-{3,}|={3,}|_{3,}|#{3,}|\*{3,})\s*$")
BULLET_RE = re.compile(r"^\s*(?:[-*•·]\s*|\d{1,2}[.、)）]\s*)")
ALL_BRACKET_RE = re.compile(r"[\[【(（].{1,40}[\]】)）]")
# "【重要】通知：下周五…" 这类前缀只起提醒作用，标题里去掉
LEAD_TAG_RE = re.compile(r"^\s*(?:【[^】]{1,8}】|\[[^\]]{1,8}\])\s*")
TITLE_PREFIX_RE = re.compile(r"^\s*(?:通知|公告|提醒|重要通知|紧急通知|关于)\s*[:：]?\s*")

# 有明确时间锚点的活动词
EVENT_KEYWORDS = (
    "通知", "安排", "会议", "开会", "讲座", "报告", "答辩", "考试", "测验", "补考",
    "报名", "签到", "集合", "面试", "宣讲", "培训", "分享会", "例会", "班会",
    "活动", "比赛", "彩排", "排练", "演出", "值班", "截止", "提交", "上交",
    "聚餐", "团建", "体检", "缴费", "领取", "发放", "搬迁", "上课", "课程",
    "提交截止", "报名截止", "开题", "中期", "答辩会", "组会", "研讨",
)
# 社交闲聊——出现这些且没有活动词时直接丢弃
CHATTER_HINTS = (
    "哈哈", "笑死", "表情", "沙发", "水群", "有人吗", "在吗", "晚安", "早安",
    "收到", "好的", "谢谢", "感谢", "恭喜", "打卡", "红包", "投票", "砍价",
)

LOCATION_KEYWORDS = (
    "会议室", "教室", "报告厅", "礼堂", "体育馆", "操场", "实验室", "办公室",
    "活动室", "多功能厅", "学术厅", "阶梯教室", "图书馆", "食堂", "宿舍",
    "大学生活动中心", "中心", "场馆", "球场", "咖啡厅", "咖啡屋", "楼下", "门口",
    "腾讯会议", "线上", "网上", "Zoom", "zoom", "ZOOM", "钉钉", "飞书", "企业微信",
    "腾讯会议室", "微信群", "电话会议", "线上会议", "云会议",
)
LOCATION_LABEL_RE = re.compile(
    r"(?:地\s*点|地\s*址|场\s*地|位\s*置|会议地点|活动地点|集合地点|签到地点|直播平台|平台)\s*[:：]?\s*"
    r"(?:在|于)?\s*(?P<v>[^\s,，。;；、|]{1,40})"
)
ONLINE_RE = re.compile(
    r"(?P<v>(?:腾讯会议|腾讯会议室|Zoom|zoom|ZOOM|钉钉|飞书|云会议|电话会议|线上会议|微信群|线上|网上)"
    r"(?:号|ID|id)?\s*[:：]?\s*[A-Za-z0-9\- ]{0,18})"
)
# 房间名后面常跟场所类型词，标题里要连着删掉（"3号楼201会议室"），但地点值只取房间号
ROOM_SUFFIX = r"(?:会议室|教室|机房|实验室|报告厅|礼堂|体育馆|办公室|楼|室)?"
ROOM_RE = re.compile(
    r"(?P<v>\d{1,2}\s*号楼\s*[\u4e00-\u9fa5A-Za-z0-9\-]{0,8}"
    r"|[\u4e00-\u9fa5]{2,6}(?:楼|栋|馆|厅)\s*[A-Za-z]{0,2}\s*\d{1,4}[\u4e00-\u9fa50-9A-Za-z\-]{0,4}"
    r"|[\u4e00-\u9fa5]{1,3}教\s*\d{1,4}"
    r"|[\u4e00-\u9fa5]{2,6}室\s*[A-Za-z]{0,2}\s*\d{1,4})"
    r"(?P<suffix>" + ROOM_SUFFIX + r")"
)
# 注意：ROOM_SUFFIX 必须定义在 ROOM_RE 之前；HINT_RE 之后的那份定义已删除
# 地点引导词：只认房间号形态，避免把"在开会"里的"在"当成地点
HINT_RE = re.compile(
    r"(?P<hint>地点|地址|场地|位置|在|到|于|去|往)"
    r"\s*(?P<rest>[\u4e00-\u9fa5A-Za-z0-9]{1,20})"
)
SPLIT_RE = re.compile(r"[,，。;；、|\s]+")
_TIME_TOKEN_RE = re.compile(r"^\d{1,2}\s*[:：点]|^\d{1,2}\s*[-/~～至到]\s*\d")

# 房间号关键词（校园通知里出现频率最高的几种）
LOCATION_EXTRA_KEYWORDS = ("教", "楼", "馆", "厅", "室")
# 规范房间名：机构名 + 楼/栋/馆/厅 + 门牌（3号楼201 / 实验楼B302 / 三教302）
ROOM_NAME_RE = re.compile(
    r"(?:\d{1,2}\s*号楼|[A-Za-z]?\d{1,2}\s*栋|[\u4e00-\u9fa5]{1,5}(?:楼|栋|馆|厅)|[\u4e00-\u9fa5]{1,3}教)"
    r"\s*[A-Za-z]{0,2}\s*\d{1,4}\s*[A-Za-z]?"
)
# 命中关键词但夹带这些动作词时，说明截错了，宁可放弃
LOCATION_ACTION_WORDS = (
    "请", "开", "召", "举", "集", "签", "参", "负", "讲", "报", "带", "交", "领",
)


def _looks_like_time(token: str) -> bool:
    return bool(_TIME_TOKEN_RE.match(token))


def _as_room_name(value: str) -> str:
    """把房间地址收敛成规范写法：3号楼201会议室 → 3号楼201，实验楼B302 → 实验楼B302。"""
    match = ROOM_NAME_RE.search(value)
    if match is not None:
        return match.group(0).strip()
    return re.sub(r"(?:会议室|教室|机房|实验室|报告厅|礼堂|体育馆|办公室)$", "", value.strip()).strip()


def _room_from(text: str) -> str | None:
    """从一段文本里取房间号形态的地点（必须带数字）。"""
    match = ROOM_RE.search(text)
    if match is None:
        return None
    tail = text[match.end():match.end() + 2]
    # "主讲：李娜教授"会撞上"李"+"3"？不会，但"主讲3"这种要挡住；人名/职务紧跟则不算地点
    if any(role in tail for role in ("老师", "教授", "同学", "主任", "书记", "负责")):
        return None
    value = _as_room_name(_trim_location(match.group("v")))
    if value and any(ch.isdigit() for ch in value) and not _is_headerish(value):
        return value
    return None


KEYWORD_LOCATION_RE = re.compile(
    r"(?P<v>[\u4e00-\u9fa5]{0,5}(?:学术报告厅|报告厅|会议室|活动中心|体育馆|体育场|图书馆|大礼堂|"
    r"阶梯教室|食堂|实验楼|实验室|教学楼|办公室|宿舍楼|操场|礼堂|会堂|教室|广场|咖啡厅))"
)


def _keyword_location(token: str) -> str | None:
    """只有地点词、没有房间号的情况（学术报告厅 / 大学生活动中心 / 体育馆）。

    长词表已保证"报告厅"整体成词（否则会被截成"厅"，也不会误吞"3号楼"）；
    再挡两类误命中：紧跟在动词后的（"主讲：李娜教授"）、尾字是动作词的（"签到"）。
    """
    for match in KEYWORD_LOCATION_RE.finditer(token):
        word = match.group("v")
        if word[-1:] in LOCATION_ACTION_WORDS:
            continue
        before = token[max(0, match.start() - 5):match.start()]
        if any(verb in before for verb in ("主讲", "负责", "联系", "前往", "地点是", "集合于")):
            continue
        value = _trim_location(word)
        if len(value) >= 2 and not _is_headerish(value):
            return value
    return None


def _is_structural_line(line: str) -> bool:
    """整行只是"日期 + 时间 + 地点 + 标签"，没有说明是什么事 → 不是标题。

    例：「通知：9月22日 14:00 在3号楼201会议室」是结构行；
        「9月24日 上午9点 学术报告厅 讲座《…》」含"讲座"，是标题行。

    另外把**纯分节标签**（`【提醒】`、`[通知]`）也算结构行：
    QQ 群通知常见「【提醒】/ 1.… / 2.…」这种排版，标签自己不是事项，
    真正的事在下一行。踩过的坑：以前会把 `【提醒】` 当标题，
    后面那行的期限状语（"…今天中午12:00前"）就没人清洗，标题变成了
    `中秋国庆假期备案 前 所有学生完成"…"流程`。
    """
    if NOTE_START_RE.search(line):
        return False
    stripped = line.strip()
    if re.fullmatch(r"[\[【(（]\s*(?:提醒|通知|公告|注意|重要|紧急|更新|补充)\s*[\]】)）][:：]?", stripped):
        return True
    residual = DATE_WORD_RE.sub("", line)
    residual = TIME_FIND_RE.sub("", residual)
    residual = ROOM_RE.sub("", residual)
    residual = re.sub(r"(?:通知|公告|提醒|重要|关于|在|于|到|地点|地址|场地|位置|[:：(),，。;；、\s])", "", residual)
    return len(residual) <= 4


def _fallback_title(line: str) -> str:
    """标题被抽空时（例如"明天上午9点 在活动中心 集合"）退回用原文清洗后的前 24 字。"""
    value = DATE_WORD_RE.sub("", line)
    value = TIME_FIND_RE.sub("", value)
    value = _clean_title(value)
    value = re.sub(r"^\s*[在到于去往]\s*", "", value)
    value = re.sub(r"[，,、；;：:|\s]+", " ", value).strip()
    return value[:24] or "（无标题事项）"


def _taken_spans(
    line: str,
    date_text: str | None,
    time_text: str | None,
    location: str | None,
    people: tuple[str, ...],
    notes: list[str],
) -> list[tuple[int, int]]:
    """收集"已经单独成字段"的片段在标题行里的位置。"""
    spans: list[tuple[int, int]] = []

    def add(pattern: str, *, eat_separator: bool = False) -> None:
        for match in re.finditer(pattern, line):
            end = match.end()
            if eat_separator:
                # 顺手吃掉紧跟的顿号/逗号/冒号：否则切完会留下"， 所有学生完成…"
                tail = re.match(r"[\s,，、;；:：]+", line[end:])
                if tail is not None:
                    end += tail.end()
            spans.append((match.start(), end))

    # 注意：解析出的片段可能带首尾空格（正则里的 \s*），必须 strip 后再定位
    for text in (date_text, time_text):
        cleaned = (text or "").strip()
        if cleaned:
            add(re.escape(cleaned))
    for text in (location,):
        cleaned = (text or "").strip()
        if cleaned:
            # 连场所类型词一起删，避免标题里残留"会议室"
            add(re.escape(cleaned) + ROOM_SUFFIX)
            for piece in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", cleaned):
                add(re.escape(piece))
    for person in people:
        if len(person) >= 2:
            add(re.escape(person) + r"(?:老师|同学|师兄|师姐|队长|部长|班长|书记|主任|导员|教授)?")
    for note in notes:
        cleaned = (note or "").strip()
        if len(cleaned) >= 2:
            add(re.escape(cleaned))
    add(r"(?:负责人|联系人|主持|召集人|组织者|联系)\s*[:：]?")
    # 截止期限短语也一起抽掉：它已经进了"时段"字段（"今天中午12:00前" → 12:00），
    # 留在标题里就是半截状语。必须在**原始行**上识别，且连后面的标点一起吃掉，
    # 否则会剩下"， 所有学生完成…"这种带逗号的碎片。
    add(_DEADLINE_PHRASE, eat_separator=True)
    return spans


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """按区间删除已经单独成字段的内容（时间/地点/人名/备注）。"""
    if not spans:
        return text
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        cursor = max(cursor, end)
    pieces.append(text[cursor:])
    return "".join(pieces)


def _compose_title(line: str, taken: list[tuple[int, int]]) -> str:
    """从首个有内容的行里，删掉已被时间/地点/人名/备注字段认领的片段，剩下的就是标题。"""
    cleaned = _strip_spans(line, taken)
    cleaned = _clean_title(cleaned)
    # 「事项名：后面整句是在说这件事该怎么办」——在首个**标签冒号**处截断。
    # 这一步必须赶在下面的"标点统一压成空格"之前：那一步会把冒号也变成空格，
    # 之后就再也认不出边界了，标题会剩成
    # 「中秋国庆假期备案 所有学生完成"假期学生去向备案"流程」。
    # 但括号里的冒号（"时间：周五 9:00"）不算，所以括起来的部分先挖掉再判断。
    probe = re.sub(r"[（(][^）)]*[)）]", "", cleaned)
    if "：" in probe or ":" in probe:
        columns = re.split(r"[:：]", cleaned, maxsplit=1)
        if len(columns) == 2 and len(columns[0].strip()) >= 2:
            cleaned = columns[0]
    # 片段被抽走后常留下悬空的连接词/动词，例如"在会议室召开班级例会" → "班级例会"
    cleaned = re.sub(r"^(?:在|于|到|去|往|地点|地址|场地)\s*", "", cleaned)
    cleaned = re.sub(r"^(?:召开|举行|举办|开始|进行|集合|签到)\s*", "", cleaned)
    cleaned = re.sub(r"(?:召开|举行|举办|开始|进行|集合|签到)$", "", cleaned)
    cleaned = LEAD_TAG_RE.sub("", cleaned)
    # 标点统一压成空格之前，先把**括号里的内容**挖出来保护起来：
    # 「讲座（时间：9:00）」里的冒号是时间，不是"事项名：说明"的那个标签冒号，
    # 一起压掉会变成「讲座（时间 9 00）」。括号是最省事的边界。
    protected: list[str] = []

    def _protect(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    cleaned = re.sub(r"[（(][^）)]*[)）]", _protect, cleaned)
    # 反复收敛：删除被抽空后残留的标点与单字连接词（"，，请" / "，主讲：" / "@ 负责"）
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"(?:会议室|教室|机房|实验室|报告厅|礼堂|体育馆)$", " ", cleaned)
        cleaned = re.sub(r"[，,、；;：:|\s]+", " ", cleaned)
        cleaned = cleaned.replace("@@", "@")
        cleaned = re.sub(r"(?:^|\s)@(?=\s|$)", " ", cleaned)
        cleaned = re.sub(r"(?:^|\s)(?:请|与|和|跟|及|由|负责|主讲|备注|说明|提前)(?=\s|$)", " ", cleaned)
        cleaned = re.sub(r"\s*[在到于去往]\s*$", " ", cleaned)
        cleaned = cleaned.strip(" \t,，。;；、|:：@")
    for index, text in enumerate(protected):
        cleaned = cleaned.replace(f"\x00{index}\x00", text)
    return _trim_deadline(cleaned)


def _first_content_line(text: str) -> str:
    """挑第一条真正表达"什么事"的行。"""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines:
        if not _is_structural_line(line):
            return line
    return lines[0] if lines else text.strip()


def _find_location(lines: list[str]) -> str | None:
    candidates = [line for line in lines if not _is_headerish(line)]

    for line in candidates:
        labeled = LOCATION_LABEL_RE.search(line)
        if labeled is not None:
            value = _as_room_name(_trim_location(labeled.group("v")))
            if value:
                return value
    for line in candidates:
        online = ONLINE_RE.search(line)
        if online is not None:
            value = _as_room_name(_trim_location(online.group("v")))
            if value:
                return value
    # 引导词窗口：在/到/地点： 之后的那一小段最可能是地点（先认房间号）
    for line in candidates:
        for match in HINT_RE.finditer(line):
            value = _room_from(match.group("rest")) or _keyword_location(match.group("rest"))
            if value:
                return value
    # 只有地点词、没有房间号（学术报告厅、大学生活动中心…）；
    # _keyword_location 会挡住"主讲"这类动词，避免"报告厅"被截成"厅"
    for line in candidates:
        value = _keyword_location(line)
        if value:
            return value
    for line in candidates:
        for token in SPLIT_RE.split(line):
            if not token or _looks_like_time(token):
                continue
            value = _room_from(token) or _keyword_location(token)
            if value:
                return value
    return None

PEOPLE_LABEL_RE = re.compile(
    r"(?:负责人|联系人|主持(?:人)?|召集人|组织者|带队(?:老师|人)?|指导(?:老师|教师)|"
    r"报名(?:请)?找|咨询(?:请)?找|联系)\s*[:：]?\s*"
    r"(?P<v>[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9·]{0,11}?"
    r"(?:老师|同学|师兄|师姐|师弟|师妹|学长|学姐|队长|部长|班长|书记|主任|导员|教授)?)"
)
AT_RE = re.compile(r"@(?P<v>[\u4e00-\u9fa5A-Za-z0-9_\-·]{1,20})")
ROLE_HINT_RE = re.compile(
    r"(?P<v>[\u4e00-\u9fa5]{2,4})(?=(?:老师|同学|师兄|师姐|师弟|师妹|学长|学姐|队长|部长|班长|书记|主任|导员|教授))"
)
NAME_VERB_RE = re.compile(
    r"(?:由|请|找|通知|联系)\s*(?P<v>[\u4e00-\u9fa5]{2,4})(?=[\s,，。:：、和跟与]|$)"
)
# "请准备周报"这类是要求，不是人
REQUIREMENT_VERBS = ("准备", "携带", "提交", "上交", "注意", "记得", "提前", "按时", "带好", "完成", "参加")

NAME_STOPWORDS = ("大家", "各位", "全员", "人员", "所有人", "同学", "全体", "成员", "群主", "管理")
NAME_ROLE_SUFFIX_RE = re.compile(r"(?:老师|同学|师兄|师姐|师弟|师妹|学长|学姐|队长|部长|班长|书记|主任|导员|教授)$")
LOCATION_ACTION_RE = re.compile(r"(?:举行|召开|开|办|集合|签到|举办|进行|地点|地址|举行|开始|召开|会议室|教室).*$")


def _trim_location(value: str) -> str:
    value = re.sub(r"^(?:在|于|地点[:：]?|地址[:：]?|场地[:：]?)\s*", "", value.strip())
    value = re.sub(r"[，。；、,;|]+$", "", value).strip()
    value = LOCATION_ACTION_RE.sub("", value).strip()
    return value.strip(" 的")[:40]

NOTE_KEYWORDS = (
    "备注", "说明", "注意", "须知", "提醒", "要求", "请", "需", "需要", "务必",
    "记得", "注意事", "携带", "自带", "准备", "穿着", "着装", "材料", "截止",
)
NOTE_START_RE = re.compile(
    r"(?:备\s*注|说\s*明|注\s*意(?:事项)?|提\s*醒|要\s*求|请\s*注\s*意|务必|记得|"
    r"需\s*要|需\s*携\s*带|携\s*带|自\s*带|准\s*备)\s*[:：]?\s*"
)

TITLE_PREFIX_RE = re.compile(r"^\s*(?:通知|公告|提醒|重要通知|紧急通知|关于)\s*[:：]?\s*")
TITLE_SUFFIX_RE = re.compile(r"\s*(?:通知|公告|安排|事宜)\s*$")
#: 截止期限的引导词：从这里往后都是"什么时候要交"，不是事项名本身。
#: 踩过的坑：QQ 群里「中秋国庆假期备案：今天中午12:00前，所有学生完成…」，
#: 时间片段被抽走后标题剩成「中秋国庆假期备案 前 所有学生完成"…"流程」——
#: 半截状语粘在标题上，用户看着就以为解析坏了。
#:
#: 两条约束（都是实测调出来的）：
#:   * 片段被抽走的地方会留下**空格**（"备案 前"），所以时间词之间的空白要允许；
#:   * 前半段不能用"月/日/时刻"当锚点——「请在9月25日17:00前把周报发到群里」里
#:     那个日期是**保留在标题里**的（"9月25日 请在 前把周报…"比"请在前把周报…"像话），
#:     所以锚点只认"今天/明天/本周…"这类相对日期词。
#: 时间词与"前"之间允许出现的字符：数字、点分秒、冒号（"12:00"里的冒号！）、空白。
#: 踩过的坑：漏了冒号，`今天中午12:00前` 就整条匹配不上——症状是标题里那句
#: "…备案 前 所有学生完成…"照旧粘着，看着像没改过。
#: 截止期限短语。
#:
#: 约束（都是实测调出来的，缺一个就出错）：
#:   ① **必须有相对时间锚点**（今天/明天/本周/中午/上午…）。只认"…前"会把
#:      「讲座《多模态大模型前沿》」的"前沿"当截止期限，标题切成「讲座《多模态大模型」。
#:   ② **锚点不能写成可选组**。写成 `(?:(?:今天|…)\s*)?(?:[上下]午|…)?` 之后，
#:      「上午9点 学术报告厅」里的"上午"变成锚点，后面随便找个"前"就开吃，同样切坏标题。
#:   ③ **冒号必须写进字符类**。漏了它 `12:00` 整条匹配不上，症状是"改了跟没改一样"。
#:
#: 关键：这个替换要在**原始行**上做（还没抽掉日期/时间片段时）。
#: 片段一抽走，句子就碎了（"今天中午12:00前" → "中午 前"），再想认出期限短语就很难。
#: 也正因如此，不用"月/日"当锚点——「请在9月25日17:00前把周报…」整句都是流程描述，
#: 切掉日期反而更不像话，这种就原样留着。
_DEADLINE_SPAN = r"[点時时分秒:：\s\d]{0,8}"
_DEADLINE_REL = r"(?:今天|今日|明天|明日|后天|本周|这周|本周末|下周|周[一二三四五六日天])"
_DEADLINE_HALF = r"(?:[上下]午|中午|晚上|早上|凌晨)"
_DEADLINE_PHRASE = (
    r"(?:请?于|请?在)?\s*"
    r"(?:" + _DEADLINE_REL + r"\s*" + _DEADLINE_HALF + r"?|" + _DEADLINE_HALF + r")"
    + _DEADLINE_SPAN +
    r"(?:前|之前|以前|以内|之内)"
)
DEADLINE_PHRASE_RE = re.compile(_DEADLINE_PHRASE)
#: 只剩一个光秃秃的"前"时（"…发到群里 前"），只切这个字，前面的都留着
DEADLINE_BARE_TAIL_RE = re.compile(r"\s*(?:前|之前|以前|以内|之内)\s*$")
ROOM_TAIL_RE = re.compile(
    r"([\u4e00-\u9fa5]{1,5}(?:室|厅|馆|楼|栋))"
    r"(?:召开|举行|举办|召开|开|有|见|集合|签到|门口|见|内|里)"
)
LOCATION_KEYWORDS = LOCATION_KEYWORDS + ("学术报告厅",)


def _clean_title(text: str) -> str:
    value = LEAD_TAG_RE.sub("", text)
    value = TITLE_PREFIX_RE.sub("", value)
    value = TITLE_SUFFIX_RE.sub("", value)
    value = BULLET_RE.sub("", value)
    value = ROOM_TAIL_RE.sub(r"\1", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip(" \t-—:：,，。;；|")


def _trim_deadline(value: str) -> str:
    """最后一道清理：收拾片段抽走后留下的空白与"标签冒号"。

    真正的期限短语已经在 `_taken_spans` 里按**原始行**整段抽掉了（连标点一起，
    见那边的 `eat_separator`）。这里做两件安全的事：

      ① 空白收敛；
      ② 在**首个标签冒号**处截断——`事项名：今天中午12:00前，所有学生完成…`
         抽掉期限后剩成 `事项名 所有学生完成…`，那个冒号就是"事项名"的结束位置，
         冒号后面的整句是对事项的描述，不该塞进标题。

    千万不能再按"前/之后"之类的字去切：那会把「讲座《多模态大模型前沿》」切成
    「讲座《多模态大模型 沿》」（真踩过，测试里专门钉住了这一条）。
    """
    trimmed = re.sub(r"\s{2,}", " ", value).strip(" \t-—:：,，。;；|")
    return trimmed if len(trimmed) >= 2 else value

# ---------------------------------------------------------------------------
# 文本切分
# ---------------------------------------------------------------------------

def parse_inbox_text(
    text: str,
    *,
    default_group: str | None = None,
    default_sender: str | None = None,
    fallback_date: Date | None = None,
    source_ref: str | None = None,
) -> list[NoticeMessage]:
    """把一段粘贴文本切成若干条通知（按 [群名] 头或分隔线）。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    messages: list[NoticeMessage] = []
    current: dict[str, object] | None = None
    preamble: list[str] = []

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        body = "\n".join(current["lines"]).strip()  # type: ignore[arg-type]
        if body:
            messages.append(NoticeMessage(
                text=body,
                group=current["group"],           # type: ignore[arg-type]
                sender=current["sender"],         # type: ignore[arg-type]
                shift_date=current["shift"],      # type: ignore[arg-type]
                source_ref=current["ref"],        # type: ignore[arg-type]
            ))
        current = None

    for index, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        ref = f"{source_ref}#L{index}" if source_ref else None

        if SEPARATOR_RE.match(line):
            flush()
            continue

        header = GROUP_HEADER_RE.match(line)
        if header is not None and header.group("g").strip():
            flush()
            current = {
                "group": header.group("g").strip(),
                "sender": default_sender,
                "shift": fallback_date,
                "lines": [],
                "ref": ref,
            }
            rest = header.group("rest").strip()
            if rest:
                current["lines"].append(rest)  # type: ignore[union-attr]
            continue

        sender = SENDER_HEADER_RE.match(line)
        if sender is not None:
            if current is None:
                current = {"group": default_group, "sender": None, "shift": fallback_date, "lines": [], "ref": ref}
            current["sender"] = sender.group("s").strip()  # type: ignore[index]
            continue

        stamp = TIME_HEADER_RE.match(line)
        if stamp is not None:
            if current is None:
                current = {"group": default_group, "sender": default_sender, "shift": fallback_date, "lines": [], "ref": ref}
            parsed = P.parse_date(stamp.group("t"), fallback_date or Date.today())
            if parsed and parsed.date:
                current["shift"] = datetime.strptime(parsed.date, "%Y-%m-%d").date()  # type: ignore[index]
            continue

        bare_group = BARE_GROUP_HEADER_RE.match(line)
        if bare_group is not None:
            flush()
            current = {
                "group": bare_group.group("g").strip(),
                "sender": default_sender,
                "shift": fallback_date,
                "lines": [],
                "ref": ref,
            }
            continue

        if current is None:
            preamble.append(line)
        else:
            current["lines"].append(line)  # type: ignore[union-attr]

    body_preamble = "\n".join(preamble).strip()
    flush()
    # 只把"像正文"的前言当成一条通知；[群名] 这类标注直接丢弃
    if body_preamble and not ALL_BRACKET_RE.fullmatch(body_preamble) and len(body_preamble) >= 6:
        messages.insert(0, NoticeMessage(
            text=body_preamble,
            group=default_group,
            sender=default_sender,
            shift_date=fallback_date,
            source_ref=source_ref,
        ))
    return messages


# ---------------------------------------------------------------------------
# 单条通知 → 候选
# ---------------------------------------------------------------------------

def _blocks(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts or [text.strip()]


def _first_meaningful(text: str) -> str:
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return text.strip()


def _is_headerish(line: str) -> bool:
    """来源标注行：[计科2301班群]、【实验室群】、群名：xxx、发送人：xxx。

    不要求括号里出现"群"字——实际粘贴时群名五花八门，判定看形态即可。
    """
    stripped = line.strip()
    if re.fullmatch(r"[\[【(（].{1,40}[\]】)）]", stripped):
        return True
    return bool(re.match(r"^\s*(?:群名|群聊|来自|群|发送人|发件人|发言人|管理员)\s*[:：]", stripped))


def _header_label(line: str) -> str | None:
    """从来源标注行里取群名标签（用于给整条通知标注来源群）。"""
    stripped = line.strip()
    bracketed = re.fullmatch(r"[\[【(（]\s*(?P<g>.{1,40}?)\s*[\]】)）]", stripped)
    if bracketed is not None:
        return bracketed.group("g").strip() or None
    labeled = re.match(r"^\s*(?:群名|群聊|来自|群)\s*[:：]\s*(?P<g>.{1,40})\s*$", stripped)
    if labeled is not None:
        return labeled.group("g").strip() or None
    return None


def _find_people(lines: list[str]) -> tuple[str, ...]:
    found: list[str] = []
    for line in lines:
        for match in AT_RE.finditer(line):
            found.append(match.group("v"))
        for match in PEOPLE_LABEL_RE.finditer(line):
            found.append(match.group("v"))
        for match in ROLE_HINT_RE.finditer(line):
            found.append(match.group("v"))
        for match in NAME_VERB_RE.finditer(line):
            found.append(match.group("v"))

    # 先归一化（去掉"老师/同学"等称谓），再按长短去重：
    # "张伟老师" → "张伟"，"张伟" 命中过就不重复出现
    normalized: list[str] = []
    for raw in found:
        name = NAME_ROLE_SUFFIX_RE.sub("", raw.strip(" @：:").strip())
        if not name or len(name) > 12:
            continue
        if any(word in name for word in NAME_STOPWORDS):
            continue
        if not re.search(r"[\u4e00-\u9fa5A-Za-z]", name):
            continue
        if any(name.startswith(verb) or verb.startswith(name) for verb in REQUIREMENT_VERBS):
            continue
        if name not in normalized:
            normalized.append(name)

    ordered: list[str] = []
    for name in normalized:
        # 已被更长的同名前缀覆盖过就跳过（"王强" vs "王强负责签到"）
        if any(other != name and other.startswith(name) for other in normalized):
            continue
        ordered.append(name)
    return tuple(ordered[:8])


def _find_notes(lines: list[str]) -> tuple[str, ...]:
    collected: list[str] = []
    for line in lines:
        match = NOTE_START_RE.search(line)
        if match is not None:
            note = line[match.start():].strip()
            note = re.sub(r"\s{2,}", " ", note)
            if 2 < len(note) <= 120:
                collected.append(note)
    return tuple(dict.fromkeys(collected))


def _is_schedulable(text: str) -> bool:
    if any(keyword in text for keyword in EVENT_KEYWORDS):
        return True
    return False


def _looks_like_chatter(text: str) -> bool:
    if any(keyword in text for keyword in EVENT_KEYWORDS):
        return False
    return any(hint in text for hint in CHATTER_HINTS)


def extract_candidates(message: NoticeMessage) -> list[Candidate]:
    """一条通知 → 零到多条候选（一条通知里常写多件事）。"""
    candidates: list[Candidate] = []
    blocks = _blocks(message.text)
    for block_index, block in enumerate(blocks):
        # 先摘掉来源标注行（[群名]、发送人：…），否则会被当成标题或地点
        cleaned_lines: list[str] = []
        block_group: str | None = None
        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):        # 模板注释当不存在
                continue
            if GROUP_HEADER_RE.match(line) or BARE_GROUP_HEADER_RE.match(line):
                continue
            if block_group is None and _is_headerish(line):
                label = _header_label(line)
                if label:
                    block_group = label
                continue
            if SENDER_HEADER_RE.match(line) and len(cleaned_lines) == 0:
                continue
            cleaned_lines.append(line)
        lines = cleaned_lines
        if not lines:
            continue
        joined = "\n".join(lines)
        source_group = message.group or block_group

        if _looks_like_chatter(joined):
            continue
        has_clock = P.has_explicit_clock(joined)
        schedulable = _is_schedulable(joined)
        if not schedulable and not has_clock:
            continue

        date_parse = P.parse_date(joined, message.shift_date or Date.today())
        if date_parse is None or date_parse.date is None:
            if message.shift_date is None:
                continue
            date_value = message.shift_date.strftime("%Y-%m-%d")
            date_source = "notice-fallback"
        else:
            date_value = date_parse.date
            date_source = date_parse.source or "absolute"

        time_parse = P.parse_time(joined)
        start, end = time_parse.start, time_parse.end
        part_only = start is not None and not has_clock

        location = _find_location(lines)
        people = _find_people(lines)
        notes = list(_find_notes(lines))

        # 标题 = 首个有内容的行，扣掉已被"日期/时间/地点/人名/备注"认领的片段
        content_lines = [line for line in lines if line.strip()]
        title_line = content_lines[0] if content_lines else ""
        fallback_line = title_line
        for line in content_lines:
            if not _is_structural_line(line):
                title_line = fallback_line = line
                break
        taken = _taken_spans(
            title_line,
            date_parse.matched if date_parse else None,
            time_parse.matched,
            location,
            people,
            notes,
        )
        title = _compose_title(title_line, taken)
        if len(title) < 2:
            # 这一行整句都在描述别的字段（例如只有一行「【提醒】」，标签被剥掉就空了）：
            # 往后找第一条真正有内容的行再来一次，别拿半截状语或空标签当标题。
            for line in content_lines[1:]:
                if line is title_line:
                    continue
                taken = _taken_spans(
                    line,
                    date_parse.matched if date_parse else None,
                    time_parse.matched, location, people, notes,
                )
                candidate = _compose_title(line, taken)
                if len(candidate) >= 2:
                    title = candidate
                    break
            else:
                title = _fallback_title(fallback_line)
        if len(title) > 60:
            title = title[:57] + "…"

        # 事件块里除标题/备注外的补充行也进入备注，避免丢掉"需携带材料"这类上下文
        extras: list[str] = []
        for line in lines[1:]:
            if NOTE_START_RE.search(line):
                continue
            cleaned = BULLET_RE.sub("", line).strip()
            if not cleaned:
                continue
            if P.parse_date(cleaned, message.shift_date or Date.today()) and len(cleaned) <= 24:
                continue
            if cleaned == title:
                continue
            if len(cleaned) <= 120:
                extras.append(cleaned)
        notes.extend(extras)

        confidence = 0.85
        if date_source == "notice-fallback":
            confidence -= 0.35
        if part_only:
            confidence -= 0.12
        if not schedulable:
            confidence -= 0.1
        if time_parse.start is None:
            confidence -= 0.1

        candidates.append(Candidate(
            title=title or "（无标题事项）",
            date=date_value,
            start=start,
            end=end,
            location=location,
            people=people,
            notes="；".join(dict.fromkeys(notes)) or None,
            group=source_group,
            sender=message.sender,
            source_ref=message.source_ref,
            date_source=date_source,
            confidence=round(max(0.2, min(1.0, confidence)), 2),
        ))

    return candidates


def extract(text: str, **kwargs: object) -> ParseOutcome:
    """一段 inbox 文本 → ParseOutcome。"""
    messages = parse_inbox_text(text, **kwargs)  # type: ignore[arg-type]
    outcome = ParseOutcome(messages=len(messages))
    for message in messages:
        found = extract_candidates(message)
        if not found:
            outcome.skipped += 1
            preview = message.text.strip().split("\n")[0][:40]
            outcome.diagnostics.append(f"跳过（未识别为日程）: {preview}")
        outcome.candidates.extend(found)
    return outcome
