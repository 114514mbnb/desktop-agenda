"""解析内核测试：中文日期/时段/字段抽取 + 去重与一周记忆。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date as Date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import aggregate, extract as extractor, parsing as P  # noqa: E402
from agenda.models import Candidate  # noqa: E402
from agenda.pipeline import Pipeline, is_demo_name  # noqa: E402
from agenda.store import EventStore, fingerprint_for  # noqa: E402

# 2026-09-21 是周一
MONDAY = Date(2026, 9, 21)


class DateParsingTests(unittest.TestCase):
    def check(self, text: str, expected: str, source: str | None = None) -> None:
        result = P.parse_date(text, MONDAY)
        self.assertIsNotNone(result, f"未识别日期: {text}")
        self.assertEqual(result.date, expected, f"{text} → {result.date}，期望 {expected}")
        if source:
            self.assertEqual(result.source, source)

    def test_relative_words(self):
        self.check("今天开会", "2026-09-21", "relative")
        self.check("明天开会", "2026-09-22", "relative")
        self.check("后天开会", "2026-09-23", "relative")
        self.check("大后天开会", "2026-09-24", "relative")

    def test_weekday_words(self):
        # 约定：下X = 下一个自然周的星期X
        self.check("这周五开会", "2026-09-25", "weekday")
        self.check("本周五开会", "2026-09-25", "weekday")
        self.check("周五开会", "2026-09-25", "weekday")
        self.check("下周一交材料", "2026-09-28", "weekday")
        self.check("下周三下午三点", "2026-09-30", "weekday")
        self.check("下周五14:00-16:00开会", "2026-10-02", "weekday")
        self.check("周一例会", "2026-09-28", "weekday")  # 当天周一 → 下一个周一
        self.check("周四答辩", "2026-09-24", "weekday")
        self.check("周日聚会", "2026-09-27", "weekday")

    def test_absolute_dates(self):
        self.check("9月22日开会", "2026-09-22", "absolute")
        self.check("9/22 开会", "2026-09-22", "absolute")
        self.check("2026-09-30 交材料", "2026-09-30", "absolute")
        self.check("2026年10月8日", "2026-10-08", "absolute")
        self.check("20261010 培训", "2026-10-10", "absolute")

    def test_no_date(self):
        self.assertIsNone(P.parse_date("大家记得交材料", MONDAY))


class TimeParsingTests(unittest.TestCase):
    def check(self, text: str, start: str | None, end: str | None) -> None:
        result = P.parse_time(text)
        self.assertEqual((result.start, result.end), (start, end), f"{text} → {result}")

    def test_colon_ranges(self):
        self.check("14:00-15:30", "14:00", "15:30")
        self.check("20:00~21:30", "20:00", "21:30")
        self.check("9:00至11:00", "09:00", "11:00")

    def test_chinese_clocks(self):
        self.check("下午3点", "15:00", None)
        self.check("晚上8点半", "20:30", None)
        self.check("上午9点一刻", "09:15", None)
        self.check("14:00", "14:00", None)

    def test_cross_halfday_range(self):
        self.check("上午9点到晚上6点", "09:00", "18:00")

    def test_chinese_ranges(self):
        self.check("下午3点到5点", "15:00", "17:00")
        self.check("上午9点到11点", "09:00", "11:00")
        self.check("14点到15:30", "14:00", "15:30")

    def test_half_day_defaults(self):
        self.check("明天上午开会", "08:00", "11:00")
        self.check("晚上聚餐", "19:00", "21:00")
        self.check("中午开会", "12:00", "13:00")

    def test_explicit_clock_detection(self):
        self.assertTrue(P.has_explicit_clock("下午3点开会"))
        self.assertFalse(P.has_explicit_clock("明天上午开会"))


class ExtractionTests(unittest.TestCase):
    def extract_one(self, text: str) -> Candidate:
        outcome = extractor.extract(text, fallback_date=MONDAY, source_ref="test.txt")
        self.assertEqual(len(outcome.candidates), 1, f"期望 1 条候选，实际 {len(outcome.candidates)}：{outcome.diagnostics}")
        return outcome.candidates[0]

    def test_full_notice(self):
        text = (
            "[计科2301班群]\n"
            "通知：下周五14:00-16:00在3号楼201会议室召开班级例会，"
            "负责人：张伟老师，请携带笔记本，提前10分钟签到。\n"
        )
        outcome = extractor.extract(
            text, default_group="计科2301班群", fallback_date=MONDAY, source_ref="test.txt",
        )
        self.assertEqual(len(outcome.candidates), 1)
        candidate = outcome.candidates[0]
        self.assertEqual(candidate.date, "2026-10-02")
        self.assertEqual(candidate.start, "14:00")
        self.assertEqual(candidate.end, "16:00")
        self.assertEqual(candidate.location, "3号楼201")
        self.assertIn("张伟", " ".join(candidate.people))
        self.assertIn("笔记本", candidate.notes or "")
        self.assertEqual(candidate.group, "计科2301班群")
        self.assertGreaterEqual(candidate.confidence, 0.8)

    def test_online_meeting_location(self):
        candidate = self.extract_one("明天晚上19:30腾讯会议开班会，会议号123456")
        self.assertEqual(candidate.location, "腾讯会议")
        self.assertEqual(candidate.start, "19:30")


    def test_notice_without_date_is_tentative(self):
        candidate = self.extract_one("通知：今天下午3点在三教302领材料")
        self.assertEqual(candidate.date, "2026-09-21")
        self.assertEqual(candidate.date_source, "relative")
        self.assertEqual(candidate.start, "15:00")
        self.assertIn("三教302", candidate.location or "")

    def test_chatter_is_dropped(self):
        outcome = extractor.extract("哈哈哈笑死我了", fallback_date=MONDAY)
        self.assertEqual(outcome.candidates, [])
        self.assertEqual(outcome.skipped, 1)

    def test_multiple_events_in_one_notice(self):
        text = (
            "[学院大群]\n"
            "9月24日 上午9点 学术报告厅 讲座《人工智能前沿》，主讲：李娜教授。\n\n"
            "9月26日 14:00 体育馆 运动会彩排，请穿运动服。\n"
        )
        outcome = extractor.extract(text, fallback_date=MONDAY)
        self.assertEqual(len(outcome.candidates), 2)
        dates = sorted(item.date for item in outcome.candidates)
        self.assertEqual(dates, ["2026-09-24", "2026-09-26"])

    def test_at_mentions_become_people(self):
        candidate = self.extract_one("@王强 明天10:00 到办公楼302开会")
        self.assertIn("王强", candidate.people)

    def test_location_is_normalized(self):
        """地点要收敛成规范房间名，不能把动作词一起吞进去。"""
        candidate = self.extract_one("明天上午10点到行政楼501交材料，负责人：王芳老师，请携带身份证复印件。")
        self.assertEqual(candidate.location, "行政楼501")
        self.assertEqual(candidate.title, "交材料")
        self.assertEqual(candidate.start, "10:00")
        self.assertEqual(candidate.people, ("王芳",))
        self.assertIn("身份证", candidate.notes or "")

    def test_room_suffix_removed_from_title(self):
        candidate = self.extract_one("下周五14:00 在3号楼201会议室开会")
        self.assertEqual(candidate.location, "3号楼201")
        self.assertEqual(candidate.title, "开会")

    def test_keyword_location_not_eaten_by_speaker(self):
        """"主讲：李娜教授"不能把"主讲"当成地点，学术报告厅要整词命中。"""
        candidate = self.extract_one("9月24日 上午9点 学术报告厅 讲座《多模态大模型前沿》，主讲：李娜教授")
        self.assertEqual(candidate.location, "学术报告厅")
        self.assertIn("讲座", candidate.title)
        self.assertIn("李娜", candidate.people)

    def test_activity_center_full_name(self):
        candidate = self.extract_one("明天上午9点 在大学生活动中心 集合")
        self.assertEqual(candidate.location, "大学生活动中心")
        self.assertEqual(candidate.start, "09:00")

    def test_group_header_becomes_source(self):
        outcome = extractor.extract(
            "[计科2301班群]\n明天下午2点在3号楼201开会", fallback_date=MONDAY,
        )
        self.assertEqual(len(outcome.candidates), 1)
        self.assertEqual(outcome.candidates[0].group, "计科2301班群")
        # 群名行不能被当成地点或标题
        self.assertEqual(outcome.candidates[0].location, "3号楼201")
        self.assertNotIn("计科", outcome.candidates[0].title)


class StoreTests(unittest.TestCase):
    def test_fingerprint_merges_group_forwarding(self):
        a = Candidate(title="班级例会", date="2026-09-25", start="14:00", location="3号楼201", group="A群")
        b = Candidate(title="班级例会通知", date="2026-09-25", start="14:00", location="3号楼201", group="B群")
        self.assertEqual(fingerprint_for(a), fingerprint_for(b))

    def test_merge_dedupes_and_counts_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.json"
            store = EventStore(path)
            candidate = Candidate(title="例会", date="2026-09-25", start="14:00", location="201")
            first = store.merge([candidate])
            second = store.merge([candidate])
            self.assertEqual((first.added, first.unchanged), (1, 0))
            self.assertEqual((second.added, second.unchanged), (0, 1))
            self.assertEqual(store.events[0].hit_count, 2)
            store.save()
            reloaded = EventStore.load(path)
            self.assertEqual(len(reloaded.events), 1)

    def test_prune_keeps_one_week(self):
        store = EventStore(Path(tempfile.gettempdir()) / "unused.json")
        old = Candidate(title="旧事", date="2026-09-10").to_event("ev-old", "fp-old", "2026-09-10 00:00:00")
        recent = Candidate(title="新事", date="2026-09-20").to_event("ev-new", "fp-new", "2026-09-20 00:00:00")
        store.events = [old, recent]
        removed = store.prune(MONDAY, retention_days=7)
        self.assertEqual(removed, 1)
        self.assertEqual([event.id for event in store.events], ["ev-new"])


class AggregateTests(unittest.TestCase):
    def test_next_event_and_groups(self):
        events = [
            Candidate(title="晨会", date="2026-09-21", start="09:00", end="09:30")
            .to_event("a", "fa", "2026-09-21 08:00:00"),
            Candidate(title="晚课", date="2026-09-21", start="19:00")
            .to_event("b", "fb", "2026-09-21 08:00:00"),
            Candidate(title="明天的事", date="2026-09-22", start="10:00")
            .to_event("c", "fc", "2026-09-21 08:00:00"),
        ]
        view = aggregate.build_view(events, MONDAY, now=datetime(2026, 9, 21, 8, 30))
        self.assertEqual(view.today_group().count, 2)
        self.assertIsNotNone(view.next_event)
        self.assertEqual(view.next_event.event.title, "晨会")
        self.assertEqual(view.next_event.minutes_until, 30)
        self.assertIn("30 分钟后", view.next_event.countdown_text())
        self.assertEqual(view.groups[1].relative, "明天")

    def test_started_event_marked(self):
        events = [
            Candidate(title="进行中的会", date="2026-09-21", start="08:00", end="10:00")
            .to_event("a", "fa", "2026-09-21 07:00:00"),
        ]
        view = aggregate.build_view(events, MONDAY, now=datetime(2026, 9, 21, 8, 30))
        self.assertTrue(view.next_event.started)
        self.assertEqual(view.next_event.countdown_text(), "进行中")


class PipelineTests(unittest.TestCase):
    def test_inbox_to_store_to_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline(Path(tmp))
            pipeline.ensure_layout()
            notice = (
                "[实验室群]\n"
                "通知：9月23日 15:00 在实验楼B302 开组会，负责人：陈老师，请准备周报。\n"
            )
            (pipeline.inbox / "lab.txt").write_text(notice, encoding="utf-8")

            report = pipeline.run(today=MONDAY)
            self.assertEqual(report.added, 1)
            self.assertEqual(report.archived_files, ["lab.txt"])
            self.assertEqual(pipeline.inbox_files(), [])

            events = pipeline.load_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].date, "2026-09-23")
            self.assertEqual(events[0].start, "15:00")
            self.assertIn("实验楼B302", events[0].location or "")

            # 同一份通知再粘一次 → 不产生新条目
            (pipeline.inbox / "lab2.txt").write_text(notice, encoding="utf-8")
            second = pipeline.run(today=MONDAY)
            self.assertEqual(second.added, 0)
            self.assertEqual(second.unchanged, 1)
            self.assertEqual(len(pipeline.load_events()), 1)

    def test_ingest_text_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline(Path(tmp))
            report = pipeline.ingest_text(
                "明天上午10点 到行政楼501 交材料，联系人：王老师",
                group="班级群",
                today=MONDAY,
            )
            self.assertEqual(report.added, 1)
            event = report.events[0]
            self.assertEqual(event.date, "2026-09-22")
            self.assertEqual(event.start, "10:00")
            self.assertIn("行政楼501", event.location or "")


class DemoFileGuardTests(unittest.TestCase):
    """示例/演示文件绝不能被当成真通知抓进面板。

    踩过的坑：项目里放过一份 `示例通知-可替换.txt`，它被解析成"开组会""讲座"
    并显示在面板上，用户看到只会以为程序在凭空编日程。
    """

    def test_demo_names_are_recognised(self):
        for name in ("示例通知-可替换.txt", "样例.txt", "demo群通知.txt", "sample notice.md",
                     "example.txt", "Example群消息.MD"):
            with self.subTest(name=name):
                self.assertTrue(is_demo_name(name))

    def test_real_names_pass_through(self):
        for name in ("群通知-20260923.txt", "计科班群消息.txt", "通知2026-09-23.txt", "lab.txt"):
            with self.subTest(name=name):
                self.assertFalse(is_demo_name(name))

    def test_demo_file_is_neither_parsed_nor_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline(Path(tmp))
            pipeline.ensure_layout()
            (pipeline.inbox / "示例通知-可替换.txt").write_text(
                "[实验室群]\n【重要】9月23日 15:00 在实验楼B302 开组会，负责人：陈静老师\n",
                encoding="utf-8",
            )
            report = pipeline.run(today=MONDAY)
            self.assertEqual(report.processed_files, [])
            self.assertEqual(report.candidates, 0)
            self.assertEqual(report.events, [])
            self.assertEqual(pipeline.load_events(), [])
            # 文件留在原地，不动它（用户自己决定删不删）
            self.assertTrue((pipeline.inbox / "示例通知-可替换.txt").exists())


class EventStoreDeleteTests(unittest.TestCase):
    """通知要能一条条删、也能一次清空（课表不在这个库里，不受影响）。"""

    def make_store(self, tmp: str) -> EventStore:
        return EventStore(Path(tmp) / "events.json")

    @staticmethod
    def _seed(store: EventStore) -> None:
        store.merge([
            Candidate(title="开会", date="2026-09-23", start="15:00"),
            Candidate(title="讲座", date="2026-09-24", start="09:00"),
        ])

    def test_remove_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self._seed(store)
            self.assertEqual(len(store.events), 2)
            store.save()
            loaded = EventStore.load(store.path)
            victim = loaded.sorted_events()[0]
            self.assertTrue(loaded.remove(victim.id))
            self.assertFalse(loaded.remove("ev-not-there"))
            loaded.save()
            self.assertEqual([e.title for e in EventStore.load(store.path).events], ["讲座"])

    def test_clear_removes_everything_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self._seed(store)
            store.save()
            self.assertEqual(store.clear(), 2)
            store.save()
            reloaded = EventStore.load(store.path)
            self.assertEqual(reloaded.events, [])
            self.assertEqual(reloaded.clear(), 0)




class DeadlineTitleTests(unittest.TestCase):
    """标题里不能粘着"截止期限"的半截状语。

    真实案例（QQ 群「2026级本科生年级群」里的一条）::

        【提醒】
        1.中秋国庆假期备案：今天中午12:00前，所有学生完成"假期学生去向备案"流程。
        2.思想动态调研：今天之内完成，每个学生都做，提交截图接龙。

    踩过的坑（连环四个）：
      ① `【提醒】` 被当成标题行 → 真正那行的期限没人清洗；
      ② 期限短语的字符类漏了冒号 → `12:00` 匹配不上；
      ③ 只切到锚点 → 标题剩成「…备案：今天」；
      ④ 收尾时按"前"字乱切 → 把「讲座《多模态大模型前沿》」切成「…模型 沿」。
    """

    def extract(self, text: str) -> Candidate:
        outcome = extractor.extract(text, fallback_date=MONDAY, source_ref="qq.txt")
        self.assertTrue(outcome.candidates, f"没解析出候选：{outcome.diagnostics}")
        return outcome.candidates[0]

    def test_label_tag_is_not_the_title(self):
        candidate = self.extract(
            "【提醒】\n"
            '1.中秋国庆假期备案：今天中午12:00前，所有学生完成"假期学生去向备案"流程。\n'
            "2.思想动态调研：今天之内完成，每个学生都做，提交截图接龙。\n"
        )
        self.assertEqual(candidate.title, "中秋国庆假期备案")
        self.assertEqual(candidate.start, "12:00")
        self.assertNotIn("前", candidate.title)
        self.assertNotIn("所有学生完成", candidate.title)

    def test_deadline_phrase_is_not_kept_in_title(self):
        candidate = self.extract("论文初稿：明天中午12:00前交到导师邮箱")
        self.assertEqual(candidate.title, "论文初稿")
        self.assertEqual(candidate.date, "2026-09-22")

    def test_word_containing_qian_is_not_mangled(self):
        """「前沿」里的"前"绝不能被当成截止期限切掉。"""
        candidate = self.extract("9月24日 上午9点 学术报告厅 讲座《多模态大模型前沿》，主讲：李娜教授")
        self.assertIn("前沿", candidate.title)
        self.assertNotIn("模型 沿", candidate.title)

    def test_parenthesised_time_keeps_its_colon(self):
        candidate = self.extract("9月24日 上午9点 学术报告厅 讲座（时间：9:00）")
        self.assertIn("9:00", candidate.title)
        self.assertIn("讲座", candidate.title)

    def test_absolute_date_deadline_is_left_alone(self):
        """绝对日期的句子整句都是流程描述，切了反而更不像话，所以原样留。"""
        candidate = self.extract("请在9月25日17:00前把周报发到群里，负责人：陈静老师")
        self.assertEqual(candidate.start, "17:00")
        self.assertIn("周报", candidate.title)

if __name__ == "__main__":
    unittest.main(verbosity=2)
