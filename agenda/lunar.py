"""农历与节气：把 vendored 的 `lunardate` 包成项目自己的几个函数。

为什么单独一层：
  * 上游 API（`LunarDate` / `TermUtils`）风格跟本项目不一致，而且到处直接用会让
    换库变成全局手术；这里只暴露"我真正需要的那四个日期"。
  * 上游只覆盖 1900–2100，超出范围要**安静地返回 None**，而不是抛异常把面板打挂。
  * 节日彩蛋要的是"某一年的春节/元宵/端午/中秋/清明分别是哪天"，就这四个。

覆盖范围：农历 1900–2100 年（公历 1900-01-31 ~ 2101-01-28），来自 vendored 的
borax `lunardate`（MIT）。换库或升版本见 `agenda/vendor/__init__.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta

from .vendor import lunardate as _lunar

#: 支持的公历范围（上游给的边界，含首尾）
FIRST_SOLAR: Date = _lunar.MIN_SOLAR_DATE
LAST_SOLAR: Date = _lunar.MAX_SOLAR_DATE

#: 支持的农历年份
FIRST_LUNAR_YEAR: int = _lunar.MIN_LUNAR_YEAR
LAST_LUNAR_YEAR: int = _lunar.MAX_LUNAR_YEAR

#: 农历节日 → (月, 日)
LUNAR_FESTIVALS: dict[str, tuple[int, int]] = {
    "spring": (1, 1),       # 春节（正月初一）
    "lantern": (1, 15),     # 元宵节（正月十五）
    "dragon": (5, 5),       # 端午节（五月初五）
    "midautumn": (8, 15),   # 中秋节（八月十五）
}

#: 节气名（上游 TermUtils 里的名字）
QINGMING_TERM = "清明"


@dataclass(frozen=True)
class LunarDateInfo:
    """某一天对应的农历信息。"""

    year: int
    month: int
    day: int
    leap: bool = False

    def text(self) -> str:
        """「八月十五」这种人话写法。"""
        from .holidays import friendly  # 只在需要时引，避免循环依赖

        names = "〇正二三四五六七八九十冬腊"
        month = ("闰" if self.leap else "") + (names[self.month] if 0 < self.month < len(names) else str(self.month))
        return f"{month}月{_day_text(self.day)}"


def _day_text(day: int) -> str:
    tens = "初十廿卅"
    units = "日一二三四五六七八九十"
    if day == 10:
        return "初十"
    if day == 20:
        return "二十"
    if day == 30:
        return "三十"
    if day < 10:
        return "初" + units[day]
    if day < 20:
        return "十" + units[day - 10]
    if day < 30:
        return "廿" + units[day - 20]
    return "卅" + units[day - 30]


def supported(day: Date) -> bool:
    return FIRST_SOLAR <= day <= LAST_SOLAR


def lunar_to_solar(year: int, month: int, day: int, *, leap: bool = False) -> Date | None:
    """农历 (年, 月, 日) → 公历日期；超出支持范围返回 None。"""
    if not FIRST_LUNAR_YEAR <= year <= LAST_LUNAR_YEAR:
        return None
    try:
        return _lunar.LunarDate(year, month, day, 1 if leap else 0).to_solar_date()
    except Exception:
        # 农历里不存在的日期（比如那一年没有闰月、某月只有 29 天）
        return None


def solar_to_lunar(day: Date) -> LunarDateInfo | None:
    """公历日期 → 农历；超出支持范围返回 None。"""
    if not supported(day):
        return None
    try:
        got = _lunar.LunarDate.from_solar_date(day.year, day.month, day.day)
    except Exception:
        return None
    return LunarDateInfo(year=got.year, month=got.month, day=got.day, leap=bool(got.leap))


def new_year(year: int) -> Date | None:
    """春节（正月初一）。"""
    return lunar_to_solar(year, 1, 1)


def lantern_festival(year: int) -> Date | None:
    """元宵节（正月十五）。"""
    return lunar_to_solar(year, 1, 15)


def dragon_boat(year: int) -> Date | None:
    """端午节（五月初五）。"""
    return lunar_to_solar(year, 5, 5)


def mid_autumn(year: int) -> Date | None:
    """中秋节（八月十五）。"""
    return lunar_to_solar(year, 8, 15)


def qingming(year: int) -> Date | None:
    """清明（节气，按上游的节气表算）。"""
    if not FIRST_LUNAR_YEAR <= year <= LAST_LUNAR_YEAR:
        return None
    try:
        return _lunar.TermUtils.nth_term_day(year, term_name=QINGMING_TERM)
    except Exception:
        return None


def festival_date(year: int, key: str) -> Date | None:
    """按节日 key 取那一年的日期（`key` 见 LUNAR_FESTIVALS，另加 newyear/qingming）。"""
    if key == "newyear":
        return Date(year, 1, 1)
    if key == "labor":
        return Date(year, 5, 1)
    if key == "national":
        return Date(year, 10, 1)
    if key == "qingming":
        return qingming(year)
    month_day = LUNAR_FESTIVALS.get(key)
    if month_day is None:
        return None
    return lunar_to_solar(year, month_day[0], month_day[1])


def next_occurrence(key: str, after: Date) -> Date | None:
    """`after` 之后（含当天）这个节日的下一次日期。"""
    for year in (after.year, after.year + 1, after.year + 2):
        day = festival_date(year, key)
        if day is not None and day >= after:
            return day
    return None


def lunar_age_text(day: Date) -> str:
    """「八月十五」；取不到就返回空串（界面不显示）。"""
    info = solar_to_lunar(day)
    return info.text() if info is not None else ""


def nearby_lunar_days(day: Date, radius: int = 1) -> list[tuple[Date, LunarDateInfo]]:
    """`day` 前后各几天对应的农历（个别节日按"农历月日"匹配时用得上）。"""
    found: list[tuple[Date, LunarDateInfo]] = []
    for offset in range(-radius, radius + 1):
        current = day + timedelta(days=offset)
        info = solar_to_lunar(current)
        if info is not None:
            found.append((current, info))
    return found
