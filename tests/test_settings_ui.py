"""界面精简后的回归：设置页只留必要的四项，日程表右下角多了个小齿轮。

用户的原话（第 18 轮反馈）：
  * 「删掉设置里"智能隐身"后面的"（原方案）"三字」
  * 「把图二（两个复选框）改成默认模式并删掉选项」
  * 「删掉图三（自动整合 inbox 间隔 + 面板图层）两行」
  * 「删掉面板底部那串操作提示，改成日程表右下角一个小齿轮作为设置呼出按钮」
  * 「删掉面板底部"课表：timetable.json"那行」

这些用例锁的就是"删干净了、默认值还照样对、齿轮真的能通到设置页"。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

#: 这几样是**故意**从界面上拿掉的：它们要么太技术，要么默认值就该固定。
#: 只要它们重新出现在任何控件的文字里，这些用例就红——因为那意味着
#: 用户刚要求删掉的东西又爬回来了。
REMOVED_FROM_UI = (
    "（原方案）",
    "自动整合 inbox 间隔",
    "自动整合间隔",
    "面板图层",
    "鼠标穿透",
    "桌面宠物",
)


def _walk(widget):
    """深度遍历一个 tkinter 控件树。"""
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _text_of(widget) -> str:
    for option in ("text", "label"):
        try:
            value = widget.cget(option)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value
    return ""


class SettingsPageTests(unittest.TestCase):
    """设置页：只留「启动时显示桌面面板」「到点弹窗提醒」+ 待机模式二选一。"""

    def setUp(self):
        from agenda import control_window as control_window_mod
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow

        self.mod = control_window_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "timetable.json").write_text(
            json.dumps({"termStart": "2026-09-05", "periods": [["08:00", "09:50"]], "courses": []},
                       ensure_ascii=False), encoding="utf-8")
        self.config = ClientConfig.load(self.data)
        self.config.panel_visible = False
        data_dir = self.data

        def _save_config() -> None:
            # 真 controller.save() 就是"把 client.json 落盘"；
            # 这里用个假的会写盘，才能验证"界面保存的值真的进了文件"。
            self.config.save(data_dir)

        controller = SimpleNamespace(
            data_dir=self.data, config=self.config,
            load_timetable_payload=lambda: json.loads(
                (self.data / "timetable.json").read_text(encoding="utf-8")),
            save_timetable_payload=lambda payload: None,
            restart_panel=lambda: None,
            panel_running=lambda: False,
            start_panel=lambda: None,
            save=_save_config,
            refresh_events=lambda: [],
            panel_pid=lambda: None,
        )
        try:
            self.window = ControlWindow(controller)
        except Exception as error:              # 无图形环境（CI）时跳过
            self.tmp.cleanup()
            raise unittest.SkipTest(f"没有可用显示：{error}")
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

    def _settings_frame(self):
        for tab in self.window.notebook.tabs():
            if self.window.notebook.tab(tab, "text") == "设置":
                return self.root.nametowidget(tab)
        self.fail("找不到「设置」页")

    def test_removed_options_are_gone_from_the_page(self):
        """拿掉的选项不许在界面上留任何残留（提示文字也算）。"""
        frame = self._settings_frame()
        texts = [text for text in (_text_of(w) for w in _walk(frame)) if text]
        joined = " ｜ ".join(texts)
        for removed in REMOVED_FROM_UI:
            self.assertNotIn(removed, joined, f"「{removed}」又被放回设置页了")
        # 「智能隐身」必须还在（它是待机模式的名字），但后面不许跟"（原方案）"
        self.assertTrue(any("智能隐身" in text for text in texts), "待机模式不见了")

    def test_only_two_checkboxes_and_two_radios_remain(self):
        """复选框只剩 2 个（启动显示 / 到点弹窗），单选只剩待机模式那 2 个。"""
        import tkinter as tk

        frame = self._settings_frame()
        checks = [w for w in _walk(frame) if isinstance(w, tk.Checkbutton)]
        radios = [w for w in _walk(frame) if isinstance(w, tk.Radiobutton)]
        self.assertEqual(len(checks), 2,
                         f"复选框应该只剩 2 个，实际 {[w.cget('text') for w in checks]}")
        self.assertEqual(len(radios), 2,
                         f"待机模式应该正好 2 个选项，实际 {[w.cget('text') for w in radios]}")
        self.assertEqual([w.cget("text") for w in radios], ["智能隐身", "常驻待机"])

    def test_power_settings_keep_their_config_values(self):
        """保存设置不许把界面上已经删掉的字段写坏。

        这几个字段（自动整合间隔 / 面板图层 / 鼠标穿透 / 宠物模式）高手是直接
        编辑 `data/client.json` 改的，界面保存一次不能把它们推回默认值。
        """
        from agenda.client_config import ClientConfig

        config = self.config
        config.window_mode = "topmost"
        config.click_through = True
        config.pet_mode = False
        config.pipeline_minutes = 42.0
        config.save(self.data)

        original = self.mod.messagebox.askyesno
        self.mod.messagebox.askyesno = lambda *a, **kw: False
        try:
            self.window.save_settings()
        finally:
            self.mod.messagebox.askyesno = original

        self.window.controller.config.save(self.data)
        reloaded = ClientConfig.load(self.data)
        self.assertEqual(reloaded.window_mode, "topmost", "面板图层被界面写回默认值了")
        self.assertTrue(reloaded.click_through, "鼠标穿透被界面写回默认值了")
        self.assertFalse(reloaded.pet_mode, "桌面宠物模式被界面写回默认值了")
        self.assertEqual(reloaded.pipeline_minutes, 42.0, "自动整合间隔被界面写回默认值了")

    def test_save_settings_writes_the_three_visible_fields(self):
        """界面上留下的是「启动时显示面板」「到点弹窗」「待机模式」，保存必须落盘。"""
        from agenda.client_config import ClientConfig

        self.window.panel_visible_var.set(True)
        self.window.popup_var.set(True)
        self.window.desktop_only_var.set(False)          # → 常驻待机

        original = self.mod.messagebox.askyesno
        self.mod.messagebox.askyesno = lambda *a, **kw: False
        try:
            self.window.save_settings()
        finally:
            self.mod.messagebox.askyesno = original

        reloaded = ClientConfig.load(self.data)
        self.assertTrue(reloaded.panel_visible)
        self.assertTrue(reloaded.popup_reminders)
        self.assertFalse(reloaded.desktop_only, "待机模式没写进去")
        self.assertEqual(reloaded.close_action, "tray",
                         "关闭窗口的行为必须是「最小化至托盘」")


class SettingsRequestTests(unittest.TestCase):
    """面板齿轮 → 客户端切到「设置」页：请求文件的产生与消费。"""

    def test_request_settings_needs_a_live_client(self):
        from agenda.client_app import SETTINGS_REQUEST, AppController

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            controller = AppController(data)
            self.assertFalse(controller.request_settings(), "没有客户端时不该假装成功")
            self.assertFalse((data / SETTINGS_REQUEST).exists())

            (data / "client.lock").write_text(str(os.getpid()), encoding="utf-8")
            self.assertTrue(controller.request_settings())
            self.assertTrue((data / SETTINGS_REQUEST).exists())
            self.assertEqual((data / SETTINGS_REQUEST).read_text(encoding="utf-8"),
                             str(os.getpid()))

    def test_console_consumes_the_flag_and_opens_the_settings_tab(self):
        """`client.settings` 一到，控制台必须显示出来并切到「设置」页（而不是别的页）。"""
        from agenda import control_window as control_window_mod
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow
        from agenda.client_app import SETTINGS_REQUEST

        tmp = tempfile.TemporaryDirectory()
        try:
            data = Path(tmp.name)
            (data / "timetable.json").write_text(
                json.dumps({"termStart": "2026-09-05", "periods": [["08:00", "09:50"]],
                            "courses": []}, ensure_ascii=False), encoding="utf-8")
            config = ClientConfig.load(data)
            config.panel_visible = False
            controller = SimpleNamespace(
                data_dir=data, config=config,
                load_timetable_payload=lambda: json.loads(
                    (data / "timetable.json").read_text(encoding="utf-8")),
                save_timetable_payload=lambda payload: None,
                restart_panel=lambda: None,
            )
            try:
                window = ControlWindow(controller)
            except Exception as error:
                raise unittest.SkipTest(f"没有可用显示：{error}")
            root = window.root
            root.geometry("+4000+4000")
            try:
                window.minimize_to_tray()                  # 控制台藏进托盘
                root.update()
                (data / SETTINGS_REQUEST).write_text("panel", encoding="utf-8")
                window._check_show_request()
                root.update()
                self.assertEqual(root.state(), "normal", "控制台没有从托盘里回来")
                current = window.notebook.tab(window.notebook.select(), "text")
                self.assertEqual(current, "设置", f"切到了「{current}」页，不是设置页")
                self.assertFalse((data / SETTINGS_REQUEST).exists(), "请求文件没被消费掉")
            finally:
                try:
                    window.stop_tick()
                except Exception:
                    pass
                try:
                    root.destroy()
                except Exception:
                    pass
        finally:
            tmp.cleanup()

    def test_quit_request_actually_quits_the_whole_app(self):
        """`client.quit` 必须是"连托盘一起退"，不能退化成"最小化至托盘"。

        真踩过：`_check_show_request` 里这条分支原来调的是 `on_close()`，
        而 `on_close()` 后来被改成了"只把控制台藏进托盘"。于是用户在面板齿轮里点
        「退出应用（面板与托盘一并退出）」的结果是：面板没了、托盘图标还在、
        进程还活着 —— 和菜单上写的话完全相反。
        """
        from agenda import control_window as control_window_mod
        from agenda.client_config import ClientConfig
        from agenda.control_window import ControlWindow
        from agenda.client_app import QUIT_REQUEST

        tmp = tempfile.TemporaryDirectory()
        try:
            data = Path(tmp.name)
            (data / "timetable.json").write_text(
                json.dumps({"termStart": "2026-09-05", "periods": [["08:00", "09:50"]],
                            "courses": []}, ensure_ascii=False), encoding="utf-8")
            config = ClientConfig.load(data)
            config.panel_visible = False
            stopped: list[int] = []
            controller = SimpleNamespace(
                data_dir=data, config=config,
                load_timetable_payload=lambda: json.loads(
                    (data / "timetable.json").read_text(encoding="utf-8")),
                save_timetable_payload=lambda payload: None,
                restart_panel=lambda: None,
                stop_panel=lambda: stopped.append(1),
            )
            try:
                window = ControlWindow(controller)
            except Exception as error:
                raise unittest.SkipTest(f"没有可用显示：{error}")
            root = window.root
            root.geometry("+4000+4000")
            try:
                (data / QUIT_REQUEST).write_text("panel", encoding="utf-8")
                window._check_show_request()
                root.update()
                self.assertFalse((data / QUIT_REQUEST).exists(), "请求文件没被消费掉")
                self.assertEqual(stopped, [1], "没有把面板一起停掉")
                # 主窗必须真的销毁（destroy 之后 winfo_exists 会抛 TclError）
                with self.assertRaises(Exception):
                    root.winfo_exists()
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        finally:
            tmp.cleanup()

    def test_request_poll_runs_faster_than_the_ui_refresh(self):
        """请求轮询必须明显快于 5 秒的界面刷新。

        原来是搭在 5 秒的 tick 上的：点一下齿轮最长等 5 秒窗口才动，
        用户只会以为按钮坏了。
        """
        from agenda.control_window import REQUEST_POLL_MS

        self.assertLessEqual(REQUEST_POLL_MS, 500, "请求轮询太慢，点齿轮会像没反应")


class FooterGearTests(unittest.TestCase):
    """日程表右下角的小齿轮：面板上唯一的"设置"入口。"""

    def _panel(self, tmp):
        from agenda.panel import AgendaPanel

        return AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                           window_mode="desktop")

    def test_gear_exists_in_the_footer_corner(self):
        import tkinter as tk

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            panel.root.update()
            gear = getattr(panel, "gear", None)
            self.assertIsNotNone(gear, "右下角没有齿轮按钮")
            self.assertIsInstance(gear, tk.Canvas)
            # 它得跟状态行在同一个"底部行"里，并且靠右
            self.assertIs(gear.master, panel.hint_label.master)
            self.assertEqual(gear.pack_info().get("side"), "right", "齿轮没有靠右下角")
            # 画上去的齿轮得有图元（空画布 = 用户什么也看不到）
            self.assertGreater(len(gear.find_all()), 0, "齿轮是空画布")
            panel.root.destroy()

    def test_gear_click_opens_the_settings_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            panel.root.update()
            self.assertIn("_open_gear_menu", panel.gear.bind("<Button-1>"),
                          "齿轮左键没接上菜单")

            labels: list[str] = []
            real_popup = None

            class SpyMenu:
                def __init__(self, *a, **kw):
                    self._items = []

                def add_command(self, label=None, command=None, **kw):
                    self._items.append(("command", label))

                def add_separator(self, **kw):
                    self._items.append(("separator", None))

                def tk_popup(self, x, y):
                    labels.extend(label for kind, label in self._items if kind == "command")

                def grab_release(self):
                    pass

            import tkinter as tk

            real_menu = tk.Menu
            tk.Menu = SpyMenu
            try:
                panel._open_gear_menu(None)
            finally:
                tk.Menu = real_menu
            self.assertIsNone(real_popup)
            self.assertTrue(labels, "齿轮菜单是空的")
            self.assertEqual(labels[0], "日程表设置…", "菜单第一项应该是「日程表设置…」")
            for wanted in ("打开客户端窗口", "快速录入群消息…", "立即整合群通知", "收起面板"):
                self.assertIn(wanted, labels, f"齿轮菜单里缺「{wanted}」")
            panel.root.destroy()

    def test_open_settings_requests_the_console_settings_page(self):
        """齿轮第一项：请客户端切到设置页；没有客户端时退而求其次直接起一个。"""
        import agenda.client_app as client_app

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            panel.root.update()
            calls: list[str] = []
            toasts: list[str] = []

            class FakeController:
                def __init__(self, data_dir):
                    self.data_dir = data_dir

                def request_settings(self):
                    calls.append("request_settings")
                    return True

                def start_console(self):
                    calls.append("start_console")
                    return True

            real = client_app.AppController
            client_app.AppController = FakeController
            panel._toast = lambda message, **kw: toasts.append(message)
            try:
                panel.open_settings()
            finally:
                client_app.AppController = real
            self.assertEqual(calls, ["request_settings"],
                             "有客户端时不该另起一个进程")
            self.assertTrue(toasts, "点了齿轮却没有任何反馈，用户会以为按钮坏了")
            panel.root.destroy()

    def test_open_settings_starts_the_console_when_none_is_running(self):
        import agenda.client_app as client_app

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            panel.root.update()
            calls: list[str] = []

            class FakeController:
                def __init__(self, data_dir):
                    self.data_dir = data_dir

                def request_settings(self):
                    calls.append("request_settings")
                    return False

                def start_console(self):
                    calls.append("start_console")
                    return True

            real = client_app.AppController
            client_app.AppController = FakeController
            panel._toast = lambda message, **kw: None
            try:
                panel.open_settings()
            finally:
                client_app.AppController = real
            self.assertEqual(calls, ["request_settings", "start_console", "request_settings"],
                             "客户端没在跑的时候应该把它起起来，并让它切到设置页")
            panel.root.destroy()

    def test_open_settings_never_raises(self):
        """唤起客户端失败不能把面板带崩——那是最糟的结局（面板没了，设置也进不去）。"""
        import agenda.client_app as client_app

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            panel.root.update()
            toasts: list[str] = []

            class BoomController:
                def __init__(self, data_dir):
                    raise OSError("模拟：客户端目录不可写")

            real = client_app.AppController
            client_app.AppController = BoomController
            panel._toast = lambda message, **kw: toasts.append(message)
            try:
                panel.open_settings()          # 不许抛
            finally:
                client_app.AppController = real
            self.assertTrue(toasts, "出错了却一声不吭")
            panel.root.destroy()

    def test_footer_hint_is_empty_by_default(self):
        """底部那串操作提示按用户要求删掉了：平时这一行必须是空的。"""
        from agenda.panel import FOOTER_HINT

        self.assertEqual(FOOTER_HINT, "", "底部提示文字又回来了")

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            panel.root.update()
            self.assertEqual(panel.hint_label.cget("text"), "",
                             "面板底部还挂着一行提示文字")
            panel.root.destroy()

    def test_status_line_no_longer_shows_the_timetable_path(self):
        """「课表：timetable.json」这行技术细节不许再出现在面板上。"""
        from agenda.timeline import build_timeline
        from agenda.timetable import load_timetable

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "timetable.json").write_text(
                json.dumps({"termStart": "2026-09-05",
                            "periods": [["08:00", "09:50"]],
                            "courses": [{"name": "高等数学", "weekday": "周一",
                                         "period": "1-2", "weeks": "1-16"}]},
                           ensure_ascii=False), encoding="utf-8")
            panel = self._panel(data)
            panel.root.update()
            table = load_timetable(data)
            self.assertTrue(table.source, "课表来源应该有值，这个用例才有意义")
            panel.timeline = build_timeline([], table)
            panel._render_status()
            panel.root.update()
            shown = panel.status_label.cget("text")
            self.assertNotIn("课表：", shown, f"底部还在显示课表路径：{shown!r}")
            self.assertNotIn("timetable.json", shown, f"底部还在显示课表文件名：{shown!r}")
            panel.root.destroy()


if __name__ == "__main__":
    unittest.main()
