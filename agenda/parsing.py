"""中文日期 / 时段表达式的确定性解析。

原则：宁可标"待确认"，不要猜错。所有兜底推断都会在 Event.date_source 里留痕，
面板据此显示"待确认"，用户可以一眼看出哪条需要核。

支持范围（覆盖 QQ 群通知里最常见的写法）：
  日期  今天/明天/后天/大后天、这周五/本周五/下周一、周五、周末、
        9月22日、9/22、2026-09-22、20260922
  时段  20:00-21:30、下午3点—5点、14点到15:30、晚上8点半、
        上午9点、中午、傍晚、晚上（只给半天时按半天默认时段）
  半日  凌晨/早上/上午/中午/下午/傍晚/晚上/深夜
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date, timedelta

# ---------------------------------------------------------------------------
# 基础常量
# ---------------------------------------------------------------------------

WEEKDAY_CN = "一二三四五六日"
# 中文的"周几"→ Python weekday（周一=0）
CN_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 半日 → (默认开始, 默认结束, 可用于"3点"这类裸小时的最小值)
DAY_PARTS: dict[str, tuple[int, int, int]] = {
    "凌晨": (0, 5, 0),
    "早上": (7, 9, 0),
    "早晨": (7, 9, 0),
    "上午": (8, 11, 0),
    "中午": (12, 13, 10),
    "午后": (13, 16, 12),
    "下午": (14, 17, 12),
    "傍晚": (17, 18, 16),
    "晚上": (19, 21, 16),
    "今晚": (19, 21, 16),
    "夜里": (20, 22, 16),
    "深夜": (22, 23, 16),
}
# 长词优先，避免"晚上"被"晚"抢先
DAY_PART_KEYS = sorted(DAY_PARTS, key=len, reverse=True)

DATE_WORD_RE = re.compile(
    r"(今天|今日|明天|明日|后天|后日|大后天|"
    r"(?:这|本|下下|下|上)?(?:周|星期|礼拜)[一二三四五六日天]|"
    r"下周|本周|这周|"
    r"(?:这|本|下下|下)?周末|"
    r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?|"
    r"\d{4}\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}\s*日?|"
    r"(?<![月日\d:：])\d{1,2}\s*[-/]\s*\d{1,2}\s*[日号]?|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]?|"
    r"(?<!\d)20\d{6}(?!\d))"
)

# 统一时段识别：两侧各自可带半日词，也允许跨天标注（次日上午9点）
_HOUR = r"\d{1,2}|[一二三四五六七八九十两]+"
_PART = r"(?:凌晨|早上|早晨|上午|中午|午后|下午|傍晚|晚上|今晚|夜里|深夜|次日)?"
TIME_FIND_RE = re.compile(
    rf"(?P<part1>{_PART})\s*(?P<a_h>{_HOUR})\s*(?::|：|点)\s*(?P<a_m>\d{{1,2}}|半|一刻|三刻)?"
    rf"\s*(?:-|–|—|~|～|至|到)\s*"
    rf"(?P<part2>{_PART})\s*(?P<b_h>{_HOUR})\s*(?::|：|点)\s*(?P<b_m>\d{{1,2}}|半|一刻|三刻)?"
    rf"|(?P<part3>{_PART})\s*(?P<s_h>{_HOUR})\s*(?::|：|点)\s*(?P<s_m>\d{{1,2}}|半|一刻|三刻)?"
)
TIME_RANGE_RE = re.compile(
    r"(?P<a_h>\d{1,2})\s*[:：]\s*(?P<a_m>\d{1,2})?"
    r"\s*(?:-|–|—|~|～|至|到)\s*"
    r"(?P<b_h>\d{1,2})\s*[:：]\s*(?P<b_m>\d{1,2})?"
)
TIME_RANGE_CN_RE = re.compile(
    r"(?P<a_h>\d{1,2}|[一二三四五六七八九十两]+)\s*点\s*(?P<a_m>半|一刻|三刻)?\s*"
    r"(?:-|–|—|~|～|至|到)\s*"
    r"(?P<b_h>\d{1,2}|[一二三四五六七八九十两]+)\s*点\s*(?P<b_m>半|一刻|三刻)?"
)
TIME_ONE_RE = re.compile(
    r"(?P<h>\d{1,2})\s*[:：]\s*(?P<m>\d{2})"
)
TIME_CN_RE = re.compile(
    r"(?P<h>\d{1,2}|[一二三四五六七八九十两]+)\s*点\s*(?P<m>半|一刻|三刻|\d{1,2}分?)?"
)

DATE_LOOSE_RE = re.compile(r"\d{4}\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}")
DATE_CN_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?")
DATE_COMPACT_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")

#: 日期区间的分隔符。**变体必须都收**：用户原文用的是 U+2011（非断行连字符），
#: 只认 ASCII `-` 的话"9月25日‑28日"整个区间都识别不出来。
RANGE_SEP = r"(?:--|[-‑–—－~～至到])"
#: 日期区间：`9月25日-28日`、`9月25日至9月28日`、`9月25日～28日`、`9月25日—28日`。
DATE_RANGE_RE = re.compile(
    rf"(?P<a>(?:\d{{4}}\s*年\s*)?\d{{1,2}}\s*月\s*\d{{1,2}}\s*[日号]?)"
    rf"\s*{RANGE_SEP}\s*"
    rf"(?P<b>(?:\d{{4}}\s*年\s*)?(?:\d{{1,2}}\s*月\s*)?\d{{1,2}}\s*[日号]?)"
)
#: 区间后面常跟「期间/之间/内」，一并认掉（标题里不该残留"期间"）
RANGE_TAIL_RE = re.compile(r"^\s*(?:期间|之间|之内|内|前)\s*")


@dataclass
class DateParse:
    date: str | None            # YYYY-MM-DD
    source: str | None          # absolute / relative / weekday
    matched: str | None = None  # 命中的原文片段，便于调试


@dataclass
class TimeParse:
    start: str | None
    end: str | None
    matched: str | None = None
    part_hint: str | None = None   # 命中的半日词


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _cn_number(token: str) -> int | None:
    """中文数字 → int（支持 一~十二）。"""
    if token.isdigit():
        return int(token)
    if token in CN_NUM:
        return CN_NUM[token]
    if token == "十":
        return 10
    if token.startswith("十") and len(token) == 2:
        return 10 + CN_NUM.get(token[1], 0)
    if token.endswith("十") and len(token) == 2:
        return CN_NUM.get(token[0], 0) * 10
    if len(token) == 3 and token[1] == "十":
        return CN_NUM.get(token[0], 0) * 10 + CN_NUM.get(token[2], 0)
    return None


def _fmt_date(value: Date) -> str:
    return value.strftime("%Y-%m-%d")


def _fmt_time(hour: int, minute: int = 0) -> str:
    return f"{hour:02d}:{minute:02d}"


def _clamp_hour(hour: int, minimum: int) -> int:
    """把 1~11 点按半日词矫正到 13~23，如 下午3点 → 15:00。"""
    if minimum and 0 < hour < minimum:
        return hour + 12
    return hour


# ---------------------------------------------------------------------------
# 日期解析
# ---------------------------------------------------------------------------

def parse_date(text: str, notice_date: Date | None = None) -> DateParse | None:
    """从文本里找第一个日期表达式。找不到返回 None。"""
    match = DATE_WORD_RE.search(text)
    if match is None:
        return None
    token = match.group(0)
    base = notice_date or Date.today()

    compact = DATE_COMPACT_RE.fullmatch(token.strip())
    if compact is not None:
        try:
            return DateParse(_fmt_date(Date(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))),
                             "absolute", token)
        except ValueError:
            return None

    # 2026年10月8日
    cn_year = DATE_CN_RE.search(token)
    if cn_year is not None:
        digits = [int(part) for part in re.findall(r"\d+", cn_year.group(0))]
        if len(digits) >= 3:
            try:
                return DateParse(_fmt_date(Date(digits[0], digits[1], digits[2])), "absolute", token)
            except ValueError:
                return None

    loose = DATE_LOOSE_RE.search(token)
    if loose is not None:
        parts = re.split(r"[-/]", loose.group(0))
        try:
            return DateParse(_fmt_date(Date(int(parts[0]), int(parts[1]), int(re.sub(r"\D", "", parts[2])))),
                             "absolute", token)
        except (ValueError, IndexError):
            return None

    # 9/22 或 9-22（不带年份，按通知年份，若已过 30 天则顺延次年）
    short = re.fullmatch(r"\s*(\d{1,2})\s*[-/]\s*(\d{1,2})\s*[日号]?\s*", token)
    if short is not None:
        month, day = int(short.group(1)), int(short.group(2))
        year = base.year
        try:
            value = Date(year, month, day)
        except ValueError:
            return None
        if (value - base).days < -30:
            try:
                value = Date(year + 1, month, day)
            except ValueError:
                return None
        return DateParse(_fmt_date(value), "absolute", token)

    # 9月22日 / 9月22号
    cn_date = re.fullmatch(r"\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?\s*", token)
    if cn_date is not None:
        month, day = int(cn_date.group(1)), int(cn_date.group(2))
        year = base.year
        try:
            value = Date(year, month, day)
        except ValueError:
            return None
        if (value - base).days < -30:
            try:
                value = Date(year + 1, month, day)
            except ValueError:
                return None
        return DateParse(_fmt_date(value), "absolute", token)

    if token in {"今天", "今日"}:
        return DateParse(_fmt_date(base), "relative", token)
    if token in {"明天", "明日"}:
        return DateParse(_fmt_date(base + timedelta(days=1)), "relative", token)
    if token in {"后天", "后日"}:
        return DateParse(_fmt_date(base + timedelta(days=2)), "relative", token)
    if token == "大后天":
        return DateParse(_fmt_date(base + timedelta(days=3)), "relative", token)

    if token in {"下周", "下星期", "下礼拜"}:
        return DateParse(_fmt_date(base + timedelta(days=7)), "relative", token)
    if token in {"本周", "这周", "这星期"}:
        return DateParse(_fmt_date(base), "relative", token)

    weekend = re.fullmatch(r"(这|本|下下|下)?\s*周末", token)
    if weekend is not None:
        prefix = weekend.group(1) or "这"
        offset_weeks = {"这": 0, "本": 0, "下": 1, "下下": 2}[prefix]
        # 周末默认取周六
        days_ahead = (5 - base.weekday()) % 7 + offset_weeks * 7
        if offset_weeks == 0 and days_ahead == 0 and base.weekday() == 6:
            days_ahead = 0 if base.weekday() in (5, 6) else days_ahead
        value = base + timedelta(days=days_ahead)
        if value.weekday() == 6 and offset_weeks == 0:
            value = value - timedelta(days=1)
        return DateParse(_fmt_date(value), "weekday", token)

    weekday = re.fullmatch(r"(这|本|下下|下|上)?\s*(?:周|星期|礼拜)([一二三四五六日天])", token)
    if weekday is not None:
        prefix = weekday.group(1) or ""
        target = CN_DIGIT[weekday.group(2)] - 1  # → Python weekday
        if prefix == "上":
            delta = target - base.weekday() - 7
            return DateParse(_fmt_date(base + timedelta(days=delta)), "weekday", token)
        delta = (target - base.weekday()) % 7
        if prefix == "" and delta == 0:
            # 裸"周五"若就是今天，视为本周已到，指下一个周五
            delta = 7
        elif prefix == "下":
            # 约定：下X = 下一个自然周的星期X（下周一从周一起算 +7 天）
            delta += 7
        elif prefix == "下下":
            delta += 14
        return DateParse(_fmt_date(base + timedelta(days=delta)), "weekday", token)

    return None


# ---------------------------------------------------------------------------
# 时段解析
# ---------------------------------------------------------------------------

def _find_day_part(text: str) -> tuple[str | None, int | None]:
    """返回 (半日词, 该半日的最小小时数)。"""
    for key in DAY_PART_KEYS:
        index = text.find(key)
        if index >= 0:
            return key, DAY_PARTS[key][2]
    return None, None


def parse_date_range(text: str, notice_date: Date | None = None) -> tuple[Date, Date, str] | None:
    """识别"9月25日-28日"这类**日期区间**，返回 (起, 止, 命中的原文)。

    为什么需要它：班级群里极常见的写法是"X月X日-X日期间，每日11:00前…"。
    只认单个日期的话，这种通知要么被丢掉、要么只落在第一天，
    而用户真正需要的是**区间内每一天**都提醒（见 `extract` 里的"每日"展开）。

    右半边可以省略月份（`9月25日-28日` → 用左半边的月份），也能跨月（`9月28日-10月2日`）。
    右半边早于左半边时按"跨月"处理（`12月28日-2日` → 次年？不猜年份，只把月份 +1）。
    """
    match = DATE_RANGE_RE.search(text or "")
    if match is None:
        return None
    base = notice_date or Date.today()
    left = parse_date(match.group("a"), base)
    if left is None or left.date is None:
        return None
    start = Date.fromisoformat(left.date)

    right_text = match.group("b")
    # 右半边没写月份时补上左半边的月份，否则 parse_date 会把它当成"日"而认不出来
    if not re.search(r"\d{1,2}\s*月", right_text):
        digits = re.findall(r"\d{1,2}", right_text)
        if not digits:
            return None
        right_text = f"{start.month}月{digits[-1]}日"
    right = parse_date(right_text, base)
    if right is None or right.date is None:
        return None
    end = Date.fromisoformat(right.date)
    if end < start:
        # 跨月：右半边补一个月（12月28日-2日 → 1月2日）
        month = end.month + 1
        year = end.year + (1 if month > 12 else 0)
        month = 1 if month > 12 else month
        try:
            end = Date(year, month, end.day)
        except ValueError:
            return None
    if end < start:
        return None
    return start, end, match.group(0)


def parse_time(text: str) -> TimeParse:
    """解析时段；找不到具体钟点则按半日词给默认窗口。

    半日词的作用域：只影响自己那一侧。"上午9点到晚上6点" → 09:00–18:00。
    文本里没有半日词时，用整句最靠前的半日词做兜底（"下午的会3点开始"）。
    """
    part, minimum = _find_day_part(text)

    for match in TIME_FIND_RE.finditer(text):
        groups = match.groupdict()
        if groups.get("a_h") is not None and groups.get("b_h") is not None:
            a_h = _cn_number(groups["a_h"])
            b_h = _cn_number(groups["b_h"])
            if a_h is None or b_h is None or a_h > 23 or b_h > 24:
                continue
            a_m = _minute(groups.get("a_m"))
            b_m = _minute(groups.get("b_m"))
            if a_m is None or b_m is None:
                continue
            a_min = DAY_PARTS[groups["part1"]][2] if groups.get("part1") else minimum
            b_min = DAY_PARTS[groups["part2"]][2] if groups.get("part2") else minimum
            if a_min:
                a_h = _clamp_hour(a_h, a_min)
            if b_min:
                b_h = _clamp_hour(b_h, b_min)
            if a_h == 24:
                a_h, a_m = 23, 59
            if b_h == 24:
                b_h, b_m = 23, 59
            if (b_h, b_m) <= (a_h, a_m):
                if b_h + 12 <= 23 and b_h + 12 > a_h:
                    # 跨半日写法（下午3点到5点）里结束侧没带半日词时补 12 小时
                    b_h += 12
                else:
                    continue
            return TimeParse(_fmt_time(a_h, a_m), _fmt_time(b_h, b_m), match.group(0), part)

        if groups.get("s_h") is not None:
            hour = _cn_number(groups["s_h"])
            minute = _minute(groups.get("s_m"))
            if hour is None or minute is None or hour > 24:
                continue
            side_min = DAY_PARTS[groups["part3"]][2] if groups.get("part3") else minimum
            if side_min:
                hour = _clamp_hour(hour, side_min)
            if hour == 24:
                hour, minute = 23, 59
            return TimeParse(_fmt_time(hour, minute), None, match.group(0), part)

    # 只有半日词：给默认窗口（置信度由调用方下调）
    if part is not None:
        start, end, _ = DAY_PARTS[part]
        return TimeParse(_fmt_time(start), _fmt_time(end), part, part)

    return TimeParse(None, None, None, None)


def _minute(token: str | None) -> int | None:
    if token is None or token == "":
        return 0
    token = token.strip()
    if token in {"半"}:
        return 30
    if token in {"一刻"}:
        return 15
    if token in {"三刻"}:
        return 45
    digits = re.sub(r"\D", "", token)
    if digits == "":
        return 0
    value = int(digits)
    return value if value <= 59 else None


def has_explicit_clock(text: str) -> bool:
    """文本里是否出现了具体钟点（用于决定给不给半日默认窗口）。"""
    return TIME_FIND_RE.search(text) is not None
