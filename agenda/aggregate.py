"""一周视图聚合：把事件列表整理成面板直接可渲染的结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date, datetime, timedelta

from .models import RETENTION_DAYS, Event

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def parse_day(value: str) -> Date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def format_day(day: Date) -> str:
    return f"{day.month}月{day.day}日 {WEEKDAY_LABELS[day.weekday()]}"


def relative_day_label(day: Date, today: Date) -> str:
    delta = (day - today).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    if delta < 0:
        return f"{-delta}天前"
    return f"{delta}天后"


def event_start_dt(event: Event) -> datetime | None:
    day = parse_day(event.date)
    if day is None or not event.start:
        return None
    try:
        hour, minute = (int(part) for part in event.start.split(":"))
    except (ValueError, AttributeError):
        return None
    return datetime(day.year, day.month, day.day, hour, minute)


def event_end_dt(event: Event) -> datetime | None:
    start = event_start_dt(event)
    if start is None:
        # 全天事务按当天 23:59 结束
        day = parse_day(event.date)
        if day is None:
            return None
        return datetime(day.year, day.month, day.day, 23, 59)
    if not event.end:
        return start + timedelta(hours=1)
    try:
        hour, minute = (int(part) for part in event.end.split(":"))
    except (ValueError, AttributeError):
        return start + timedelta(hours=1)
    end = start.replace(hour=hour, minute=minute)
    if event.end_date:
        end_day = parse_day(event.end_date)
        if end_day is not None:
            end = datetime(end_day.year, end_day.month, end_day.day, hour, minute)
    if end <= start:
        end += timedelta(days=1)
    return end


@dataclass
class DayGroup:
    date: str
    label: str
    relative: str
    is_today: bool
    is_past: bool
    events: list[Event] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def earliest(self) -> str | None:
        starts = [event.start for event in self.events if event.start]
        return min(starts) if starts else None


@dataclass
class NextEvent:
    event: Event
    minutes_until: int
    started: bool

    def countdown_text(self) -> str:
        if self.started:
            return "进行中"
        minutes = self.minutes_until
        if minutes < 60:
            return f"{minutes} 分钟后"
        hours, rest = divmod(minutes, 60)
        if hours < 24:
            return f"{hours} 小时 {rest} 分后" if rest else f"{hours} 小时后"
        days, hours = divmod(hours, 24)
        return f"{days} 天 {hours} 小时后" if hours else f"{days} 天后"


@dataclass
class AgendaView:
    today: Date
    today_label: str
    now_label: str
    groups: list[DayGroup]
    next_event: NextEvent | None
    total: int
    stale_hint: bool = False
    last_run_text: str | None = None

    def today_group(self) -> DayGroup | None:
        for group in self.groups:
            if group.is_today:
                return group
        return None


def build_view(
    events: list[Event],
    today: Date | None = None,
    *,
    now: datetime | None = None,
    window_days: int = RETENTION_DAYS,
) -> AgendaView:
    """把事件整理成"今天 + 未来一周"的视图。"""
    anchor = today or Date.today()
    current = now or datetime.now()

    grouped: dict[str, list[Event]] = {}
    for event in events:
        day = parse_day(event.date)
        if day is None:
            continue
        if day < anchor:
            # 已过去的日子不展示（保留在库里供复盘）
            continue
        grouped.setdefault(event.date, []).append(event)

    groups: list[DayGroup] = []
    for offset in range(window_days):
        day = anchor + timedelta(days=offset)
        key = day.strftime("%Y-%m-%d")
        day_events = sorted(grouped.get(key, []), key=lambda e: (e.start or "00:00", e.title))
        groups.append(DayGroup(
            date=key,
            label=format_day(day),
            relative=relative_day_label(day, anchor),
            is_today=offset == 0,
            is_past=False,
            events=day_events,
        ))

    next_event: NextEvent | None = None
    best: tuple[datetime, Event] | None = None
    for event in events:
        end = event_end_dt(event)
        start = event_start_dt(event)
        if start is None or end is None:
            continue
        if end < current:
            continue
        if best is None or start < best[0]:
            best = (start, event)
    if best is not None:
        start, event = best
        minutes = int((start - current).total_seconds() // 60)
        end = event_end_dt(event) or start
        next_event = NextEvent(event=event, minutes_until=max(0, minutes), started=start <= current < end)

    today_events = [event for event in events if event.date == anchor.strftime("%Y-%m-%d")]

    return AgendaView(
        today=anchor,
        today_label=format_day(anchor),
        now_label=current.strftime("%H:%M"),
        groups=groups,
        next_event=next_event,
        total=len(today_events),
    )


def week_load(groups: list[DayGroup]) -> str:
    """一周负载概览，例如 "周一 2 · 周二 0 · 周三 3"。"""
    parts = []
    for group in groups[:7]:
        label = WEEKDAY_LABELS[parse_day(group.date).weekday()] if parse_day(group.date) else group.date
        mark = f"{group.earliest} " if group.earliest else ""
        parts.append(f"{label} {mark}{group.count}")
    return " · ".join(parts)
