"""法定假期 / 学校放假安排 + 调休。

用户的原始需求：
  * "增加一个假期时间以及调休修改功能，我可以自定义时间假期时间"
  * "处于假期时间，日程表不显示课程表内容，但是显示群聊通知信息"
  * "调休功能可以将任意时间段的日程安排改为调休日的任务，
     在我给的日期旁边增加小字（调休X月X日日程）"

所以这里有两件事，语义完全不同：
  * **假期（Holiday）**：一段日期区间，期内**不上课**（课表整段压掉），
    但群通知照常显示 —— 放假期间老师照样在群里发通知。
  * **调休（Makeup）**：某一天"补哪一天的课"。`date` 是要上课的那天
    （通常是周末），`source` 是被放假挤掉的那天。渲染时把 source 那天的
    课程原样搬到 date 上，并在日期旁边写一行小字"调休10月7日日程"。

存储是明文 JSON（`data/calendar.json`），跟其它数据文件一个风格，方便手改：

    {
      "holidays": [{"name": "国庆节", "start": "2026-10-01", "end": "2026-10-08", "note": ""}],
      "makeups":  [{"date": "2026-10-10", "source": "2026-10-07", "label": ""}]
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import timedelta
from pathlib import Path

CALENDAR_NAME = "calendar.json"

#: 假期结束后还会"收尾"几天：这几天里面板显示一个按天数的收心倒计时。
#: 需求原话：「自定义假期结束后自动结束（假期后三天设计一个小倒计时，按天数倒计时）」
WRAP_UP_DAYS = 3

#: "10月1日" / "2026-10-01" / "2026/10/1" / "10-01" / "1001" 都收
_DATE_PATTERNS = (
    re.compile(r"(?P<y>\d{4})\s*[-/.年]\s*(?P<m>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})\s*日?"),
    re.compile(r"(?P<m>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})\s*日?"),
)


def parse_date(text: str | Date | None, *, default_year: int | None = None) -> Date | None:
    """把一串人写的日期解析成 date；认不出来返回 None。"""
    if isinstance(text, Date):
        return text
    raw = str(text or "").strip()
    if not raw:
        return None
    raw = raw.replace("　", " ").strip()
    for pattern in _DATE_PATTERNS:
        match = pattern.fullmatch(raw)
        if match is None:
            continue
        groups = match.groupdict()
        year = int(groups.get("y") or default_year or Date.today().year)
        month, day = int(groups["m"]), int(groups["d"])
        try:
            return Date(year, month, day)
        except ValueError:
            return None
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", raw)
    if compact:
        try:
            return Date(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))
        except ValueError:
            return None
    return None


def parse_date_range(text: str, *, default_year: int | None = None) -> tuple[Date, Date] | None:
    """解析 "10月1日-10月8日" / "2026-10-01~2026-10-08" 这类区间。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    parts = re.split(r"\s*(?:至|到|—|–|－|~|～|—)\s*|\s+-\s+", raw)
    if len(parts) < 2:
        parts = re.split(r"(?<=日)\s*-\s*", raw)
    parts = [part for part in (p.strip() for p in parts) if part]
    if not parts:
        return None
    start = parse_date(parts[0], default_year=default_year)
    if start is None:
        return None
    if len(parts) == 1:
        return start, start
    # 结束那一半可能只写"8日"（没有月份），按开始月份补
    tail = parts[-1]
    end = parse_date(tail, default_year=default_year)
    if end is None:
        single = re.fullmatch(r"(\d{1,2})\s*日?", tail)
        if single:
            try:
                end = start.replace(day=int(single.group(1)))
            except ValueError:
                return None
    if end is None:
        return start, start
    if end < start:
        start, end = end, start
    return start, end


def format_date(day: Date) -> str:
    return day.strftime("%Y-%m-%d")


def friendly(day: Date) -> str:
    """「10月7日」这种人话写法（小字标签、界面提示都用它）。"""
    return f"{day.month}月{day.day}日"


@dataclass(frozen=True)
class Holiday:
    """一段放假区间（含首尾两天）。"""

    name: str
    start: Date
    end: Date
    note: str = ""

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def covers(self, day: Date) -> bool:
        return self.start <= day <= self.end

    def label(self) -> str:
        if self.start == self.end:
            return f"{self.name}（{friendly(self.start)}）"
        return f"{self.name}（{friendly(self.start)} 至 {friendly(self.end)}，共 {self.days} 天）"

    def to_payload(self) -> dict:
        payload = {"name": self.name, "start": format_date(self.start), "end": format_date(self.end)}
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass(frozen=True)
class Makeup:
    """调休：`date` 当日补 `source` 当日的课程（source 为空表示通知未写明补课日期）。"""

    date: Date
    source: Date | None = None
    label: str = ""

    def text(self) -> str:
        """日期旁边那行小字：默认「调休10月7日日程」。"""
        if self.label:
            return self.label
        if self.source is None:
            return "调休"
        return f"调休{friendly(self.source)}日程"

    def to_payload(self) -> dict:
        payload = {"date": format_date(self.date)}
        if self.source is not None:
            payload["source"] = format_date(self.source)
        if self.label:
            payload["label"] = self.label
        return payload


@dataclass(frozen=True)
class HolidayWrapUp:
    """假期刚结束那几天的收尾状态（面板用它显示按天数的收心倒计时）。"""

    holiday: "Holiday"
    days_after: int      # 假期结束后第几天：1、2、3
    remaining: int       # 倒计时还剩几天：3、2、1

    def short(self) -> str:
        """一行小字：`假期已结束 · 收心倒计时 2 天`。"""
        return f"{self.holiday.name}假期已结束 · 收心倒计时 {self.remaining} 天"

    def detail(self) -> str:
        return (f"{self.holiday.name}假期已于 {friendly(self.holiday.end)} 结束，"
                f"今天是第 {self.days_after} 天，倒计时还剩 {self.remaining} 天")


@dataclass
class Calendar:
    """假期 + 调休的集合（`agenda` 里到处传它）。"""

    holidays: list[Holiday] = field(default_factory=list)
    makeups: list[Makeup] = field(default_factory=list)

    # -- 查询 ------------------------------------------------------------
    def holiday_on(self, day: Date) -> Holiday | None:
        for holiday in self.holidays:
            if holiday.covers(day):
                return holiday
        return None

    def is_holiday(self, day: Date) -> bool:
        return self.holiday_on(day) is not None

    def holiday_days_left(self, day: Date) -> int:
        """假期还剩几天（含当天）：最后一天是 1；不在假期里是 0。"""
        holiday = self.holiday_on(day)
        if holiday is None:
            return 0
        return (holiday.end - day).days + 1

    def wrap_up(self, day: Date) -> "HolidayWrapUp | None":
        """假期刚结束那几天（默认 3 天）的收尾信息；过了这段就返回 None。

        为什么要有"收尾"：假期一结束就把所有痕迹抹掉，用户（和假期里的作息）
        都还停在放假模式；这三天用一个按天数的倒计时把状态收回来。
        """
        best: HolidayWrapUp | None = None
        for holiday in self.holidays:
            if day <= holiday.end:
                continue
            days_after = (day - holiday.end).days
            if days_after > WRAP_UP_DAYS:
                continue
            candidate = HolidayWrapUp(holiday=holiday, days_after=days_after,
                                      remaining=WRAP_UP_DAYS - days_after + 1)
            if best is None or candidate.days_after < best.days_after:
                best = candidate
        return best

    def holiday_status(self, holiday: Holiday, day: Date) -> str:
        """给控制台的"状态"列：未开始 / 进行中 / 收尾中 / 已结束。"""
        if day < holiday.start:
            return "未开始"
        if day <= holiday.end:
            return "进行中"
        if (day - holiday.end).days <= WRAP_UP_DAYS:
            return "收尾中"
        return "已结束"

    def finished_holidays(self, day: Date) -> list[Holiday]:
        """已经结束（含收尾期）的假期，供"清理已结束"用。"""
        return [holiday for holiday in self.holidays
                if day > holiday.end + timedelta(days=WRAP_UP_DAYS)]

    def makeup_on(self, day: Date) -> Makeup | None:
        for makeup in self.makeups:
            if makeup.date == day:
                return makeup
        return None

    def holiday_named(self, name: str) -> Holiday | None:
        wanted = _normalize_name(name)
        if not wanted:
            return None
        for holiday in self.holidays:
            got = _normalize_name(holiday.name)
            # 放假安排里的名字是自由文本："国庆节" / "国庆节、中秋节" / "国庆假期" 都得认
            if got == wanted or wanted in got or got in wanted:
                return holiday
        return None

    @property
    def empty(self) -> bool:
        return not self.holidays and not self.makeups

    def sorted_holidays(self) -> list[Holiday]:
        return sorted(self.holidays, key=lambda item: (item.start, item.name))

    def sorted_makeups(self) -> list[Makeup]:
        return sorted(self.makeups, key=lambda item: (item.date, item.source or item.date))

    def add_holiday(self, holiday: Holiday) -> None:
        self.holidays = [item for item in self.holidays
                         if not (item.name == holiday.name and item.start == holiday.start)]
        self.holidays.append(holiday)

    def remove_holiday(self, index: int) -> bool:
        if 0 <= index < len(self.holidays):
            del self.holidays[index]
            return True
        return False

    def add_makeup(self, makeup: Makeup) -> None:
        self.makeups = [item for item in self.makeups if item.date != makeup.date]
        self.makeups.append(makeup)

    def remove_makeup(self, index: int) -> bool:
        if 0 <= index < len(self.makeups):
            del self.makeups[index]
            return True
        return False

    def replace(self, *, holidays: list[Holiday], makeups: list[Makeup]) -> None:
        self.holidays = list(holidays)
        self.makeups = list(makeups)

    # -- 落盘 ------------------------------------------------------------
    def to_payload(self) -> dict:
        return {
            "holidays": [item.to_payload() for item in self.sorted_holidays()],
            "makeups": [item.to_payload() for item in self.sorted_makeups()],
        }

    @classmethod
    def from_payload(cls, payload: object) -> "Calendar":
        calendar = cls()
        if not isinstance(payload, dict):
            return calendar
        raw_holidays = payload.get("holidays")
        if isinstance(raw_holidays, list):
            for item in raw_holidays:
                holiday = _holiday_from(item)
                if holiday is not None:
                    calendar.holidays.append(holiday)
        raw_makeups = payload.get("makeups")
        if isinstance(raw_makeups, list):
            for item in raw_makeups:
                makeup = _makeup_from(item)
                if makeup is not None:
                    calendar.makeups.append(makeup)
        return calendar

    def save(self, data_dir: Path) -> Path:
        path = Path(data_dir) / CALENDAR_NAME
        path.write_text(json.dumps(self.to_payload(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path


def _holiday_from(item: object) -> Holiday | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip() or "假期"
    start = parse_date(item.get("start") or item.get("from"))
    if start is None:
        return None
    end = parse_date(item.get("end") or item.get("to"), default_year=start.year) or start
    if end < start:
        start, end = end, start
    return Holiday(name=name, start=start, end=end, note=str(item.get("note") or "").strip())


def _makeup_from(item: object) -> Makeup | None:
    if not isinstance(item, dict):
        return None
    day = parse_date(item.get("date") or item.get("day"))
    if day is None:
        return None
    source = parse_date(item.get("source") or item.get("from"), default_year=day.year)
    return Makeup(date=day, source=source, label=str(item.get("label") or "").strip())


def load_calendar(data_dir: Path) -> Calendar:
    """读 calendar.json；文件不在 / 格式坏了都当成"没配假期"，不让面板挂掉。"""
    path = Path(data_dir) / CALENDAR_NAME
    if not path.exists():
        return Calendar()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return Calendar()
    return Calendar.from_payload(payload)


def _normalize_name(name: str) -> str:
    return re.sub(r"[\s·、,，/]|节|日|假期|放假", "", str(name or "")).lower()


# ---------------------------------------------------------------------------
# 预填：按节日给出"典型连休"区间
# ---------------------------------------------------------------------------

def suggested_holidays(year: int) -> list[Holiday]:
    """按节日日期铺一份"典型放假区间"（**预填，务必按学校/国务院通知核对**）。

    为什么不写死"某年放假安排"：那是每年 11 月国务院另发的通知，还会因为
    调休凑假年年不同。这里只按节日当天 + 常见连休长度给个草稿，
    note 里写清楚要核对，用户改不改都行。
    """
    from .festival import festival_occurrences

    suggestions: list[Holiday] = []
    for festival in festival_occurrences(year):
        if not festival.statutory:
            continue
        span = festival.suggested_span
        start = festival.day - timedelta(days=span[0])
        end = festival.day + timedelta(days=span[1])
        suggestions.append(Holiday(
            name=festival.name, start=start, end=end,
            note="系统预填：按常见连休推算，请以学校通知为准",
        ))
    return suggestions
