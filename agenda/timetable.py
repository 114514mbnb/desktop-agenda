"""课程表读取：CSV / JSON / 纯文本 → 每周重复课程 → 展开成日期事件。

课程表与群通知的关系：课程表是"每周重复"的骨架（学期内每都发生），
群通知是"一次性"的事务（会议、报名截止）。两者在周视图里合并展示，
课程表不写入 events.json，只在渲染时按当周日期展开，避免把一周记忆撑爆。

支持的课程表文件（放在 data/ 目录，任意一个即可）：
  timetable.json  手工/导出的结构化课表
  timetable.csv   教务系统导出的表格，列名见 csv 解析说明
  timetable.txt   纯文本课表，逐行写 "周一 第1-2节 高等数学 教三301 张老师 1-16周"
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date as Date, datetime, timedelta
from pathlib import Path

DEFAULT_PERIODS: tuple[tuple[str, str], ...] = (
    ("08:00", "08:45"),
    ("08:55", "09:40"),
    ("10:00", "10:45"),
    ("10:55", "11:40"),
    ("14:00", "14:45"),
    ("14:55", "15:40"),
    ("16:00", "16:45"),
    ("16:55", "17:40"),
    ("19:00", "19:45"),
    ("19:55", "20:40"),
    ("20:50", "21:35"),
    ("21:45", "22:30"),
)

WEEKDAY_TOKENS = {
    "一": 0, "1": 0, "mon": 0, "monday": 0,
    "二": 1, "2": 1, "tue": 1, "tuesday": 1,
    "三": 2, "3": 2, "wed": 2, "wednesday": 2,
    "四": 3, "4": 3, "thu": 3, "thursday": 3,
    "五": 4, "5": 4, "fri": 4, "friday": 4,
    "六": 5, "6": 5, "sat": 5, "saturday": 5,
    "日": 6, "天": 6, "7": 6, "sun": 6, "sunday": 6,
}

WEEKDAY_RE = re.compile(r"(?:周|星期|礼拜)\s*([一二三四五六日天1-7])")
PERIOD_RE = re.compile(r"第?\s*(\d{1,2})\s*(?:[-–~至到]\s*(\d{1,2})\s*)?节")
TIME_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})\s*[-–~至到]\s*(\d{1,2})\s*[:：]\s*(\d{2})")


# ---------------------------------------------------------------------------
# 时间表与课程模型
# ---------------------------------------------------------------------------

def period_table_from(raw: object) -> tuple[tuple[str, str], ...]:
    """把用户配置的节次时间转成元组；非法就退回默认。"""
    if not isinstance(raw, list) or not raw:
        return DEFAULT_PERIODS
    table: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            table.append((str(item[0]), str(item[1])))
        elif isinstance(item, dict) and "start" in item and "end" in item:
            table.append((str(item["start"]), str(item["end"])))
    return tuple(table) if table else DEFAULT_PERIODS


@dataclass
class Course:
    """一条每周重复的课程。"""

    name: str
    weekday: int                     # 0=周一
    start_period: int                # 1 起
    end_period: int                  # 含
    location: str | None = None
    teacher: str | None = None
    weeks: tuple[int, ...] | None = None   # None = 全学期每周
    start: str | None = None         # 直接给时间时优先于节次
    end: str | None = None
    note: str | None = None
    color: str | None = None
    #: 「只改这一条的下课时间」的覆盖值（面板右键"修改结束时间"写入）。
    #: 为什么不用改课时表：课时表是全校作息，改它会影响所有课；
    #: 而"这门课今天拖堂到 12:30"只跟这一门课有关。
    end_time: str | None = None
    #: 这条课在 timetable.json 的 courses 数组里的下标（加载时填，右键回写用）
    source_index: int | None = None

    def period_label(self) -> str:
        if self.start_period == self.end_period:
            return f"第{self.start_period}节"
        return f"第{self.start_period}-{self.end_period}节"

    def active_on(self, week: int) -> bool:
        return self.weeks is None or week in self.weeks

    def times(self, table: tuple[tuple[str, str], ...]) -> tuple[str | None, str | None]:
        """返回 (开始时间, 结束时间)。

        关键：第 4-5 节 = 第 4 节的**开始** 到 第 5 节的**结束**。
        早期实现取的是 table[end_period][1]（第5节之后那一节的结束时间），
        于是"第4-5节"会显示成 10:55-14:45 跨过午休——这是用户实测报回来的 bug。
        """
        if self.start:
            return self.start, self.end_time or self.end
        table = table or ()
        start = table[self.start_period - 1][0] if 1 <= self.start_period <= len(table) else None
        end = table[self.end_period - 1][1] if 1 <= self.end_period <= len(table) else None
        return start, self.end_time or end


@dataclass
class Timetable:
    courses: list[Course] = field(default_factory=list)
    periods: tuple[tuple[str, str], ...] = DEFAULT_PERIODS
    term_start: Date | None = None       # 第 1 周的周一
    source: str | None = None

    def __len__(self) -> int:
        return len(self.courses)

    def week_of(self, day: Date) -> int:
        """某天属于第几教学周（没设 term_start、或它格式不对时按 1 计）。"""
        if not isinstance(self.term_start, Date):
            return 1
        delta = (day - self.term_start).days
        return delta // 7 + 1

    def week_start(self, day: Date) -> Date:
        return day - timedelta(days=day.weekday())

    def courses_on(self, day: Date) -> list[Course]:
        week = self.week_of(day)
        found = [c for c in self.courses if c.weekday == day.weekday() and c.active_on(week)]
        return sorted(found, key=lambda c: (c.start_period, c.name))


# ---------------------------------------------------------------------------
# 周次解析
# ---------------------------------------------------------------------------

def parse_weeks(text: str) -> tuple[int, ...] | None:
    """解析 "1-16周" "1-16周(单)" "1,3,5-9周" "单周" "双周"；全周返回 None。"""
    if not text:
        return None
    value = text.strip()
    if not value or value in {"每周", "全周", "全部", "all"}:
        return None
    odd = "单" in value
    even = "双" in value
    numbers = re.findall(r"\d{1,2}", value)
    if not numbers:
        return None
    weeks: set[int] = set()
    for match in re.finditer(r"(\d{1,2})\s*[-–~至到]\s*(\d{1,2})", value):
        start, end = int(match.group(1)), int(match.group(2))
        if start > end:
            start, end = end, start
        weeks.update(range(start, end + 1))
    for single in re.findall(r"(?<![\d\-–~至到])(\d{1,2})(?![\d\-–~至到])", value):
        weeks.add(int(single))
    if not weeks:
        weeks.update(int(number) for number in numbers)
    if odd:
        weeks = {week for week in weeks if week % 2 == 1}
    elif even:
        weeks = {week for week in weeks if week % 2 == 0}
    return tuple(sorted(weeks))


def parse_weekday(text: str) -> int | None:
    match = WEEKDAY_RE.search(text or "")
    if match is not None:
        return WEEKDAY_TOKENS.get(match.group(1).lower())
    stripped = (text or "").strip().lower()
    return WEEKDAY_TOKENS.get(stripped)


# ---------------------------------------------------------------------------
# 各格式解析
# ---------------------------------------------------------------------------

def _course_from_mapping(item: dict[str, object], periods: tuple[tuple[str, str], ...]) -> Course | None:
    name = str(item.get("name") or item.get("课程") or item.get("course") or "").strip()
    if not name:
        return None
    weekday_raw = item.get("weekday", item.get("周", item.get("星期")))
    if isinstance(weekday_raw, int):
        weekday = weekday_raw - 1 if 1 <= weekday_raw <= 7 else max(0, min(6, weekday_raw))
    else:
        weekday = parse_weekday(str(weekday_raw or ""))
    if weekday is None:
        return None

    start_period = end_period = None
    for key in ("period", "节次", "节"):
        if key in item and item[key] not in (None, ""):
            match = PERIOD_RE.search(str(item[key])) or re.fullmatch(r"\s*(\d{1,2})\s*[-–~至到]?\s*(\d{1,2})?\s*", str(item[key]))
            if match is not None:
                start_period = int(match.group(1))
                end_period = int(match.group(2)) if match.group(2) else start_period
            break
    start = str(item.get("start") or "").strip() or None
    end = str(item.get("end") or "").strip() or None
    if start and not start_period:
        start_period = end_period = 1
    if start_period is None:
        start_period = end_period = 1
    end_period = end_period or start_period

    weeks = parse_weeks(str(item.get("weeks") or item.get("周次") or ""))

    return Course(
        name=name,
        weekday=weekday,
        start_period=start_period,
        end_period=end_period,
        location=str(item.get("location") or item.get("教室") or item.get("地点") or "").strip() or None,
        teacher=str(item.get("teacher") or item.get("老师") or item.get("教师") or "").strip() or None,
        weeks=weeks,
        start=start,
        end=end,
        note=str(item.get("note") or item.get("备注") or "").strip() or None,
        color=str(item.get("color") or "").strip() or None,
        end_time=str(item.get("endTime") or item.get("end_time") or "").strip() or None,
    )


def parse_json_timetable(payload: object) -> Timetable:
    periods = DEFAULT_PERIODS
    term_start = None
    rows: list[dict[str, object]] = []

    if isinstance(payload, dict):
        periods = period_table_from(payload.get("periods"))
        raw_term = payload.get("termStart") or payload.get("term_start") or payload.get("开学第一周周一")
        if isinstance(raw_term, str) and raw_term.strip():
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
                try:
                    term_start = datetime.strptime(raw_term.strip(), fmt).date()
                    break
                except ValueError:
                    continue
        courses = payload.get("courses") or payload.get("课程") or []
        if isinstance(courses, list):
            rows = [item for item in courses if isinstance(item, dict)]
    elif isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]

    courses = [course for course in (_course_from_mapping(row, periods) for row in rows) if course is not None]
    return Timetable(courses=courses, periods=periods, term_start=term_start)


CSV_ALIASES = {
    "name": ("课程", "课程名称", "科目", "name", "course", "课程名"),
    "weekday": ("星期", "周几", "weekday", "周", "星期几"),
    "period": ("节次", "节", "period", "时间", "上课时间"),
    "weeks": ("周次", "weeks", "教学周", "上课周次"),
    "location": ("教室", "地点", "location", "上课地点", "场地"),
    "teacher": ("老师", "教师", "teacher", "任课教师", "授课教师"),
    "note": ("备注", "note", "说明"),
    "start": ("开始", "start", "开始时间"),
    "end": ("结束", "end", "结束时间"),
}


def _pick(row: dict[str, str], key: str) -> str:
    for alias in CSV_ALIASES[key]:
        for column, value in row.items():
            if column and column.strip().lower() == alias.lower():
                return (value or "").strip()
    return ""


def parse_csv_timetable(text: str) -> Timetable:
    reader = csv.DictReader(text.splitlines())
    courses: list[Course] = []
    for row in reader:
        if not any((value or "").strip() for value in row.values()):
            continue
        mapping: dict[str, object] = {
            "name": _pick(row, "name"),
            "weekday": _pick(row, "weekday"),
            "weeks": _pick(row, "weeks"),
            "location": _pick(row, "location"),
            "teacher": _pick(row, "teacher"),
            "note": _pick(row, "note"),
        }
        period_text = _pick(row, "period")
        start_text = _pick(row, "start")
        end_text = _pick(row, "end")
        if period_text:
            mapping["period"] = period_text
        if start_text:
            mapping["start"] = start_text
            mapping["end"] = end_text or None
        course = _course_from_mapping(mapping, DEFAULT_PERIODS)
        if course is not None:
            courses.append(course)
    return Timetable(courses=courses, source="csv")


TEXT_LINE_RE = re.compile(
    r"^\s*(?P<weekday>周[一二三四五六日天]|星期[一二三四五六日天])"
    r"\s*(?P<period>第?\s*\d{1,2}\s*(?:[-–~至到]\s*\d{1,2}\s*)?节)"
    r"\s+(?P<name>[^\s]+)"
    r"(?:\s+(?P<location>[^\s]+))?"
    r"(?:\s+(?P<teacher>[^\s]+))?"
    r"(?:\s+(?P<weeks>\S*周[^\s]*))?\s*$"
)


def parse_text_timetable(text: str) -> Timetable:
    courses: list[Course] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = TEXT_LINE_RE.match(line)
        if match is None:
            continue
        mapping = match.groupdict()
        course = _course_from_mapping(mapping, DEFAULT_PERIODS)  # type: ignore[arg-type]
        if course is not None:
            courses.append(course)
    return Timetable(courses=courses, source="text")


# ---------------------------------------------------------------------------
# 载入与展开
# ---------------------------------------------------------------------------

def load_timetable(data_dir: Path) -> Timetable | None:
    """按 json → csv → txt 的优先级读取课程表；都没有则返回 None。"""
    candidates = [
        (data_dir / "timetable.json", "json"),
        (data_dir / "timetable.csv", "csv"),
        (data_dir / "timetable.txt", "txt"),
    ]
    for path, kind in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            if kind == "json":
                table = parse_json_timetable(json.loads(text))
            elif kind == "csv":
                table = parse_csv_timetable(text)
            else:
                table = parse_text_timetable(text)
        except (json.JSONDecodeError, csv.Error) as error:
            raise RuntimeError(f"{path.name} 解析失败：{error}") from error
        table.source = path.name
        if table.courses:
            return table
    return None


@dataclass
class CourseOccurrence:
    """展开后的一次具体上课。"""

    course: Course
    day: Date
    start: str | None
    end: str | None
    week: int

    @property
    def title(self) -> str:
        return self.course.name

    @property
    def detail(self) -> str:
        parts = [part for part in (self.course.location, self.course.teacher) if part]
        return " · ".join(parts)


def occurrences_on(
    table: Timetable | None,
    day: Date,
    *,
    source: Date | None = None,
) -> list[CourseOccurrence]:
    """`day` 那天实际要上的课。

    `source` 给定时表示**调休补课**：把 source 那天的课原样搬到 day 上
    （周次也按 source 所在教学周算，因为补的就是"那一天"的课）。
    """
    if table is None or not table.courses:
        return []
    key_day = source or day
    week = table.week_of(key_day)
    found = [course for course in table.courses
             if course.weekday == key_day.weekday() and course.active_on(week)]
    found.sort(key=lambda course: (course.start_period, course.name))
    occurrences: list[CourseOccurrence] = []
    for course in found:
        begin, finish = course.times(table.periods)
        # 记下它在 timetable.json 的 courses 里的下标：
        # 面板右键"修改结束时间"要按这个下标回写，不能靠课程名匹配
        # （同名课程会有多条：单周一个教室、双周另一个教室）。
        if course.source_index is None:
            for index, candidate in enumerate(table.courses):
                if candidate is course:
                    course.source_index = index
                    break
        occurrences.append(CourseOccurrence(
            course=course, day=day, start=begin, end=finish, week=week,
        ))
    return occurrences


def expand_timetable(
    table: Timetable | None,
    start: Date,
    days: int = 7,
) -> list[CourseOccurrence]:
    """把课表展开到 [start, start+days) 的具体日期上（不考虑假期/调休）。"""
    occurrences: list[CourseOccurrence] = []
    for offset in range(days):
        occurrences.extend(occurrences_on(table, start + timedelta(days=offset)))
    return occurrences
