"""从**本地文件**识别课表（.ics / .pdf / .html / .json / .csv / .txt / 剪贴板）。

为什么把这条路做成一等公民：让浏览器自己去教务系统里翻课表，受登录方式、
iframe 结构、页面改版影响太大，识别质量不稳。而"教务系统导出的文件"是**静态**的：
格式固定、可以反复解析、坏了也能把文件发给我加规则。所以主推这条。

支持格式与各自的解析方式：

| 后缀 | 来源 | 解析方式 |
| --- | --- | --- |
| `.ics` | WakeUp 课程表导出 | 展开 RRULE 求教学周（`tools/import_wakeup_ics.py` 同一份逻辑） |
| `.pdf`  | 教务系统打印/导出 | 按 ★ 定位课程格，UTF-16BE + 页面旋转（`tools/parse_schedule_pdf.py` 同一份逻辑） |
| `.html` `.htm` | 课表页"另存为" | 先按识别到的课表页地址抓取，再退回表格/文本 |
| `.json` | 本项目 timetable.json | 直读 |
| `.csv` `.tsv` | Excel / WakeUp 模板 | 按列名识别（复用 WakeUpCsvAdapter / PlainTableAdapter） |
| `.txt` `.md` | 复制粘贴保存的文本 | 按表格文本解析 |
| 剪贴板 | 页面上全选复制 | 同上 |

失败时不抛裸异常：返回 `warnings` 说明"这份文件哪里没看懂"，
用户把文件发过来就能加规则。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: 支持的后缀 → 给人看的说明
SUPPORTED_SUFFIXES: dict[str, str] = {
    ".ics": "WakeUp 课程表导出的日历文件",
    ".pdf": "教务系统导出的课表 PDF",
    ".html": "课表网页另存为",
    ".htm": "课表网页另存为",
    ".json": "本程序的 timetable.json",
    ".csv": "Excel / WakeUp 模板导出的 CSV",
    ".tsv": "制表符分隔的表格",
    ".txt": "复制的课表文本",
    ".md": "复制的课表文本",
}

#: 各格式的"这门课从哪来"，显示在确认窗右上角
SOURCE_LABELS: dict[str, str] = {
    ".ics": "WakeUp .ics 文件",
    ".pdf": "教务课表 PDF",
    ".html": "课表网页文件",
    ".htm": "课表网页文件",
    ".json": "timetable.json",
    ".csv": "CSV 表格",
    ".tsv": "CSV 表格",
    ".txt": "课表文本",
    ".md": "课表文本",
}

FILE_TYPES = [
    ("课表文件", "*.ics *.pdf *.html *.htm *.json *.csv *.tsv *.txt *.md"),
    ("WakeUp 日历 (.ics)", "*.ics"),
    ("教务课表 PDF (*.pdf)", "*.pdf"),
    ("课表网页 (*.html)", "*.html *.htm"),
    ("表格 (*.csv *.tsv)", "*.csv *.tsv"),
    ("所有文件", "*.*"),
]


@dataclass
class FileImportResult:
    """一次文件识别的结果，直接喂给「确认识别结果」窗。"""

    courses: list[dict] = field(default_factory=list)
    source: str = ""
    term_start: str = ""
    periods: list[list[str]] | None = None
    warnings: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def count(self) -> int:
        return len(self.courses)


def _read_text(path: Path) -> str:
    """按常见中文编码依次尝试（教务导出的 CSV 常是 GBK）。"""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 各格式解析
# ---------------------------------------------------------------------------

def parse_ics(path: Path) -> FileImportResult:
    """WakeUp 导出的 .ics：展开 RRULE → 教学周；第 1 周按文件里最早那节课推。"""
    from tools import import_wakeup_ics as ics_tool

    events = ics_tool.parse_events(path)
    if not events:
        return FileImportResult(
            source=SOURCE_LABELS[".ics"],
            warnings=["该 .ics 文件中没有读到任何日程（VEVENT），请确认是否为 WakeUp 导出的课表文件。"],
        )
    anchor = ics_tool.guess_term_start(events)
    courses = ics_tool.build(events, term_start=anchor)
    warnings: list[str] = []
    if courses:
        weeks = [c for c in courses if c.get("weeks")]
        if not weeks:
            warnings.append("所有课程均未解析出周次，已按每周处理，请核对。")
    else:
        warnings.append("读取了 %d 个日程，但未能组成课程条目。" % len(events))
    return FileImportResult(
        courses=courses,
        source=SOURCE_LABELS[".ics"],
        term_start=anchor.strftime("%Y-%m-%d"),
        periods=[["08:00", "08:45"], ["08:55", "09:50"], ["10:10", "10:55"], ["11:05", "12:00"],
                 ["14:00", "14:45"], ["14:55", "15:50"], ["16:10", "16:55"], ["17:05", "18:00"],
                 ["19:00", "19:45"], ["19:55", "20:50"], ["20:50", "21:35"]],
        warnings=warnings,
        note=f"来自 WakeUp .ics（{len(events)} 个日程；第 1 教学周周一 = {anchor}）",
    )


def parse_pdf(path: Path) -> FileImportResult:
    """教务导出的课表 PDF。"""
    from tools import parse_schedule_pdf as pdf_tool

    courses, warnings, page_base = pdf_tool.extract(path)
    rows: list[dict] = []
    days = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    uncertain = 0
    for course in courses:
        weekday = max(0, min(6, int(course.get("weekday", 0))))
        if not course.get("_weekday_confirmed"):
            uncertain += 1
        rows.append({
            "name": course.get("name", ""),
            "weekday": days[weekday],
            "period": str(course.get("period") or "1-2"),
            "weeks": str(course.get("weeks") or ""),
            "location": str(course.get("location") or ""),
            "teacher": str(course.get("teacher") or ""),
        })
    notes = list(warnings)
    if uncertain:
        notes.append(f"有 {uncertain} 条课程的星期由列位置推断，请在下方逐条核对（星期列已确认的标记为 ✓）")
    if not rows:
        notes.append("该 PDF 未能解析出课程：可能为扫描件或图片版，也可能并非教务导出的课表。")
    return FileImportResult(
        courses=rows,
        source=SOURCE_LABELS[".pdf"],
        warnings=notes,
        note=f"来自课表 PDF（{len(rows)} 条；每页首列星期基准 {page_base}）",
    )


def parse_html_text(text: str, *, suffix: str = ".html") -> FileImportResult:
    """课表网页（文件或粘贴内容）。"""
    from agenda.eas import EasError, HtmlPageAdapter, PlainTableAdapter

    warnings: list[str] = []
    try:
        result = HtmlPageAdapter().parse(text)
        if result.count:
            return FileImportResult(
                courses=[_course_to_row(c) for c in result.courses],
                source=SOURCE_LABELS.get(suffix, "课表网页"),
                note=f"来自网页内容（{result.count} 门课）",
            )
        warnings.extend(getattr(result, "warnings", []) or [])
    except EasError as error:
        warnings.append(f"HTML 解析未成功：{error}")

    try:
        result = PlainTableAdapter().parse(text)
        if result.count:
            return FileImportResult(
                courses=[_course_to_row(c) for c in result.courses],
                source=SOURCE_LABELS.get(suffix, "课表文本"),
                note=f"按表格文本解析（{result.count} 门课）",
            )
    except EasError as error:
        warnings.append(f"表格文本解析未成功：{error}")

    warnings.append("该内容中没有识别出课表结构：请确认保存或复制的是**课表页**，"
                    "而非登录页或首页；也可将文件反馈给开发者以补充规则。")
    return FileImportResult(source=SOURCE_LABELS.get(suffix, "课表网页"), warnings=warnings)


def parse_json_payload(text: str) -> FileImportResult:
    """本项目的 timetable.json（或任何 {"courses": [...]} 结构）。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        return FileImportResult(source=SOURCE_LABELS[".json"],
                                warnings=[f"不是合法的 JSON：{error}"])
    if not isinstance(payload, dict):
        return FileImportResult(source=SOURCE_LABELS[".json"],
                                warnings=["JSON 顶层不是对象，无法读取课程列表。"])
    raw = payload.get("courses") or payload.get("课程") or []
    if not isinstance(raw, list) or not raw:
        return FileImportResult(source=SOURCE_LABELS[".json"],
                                warnings=["这份 JSON 里没有 courses 列表。"])
    rows = [
        {
            "name": str(item.get("name") or "").strip(),
            "weekday": str(item.get("weekday") or item.get("星期") or "周一").strip(),
            "period": str(item.get("period") or item.get("节次") or "1-2").strip(),
            "weeks": str(item.get("weeks") or item.get("周次") or "").strip(),
            "location": str(item.get("location") or item.get("地点") or "").strip(),
            "teacher": str(item.get("teacher") or item.get("老师") or "").strip(),
        }
        for item in raw if isinstance(item, dict)
    ]
    rows = [row for row in rows if row["name"]]
    return FileImportResult(
        courses=rows,
        source=SOURCE_LABELS[".json"],
        term_start=str(payload.get("termStart") or payload.get("term_start") or ""),
        periods=payload.get("periods") if isinstance(payload.get("periods"), list) else None,
        note=f"来自 timetable.json（{len(rows)} 门课）",
    )


def parse_table_text(text: str, *, suffix: str = ".csv") -> FileImportResult:
    """CSV / TSV / 纯文本课表。"""
    from agenda.eas import EasError, PlainTableAdapter, WakeUpCsvAdapter

    if suffix in (".csv", ".tsv"):
        try:
            result = WakeUpCsvAdapter().parse(text)
            if result.count:
                return FileImportResult(
                    courses=[_course_to_row(c) for c in result.courses],
                    source=SOURCE_LABELS.get(suffix, "CSV"),
                    note=f"按 WakeUp 模板解析（{result.count} 门课）",
                    warnings=list(getattr(result, "warnings", []) or []),
                )
        except EasError as error:
            head = f"按 WakeUp 模板解析未成功（{error}），改用通用表格解析。"
        else:
            head = "按 WakeUp 模板没解析出课程，改用通用表格解析。"
    else:
        head = ""

    try:
        result = PlainTableAdapter().parse(text)
        if result.count:
            warnings = [head] if head else []
            warnings.extend(list(getattr(result, "warnings", []) or []))
            return FileImportResult(
                courses=[_course_to_row(c) for c in result.courses],
                source=SOURCE_LABELS.get(suffix, "课表文本"),
                note=f"按表格文本解析（{result.count} 门课）",
                warnings=warnings,
            )
    except EasError as error:
        return FileImportResult(source=SOURCE_LABELS.get(suffix, "课表文本"),
                                warnings=[f"解析失败：{error}"])

    return FileImportResult(
        source=SOURCE_LABELS.get(suffix, "课表文本"),
        warnings=["该表格中未识别出课程：需要能识别「课程 / 星期 / 节次」三列。"
                  "若内容复制自教务页面，可改用「粘贴课表网页」重试。"],
    )


def _course_to_row(course) -> dict:
    from agenda.eas import format_weeks

    weeks = getattr(course, "weeks", None)
    if weeks is None:
        weeks_text = ""
    else:
        try:
            weeks_text = format_weeks(list(weeks))
        except Exception:
            weeks_text = str(weeks)
    period = (f"{course.start_period}-{course.end_period}"
              if course.end_period != course.start_period else str(course.start_period))
    return {
        "name": course.name,
        "weekday": ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[max(0, min(6, course.weekday))],
        "period": period,
        "weeks": weeks_text,
        "location": course.location or "",
        "teacher": course.teacher or "",
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def import_path(path: Path) -> FileImportResult:
    """按后缀选解析器。任何异常都变成 warnings，不往上抛。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return FileImportResult(
            source=path.name,
            warnings=[f"暂不支持 {suffix or '（无后缀）'} 格式。支持的格式："
                      + "、".join(sorted(SUPPORTED_SUFFIXES))],
        )
    try:
        if suffix == ".ics":
            return parse_ics(path)
        if suffix == ".pdf":
            return parse_pdf(path)
        text = _read_text(path)
        if suffix == ".json":
            return parse_json_payload(text)
        if suffix in (".html", ".htm"):
            result = parse_html_text(text, suffix=suffix)
        else:
            result = parse_table_text(text, suffix=suffix)
        result.source = f"{SOURCE_LABELS.get(suffix, suffix)}：{path.name}"
        return result
    except Exception as error:  # 解析器再怎么写也可能踩到畸形文件
        return FileImportResult(
            source=f"{SOURCE_LABELS.get(suffix, suffix)}：{path.name}",
            warnings=[f"解析该文件时出错：{type(error).__name__}: {error}"],
        )


def import_text(text: str, *, suffix: str = ".txt") -> FileImportResult:
    """直接解析一段文本（剪贴板/粘贴框用）。"""
    suffix = suffix if suffix in SOURCE_LABELS else ".txt"
    text = (text or "").strip()
    if not text:
        return FileImportResult(warnings=["内容是空的。"])
    try:
        if suffix == ".json":
            return parse_json_payload(text)
        if suffix == ".html":
            return parse_html_text(text, suffix=suffix)
        return parse_table_text(text, suffix=suffix)
    except Exception as error:
        return FileImportResult(warnings=[f"解析时出错：{type(error).__name__}: {error}"])


def main() -> int:
    """命令行： ``python -m agenda.file_import 课表.ics`` 先看看能读出什么。"""
    if len(sys.argv) < 2:
        print("用法：python -m agenda.file_import <课表文件>")
        print("支持：" + "、".join(f"{k}（{v}）" for k, v in SUPPORTED_SUFFIXES.items()))
        return 2
    result = import_path(Path(sys.argv[1]))
    print(f"{result.source} → 识别到 {result.count} 条")
    if result.term_start:
        print(f"第 1 教学周周一：{result.term_start}")
    for row in result.courses:
        print(f"  {row['weekday']} 第{row['period']:>4}节  {row['weeks'] or '每周':16}"
              f"  {row['name']:16} @ {row['location'] or '-':18} {row['teacher'] or '-'}")
    for warning in result.warnings:
        print(f"  ! {warning}")
    return 0 if result.count else 1


if __name__ == "__main__":
    raise SystemExit(main())
