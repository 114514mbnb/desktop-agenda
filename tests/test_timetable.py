"""课程表与时间线测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date as Date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.models import Candidate  # noqa: E402
from agenda.timeline import (  # noqa: E402
    EMPTY_AGENDA_TEXT,
    build_timeline,
    day_heading,
    empty_hint,
    footer_summary,
    narrow_week_courses,
    weeks_warning,
)
from agenda.timetable import (  # noqa: E402
    DEFAULT_PERIODS,
    Timetable,
    expand_timetable,
    load_timetable,
    parse_csv_timetable,
    parse_json_timetable,
    parse_text_timetable,
    parse_weeks,
)

JSON_SAMPLE = """
{
  "termStart": "2026-09-07",
  "courses": [
    {"name": "高等数学", "weekday": "周一", "period": "1-2", "location": "教三301",
     "teacher": "张伟老师", "weeks": "1-16周"},
    {"name": "数据结构", "weekday": "周一", "period": "3-4", "location": "实验楼B302"},
    {"name": "体育", "weekday": "周三", "period": "3-4", "location": "体育馆", "weeks": "2-16周(单)"},
    {"name": "选修课", "weekday": "周五", "start": "19:30", "end": "21:00", "location": "线上"}
  ]
}
"""

CSV_SAMPLE = """课程,星期,节次,教室,老师,周次
高等数学,周一,1-2,教三301,张伟老师,1-16周
大学英语,周二,5-6,外语楼204,Linda,1-16周
"""

TEXT_SAMPLE = """
# 周次 节次 课程 教室 老师
周一 第1-2节 高等数学 教三301 张伟老师 1-16周
周三 第3-4节 体育 体育馆 赵磊老师 2-16周
"""


class WeekParsingTests(unittest.TestCase):
    def test_ranges_and_parity(self):
        self.assertEqual(parse_weeks("1-4周"), (1, 2, 3, 4))
        self.assertEqual(parse_weeks("1-8周(单)"), (1, 3, 5, 7))
        self.assertEqual(parse_weeks("2-8周(双)"), (2, 4, 6, 8))
        self.assertEqual(parse_weeks("1,3,5周"), (1, 3, 5))
        self.assertIsNone(parse_weeks("每周"))
        self.assertIsNone(parse_weeks(""))


class TimetableParsingTests(unittest.TestCase):
    def test_json(self):
        table = parse_json_timetable(__import__("json").loads(JSON_SAMPLE))
        self.assertEqual(len(table.courses), 4)
        self.assertEqual(table.term_start, Date(2026, 9, 7))
        first = table.courses[0]
        self.assertEqual(first.name, "高等数学")
        self.assertEqual(first.weekday, 0)
        self.assertEqual(first.times(table.periods), ("08:00", "09:40"))
        sport = [c for c in table.courses if c.name == "体育"][0]
        self.assertEqual(sport.weeks, (3, 5, 7, 9, 11, 13, 15))
        elective = [c for c in table.courses if c.name == "选修课"][0]
        self.assertEqual(elective.times(table.periods), ("19:30", "21:00"))

    def test_csv(self):
        table = parse_csv_timetable(CSV_SAMPLE)
        self.assertEqual([c.name for c in table.courses], ["高等数学", "大学英语"])
        self.assertEqual(table.courses[1].weekday, 1)
        self.assertEqual(table.courses[1].location, "外语楼204")

    def test_text(self):
        table = parse_text_timetable(TEXT_SAMPLE)
        self.assertEqual([c.name for c in table.courses], ["高等数学", "体育"])
        self.assertEqual(table.courses[1].weeks, (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16))

    def test_load_prefers_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "timetable.json").write_text(JSON_SAMPLE, encoding="utf-8")
            (root / "timetable.csv").write_text(CSV_SAMPLE, encoding="utf-8")
            table = load_timetable(root)
            self.assertIsNotNone(table)
            self.assertEqual(table.source, "timetable.json")

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_timetable(Path(tmp)))


class PeriodTimeTests(unittest.TestCase):
    """节次 → 时间的映射。

    用户实测报过的 bug：第4-5节被算成 10:55-14:45（跨过午休），
    原因是错取了"第5节之后那一节"的结束时间。
    """

    TABLE = (
        ("08:00", "08:45"), ("08:55", "09:40"),
        ("10:00", "10:45"), ("10:55", "11:40"),
        ("14:00", "14:45"), ("14:55", "15:40"),
    )

    def test_range_spans_first_start_to_last_end(self):
        from agenda.timetable import Course

        course = Course(name="中国近现代史纲要", weekday=0, start_period=4, end_period=5)
        self.assertEqual(course.times(self.TABLE), ("10:55", "14:45"))

    def test_single_period(self):
        from agenda.timetable import Course

        course = Course(name="解析几何", weekday=2, start_period=4, end_period=4)
        self.assertEqual(course.times(self.TABLE), ("10:55", "11:40"))

    def test_out_of_range_period_is_none(self):
        from agenda.timetable import Course

        course = Course(name="x", weekday=0, start_period=9, end_period=10)
        self.assertEqual(course.times(self.TABLE), (None, None))


class ExpansionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = parse_json_timetable(__import__("json").loads(JSON_SAMPLE))

    def test_week_number(self):
        # 2026-09-07 是第 1 周周一，09-21 是第 3 周周一
        self.assertEqual(self.table.week_of(Date(2026, 9, 7)), 1)
        self.assertEqual(self.table.week_of(Date(2026, 9, 21)), 3)

    def test_expand_one_week(self):
        occurrences = expand_timetable(self.table, Date(2026, 9, 21), 7)
        names = [o.course.name for o in occurrences]
        self.assertIn("高等数学", names)
        self.assertIn("数据结构", names)
        # 体育是单周课，第 3 周应出现
        self.assertIn("体育", names)
        monday = [o for o in occurrences if o.course.name == "高等数学"][0]
        self.assertEqual(monday.start, "08:00")
        self.assertEqual(monday.end, "09:40")

    def test_even_week_excludes_odd_course(self):
        occurrences = expand_timetable(self.table, Date(2026, 9, 28), 7)  # 第 4 周
        self.assertNotIn("体育", [o.course.name for o in occurrences])


class TimelineTests(unittest.TestCase):
    def test_timeline_merges_courses_and_notices(self):
        table = parse_json_timetable(__import__("json").loads(JSON_SAMPLE))
        events = [
            Candidate(
                title="班级例会", date="2026-09-21", start="14:00", end="15:30",
                location="3号楼201", people=("张伟",), notes="带笔记本", group="计科班群",
            ).to_event("ev-1", "fp-1", "2026-09-21 08:00:00"),
        ]
        timeline = build_timeline(
            events, table, today=Date(2026, 9, 21),
            now=datetime(2026, 9, 21, 8, 30), days=2,
        )
        today = timeline.today_section()
        self.assertIsNotNone(today)
        titles = [card.title for card in today.cards]
        self.assertIn("高等数学", titles)
        self.assertIn("班级例会", titles)
        # 08:30 时第一节课正在进行
        ongoing = [card for card in today.cards if card.state == "now"]
        self.assertTrue(ongoing)
        self.assertGreater(ongoing[0].progress, 0)
        # 08:30 时第一节课还在上，下一项应当就是"进行中"的它
        self.assertEqual(timeline.next_item.card.title, "高等数学")
        self.assertTrue(timeline.next_item.started)
        self.assertEqual(timeline.next_item.text(), "进行中")
        self.assertIn("今天", day_heading(today))
        self.assertIn("一周共", footer_summary(timeline))

    def test_timeline_without_timetable_still_works(self):
        events = [
            Candidate(title="交材料", date="2026-09-21", location="行政楼501")
            .to_event("ev-2", "fp-2", "2026-09-21 08:00:00"),
        ]
        timeline = build_timeline(events, None, today=Date(2026, 9, 21))
        self.assertEqual(timeline.today_event_count, 1)
        card = timeline.today_section().cards[0]
        self.assertEqual(card.time_label, "全天")
        self.assertIsNone(timeline.next_item)  # 全天事项不参与倒计时


class EmptyStateTests(unittest.TestCase):
    """课表没导入 / 这几天没课：面板必须给一句明确的话，而不是一片空白。"""

    def test_nothing_at_all_is_empty(self):
        timeline = build_timeline([], None, today=Date(2026, 9, 21))
        self.assertEqual(timeline.total_cards, 0)
        self.assertEqual(EMPTY_AGENDA_TEXT, "暂无日程安排")
        self.assertIn("尚未导入课表", empty_hint(None))

    def test_empty_table_is_treated_as_empty(self):
        table = Timetable(courses=[], source="timetable.json", term_start="2026-09-07")
        timeline = build_timeline([], table, today=Date(2026, 9, 21))
        self.assertEqual(timeline.total_cards, 0)
        self.assertIn("尚未导入课表", empty_hint(table))

    def test_table_but_no_class_this_week(self):
        """周次全在别的周 → 一周空白，但提示要说清是"这几天没安排"。"""
        table = parse_json_timetable({"termStart": "2026-09-07", "courses": [
            {"name": "体育", "weekday": "周一", "period": "3-4", "weeks": "1-2"},
        ]})
        timeline = build_timeline([], table, today=Date(2026, 9, 21))   # 第 3 周
        self.assertEqual(timeline.total_cards, 0)
        self.assertIn("没有安排", empty_hint(table))

    def test_a_real_card_means_not_empty(self):
        table = parse_json_timetable({"termStart": "2026-09-07", "courses": [
            {"name": "数学分析1", "weekday": "周三", "period": "1-2", "weeks": "3-19"},
        ]})
        timeline = build_timeline([], table, today=Date(2026, 9, 23))   # 周三第 3 周
        self.assertGreater(timeline.total_cards, 0)
        self.assertIn("数学分析1", [c.title for c in timeline.today_section().cards])


class NarrowWeekWarningTests(unittest.TestCase):
    """从 .ics 导入的课常常只有"导出当天那几周"，必须提示出来。"""

    def test_narrow_weeks_are_flagged(self):
        table = parse_json_timetable({"courses": [
            {"name": "数学分析1", "weekday": "周三", "period": "1-2", "weeks": "3"},
            {"name": "高等代数1", "weekday": "周二", "period": "9-10", "weeks": "4-19"},
        ]})
        self.assertEqual(narrow_week_courses(table), ["数学分析1"])
        self.assertIn("数学分析1", weeks_warning(table))

    def test_full_term_weeks_are_not_flagged(self):
        table = parse_json_timetable({"courses": [
            {"name": "高等代数1", "weekday": "周二", "period": "9-10", "weeks": "4-19"},
            {"name": "形势与政策", "weekday": "周四", "period": "9-10", "weeks": "11-14"},
        ]})
        self.assertEqual(narrow_week_courses(table), [])
        self.assertEqual(weeks_warning(table), "")

    def test_weekly_course_has_nothing_to_flag(self):
        table = parse_json_timetable({"courses": [
            {"name": "体育", "weekday": "周一", "period": "3-4"},
        ]})
        self.assertEqual(narrow_week_courses(table), [])

    def test_missing_table_is_safe(self):
        self.assertEqual(narrow_week_courses(None), [])
        self.assertEqual(weeks_warning(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
