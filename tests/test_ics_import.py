"""WakeUp .ics 导入的测试。

.ics 里有绝对时间，是"星期/节次"最可靠的来源；这些行为一旦退化，
用户看到的就是"课排到了错的日子"，所以全部钉死。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import import_wakeup_ics as ics_tool  # noqa: E402
import merge_schedule as merge_tool  # noqa: E402

SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//YZune//WakeUpSchedule//EN
BEGIN:VEVENT
UID:WakeUpSchedule-1
SUMMARY:数学分析1
DTSTART;TZID=Asia/Shanghai:20260923T080000
DTEND;TZID=Asia/Shanghai:20260923T095000
RRULE:FREQ=WEEKLY;UNTIL=20270112T160000Z;INTERVAL=1
LOCATION:中心校区 A-330 陈立
END:VEVENT
BEGIN:VEVENT
UID:WakeUpSchedule-2
SUMMARY:大学英语1
DTSTART;TZID=Asia/Shanghai:20260928T140000
DTEND;TZID=Asia/Shanghai:20260928T155000
RRULE:FREQ=WEEKLY;UNTIL=20261130T160000Z;INTERVAL=1
LOCATION:中心校区 A-308 苏晴
END:VEVENT
BEGIN:VEVENT
UID:WakeUpSchedule-3
SUMMARY:美术鉴赏
DTSTART;TZID=Asia/Shanghai:20260921T161000
DTEND;TZID=Asia/Shanghai:20260921T180000
RRULE:FREQ=WEEKLY;UNTIL=20260927T160000Z;INTERVAL=1
LOCATION:中心校区 E-402 郑新明
END:VEVENT
END:VCALENDAR
"""


class IcsParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "课表.ics"
        self.path.write_text(SAMPLE_ICS, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_events_parsed_with_absolute_time(self):
        events = ics_tool.parse_events(self.path)
        self.assertEqual(len(events), 3)
        first = events[0]
        self.assertEqual(first["summary"], "数学分析1")
        self.assertEqual(first["start"], datetime(2026, 9, 23, 8, 0))
        self.assertEqual(first["end"], datetime(2026, 9, 23, 9, 50))

    def test_location_splits_place_and_teacher(self):
        events = ics_tool.parse_events(self.path)
        place, teacher = ics_tool.clean_location(events[0]["location"])
        self.assertEqual(place, "中心校区 A-330")
        self.assertEqual(teacher, "陈立")

    def test_teaching_week_anchor(self):
        # 2026-09-07 是第 1 教学周周一；09-23 属于第 3 周
        self.assertEqual(ics_tool.teaching_week(date(2026, 9, 7)), 1)
        self.assertEqual(ics_tool.teaching_week(date(2026, 9, 13)), 1)
        self.assertEqual(ics_tool.teaching_week(date(2026, 9, 14)), 2)
        self.assertEqual(ics_tool.teaching_week(date(2026, 9, 23)), 3)

    def test_period_of_absolute_time(self):
        self.assertEqual(ics_tool.period_of(datetime(2026, 9, 23, 8, 0)), 1)
        self.assertEqual(ics_tool.period_of(datetime(2026, 9, 23, 9, 50)), 2)
        self.assertEqual(ics_tool.period_of(datetime(2026, 9, 23, 16, 10)), 7)
        self.assertEqual(ics_tool.period_of(datetime(2026, 9, 23, 20, 50)), 10)

    def test_rrule_expansion_covers_until(self):
        events = ics_tool.parse_events(self.path)
        english = next(e for e in events if e["summary"] == "大学英语1")
        days = ics_tool.expand_dates(english)
        weeks = {ics_tool.teaching_week(day) for day in days}
        self.assertIn(4, weeks)                       # 首次 09-28 = 第 4 教学周
        self.assertEqual(min(weeks), 4)
        self.assertGreater(len(weeks), 3)             # 按 UNTIL 展开成多周，而不是只有一周


class MergeTests(unittest.TestCase):
    def test_name_alias_and_week_union(self):
        # PDF 里"军事理论"是每周一条，周次必须并起来而不是只取最长那条
        self.assertEqual(merge_tool.normalize("数学分析"), "数学分析1")
        weeks = merge_tool.expand_weeks("9周")
        self.assertEqual(weeks, (9,))
        self.assertEqual(merge_tool.expand_weeks("1-5、7-11单"), (1, 3, 5, 7, 9, 11))
        self.assertEqual(merge_tool.expand_weeks("4-16周(双)"), (4, 6, 8, 10, 12, 14, 16))

    def test_weeks_text_round_trip(self):
        for text in ("1-16", "1-5、7-11单", "4-16(双)"):
            weeks = merge_tool.expand_weeks(text)
            self.assertEqual(merge_tool.expand_weeks(merge_tool.weeks_text(weeks)), weeks)

    def test_bucket_expands_rrule_not_just_dtstart(self):
        """只看 DTSTART 会把整门课写成"只有第 3 周"——第 4 周之后面板就空了。"""
        events = ics_tool.parse_events(Path(self._write(SAMPLE_ICS)))
        merged = merge_tool.bucket_events(events)
        math_key = next(key for key in merged if key[0] == "数学分析1")
        weeks = sorted(merged[math_key]["weeks"])
        self.assertEqual(min(weeks), 3)          # DTSTART 2026-09-23 = 第 3 周
        self.assertGreater(len(weeks), 10)       # UNTIL 20270112 → 一直排到期末
        self.assertIn(4, weeks)                  # 下一周也必须算进去

    def test_bucket_keeps_teacher_and_place(self):
        events = ics_tool.parse_events(Path(self._write(SAMPLE_ICS)))
        merged = merge_tool.bucket_events(events)
        math_key = next(key for key in merged if key[0] == "数学分析1")
        self.assertEqual(math_key[4], "中心校区 A-330")
        self.assertEqual(merged[math_key]["teacher"], "陈立")

    def _write(self, text: str) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "课表.ics"
        path.write_text(text, encoding="utf-8")
        return str(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
