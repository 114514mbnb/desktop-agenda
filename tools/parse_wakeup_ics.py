"""从 WakeUp 导出的 .ics 读取课表（每个事件带绝对时间，星期可信）。

顺带用它标定教务 PDF 的"列→星期"：PDF 只有第1页带表头，
而 ICS 里每门课都有真实星期，两边按课程名一比对就知道每页第一列是星期几。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path


def unfold(text: str) -> list[str]:
    """ICS 长行会折行（续行以空格/Tab 开头），先还原。"""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_events(path: Path) -> list[dict]:
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
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
            continue
        if line.startswith("END:VEVENT"):
            if current:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, _, value = line.partition(":")
        name = key.split(";")[0].upper()
        value = value.replace("\\n", " ").replace("\\,", ",").strip()
        if name in {"SUMMARY", "DTSTART", "DTEND", "LOCATION", "RRULE", "UID"}:
            current.setdefault(name, value)
    for event in events:
        for field in ("DTSTART", "DTEND"):
            raw = event.get(field, "")
            match = re.search(r"(\d{8})T(\d{6})", raw)
            if match:
                event[field + "_DT"] = datetime.strptime(
                    match.group(1) + match.group(2), "%Y%m%d%H%M%S"
                )
    return [event for event in events if "DTSTART_DT" in event]


def main() -> int:
    path = Path(sys.argv[1])
    events = parse_events(path)
    print(f"解析出 {len(events)} 个事件\n")
    weekdays = "一二三四五六日"
    for index, event in enumerate(events[:60], start=1):
        start = event["DTSTART_DT"]
        end = event.get("DTEND_DT")
        span = f"{start:%H:%M}-{end:%H:%M}" if end else f"{start:%H:%M}"
        week = start.isocalendar()[1]
        print(f"{index:3}. 周{weekdays[start.weekday()]} {start:%m-%d} {span}  "
              f"{event.get('SUMMARY', '')!r}  @ {event.get('LOCATION', '')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
