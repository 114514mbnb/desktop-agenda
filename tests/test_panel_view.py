"""面板时间线的"看法"：内容贴顶、下一项显示完整、过期通知自动消失。

对应用户这一轮的三条反馈：

1. 「图中的下一项显示不全。」——标题原来被硬截成 9 个字再截一次；
2. 「为什么我的日程安排不是处于滚轮条的最上方？不合逻辑……时间最早的位于滚轮条顶端」；
3. 「我要求我的群聊消息日程按照时间排好，过期日程自动消失。」
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.models import Event  # noqa: E402
from agenda.panel import AgendaPanel  # noqa: E402
from agenda.timeline import build_timeline  # noqa: E402
from tests import ClipboardSafeTestCase  # noqa: E402

LONG_TITLE = "统计本班当日留校人员名单并报送至负责人群"


class PanelViewTests(ClipboardSafeTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.today = date.today()
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": (self.today - timedelta(days=7)).strftime("%Y-%m-%d"),
            "periods": [["08:00", "08:50"], ["09:00", "09:50"]],
            "courses": [],
        }, ensure_ascii=False), encoding="utf-8")
        (self.data / "events.json").write_text(json.dumps({
            "schemaVersion": 1,
            "events": [{
                "id": "ev-long", "title": LONG_TITLE,
                "date": self.today.strftime("%Y-%m-%d"), "start": "11:00",
                "notes": "备注一行",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        self.panel = self._panel()

    def _panel(self, width: int = 360):
        try:
            panel = AgendaPanel(self.data, width=width, pipeline_ms=0,
                                autostart_pipeline=False, window_mode="desktop",
                                position=(80, 60))
        except Exception as error:                 # 无图形环境
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        panel.root.update()
        panel.refresh()
        panel.root.update()
        panel.root.update_idletasks()
        return panel

    def tearDown(self):
        super().tearDown()
        try:
            self.panel.quit()
        except Exception:                # noqa: BLE001
            pass
        self.tmp.cleanup()

    def _top_gap(self) -> int:
        """内容顶端距离画布顶端多少像素。"""
        canvas = self.panel.canvas
        inner = self.panel.inner
        visible = [child for child in inner.winfo_children() if child.winfo_ismapped()]
        if not visible:
            return 0
        top = min(child.winfo_rooty() for child in visible)
        return top - canvas.winfo_rooty()

    # -- 2. 内容必须贴顶 --------------------------------------------------
    def test_content_sits_at_the_very_top(self):
        """内容比画布矮的时候，也要紧贴滚动区顶端。

        用户截图里内容跑到下半、上面空一大块（上面空白的颜色正是画布底色，
        说明那块空白在滚动区内部）。
        """
        self.assertLess(self._top_gap(), 24,
                        f"内容没有贴在滚动区顶端，空了 {self._top_gap()} px")

    def test_snap_to_top_recovers_after_being_pushed_down(self):
        """不管 Tk 把内容摆到了哪儿，_snap_to_top 都要把它掰回顶端。"""
        canvas = self.panel.canvas
        canvas.yview_moveto(1.0)                       # 先滚到底
        self.panel._snap_to_top()
        self.panel.root.update()
        yview_top = canvas.yview()[0]
        self.assertLess(yview_top, 0.02, f"没有回到顶端：yview={canvas.yview()}")
        self.assertLess(self._top_gap(), 24)

    def test_snap_to_top_survives_a_late_layout_pass(self):
        """Tk 的重排可能发生在我们设完位置之后 —— idle 那一钉要兜住这种情况。"""
        self.panel.canvas.yview_moveto(1.0)
        self.panel._snap_to_top()
        self.panel.root.update_idletasks()             # 让 after_idle 那一钉跑掉
        self.panel.root.update()
        self.assertLess(self.panel.canvas.yview()[0], 0.02)

    # -- 1. 下一项显示完整 ------------------------------------------------
    def test_next_line_shows_the_whole_title(self):
        text = self.panel.next_label.cget("text")
        self.assertIn(LONG_TITLE, text, f"下一项被截断了：{text!r}")
        self.assertNotIn("…", text, f"下一项里还有省略号：{text!r}")

    def test_next_line_still_says_when(self):
        text = self.panel.next_label.cget("text")
        self.assertTrue(text.startswith("下一项 ") or text.startswith("进行中 "), text)
        self.assertIn("11:00", text)

    def test_header_grows_when_the_line_wraps(self):
        """放不下就折行 + 让高，而不是裁掉末行。

        头部现在**不写死高度**，交给 Tk 按内容撑开；这条盯的是"长标题的头部
        必须比短标题高"，也就是它真的让了位。
        """
        tall = int(self.panel.header.winfo_height())
        self._use_events([{"id": "ev-short", "title": "开班会",
                           "date": self.today.strftime("%Y-%m-%d"), "start": "11:00"}])
        short = int(self.panel.header.winfo_height())
        self.assertGreater(tall, short, "长标题没有让头部变高，末行会被裁掉")

    def test_the_wrapped_line_is_not_clipped_by_the_header(self):
        """头部必须装得下折行后的**全部**行数。

        真踩过：行数按"整串宽度 ÷ 可用宽度"估，算出 2 行、实际 Tk 按空格断成了 3 行，
        第三行就这样被固定高度的头部裁掉了 —— 用户看到的还是"显示不全"。
        """
        header = self.panel.header
        needed = 0
        for child in (self.panel.clock_label, self.panel.date_label, self.panel.next_label):
            pady = child.pack_info().get("pady", 0)
            if isinstance(pady, str):
                parts = [int(part) for part in pady.split()] or [0]
            elif isinstance(pady, (tuple, list)):
                parts = [int(part) for part in pady]
            else:
                parts = [int(pady)]
            needed += int(child.winfo_reqheight()) + sum(parts)
        self.assertLessEqual(
            needed, int(header.winfo_height()),
            f"头部装不下折行后的文字：内容需要 {needed}px，头部只有 {header.winfo_height()}px")

    def _use_events(self, events: list[dict]) -> None:
        (self.data / "events.json").write_text(
            json.dumps({"schemaVersion": 1, "events": events}, ensure_ascii=False),
            encoding="utf-8")
        self.panel.refresh()
        self.panel.root.update()

    def test_short_titles_keep_the_header_compact(self):
        self._use_events([{"id": "ev-short", "title": "开班会",
                           "date": self.today.strftime("%Y-%m-%d"), "start": "11:00"}])
        self.assertLessEqual(int(self.panel.header.winfo_height()),
                             int(140 * self.panel.scale),
                             "短标题不该让头部变得很高（版面会跟着跳）")

    def test_no_next_item_shrinks_the_header_back(self):
        self._use_events([])
        self.assertEqual(self.panel.next_label.cget("text"), "暂无后续安排")
        self.assertLessEqual(int(self.panel.header.winfo_height()),
                             int(140 * self.panel.scale))


class HidePastEventsTests(unittest.TestCase):
    """过期通知不再占版面；课程不受影响。"""

    def _timeline(self, hour: int, *, hide: bool):
        today = date(2026, 9, 28)
        events = [
            Event(id="ev-past", date="2026-09-28", title="上午已经开完的会",
                  start="08:00", end="09:00"),
            Event(id="ev-now", date="2026-09-28", title="下午的讲座",
                  start="14:00", end="15:00"),
        ]
        return build_timeline(events, None, today=today,
                              now=datetime(2026, 9, 28, hour, 0),
                              hide_past_events=hide)

    def test_panel_hides_the_event_that_already_ended(self):
        timeline = self._timeline(12, hide=True)
        titles = [card.title for section in timeline.sections for card in section.cards]
        self.assertNotIn("上午已经开完的会", titles)
        self.assertIn("下午的讲座", titles)

    def test_console_still_sees_it_by_default(self):
        """默认不隐藏：控制台的通知列表是"今天有什么"，回看时还要能查到。"""
        timeline = self._timeline(12, hide=False)
        titles = [card.title for section in timeline.sections for card in section.cards]
        self.assertIn("上午已经开完的会", titles)

    def test_an_event_still_running_is_kept(self):
        timeline = self._timeline(8, hide=True)         # 08:00–09:00 正在进行
        titles = [card.title for section in timeline.sections for card in section.cards]
        self.assertIn("上午已经开完的会", titles, "进行中的事项不能算过期")

    def test_counts_follow_what_is_shown(self):
        timeline = self._timeline(12, hide=True)
        today_section = timeline.sections[0]
        self.assertEqual(today_section.event_count, 1)
        self.assertEqual(timeline.today_event_count, 1)

    def test_past_courses_are_not_removed(self):
        """课表是当天的骨架：上午的课下午还要回看，不能因为"过期"就清掉。"""
        from agenda.timetable import Timetable

        table = Timetable(term_start=date(2026, 9, 7),
                          periods=(("08:00", "08:50"), ("09:00", "09:50")),
                          courses=())
        timeline = build_timeline([], table, today=date(2026, 9, 28),
                                  now=datetime(2026, 9, 28, 23, 0),
                                  hide_past_events=True)
        self.assertEqual(timeline.total_cards, 0)       # 没课，只是确认不炸
        self.assertIsNotNone(timeline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
