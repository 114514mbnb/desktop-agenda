"""课表体检：把"这份课表识别得对不对"变成一份能读的报告。

为什么需要它：课表是从一堆格式各异的东西里**猜**出来的（教务页面的脏字段、PDF 排版、
WakeUp 导出的 .ics……）。识别错了不会报错，只会安静地少一门课、把周次读成节次、
或者把课程代码当成教室——用户面对一百多行课表，靠眼睛逐条核对是不现实的。
所以这里主动把**可疑的地方**挑出来，分成"错误"和"提示"两级。

设计原则：
  * **只报告，不自动改**：识别错了该由用户决定怎么改（自动"修好"反而会掩盖问题）；
  * **宁可少报也不要误报**：比如"只上后半学期的课"是正常现象，不能报成周次异常
    （这条是实测踩过的：`形势与政策 11-14 周` 被误判成"周次不全"）；
  * **每条都要说清依据**：报告里带上具体是哪门课、哪一天、哪些周，用户才能核对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as Date

from .timetable import Course, Timetable

#: 严重程度：error = 一定有问题；warn = 建议核对
ERROR = "error"
WARN = "warn"

_WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

#: 课名里不该出现的教务内部字段（出现了说明这一格里混进了别的列）
_INTERNAL_TOKENS = (
    "考试", "考查", "考核", "讲课", "上机", "实验学时", "未安排", "必修", "选修",
    "限选", "任选", "公选", "考试课", "考查课",
)
#: 课程代码的样子：-D206020600-01 / D206020600 / 一串六位以上数字
_COURSE_CODE = re.compile(r"^[A-Za-z]?\d{6,}|^-?[A-Za-z]\d{6,}")
#: 班级名单的样子：`统计261;统计262;应数261`、`应数1班;应数2班`。
#: 要求**至少两段、每段都带数字**——课名里出现分号是正常的（"大学英语;视听说"），
#: 只有成串的编号才说明这一格混进了别班的名单。
_CLASS_LIST = re.compile(
    r"(?=[^，,、;；]*\d)[^，,、;；]{1,10}(?:[;；](?=[^，,、;；]*\d)[^，,、;；]{1,10})+")

#: 一个学期最多多少周（超过多半是把节次/日期读成了周次）
MAX_WEEKS = 30


@dataclass
class Finding:
    """一条体检结论。"""

    level: str
    title: str
    detail: str
    courses: list[str] = field(default_factory=list)

    def line(self) -> str:
        mark = "✗" if self.level == ERROR else "⚠"
        head = f"{mark} {self.title}"
        if self.courses:
            head += f"（{('、'.join(self.courses[:3]))}{'…' if len(self.courses) > 3 else ''}）"
        return f"{head}\n    {self.detail}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.level == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.level == WARN]

    @property
    def healthy(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.healthy:
            return "课表体检通过：没有发现可疑之处"
        return f"课表体检：{len(self.errors)} 个问题、{len(self.warnings)} 条建议核对"

    def as_text(self, *, course_count: int = 0, term_start: Date | None = None) -> str:
        """导出给用户看的纯文本报告（也可以直接发给开发者排错）。"""
        lines = [self.summary(), ""]
        if course_count:
            lines.append(f"课程数：{course_count}")
        if term_start is not None:
            lines.append(f"第 1 教学周周一：{term_start.isoformat()}")
        if not self.healthy:
            lines.append("")
            for item in self.errors:
                lines.append(item.line())
            for item in self.warnings:
                lines.append(item.line())
        return "\n".join(lines)


def course_weeks(course: Course) -> int:
    """这门课最后一节课在第几周（`weeks=None` 表示全学期，返回 0）。"""
    if not course.weeks:
        return 0
    return max(course.weeks)


def _label(course: Course) -> str:
    day = _WEEKDAY_NAMES[course.weekday] if 0 <= course.weekday < 7 else f"周{course.weekday}"
    return f"{course.name}（{day} 第{course.start_period}-{course.end_period}节）"


def _weeks_text(course: Course, limit: int = 12) -> str:
    if not course.weeks:
        return "全学期"
    weeks = sorted(course.weeks)
    if len(weeks) > limit:
        return f"{weeks[0]}-{weeks[-1]} 周（共 {len(weeks)} 周）"
    return "、".join(str(week) for week in weeks) + " 周"


def _parity(weeks: tuple[int, ...] | None) -> str:
    """单周 / 双周 / 混合——用来发现"同一门课被识别成两条"的情况。"""
    if not weeks:
        return "all"
    odd = [week for week in weeks if week % 2 == 1]
    even = [week for week in weeks if week % 2 == 0]
    if odd and not even:
        return "odd"
    if even and not odd:
        return "even"
    return "mixed"


def check_timetable(table: Timetable | None, *,
                    term_weeks: int = 0) -> Report:
    """体检入口。`term_weeks>0` 时顺带检查"周次是否超出学期"。"""
    report = Report()
    if table is None or not table.courses:
        report.findings.append(Finding(
            level=WARN, title="课表是空的",
            detail="还没有导入课程表，先把课表导进来（课程表 → 从文件识别课表…）。",
        ))
        return report

    courses = table.courses
    period_count = len(table.periods)

    # ---- 1. 节次超出"上课时间"表 ----
    over: list[str] = []
    for course in courses:
        if course.start is not None and course.end is not None:
            continue                      # 直接给了时间，不依赖节次表
        if course.end_period > period_count or course.start_period > period_count:
            over.append(_label(course))
    if over:
        report.findings.append(Finding(
            level=ERROR, title="有课的节次超出「上课时间」表",
            detail=(f"课时表只定义了 {period_count} 节，但下面这些课用到了更大的节次，"
                    f"它们算不出具体时间。请到「上课时间」页把节数调大（最大 24），"
                    f"或检查这几门课的节次是不是被读错了。"),
            courses=over,
        ))

    # ---- 2. 节次顺序反了 ----
    reversed_rows = [_label(course) for course in courses
                     if course.end_period < course.start_period]
    if reversed_rows:
        report.findings.append(Finding(
            level=ERROR, title="有课的结束节次小于开始节次",
            detail="这种课在时间线上会显示成零长度或错位，请核对节次写法（例如 3-4 节）。",
            courses=reversed_rows,
        ))

    # ---- 3. 同一时段撞课 ----
    conflicts: dict[tuple[int, int], list[Course]] = {}
    for course in courses:
        weeks = set(course.weeks or range(1, MAX_WEEKS + 1))
        for period in range(course.start_period, course.end_period + 1):
            conflicts.setdefault((course.weekday, period), []).append(course)
    pairs: list[str] = []
    seen: set[tuple[str, str]] = set()
    for (weekday, period), group in conflicts.items():
        if len(group) < 2:
            continue
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                if first.name == second.name:
                    continue                      # 同名课不算冲突（可能是合班/分段）
                overlap = set(first.weeks or range(1, MAX_WEEKS + 1)) & \
                    set(second.weeks or range(1, MAX_WEEKS + 1))
                if not overlap:
                    continue                      # 周次不重叠就没事
                key = tuple(sorted((_label(first), _label(second))))
                if key in seen:
                    continue
                seen.add(key)
                weeks = "、".join(str(week) for week in sorted(overlap)[:6])
                pairs.append(f"{key[0]} ↔ {key[1]}（{_WEEKDAY_NAMES[weekday]} 第{period}节，"
                             f"第 {weeks} 周重叠）")
    if pairs:
        report.findings.append(Finding(
            level=ERROR, title="同一时段有两门课",
            detail=("可能是选课冲突，也可能是识别时把两门课并到了一格。"
                    "请在「课程表」页逐条核对这几对。"),
            courses=pairs,
        ))

    # ---- 4. 完全重复的行 ----
    signature: dict[tuple, list[Course]] = {}
    for course in courses:
        key = (course.name, course.weekday, course.start_period, course.end_period,
               tuple(sorted(course.weeks)) if course.weeks else None,
               course.location or "", course.teacher or "")
        signature.setdefault(key, []).append(course)
    duplicated = [_label(items[0]) + f"（重复 {len(items)} 次）"
                  for items in signature.values() if len(items) > 1]
    if duplicated:
        report.findings.append(Finding(
            level=WARN, title="有完全一样的课程重复出现",
            detail="内容（课名/星期/节次/周次/地点/教师）完全相同的行多半是导入时重复了，"
                   "可以在「课程表」页删掉多余的。",
            courses=duplicated,
        ))

    # ---- 5. 单双周矛盾：同名课同一天，一条单周一条双周 ----
    by_slot: dict[tuple[str, int, int], list[Course]] = {}
    for course in courses:
        by_slot.setdefault((course.name, course.weekday, course.start_period), []).append(course)
    parity_rows: list[str] = []
    for (name, weekday, period), group in by_slot.items():
        parities = {_parity(course.weeks) for course in group}
        if "odd" in parities and "even" in parities:
            parity_rows.append(f"{name}（{_WEEKDAY_NAMES[weekday]} 第{period}节）")
    if parity_rows:
        report.findings.append(Finding(
            level=WARN, title="同一门课同时存在单周和双周两条",
            detail="如果本来就该每周都上，建议合并成一条并把周次写成 1-16 周；"
                   "确实是单双周分开上的话，忽略这条即可。",
            courses=parity_rows,
        ))

    # ---- 6. 周次异常 ----
    too_big = [f"{_label(course)} → {_weeks_text(course)}"
               for course in courses if course_weeks(course) > MAX_WEEKS]
    if too_big:
        report.findings.append(Finding(
            level=ERROR, title="周次明显超出学期（可能是节次被读成了周次）",
            detail=f"正常情况下一个学期不超过 {MAX_WEEKS} 周。请核对这几门课的周次写法。",
            courses=too_big,
        ))
    if term_weeks <= 0:
        # 用户没告诉学期多长时，用课表自己的**中位数**当"正常长度"：
        # 大部分课在第 16-18 周结束，某一条跑到第 26 周就该问一句。
        ends = sorted(course_weeks(course) for course in courses if course.weeks)
        if len(ends) >= 3:
            median = ends[len(ends) // 2]
            if median > 0:
                term_weeks = median + 6      # 留足余量，宁可不报也别误报
    if term_weeks > 0:
        beyond = [f"{_label(course)} → 第 {course_weeks(course)} 周"
                  for course in courses if course_weeks(course) > term_weeks]
        if beyond:
            report.findings.append(Finding(
                level=WARN, title=f"有课的周次明显长于其他课（多数课在 {term_weeks - 6} 周左右结束）",
                detail="这些课在学期结束之后才会出现，时间线上看不到它们，多半是周次读错了。",
                courses=beyond,
            ))

    # ---- 7. 字段缺失 ----
    missing_place = [_label(course) for course in courses if not (course.location or "").strip()]
    missing_teacher = [_label(course) for course in courses if not (course.teacher or "").strip()]
    missing_weeks = [_label(course) for course in courses if not course.weeks]
    if missing_place:
        report.findings.append(Finding(
            level=WARN, title="有课没读到上课地点",
            detail="不影响时间线显示，但上课前找不到教室。可以在「课程表」页双击补上。",
            courses=missing_place,
        ))
    if missing_teacher:
        report.findings.append(Finding(
            level=WARN, title="有课没读到任课教师",
            detail="同上，属识别信息不全，双击可补。",
            courses=missing_teacher,
        ))
    if missing_weeks:
        report.findings.append(Finding(
            level=WARN, title="有课没写周次（按每周都上处理）",
            detail="如果这门课其实只上前几周，时间线上会一直显示它。",
            courses=missing_weeks,
        ))

    # ---- 8. 课名可疑 ----
    dirty: list[str] = []
    for course in courses:
        name = (course.name or "").strip()
        reasons = []
        if len(name) <= 1:
            reasons.append("只有一个字")
        if len(name) > 30:
            reasons.append("名字过长")
        if _COURSE_CODE.match(name):
            reasons.append("开头像课程代码")
        if _CLASS_LIST.search(name):
            reasons.append("含班级名单")
        for token in _INTERNAL_TOKENS:
            if token in name:
                reasons.append(f"含教务字段「{token}」")
                break
        if reasons:
            dirty.append(f"{name[:24]}（{'、'.join(reasons)}）")
    if dirty:
        report.findings.append(Finding(
            level=ERROR, title="有课名混进了教务内部字段",
            detail="这类课名在面板上会显示成一长串乱码般的文字，多半是识别时串了列，"
                   "请在「课程表」页双击改成真正的课程名。",
            courses=dirty,
        ))

    # ---- 9. 学期开始 ----
    if table.term_start is None:
        report.findings.append(Finding(
            level=WARN, title="没有设置「第 1 教学周周一」",
            detail="没有它的话，周次无法换算成具体日期，面板只能按“每周都上”来显示。"
                   "请到「上课时间」页设置学期开始日期。",
        ))
    return report
