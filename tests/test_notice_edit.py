"""《编辑此条通知》与"选中后按热键 = 补充备注"的测试。

用户提的两条功能补充：

1. 「有的群通知识别不准确，需要我对内容进行补充，所以加一条《编辑此条通知》
   让我能够对通知进行微调」；
2. 「有的群通知并不是只有一条，它还有附带的补充内容……当我选中已经识别的那条信息后
   再按热键，为对那条通知的补充（备注）。当没有选中原信息时，为创建新的群通知。
   说明：这条规则仅对通知起效果，对日常课表无效。」

下面既测数据真的落了盘，也测"该走哪条路"的路由判断。
"""

from __future__ import annotations

import json
import sys
import tempfile
import tkinter as tk
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agenda.panel as panel_mod  # noqa: E402
from agenda import hotkey  # noqa: E402
from agenda.panel import AgendaPanel, merge_notes  # noqa: E402
from tests import ClipboardSafeTestCase  # noqa: E402

SUPPLEMENT = "补充：报送表格换成新版，地点改到办公楼 302。"


class EditAndSupplementTests(ClipboardSafeTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.today = date.today()
        # 课表按"今天就是上课日"来造，免得测试跟着真实星期几飘。
        # 学期开始放在一周前：今天是第 2 教学周，30 周的区间一定罩得住。
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][self.today.weekday()]
        term_start = (self.today - timedelta(days=7)).strftime("%Y-%m-%d")
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": term_start,
            "periods": [["08:00", "08:50"], ["09:00", "09:50"]],
            "courses": [{"name": "高等数学", "weekday": weekday, "period": "1-2",
                         "weeks": "1-30", "teacher": "陈立", "location": "A-101"}],
        }, ensure_ascii=False), encoding="utf-8")
        today = self.today.strftime("%Y-%m-%d")
        (self.data / "events.json").write_text(json.dumps({
            "schemaVersion": 1,
            "events": [
                {"id": "ev-notice", "title": "统计本班当日留校人员名单", "date": today,
                 "start": "11:00", "end": None, "location": "教学楼 201",
                 "people": ["班长"], "notes": "连续事项：每日", "group": "2026级群"},
                {"id": "ev-other", "title": "讲座：多模态大模型", "date": today,
                 "start": "14:00", "end": "15:30", "people": [], "notes": None},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        try:
            self.panel = AgendaPanel(self.data, pipeline_ms=0, autostart_pipeline=False,
                                     window_mode="desktop", position=(80, 60))
        except Exception as error:                 # 无图形环境
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.panel.root.update()
        self.panel.refresh()
        self.panel.root.update()

    def tearDown(self):
        super().tearDown()
        try:
            self.panel.quit()
        except Exception:                # noqa: BLE001
            pass
        self.tmp.cleanup()

    # -- 工具 ------------------------------------------------------------
    def _events(self) -> list[dict]:
        payload = json.loads((self.data / "events.json").read_text(encoding="utf-8"))
        return payload.get("events", [])

    def _event(self, event_id: str) -> dict:
        return next(item for item in self._events() if item["id"] == event_id)

    def _card(self, title_prefix: str, kind: str = "event"):
        for section in self.panel.timeline.sections:
            for card in section.cards:
                if card.kind == kind and card.title.startswith(title_prefix):
                    return card
        self.fail(f"没有找到卡片：{title_prefix}")

    def _feed(self, text: str):
        """模拟"热键拿到了一段文字"：选区抓取失败（面板在前台）+ 剪贴板里有内容。"""
        real_capture = hotkey.capture_selection
        real_read = hotkey.read_clipboard_text
        hotkey.capture_selection = lambda: ("", "当前前台是本程序窗口，没有可抓取的选区")
        hotkey.read_clipboard_text = lambda: text
        try:
            self.panel.ingest_clipboard()
        finally:
            hotkey.capture_selection = real_capture
            hotkey.read_clipboard_text = real_read
        self.panel.root.update()

    # -- 1. 编辑此条通知 --------------------------------------------------
    def test_edit_writes_every_field_back(self):
        card = self._card("统计本班")
        real = panel_mod.ask_edit_event
        panel_mod.ask_edit_event = lambda *a, **kw: {
            "title": "统计留校名单（改）", "date": "2026-10-08", "start": "09:30",
            "end": "10:00", "location": "办公楼 302", "people": "班长、学委",
            "notes": "改成每周三",
        }
        try:
            self.panel.card_edit(card)
        finally:
            panel_mod.ask_edit_event = real
        self.panel.root.update()
        saved = self._event("ev-notice")
        self.assertEqual(saved["title"], "统计留校名单（改）")
        self.assertEqual(saved["date"], "2026-10-08")
        self.assertEqual(saved["start"], "09:30")
        self.assertEqual(saved["end"], "10:00")
        self.assertEqual(saved["location"], "办公楼 302")
        self.assertEqual(list(saved["people"]), ["班长", "学委"])
        self.assertEqual(saved["notes"], "改成每周三")

    def test_edit_prefills_the_current_values(self):
        """对话框打开时应当带着这条通知现在的值，用户只改要改的那一项。"""
        card = self._card("统计本班")
        seen: dict = {}
        real = panel_mod.ask_edit_event
        panel_mod.ask_edit_event = lambda _parent, **kw: seen.update(kw) or None
        try:
            self.panel.card_edit(card)
        finally:
            panel_mod.ask_edit_event = real
        self.assertEqual(seen["title"], "统计本班当日留校人员名单")
        self.assertEqual(seen["start"], "11:00")
        self.assertEqual(seen["location"], "教学楼 201")
        self.assertEqual(seen["people"], "班长")
        self.assertEqual(seen["notes"], "连续事项：每日")

    def test_edit_cancel_changes_nothing(self):
        card = self._card("统计本班")
        before = self._events()
        real = panel_mod.ask_edit_event
        panel_mod.ask_edit_event = lambda *a, **kw: None
        try:
            self.panel.card_edit(card)
        finally:
            panel_mod.ask_edit_event = real
        self.assertEqual(self._events(), before)

    def test_edit_clearing_a_field_writes_null(self):
        """把地点清空 = 这条没有地点，不是留着旧值。"""
        card = self._card("统计本班")
        real = panel_mod.ask_edit_event
        panel_mod.ask_edit_event = lambda *a, **kw: {
            "title": "统计本班当日留校人员名单", "date": "2026-09-28", "start": "",
            "end": "", "location": "", "people": "", "notes": "",
        }
        try:
            self.panel.card_edit(card)
        finally:
            panel_mod.ask_edit_event = real
        saved = self._event("ev-notice")
        self.assertIsNone(saved["location"])
        self.assertEqual(list(saved["people"]), [])
        self.assertIsNone(saved["notes"])
        self.assertIsNone(saved["start"])

    def test_edit_marks_the_date_as_manually_confirmed(self):
        """人手确认过日期之后，"日期待确认"这类自动标记要跟着消失。"""
        payload = json.loads((self.data / "events.json").read_text(encoding="utf-8"))
        payload["events"][0]["date_source"] = "notice-fallback"
        (self.data / "events.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.panel.refresh()
        card = self._card("统计本班")
        real = panel_mod.ask_edit_event
        panel_mod.ask_edit_event = lambda *a, **kw: {
            "title": "统计本班当日留校人员名单", "date": "2026-09-28", "start": "11:00",
            "end": "", "location": "教学楼 201", "people": "班长", "notes": "",
        }
        try:
            self.panel.card_edit(card)
        finally:
            panel_mod.ask_edit_event = real
        self.assertEqual(self._event("ev-notice")["date_source"], "manual")

    def test_course_cards_are_not_editable_here(self):
        """课程不去改：课时表改一条会影响所有周次，入口留在客户端。"""
        course = self._card("高等数学", kind="course")
        toasts: list[str] = []
        self.panel._toast = lambda message, **kw: toasts.append(message)
        real = panel_mod.ask_edit_event
        called: list[int] = []
        panel_mod.ask_edit_event = lambda *a, **kw: called.append(1) or None
        try:
            self.panel.card_edit(course)
        finally:
            panel_mod.ask_edit_event = real
        self.assertEqual(called, [], "课程不该弹出编辑通知的对话框")
        self.assertTrue(any("课程" in message for message in toasts), toasts)

    def test_the_menu_offers_editing(self):
        """右键菜单里必须有《编辑此条通知》。"""
        menu = self._menu_labels(self._card("统计本班"))
        self.assertIn("编辑此条通知…", menu)

    def _menu_labels(self, card) -> list[str]:
        labels: list[str] = []
        real_menu = panel_mod.tk.Menu

        class Spy(real_menu):                       # type: ignore[misc,valid-type]
            def add_command(self, **kwargs):
                labels.append(kwargs.get("label", ""))
                return super().add_command(**kwargs)

            def tk_popup(self, *_a, **_kw):         # 不真的弹出来，测试不该抢用户的鼠标
                return None

        panel_mod.tk.Menu = Spy
        try:
            event = type("E", (), {"x_root": 0, "y_root": 0})()
            self.panel._card_menu(card, event)
        finally:
            panel_mod.tk.Menu = real_menu
        return labels

    # -- 2. 选中后按热键 = 补充备注 ---------------------------------------
    def test_nothing_selected_creates_a_new_notice(self):
        """没选中任何卡片时，热键照旧是"新建通知"。"""
        self.assertIsNone(self.panel._selected_event_id)
        self._feed("【提醒】\n1.明天上午9点开班会。\n")
        titles = [item["title"] for item in self._events()]
        self.assertTrue(any("班会" in title for title in titles), titles)
        self.assertEqual(len(self._events()), 3, "应当新增一条，而不是改旧的")

    def test_selected_card_turns_the_hotkey_into_a_supplement(self):
        """选中某条通知后再按热键 = 把这段文字并进它的备注，不新建。"""
        card = self._card("统计本班")
        self.panel.select_card(card)
        self.assertEqual(self.panel._selected_event_id, "ev-notice")
        self._feed(SUPPLEMENT)
        self.assertEqual(len(self._events()), 2, "补充不该新建通知")
        notes = self._event("ev-notice")["notes"] or ""
        self.assertIn("连续事项：每日", notes, "原来的备注不能被冲掉")
        self.assertIn("办公楼 302", notes, "补充内容没进去")

    def test_supplement_does_not_touch_other_notices(self):
        card = self._card("统计本班")
        self.panel.select_card(card)
        self._feed(SUPPLEMENT)
        self.assertIsNone(self._event("ev-other")["notes"])

    def test_clicking_the_same_card_again_deselects(self):
        card = self._card("统计本班")
        self.panel.select_card(card)
        # 隔一会儿再点（不是双击的第二拍）；双击保护见下一条用例
        self.panel._last_select_at = 0.0
        self.panel.select_card(card)
        self.assertIsNone(self.panel._selected_event_id)
        self._feed("【提醒】\n1.明天上午9点开班会。\n")
        self.assertEqual(len(self._events()), 3, "取消选中后应当恢复「新建通知」的行为")

    def test_the_second_click_of_a_double_click_keeps_the_selection(self):
        """双击是"看全文"，不该顺手把刚选中的又取消掉。

        Tk 会把双击的第二拍也当成一次 `<Button-1>` 发过来，所以卡片上那两下
        必然一拍选中、一拍取消——不挡一下的话，双击完卡片反而是未选中状态。
        """
        card = self._card("统计本班")
        self.panel.select_card(card)
        self.panel.select_card(card)            # 第二拍，紧接着来
        self.assertEqual(self.panel._selected_event_id, "ev-notice",
                         "双击之后选中态不该被第二拍取消")

    def test_double_clicking_opens_the_detail_of_a_selected_card(self):
        card = self._card("统计本班")
        self.panel.select_card(card)
        self.panel.show_card_detail(card)
        self.panel.root.update()
        self.assertEqual(self.panel._selected_event_id, "ev-notice")
        self.assertIsNotNone(getattr(self.panel, "_detail_window", None))

    def test_course_cards_cannot_be_supplemented(self):
        """用户明确说：这条规则仅对通知生效，对日常课表无效。"""
        course = self._card("高等数学", kind="course")
        notice = self._card("统计本班")
        self.panel.select_card(notice)              # 先选中一条通知
        toasts: list[str] = []
        self.panel._toast = lambda message, **kw: toasts.append(message)
        self.panel.select_card(course)              # 再点到课程上 → 选中被取消并说明原因
        self.assertIsNone(self.panel._selected_event_id, "课程不该被选成补充对象")
        self._feed("【提醒】\n1.明天上午9点开班会。\n")
        self.assertEqual(len(self._events()), 3, "课程被选中时应当走「新建通知」")
        self.assertTrue(any("课程" in message for message in toasts), toasts)

    def test_clicking_a_course_card_is_silent_when_nothing_was_selected(self):
        """本来就没选中东西，点一下课程卡不该蹦提示（那只是随手一点）。"""
        course = self._card("高等数学", kind="course")
        toasts: list[str] = []
        self.panel._toast = lambda message, **kw: toasts.append(message)
        self.panel.select_card(course)
        self.assertEqual(toasts, [], f"不该有提示：{toasts}")

    def test_a_deleted_selection_falls_back_to_creating(self):
        """选中的那条被删掉之后再按热键，不该报错，也不该往空气里补。"""
        card = self._card("统计本班")
        self.panel.select_card(card)
        payload = json.loads((self.data / "events.json").read_text(encoding="utf-8"))
        payload["events"] = [item for item in payload["events"] if item["id"] != "ev-notice"]
        (self.data / "events.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.panel.refresh()
        self._feed("【提醒】\n1.明天上午9点开班会。\n")
        titles = [item["title"] for item in self._events()]
        self.assertTrue(any("班会" in title for title in titles), titles)

    def test_selection_survives_a_refresh(self):
        """刷新（每 30 秒一次）之后选中态要还在，否则用户会以为没选中。"""
        card = self._card("统计本班")
        self.panel.select_card(card)
        self.panel.refresh()
        self.panel.root.update()
        self.assertEqual(self.panel._selected_event_id, "ev-notice")
        painted = [event_id for _frame, event_id in self.panel._card_frames
                   if event_id == "ev-notice"]
        self.assertTrue(painted, "刷新后卡片没登记进选中态列表")
        frames = [frame for frame, event_id in self.panel._card_frames
                  if event_id == "ev-notice"]
        self.assertTrue(all(int(frame.cget("highlightthickness")) == 2 for frame in frames),
                        "刷新后选中边框没了")

    def test_only_one_card_can_be_selected(self):
        first = self._card("统计本班")
        second = self._card("讲座")
        self.panel.select_card(first)
        self.panel.select_card(second)
        self.assertEqual(self.panel._selected_event_id, "ev-other")

    def test_supplement_toast_names_the_target(self):
        card = self._card("统计本班")
        self.panel.select_card(card)
        toasts: list[str] = []
        self.panel._toast = lambda message, **kw: toasts.append(message)
        self._feed(SUPPLEMENT)
        self.assertTrue(any("统计本班当日留校人员名单" in message for message in toasts), toasts)


class MergeNotesTests(unittest.TestCase):
    """补充内容和原备注怎么合：原文一个字都不能动，但要读得下去。"""

    def test_appends_with_a_separator_for_short_lines(self):
        self.assertEqual(merge_notes("原备注", "补充一句"), "原备注；补充一句")

    def test_empty_existing_just_uses_the_addition(self):
        self.assertEqual(merge_notes(None, "只有补充"), "只有补充")
        self.assertEqual(merge_notes("", "只有补充"), "只有补充")

    def test_empty_addition_keeps_the_original(self):
        self.assertEqual(merge_notes("原备注", ""), "原备注")
        self.assertEqual(merge_notes("原备注", "   "), "原备注")

    def test_long_or_multiline_additions_start_a_new_line(self):
        self.assertEqual(merge_notes("原备注", "第一行\n第二行"), "原备注\n第一行\n第二行")
        long_text = "这是一段很长的补充说明" * 6
        self.assertEqual(merge_notes("原备注", long_text), f"原备注\n{long_text}")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(merge_notes("  原备注  ", "  补充  "), "原备注；补充")


def _walk(widget, kind):
    """按创建顺序收集某个窗口里的子控件。"""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, kind):
            found.append(child)
        found.extend(_walk(child, kind))
    return found


@unittest.skipUnless(sys.platform == "win32", "对话框置顶是 Windows 概念")
class EditDialogTests(unittest.TestCase):
    """真正把《编辑此条通知》窗口打开来操作一遍（上面那些测的是桩）。"""

    TITLE = "编辑此条通知"

    def setUp(self):
        import tkinter as tk

        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.root.geometry("+80+60")
        self.root.attributes("-topmost", False)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:                # noqa: BLE001
            pass

    def _open(self, action, **fields):
        """开对话框、执行 action、返回 (结果, 现场记录)。

        `action(window, entries, texts, buttons)` 里做用户会做的事。
        另外挂一个保底回调：校验没过时窗口不会关，不能把测试挂死。
        """
        from agenda.panel import ask_edit_event

        seen: dict = {}

        def run():
            window = self._find()
            if window is None:
                seen["missing"] = True
                return
            entries = _walk(window, tk.Entry)
            texts = _walk(window, tk.Text)
            buttons = {button.cget("text"): button for button in _walk(window, tk.Button)}
            seen["topmost"] = window.attributes("-topmost")
            seen["entries_before"] = [entry.get() for entry in entries]
            labels_before = [label.cget("text") for label in _walk(window, tk.Label)]
            action(window, entries, texts, buttons)
            seen["alive"] = bool(window.winfo_exists())
            labels_after = ([label.cget("text") for label in _walk(window, tk.Label)]
                            if seen["alive"] else [])
            seen["new_hint"] = next(
                (text for text in labels_after if text and text not in labels_before), "")

        def force_close():
            window = self._find()
            if window is not None:
                window.destroy()

        self.root.after(400, run)
        self.root.after(1600, force_close)
        result = ask_edit_event(self.root, **fields)
        return result, seen

    def _find(self):
        import tkinter as tk

        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel) and child.title() == self.TITLE:
                return child
        return None

    def _prefill(self) -> dict:
        return {"title": "统计本班当日留校人员名单", "date": "2026-09-28", "start": "11:00",
                "end": "", "location": "教学楼 201", "people": "班长", "notes": "连续事项：每日"}

    def test_dialog_prefills_and_is_topmost(self):
        result, seen = self._open(lambda *_a: None, **self._prefill())
        self.assertNotIn("missing", seen, "对话框没出现")
        self.assertEqual(int(seen["topmost"]), 1, "对话框没置顶，会被别的窗口盖住")
        # 控件创建顺序：标题 / 年 月 日 / 起 止 / 地点 / 人员
        self.assertEqual(seen["entries_before"][0], "统计本班当日留校人员名单")
        self.assertEqual(seen["entries_before"][1:4], ["2026", "09", "28"])
        self.assertEqual(seen["entries_before"][4:6], ["11:00", ""])
        self.assertEqual(seen["entries_before"][6], "教学楼 201")
        self.assertEqual(seen["entries_before"][7], "班长")
        self.assertIsNone(result, "没点保存就关掉，应当返回 None")

    def test_saving_returns_what_the_user_typed(self):
        def action(_window, entries, texts, buttons):
            entries[0].delete(0, "end")
            entries[0].insert(0, "统计留校名单（改）")
            entries[1].delete(0, "end")
            entries[1].insert(0, "2026")
            entries[2].delete(0, "end")
            entries[2].insert(0, "10")
            entries[3].delete(0, "end")
            entries[3].insert(0, "08")
            entries[4].delete(0, "end")
            entries[4].insert(0, "09:30")
            entries[5].delete(0, "end")
            entries[5].insert(0, "10:00")
            entries[6].delete(0, "end")
            entries[6].insert(0, "办公楼 302")
            entries[7].delete(0, "end")
            entries[7].insert(0, "班长、学委")
            texts[0].delete("1.0", "end")
            texts[0].insert("1.0", "改成每周三")
            buttons["保存"].invoke()

        result, seen = self._open(action, **self._prefill())
        self.assertFalse(seen["alive"], "点了保存窗口却没关")
        self.assertEqual(result, {
            "title": "统计留校名单（改）", "date": "2026-10-08", "start": "09:30",
            "end": "10:00", "location": "办公楼 302", "people": "班长、学委",
            "notes": "改成每周三",
        })

    def test_empty_title_is_refused_without_closing(self):
        def action(_window, entries, _texts, buttons):
            entries[0].delete(0, "end")
            buttons["保存"].invoke()

        result, seen = self._open(action, **self._prefill())
        self.assertTrue(seen["alive"], "标题为空却把窗口关了")
        self.assertIn("标题", seen["new_hint"])
        self.assertIsNone(result)

    def test_bad_time_is_refused_without_closing(self):
        def action(_window, entries, _texts, buttons):
            entries[4].delete(0, "end")
            entries[4].insert(0, "9点半")
            buttons["保存"].invoke()

        _result, seen = self._open(action, **self._prefill())
        self.assertTrue(seen["alive"], "时间写法不对却把窗口关了")
        self.assertIn("HH:MM", seen["new_hint"])

    def test_end_before_start_is_refused(self):
        def action(_window, entries, _texts, buttons):
            entries[4].delete(0, "end")
            entries[4].insert(0, "10:00")
            entries[5].delete(0, "end")
            entries[5].insert(0, "09:00")
            buttons["保存"].invoke()

        _result, seen = self._open(action, **self._prefill())
        self.assertTrue(seen["alive"])
        self.assertIn("晚于", seen["new_hint"])

    def test_incomplete_date_is_refused(self):
        def action(_window, entries, _texts, buttons):
            entries[3].delete(0, "end")
            buttons["保存"].invoke()

        _result, seen = self._open(action, **self._prefill())
        self.assertTrue(seen["alive"])
        self.assertIn("日期", seen["new_hint"])

    def test_escape_cancels(self):
        def action(window, *_a):
            window.event_generate("<Escape>")

        result, seen = self._open(action, **self._prefill())
        self.assertFalse(seen["alive"], "Esc 没关掉窗口")
        self.assertIsNone(result)

    def test_an_afternoon_and_evening_time_are_accepted(self):
        """边界值：00:00 和 23:59 都要能存。"""
        def action(_window, entries, _texts, buttons):
            entries[4].delete(0, "end")
            entries[4].insert(0, "00:00")
            entries[5].delete(0, "end")
            entries[5].insert(0, "23:59")
            buttons["保存"].invoke()

        result, _seen = self._open(action, **self._prefill())
        self.assertEqual(result["start"], "00:00")
        self.assertEqual(result["end"], "23:59")


if __name__ == "__main__":
    unittest.main(verbosity=2)
