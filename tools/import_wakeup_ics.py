"""WakeUp .ics → 本项目 timetable.json（按 RRULE 展开周次）。

WakeUp 的导出会把同一门课拆成很多个 VEVENT：每个事件从某个上课日出发、
以 FREQ=WEEKLY + UNTIL 描述"一开始每周都有，直到某天"。
所以正确做法是**逐个展开 RRULE，再按课程聚合取周次并集**，
只看 DTSTART 那一周会漏掉大部分周次。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

TERM_START = date(2026, 9, 7)          # 第 1 教学周周一（与教务 PDF 表头一致）
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
PERIOD_SLOTS: tuple[tuple[int, str, str], ...] = (
    (1, "08:00", "08:45"), (2, "08:55", "09:50"),
    (3, "10:10", "10:55"), (4, "11:05", "12:00"),
    (5, "14:00", "14:45"), (6, "14:55", "15:50"),
    (7, "16:10", "16:55"), (8, "17:05", "18:00"),
    (9, "19:00", "19:45"), (10, "19:55", "20:50"),
    (11, "20:50", "21:35"),
)


def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_events(path: Path) -> list[dict]:
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    events: list[dict] = []
    current: dict | None = None
    for line in unfold(text):
        if line.startswith("BEGIN:VEVENT"):
            current = {}
        elif line.startswith("END:VEVENT"):
            if current:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            name = key.split(";")[0].upper()
            value = value.replace("\\n", " ").replace("\\,", ",").strip()
            if name in {"SUMMARY", "DTSTART", "DTEND", "LOCATION", "RRULE"}:
                current.setdefault(name, value)

    parsed: list[dict] = []
    for event in events:
        match = re.search(r"(\d{8})T(\d{6})", event.get("DTSTART", ""))
        if not match:
            continue
        start = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
        end_match = re.search(r"(\d{8})T(\d{6})", event.get("DTEND", ""))
        end = (datetime.strptime(end_match.group(1) + end_match.group(2), "%Y%m%d%H%M%S")
               if end_match else start)
        parsed.append({
            "summary": (event.get("SUMMARY") or "").strip(),
            "location": (event.get("LOCATION") or "").strip(),
            "start": start,
            "end": end,
            "rrule": event.get("RRULE", ""),
        })
    return parsed


def expand_dates(event: dict, *, limit_week: int = 30, term_start: date | None = None) -> list[date]:
    """按 RRULE 展开出该事件实际发生的所有日期（只保留 FREQ=WEEKLY 的常见情形）。"""
    anchor = term_start or TERM_START
    rrule = event.get("rrule", "")
    start: datetime = event["start"]
    if "FREQ=WEEKLY" not in rrule:
        return [start.date()]
    interval = 1
    found = re.search(r"INTERVAL=(\d+)", rrule)
    if found:
        interval = max(1, int(found.group(1)))
    until: date | None = None
    found = re.search(r"UNTIL=(\d{8})", rrule)
    if found:
        until = datetime.strptime(found.group(1), "%Y%m%d").date()
    last_week = int(((until or start.date()) - anchor).days // 7) + 1 if until else 18
    last_week = min(max(last_week, 1), limit_week)

    dates: list[date] = []
    current = start.date()
    while True:
        week = (current - anchor).days // 7 + 1
        if week > last_week:
            break
        if until and current > until:
            break
        dates.append(current)
        current += timedelta(weeks=interval)
    return dates


def teaching_week(day: date, term_start: date | None = None) -> int:
    anchor = term_start or TERM_START
    return (day - anchor).days // 7 + 1


def period_of(moment: datetime) -> int:
    from datetime import time as dtime

    current = moment.time()
    best = 1
    for index, start_text, end_text in PERIOD_SLOTS:
        if dtime.fromisoformat(start_text) <= current <= dtime.fromisoformat(end_text):
            return index
        if current < dtime.fromisoformat(start_text):
            return max(1, index - 1)
        best = index
    return best


def clean_location(raw: str) -> tuple[str | None, str | None]:
    """LOCATION 形如 "中心校区 A-330 陈立" → (地点, 教师)。"""
    text = re.sub(r"\s+", " ", raw).strip()
    if not text:
        return None, None
    parts = text.split(" ")
    teacher = None
    if len(parts) > 1 and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}(?:,[\u4e00-\u9fa5]{2,4})*", parts[-1]):
        teacher = parts[-1]
        parts = parts[:-1]
    return (" ".join(parts) or None), teacher


def build(events: list[dict], *, term_start: date | None = None) -> list[dict]:
    """把事件聚合成课程条目；term_start 决定"第几教学周"怎么算。

    分组键里带上 **地点**：同一门课在不同周的教室可能不同（单周在 A 楼、双周在线上），
    按地点分开才能如实反映，而不是把两个地点揉成一条。
    """
    anchor = term_start or TERM_START
    grouped: dict[tuple, dict] = {}
    for event in events:
        if not event["summary"]:
            continue
        place, teacher = clean_location(event["location"])
        key = (event["summary"], event["start"].weekday(), event["start"].time(),
               event["end"].time(), place)
        bucket = grouped.setdefault(key, {"weeks": set(), "teacher": teacher})
        bucket["weeks"].update(teaching_week(day, anchor) for day in expand_dates(event, term_start=anchor))

    courses: list[dict] = []
    for (name, weekday, start_time, end_time, place), bucket in grouped.items():
        weeks = sorted(week for week in bucket["weeks"] if 1 <= week <= 30)
        if not weeks:
            continue
        start_period = period_of(datetime.combine(anchor, start_time))
        end_period = period_of(datetime.combine(anchor, end_time))
        course: dict = {
            "name": name,
            "weekday": WEEKDAYS[weekday],
            "period": f"{start_period}-{end_period}" if end_period != start_period else str(start_period),
            "weeks": _weeks_text(weeks),
        }
        if place:
            course["location"] = place
        if bucket["teacher"]:
            course["teacher"] = bucket["teacher"]
        courses.append(course)
    return sorted(courses, key=lambda c: (WEEKDAYS.index(c["weekday"]), c["period"]))


def guess_term_start(events: list[dict]) -> date:
    """猜"第 1 教学周周一"。

    ⚠ 这只是**兜底猜测**，必须让用户能改：WakeUp 导出的 .ics 里，
    最早那节课不一定是学期第一周——学期中途才想起来导出，第一条就是当时的课。
    所以确认窗里会把这个日期摆出来让人核对（改它比改 18 条周次便宜得多）。
    没有事件时退回内置默认值。
    """
    starts = [event["start"].date() for event in events if event.get("start")]
    if not starts:
        return TERM_START
    earliest = min(starts)
    return earliest - timedelta(days=earliest.weekday())


def _weeks_text(weeks: list[int]) -> str:
    values = sorted(set(weeks))
    groups: list[list[int]] = []
    for value in values:
        if groups and value == groups[-1][-1] + 1:
            groups[-1].append(value)
        else:
            groups.append([value])
    text = "、".join(str(g[0]) if len(g) == 1 else f"{g[0]}-{g[-1]}" for g in groups)
    if len(values) > 2 and all(w % 2 == 1 for w in values):
        text += "(单)"
    elif len(values) > 2 and all(w % 2 == 0 for w in values):
        text += "(双)"
    return text


def main() -> int:
    ics = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("timetable-from-ics.json")
    events = parse_events(ics)
    anchor = guess_term_start(events)
    courses = build(events, term_start=anchor)
    payload = {
        "termStart": anchor.strftime("%Y-%m-%d"),
        "periods": [[start, end] for _i, start, end in PERIOD_SLOTS],
        "note": f"由 WakeUp 导出的 .ics 转换（RRULE 展开为教学周；第 1 教学周周一取 {anchor}）",
        "courses": courses,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(events)} 个事件 → {len(courses)} 门课（第 1 周周一 = {anchor}）→ {out}\n")
    for course in courses:
        print(f"  {course['weekday']} 第{course['period']:>4}节  {course.get('weeks', '每周'):16}"
              f"  {course['name']:14} @ {course.get('location', '-'):18} / {course.get('teacher', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
