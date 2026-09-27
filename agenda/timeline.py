"""时间线视图模型：把群通知事务 + 课表课程，合并成面板可直接渲染的结构。

版式参考 WakeUp 课表的日程时间线：
  左侧时间竖列（起始时间加粗、结束时间小字），中间竖向时间轴（圆点 + 连接线），
  右侧彩色卡片（标题、地点、人员、备注、标签）。进行中的课显示进度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date, datetime, timedelta

from .aggregate import WEEKDAY_LABELS, parse_day
from .festival import Festival, hint_for
from .holidays import Calendar, Holiday, HolidayWrapUp, Makeup
from .models import Event
from .timetable import CourseOccurrence, Timetable, occurrences_on

# 卡片配色（晨课/上午/下午/晚间/待确认）
PALETTE = {
    "morning": "#4C8DF6",     # 晨间
    "noon": "#F2994A",        # 午间
    "afternoon": "#27AE60",   # 下午
    "evening": "#8E6BF0",     # 晚间
    "anytime": "#7A8699",     # 全天/无时间
    "tentative": "#E0555B",   # 日期待确认
}


def _minutes(value: str | None) -> int | None:
    if not value:
        return None
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return hour * 60 + minute
    except (ValueError, AttributeError):
        return None


def _slot_color(start: str | None, tentative: bool) -> str:
    if tentative:
        return PALETTE["tentative"]
    minutes = _minutes(start)
    if minutes is None:
        return PALETTE["anytime"]
    if minutes < 12 * 60:
        return PALETTE["morning"]
    if minutes < 14 * 60:
        return PALETTE["noon"]
    if minutes < 18 * 60:
        return PALETTE["afternoon"]
    return PALETTE["evening"]


@dataclass
class Card:
    """时间线上的一张卡片。"""

    kind: str                    # "event" | "course"
    title: str
    start: str | None
    end: str | None
    date: str
    location: str | None = None
    people: str | None = None
    notes: str | None = None
    badge: str | None = None      # 第几节 / 来源群
    color: str = PALETTE["anytime"]
    tentative: bool = False
    state: str = "future"        # past | now | future
    progress: float = 0.0        # 0~1，仅 now 有意义
    countdown: str | None = None
    #: 通知类卡片对应 events.json 里那条事件的 id（右键"完成/删除"要用它）
    event_id: str | None = None
    #: 课程类卡片对应 timetable.json 里 courses 的下标（右键"改结束时间"要用它）
    course_index: int | None = None

    @property
    def time_label(self) -> str:
        """时间显示。

        课程改用"起始时间 + 节次标签"（如 `10:55 · 第4-5节`）：
        第4-5节横跨午休时，起止写成 10:55–14:45 会让人误以为连上四小时。
        普通事项仍显示完整时段。
        """
        if self.start and self.end:
            if self.kind == "course" and self.badge:
                return f"{self.start} · {self.badge}"
            return f"{self.start}–{self.end}"
        return self.start or "全天"

    @property
    def meta_line(self) -> str:
        parts = [part for part in (self.location, self.people) if part]
        return " · ".join(parts)


@dataclass
class DaySection:
    date: str
    label: str
    weekday: str
    relative: str
    is_today: bool
    is_weekend: bool
    cards: list[Card] = field(default_factory=list)
    course_count: int = 0
    event_count: int = 0
    #: 这一天所属的年份（跨年那几天要显示，否则"1/1"到底是哪年看不出来）
    year: int = 0
    #: 年份和今天不同 → 日期头写成 "2027/1/1"
    show_year: bool = False
    #: 放假区间（在假期里时非空）；假期内课程被压掉
    holiday: Holiday | None = None
    #: 调休：这一天补的是哪一天，以及那行小字"调休10月7日日程"
    makeup_from: str = ""
    makeup_label: str = ""
    #: 这一天的节日（彩蛋），没有就是 None
    festival: Festival | None = None

    @property
    def count(self) -> int:
        return len(self.cards)

    @property
    def holiday_name(self) -> str:
        return self.holiday.name if self.holiday is not None else ""

    @property
    def earliest(self) -> str | None:
        starts = [card.start for card in self.cards if card.start]
        return min(starts) if starts else None


@dataclass
class NextItem:
    card: Card
    minutes_until: int
    started: bool

    def text(self) -> str:
        if self.started:
            return "进行中"
        if self.minutes_until < 60:
            return f"{self.minutes_until} 分钟后"
        hours, rest = divmod(self.minutes_until, 60)
        if hours < 24:
            return f"{hours}小时{rest}分后" if rest else f"{hours}小时后"
        days, hours = divmod(hours, 24)
        return f"{days}天{hours}小时后" if hours else f"{days}天后"


@dataclass
class Timeline:
    today: Date
    now: datetime
    sections: list[DaySection]
    next_item: NextItem | None
    today_event_count: int
    today_course_count: int
    total_cards: int
    timetable_source: str | None = None
    last_run_text: str | None = None
    week_label: str = ""
    stale: bool = False
    #: 今天是不是在放假（假期名）——底部状态和日期头都要用
    holiday: Holiday | None = None
    #: 今天的调休信息（有的话）
    makeup: Makeup | None = None
    #: 今天的节日彩蛋
    festival: Festival | None = None
    #: 假期还剩几天（含今天）；不在假期里是 0
    holiday_left: int = 0
    #: 假期刚结束那几天的收尾状态（含按天数的倒计时）
    wrap_up: HolidayWrapUp | None = None

    def today_section(self) -> DaySection | None:
        for section in self.sections:
            if section.is_today:
                return section
        return None


def _week_label(today: Date, table: Timetable | None) -> str:
    week = table.week_of(today) if table is not None else None
    # 学期开始之前算出来是负数（2026-09-05 开学、现在是 2 月 → 第 -28 周）。
    # 那种"第 -28 教学周"看着像 bug，干脆不显示。
    if week is None or week < 1:
        return ""
    return f"第 {week} 教学周"


def _event_card(event: Event, today: Date, now: datetime) -> Card:
    start_dt = _event_start(event)
    end_dt = _event_end(event)
    state = "future"
    progress = 0.0
    if start_dt is not None and end_dt is not None:
        if now >= end_dt:
            state = "past"
        elif now >= start_dt:
            state = "now"
            total = max(1, int((end_dt - start_dt).total_seconds() // 60))
            passed = int((now - start_dt).total_seconds() // 60)
            progress = max(0.0, min(1.0, passed / total))
    people = "、".join(event.people) if event.people else None
    badge_parts = []
    if event.group:
        badge_parts.append(event.group)
    if event.tentative:
        badge_parts.append("日期待确认")
    return Card(
        kind="event",
        title=event.title,
        start=event.start,
        end=event.end,
        date=event.date,
        location=event.location,
        people=people,
        notes=event.notes,
        badge=" · ".join(badge_parts) or None,
        color=_slot_color(event.start, event.tentative),
        tentative=event.tentative,
        state=state,
        progress=progress,
        event_id=event.id,
    )


def _event_start(event: Event) -> datetime | None:
    day = parse_day(event.date)
    minutes = _minutes(event.start)
    if day is None or minutes is None:
        return None
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60)


def _event_end(event: Event) -> datetime | None:
    start = _event_start(event)
    if start is None:
        return None
    minutes = _minutes(event.end)
    if minutes is None:
        return start + timedelta(hours=1)
    end = start.replace(hour=minutes // 60, minute=minutes % 60)
    if end <= start:
        end += timedelta(days=1)
    return end


def _course_card(occurrence: CourseOccurrence, now: datetime) -> Card:
    start_dt = None
    end_dt = None
    minutes = _minutes(occurrence.start)
    if minutes is not None:
        start_dt = datetime(occurrence.day.year, occurrence.day.month, occurrence.day.day, minutes // 60, minutes % 60)
    end_minutes = _minutes(occurrence.end)
    if start_dt is not None and end_minutes is not None:
        end_dt = start_dt.replace(hour=end_minutes // 60, minute=end_minutes % 60)
    state = "future"
    progress = 0.0
    if start_dt is not None and end_dt is not None:
        if now >= end_dt:
            state = "past"
        elif now >= start_dt:
            state = "now"
            total = max(1, int((end_dt - start_dt).total_seconds() // 60))
            progress = max(0.0, min(1.0, (now - start_dt).total_seconds() / 60 / total))
    return Card(
        kind="course",
        title=occurrence.title,
        start=occurrence.start,
        end=occurrence.end,
        date=occurrence.day.strftime("%Y-%m-%d"),
        location=occurrence.course.location,
        people=occurrence.course.teacher,
        notes=occurrence.course.note,
        badge=occurrence.course.period_label(),
        color=_slot_color(occurrence.start, False),
        state=state,
        progress=progress,
        course_index=getattr(occurrence.course, "source_index", None),
    )


def build_timeline(
    events: list[Event],
    table: Timetable | None = None,
    *,
    today: Date | None = None,
    now: datetime | None = None,
    days: int = 7,
    last_run_text: str | None = None,
    calendar: Calendar | None = None,
    hide_past_events: bool = False,
) -> Timeline:
    anchor = today or Date.today()
    current = now or datetime.now()
    calendar = calendar if calendar is not None else Calendar()

    # 课程展开：课程表是骨架，先展开，再让同一天的事务按时间插入。
    # 假期/调休都在这一步生效：
    #   * 假期内的日子**一天课都不排**（群通知照旧，下面 events 那段不受影响）；
    #   * 调休日排的是"被补的那一天"的课，日期仍然是调休日。
    by_date: dict[str, list[Card]] = {}
    course_counts: dict[str, int] = {}
    day_meta: dict[str, dict] = {}

    for offset in range(days):
        day = anchor + timedelta(days=offset)
        key = day.strftime("%Y-%m-%d")
        makeup = calendar.makeup_on(day)
        holiday = calendar.holiday_on(day)
        meta: dict = {
            "holiday": holiday,
            "makeup_label": "",
            "makeup_from": "",
            "festival": None,
        }
        if makeup is not None:
            # 调休优先于假期：万一同一天既在放假区间里又安排了补课，按补课算
            occurrences = occurrences_on(table, day, source=makeup.source)
            meta["makeup_label"] = makeup.text()
            meta["makeup_from"] = makeup.source.strftime("%Y-%m-%d") if makeup.source else ""
        elif holiday is not None:
            occurrences = []
        else:
            occurrences = occurrences_on(table, day)
        for occurrence in occurrences:
            by_date.setdefault(key, []).append(_course_card(occurrence, current))
            course_counts[key] = course_counts.get(key, 0) + 1
        meta["festival"] = hint_for(day, calendar)
        day_meta[key] = meta

    event_counts: dict[str, int] = {}
    for event in events:
        day = parse_day(event.date)
        if day is None or day < anchor or day >= anchor + timedelta(days=days):
            continue
        card = _event_card(event, anchor, current)
        # 已经结束的通知不再占版面（用户要求「过期日程自动消失」）。
        # **只对通知生效，不动课程**：课时表是当天的骨架，上午的课下午还要回看，
        # 清掉它整天的结构就散了。
        if hide_past_events and card.state == "past":
            continue
        by_date.setdefault(event.date, []).append(card)
        event_counts[event.date] = event_counts.get(event.date, 0) + 1

    sections: list[DaySection] = []
    for offset in range(days):
        day = anchor + timedelta(days=offset)
        key = day.strftime("%Y-%m-%d")
        meta = day_meta.get(key, {})
        hint = meta.get("festival")
        cards = sorted(by_date.get(key, []), key=lambda card: (_minutes(card.start) is None, _minutes(card.start) or 0, card.title))
        sections.append(DaySection(
            date=key,
            label=f"{day.month}/{day.day}",
            weekday=WEEKDAY_LABELS[day.weekday()],
            relative="今天" if offset == 0 else ("明天" if offset == 1 else ("后天" if offset == 2 else f"{offset}天后")),
            is_today=offset == 0,
            is_weekend=day.weekday() >= 5,
            cards=cards,
            course_count=course_counts.get(key, 0),
            event_count=event_counts.get(key, 0),
            year=day.year,
            show_year=day.year != anchor.year,
            holiday=meta.get("holiday"),
            makeup_from=meta.get("makeup_from", ""),
            makeup_label=meta.get("makeup_label", ""),
            festival=hint.festival if hint is not None else None,
        ))

    next_item: NextItem | None = None
    best: tuple[datetime, Card] | None = None
    for section in sections:
        for card in section.cards:
            if card.state == "past":
                continue
            start_dt = None
            day = parse_day(card.date)
            minutes = _minutes(card.start)
            if day is not None and minutes is not None:
                start_dt = datetime(day.year, day.month, day.day, minutes // 60, minutes % 60)
            if start_dt is None:
                continue
            if best is None or start_dt < best[0]:
                best = (start_dt, card)
    if best is not None:
        start_dt, card = best
        minutes_until = int((start_dt - current).total_seconds() // 60)
        next_item = NextItem(card=card, minutes_until=max(0, minutes_until), started=card.state == "now")

    today_key = anchor.strftime("%Y-%m-%d")
    today_meta = day_meta.get(today_key, {})
    today_hint = today_meta.get("festival")
    return Timeline(
        today=anchor,
        now=current,
        sections=sections,
        next_item=next_item,
        today_event_count=event_counts.get(today_key, 0),
        today_course_count=course_counts.get(today_key, 0),
        total_cards=sum(section.count for section in sections),
        timetable_source=table.source if table is not None else None,
        last_run_text=last_run_text,
        week_label=_week_label(anchor, table),
        holiday=today_meta.get("holiday"),
        makeup=calendar.makeup_on(anchor),
        festival=today_hint.festival if today_hint is not None else None,
        holiday_left=calendar.holiday_days_left(anchor),
        wrap_up=calendar.wrap_up(anchor),
    )


def footer_summary(timeline: Timeline) -> str:
    """底部一行状态：今天 N 节课 + M 条通知；一周共 K 项。"""
    parts = []
    if timeline.holiday is not None:
        left = f"，还剩 {timeline.holiday_left} 天" if timeline.holiday_left else ""
        parts.append(f"今天 放假（{timeline.holiday.name}{left}）")
    elif timeline.makeup is not None:
        parts.append(f"今天 调休（{timeline.makeup.text()}）")
    elif timeline.wrap_up is not None:
        parts.append(f"今天 假期收尾（倒计时 {timeline.wrap_up.remaining} 天）")
    elif timeline.timetable_source:
        parts.append(f"今天 {timeline.today_course_count} 节课")
    parts.append(f"今日通知 {timeline.today_event_count} 条")
    parts.append(f"一周共 {timeline.total_cards} 项")
    return " · ".join(parts)


def day_heading(section: DaySection) -> str:
    """例如 "今天 9/21 周一"；跨年那几天写成 "1/1 2027 周四"。

    调休那行小字（"调休10月7日日程"）**不放在这里**：面板要把它画成小字、
    用强调色才醒目，所以留在 `section.makeup_label` 里给渲染层处理
    （控制台那种纯文本表格会自己拼上去）。
    """
    date_text = f"{section.year}/{section.label}" if section.show_year else section.label
    text = f"{section.relative} {date_text} {section.weekday}"
    if section.holiday is not None:
        text += f" · {section.holiday.name}"
    return text


#: 一天/一周什么都没有时显示的占位文案（课表没导入、也没有通知）
EMPTY_AGENDA_TEXT = "暂无日程安排"

#: 学期里"周次明显偏短"的判定线：最后一节课早于这一周，多半是残缺数据
EARLY_END_WEEK = 8


def empty_hint(table: Timetable | None, calendar: Calendar | None = None,
               today: Date | None = None) -> str:
    """空状态的第二行提示：区分"未导入课表""假期中""假期收尾期"与"这几天无课"。"""
    day = today or Date.today()
    if calendar is not None:
        holiday = calendar.holiday_on(day)
        if holiday is not None:
            left = calendar.holiday_days_left(day)
            if holiday.end > day:
                return (f"{holiday.name}假期中（至 {holiday.end.month}月{holiday.end.day}日，"
                        f"还剩 {left} 天）：假期内不排课，群通知照常显示")
            return f"{holiday.name}假期最后一天（明天恢复上课）"
        wrap_up = calendar.wrap_up(day)
        makeup = calendar.makeup_on(day)
        # 调休优先于收尾倒计时：调休是"今天真的要上课"，比一句倒计时重要
        if makeup is not None and makeup.source is not None:
            return (f"今日为调休：补 {makeup.source.month}月{makeup.source.day}日"
                    f"（{WEEKDAY_LABELS[makeup.source.weekday()]}）的课程")
        if wrap_up is not None:
            return (f"{wrap_up.holiday.name}假期已结束，今天是第 {wrap_up.days_after} 天："
                    f"收心倒计时还剩 {wrap_up.remaining} 天")
    if table is None or not table.courses:
        return "尚未导入课表：请于客户端「课程表 → 从文件识别课表…」导入"
    return "课表在这几天没有安排，亦无待办通知"


def narrow_week_courses(table: Timetable | None, *, minimum: int = EARLY_END_WEEK) -> list[str]:
    """挑出"周次明显偏短"的课名（去重，保序）。

    为什么要这个：从 WakeUp 导出的 .ics 只包含**导出当天前后那几周**的事件，
    于是"数学分析1 = 第3周"这种残缺周次会被当成全部事实写进课表——
    第 4 周之后面板就空了，用户看到的就是"日程不见了"。

    判定用"最后一节课的周次"，不用总周数：像"形势与政策 11-14周"这种
    只上 4 周但排在学期后半的课是正常的，不该被误报。
    """
    if table is None:
        return []
    names: list[str] = []
    for course in table.courses:
        weeks = getattr(course, "weeks", None)
        if not weeks:
            continue
        if max(weeks) < minimum and course.name not in names:
            names.append(course.name)
    return names


def weeks_warning(table: Timetable | None) -> str:
    """控制台课程表页顶部的一句话提醒（没问题时返回空串）。"""
    names = narrow_week_courses(table)
    if not names:
        return ""
    shown = "、".join(names[:4]) + ("…" if len(names) > 4 else "")
    return f"　⚠ 周次可能不全：{shown}（.ics 仅含导出当天前后数周，请核对，或改用教务 / CSV 导入）"
