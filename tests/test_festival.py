"""节日彩蛋：日期表、触发条件、文案安全、以及它跟假期/调休的联动。

用户原话：「在中秋节，元旦，春节，国庆节等重要传统节日适当做些小彩蛋，
凸显一定的节日气氛和该节日特点，彩蛋的触发条件为节日当天以及相应法定假期内，
交付前仔细检查并连同每个节日的彩蛋一同交付给我」。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date as Date
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.festival import (  # noqa: E402
    FESTIVAL_WINDOW_DAYS,
    TEMPLATES,
    festival_for,
    festival_occurrences,
    hint_for,
    occurrences_near,
)
from agenda.holidays import Calendar, Holiday, Makeup, WRAP_UP_DAYS  # noqa: E402
from agenda.timeline import build_timeline, day_heading, empty_hint, footer_summary  # noqa: E402
from agenda.timetable import load_timetable  # noqa: E402

#: 各字号都能画出来的符号（tools/font_probe.py 实测过）——
#: 🧧🏮 这类新 emoji 在小字号下会变成豆腐块，所以只许用这些。
SAFE_SYMBOLS = set("★☆✦✧❋✺❁❀✿☾☽♥♪❄☃♣♦♠·、。，： —－")


class FestivalDateTests(unittest.TestCase):
    def test_2026_dates_are_right(self):
        days = {item.key: item.day for item in festival_occurrences(2026)}
        self.assertEqual(days["newyear"], Date(2026, 1, 1))
        self.assertEqual(days["spring"], Date(2026, 2, 17))
        self.assertEqual(days["lantern"], Date(2026, 3, 3))     # 正月十五 = 春节 + 14 天
        self.assertEqual(days["qingming"], Date(2026, 4, 5))
        self.assertEqual(days["labor"], Date(2026, 5, 1))
        self.assertEqual(days["dragon"], Date(2026, 6, 19))
        self.assertEqual(days["midautumn"], Date(2026, 9, 25))
        self.assertEqual(days["national"], Date(2026, 10, 1))

    def test_every_supported_year_has_every_festival(self):
        for year in range(2024, 2036):
            keys = {item.key for item in festival_occurrences(year)}
            self.assertEqual(keys, {item.key for item in TEMPLATES}, f"{year} 年缺节日")

    def test_spring_festival_moves_every_year(self):
        days = [next(item.day for item in festival_occurrences(year) if item.key == "spring")
                for year in range(2024, 2027)]
        self.assertEqual(days, [Date(2024, 2, 10), Date(2025, 1, 29), Date(2026, 2, 17)])

    def test_occurrences_near_spans_years(self):
        found = {item.key for item in occurrences_near(Date(2026, 1, 1))}
        self.assertIn("newyear", found)


class FestivalTriggerTests(unittest.TestCase):
    def test_exact_day_triggers(self):
        for festival in festival_occurrences(2026):
            got = festival_for(festival.day)
            self.assertIsNotNone(got, f"{festival.name} 当天没触发")
            self.assertEqual(got.key, festival.key)

    def test_holiday_range_triggers_the_same_festival(self):
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8))])
        for offset in range(8):
            day = Date(2026, 10, 1 + offset)
            got = festival_for(day, calendar)
            self.assertIsNotNone(got, f"{day} 在国庆假期里却没了彩蛋")
            self.assertEqual(got.key, "national")

    def test_midautumn_break_also_triggers(self):
        calendar = Calendar(holidays=[Holiday("中秋节", Date(2026, 9, 25), Date(2026, 9, 27))])
        self.assertEqual(festival_for(Date(2026, 9, 26), calendar).key, "midautumn")
        self.assertEqual(festival_for(Date(2026, 9, 27), calendar).key, "midautumn")

    def test_normal_day_has_no_egg(self):
        """离任何节日都超过 3 天的日子：没有彩蛋（窗口见 FESTIVAL_WINDOW_DAYS）。"""
        self.assertIsNone(festival_for(Date(2026, 11, 15)))
        self.assertIsNone(festival_for(Date(2026, 11, 15), Calendar()))

    def test_window_extends_before_and_after_the_festival(self):
        """用户要求"范围能大一点"：节日前后各 3 天也进入节日状态。"""
        festival = Date(2026, 9, 25)          # 中秋
        for offset in range(-FESTIVAL_WINDOW_DAYS, FESTIVAL_WINDOW_DAYS + 1):
            day = festival + timedelta(days=offset)
            with self.subTest(offset=offset):
                got = festival_for(day)
                self.assertIsNotNone(got, f"{day} 应该在彩蛋窗口里")
                self.assertEqual(got.key, "midautumn")

    def test_window_does_not_reach_beyond_it(self):
        """前后各 3 天之外就不该再触发了（挑一个邻居节日都很远的：端午）。"""
        festival = Date(2026, 6, 19)
        self.assertIsNone(festival_for(festival - timedelta(days=FESTIVAL_WINDOW_DAYS + 1)))
        self.assertIsNone(festival_for(festival + timedelta(days=FESTIVAL_WINDOW_DAYS + 1)))

    def test_hint_reports_the_offset(self):
        self.assertEqual(hint_for(Date(2026, 9, 25)).offset, 0)
        self.assertEqual(hint_for(Date(2026, 9, 23)).offset, -2)
        self.assertEqual(hint_for(Date(2026, 9, 27)).offset, 2)
        self.assertEqual(hint_for(Date(2026, 9, 25)).timing_text(), "")
        self.assertEqual(hint_for(Date(2026, 9, 23)).timing_text(), "还有 2 天")
        self.assertEqual(hint_for(Date(2026, 9, 27)).timing_text(), "已过 2 天")

    def test_hint_switches_to_the_countdown_inside_the_wrap_up(self):
        """收尾期里横幅括号写的应该是"收心倒计时 N 天"，而不是"已过 8 天"。"""
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 7))])
        hint = hint_for(Date(2026, 10, 9), calendar)
        self.assertEqual(hint.wrap_up_remaining, 2)
        self.assertEqual(hint.timing_text(), "收心倒计时 2 天")

    def test_nearest_festival_wins_when_two_are_close(self):
        """国庆 10/1 和中秋 9/25 差 6 天；9/29 离国庆更近，就该是国庆。"""
        self.assertEqual(festival_for(Date(2026, 9, 29)).key, "national")
        self.assertEqual(festival_for(Date(2026, 9, 27)).key, "midautumn")

    # -- 合并假期区间（"中秋国庆连放"）------------------------------------
    def _merged(self):
        """用户真实配置：一条区间把中秋和国庆并在一起。"""
        return Calendar(holidays=[Holiday("中秋国庆连放", Date(2026, 9, 25), Date(2026, 10, 7))])

    def test_merged_range_splits_between_its_festivals(self):
        """合并区间要**按各节日自己的天数分段**，不能第一个节日吃掉整段。

        用户反馈：「今天都已经中秋第四天了，中秋节的彩蛋是不是应该取消了，
        应该恢复原样了吧？」——旧行为下 9/28 还在放中秋，甚至国庆假期里的 10/5
        显示的是"中秋 已过 10 天"，国庆自己的假期反而没有国庆。
        """
        calendar = self._merged()
        self.assertEqual(festival_for(Date(2026, 9, 25), calendar).key, "midautumn")
        self.assertEqual(festival_for(Date(2026, 10, 1), calendar).key, "national")
        self.assertEqual(festival_for(Date(2026, 10, 7), calendar).key, "national")

    def test_merged_range_leaves_the_gap_days_plain(self):
        """中秋和国庆之间那几天不属于任何节日 → 恢复原样，一点彩蛋都不留。"""
        calendar = self._merged()
        for day in (Date(2026, 9, 26), Date(2026, 9, 27), Date(2026, 9, 28),
                    Date(2026, 9, 29), Date(2026, 9, 30)):
            with self.subTest(day=day):
                self.assertIsNone(festival_for(day, calendar),
                                  f"{day} 不该再有彩蛋（用户要求中秋过了就恢复原样）")
                self.assertIsNone(hint_for(day, calendar))

    def test_merged_range_still_announces_before_it_starts(self):
        """区间**之前**的日子照旧提前预告（9/22–9/24「还有 N 天」）。"""
        calendar = self._merged()
        for offset, text in ((-3, "还有 3 天"), (-2, "还有 2 天"), (-1, "还有 1 天")):
            day = Date(2026, 9, 25) + timedelta(days=offset)
            with self.subTest(day=day):
                hint = hint_for(day, calendar)
                self.assertIsNotNone(hint)
                self.assertEqual(hint.festival.key, "midautumn")
                self.assertEqual(hint.timing_text(), text)

    def test_merged_range_keeps_the_wrap_up_after_it_ends(self):
        """区间结束后仍走收心倒计时。"""
        calendar = self._merged()
        hint = hint_for(Date(2026, 10, 9), calendar)
        self.assertIsNotNone(hint)
        self.assertEqual(hint.festival.key, "national")
        self.assertTrue(hint.timing_text().startswith("收心倒计时"))

    def test_single_festival_holiday_still_covers_the_whole_range(self):
        """只对得上一个节日的假期，整段仍然是它的气氛（原行为不能丢）。"""
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 7))])
        for day in (Date(2026, 10, 3), Date(2026, 10, 6), Date(2026, 10, 7)):
            with self.subTest(day=day):
                self.assertEqual(festival_for(day, calendar).key, "national")
        # 春节一路到元宵那种长假同理
        spring = Calendar(holidays=[Holiday("春节", Date(2027, 2, 6), Date(2027, 2, 12))])
        self.assertEqual(festival_for(Date(2027, 2, 11), spring).key, "spring")

    def test_several_spans_are_read_from_the_festival_itself(self):
        """各节日占几天取自它自己的 `suggested_span`，不是写死的窗口。"""
        calendar = self._merged()
        midautumn = festival_for(Date(2026, 9, 25), calendar)
        national = festival_for(Date(2026, 10, 1), calendar)
        self.assertEqual(midautumn.suggested_span, (0, 0), "中秋应当只有一天")
        self.assertEqual(national.suggested_span, (0, 6), "国庆应当是七天")

    def test_holiday_too_far_from_the_festival_does_not_trigger_it(self):
        """假期名叫"国庆节"但排在三月 —— 不许因为这个假名放出国庆彩蛋。"""
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 3, 1), Date(2026, 3, 3))])
        got = festival_for(Date(2026, 3, 2), calendar)
        self.assertNotEqual(getattr(got, "key", None), "national")

    def test_unrelated_holiday_does_not_trigger(self):
        """假期名跟任何节日都对不上，而且离所有节日都远 → 没有彩蛋。"""
        calendar = Calendar(holidays=[Holiday("校庆假期", Date(2026, 11, 10), Date(2026, 11, 12))])
        self.assertIsNone(festival_for(Date(2026, 11, 11), calendar))

    def test_egg_survives_the_wrap_up_window(self):
        """假期结束后的收尾期内仍然保留节日状态（和收心倒计时配套）。"""
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 7))])
        for day in (Date(2026, 10, 8), Date(2026, 10, 9), Date(2026, 10, 10)):
            with self.subTest(day=day):
                self.assertEqual(festival_for(day, calendar).key, "national")
        self.assertIsNone(festival_for(Date(2026, 10, 12), calendar))

    def test_hint_reports_being_inside_the_holiday(self):
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8))])
        hint = hint_for(Date(2026, 10, 3), calendar)
        self.assertTrue(hint.in_holiday)
        self.assertEqual(hint.holiday_name, "国庆节")
        self.assertIn("国庆节", hint.banner_text)
        self.assertIn("★", hint.banner_text)

    def test_hint_on_the_day_but_outside_the_holiday(self):
        hint = hint_for(Date(2026, 10, 1))
        self.assertIsNotNone(hint)
        self.assertFalse(hint.in_holiday)


class FestivalCopyTests(unittest.TestCase):
    """文案本身的自查（"交付前仔细检查"那条）。"""

    def test_every_festival_has_copy(self):
        for festival in TEMPLATES:
            self.assertTrue(festival.name)
            self.assertTrue(festival.greeting, f"{festival.name} 没有祝福语")
            self.assertTrue(festival.egg, f"{festival.name} 没有彩蛋文案")
            self.assertTrue(festival.particles, f"{festival.name} 没有装饰图元")
            for color in (festival.accent, festival.banner_bg, festival.banner_fg):
                self.assertRegex(color, r"^#[0-9A-Fa-f]{6}$", f"{festival.name} 颜色写错了：{color}")

    def test_no_emoji_in_visible_text(self):
        """小字号下 emoji 会画成豆腐块，所以正文里一个都不许有。

        实测（`tools/font_probe.py`）：9pt 时 🧧🏮 直接是空框，
        而横幅正文/彩蛋都是 9pt 上下。气氛交给 Canvas 图元去做。
        BMP 里的符号（★ ✦ ☾ 这些）实测各字号都正常，所以只禁星形平面的 emoji
        和 VS16/ZWJ 这类组合用的控制字符。
        """
        banned_ranges = ((0x1F000, 0x1FAFF), (0xFE00, 0xFE0F), (0x200D, 0x200D))
        for festival in TEMPLATES:
            for label, text in (("祝福语", festival.greeting), ("彩蛋", festival.egg),
                                ("符号", festival.emoji)):
                for char in text:
                    point = ord(char)
                    for low, high in banned_ranges:
                        self.assertFalse(
                            low <= point <= high,
                            f"{festival.name} 的{label}里有 emoji「{char}」（{hex(point)}），"
                            "小字号会变成豆腐块",
                        )

    def test_headline_symbol_is_from_the_safe_set(self):
        for festival in TEMPLATES:
            for char in festival.emoji:
                self.assertIn(char, SAFE_SYMBOLS, f"{festival.name} 用了没验证过的符号「{char}」")

    def test_countdown_text_has_no_risky_glyphs(self):
        """收尾倒计时那行也不能出现画不出来的符号（⏳ 这类实测是豆腐块）。"""
        from agenda.holidays import Holiday, HolidayWrapUp

        wrap_up = HolidayWrapUp(holiday=Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 7)),
                                days_after=1, remaining=3)
        for text in (wrap_up.short(), wrap_up.detail()):
            for char in text:
                point = ord(char)
                self.assertFalse(0x1F000 <= point <= 0x1FAFF, f"{text} 里有 emoji「{char}」")
                self.assertNotEqual(point, 0x23F3, "⏳ 在这个字体下是豆腐块")

    def test_every_festival_is_distinct(self):
        keys = [item.key for item in TEMPLATES]
        names = [item.name for item in TEMPLATES]
        self.assertEqual(len(set(keys)), len(keys))
        self.assertEqual(len(set(names)), len(names))

    def test_the_four_requested_festivals_are_present(self):
        names = {item.name for item in TEMPLATES}
        for wanted in ("中秋节", "元旦", "春节", "国庆节"):
            self.assertIn(wanted, names)

    def test_particle_kinds_are_supported_by_the_banner(self):
        supported = {"moon", "star", "spark", "firework", "lantern", "wave", "boat", "drop",
                     "gear", "text"}
        for festival in TEMPLATES:
            for particle in festival.particles:
                self.assertIn(particle.kind, supported, f"{festival.name} 用了没实现的图元")


class TimelineCalendarTests(unittest.TestCase):
    """假期压课、调休补课、年份显示 —— 都在时间线这一层验。"""

    ROWS = [
        {"name": "数学分析1", "weekday": "周三", "period": "1-2", "location": "A-330",
         "teacher": "陈立", "weeks": "1-16"},
        {"name": "解析几何", "weekday": "周三", "period": "9-10", "location": "B-101",
         "teacher": "李文明", "weeks": "1-16"},
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        import json
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-05",
            "periods": [["08:00", "09:50"], ["10:10", "12:00"]],
            "courses": self.ROWS,
        }, ensure_ascii=False), encoding="utf-8")
        self.table = load_timetable(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def _event(self, day: str):
        from agenda.models import Event
        return Event(id="e1", title="交材料", date=day, start="10:00", end="11:00")

    def test_holiday_drops_courses_but_keeps_notices(self):
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8))])
        # 2026-10-07 是周三，本该有两门课
        plain = build_timeline([], self.table, today=Date(2026, 10, 7))
        self.assertEqual(plain.sections[0].course_count, 2)
        timeline = build_timeline([self._event("2026-10-07")], self.table,
                                  today=Date(2026, 10, 7), calendar=calendar)
        section = timeline.sections[0]
        self.assertEqual(section.course_count, 0, "假期里不该排课")
        self.assertIsNotNone(section.holiday)
        self.assertEqual(section.holiday.name, "国庆节")
        self.assertEqual(section.event_count, 1, "群通知必须照常显示")
        self.assertIn("交材料", [card.title for card in section.cards])

    def test_makeup_replays_the_source_day(self):
        calendar = Calendar(makeups=[Makeup(Date(2026, 10, 10), Date(2026, 10, 7))])
        timeline = build_timeline([], self.table, today=Date(2026, 10, 10), calendar=calendar)
        section = timeline.sections[0]
        # 2026-10-10 是周六（本来没课）；补的是周三 10-07 的课
        self.assertEqual(section.course_count, 2)
        self.assertEqual(section.makeup_label, "调休10月7日日程")
        self.assertEqual(section.makeup_from, "2026-10-07")
        self.assertEqual({card.title for card in section.cards}, {"数学分析1", "解析几何"})
        # 卡片记的是**调休日**的日期，不是被补的那天
        self.assertEqual({card.date for card in section.cards}, {"2026-10-10"})

    def test_makeup_keeps_course_index_for_right_click_edit(self):
        calendar = Calendar(makeups=[Makeup(Date(2026, 10, 10), Date(2026, 10, 7))])
        timeline = build_timeline([], self.table, today=Date(2026, 10, 10), calendar=calendar)
        indexes = {card.title: card.course_index for card in timeline.sections[0].cards}
        self.assertEqual(indexes["数学分析1"], 0)
        self.assertEqual(indexes["解析几何"], 1)

    def test_makeup_wins_over_holiday_on_the_same_day(self):
        """既在放假区间里、又安排了补课：按补课算（学生确实要去上课）。"""
        calendar = Calendar(
            holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 10))],
            makeups=[Makeup(Date(2026, 10, 10), Date(2026, 10, 7))],
        )
        timeline = build_timeline([], self.table, today=Date(2026, 10, 10), calendar=calendar)
        section = timeline.sections[0]
        self.assertEqual(section.course_count, 2)
        self.assertEqual(section.makeup_label, "调休10月7日日程")

    def test_custom_label_is_used(self):
        calendar = Calendar(makeups=[Makeup(Date(2026, 10, 10), Date(2026, 10, 7), "补周三")])
        timeline = build_timeline([], self.table, today=Date(2026, 10, 10), calendar=calendar)
        self.assertEqual(timeline.sections[0].makeup_label, "补周三")

    def test_makeup_without_source_shows_a_plain_label(self):
        calendar = Calendar(makeups=[Makeup(Date(2026, 10, 10), None)])
        timeline = build_timeline([], self.table, today=Date(2026, 10, 10), calendar=calendar)
        section = timeline.sections[0]
        self.assertEqual(section.makeup_label, "调休")
        self.assertEqual(section.course_count, 0, "不知道补哪天就不该瞎排课")

    def test_year_shows_up_across_new_year(self):
        early = build_timeline([], None, today=Date(2026, 12, 30), days=4)
        later = [section for section in early.sections if section.date.startswith("2027")]
        self.assertTrue(later, "跨年那几天应该在时间线里")
        self.assertTrue(later[0].show_year)
        self.assertIn("2027", day_heading(later[0]))
        today_section = early.sections[0]
        self.assertFalse(today_section.show_year)
        self.assertNotIn("2026/", day_heading(today_section))

    def test_week_label_is_hidden_before_the_term_starts(self):
        """学期还没开始（2 月）不该显示"第 -28 教学周"。"""
        timeline = build_timeline([], self.table, today=Date(2026, 2, 17))
        self.assertEqual(timeline.week_label, "")
        autumn = build_timeline([], self.table, today=Date(2026, 11, 1))
        self.assertTrue(autumn.week_label.startswith("第 "))

    def test_footer_says_holiday_and_makeup(self):
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8))],
                            makeups=[Makeup(Date(2026, 10, 10), Date(2026, 10, 7))])
        holiday = build_timeline([], self.table, today=Date(2026, 10, 2), calendar=calendar)
        self.assertIn("放假（国庆节", footer_summary(holiday))
        self.assertIn("还剩 7 天", footer_summary(holiday))
        makeup = build_timeline([], self.table, today=Date(2026, 10, 10), calendar=calendar)
        self.assertIn("调休", footer_summary(makeup))

    def test_empty_hint_explains_holiday_and_makeup(self):
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8))],
                            makeups=[Makeup(Date(2026, 10, 10), Date(2026, 10, 7))])
        hint = empty_hint(self.table, calendar, Date(2026, 10, 2))
        self.assertIn("国庆节假期中", hint)
        self.assertIn("群通知照常显示", hint)
        self.assertIn("还剩 7 天", hint)
        makeup_hint = empty_hint(self.table, calendar, Date(2026, 10, 10))
        self.assertIn("调休", makeup_hint)
        self.assertIn("10月7日", makeup_hint)

    def test_holiday_left_counts_the_last_day(self):
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 8))])
        self.assertEqual(calendar.holiday_days_left(Date(2026, 10, 1)), 8)
        self.assertEqual(calendar.holiday_days_left(Date(2026, 10, 8)), 1)
        self.assertEqual(calendar.holiday_days_left(Date(2026, 10, 9)), 0)
        timeline = build_timeline([], self.table, today=Date(2026, 10, 8), calendar=calendar)
        self.assertEqual(timeline.holiday_left, 1)
        self.assertIn("最后一天", empty_hint(self.table, calendar, Date(2026, 10, 8)))

    def test_wrap_up_countdown_runs_for_three_days(self):
        """假期结束后三天：按天数递减的收心倒计时，第四天起彻底结束。"""
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 7))])
        self.assertIsNone(calendar.wrap_up(Date(2026, 10, 7)), "假期最后一天还不是收尾期")
        expected = [(1, 3), (2, 2), (3, 1)]
        for days_after, remaining in expected:
            day = Date(2026, 10, 7) + timedelta(days=days_after)
            with self.subTest(day=day):
                wrap_up = calendar.wrap_up(day)
                self.assertIsNotNone(wrap_up, f"{day} 应该在收尾期里")
                self.assertEqual(wrap_up.days_after, days_after)
                self.assertEqual(wrap_up.remaining, remaining)
                self.assertIn(f"收心倒计时 {remaining} 天", wrap_up.short())
        self.assertIsNone(calendar.wrap_up(Date(2026, 10, 7) + timedelta(days=WRAP_UP_DAYS + 1)))

    def test_wrap_up_shows_in_hint_footer_and_timeline(self):
        calendar = Calendar(holidays=[Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 7))])
        day = Date(2026, 10, 8)
        timeline = build_timeline([], self.table, today=day, calendar=calendar)
        self.assertIsNotNone(timeline.wrap_up)
        self.assertEqual(timeline.wrap_up.remaining, 3)
        self.assertIsNone(timeline.holiday)
        self.assertIn("收心倒计时还剩 3 天", empty_hint(self.table, calendar, day))
        self.assertIn("假期收尾（倒计时 3 天）", footer_summary(timeline))

    def test_holiday_status_column_values(self):
        holiday = Holiday("国庆节", Date(2026, 10, 1), Date(2026, 10, 7))
        calendar = Calendar(holidays=[holiday])
        self.assertEqual(calendar.holiday_status(holiday, Date(2026, 9, 30)), "未开始")
        self.assertEqual(calendar.holiday_status(holiday, Date(2026, 10, 3)), "进行中")
        self.assertEqual(calendar.holiday_status(holiday, Date(2026, 10, 9)), "收尾中")
        self.assertEqual(calendar.holiday_status(holiday, Date(2026, 11, 1)), "已结束")

    def test_finished_holidays_excludes_active_ones(self):
        calendar = Calendar(holidays=[Holiday("旧的", Date(2026, 1, 1), Date(2026, 1, 3)),
                                      Holiday("新的", Date(2026, 11, 1), Date(2026, 11, 3))])
        finished = calendar.finished_holidays(Date(2026, 6, 1))
        self.assertEqual([item.name for item in finished], ["旧的"])

    def test_timeline_carries_today_festival_and_holiday(self):
        calendar = Calendar(holidays=[Holiday("中秋节", Date(2026, 9, 25), Date(2026, 9, 27))])
        timeline = build_timeline([], self.table, today=Date(2026, 9, 26), calendar=calendar)
        self.assertIsNotNone(timeline.festival)
        self.assertEqual(timeline.festival.name, "中秋节")
        self.assertIsNotNone(timeline.holiday)
        section = timeline.today_section()
        self.assertEqual(section.festival.name, "中秋节")

    def test_no_calendar_still_works(self):
        """没配假期时（calendar.json 不存在）行为跟以前完全一样。"""
        timeline = build_timeline([], self.table, today=Date(2026, 10, 7))
        self.assertEqual(timeline.sections[0].course_count, 2)
        self.assertEqual(timeline.sections[0].makeup_label, "")
        self.assertIsNone(timeline.holiday)


if __name__ == "__main__":
    unittest.main(verbosity=2)
