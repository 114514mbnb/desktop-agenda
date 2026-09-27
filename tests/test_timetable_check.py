"""课表体检的测试。

体检的价值全在**判得准**：漏报等于没做，误报会让用户白折腾一遍再也不敢看这个功能。
所以每个规则都配了"该报"和"不该报"两侧的用例——尤其是"只上后半学期的课"这种
**正常现象**，绝不能报成周次异常（实机上误报过）。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date as Date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.timetable import Course, Timetable  # noqa: E402
from agenda.timetable_check import ERROR, WARN, check_timetable  # noqa: E402

WEEK = tuple(range(1, 17))


def _table(courses: list[Course], *, periods: int = 12,
           term_start: Date | None = Date(2026, 9, 7)) -> Timetable:
    table = Timetable(courses=list(courses), term_start=term_start)
    if periods != len(table.periods):
        table.periods = tuple(table.periods[:periods])
    return table


def _titles(report) -> list[str]:
    return [item.title for item in report.findings]


class HealthyTableTests(unittest.TestCase):
    """一份正常课表不该报出任何东西——误报比漏报更劝退。"""

    def test_clean_table_is_healthy(self):
        table = _table([
            Course(name="高等数学", weekday=0, start_period=1, end_period=2, weeks=WEEK,
                   location="A-101", teacher="陈立"),
            Course(name="大学英语", weekday=1, start_period=3, end_period=4, weeks=WEEK,
                   location="A-203", teacher="苏晴"),
        ])
        report = check_timetable(table)
        self.assertTrue(report.healthy, f"正常课表被报了问题：{_titles(report)}")
        self.assertIn("通过", report.summary())

    def test_late_semester_course_is_not_flagged(self):
        """只上后半学期的课是正常的（实测误报过：形势与政策 11-14 周）。"""
        table = _table([
            Course(name="高等数学", weekday=0, start_period=1, end_period=2,
                   weeks=tuple(range(1, 17)), location="A-101", teacher="陈立"),
            Course(name="线性代数", weekday=2, start_period=1, end_period=2,
                   weeks=tuple(range(1, 17)), location="A-105", teacher="李文明"),
            Course(name="大学物理", weekday=3, start_period=5, end_period=6,
                   weeks=tuple(range(1, 17)), location="B-203", teacher="何芳"),
            Course(name="形势与政策", weekday=4, start_period=7, end_period=8,
                   weeks=tuple(range(11, 15)), location="E-402", teacher="曹文超"),
        ])
        report = check_timetable(table)
        self.assertNotIn("周次", "".join(_titles(report)), f"后半学期的课被误报：{_titles(report)}")

    def test_weeks_none_means_every_week_and_is_not_an_error(self):
        table = _table([
            Course(name="体育", weekday=2, start_period=3, end_period=4, weeks=None,
                   location="体育馆", teacher="徐鹏"),
        ])
        report = check_timetable(table)
        self.assertEqual(report.errors, [], "全学期课不该报错")
        self.assertIn("没写周次", "".join(_titles(report)))


class ConflictTests(unittest.TestCase):
    def test_overlapping_courses_in_week_and_period(self):
        table = _table([
            Course(name="高等数学", weekday=0, start_period=1, end_period=2,
                   weeks=tuple(range(1, 9)), location="A-101", teacher="陈立"),
            Course(name="大学物理", weekday=0, start_period=2, end_period=3,
                   weeks=tuple(range(5, 13)), location="B-203", teacher="何芳"),
        ])
        report = check_timetable(table)
        self.assertIn("同一时段有两门课", _titles(report))
        self.assertTrue(any(item.level == ERROR for item in report.findings))

    def test_same_slot_but_disjoint_weeks_is_fine(self):
        """周次不重叠就不算冲突（单双周、前后半学期分段上课都这样）。"""
        table = _table([
            Course(name="高等数学", weekday=0, start_period=1, end_period=2,
                   weeks=(1, 3, 5, 7), location="A-101", teacher="陈立"),
            Course(name="大学物理", weekday=0, start_period=1, end_period=2,
                   weeks=(2, 4, 6, 8), location="B-203", teacher="何芳"),
        ])
        report = check_timetable(table)
        self.assertNotIn("同一时段有两门课", _titles(report))

    def test_same_name_same_slot_is_not_a_conflict(self):
        table = _table([
            Course(name="高等数学", weekday=0, start_period=1, end_period=2, weeks=WEEK,
                   location="A-101", teacher="陈立"),
            Course(name="高等数学", weekday=0, start_period=1, end_period=2, weeks=WEEK,
                   location="A-102", teacher="陈立"),
        ])
        report = check_timetable(table)
        self.assertNotIn("同一时段有两门课", _titles(report))


class PeriodTests(unittest.TestCase):
    def test_period_beyond_the_table(self):
        table = _table([
            Course(name="晚自习", weekday=0, start_period=13, end_period=14, weeks=WEEK,
                   location="A-101", teacher="陈立"),
        ], periods=12)
        report = check_timetable(table)
        self.assertIn("超出「上课时间」表", "".join(_titles(report)))

    def test_reversed_periods(self):
        table = _table([
            Course(name="高等数学", weekday=0, start_period=4, end_period=2, weeks=WEEK,
                   location="A-101", teacher="陈立"),
        ])
        report = check_timetable(table)
        self.assertIn("结束节次小于开始节次", "".join(_titles(report)))

    def test_explicit_times_do_not_need_the_period_table(self):
        table = _table([
            Course(name="讲座", weekday=0, start_period=20, end_period=21, weeks=WEEK,
                   start="19:00", end="20:30", location="报告厅", teacher="李明"),
        ], periods=12)
        report = check_timetable(table)
        self.assertNotIn("超出「上课时间」表", "".join(_titles(report)))


class DuplicateAndParityTests(unittest.TestCase):
    def test_identical_rows_are_flagged(self):
        row = dict(weekday=0, start_period=1, end_period=2, weeks=WEEK,
                   location="A-101", teacher="陈立")
        table = _table([Course(name="高等数学", **row), Course(name="高等数学", **row)])
        report = check_timetable(table)
        self.assertIn("重复出现", "".join(_titles(report)))
        self.assertTrue(any(item.level == WARN for item in report.findings))

    def test_odd_and_even_rows_for_the_same_course(self):
        table = _table([
            Course(name="程序设计基础", weekday=2, start_period=5, end_period=6,
                   weeks=(1, 3, 5, 7), location="D-107", teacher="周宏"),
            Course(name="程序设计基础", weekday=2, start_period=5, end_period=6,
                   weeks=(2, 4, 6, 8), location="D-107", teacher="周宏"),
        ])
        report = check_timetable(table)
        self.assertIn("单周和双周", "".join(_titles(report)))


class WeekRangeTests(unittest.TestCase):
    def test_absurd_week_count(self):
        table = _table([
            Course(name="高等数学", weekday=0, start_period=1, end_period=2,
                   weeks=tuple(range(1, 17)), location="A-101", teacher="陈立"),
            Course(name="线性代数", weekday=2, start_period=1, end_period=2,
                   weeks=tuple(range(1, 17)), location="A-105", teacher="李文明"),
            Course(name="大学物理", weekday=3, start_period=5, end_period=6,
                   weeks=tuple(range(1, 17)), location="B-203", teacher="何芳"),
            Course(name="读错的课", weekday=4, start_period=1, end_period=2,
                   weeks=(1, 2, 33), location="A-101", teacher="陈立"),
        ])
        report = check_timetable(table)
        joined = "".join(_titles(report))
        self.assertTrue("周次" in joined, f"没有报出异常周次：{_titles(report)}")

    def test_outlier_against_the_median(self):
        courses = [Course(name=f"课{index}", weekday=index, start_period=1, end_period=2,
                          weeks=tuple(range(1, 17)), location="A-101", teacher="陈立")
                   for index in range(4)]
        courses.append(Course(name="超长的课", weekday=5, start_period=1, end_period=2,
                              weeks=tuple(range(1, 26)), location="A-101", teacher="陈立"))
        report = check_timetable(_table(courses))
        self.assertIn("明显长于其他课", "".join(_titles(report)))


class FieldTests(unittest.TestCase):
    def test_missing_fields_are_reported_as_hints(self):
        table = _table([
            Course(name="高等数学", weekday=0, start_period=1, end_period=2, weeks=WEEK),
            Course(name="大学英语", weekday=1, start_period=3, end_period=4, weeks=WEEK,
                   location="A-203"),
        ])
        report = check_timetable(table)
        titles = "".join(_titles(report))
        self.assertIn("没读到上课地点", titles)
        self.assertIn("没读到任课教师", titles)
        self.assertTrue(all(item.level == WARN for item in report.findings))

    def test_missing_term_start(self):
        table = _table([Course(name="高等数学", weekday=0, start_period=1, end_period=2,
                               weeks=WEEK, location="A-101", teacher="陈立")],
                       term_start=None)
        report = check_timetable(table)
        self.assertIn("第 1 教学周周一", "".join(_titles(report)))

    def test_empty_timetable(self):
        report = check_timetable(None)
        self.assertFalse(report.healthy)
        self.assertIn("空的", "".join(_titles(report)))
        report = check_timetable(Timetable(courses=[]))
        self.assertIn("空的", "".join(_titles(report)))


class DirtyNameTests(unittest.TestCase):
    """课名里混进教务字段——这是最常见的识别错误，也是最伤观感的一种。"""

    def test_lecture_hours_inside_the_name(self):
        table = _table([
            Course(name="解析几何 讲课:48 48 48", weekday=0, start_period=1, end_period=2,
                   weeks=WEEK, location="A-101", teacher="李文明"),
        ])
        report = check_timetable(table)
        self.assertIn("教务内部字段", "".join(_titles(report)))

    def test_course_code_as_name(self):
        table = _table([
            Course(name="D206020600", weekday=0, start_period=1, end_period=2,
                   weeks=WEEK, location="A-101", teacher="李文明"),
        ])
        report = check_timetable(table)
        self.assertIn("教务内部字段", "".join(_titles(report)))

    def test_class_list_as_name(self):
        table = _table([
            Course(name="应数1班;应数2班", weekday=0, start_period=1, end_period=2,
                   weeks=WEEK, location="A-101", teacher="李文明"),
        ])
        report = check_timetable(table)
        self.assertIn("教务内部字段", "".join(_titles(report)))

    def test_normal_names_are_not_flagged(self):
        for name in ("高等数学", "C++程序设计实验", "大学英语（视听说）", "体育（羽毛球）"):
            with self.subTest(name=name):
                table = _table([Course(name=name, weekday=0, start_period=1, end_period=2,
                                       weeks=WEEK, location="A-101", teacher="陈立")])
                report = check_timetable(table)
                self.assertNotIn("教务内部字段", "".join(_titles(report)),
                                 f"正常课名被误报：{name}")


class ReportTextTests(unittest.TestCase):
    def test_text_report_is_copyable_and_complete(self):
        table = _table([
            Course(name="高等数学", weekday=0, start_period=1, end_period=2, weeks=WEEK,
                   location="A-101", teacher="陈立"),
            Course(name="大学物理", weekday=0, start_period=1, end_period=2, weeks=WEEK,
                   location="B-203", teacher="何芳"),
        ])
        report = check_timetable(table)
        text = report.as_text(course_count=len(table.courses), term_start=table.term_start)
        self.assertIn("课表体检", text)
        self.assertIn("课程数：2", text)
        self.assertIn("第 1 教学周周一：2026-09-07", text)
        self.assertIn("同一时段有两门课", text)
        self.assertIn("✗", text)          # 错误用 ✗，提示用 ⚠，用户一眼能分

    def test_healthy_report_text_says_so(self):
        table = _table([Course(name="高等数学", weekday=0, start_period=1, end_period=2,
                               weeks=WEEK, location="A-101", teacher="陈立")])
        text = check_timetable(table).as_text(course_count=1)
        self.assertIn("通过", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
