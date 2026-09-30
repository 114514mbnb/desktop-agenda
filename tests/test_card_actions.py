"""卡片右键菜单四个动作的回归测试。

用户报的原话：「你加入的这些功能除了《标记已完成》其他的都有不同程度的 BUG」。
一测就露出来两个**运行时才炸的漏导入**（`ttk` / `messagebox`）：

  * 「修改结束时间…」→ `ask_end_time` 里用 `ttk.Combobox`，而 `panel.py` 没导入 `ttk`
    → NameError，对话框根本不出现；
  * 「删除此条…」→ `card_delete` 里用 `messagebox.askyesno`，同样没导入
    → NameError，点了没反应。

这类错误 `compileall` 抓不到（语法没错），只有真的点到那个菜单才会炸；
所以下面既测**行为**（数据有没有真的改），也加了一条**静态检查**（见 test_smoke）。
"""

from __future__ import annotations

import ctypes
import json
import sys
import tempfile
import tkinter as tk
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agenda.panel as panel_mod  # noqa: E402
from agenda import hotkey  # noqa: E402
from agenda.panel import AgendaPanel  # noqa: E402
from tests import ClipboardSafeTestCase  # noqa: E402

IS_WINDOWS = sys.platform == "win32"


def _parse_geometry(geometry: str) -> tuple[int, int, int, int]:
    """把 `263x180+138+140` 拆成 (宽, 高, x, y)。"""
    size, rest = str(geometry).split("+", 1)
    width, height = (int(part) for part in size.split("x"))
    # 负坐标写出来是 `+-100`，split("+") 会多出空串
    parts = [part for part in rest.split("+") if part not in ("", "-")]
    x, y = int(parts[0]), int(parts[1])
    return width, height, x, y


def _register_dialog_probe(panel, title: str) -> dict:
    """等对话框真的映射出来，再把它的几何/层级记进这个字典，然后关掉它。

    为什么要等：位置和大小是 `_center_over` 设的，但 Tk 直到映射阶段才真正落定；
    刚 `winfo_geometry()` 出来的值可能还是映射前的临时值（实测拿到过 +0+0）。
    600ms 足够让映射完成，也足够暴露"映射时被重排"这类问题。
    """
    seen: dict = {}
    panel.root.after(600, lambda: _close_dialogs(panel, title, seen))
    return seen


def _close_dialogs(panel, title: str, seen: dict) -> None:
    for child in panel.root.winfo_children():
        if isinstance(child, tk.Toplevel) and child.title() == title:
            seen.update(_read_dialog(child))
            child.destroy()
            return
    seen["missing"] = True              # 600ms 了还没出现 → 调用方要报错


def _read_dialog(child) -> dict:
    return {
        "geometry": child.winfo_geometry(),
        "topmost": child.attributes("-topmost"),
        "mapped": child.winfo_ismapped(),
    }


def _find_dialog(panel, title: str):
    """非模态的对话框（如「事项详情」）：转几圈事件循环再去找它。"""
    for _ in range(20):
        panel.root.update()
        for child in panel.root.winfo_children():
            if isinstance(child, tk.Toplevel) and child.title() == title:
                return child
        panel.root.after(20)
        panel.root.update()
    return None


class CardActionTests(ClipboardSafeTestCase):
    def setUp(self):
        super().setUp()          # 先存剪贴板：下面「复制内容」会真的写系统剪贴板
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-07",
            "periods": [["08:00", "08:45"], ["08:55", "09:40"]],
            "courses": [],
        }, ensure_ascii=False), encoding="utf-8")
        self._write_events(["班会：材料提交", "讲座：多模态大模型"])
        try:
            # hide_past=False：这条用例要看的是"卡片上的操作"，而事件时间写死在 12:00。
            # 面板默认会隐藏**已过期**的通知 —— 不关掉的话，晚上跑测试卡片就没了
            # （同一个提交凌晨绿、晚上红，真踩过）。
            self.panel = AgendaPanel(self.data, pipeline_ms=0, autostart_pipeline=False,
                                     hide_past=False,
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
    def _write_events(self, titles: list[str]) -> None:
        today = date.today().strftime("%Y-%m-%d")
        (self.data / "events.json").write_text(json.dumps({
            "schemaVersion": 1,
            "events": [
                {"id": f"ev-{index}", "title": title, "date": today,
                 "start": f"{12 + index:02d}:00", "end": f"{13 + index:02d}:00",
                 "people": ["班长"], "notes": "交到辅导员办公室", "group": "2026级群",
                 "tentative": False}
                for index, title in enumerate(titles)
            ],
        }, ensure_ascii=False), encoding="utf-8")

    def _events(self) -> list[dict]:
        payload = json.loads((self.data / "events.json").read_text(encoding="utf-8"))
        return payload.get("events", [])

    def _card(self, title_prefix: str = ""):
        for section in self.panel.timeline.sections:
            for card in section.cards:
                if card.kind == "event" and (not title_prefix or
                                             card.title.startswith(title_prefix)):
                    return card
        self.fail("没有找到通知卡片")

    # -- 四个动作 --------------------------------------------------------
    def test_copy_puts_the_text_on_the_clipboard(self):
        """「复制内容」：内容要真的进系统剪贴板（面板不是置顶窗口也要能复制）。"""
        if not IS_WINDOWS:
            self.skipTest("剪贴板读取是 Windows 专有")
        card = self._card("班会")
        hotkey.write_clipboard_text("（复制前的占位）")
        self.panel.card_copy(card)
        self.panel.root.update()
        copied = hotkey.read_clipboard_text()
        self.assertIn(card.title, copied, f"剪贴板里没有卡片内容：{copied!r}")
        self.assertIn(card.time_label, copied)

    def test_edit_end_time_writes_the_new_value(self):
        """「修改结束时间…」：改完要落盘（用桩值代替对话框，只验数据链路）。"""
        card = self._card("班会")
        real = panel_mod.ask_end_time
        panel_mod.ask_end_time = lambda *a, **kw: "15:30"
        try:
            self.panel.card_edit_end(card)          # 这里以前会 NameError: ttk
        finally:
            panel_mod.ask_end_time = real
        self.panel.root.update()
        ends = [event.get("end") for event in self._events()]
        self.assertIn("15:30", ends, f"结束时间没有写进 events.json：{ends}")

    def test_edit_end_time_rejects_earlier_time(self):
        card = self._card("班会")
        real = panel_mod.ask_end_time
        panel_mod.ask_end_time = lambda *a, **kw: "00:01"      # 早于开始时间
        toasts: list[str] = []
        self.panel._toast = lambda message, **kw: toasts.append(message)
        try:
            self.panel.card_edit_end(card)
        finally:
            panel_mod.ask_end_time = real
        self.assertTrue(any("晚于" in message for message in toasts), toasts)
        self.assertNotIn("00:01", [event.get("end") for event in self._events()])

    def test_edit_end_time_cancel_changes_nothing(self):
        card = self._card("班会")
        before = self._events()
        real = panel_mod.ask_end_time
        panel_mod.ask_end_time = lambda *a, **kw: None        # 用户点了取消
        try:
            self.panel.card_edit_end(card)
        finally:
            panel_mod.ask_end_time = real
        self.assertEqual(self._events(), before)

    def test_delete_removes_the_event_after_confirming(self):
        """「删除此条…」：确认后要真删（这里以前会 NameError: messagebox）。"""
        card = self._card("班会")
        real = panel_mod.messagebox.askyesno
        panel_mod.messagebox.askyesno = lambda *a, **kw: True
        try:
            self.panel.card_delete(card)
        finally:
            panel_mod.messagebox.askyesno = real
        self.panel.root.update()
        titles = [event.get("title") for event in self._events()]
        self.assertNotIn(card.title, titles, f"没有删掉：{titles}")
        self.assertEqual(len(titles), 1, "只该删掉选中的那一条")

    def test_delete_keeps_the_event_when_cancelled(self):
        card = self._card("班会")
        before = self._events()
        real = panel_mod.messagebox.askyesno
        panel_mod.messagebox.askyesno = lambda *a, **kw: False
        try:
            self.panel.card_delete(card)
        finally:
            panel_mod.messagebox.askyesno = real
        self.assertEqual(self._events(), before, "点了取消却删掉了")

    def test_done_removes_the_event(self):
        """对照组：这条一直好使，别在重构里弄坏。"""
        card = self._card("班会")
        self.panel.card_done(card)
        self.panel.root.update()
        titles = [event.get("title") for event in self._events()]
        self.assertNotIn(card.title, titles)

    def test_course_actions_explain_themselves(self):
        """课程卡片不能删/不能标记完成，但必须给出说法而不是静默失败。"""
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-07",
            "periods": [["08:00", "08:45"], ["08:55", "09:40"]],
            "courses": [{"name": "高等数学", "weekday": "周一", "period": "1-2",
                         "weeks": "1-18", "teacher": "陈立", "location": "A-101"}],
        }, ensure_ascii=False), encoding="utf-8")
        self.panel.refresh()
        self.panel.root.update()
        course = None
        for section in self.panel.timeline.sections:
            for card in section.cards:
                if card.kind == "course":
                    course = card
        if course is None:
            self.skipTest("这一周没有课程卡片可测")
        toasts: list[str] = []
        self.panel._toast = lambda message, **kw: toasts.append(message)
        self.panel.card_done(course)
        self.panel.card_delete(course)
        self.assertEqual(len(toasts), 2, f"课程卡片的两个动作没有提示：{toasts}")
        self.assertTrue(all("课程" in message for message in toasts), toasts)


@unittest.skipUnless(IS_WINDOWS, "窗口层级是 Windows 概念")
class PanelDialogVisibilityTests(unittest.TestCase):
    """面板弹的对话框必须**看得见**。

    用户报「修改结束时间 / 删除此条都有 BUG」，其中一半原因是：面板默认沉在桌面层，
    它弹的对话框跟着一起被别的窗口盖住 → 点了菜单像没反应。
    实测桌面模式下对话框的 `-topmost` 是 0，现在是 1。
    """

    def test_end_time_dialog_is_topmost_and_centered(self):
        from agenda.panel import ask_end_time

        with tempfile.TemporaryDirectory() as tmp:
            panel = AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                                hide_past=False,
                                window_mode="desktop", position=(80, 60))
            try:
                panel.root.update()
                seen = _register_dialog_probe(panel, "修改结束时间")
                ask_end_time(panel.root, "班会：材料提交", "12:00", "13:00")
                self.assertIn("geometry", seen, "对话框没出现")
                self.assertEqual(int(seen["topmost"]), 1, "对话框没有置顶（会被别的窗口盖住）")
                width, height, x, y = _parse_geometry(seen["geometry"])
                self.assertNotEqual((width, height), (200, 200), "对话框停在了 Tk 的默认空尺寸")
                self.assertGreater(x, 0, "对话框没有摆到面板附近，停在屏幕左上角了")
                self.assertGreater(y, 0, "对话框没有摆到面板附近，停在屏幕左上角了")
            finally:
                panel.quit()

    def test_detail_dialog_is_topmost_and_centered(self):
        """「事项详情」（双击卡片）同样不能停在左上角的默认空窗里。"""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            today = date.today().strftime("%Y-%m-%d")
            (data / "timetable.json").write_text(json.dumps({
                "termStart": "2026-09-07",
                "periods": [["08:00", "08:45"]],
                "courses": [],
            }, ensure_ascii=False), encoding="utf-8")
            (data / "events.json").write_text(json.dumps({
                "schemaVersion": 1,
                "events": [{"id": "ev-0", "title": "班会：材料提交", "date": today,
                            "start": "12:00", "end": "13:00", "notes": "交到辅导员办公室"}],
            }, ensure_ascii=False), encoding="utf-8")
            panel = AgendaPanel(data, pipeline_ms=0, autostart_pipeline=False,
                                hide_past=False,
                                window_mode="desktop", position=(80, 60))
            try:
                panel.root.update()
                panel.refresh()
                panel.root.update()
                card = None
                for section in panel.timeline.sections:
                    for candidate in section.cards:
                        if candidate.kind == "event":
                            card = candidate
                self.assertIsNotNone(card, "没有卡片可以双击")
                panel.show_card_detail(card)
                # 详情窗是非模态的（没有 wait_window），所以要马上找它、马上量
                dialog = _find_dialog(panel, "事项详情")
                self.assertIsNotNone(dialog, "详情窗没出现")
                seen = _read_dialog(dialog)
                self.assertEqual(int(seen["topmost"]), 1)
                width, height, x, y = _parse_geometry(seen["geometry"])
                self.assertGreater(width, 200, f"详情窗尺寸不对：{seen['geometry']}")
                self.assertGreater(y, 0, "详情窗停在屏幕左上角了")
                dialog.destroy()
            finally:
                panel.quit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
