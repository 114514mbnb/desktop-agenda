"""把 WakeUp .ics 与教务 PDF 的解析结果合并成最终课表。

两边各有短板，合起来才完整：
  * ICS：有**绝对时间**（星期/节次可信）、有教师；但周次是分块 RRULE，部分课程只导出了抽样日期。
  * PDF：有**教务给出的明确周次/场地**；但星期列要靠坐标推断（只有第1页有表头）。
合并规则：以 ICS 的星期+节次+教师为准，周次优先用 PDF 的显式范围，PDF 缺失时用 ICS 绝对日期推出的周次。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_wakeup_ics as ics_tool  # noqa: E402

TERM_START = date(2026, 9, 7)
WEEKDAYS = ics_tool.WEEKDAYS
#: 教务 PDF 与 WakeUp 的课程名差异（同一门课的不同写法）
NAME_ALIASES = {
    "数学分析": "数学分析1",
    "高等代数": "高等代数1",
    "解析几何": "解析几何",
    "C++程序设计实验": "C++程序设计实验",
}


def normalize(name: str) -> str:
    name = name.strip()
    return NAME_ALIASES.get(name, name)


def expand_weeks(text: str) -> tuple[int, ...]:
    """解析 "1-16"、"1-5、7-11单"、"4-16周(双)" 这类周次写法。

    注意：范围要先剥离再找单周，否则 "7-11单" 里的 11 会被当成单独一周，
    导致周次集合多出 2、4、8、10 这些不该有的周（真被测试抓到过）。
    """
    if not text:
        return ()
    weeks: set[int] = set()
    rest = text
    for match in re.finditer(r"(\d{1,2})\s*[-–~至]\s*(\d{1,2})", text):
        start, end = int(match.group(1)), int(match.group(2))
        weeks.update(range(min(start, end), max(start, end) + 1))
        rest = rest.replace(match.group(0), " ", 1)
    for single in re.findall(r"\d{1,2}", rest):
        weeks.add(int(single))
    # 单双周的写法有两种："(单)" 和裸"单"（教务 PDF 里是 `7-11单`）
    if "单" in text:
        weeks = {w for w in weeks if w % 2 == 1}
    elif "双" in text:
        weeks = {w for w in weeks if w % 2 == 0}
    return tuple(sorted(weeks))


def weeks_text(weeks: tuple[int, ...]) -> str:
    values = sorted(set(weeks))
    if not values:
        return ""
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


def bucket_events(events: list[dict]) -> dict[tuple, dict]:
    """把 ICS 事件按 (课程, 星期, 起节, 止节, 地点) 聚合，并展开 RRULE 得到周次。

    **必须展开 RRULE，不能只看 DTSTART**：数学分析1 周三那节的 DTSTART 是 9/23（第3周），
    但它带 FREQ=WEEKLY;UNTIL=20270112 的规则，实际上到第 19 周。
    只取 DTSTART 会把整门课写成"只有第 3 周有课"，第 4 周之后面板就空了（实机踩过）。
    """
    merged: dict[tuple, dict] = {}
    for event in events:
        if not event["summary"]:
            continue
        place, teacher = ics_tool.clean_location(event["location"])
        name = normalize(event["summary"])
        weekday = event["start"].weekday()
        start_period = ics_tool.period_of(event["start"])
        end_period = ics_tool.period_of(event["end"])
        key = (name, weekday, start_period, end_period, place)
        bucket = merged.setdefault(key, {"weeks": set(), "teacher": teacher, "ics": True})
        bucket["weeks"].update(
            ics_tool.teaching_week(day) for day in ics_tool.expand_dates(event)
        )
        if teacher and not bucket.get("teacher"):
            bucket["teacher"] = teacher
    return merged


def merge_courses(
    merged: dict[tuple, dict],
    pdf_courses: list[dict],
    *,
    report: list[str] | None = None,
) -> list[dict]:
    """ICS 聚合结果 + PDF 周次 → 最终课表条目（可单测，不落盘）。"""
    pdf_index: dict[tuple, dict] = {}
    pdf_by_name: dict[str, list[dict]] = {}
    for course in pdf_courses:
        name = normalize(course["name"])
        pdf_index[(name, course["weekday"])] = course
        pdf_by_name.setdefault(name, []).append(course)

    def pdf_weeks_for(name: str, weekday: int) -> tuple[tuple[int, ...], dict | None]:
        """把这门课在 PDF 里所有条目的周次**并起来**。

        PDF 里同一门课常被拆成多条（军事理论就是"第9周""第10周"…每周一条），
        只取最长的一条会漏掉其余周次。
        优先按 (课程, 星期) 匹配；匹配不上（PDF 的星期列靠坐标推断，个别课会偏一列）
        就退化为按课程名匹配——周次与星期无关。
        """
        entries = [pdf_index.get((name, weekday))] if (name, weekday) in pdf_index else []
        if not entries:
            entries = pdf_by_name.get(name, [])
        weeks: set[int] = set()
        best: dict | None = None
        for course in entries:
            if course is None:
                continue
            parsed = expand_weeks(course.get("weeks", ""))
            if parsed:
                weeks.update(parsed)
                if best is None or len(parsed) > len(expand_weeks(best.get("weeks", ""))):
                    best = course
        return tuple(sorted(weeks)), best

    courses: list[dict] = []
    for (name, weekday, start_period, end_period, place), bucket in merged.items():
        ics_weeks = tuple(sorted(bucket["weeks"]))
        pdf_weeks, pdf = pdf_weeks_for(name, weekday)
        # 周次取舍：ICS 的 RRULE 展开来自用户**真正在用的课表**，且能反映单/双周节奏，
        # 有 ≥3 周就采信它；不到 3 周（导出时被抽样）才用 PDF 的显式周次兜底。
        if len(ics_weeks) >= 3:
            weeks, source = ics_weeks, "ics"
        elif pdf_weeks:
            weeks, source = pdf_weeks, "pdf"
        else:
            weeks, source = ics_weeks, "ics"
        if report is not None and ics_weeks and pdf_weeks and ics_weeks != pdf_weeks:
            report.append(
                f"{name}（{WEEKDAYS[weekday]} 第{start_period}-{end_period}节）："
                f"ICS 周次 {weeks_text(ics_weeks)}，PDF 周次 {weeks_text(pdf_weeks)}"
                f" → 采用 ICS（{weeks_text(weeks)}）"
            )
        course: dict = {
            "name": name,
            "weekday": WEEKDAYS[weekday],
            "period": f"{start_period}-{end_period}" if end_period != start_period else str(start_period),
        }
        if weeks:
            course["weeks"] = weeks_text(weeks)
            course["_weeksSource"] = source
        location = (place or (pdf or {}).get("location") or "").strip()
        if location:
            course["location"] = location
        teacher = bucket.get("teacher") or (pdf or {}).get("teacher")
        if teacher:
            course["teacher"] = teacher
        courses.append(course)
    courses.sort(key=lambda c: (WEEKDAYS.index(c["weekday"]), c["period"]))
    return courses


def main() -> int:
    ics_path = Path(sys.argv[1])
    pdf_json = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("timetable.json")

    # ---- ICS：星期/节次/教师/地点（权威），周次由 RRULE 展开 ----
    events = ics_tool.parse_events(ics_path)
    merged = bucket_events(events)

    # ---- PDF：显式周次（ICS 抽样不全时兜底） ----
    pdf_courses = []
    if pdf_json is not None and pdf_json.exists():
        payload = json.loads(pdf_json.read_text(encoding="utf-8"))
        pdf_courses = payload.get("courses", [])

    report: list[str] = []
    courses = merge_courses(merged, pdf_courses, report=report)
    payload = {
        "termStart": TERM_START.strftime("%Y-%m-%d"),
        "periods": [[start, end] for _i, start, end in ics_tool.PERIOD_SLOTS],
        "note": "星期/节次/教师/地点来自 WakeUp 导出的 .ics（绝对时间，RRULE 已展开为教学周）；周次按证据择优",
        "courses": courses,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"合并完成：{len(courses)} 门课 → {out}\n")
    for course in courses:
        print(f"  {course['weekday']} 第{course['period']:>4}节  {course.get('weeks', '每周'):18}"
              f"  {course['name']:14} @ {course.get('location', '-'):20} / {course.get('teacher', '-')}")
    if report:
        print("\n⚠ ICS 与教务 PDF 周次不一致（已采用 ICS，列出来供人工核对）：")
        for line in report:
            print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
