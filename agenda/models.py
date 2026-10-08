"""日程数据模型。

设计取舍：
- 一天内的时间用 (start, end) 的 "HH:MM" 表示，未指定结束则为 None；
  跨天事务用 end_date 表达，避免把 23:00-01:00 硬塞进同一天。
- date_source 记录日期是怎么来的（相对词/绝对日期/按通知落档），
  面板上会据此标注"待确认"，让"精准"是可验收的而不是自我声称。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date as Date
from typing import Any, Literal

# 一周记忆窗口（含今天）
RETENTION_DAYS = 7

# 低置信度日期来源：面板会打上"待确认"标记
TENTATIVE_DATE_SOURCES = {"notice-fallback"}

DateSource = Literal["absolute", "relative", "weekday", "notice-fallback"]


@dataclass
class Event:
    """一条日程事务。"""

    id: str
    date: str                      # YYYY-MM-DD
    title: str
    start: str | None = None       # HH:MM
    end: str | None = None         # HH:MM
    end_date: str | None = None    # 跨天时的结束日期
    location: str | None = None
    people: tuple[str, ...] = ()
    notes: str | None = None
    group: str | None = None       # 来源群
    sender: str | None = None      # 来源人
    source_ref: str | None = None  # 来源文件 + 行号
    date_source: str = "absolute"
    confidence: float = 0.8
    fingerprint: str = ""
    created_at: str = ""
    updated_at: str = ""
    first_seen: str = ""
    hit_count: int = 1

    @property
    def tentative(self) -> bool:
        """日期是否只是"按通知落档"的兜底推断。"""
        return self.date_source in TENTATIVE_DATE_SOURCES

    @property
    def all_day(self) -> bool:
        return self.start is None

    def time_label(self) -> str:
        if self.start and self.end:
            return f"{self.start}–{self.end}"
        if self.start:
            return self.start
        return "全天"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["people"] = list(self.people)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        payload = dict(data)
        payload["people"] = tuple(payload.get("people") or ())
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in payload.items() if k in known}
        return cls(**payload)


@dataclass
class Candidate:
    """解析中间产物：尚未分配 id / 指纹。"""

    title: str
    date: str
    start: str | None = None
    end: str | None = None
    end_date: str | None = None
    location: str | None = None
    people: tuple[str, ...] = ()
    notes: str | None = None
    group: str | None = None
    sender: str | None = None
    source_ref: str | None = None
    date_source: str = "absolute"
    confidence: float = 0.8

    def to_event(self, event_id: str, fingerprint: str, now_iso: str) -> Event:
        return Event(
            id=event_id,
            date=self.date,
            title=self.title,
            start=self.start,
            end=self.end,
            end_date=self.end_date,
            location=self.location,
            people=self.people,
            notes=self.notes,
            group=self.group,
            sender=self.sender,
            source_ref=self.source_ref,
            date_source=self.date_source,
            confidence=self.confidence,
            fingerprint=fingerprint,
            created_at=now_iso,
            updated_at=now_iso,
            first_seen=now_iso,
            hit_count=1,
        )


@dataclass
class NoticeMessage:
    """从 inbox 文本里切分出来的一条通知。"""

    text: str
    group: str | None = None
    sender: str | None = None
    shift_date: Date | None = None   # 通知落档日期（通常=粘贴当天）
    source_ref: str | None = None
    received_at: str | None = None


@dataclass
class ParseOutcome:
    """一次解析的完整结果，便于调试与面板"为什么这么定"。"""

    candidates: list[Candidate] = field(default_factory=list)
    messages: int = 0
    skipped: int = 0
    diagnostics: list[str] = field(default_factory=list)
