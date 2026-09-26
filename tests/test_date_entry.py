"""日期输入控件：短横线必须删不掉。

用户原话：「我要求你把所有能够修改日期的地方中 - 改为不可删除」。

控件的做法是"分隔符根本不是文本"（年、月、日三个输入框 + 两个 Label 短横线），
所以这里既验行为（删不掉、敲不进非数字），也验结构（短横线是 Label，不是 Entry 里的字符）。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date as Date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.date_entry import DateEntry  # noqa: E402


class DateEntryTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")
        # 不能 withdraw：藏起来的窗口拿不到键盘焦点。还要 focus_force ——
        # 测试进程不是前台程序时，Tk 的焦点链是空的，focus_get() 会返回 None。
        self.root.geometry("+4000+4000")
        self.root.focus_force()
        self.entry = DateEntry(self.root, value="2026-10-01")
        self.root.update()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    # -- 结构：短横线不是文本 --------------------------------------------
    def test_dashes_are_labels_not_text(self):
        """短横线是独立的 Label —— 它不在任何输入框里，所以删不掉。"""
        import tkinter as tk

        dashes = [child for child in self.entry.winfo_children()
                  if isinstance(child, tk.Label)]
        self.assertEqual(len(dashes), 2, "应该有且只有两个短横线分隔标签")
        for dash in dashes:
            self.assertEqual(dash.cget("text"), "-")
        for widget in (self.entry.year, self.entry.month, self.entry.day):
            self.assertNotIn("-", widget.get())

    def test_only_digits_can_get_in(self):
        """往输入框里塞非数字会被拒绝（控件只放行数字）。"""
        validate = self.entry.year.cget("validate")
        command = self.entry.year.cget("validatecommand")
        self.assertEqual(validate, "key")
        self.assertTrue(command)

    def test_separators_survive_clearing(self):
        """清空/全删之后，短横线仍然在（因为它们是标签）。"""
        self.entry.year.delete(0, "end")
        self.entry.month.delete(0, "end")
        self.entry.day.delete(0, "end")
        self.root.update()
        dashes = [child for child in self.entry.winfo_children()
                  if child.winfo_class() == "Label"]
        self.assertEqual([dash.cget("text") for dash in dashes], ["-", "-"])
        self.assertEqual(self.entry.get(), "")

    # -- 取值 ------------------------------------------------------------
    def test_get_returns_padded_iso_date(self):
        self.assertEqual(self.entry.get(), "2026-10-01")
        self.entry.set("2026-1-5")
        self.assertEqual(self.entry.get(), "2026-01-05")

    def test_accepts_the_usual_date_notations(self):
        for value in ("2026-10-01", "2026/10/1", "2026.10.01", "20261001", Date(2026, 10, 1)):
            with self.subTest(value=value):
                self.entry.set(value)
                self.assertEqual(self.entry.get(), "2026-10-01")

    def test_incomplete_date_returns_empty(self):
        self.entry.set("2026-")
        self.assertFalse(self.entry.is_complete())
        self.assertEqual(self.entry.get(), "")
        self.entry.set("2026-10")
        self.assertFalse(self.entry.is_complete())
        self.entry.set("2026-10-0")
        self.assertEqual(self.entry.get(), "2026-10-00")   # 只补零，不猜日期

    def test_set_with_a_real_date_object(self):
        self.entry.set(Date(2027, 1, 3))
        self.assertEqual((self.entry.year_var.get(), self.entry.month_var.get(),
                          self.entry.day_var.get()), ("2027", "01", "03"))
        self.assertEqual(self.entry.get(), "2027-01-03")

    def test_clear(self):
        self.entry.clear()
        self.assertEqual(self.entry.get(), "")
        self.assertEqual(self.entry.year_var.get(), "")

    # -- 输入联动 --------------------------------------------------------
    # 说明：这一台机器上 Tk 的焦点链在无人操作时拿不到（focus_get() 恒为 "."），
    # 所以这里不查"焦点现在在哪"，而是查"控件有没有主动把焦点交给下一格"——
    # 用替身记录 focus_set 调用，既确定又不依赖运行环境。
    def _spy_focus(self, widget):
        """把 widget.focus_set 换成记录器，返回 (调用记录, 还原函数)。"""
        calls: list[int] = []
        real = widget.focus_set
        widget.focus_set = lambda: (calls.append(1), real())[1]

        def restore() -> None:
            try:
                del widget.focus_set
            except AttributeError:
                pass

        return calls, restore

    def test_typing_four_digits_moves_to_the_month(self):
        self.entry.clear()
        calls, restore = self._spy_focus(self.entry.month)
        try:
            for char in "2026":
                self.entry.year.insert("end", char)
                self.root.update()
        finally:
            restore()
        self.assertEqual(self.entry.year_var.get(), "2026")
        self.assertEqual(calls, [1], "年填满后应把焦点交给月")

    def test_extra_digits_beyond_the_limit_are_dropped(self):
        self.entry.clear()
        self.entry.year.insert("end", "123456")
        self.root.update()
        self.assertEqual(self.entry.year_var.get(), "1234")

    def test_paste_splits_into_three_fields(self):
        self.root.clipboard_clear()
        self.root.clipboard_append("2026-10-01")
        self.entry.clear()
        self.entry._on_paste(None)
        self.root.update()
        self.assertEqual(self.entry.get(), "2026-10-01")

    def test_backspace_in_an_empty_field_jumps_back(self):
        self.entry.clear()
        calls, restore = self._spy_focus(self.entry.year)
        try:

            class Event:
                widget = self.entry.month

            self.entry._on_backspace(Event())
        finally:
            restore()
        self.assertEqual(calls, [1], "在空框里按退格应退回上一格")

    def test_backspace_in_a_filled_field_does_not_jump_back(self):
        self.entry.set("2026-10-01")
        calls, restore = self._spy_focus(self.entry.month)
        try:

            class Event:
                widget = self.entry.year

            self.entry._on_backspace(Event())
        finally:
            restore()
        self.assertEqual(calls, [], "框里还有字就不该跳走")


if __name__ == "__main__":
    unittest.main(verbosity=2)
