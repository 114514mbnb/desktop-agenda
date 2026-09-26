"""从放假通知原文里识别"放假区间 + 调休上班/补课日"。

学校发的通知一般长这样（国务院版本和学校版本大同小异）：

    各单位：根据国务院办公厅通知，现将 2026 年国庆节放假安排通知如下：
    10月1日至10月8日放假调休，共8天。10月10日（星期六）上班，
    补10月7日（星期三）的课。

要抠出来的是：
  * 假期：10-01 ~ 10-08（"X月X日至X月X日放假"）
  * 调休：10-10 上班，补 10-07 的课（"X月X日（星期X）上班，补X月X日的课"）

**不猜**：句子没说"补哪天的课"（国务院的通知就只写"上班"，不写补哪天），
就把 source 留空、在 warnings 里说清楚让用户自己填 —— 猜错一天，学生就白跑一趟。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as Date

from .holidays import Holiday, Makeup, parse_date, format_date, friendly

#: 一句话里的日期（含"至/到/-"区间）
_DATE = r"\d{1,2}\s*月\s*\d{1,2}\s*日"
_RANGE = rf"({_DATE})\s*(?:至|到|—|–|－|~|～|-)\s*({_DATE})"
_SINGLE = rf"({_DATE})"

#: 判断一句话在说什么
_HOLIDAY_WORDS = ("放假", "休假", "休息", "放假调休", "假期")
_WORK_WORDS = ("上班", "上课", "补课", "正常上班", "调休上班")
_MAKEUP_WORDS = ("补", "上周", "补上")

#: 从"补10月7日（星期三）的课"里抠出补课日期
_MAKEUP_SOURCE = re.compile(rf"补\s*({_DATE})\s*(?:（[^）]*）|\([^)]*\))?\s*(?:的)?\s*(?:课|班)?")


@dataclass
class HolidayParseResult:
    holidays: list[Holiday] = field(default_factory=list)
    makeups: list[Makeup] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.holidays) + len(self.makeups)

    def summary(self) -> str:
        lines = [f"假期 {len(self.holidays)} 段、调休 {len(self.makeups)} 天"]
        for holiday in self.holidays:
            lines.append(f"　· 放假日 {holiday.label()}")
        for makeup in self.makeups:
            lines.append(f"　· 调休 {format_date(makeup.date)} 上班/上课"
                         + (f"，补 {friendly(makeup.source)}" if makeup.source else "（没说补哪天的课，请自己补上）"))
        if self.warnings:
            lines.append("提醒：")
            lines.extend(f"　· {line}" for line in self.warnings)
        return "\n".join(lines)

#: 假期名字：先认已知节日名，再兜底认"XX节/XX假期/XX放假"。
#: 踩过的坑：一开始写的是 `[\u4e00-\u9fa5]{2,6}节|[春节|元旦|...]+`，
#: 那个字符类是"单个汉字"的集合，`search` 找到最左边能匹配的位置就返回，
#: 于是"根据国务院办公厅通知…"里的"国"被当成了假期名（实测 name='国'）。
_NAME_PATTERN = re.compile(r"([\u4e00-\u9fa5]{2,4})(?:节|假期)")
_NAME_BEFORE_HOLIDAY = re.compile(r"([\u4e00-\u9fa5]{2,4})(?:放假|休假)")


def _guess_name(sentence: str) -> str:
    from .festival import TEMPLATES
    for name in sorted((item.name for item in TEMPLATES), key=len, reverse=True):
        if name in sentence:
            return name
    match = _NAME_PATTERN.search(sentence)
    if match:
        return match.group(0)
    match = _NAME_BEFORE_HOLIDAY.search(sentence)
    if match:
        return f"{match.group(1)}假期"
    return "假期"


def _year_for(month: int, default_year: int) -> int:
    """通知里的年份：多数通知只写月日。1~2 月按"下一年"理解（元旦/春节通知）。"""
    today = Date.today()
    if default_year:
        return default_year
    if month <= 2 and today.month >= 9:
        return today.year + 1
    return today.year


def parse_holiday_notice(text: str, *, default_year: int | None = None,
                         year: int | None = None) -> HolidayParseResult:
    """解析一段通知文本。`year`/`default_year` 指定没写年份时按哪一年算。"""
    result = HolidayParseResult()
    raw = str(text or "")
    if not raw.strip():
        result.warnings.append("没有内容")
        return result

    explicit_year = None
    year_match = re.search(r"(20\d{2})\s*年", raw)
    if year_match:
        explicit_year = int(year_match.group(1))
    fallback = year or default_year or explicit_year or Date.today().year

    seen_holidays: set[tuple[str, str]] = set()
    seen_makeups: set[str] = set()

    # 按句子切：中文句号/分号/换行/感叹号
    sentences = [part for part in re.split(r"[。！？；;\n\r]+", raw) if part.strip()]
    for sentence in sentences:
        name = _guess_name(sentence)
        is_holiday = any(word in sentence for word in _HOLIDAY_WORDS)
        is_work = any(word in sentence for word in _WORK_WORDS)
        # 1) 区间放假：10月1日至10月8日放假
        for match in re.finditer(_RANGE, sentence):
            if not is_holiday:
                continue
            first = _date_of(match.group(1), fallback, explicit_year)
            second = _date_of(match.group(2), first.year if first else fallback, explicit_year)
            if first is None or second is None:
                continue
            if second < first:
                first, second = second, first
            key = (format_date(first), format_date(second))
            if key in seen_holidays:
                continue
            seen_holidays.add(key)
            result.holidays.append(Holiday(name=name, start=first, end=second,
                                           note="从放假通知识别"))
        # 2) 单天放假：1月1日放假1天
        if is_holiday and not re.search(_RANGE, sentence):
            for match in re.finditer(_SINGLE, sentence):
                day = _date_of(match.group(1), fallback, explicit_year)
                if day is None:
                    continue
                key = (format_date(day), format_date(day))
                if key in seen_holidays:
                    continue
                seen_holidays.add(key)
                result.holidays.append(Holiday(name=name, start=day, end=day,
                                               note="从放假通知识别"))
        # 3) 调休上班/补课：10月10日（星期六）上班，补10月7日的课
        if is_work:
            source_match = _MAKEUP_SOURCE.search(sentence)
            source = None
            if source_match and any(word in sentence for word in _MAKEUP_WORDS):
                source = _date_of(source_match.group(1), fallback, explicit_year)
            for match in re.finditer(_SINGLE, sentence):
                day = _date_of(match.group(1), fallback, explicit_year)
                if day is None:
                    continue
                if source is not None and day == source:
                    continue
                if format_date(day) in seen_makeups:
                    continue
                seen_makeups.add(format_date(day))
                result.makeups.append(Makeup(date=day, source=source))
            if source is None and re.search(_SINGLE, sentence):
                result.warnings.append(
                    f"「{sentence.strip()[:24]}…」只说了上班/上课，没说补哪天的课，"
                    "调休表里那几天的「补哪天的课」要自己选。"
                )

    if not result.holidays and not result.makeups:
        result.warnings.append(
            "没认出放假或调休的句子。至少要有一句带「放假」或「上班/补课」、"
            "并且写了「X月X日」的话。"
        )
    return result


def _date_of(text: str, fallback_year: int, explicit_year: int | None) -> Date | None:
    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", str(text or ""))
    if match is None:
        return parse_date(text, default_year=fallback_year)
    month, day = int(match.group(1)), int(match.group(2))
    year = explicit_year or _year_for(month, fallback_year)
    try:
        return Date(year, month, day)
    except ValueError:
        return None


def merge_into(calendar, result: HolidayParseResult) -> int:
    """把识别结果并进 Calendar（同名同起始的假期合并，同一天的调休覆盖）。"""
    added = 0
    for holiday in result.holidays:
        calendar.add_holiday(holiday)
        added += 1
    for makeup in result.makeups:
        calendar.add_makeup(makeup)
        added += 1
    return added
