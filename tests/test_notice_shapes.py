"""班级通知的真实形状：日期区间 + 每日 + 呼语 + 客套话。

用户报的原话：「为什么这种格式的通知无法被录入？」
给的是这条：

    请各班临时负责人，9月25日‑28日期间，每日上午11:00前统计本班当日留校人员名单，
    并报送至本群。辛苦各位按时完成，感谢配合。

它同时踩了四个坑，任何一个都会让整条通知消失或变成乱码标题：
  1. **「感谢」被当成水群词** → 整条丢弃（最严重，四个字毁掉一条通知）；
  2. 区间连字符是 **U+2011**（非断行连字符），只认 ASCII `-` 就认不出区间；
  3. 「区间 + 每日」的落点（见下面的 `DailyRangeNoticeTests`）；
  4. 标题里会残留呼语（"请各班临时"）、周期词（"每日"）、客套话（"辛苦…感谢配合"）。

关于第 3 点，行为改过一回，记下来免得以后又改回去：
最初是**逐日展开**（9/25–9/28 每天一条）。用户看到之后的原话是
「没有必要把后面空闲的时间都连带调出，直接把任务结束的那天就行了」——
这类"每天报送一次"的临时任务是一条**有截止日**的事，不是 4 件独立的事；
摊开会把区间里本来空闲的日子全填上同名卡片，7 天视图里全是重复行。
现在**只记一条、落在区间最后一天**，区间和"每日"都写进备注。

下面每个坑一条用例，全部照用户原文的形状写（含那个 U+2011 字符）。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import extract as extractor  # noqa: E402
from agenda import parsing  # noqa: E402

#: 用户给的那条原文（连字符是 U+2011，不是 ASCII 减号）
NOTICE_U2011 = (
    "请各班临时负责人，9月25日\u201128日期间，每日上午11:00前统计本班当日留校人员名单，"
    "并报送至本群。辛苦各位按时完成，感谢配合。"
)
NOTICE_ASCII = NOTICE_U2011.replace("\u2011", "-")


def _parse(text: str, base: date = date(2026, 9, 24)):
    return extractor.extract(text, fallback_date=base, source_ref="测试")


class DailyRangeNoticeTests(unittest.TestCase):
    """用户那条通知的完整形状。"""

    def test_polite_closing_does_not_discard_the_notice(self):
        """「感谢」不能把整条通知变成水群消息——这是最要命的那条。"""
        for text in (NOTICE_U2011, NOTICE_ASCII):
            with self.subTest(text=text[:20]):
                outcome = _parse(text)
                self.assertEqual(len(outcome.candidates), 1,
                                 f"应当记成一条，实际 {len(outcome.candidates)}：{outcome.diagnostics}")

    def test_every_dash_variant_works(self):
        """连字符变体（U+2011 / – / — / － / ～ / 至 / 到）都要认。"""
        for char in ("\u2011", "-", "\u2013", "\u2014", "－", "～", "~", "至", "到"):
            with self.subTest(char=repr(char)):
                text = NOTICE_U2011.replace("\u2011", char)
                outcome = _parse(text)
                self.assertEqual(len(outcome.candidates), 1,
                                 f"分隔符 {char!r} 没认出来：{outcome.diagnostics}")
                # 认出来了就说明区间起点没错：日期落在区间最后一天
                self.assertEqual(outcome.candidates[0].date, "2026-09-28")

    def test_daily_range_lands_on_the_last_day(self):
        """「每日」的区间只记**一条**，日期取区间最后一天（截止日）。

        用户原话：「没有必要把后面空闲的时间都连带调出，
        直接把时间显示的任务结束的那天就行了」。
        """
        outcome = _parse(NOTICE_U2011)
        self.assertEqual([item.date for item in outcome.candidates], ["2026-09-28"])

    def test_time_and_notes_are_kept(self):
        """区间信息一点不能丢：哪几天、每天几点、哪天截止，都要能在卡片上看到。"""
        outcome = _parse(NOTICE_U2011)
        item = outcome.candidates[0]
        self.assertEqual(item.start, "11:00")
        notes = item.notes or ""
        self.assertIn("连续事项", notes)
        self.assertIn("09月25日", notes)
        self.assertIn("09月28日", notes)
        self.assertIn("每日", notes)

    def test_title_is_clean(self):
        """标题里不该有呼语、周期词和客套话。"""
        title = _parse(NOTICE_U2011).candidates[0].title
        self.assertIn("统计", title)
        for junk in ("请各班", "负责人", "每日", "辛苦", "感谢", "期间", "11:00"):
            with self.subTest(junk=junk):
                self.assertNotIn(junk, title)

    def test_cross_month_range(self):
        outcome = _parse("请各班注意，9月28日-10月2日期间，每日上午11:00前统计留校人员名单，感谢配合。")
        self.assertEqual([item.date for item in outcome.candidates], ["2026-10-02"])
        self.assertIn("统计", outcome.candidates[0].title)
        self.assertIn("10月02日", outcome.candidates[0].notes or "")

    def test_range_without_daily_stays_one_item(self):
        """没有「每日」时，区间只记开始那一天（并在备注里保留区间信息）。"""
        outcome = _parse("培训：9月25日-28日在报告厅举行。")
        self.assertEqual(len(outcome.candidates), 1)
        self.assertEqual(outcome.candidates[0].date, "2026-09-25")

    def test_long_range_is_not_exploded(self):
        """整学期级别的区间不该炸成上百条。"""
        outcome = _parse("每日打卡：9月1日-12月31日期间，每天上午8:00前提交健康打卡。")
        self.assertLessEqual(len(outcome.candidates), 1)

    def test_parse_date_range_returns_both_ends(self):
        span = parsing.parse_date_range("9月25日-28日期间", date(2026, 9, 24))
        self.assertIsNotNone(span)
        self.assertEqual((span[0].isoformat(), span[1].isoformat()),
                         ("2026-09-25", "2026-09-28"))
        self.assertIsNone(parsing.parse_date_range("没有任何日期", date(2026, 9, 24)))


class ChatterTests(unittest.TestCase):
    """闲聊判定必须保守：宁可留一条噪音，也别把正经通知丢掉。"""

    def test_pure_chatter_is_still_dropped(self):
        for text in ("收到，谢谢大家！", "哈哈哈哈", "好的", "感谢🙏"):
            with self.subTest(text=text):
                self.assertEqual(_parse(text).candidates, [])

    def test_task_language_is_not_chatter(self):
        """带任务动词的通知即使结尾说"感谢"也必须留下。"""
        for text in ("请各班统计留校名单，感谢配合。",
                     "各位同学，明天之内提交材料，谢谢。",
                     "注意：本周五前完成填写，辛苦了。"):
            with self.subTest(text=text[:14]):
                self.assertTrue(_parse(text).candidates, f"被误判成闲聊：{text}")

    def test_date_or_clock_alone_is_not_chatter(self):
        self.assertTrue(_parse("收到，明天下午3点开会。").candidates)
        self.assertTrue(_parse("好的，9月25日上午11:00前交。").candidates)


class TitleCleanupTests(unittest.TestCase):
    def test_vocative_is_removed(self):
        cases = {
            "请各班班长，明天上午9点在会议室开会。": "开会",
            "各位同学，后天14:00讲座报名截止。": "报名截止",
            "通知：明天上午9点开会。": "开会",
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:16]):
                title = _parse(text).candidates[0].title
                self.assertIn(expected, title)

    def test_courtesy_tail_is_removed(self):
        title = _parse("讲座：周五14:00在报告厅，请准时参加，感谢配合。").candidates[0].title
        self.assertNotIn("感谢", title)

    def test_content_word_is_not_swallowed(self):
        """「注意」后面的正文不能被当成呼语残渣一起吃掉（实测踩到过）。"""
        title = _parse("注意，9月25日-28日期间每日上午11:00前统计名单，感谢配合。").candidates[0].title
        self.assertIn("统计名单", title)
        self.assertNotIn("感谢", title)


if __name__ == "__main__":
    unittest.main(verbosity=2)
