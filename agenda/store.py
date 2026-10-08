"""日程存储：JSON 文件 + 指纹去重 + 一周滚动记忆。

为什么不用数据库：一周量级只有几十条，JSON 可直接打开核对、可手改、可备份，
出问题时用户能自己看懂——对"精准"这个要求，透明比性能重要。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta
from pathlib import Path

from .models import RETENTION_DAYS, Candidate, Event

STORE_FILENAME = "events.json"
SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


TITLE_NOISE_RE = re.compile(r"(?:温馨提示|通知|公告|安排|事宜|提醒)$")


def _normalize_title(title: str) -> str:
    value = re.sub(r"[\s\u3000]+", "", title)
    value = re.sub(r"[，。、；：,.;:|~\-—–_\[\]【】()（）]", "", value)
    value = TITLE_NOISE_RE.sub("", value).lower()
    return value[:40]


def fingerprint_for(candidate: Candidate) -> str:
    """稳定指纹：同一天 + 同一时段 + 同一标题 + 同一地点 视为同一条。

    刻意不包含群名与备注——同一条通知被两个群转发时应当合并为一条，
    备注变化也不该产生新条目。
    """
    raw = "|".join([
        candidate.date,
        candidate.start or "",
        _normalize_title(candidate.title),
        candidate.location or "",
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def new_event_id(fingerprint: str, existing: set[str]) -> str:
    base = f"ev-{fingerprint[:10]}"
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

@dataclass
class MergeStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    pruned: int = 0

    def as_text(self) -> str:
        parts = []
        if self.added:
            parts.append(f"新增 {self.added}")
        if self.updated:
            parts.append(f"更新 {self.updated}")
        if self.unchanged:
            parts.append(f"重复 {self.unchanged}")
        if self.pruned:
            parts.append(f"清理过期 {self.pruned}")
        return "，".join(parts) or "无变化"


class EventStore:
    """一周滚动日程库。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.events: list[Event] = []
        self.last_run: dict[str, object] = {}

    # -- 读写 ---------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "EventStore":
        store = cls(path)
        if not store.path.exists():
            return store
        try:
            payload = json.loads(store.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            backup = store.path.with_suffix(".corrupt.json")
            try:
                store.path.replace(backup)
            except OSError:
                pass
            raise RuntimeError(f"{store.path.name} 读取失败（已备份到 {backup.name}）：{error}") from error
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            raise RuntimeError(f"{store.path.name} 版本不匹配：{payload.get('schemaVersion')}")
        store.events = [Event.from_dict(item) for item in payload.get("events", [])]
        store.last_run = payload.get("lastRun") or {}
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "updatedAt": _now_iso(),
            "lastRun": self.last_run,
            "events": [event.to_dict() for event in self.sorted_events()],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        handle, temp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=".events-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    # -- 查询 ---------------------------------------------------------------
    def sorted_events(self) -> list[Event]:
        return sorted(
            self.events,
            key=lambda e: (e.date, e.start or "00:00", e.title),
        )

    def by_date(self, day: Date) -> list[Event]:
        key = day.strftime("%Y-%m-%d")
        return [event for event in self.sorted_events() if event.date == key]

    def all_ids(self) -> set[str]:
        return {event.id for event in self.events}

    # -- 删除 ---------------------------------------------------------------
    def remove(self, event_id: str) -> bool:
        """删掉一条事务；返回是否真的删了。"""
        for index, event in enumerate(self.events):
            if event.id == event_id:
                self.events.pop(index)
                return True
        return False

    def clear(self) -> int:
        """清空全部通知（课表不在这个库里，不受影响）；返回删掉的条数。"""
        count = len(self.events)
        self.events = []
        return count

    # -- 合并与清理 ---------------------------------------------------------
    def merge(self, candidates: list[Candidate]) -> MergeStats:
        stats = MergeStats()
        now = _now_iso()
        by_fingerprint = {event.fingerprint: event for event in self.events}

        for candidate in candidates:
            fingerprint = fingerprint_for(candidate)
            existing = by_fingerprint.get(fingerprint)
            if existing is None:
                event = candidate.to_event(new_event_id(fingerprint, self.all_ids()), fingerprint, now)
                self.events.append(event)
                by_fingerprint[fingerprint] = event
                stats.added += 1
                continue

            changed = self._apply_update(existing, candidate, now)
            if changed:
                stats.updated += 1
            else:
                stats.unchanged += 1
        return stats

    def _apply_update(self, event: Event, candidate: Candidate, now: str) -> bool:
        changed = False
        merged_people = list(event.people)
        for person in candidate.people:
            if person not in merged_people:
                merged_people.append(person)
                changed = True
        fields = {
            "start": candidate.start,
            "end": candidate.end,
            "end_date": candidate.end_date,
            "location": candidate.location,
            "notes": candidate.notes,
            # 群名/发送人只在本次解析确实带上了才覆盖，避免"另一个文件重发同一条"把来源改错
            "group": candidate.group,
            "sender": candidate.sender,
        }
        for name, value in fields.items():
            if value and getattr(event, name) != value:
                if name in {"start", "end"} and getattr(event, name):
                    # 时段只在新信息更具体时覆盖
                    continue
                setattr(event, name, value)
                changed = True
        if tuple(merged_people) != event.people:
            event.people = tuple(merged_people)
        if candidate.confidence > event.confidence:
            event.confidence = candidate.confidence
            event.date_source = candidate.date_source
            changed = True
        event.hit_count += 1
        if changed:
            event.updated_at = now
        return changed

    def prune(self, today: Date | None = None, retention_days: int = RETENTION_DAYS) -> int:
        """丢弃早于记忆窗口的事务；返回清理条数。"""
        anchor = (today or Date.today()) - timedelta(days=retention_days)
        keep: list[Event] = []
        removed = 0
        for event in self.events:
            try:
                event_date = datetime.strptime(event.date, "%Y-%m-%d").date()
            except ValueError:
                removed += 1
                continue
            if event_date < anchor:
                removed += 1
                continue
            keep.append(event)
        self.events = keep
        return removed

    def stats(self) -> dict[str, object]:
        today = Date.today()
        return {
            "total": len(self.events),
            "today": len(self.by_date(today)),
            "earliest": min((e.date for e in self.events), default=None),
            "latest": max((e.date for e in self.events), default=None),
            "lastRun": self.last_run,
        }

    def set_periods(self, periods: tuple[tuple[str, str], ...]) -> None:
        """由客户端在读快照、改节次时间后回写，供下次启动使用。"""
        self.last_run = {**self.last_run, "periods": [[start, end] for start, end in periods]}

    def stored_periods(self) -> tuple[tuple[str, str], ...] | None:
        raw = self.last_run.get("periods") if isinstance(self.last_run, dict) else None
        if not isinstance(raw, list) or not raw:
            return None
        pairs: list[tuple[str, str]] = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((str(item[0]), str(item[1])))
        return tuple(pairs) if pairs else None
