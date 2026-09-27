"""导入链路的过程级测试：识别结果 → 确认窗（可编辑）→ 落盘。

不点鼠标，只调用同一个入口（ControlWindow._apply_import），把一个假的识别结果
推进去，然后在事件循环里操作确认窗：改一格、加一行、删一行、重新识别、确认导入。
钉住的是"改完之后真的写进了 timetable.json"，因为这正是用户最容易被坑的一步。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda.course_review import validate_rows  # noqa: E402
from agenda.date_entry import DateEntry  # noqa: E402
from agenda.eas.base import Course, FetchResult  # noqa: E402


def _fetch_result() -> FetchResult:
    return FetchResult(courses=[
        Course(name="数学分析1", weekday=2, start_period=1, end_period=2,
               teacher="陈立", location="中心校区 A-330", weeks=tuple(range(1, 17))),
        Course(name="军事理论", weekday=3, start_period=1, end_period=2,
               teacher="秦政", location="E-302", weeks=tuple(range(1, 17))),
    ])


class ImportFlowTests(unittest.TestCase):
    """确认窗是真的能改、改完是真的按改后的内容落盘。"""

    def setUp(self):
        from agenda import control_window as control_window_mod
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow
        from agenda.course_review import CourseReviewDialog

        # 模态对话框在单测里没人点，会一直挂着拖慢整套（实测 6 个用例要 58 秒）
        self._real_boxes = (
            control_window_mod.messagebox.showinfo, control_window_mod.messagebox.showwarning,
            control_window_mod.messagebox.showerror, control_window_mod.messagebox.askyesno,
        )
        self.info_calls: list[str] = []
        control_window_mod.messagebox.showinfo = lambda title, message, **kw: self.info_calls.append(message)
        control_window_mod.messagebox.showwarning = lambda *a, **kw: None
        control_window_mod.messagebox.showerror = lambda *a, **kw: None
        control_window_mod.messagebox.askyesno = lambda *a, **kw: True

        self.review_type = CourseReviewDialog
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        (self.data_dir / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-07",
            "periods": [["08:00", "09:50"], ["10:10", "12:00"]],
            "courses": [],
        }, ensure_ascii=False), encoding="utf-8")

        # 用真的 ClientConfig（窗口要读 console_x/y 等一串字段），
        # 只把"会真的起进程"的那几个动作换成假的
        config = ClientConfig.load(self.data_dir)
        config.confirm_import = True
        config.panel_visible = False
        controller = SimpleNamespace(
            data_dir=self.data_dir,
            config=config,
            load_timetable_payload=lambda: json.loads(
                (self.data_dir / "timetable.json").read_text(encoding="utf-8")),
            save_timetable_payload=self._save,
            restart_panel=lambda: None,
        )
        self.saved: list[dict] = []
        try:
            self.window = ControlWindow(controller)
        except Exception as error:            # 无图形环境（CI）时跳过
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        # 主窗就是 ControlWindow 自己的 Tk；不能 withdraw——Tk 会把子窗
        # （确认窗挂在同一个 root 下）一起藏掉，测试就永远等不到确认窗。
        self.root = self.window.root
        self.root.geometry("+4000+4000")

    def tearDown(self):
        from agenda import control_window as control_window_mod
        (control_window_mod.messagebox.showinfo, control_window_mod.messagebox.showwarning,
         control_window_mod.messagebox.showerror, control_window_mod.messagebox.askyesno) = self._real_boxes
        self.window.stop_tick()      # 先停定时器，否则销毁后 Tk 会报 invalid command name
        for widget in list(self.root.winfo_children()):
            if isinstance(widget, self.review_type):
                widget.destroy()
        try:
            self.root.update_idletasks()   # 把 ttk 排队的 <<ThemeChanged>> 消化掉再销毁
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            self.root.update_idletasks()
        except Exception:
            pass                        # 销毁后的 ThemeChanged 是 Tk 的收尾噪音，忽略
        self.tmp.cleanup()

    def _save(self, payload: dict) -> Path:
        self.saved.append(payload)
        path = self.data_dir / "timetable.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    # -- 工具 ------------------------------------------------------------
    def _pump(self, seconds: float = 0.6) -> None:
        import time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)

    def _open_import(self, *, retry=None) -> object:
        self.window._apply_import(_fetch_result(), source="测试来源", on_retry=retry)
        self._pump(0.8)
        dialogs = [child for child in self.root.winfo_children()
                   if isinstance(child, self.review_type)]
        self.assertEqual(len(dialogs), 1, "确认窗没有打开")
        return dialogs[0]

    def _edit(self, dialog, item: str, column: str, value: str) -> None:
        import types
        bbox = dialog.tree.bbox(item, column)
        self.assertTrue(bbox, f"单元格取不到坐标：{item}/{column}")
        dialog.editor._begin(types.SimpleNamespace(x=bbox[0] + 8, y=bbox[1] + 8))
        self._pump(0.15)
        widget = dialog.editor._widget
        self.assertIsNotNone(widget, "编辑框没有弹出来")
        if hasattr(widget, "set"):
            widget.set(value)
        else:
            widget.delete(0, "end")
            widget.insert(0, value)
        dialog.editor._commit()
        self._pump(0.15)

    # -- 测试 ------------------------------------------------------------
    def test_edited_values_are_what_gets_saved(self):
        dialog = self._open_import()
        self.assertEqual(len(dialog.tree.get_children()), 2)

        self._edit(dialog, "0", "location", "中心校区 A-330（改过）")
        self._edit(dialog, "1", "weekday", "周五")
        self._edit(dialog, "1", "weeks", "1-8周")

        dialog.confirm()
        self._pump(0.8)

        payload = json.loads((self.data_dir / "timetable.json").read_text(encoding="utf-8"))
        courses = {course["name"]: course for course in payload["courses"]}
        self.assertEqual(len(courses), 2)
        self.assertEqual(courses["数学分析1"]["location"], "中心校区 A-330（改过）")
        self.assertEqual(courses["军事理论"]["weekday"], "周五")
        self.assertEqual(courses["军事理论"]["weeks"], "1-8周")
        self.assertIn("已人工确认", payload["note"])
        self.assertTrue(any("已导入 2 门课" in text for text in self.info_calls), self.info_calls)

    def test_add_and_delete_change_what_is_saved(self):
        dialog = self._open_import()
        dialog.add_row()
        self._pump(0.2)
        self.assertEqual(len(dialog.tree.get_children()), 3)

        dialog.rows[-1].update({
            "name": "体育1", "weekday": "周二", "period": "9-10",
            "weeks": "1-18", "location": "体育馆", "teacher": "李老师",
        })
        dialog._populate()
        self._pump(0.2)

        dialog.tree.selection_set("0")
        dialog.delete_selected()
        self._pump(0.2)
        self.assertEqual(len(dialog.tree.get_children()), 2)

        dialog.confirm()
        self._pump(0.6)
        payload = json.loads((self.data_dir / "timetable.json").read_text(encoding="utf-8"))
        names = sorted(course["name"] for course in payload["courses"])
        self.assertEqual(names, ["体育1", "军事理论"])

    def test_cancel_saves_nothing(self):
        done: list[int] = []
        dialog = self._open_import()
        self._edit(dialog, "0", "name", "不该被保存")
        dialog.cancel()
        self._pump(0.4)
        payload = json.loads((self.data_dir / "timetable.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["courses"], [])
        self.assertEqual(self.saved, [])
        self.assertEqual(done, [])

    def test_retry_calls_back_instead_of_saving(self):
        calls: list[int] = []
        done: list[int] = []
        dialog = self._open_import(retry=lambda: calls.append(1))
        dialog.on_retry = lambda: (calls.append(1), done.append(1))
        dialog.retry()
        self._pump(0.4)
        self.assertEqual(calls, [1])
        self.assertEqual(self.saved, [])

    def test_on_done_fires_once_when_dialog_closes(self):
        """确认窗一关就该收掉浏览器会话（且只收一次，别被子控件的 Destroy 事件重复触发）。"""
        done: list[int] = []
        dialog = self._open_import(retry=lambda: None)
        self.window._apply_import(_fetch_result(), source="测试来源二",
                                  on_retry=lambda: None, on_done=lambda: done.append(1))
        self._pump(0.5)
        dialogs = [child for child in self.root.winfo_children()
                   if isinstance(child, self.review_type)]
        dialogs[-1].cancel()
        self._pump(0.5)
        self.assertEqual(done, [1])

    def test_invalid_weekday_is_reverted(self):
        dialog = self._open_import()
        dialog.editor._commit()   # 没有打开编辑器时提交应当是安全的空操作
        dialog._on_cell_commit("0", "weekday", "周八")
        self.assertEqual(dialog.tree.set("0", "weekday"), "周三")



class FileImportRowsTests(unittest.TestCase):
    """文件识别的行结构必须能直接喂进确认窗（字段名对得上才算通）。"""

    def test_rows_have_exactly_the_review_columns(self):
        from agenda.course_review import CourseReviewDialog
        from agenda.file_import import parse_ics

        ics = Path(__file__).resolve().parent / "fixtures" / "wakeup-sample.ics"
        result = parse_ics(ics)
        self.assertGreater(result.count, 0)
        self.assertEqual(result.warnings, [])
        for row in result.courses:
            self.assertEqual(
                set(row) - set(CourseReviewDialog.COLUMNS), set(),
                f"多出确认窗没有的列：{row}",
            )
            for column in CourseReviewDialog.COLUMNS:
                self.assertIn(column, row)

    def test_bad_file_becomes_a_warning_not_an_exception(self):
        """畸形/看不懂的文件不能让导入崩掉：要变成一句能读懂的提示。"""
        import tempfile

        from agenda.file_import import import_path

        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "课表.ics"
            broken.write_text("这不是日历文件", encoding="utf-8")
            result = import_path(broken)
            self.assertEqual(result.count, 0)
            self.assertTrue(result.warnings)

    def test_unknown_suffix_lists_what_is_supported(self):
        import tempfile

        from agenda.file_import import import_path

        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "课表.xlsx"
            other.write_bytes(b"PK\x03\x04")
            result = import_path(other)
            self.assertEqual(result.count, 0)
            self.assertIn(".ics", result.warnings[0])


class PeriodTableTests(unittest.TestCase):
    """上课时间表：节数可自定义，时间用「时:分」两个下拉框填（冒号不用敲）。

    这里不建真窗口，直接构造 _read_period_table 需要的四个 StringVar，
    把"末尾空行 / 中间缺口 / 下课早于上课"三种情况钉住。
    """

    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def make_window(self, rows: list[list[str]]):
        import tkinter as tk

        from agenda.control_window import ControlWindow

        window = ControlWindow.__new__(ControlWindow)
        window.root = self.root
        window.period_rows = [
            tuple(tk.StringVar(master=self.root, value=value) for value in row)
            for row in rows
        ]
        return window

    def test_reads_hh_mm_pairs(self):
        window = self.make_window([["08", "00", "08", "45"], ["08", "55", "09", "40"]])
        self.assertEqual(window._read_period_table(), [["08:00", "08:45"], ["08:55", "09:40"]])

    def test_trailing_empty_rows_are_dropped_not_fatal(self):
        """把节数调大但没填完是常见状态，保存时按"配到第几节"截断即可，不该报错。"""
        window = self.make_window([
            ["08", "00", "08", "45"], ["08", "55", "09", "40"],
            ["", "", "", ""], ["", "", "", ""],
        ])
        self.assertEqual(len(window._read_period_table()), 2)

    def test_gap_in_the_middle_is_an_error(self):
        window = self.make_window([
            ["08", "00", "08", "45"], ["", "", "", ""], ["10", "10", "10", "55"],
        ])
        with self.assertRaises(ValueError) as caught:
            window._read_period_table()
        self.assertIn("中间不可留空", str(caught.exception))

    def test_end_must_be_after_start(self):
        window = self.make_window([["10", "00", "09", "00"]])
        with self.assertRaises(ValueError) as caught:
            window._read_period_table()
        self.assertIn("不晚于", str(caught.exception))

    def test_all_empty_is_an_error(self):
        window = self.make_window([["", "", "", ""]])
        with self.assertRaises(ValueError):
            window._read_period_table()

    def test_split_time_accepts_full_width_colon_and_rejects_junk(self):
        from agenda.control_window import ControlWindow
        self.assertEqual(ControlWindow._split_time("08：00"), ["08", "00"])
        self.assertEqual(ControlWindow._split_time("8:5"), ["08", "05"])
        self.assertEqual(ControlWindow._split_time("25:00"), ["", ""])
        self.assertEqual(ControlWindow._split_time("上午"), ["", ""])

    def test_term_start_must_be_a_monday(self):
        """周次按"第 1 周周一"算，填个周六会让整学期的第几周错位（实机踩过）。"""
        import tkinter as tk

        from agenda.control_window import ControlWindow

        window = ControlWindow.__new__(ControlWindow)
        window.root = self.root
        window.term_start_entry = DateEntry(self.root, value="2026-09-05")   # 周六
        term, error = window._checked_term_start()
        self.assertEqual(term, "2026-08-31")          # 那一周的周一
        self.assertEqual(
            datetime.strptime(term, "%Y-%m-%d").date().weekday(), 0,
            "校准后的日期必须是周一",
        )
        self.assertIn("并非周一", error)

    def test_term_start_accepts_a_real_monday_silently(self):
        import tkinter as tk

        from agenda.control_window import ControlWindow

        window = ControlWindow.__new__(ControlWindow)
        window.root = self.root
        window.term_start_entry = DateEntry(self.root, value="2026-09-07")
        term, error = window._checked_term_start()
        self.assertEqual((term, error), ("2026-09-07", ""))

    def test_term_start_requires_a_complete_date(self):
        """日期控件填不全（比如只填了年）时，要明确说"没填完"，而不是当成"没填"。"""
        from agenda.control_window import ControlWindow

        window = ControlWindow.__new__(ControlWindow)
        window.root = self.root
        for raw, expect_error in (("2026-09-07", False), ("", True)):
            with self.subTest(raw=raw):
                window.term_start_entry = DateEntry(self.root, value=raw)
                _term, error = window._checked_term_start()
                self.assertEqual(bool(error), expect_error)


class ClientRecoveryTests(unittest.TestCase):
    """控制台"最小化到托盘"之后必须还能叫回来。

    踩过的坑：窗口是隐藏的（不是关闭），进程还在，但再点一次桌面快捷方式时
    单实例保护**安静地直接退出**——用户看到的就是"客户端怎么打不开了？"（真踩过）。
    现在第二次启动会写一个请求文件，正在跑的客户端看到就把窗口叫出来。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_request_show_returns_false_when_nobody_runs(self):
        from agenda.client_app import AppController

        controller = AppController(self.data_dir)
        self.assertFalse(controller.request_show(), "没人跑的时候不该假装写成功")
        self.assertFalse((self.data_dir / "client.show").exists())

    def test_request_show_writes_flag_when_client_alive(self):
        import os

        from agenda.client_app import SHOW_REQUEST, AppController

        (self.data_dir / "client.lock").write_text(str(os.getpid()), encoding="utf-8")
        controller = AppController(self.data_dir)
        self.assertEqual(controller.client_pid(), os.getpid())
        self.assertTrue(controller.request_show())
        self.assertTrue((self.data_dir / SHOW_REQUEST).exists())

    def test_stale_lock_is_treated_as_nobody_running(self):
        from agenda.client_app import AppController

        (self.data_dir / "client.lock").write_text("999999", encoding="utf-8")
        controller = AppController(self.data_dir)
        self.assertIsNone(controller.client_pid())
        self.assertFalse(controller.request_show())

    def test_closing_the_console_does_not_touch_the_panel(self):
        """点控制台的 X：只最小化至托盘，**绝不能顺手关掉桌面面板**。

        用户原话：「我关闭客户端的时候默认自动关闭日程表，我要求客户端关闭进入状态栏中
        不会影响日程表的显示，只有在退出应用的时候才会关闭」。
        """
        from agenda import control_window as control_window_mod
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow

        config = ClientConfig.load(self.data_dir)
        config.panel_visible = True
        config.tray_notice_shown = False
        stopped: list[int] = []
        saved: list[int] = []
        controller = SimpleNamespace(
            data_dir=self.data_dir, config=config,
            load_timetable_payload=lambda: json.loads(
                (self.data_dir / "timetable.json").read_text(encoding="utf-8")),
            save_timetable_payload=lambda payload: None,
            restart_panel=lambda: None,
            stop_panel=lambda: stopped.append(1),
            refresh_events=lambda: [], panel_running=lambda: True, panel_pid=lambda: 1,
            save=lambda: saved.append(1),
        )
        (self.data_dir / "timetable.json").write_text(
            json.dumps({"termStart": "2026-09-07", "periods": [["08:00", "08:50"]], "courses": []},
                       ensure_ascii=False), encoding="utf-8")
        try:
            window = ControlWindow(controller)
        except Exception as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")
        notices: list[str] = []
        real_info = control_window_mod.messagebox.showinfo
        control_window_mod.messagebox.showinfo = lambda title, message, **kw: notices.append(title)
        try:
            window.root.update()
            window.on_close()
            window.root.update()
            withdrawn = window.root.state() == "withdrawn"
        finally:
            control_window_mod.messagebox.showinfo = real_info
            try:
                window.stop_tick()
            except Exception:
                pass
            try:
                window.root.destroy()
            except Exception:
                pass
        self.assertEqual(stopped, [], "关窗口不该停掉面板进程")
        self.assertTrue(withdrawn, "关窗口 = 最小化至托盘（withdraw）")
        self.assertTrue(saved, "窗口位置要落盘")
        self.assertTrue(config.tray_notice_shown, "第一次要提示一次去哪儿退出")
        self.assertIn("已最小化至托盘", notices)
        # 再关一次就不再打扰
        self.assertEqual(config.close_action, "tray")

    def test_start_console_short_circuits_when_client_already_runs(self):


        """客户端在跑的时候，start_console 只发"显示窗口"请求，不该再起一个进程。

        面板单独跑起来（没有客户端）时用户点「打开客户端窗口」，
        得把客户端拉起来——但反过来，客户端明明在跑就不能重复拉。
        """
        from agenda import client_app as client_app_mod
        from agenda.client_app import AppController

        controller = AppController(self.data_dir)
        calls: list[str] = []

        def fake_request_show() -> bool:
            calls.append("show")
            return True

        controller.request_show = fake_request_show
        started: list = []
        real_popen = client_app_mod.subprocess.Popen
        client_app_mod.subprocess.Popen = lambda *a, **kw: started.append(a) or SimpleNamespace()
        try:
            self.assertTrue(controller.start_console())
        finally:
            client_app_mod.subprocess.Popen = real_popen
        self.assertEqual(calls, ["show"])
        self.assertEqual(started, [], "客户端在跑的时候不该再拉一个进程")

    def test_start_console_spawns_client_with_hide_panel(self):
        """没人接请求时，真的把客户端拉起来——而且不能再拉起一个面板。"""
        from agenda import client_app as client_app_mod
        from agenda.client_app import AppController

        controller = AppController(self.data_dir)
        controller.request_show = lambda: False
        controller.client_pid = lambda: 4242      # 假装起来之后 lock 里有 pid
        seen: list[list[str]] = []

        class FakePopen:
            def __init__(self, command, **kwargs):
                seen.append(list(command))

        real_popen = client_app_mod.subprocess.Popen
        client_app_mod.subprocess.Popen = FakePopen
        try:
            self.assertTrue(controller.start_console())
        finally:
            client_app_mod.subprocess.Popen = real_popen
        self.assertEqual(len(seen), 1, "应该正好起一个客户端进程")
        command = " ".join(str(part) for part in seen[0])
        self.assertIn("--client", command)
        self.assertIn("--hide-panel", command, "客户端不该再拉一个重复的面板")

    def test_console_consumes_the_request_and_shows(self):
        """真起一个控制台，写请求文件，看它有没有把窗口显示出来并消费掉请求。"""
        from agenda.client_app import SHOW_REQUEST
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow

        config = ClientConfig.load(self.data_dir)
        config.panel_visible = False
        controller = SimpleNamespace(
            data_dir=self.data_dir, config=config,
            load_timetable_payload=lambda: json.loads(
                (self.data_dir / "timetable.json").read_text(encoding="utf-8")),
            save_timetable_payload=lambda payload: None,
            restart_panel=lambda: None, save=lambda: None,
            refresh_events=lambda: [], panel_running=lambda: False, panel_pid=lambda: None,
        )
        (self.data_dir / "timetable.json").write_text(
            json.dumps({"termStart": "2026-09-07", "periods": [["08:00", "08:50"]], "courses": []},
                       ensure_ascii=False), encoding="utf-8")
        try:
            window = ControlWindow(controller)
        except Exception as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")
        try:
            window.root.withdraw()                       # 等价于"最小化到托盘"
            (self.data_dir / SHOW_REQUEST).write_text("1", encoding="utf-8")
            window._check_show_request()
            window.root.update()
            self.assertFalse((self.data_dir / SHOW_REQUEST).exists(), "请求没被消费，会反复触发")
            self.assertTrue(bool(window.root.winfo_viewable()), "控制台没有被显示出来")
        finally:
            window.stop_tick()
            window.root.destroy()


class CardActionTests(unittest.TestCase):
    """单条日程的右键操作：标记完成 / 改结束时间 / 删除。

    需求原话：「我通知完成怎么取消通知，能不能给每一条显示在日程表上的日程加一个功能，
    右键该条日程后可以关闭或者修改结束时间」。
    """

    def setUp(self):
        import tkinter as tk

        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow

        # 注意：这里有**两个** Tk 根（探测用的 + ControlWindow 自己的）。
        # 之前还多建了一个探测 root 然后销毁，结果是整套跑完时进程崩掉
        # （Windows 退出码 0xC000041D），单独跑这个文件却没事。
        # 多 Tk 解释器 + ctypes 窗口过程回调凑一起很容易踩这种内存问题，
        # 所以这里就只保留一个，而且测试结束时一定要 destroy。
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用显示：{error}")

        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        # 用干净的 fixture，别拷 data/ 里的真实数据：用户的实际通知会让断言飘
        (self.data / "events.json").write_text(json.dumps({
            "schemaVersion": 1, "updatedAt": "2026-09-23 00:00:00",
            "lastRun": {}, "events": [],
        }, ensure_ascii=False), encoding="utf-8")
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-07",
            "periods": [["08:00", "08:50"], ["09:00", "09:50"]],
            "courses": [{"name": "高等数学", "weekday": "周三", "period": "1-2",
                         "location": "教三301", "teacher": "张伟"}],
        }, ensure_ascii=False), encoding="utf-8")

        self.config = ClientConfig.load(self.data)
        self.config.panel_visible = False
        from agenda.pipeline import Pipeline
        self.pipeline = Pipeline(self.data)
        self.controller = SimpleNamespace(
            data_dir=self.data, config=self.config, pipeline=self.pipeline,
            load_timetable_payload=lambda: json.loads(
                (self.data / "timetable.json").read_text(encoding="utf-8")),
            save_timetable_payload=lambda payload: (self.data / "timetable.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"),
            restart_panel=lambda: None, save=lambda: None,
            refresh_events=lambda: [],
            panel_running=lambda: False, panel_pid=lambda: None,
        )
        try:
            self.window = ControlWindow(self.controller)
        except Exception as error:
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")

    def tearDown(self):
        try:
            self.window.stop_tick()
            self.window.root.destroy()
        except Exception:
            pass
        self.tmp.cleanup()

    def _add_notice(self, title: str, start: str) -> str:
        from agenda.models import Candidate
        from agenda.store import EventStore

        store = EventStore.load(self.data / "events.json")
        store.merge([Candidate(title=title, date="2026-09-23", start=start,
                               location="3号楼201", group="测试群")])
        store.save()
        return store.sorted_events()[0].id

    def _cards(self):
        from datetime import date as _Date

        from agenda.store import EventStore
        from agenda.timeline import build_timeline
        from agenda.timetable import load_timetable

        store = EventStore.load(self.data / "events.json")
        table = load_timetable(self.data)
        return [card for section in build_timeline(
            store.sorted_events(), table, today=_Date(2026, 9, 23)).sections
            for card in section.cards]

    def test_notice_card_has_event_id(self):
        event_id = self._add_notice("交材料", "10:00")
        cards = [card for card in self._cards() if card.kind == "event"]
        self.assertTrue(cards, "通知没有变成卡片")
        self.assertEqual(cards[0].event_id, event_id)

    def test_course_card_has_course_index(self):
        cards = [card for card in self._cards() if card.kind == "course"]
        self.assertTrue(cards, "课程没有变成卡片")
        self.assertIsNotNone(cards[0].course_index)

    def test_agenda_done_removes_the_notice(self):
        from agenda.store import EventStore

        self._add_notice("交材料", "10:00")
        card = next(card for card in self._cards() if card.kind == "event")
        self.window.agenda_done(card)
        remaining = EventStore.load(self.data / "events.json").events
        self.assertEqual(remaining, [], "标记完成后这条应该不在 events.json 里了")
        self.assertIn("已完成", self.window.notice_hint.cget("text"))

    def test_agenda_edit_end_writes_event_end(self):
        from agenda.store import EventStore

        event_id = self._add_notice("交材料", "10:00")
        card = next(card for card in self._cards() if card.kind == "event")
        ok, message = self._apply_end(self.window, card, "11:30")
        self.assertTrue(ok, message)
        stored = next(e for e in EventStore.load(self.data / "events.json").events
                      if e.id == event_id)
        self.assertEqual(stored.end, "11:30")

    def test_course_edit_end_only_touches_that_course(self):
        """改课程结束时间只写这条的 endTime，绝不能动全校课时表。"""
        from agenda.timetable import load_timetable

        before = load_timetable(self.data)
        periods_before = list(before.periods)
        card = next(card for card in self._cards() if card.kind == "course")
        ok, message = self._apply_end(self.window, card, "09:30")
        self.assertTrue(ok, message)
        after = load_timetable(self.data)
        self.assertEqual(list(after.periods), periods_before, "课时表不该被改")
        self.assertEqual(after.courses[card.course_index].end_time, "09:30")
        # 生效：这门课的结束时间跟着变
        start, end = after.courses[card.course_index].times(after.periods)
        self.assertEqual(end, "09:30")

    def test_end_before_start_is_rejected(self):
        self._add_notice("交材料", "10:00")
        card = next(card for card in self._cards() if card.kind == "event")
        ok, message = self._apply_end(self.window, card, "09:00")
        self.assertFalse(ok)
        self.assertIn("晚于", message)

    def test_removing_event_leaves_reminder_list_usable(self):
        """删通知不能把"已提醒"名单搞乱。

        提醒键是 `日期|时间|标题`，跟 event id 对不上——所以只做长度修剪，
        不做语义删除：删错了会让别的通知重复弹窗，比留个过期键更糟。
        """
        from agenda.store import EventStore

        event_id = self._add_notice("交材料", "10:00")
        self.config.reminded = ["2026-09-23|10:00|交材料", "2026-09-23|15:00|开会"]
        card = next(card for card in self._cards() if card.kind == "event")
        self.assertTrue(self.window._remove_event(event_id))
        self.assertEqual(EventStore.load(self.data / "events.json").events, [])
        self.assertEqual(self.config.reminded,
                         ["2026-09-23|10:00|交材料", "2026-09-23|15:00|开会"],
                         "短名单不该被动过")

    def _apply_end(self, window, card, new_end: str):
        """走面板那条真写入路径（不弹窗，直接调底层），保证测的是同一份逻辑。

        `_apply_end_time` 是面板的方法，要用到 panel.data_dir / panel.pipeline；
        这里拿控制台当壳、临时补上这两个属性，避免把同一段写入逻辑再抄一遍。
        """
        from agenda.panel import AgendaPanel

        window.data_dir = self.data
        window.pipeline = self.pipeline
        return AgendaPanel._apply_end_time(window, card, new_end)


class CourseEditPrefillTests(unittest.TestCase):
    """双击课表某一门课要能改，而且改的是**那一门**。

    用户原话：「图中有有一个表格我输错了时间，想直接修改信息却出现了空白文本框」。
    两个叠在一起的坑，都在这里钉住：
    1. 显示顺序是按"星期+节次"重排过的，但编辑拿的是选中项的序号去 rows[] 里取
       → 双击一门课弹出的是另一门课，或者越界取空 → 文本框一片空白。
       （树里的 iid 现在存的是它在 timetable.json 里的原始下标。）
    2. rows 里的 weekday 是 "周二" 这种中文，编辑框预填时用 int() 转
       → ValueError，而异常抛在 grab_set() 之后 → 屏幕上留下一个空白、
       还抢着焦点的窗口，主界面点不动，看起来就是"卡住了"。
    """

    ROWS = [
        {"name": "C++程序设计", "weekday": "周一", "period": "3-4", "location": "图-203",
         "teacher": "李明", "weeks": "1-16"},
        {"name": "解析几何", "weekday": "周二", "period": "3-4", "location": "B-103",
         "teacher": "李文明", "weeks": "1-16"},
        {"name": "体育", "weekday": "3", "period": "5-6", "location": "体育馆",
         "teacher": "吴晓敏", "weeks": "1-16"},
        {"name": "高等代数1", "weekday": "周六", "period": "3-6", "location": "A-330",
         "teacher": "王芳", "weeks": "1-16"},
    ]

    def setUp(self):
        from agenda import control_window as control_window_mod
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow

        self.mod = control_window_mod
        self._real_warn = control_window_mod.messagebox.showwarning
        self.warnings: list[str] = []
        control_window_mod.messagebox.showwarning = lambda title, message, **kw: self.warnings.append(message)

        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "timetable.json").write_text(json.dumps({
            "termStart": "2026-09-05",
            "periods": [["08:00", "09:50"], ["10:10", "12:00"]],
            "courses": [dict(row) for row in self.ROWS],
        }, ensure_ascii=False), encoding="utf-8")

        config = ClientConfig.load(self.data)
        config.panel_visible = False
        controller = SimpleNamespace(
            data_dir=self.data,
            config=config,
            load_timetable_payload=lambda: json.loads(
                (self.data / "timetable.json").read_text(encoding="utf-8")),
            save_timetable_payload=self._save,
            restart_panel=lambda: None,
        )
        try:
            self.window = ControlWindow(controller)
        except Exception as error:
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.root = self.window.root
        self.root.geometry("+4000+4000")

    def tearDown(self):
        self.mod.messagebox.showwarning = self._real_warn
        try:
            self.window.stop_tick()
        except Exception:
            pass
        for widget in list(self.root.winfo_children()):
            if isinstance(widget, self.mod.tk.Toplevel):
                try:
                    widget.destroy()
                except Exception:
                    pass
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        self.tmp.cleanup()

    def _save(self, payload: dict) -> None:
        (self.data / "timetable.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _courses(self) -> list[dict]:
        return json.loads((self.data / "timetable.json").read_text(encoding="utf-8"))["courses"]

    def _edit_dialog(self, index: int):
        """开编辑窗，返回 (dialog, {字段名: Entry})。"""
        import tkinter as tk

        before = set(self.root.winfo_children())
        self.window.add_course_dialog(edit_index=index)
        self.root.update_idletasks()
        dialogs = [w for w in self.root.winfo_children() if w not in before and isinstance(w, tk.Toplevel)]
        self.assertEqual(len(dialogs), 1, "应该只弹出一个编辑窗")
        dialog = dialogs[0]
        entries: list[tk.Entry] = []
        for child in dialog.winfo_children():
            for grand in child.winfo_children():
                if isinstance(grand, tk.Entry):
                    entries.append(grand)
        return dialog, entries

    def _entries(self, dialog):
        import tkinter as tk

        found = []
        for child in dialog.winfo_children():
            for grand in child.winfo_children():
                if isinstance(grand, tk.Entry):
                    found.append(grand)
        return found

    def _click(self, dialog, text: str) -> bool:
        """先找齐按钮再点。

        踩过的坑：边遍历边点，"保存" 把整个窗口 destroy 掉之后，
        循环走到下一个按钮（取消）再 cget 就是 `invalid command name`。
        """
        import tkinter as tk

        buttons = [grand for child in dialog.winfo_children()
                   for grand in child.winfo_children() if isinstance(grand, tk.Button)]
        for button in buttons:
            if button.cget("text") == text:
                button.invoke()
                return True
        return False

    def test_tree_iid_is_the_index_in_the_file(self):
        """树里的 iid 必须是 timetable.json 里的下标，而不是显示序号。"""
        tree = self.window.course_tree
        self.window.refresh_courses()
        self.root.update_idletasks()
        iids = [int(iid) for iid in tree.get_children()]
        self.assertEqual(sorted(iids), list(range(len(self.ROWS))), "iid 应该是文件下标的一个排列")
        for iid in iids:
            shown = tree.set(str(iid), "name")
            self.assertEqual(shown, self.ROWS[iid]["name"],
                             f"iid={iid} 显示的是 {shown!r}，文件里是 {self.ROWS[iid]['name']!r}")

    def test_edit_dialog_is_prefilled_for_every_row(self):
        """每一门课双击出来都必须是它自己的数据，一个空框都不许有。"""
        for index, row in enumerate(self.ROWS):
            dialog, entries = self._edit_dialog(index)
            self.assertEqual(len(entries), 6, f"第 {index} 行应该 6 个输入框")
            values = [entry.get() for entry in entries]
            self.assertTrue(all(value.strip() for value in values),
                            f"第 {index} 行有空白框：{values}")
            self.assertEqual(values[0], row["name"], f"第 {index} 行课程名错位")
            # 星期按 1-7 预填（"周二" → 2，"3" → 3，周六 → 6）
            self.assertEqual(values[1], str(self.mod._weekday_index(row["weekday"]) + 1),
                             f"第 {index} 行星期预填不对")
            self.assertEqual(values[2], row["period"])
            dialog.destroy()
            self.root.update_idletasks()

    def test_editing_saves_the_same_row(self):
        """改完落盘的还是那一门课，星期写法保持 "周X"，不该变成会读错的数字。"""
        index = 1
        dialog, entries = self._edit_dialog(index)
        entries[2].delete(0, "end")           # 节次改成 7-8
        entries[2].insert(0, "7-8")
        self.assertTrue(self._click(dialog, "保存"), "没找到保存按钮")
        self.root.update_idletasks()

        courses = self._courses()
        self.assertEqual(len(courses), len(self.ROWS), "课程数不该变")
        self.assertEqual(courses[index]["name"], self.ROWS[index]["name"], "改错了行")
        self.assertEqual(courses[index]["period"], "7-8")
        self.assertEqual(courses[index]["weekday"], "周二", "星期写法被改坏了")
        self.assertEqual(courses[index]["teacher"], self.ROWS[index]["teacher"])
        self.assertFalse(dialog.winfo_exists(), "保存后编辑窗应该关掉")

    def test_saved_weekday_round_trips_through_timetable(self):
        """存回去的星期必须能被 timetable 解析成同一天。

        踩过的坑：编辑窗原来存 0 基下标，而 timetable.parse_course 把 1-7 当"周几"，
        "周二" 存成 1 会被读成周一 —— 改一次时间，课就悄悄挪了一天。
        """
        from agenda.timetable import parse_weekday

        for index, row in enumerate(self.ROWS):
            dialog, entries = self._edit_dialog(index)
            self.assertTrue(self._click(dialog, "保存"), f"第 {index} 行没找到保存按钮")
            self.root.update_idletasks()
            stored = self._courses()[index]["weekday"]
            self.assertEqual(parse_weekday(str(stored)), self.mod._weekday_index(row["weekday"]),
                             f"{row['name']} 改完星期从 {row['weekday']} 变成了 {stored}")


class ConsoleQuickEntryTests(unittest.TestCase):
    """托盘右键「快速录入群消息…」要真的把光标放到输入框里。

    只把窗口叫出来是不够的——用户点这个菜单项的意思是"我马上要粘东西"，
    所以还得切到「通知」页、并把焦点给输入框（否则粘不进任何地方）。
    """

    def setUp(self):
        from agenda import control_window as control_window_mod
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow

        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "timetable.json").write_text(
            json.dumps({"termStart": "2026-09-05", "periods": [["08:00", "09:50"]], "courses": []},
                       ensure_ascii=False), encoding="utf-8")
        config = ClientConfig.load(self.data)
        config.panel_visible = False
        controller = SimpleNamespace(
            data_dir=self.data, config=config,
            load_timetable_payload=lambda: json.loads(
                (self.data / "timetable.json").read_text(encoding="utf-8")),
            save_timetable_payload=lambda payload: None,
            restart_panel=lambda: None,
        )
        try:
            self.window = ControlWindow(controller)
        except Exception as error:
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
        self.mod = control_window_mod
        self.root = self.window.root
        self.root.geometry("+4000+4000")

    def tearDown(self):
        try:
            self.window.stop_tick()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_quick_entry_selects_notice_tab_and_focuses_the_box(self):
        # 不查 focus_get()：那取决于窗口有没有被系统聚焦，
        # 单测跑在离屏窗口上、旁边还有别的程序，会随机飘。
        focused: list[int] = []
        real_focus_set = self.window.notice_text.focus_set
        self.window.notice_text.focus_set = lambda: (focused.append(1), real_focus_set())[1]
        try:
            self.window.minimize_to_tray()
            self.window.quick_entry()
            self.root.update()
            self.assertEqual(str(self.window.notebook.index(self.window.notebook.select())), "0",
                             "应该切到 0 号页（通知）")
        finally:
            self.window.notice_text.focus_set = real_focus_set
        self.assertEqual(focused, [1], "光标没有落到通知输入框上，粘进去的东西会丢")


if __name__ == "__main__":
    unittest.main()
