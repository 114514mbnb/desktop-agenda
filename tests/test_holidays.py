"""假期 / 调休：数据模型、解析、放假通知识别。

用户原话：「增加一个假期时间以及调休修改功能，我可以自定义时间假期时间，
处于假期时间，日程表不显示课程表内容，但是显示群聊通知信息。调休功能可以将
任意时间段的日程安排改为调休日的任务，在我给的日期旁边增加小字（调休X月X日日程）」
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date as Date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.holidays import (  # noqa: E402
    CALENDAR_NAME,
    Calendar,
    Holiday,
    Makeup,
    friendly,
    load_calendar,
    parse_date,
    parse_date_range,
    suggested_holidays,
)
from agenda.holiday_parse import merge_into, parse_holiday_notice  # noqa: E402


class ParseDateTests(unittest.TestCase):
    def test_accepts_the_ways_people_write_dates(self):
        want = Date(2026, 10, 1)
        for text in ("2026-10-01", "2026/10/1", "2026.10.01", "2026年10月1日",
                     "2026-10-01 ", " 2026-10-01"):
            self.assertEqual(parse_date(text), want, text)

    def test_month_day_defaults_to_a_year(self):
        self.assertEqual(parse_date("10月1日", default_year=2026), Date(2026, 10, 1))
        self.assertEqual(parse_date("10-01", default_year=2026), Date(2026, 10, 1))
        self.assertIsNone(parse_date("十月一号", default_year=2026))
        self.assertIsNone(parse_date("", default_year=2026))

    def test_impossible_dates_are_rejected(self):
        self.assertIsNone(parse_date("2026-02-30"))
        self.assertIsNone(parse_date("2026-13-01"))

    def test_date_ranges(self):
        self.assertEqual(parse_date_range("10月1日至10月8日", default_year=2026),
                         (Date(2026, 10, 1), Date(2026, 10, 8)))
        self.assertEqual(parse_date_range("2026-10-01~2026-10-08"),
                         (Date(2026, 10, 1), Date(2026, 10, 8)))
        # 结束那半只写"8日"（同一个月的简写）
        self.assertEqual(parse_date_range("10月1日-8日", default_year=2026),
                         (Date(2026, 10, 1), Date(2026, 10, 8)))
        self.assertEqual(parse_date_range("10月1日", default_year=2026),
                         (Date(2026, 10, 1), Date(2026, 10, 1)))

    def test_reversed_range_is_normalized(self):
        self.assertEqual(parse_date_range("10月8日-10月1日", default_year=2026),
                         (Date(2026, 10, 1), Date(2026, 10, 8)))


class HolidayModelTests(unittest.TestCase):
    def test_days_and_covers_are_inclusive(self):
        holiday = Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8))
        self.assertEqual(holiday.days, 8)
        self.assertTrue(holiday.covers(Date(2026, 10, 1)))
        self.assertTrue(holiday.covers(Date(2026, 10, 8)))
        self.assertFalse(holiday.covers(Date(2026, 9, 30)))
        self.assertFalse(holiday.covers(Date(2026, 10, 9)))

    def test_single_day_label(self):
        holiday = Holiday("元旦", Date(2026, 1, 1), Date(2026, 1, 1))
        self.assertEqual(holiday.days, 1)
        self.assertIn("1月1日", holiday.label())
        self.assertNotIn("–", holiday.label())

    def test_makeup_default_small_text(self):
        makeup = Makeup(Date(2026, 10, 10), Date(2026, 10, 7))
        self.assertEqual(makeup.text(), "调休10月7日日程")
        self.assertEqual(Makeup(Date(2026, 10, 10), None).text(), "调休")
        self.assertEqual(Makeup(Date(2026, 10, 10), Date(2026, 10, 7), "补周三的课").text(),
                         "补周三的课")

    def test_friendly(self):
        self.assertEqual(friendly(Date(2026, 10, 7)), "10月7日")


class CalendarStoreTests(unittest.TestCase):
    def test_round_trip_through_json(self):
        calendar = Calendar(
            holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8), "校历")],
            makeups=[Makeup(Date(2026, 10, 10), Date(2026, 10, 7))],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = calendar.save(Path(tmp))
            self.assertEqual(path.name, CALENDAR_NAME)
            again = load_calendar(Path(tmp))
        self.assertEqual(len(again.holidays), 1)
        self.assertEqual(again.holidays[0].name, "国庆节")
        self.assertEqual(again.holidays[0].start, Date(2026, 10, 1))
        self.assertEqual(again.holidays[0].end, Date(2026, 10, 8))
        self.assertEqual(again.makeups[0].source, Date(2026, 10, 7))
        self.assertTrue(again.holiday_on(Date(2026, 10, 3)))
        self.assertIsNotNone(again.makeup_on(Date(2026, 10, 10)))

    def test_missing_file_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            calendar = load_calendar(Path(tmp))
        self.assertTrue(calendar.empty)
        self.assertIsNone(calendar.holiday_on(Date(2026, 10, 1)))

    def test_broken_file_is_ignored(self):
        """手改坏了不能把面板拖垮。"""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / CALENDAR_NAME).write_text("{不是 json", encoding="utf-8")
            self.assertTrue(load_calendar(Path(tmp)).empty)

    def test_payload_tolerates_alternative_keys_and_bad_rows(self):
        payload = {
            "holidays": [
                {"name": "五一", "from": "2026-05-01", "to": "2026-05-05"},
                {"name": "没日期", "start": ""},
                "不是字典",
            ],
            "makeups": [{"day": "2026-05-09", "from": "2026-05-05"}, {"date": "?"}],
        }
        calendar = Calendar.from_payload(payload)
        self.assertEqual(len(calendar.holidays), 1)
        self.assertEqual(calendar.holidays[0].start, Date(2026, 5, 1))
        self.assertEqual(len(calendar.makeups), 1)
        self.assertEqual(calendar.makeups[0].source, Date(2026, 5, 5))

    def test_add_replaces_same_name_and_start(self):
        calendar = Calendar()
        calendar.add_holiday(Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 7)))
        calendar.add_holiday(Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8)))
        self.assertEqual(len(calendar.holidays), 1)
        self.assertEqual(calendar.holidays[0].end, Date(2026, 10, 8))

    def test_add_makeup_replaces_same_day(self):
        calendar = Calendar()
        calendar.add_makeup(Makeup(Date(2026, 10, 10), Date(2026, 10, 7)))
        calendar.add_makeup(Makeup(Date(2026, 10, 10), Date(2026, 10, 8)))
        self.assertEqual(len(calendar.makeups), 1)
        self.assertEqual(calendar.makeups[0].source, Date(2026, 10, 8))

    def test_remove_by_index(self):
        calendar = Calendar(holidays=[Holiday("A", Date(2026, 1, 1), Date(2026, 1, 1)),
                                      Holiday("B", Date(2026, 2, 1), Date(2026, 2, 1))])
        self.assertTrue(calendar.remove_holiday(0))
        self.assertEqual([item.name for item in calendar.holidays], ["B"])
        self.assertFalse(calendar.remove_holiday(5))

    def test_sorted_output(self):
        calendar = Calendar(holidays=[Holiday("晚", Date(2026, 10, 1), Date(2026, 10, 1)),
                                      Holiday("早", Date(2026, 1, 1), Date(2026, 1, 1))])
        self.assertEqual([item.name for item in calendar.sorted_holidays()], ["早", "晚"])

    def test_holiday_named_matches_loosely(self):
        calendar = Calendar(holidays=[Holiday("国庆节、中秋节", Date(2026, 10, 1),
                                              Date(2026, 10, 8))])
        self.assertIsNotNone(calendar.holiday_named("国庆节"))
        self.assertIsNotNone(calendar.holiday_named("中秋"))
        self.assertIsNone(calendar.holiday_named("劳动节"))


class SuggestedHolidayTests(unittest.TestCase):
    def test_suggestions_cover_the_festivals_of_that_year(self):
        suggestions = suggested_holidays(2026)
        names = {item.name for item in suggestions}
        self.assertIn("国庆节", names)
        self.assertIn("春节", names)
        national = next(item for item in suggestions if item.name == "国庆节")
        self.assertEqual(national.start, Date(2026, 10, 1))
        self.assertGreaterEqual(national.days, 7)

    def test_suggestions_say_they_are_a_draft(self):
        for holiday in suggested_holidays(2026):
            self.assertIn("预填", holiday.note)
            self.assertIn("为准", holiday.note)

    def test_lantern_festival_is_not_a_statutory_holiday(self):
        self.assertNotIn("元宵节", {item.name for item in suggested_holidays(2026)})


class HolidayNoticeParseTests(unittest.TestCase):
    def test_state_council_style_notice(self):
        text = ("根据国务院办公厅通知，现将 2026 年国庆节放假安排通知如下："
                "10月1日至10月8日放假调休，共8天。"
                "10月10日（星期六）上班。")
        result = parse_holiday_notice(text, year=2026)
        self.assertEqual(len(result.holidays), 1)
        self.assertEqual(result.holidays[0].start, Date(2026, 10, 1))
        self.assertEqual(result.holidays[0].end, Date(2026, 10, 8))
        self.assertIn("国庆", result.holidays[0].name)
        self.assertEqual([item.date for item in result.makeups], [Date(2026, 10, 10)])
        # 通知没写补哪天 —— 必须留空并提醒，不能猜
        self.assertIsNone(result.makeups[0].source)
        self.assertTrue(any("没说补哪天" in line for line in result.warnings))

    def test_school_style_notice_with_makeup_source(self):
        text = ("2026年国庆节放假安排：10月1日至10月8日放假，共8天。"
                "10月10日（星期六）上班，补10月7日（星期三）的课。")
        result = parse_holiday_notice(text)
        self.assertEqual(len(result.makeups), 1)
        self.assertEqual(result.makeups[0].date, Date(2026, 10, 10))
        self.assertEqual(result.makeups[0].source, Date(2026, 10, 7))
        self.assertEqual(result.makeups[0].text(), "调休10月7日日程")

    def test_multiple_makeup_days_in_one_sentence(self):
        text = "10月1日至10月8日放假。9月27日（星期日）、10月10日（星期六）上班。"
        result = parse_holiday_notice(text, year=2026)
        self.assertEqual([item.date for item in result.makeups],
                         [Date(2026, 9, 27), Date(2026, 10, 10)])

    def test_single_day_holiday(self):
        text = "2026年元旦：1月1日放假1天，不调休。"
        result = parse_holiday_notice(text)
        self.assertEqual(len(result.holidays), 1)
        holiday = result.holidays[0]
        self.assertEqual((holiday.start, holiday.end), (Date(2026, 1, 1), Date(2026, 1, 1)))
        self.assertIn("元旦", holiday.name)

    def test_year_is_inferred_for_january_notices(self):
        """九月以后发的通知里写"1月1日"，那一天是明年。"""
        result = parse_holiday_notice("元旦：1月1日放假1天。", default_year=2026)
        self.assertEqual(result.holidays[0].start, Date(2026, 1, 1))

    def test_garbage_returns_a_clear_warning(self):
        result = parse_holiday_notice("今天天气不错，去打球吗？")
        self.assertEqual(result.count, 0)
        self.assertTrue(result.warnings)

    def test_summary_lists_everything(self):
        result = parse_holiday_notice("10月1日至10月8日放假。10月10日上班，补10月7日的课。", year=2026)
        summary = result.summary()
        self.assertIn("假期 1 段", summary)
        self.assertIn("10月7日", summary)

    def test_merge_into_calendar(self):
        result = parse_holiday_notice("10月1日至10月8日放假。10月10日上班，补10月7日的课。", year=2026)
        calendar = Calendar()
        added = merge_into(calendar, result)
        self.assertEqual(added, 2)
        self.assertTrue(calendar.is_holiday(Date(2026, 10, 5)))
        self.assertEqual(calendar.makeup_on(Date(2026, 10, 10)).source, Date(2026, 10, 7))

    def test_round_trip_through_json_payload(self):
        """from_payload 要能吃自己 to_payload 出来的东西。"""
        calendar = Calendar(holidays=[Holiday("春假", Date(2026, 4, 1), Date(2026, 4, 3))],
                            makeups=[Makeup(Date(2026, 4, 4), Date(2026, 4, 2), "补周四")])
        payload = json.loads(json.dumps(calendar.to_payload(), ensure_ascii=False))
        again = Calendar.from_payload(payload)
        self.assertEqual(again.holidays[0].name, "春假")
        self.assertEqual(again.makeups[0].label, "补周四")


if __name__ == "__main__":
    unittest.main(verbosity=2)
