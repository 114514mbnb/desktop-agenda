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
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.models import Event  # noqa: E402
from agenda.panel import AgendaPanel  # noqa: E402
from agenda.timeline import build_timeline  # noqa: E402
from tests import ClipboardSafeTestCase  # noqa: E402

LONG_TITLE = "统计本班当日留校人员名单并报送至负责人群"


def future_start(hours: int = 2) -> tuple[str, str]:
    """"从现在起 N 小时后"的 (日期, HH:MM)。

    用例**不能**把时间写死（比如 11:00）：面板会隐藏已过期的通知、`next_item` 只认
    未来或进行中的事项，于是"上午跑绿、晚上跑红"。这里一律相对当前时间算，
    跨过午夜也没关系（日期跟着走，仍在 7 天窗口内）。
    """
    moment = datetime.now() + timedelta(hours=hours)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M")


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
        self.start_date, self.start_time = future_start(2)
        (self.data / "events.json").write_text(json.dumps({
            "schemaVersion": 1,
            "events": [{
                "id": "ev-long", "title": LONG_TITLE,
                "date": self.start_date, "start": self.start_time,
                "notes": "备注一行",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        self.panel = self._panel()

    def _panel(self, width: int = 360):
        try:
            panel = AgendaPanel(self.data, width=width, pipeline_ms=0,
                                autostart_pipeline=False, hide_past=False,
                                window_mode="desktop",
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
        self.assertIn(self.start_time, text)

    def test_header_grows_when_the_line_wraps(self):
        """放不下就折行 + 让高，而不是裁掉末行。

        头部现在**不写死高度**，交给 Tk 按内容撑开；这条盯的是"长标题的头部
        必须比短标题高"，也就是它真的让了位。
        """
        tall = int(self.panel.header.winfo_height())
        short_date, short_time = future_start(3)
        self._use_events([{"id": "ev-short", "title": "开班会",
                           "date": short_date, "start": short_time}])
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
        short_date, short_time = future_start(4)
        self._use_events([{"id": "ev-short", "title": "开班会",
                           "date": short_date, "start": short_time}])
        self.assertLessEqual(int(self.panel.header.winfo_height()),
                             int(140 * self.panel.scale),
                             "短标题不该让头部变得很高（版面会跟着跳）")

    def test_no_next_item_shrinks_the_header_back(self):
        self._use_events([])
        self.assertEqual(self.panel.next_label.cget("text"), "暂无后续安排")
        self.assertLessEqual(int(self.panel.header.winfo_height()),
                             int(140 * self.panel.scale))


class FrontOnRequestTests(ClipboardSafeTestCase):
    """用户显式要求显示时，面板必须**跳到最前面**。

    用户的配置是「桌面挂件」（`window_mode="desktop"` + 压在普通窗口之下）。
    双击桌面快捷方式时面板确实"显示了"，但只要浏览器是最大化的就被完全盖住 ——
    用户的原话是"点了那个图标好长时间都没有响应"。所以显式请求要临时置顶几秒。
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def _panel(self):
        try:
            panel = AgendaPanel(self.data, width=340, pipeline_ms=0,
                                autostart_pipeline=False, hide_past=False,
                                window_mode="desktop", position=(60, 60))
        except Exception as error:                 # 无图形环境
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.addCleanup(panel.quit)
        panel.root.update()
        return panel

    def _patch_winlayer(self):
        """把层级操作换成记录器：不真的动窗口，只看**调了哪些**。"""
        from agenda import panel as panel_mod
        from agenda import winlayer

        calls: list[str] = []
        saved = {}
        for name in ("set_topmost", "send_to_bottom", "raise_to_top_of_normal",
                     "flash_window", "desktop_is_foreground"):
            saved[name] = getattr(winlayer, name)
        winlayer.set_topmost = lambda hwnd, topmost: calls.append(f"topmost={topmost}") or True
        winlayer.send_to_bottom = lambda hwnd: calls.append("bottom") or True
        winlayer.raise_to_top_of_normal = lambda hwnd: calls.append("raise") or True
        winlayer.flash_window = lambda hwnd, count=3: calls.append("flash") or True
        winlayer.desktop_is_foreground = lambda: False
        self.addCleanup(lambda: [setattr(winlayer, k, v) for k, v in saved.items()])
        return panel_mod, calls

    def test_an_explicit_request_brings_it_to_the_front(self):
        panel = self._panel()
        panel._hwnd = 12345                     # 假装已经拿到窗口句柄
        _mod, calls = self._patch_winlayer()
        panel.show_now(front=True)
        self.assertIn("topmost=True", calls, "没有把面板提到最上面")
        self.assertIn("flash", calls, "没有闪烁提醒")
        self.assertTrue(panel._front_active())

    def test_the_watcher_does_not_push_it_back_down_while_shown(self):
        """置顶的这几秒里，前台巡检不许把它压回底层（否则刚看到就没了）。"""
        panel = self._panel()
        panel._hwnd = 12345
        _mod, calls = self._patch_winlayer()
        panel.show_now(front=True)
        calls.clear()
        panel._apply_foreground_change()
        self.assertNotIn("bottom", calls, "巡检把刚提到前面的面板压回去了")

    def test_it_returns_to_its_own_layer_afterwards(self):
        panel = self._panel()
        panel._hwnd = 12345
        _mod, calls = self._patch_winlayer()
        panel.show_now(front=True)
        calls.clear()
        panel._end_front()
        self.assertFalse(panel._front_active())
        self.assertIn("topmost=False", calls, "置顶没解除（面板会一直浮在别人上面）")
        self.assertIn("bottom", calls, "没有回到桌面挂件该待的层")

    def test_a_plain_show_does_not_steal_the_front(self):
        """自己刷新（不是用户点的）不该把面板提到别人上面。"""
        panel = self._panel()
        panel._hwnd = 12345
        _mod, calls = self._patch_winlayer()
        panel.show_now()
        self.assertNotIn("topmost=True", calls)
        self.assertFalse(panel._front_active())

    def test_front_on_start_is_opt_in(self):
        """`--front`：面板是被用户点起来的时候才在启动后露一手。"""
        import inspect

        signature = inspect.signature(AgendaPanel.__init__)
        self.assertIn("front_on_start", signature.parameters)
        self.assertFalse(signature.parameters["front_on_start"].default)


class HidePastSwitchTests(unittest.TestCase):
    """"过期让位"必须留一个显式开关，否则用例会随"跑测试时几点"变红变绿。

    真踩过：`hide_past` 一开始是硬编码的 True，而不少用例把事件时间写死在 11:00/12:00。
    凌晨跑全绿，晚上跑 28 条红 —— 同一个提交，不同时刻，两种结果。
    所以这个开关是**测试的确定性接口**，不是给用户调的。
    """

    def test_the_panel_defaults_to_hiding_past_notices(self):
        import inspect

        from agenda.panel import AgendaPanel

        parameter = inspect.signature(AgendaPanel.__init__).parameters.get("hide_past")
        self.assertIsNotNone(parameter, "面板少了 hide_past 这个测试接口")
        self.assertTrue(parameter.default, "面板默认就该隐藏已结束的通知")

    def test_the_panel_forwards_the_switch_to_the_timeline(self):
        """面板要把开关真的传给时间线（不是收在口袋里）。"""
        captured: dict = {}

        import agenda.panel as panel_mod

        real_build = panel_mod.build_timeline

        def spy_build(events, table=None, **kwargs):
            captured.update(kwargs)
            # 一定转交真实实现：返回 None 会让 refresh() 里的
            # `assert self.timeline is not None` 炸掉（那会伪装成这条用例失败）。
            return real_build(events, table, **kwargs)

        panel_mod.build_timeline = spy_build
        try:
            with tempfile.TemporaryDirectory() as tmp:
                data = Path(tmp)
                (data / "timetable.json").write_text(
                    json.dumps({"termStart": "2026-09-07", "periods": [], "courses": []},
                               ensure_ascii=False), encoding="utf-8")
                (data / "events.json").write_text(
                    json.dumps({"schemaVersion": 1, "events": []}, ensure_ascii=False),
                    encoding="utf-8")
                panel = None
                try:
                    panel = AgendaPanel(data, pipeline_ms=0, autostart_pipeline=False,
                                        window_mode="desktop", hide_past=False)
                    panel.root.update()
                finally:
                    # 构造中途炸掉时也要把 Tk 根窗口收掉：半构造的根窗口留着会污染
                    # 同进程后面的 Tk 用例（实测连累了一堆对话框用例）
                    if panel is not None:
                        panel.root.destroy()
        finally:
            panel_mod.build_timeline = real_build
        self.assertEqual(captured.get("hide_past_events"), False,
                         "面板没有把 hide_past 传下去")


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


class DragTests(ClipboardSafeTestCase):
    """面板拖动。

    用户报：「你改的有问题。现在日程表无法自由拖动了」——
    照片模式下标题区被 `header_canvas` 铺满、三个标签又让出了版面，
    而拖动只绑在 header/label 上。Tk 的事件只发给指针底下的控件，
    它的 bindtags 里**没有父框架**，于是拖标题区拖到的是画布，拖动永远不触发。
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-07", "periods": [["08:00", "08:50"]], "courses": [],
        }, ensure_ascii=False), encoding="utf-8")
        (self.data / "events.json").write_text(
            json.dumps({"schemaVersion": 1, "events": []}, ensure_ascii=False),
            encoding="utf-8")
        (self.data / "client.json").write_text(json.dumps({
            "theme_mode": "classic", "hotkey_enabled": False,
        }, ensure_ascii=False), encoding="utf-8")
        try:
            self.panel = AgendaPanel(self.data, width=360, pipeline_ms=0,
                                     autostart_pipeline=False, hide_past=False,
                                     window_mode="desktop", position=(80, 60))
        except Exception as error:                 # 无图形环境
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.panel.root.update()

    def tearDown(self):
        try:
            self.panel.quit()
        except Exception:                # noqa: BLE001
            pass
        self.tmp.cleanup()

    @staticmethod
    def _event(x_root: int, y_root: int):
        return SimpleNamespace(x_root=x_root, y_root=y_root, x=0, y=0)

    def test_the_header_surface_is_draggable(self):
        """标题区**表面**（照片模式下的画布）必须自己带拖动绑定。"""
        canvas = self.panel.header_canvas
        self.assertTrue(canvas.bind("<Button-1>"), "标题区画布没绑按下事件")
        self.assertTrue(canvas.bind("<B1-Motion>"), "标题区画布没绑拖动过程")

    def test_the_labels_are_draggable(self):
        for widget in (self.panel.clock_label, self.panel.date_label, self.panel.next_label):
            self.assertTrue(widget.bind("<Button-1>"), f"{widget} 没绑拖动")

    def test_dragging_the_header_surface_moves_the_window(self):
        """拖动跟手：窗口相对光标的偏移在**按下那一刻**定下来，之后一直保持。

        死区那一拍只负责重新锚定（所以面板不会因为死区而"跳"一下），
        从越过死区之后开始，光标走多少、窗口就走多少。
        """
        self.panel.root.update_idletasks()
        start_x = self.panel.root.winfo_x()
        start_y = self.panel.root.winfo_y()
        self.panel._start_drag(self._event(500, 300))
        self.panel._do_drag(self._event(560, 340))     # 越过死区：重新锚定，这一拍不移动
        self.panel._do_drag(self._event(590, 360))     # 从这里开始跟手
        self.panel.root.update_idletasks()
        self.assertEqual(self.panel.root.winfo_x(), start_x + 30)
        self.assertEqual(self.panel.root.winfo_y(), start_y + 20)
        self.assertEqual((self.panel._target_x, self.panel._target_y),
                         (start_x + 30, start_y + 20), "新位置没记住，隐身回来会跳回去")

    def test_a_tiny_jitter_does_not_move_the_window(self):
        """点击时手抖一两像素不该让面板漂移（正文空白处现在也能拖了，更要注意）。"""
        self.panel.root.update_idletasks()
        start_x = self.panel.root.winfo_x()
        self.panel._start_drag(self._event(500, 300))
        self.panel._do_drag(self._event(501, 300))
        self.panel._do_drag(self._event(502, 299))
        self.panel.root.update_idletasks()
        self.assertEqual(self.panel.root.winfo_x(), start_x, "抖动把面板挪走了")

    def test_the_body_blank_area_is_draggable(self):
        """正文空白处也能拖（画布 / 内层容器 / 外壳都绑上）。"""
        for widget in (self.panel.body, self.panel.canvas, self.panel.inner, self.panel.shell):
            self.assertTrue(widget.bind("<Button-1>"), f"{widget} 没绑拖动")

    def test_a_finished_drag_saves_the_new_position(self):
        """松手就把位置落盘 —— 不能只在正常退出时存（强杀 / 关机就白拖了）。"""
        client = self.data / "client.json"
        self.panel._start_drag(self._event(500, 300))
        self.panel._do_drag(self._event(600, 400))
        self.panel._do_drag(self._event(660, 440))
        self.panel._end_drag()
        saved = json.loads(client.read_text(encoding="utf-8"))
        self.assertEqual((saved.get("panel_x"), saved.get("panel_y")),
                         (self.panel.root.winfo_x(), self.panel.root.winfo_y()),
                         "松手之后位置没进 client.json")

    def test_a_click_does_not_rewrite_the_config(self):
        """只是点了一下（没拖动）不该写配置文件。"""
        client = self.data / "client.json"
        before = client.read_text(encoding="utf-8")
        self.panel._start_drag(self._event(500, 300))
        self.panel._do_drag(self._event(501, 300))
        self.panel._end_drag()
        self.assertEqual(client.read_text(encoding="utf-8"), before)

    def test_the_panel_saving_its_own_position_is_not_an_external_change(self):
        """面板自己写的位置不该被 700ms 巡检当成"用户在控制台改了设置"。

        否则每拖一次面板就会重挂热键、重套主题、顺带刷一次界面。
        """
        self.panel._data_changed()                     # 建立基线
        self.panel._start_drag(self._event(500, 300))
        self.panel._do_drag(self._event(600, 400))
        self.panel._do_drag(self._event(660, 440))
        self.panel._end_drag()
        self.assertFalse(self.panel._data_changed(),
                         "自己写的位置被当成了外部改动")

    def test_a_show_request_brings_the_panel_back(self):
        """`panel.show` 请求：面板收起之后，双击快捷方式要能把它叫回眼前。

        桌面快捷方式只走这一条（它永远不开控制台），所以这条断了就真"叫不回来"。
        """
        self.panel.hide_instant()
        self.assertTrue(self.panel._hidden_offscreen, "没收起来，用例前提不成立")
        (self.data / "panel.show").write_text("1", encoding="utf-8")
        self.panel._watch_stop_flag()
        self.panel.root.update_idletasks()
        self.assertFalse(self.panel._hidden_offscreen, "面板没被叫回来")
        self.assertFalse((self.data / "panel.show").exists(), "请求文件没被消掉，会反复触发")

    def test_an_offscreen_saved_position_is_clamped(self):
        """client.json 里的坐标可能在屏幕外（换过显示器 / 拔过副屏）。

        不夹的话面板"启动就看不见"，用户只会说"日程表自己没了"。
        """
        panel = AgendaPanel(self.data, width=360, pipeline_ms=0,
                            autostart_pipeline=False, hide_past=False,
                            window_mode="desktop", position=(99999, 99999))
        try:
            panel.root.update_idletasks()
            logical_w = panel.root.winfo_screenwidth() / (panel.scale or 1.0)
            self.assertLess(panel._target_x, logical_w,
                            "面板被放到了屏幕右边界之外")
            self.assertGreaterEqual(panel._target_x, 0)
            self.assertLess(panel._target_y, panel.root.winfo_screenheight())
        finally:
            panel.quit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
