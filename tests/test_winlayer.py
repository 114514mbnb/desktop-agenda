"""桌面图层行为的测试。

Win32 的 z-order 不好在单测里断言，能钉住的是：
  * 探测函数在真实环境下不抛异常、返回值类型正确
  * 面板"桌面模式"下确实被加上了鼠标穿透/不抢焦点的扩展样式
    （真机实测：扩展样式里出现 WS_EX_TRANSPARENT | WS_EX_NOACTIVATE）
"""

from __future__ import annotations

import ctypes
import sys
import time
import unittest
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import winlayer  # noqa: E402


class WinLayerHelpersTests(unittest.TestCase):
    def test_foreground_probes_are_safe(self):
        # 不假定有前台窗口/桌面，只要求不炸且类型正确
        self.assertIsInstance(winlayer.foreground_class(), str)
        self.assertIsInstance(winlayer.foreground_title(), str)
        self.assertIsInstance(winlayer.desktop_is_foreground(), bool)
        self.assertIsInstance(winlayer.fullscreen_foreground(), bool)

    def test_desktop_classes_cover_common_shell_windows(self):
        for name in ("Progman", "WorkerW", "Shell_TrayWnd"):
            self.assertIn(name, winlayer.DESKTOP_CLASSES)

    def test_send_to_bottom_with_zero_handle_is_false(self):
        self.assertFalse(winlayer.send_to_bottom(0))
        self.assertFalse(winlayer.apply_click_through(0))
        self.assertFalse(winlayer.set_topmost(0, True))


@unittest.skipUnless(sys.platform == "win32", "仅 Windows 需要验证窗口样式")
class PanelDesktopModeStyleTests(unittest.TestCase):
    """起一个真实面板，检查桌面模式下扩展样式位。

    这是"打游戏不被挡"的核心保证：WS_EX_TRANSPARENT 让它不吃鼠标事件，
    WS_EX_NOACTIVATE 让它不抢焦点。样式拿不到就说明点击穿透没生效。
    """

    def test_panel_window_is_click_through_in_desktop_mode(self):
        import tempfile
        import tkinter as tk

        from agenda.panel import AgendaPanel

        with tempfile.TemporaryDirectory() as tmp:
            panel = AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                                window_mode="desktop", click_through=True, desktop_only=True)
            panel.root.update()
            panel._init_desktop_layer()
            panel.root.update()
            hwnd = panel._hwnd
            self.assertTrue(hwnd, "拿不到窗口句柄，桌面图层初始化失败")
            user32 = ctypes.windll.user32
            getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            style = getter(wintypes.HWND(hwnd), winlayer.GWL_EXSTYLE)
            panel.root.destroy()

        self.assertTrue(style & winlayer.WS_EX_TRANSPARENT, "缺少鼠标穿透样式")
        self.assertTrue(style & winlayer.WS_EX_NOACTIVATE, "缺少不抢焦点样式")

    def test_panel_topmost_mode_has_no_click_through(self):
        import tempfile
        from pathlib import Path as _Path

        from agenda.panel import AgendaPanel

        with tempfile.TemporaryDirectory() as tmp:
            panel = AgendaPanel(_Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                                topmost=True, window_mode="topmost")
            panel.root.update()
            topmost = bool(panel.root.attributes("-topmost"))
            panel.root.destroy()
        self.assertTrue(topmost, "topmost 模式下窗口应保持置顶")

    def test_scrollbar_is_actually_visible(self):
        """滚轮条得有真实宽度。

        踩过的坑：先 pack 画布再 pack 滚动条，画布的 expand=True 会先把整行宽度吃掉，
        滚动条只剩 1px——截图里右边那条细线一直没出现，用户还以为没有滚动条。
        """
        import tempfile

        from agenda.panel import AgendaPanel

        with tempfile.TemporaryDirectory() as tmp:
            panel = AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                                window_mode="desktop")
            panel.root.update()
            panel.root.update_idletasks()
            mapped = bool(panel.scrollbar.winfo_ismapped())
            width = panel.scrollbar.winfo_width()
            canvas_width = panel.canvas.winfo_width()
            panel_width = panel.root.winfo_width()
            panel.root.destroy()
        self.assertTrue(mapped, "滚动条没有被映射出来")
        self.assertGreaterEqual(width, 6, f"滚动条只有 {width}px，等于看不见")
        self.assertLess(canvas_width, panel_width, "画布占满了整宽，滚动条没有位置")


class PanelRestoreLatencyTests(unittest.TestCase):
    """"离开屏幕→回到屏幕"必须快到看不出停顿。

    一开始靠 `after(2500, ...)` 轮询，最坏要等满一个周期才恢复，
    用户的原话是"回到屏幕时日程表打开的时间太长，甚至让人觉的是应用自己出现了 BUG"。
    现在改成 100 ms 一次的廉价轮询（探测只要 0.01 ms/次），恢复走"挪窗口"。
    试过 `SetWinEventHook` 想做到事件驱动，但 ctypes 回调没有 GIL，
    一进回调进程就崩——详见 `agenda/dropzone.py` 里记的结论。
    """

    def test_restore_happens_within_one_poll_cycle(self):
        import tempfile

        from agenda import winlayer
        from agenda.panel import FOREGROUND_POLL_MS, AgendaPanel

        real_fs, real_desktop = winlayer.fullscreen_foreground, winlayer.desktop_is_foreground
        with tempfile.TemporaryDirectory() as tmp:
            panel = AgendaPanel(Path(tmp), refresh_ms=60_000, pipeline_ms=0,
                                autostart_pipeline=False, window_mode="topmost")
            try:
                panel.root.update()
                self.assertLessEqual(FOREGROUND_POLL_MS, 150,
                                     "前台快查间隔太大，回到屏幕会有肉眼可见的停顿")
                # 全屏应用出现 → 面板离开屏幕
                winlayer.fullscreen_foreground = lambda: True
                panel._apply_foreground_change()
                self.assertTrue(panel._hidden_offscreen, "全屏时面板应当离开屏幕")
                # 切回桌面 → 面板要马上回来
                winlayer.fullscreen_foreground = lambda: False
                winlayer.desktop_is_foreground = lambda: True
                started = time.perf_counter()
                panel._apply_foreground_change()
                elapsed = (time.perf_counter() - started) * 1000
                self.assertFalse(panel._hidden_offscreen, "回到桌面后面板没有恢复")
                self.assertLess(elapsed, 60, f"恢复花了 {elapsed:.0f} ms，太慢")
            finally:
                winlayer.fullscreen_foreground, winlayer.desktop_is_foreground = real_fs, real_desktop
                try:
                    panel.stop_tick()
                except Exception:
                    pass
                panel.root.destroy()

    def test_pet_mode_does_not_block_gaming(self):
        """宠物模式也必须让全屏应用把面板顶掉，不能因为"浮到最上层"就挡住游戏。"""
        from agenda import winlayer
        from agenda.panel import AgendaPanel

        real = (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground)
        winlayer.desktop_is_foreground = lambda: False
        winlayer.fullscreen_foreground = lambda: True
        hidden = None
        try:
            panel = AgendaPanel.__new__(AgendaPanel)
            panel._closing = False
            panel.desktop_only = True
            panel.pet_mode = True
            panel._hidden_by_watcher = False
            panel._user_hidden = False
            panel._hidden_offscreen = False
            panel._hwnd = 0
            panel.hide_instant = lambda: setattr(panel, "_hidden_offscreen", True)
            panel._apply_foreground_change()
            hidden = panel._hidden_offscreen
        finally:
            (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground) = real
        self.assertTrue(hidden, "宠物模式下全屏应用在前台时也必须隐身")


class PetLayerTests(unittest.TestCase):
    """桌面宠物模式：显示桌面时面板要"浮上来"，而不是被压没了。

    踩过的坑：`_desktop_watch` 原来只会 `send_to_bottom`——面板本来就在最底层，
    等于什么都没做，用户按 Win+D 之后反而看不见面板。
    """

    def test_pet_mode_raises_when_desktop_is_foreground(self):
        from agenda import winlayer

        calls: list[tuple] = []
        real = (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground,
                winlayer.raise_to_top_of_normal, winlayer.send_to_bottom)
        winlayer.desktop_is_foreground = lambda: True
        winlayer.fullscreen_foreground = lambda: False
        winlayer.raise_to_top_of_normal = lambda hwnd: calls.append(("raise", hwnd)) or True
        winlayer.send_to_bottom = lambda hwnd: calls.append(("bottom", hwnd)) or True
        try:
            panel = self._fake_panel(pet_mode=True)
            panel._desktop_watch()
        finally:
            (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground,
             winlayer.raise_to_top_of_normal, winlayer.send_to_bottom) = real
        self.assertEqual(calls, [("raise", 4242)])

    def test_without_pet_mode_keeps_old_bottom_behaviour(self):
        from agenda import winlayer

        calls: list[tuple] = []
        real = (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground,
                winlayer.raise_to_top_of_normal, winlayer.send_to_bottom,
                winlayer.set_topmost)
        winlayer.desktop_is_foreground = lambda: True
        winlayer.fullscreen_foreground = lambda: False
        winlayer.raise_to_top_of_normal = lambda hwnd: calls.append(("raise", hwnd)) or True
        winlayer.send_to_bottom = lambda hwnd: calls.append(("bottom", hwnd)) or True
        winlayer.set_topmost = lambda hwnd, flag: calls.append(("topmost", flag)) or True
        try:
            panel = self._fake_panel(pet_mode=False)
            panel._desktop_watch()
        finally:
            (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground,
             winlayer.raise_to_top_of_normal, winlayer.send_to_bottom,
             winlayer.set_topmost) = real
        self.assertIn(("bottom", 4242), calls)
        self.assertNotIn(("raise", 4242), calls)

    def test_fullscreen_withdraws_even_in_pet_mode(self):
        """宠物模式也不能挡游戏：全屏时照样隐身。"""
        from agenda import winlayer

        real = (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground)
        winlayer.desktop_is_foreground = lambda: False
        winlayer.fullscreen_foreground = lambda: True
        try:
            panel = self._fake_panel(pet_mode=True)
            panel.desktop_only = True
            panel._desktop_watch()
            hidden = panel.hidden
        finally:
            (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground) = real
        self.assertTrue(hidden, "全屏应用在前台时面板必须隐身")

    def test_minimized_panel_is_restored(self):
        """任务栏"显示桌面"最小化过面板 → 监控要把它还原回来。

        单纯 raise/send_to_bottom 对最小化的窗口无效，必须走 restore_window。
        """
        from agenda import winlayer

        calls: list[str] = []
        real = (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground,
                winlayer.is_minimized, winlayer.restore_window,
                winlayer.raise_to_top_of_normal)
        winlayer.desktop_is_foreground = lambda: True
        winlayer.fullscreen_foreground = lambda: False
        winlayer.is_minimized = lambda hwnd: True
        winlayer.restore_window = lambda hwnd, **kw: calls.append("restore") or True
        winlayer.raise_to_top_of_normal = lambda hwnd: calls.append("raise") or True
        try:
            panel = self._fake_panel(pet_mode=True)
            panel._desktop_watch()
        finally:
            (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground,
             winlayer.is_minimized, winlayer.restore_window,
             winlayer.raise_to_top_of_normal) = real
        self.assertIn("restore", calls)
        self.assertIn("raise", calls)

    def test_user_hidden_panel_is_not_pulled_back(self):
        """用户用右键菜单收起的面板，监控不能又把它拉回来。"""
        from agenda import winlayer

        calls: list[str] = []
        real = (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground,
                winlayer.is_minimized, winlayer.raise_to_top_of_normal)
        winlayer.desktop_is_foreground = lambda: True
        winlayer.fullscreen_foreground = lambda: False
        winlayer.is_minimized = lambda hwnd: False
        winlayer.raise_to_top_of_normal = lambda hwnd: calls.append("raise") or True
        try:
            panel = self._fake_panel(pet_mode=True)
            panel._user_hidden = True
            panel._desktop_watch()
        finally:
            (winlayer.desktop_is_foreground, winlayer.fullscreen_foreground,
             winlayer.is_minimized, winlayer.raise_to_top_of_normal) = real
        self.assertEqual(calls, [], "用户主动收起后不该被监控拉回来")

    def test_minimize_is_blocked_at_the_message_level(self):
        """起一个真面板，验证最小化被挡掉（这是"按显示桌面就消失"的根因修复）。"""
        import tempfile

        from agenda.panel import AgendaPanel

        with tempfile.TemporaryDirectory() as tmp:
            panel = AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                                window_mode="desktop", desktop_only=True)
            panel.root.update()
            panel._init_desktop_layer()
            panel.root.update()
            hwnd = panel._hwnd
            self.assertTrue(hwnd, "拿不到窗口句柄")
            self.assertTrue(winlayer.minimize_blocked(hwnd), "最小化没有被挡住")
            # 真的发一条最小化命令过去，窗口应当仍然不是最小化状态
            user32 = ctypes.windll.user32
            user32.SendMessageW(wintypes.HWND(hwnd), 0x0112, 0xF020, 0)   # WM_SYSCOMMAND/SC_MINIMIZE
            panel.root.update()
            minimized = winlayer.is_minimized(hwnd)
            panel.root.destroy()
        self.assertFalse(minimized, "发了 SC_MINIMIZE 之后窗口不该处于最小化状态")

    def _fake_panel(self, *, pet_mode: bool):
        """绕过 Tk：只保留 _desktop_watch 用到的那点状态。"""
        from agenda.panel import AgendaPanel

        class FakeRoot:
            def withdraw(self):
                panel.hidden = True

            def deiconify(self):
                panel.hidden = False

            def geometry(self, _spec):
                pass

            def after(self, _ms, _cb):
                return None

        panel = AgendaPanel.__new__(AgendaPanel)
        panel.root = FakeRoot()
        panel._hwnd = 4242
        panel._closing = False
        panel._hidden_by_watcher = False
        panel._user_hidden = False
        panel._hidden_offscreen = False
        panel.hidden = False
        panel.desktop_only = True
        panel.pet_mode = pet_mode
        # 隐藏/恢复现在走"挪窗口"而不是 withdraw/deiconify
        panel.hide_instant = lambda: setattr(panel, "hidden", True)
        panel.show_now = lambda: setattr(panel, "hidden", False)
        return panel


class FooterContextMenuTests(unittest.TestCase):
    """面板底部状态栏右键菜单。

    用户原话：「当我点击右下角的状态栏想要右键关闭时却是空白。我要求你修改。
    右键状态栏标识可以退出应用，呼出客户端窗口，呼出快速录入群消息」。
    底部那条（状态文字 + 操作提示）原来没绑任何菜单，右键只有一片空白。
    """

    WANTED = ("打开客户端窗口", "快速录入群消息…", "退出应用（面板与托盘一并退出）")

    def _panel(self, tmp):
        from agenda.panel import AgendaPanel

        return AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                           window_mode="desktop")

    def test_footer_is_bound_and_menu_is_not_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            panel.root.update()
            widgets = {"footer": panel.status_label.master,
                       "status": panel.status_label,
                       "hint": panel.hint_label}
            for name, widget in widgets.items():
                binding = widget.bind("<Button-3>")
                self.assertTrue(binding, f"{name} 没有绑右键，右键就是一片空白")
            menu = panel.footer_menu
            self.assertIsNotNone(menu, "底部菜单根本没建")
            self.assertGreater(menu.index("end"), 0, "底部菜单是空的")
            labels = []
            for index in range(menu.index("end") + 1):
                if menu.type(index) == "separator":
                    labels.append("---")
                else:
                    labels.append(menu.entrycget(index, "label"))
            for wanted in self.WANTED:
                self.assertIn(wanted, labels, f"底部菜单里缺「{wanted}」")
            panel.root.destroy()

    def test_footer_binding_calls_the_footer_menu(self):
        """底部绑的必须是自己那套菜单，别落到卡片菜单上（卡片菜单对自己没选中的条目是空的）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            panel.root.update()
            for widget in (panel.status_label, panel.hint_label):
                script = widget.bind("<Button-3>")
                self.assertIn("_popup_footer_menu", script,
                              "底部右键绑到了别的处理函数上")
            self.assertIsNot(panel.footer_menu, getattr(panel, "menu", None),
                             "底部菜单不该复用卡片菜单")
            panel.root.destroy()

    def test_quit_app_also_asks_the_client_to_quit(self):
        """「退出应用」要连托盘一起退。

        面板和客户端是两个进程，只 destroy 面板的话托盘图标还留着，
        用户会以为没退干净。
        """
        import os
        import tempfile

        from agenda.client_app import QUIT_REQUEST

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            panel = self._panel(data)
            panel.root.update()
            panel.data_dir = data
            # 假装客户端正在跑：client.lock 里放一个活着的 pid（就是本进程）
            (data / "client.lock").write_text(str(os.getpid()), encoding="utf-8")
            panel.quit_app()
            self.assertTrue((data / QUIT_REQUEST).exists(), "没有给客户端留退出请求")
            self.assertEqual((data / QUIT_REQUEST).read_text(encoding="utf-8"), "panel")

    def test_quit_app_survives_a_missing_client(self):
        """没装客户端 / 客户端没在跑的时候点退出，也不能抛异常，面板还是要退掉。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            panel = self._panel(data)
            panel.root.update()
            panel.data_dir = data
            quit_called = []
            panel.quit = lambda: quit_called.append(True)
            panel.quit_app()
            self.assertEqual(quit_called, [True], "面板没有退出")
            panel.root.destroy()


class PanelWakeHookTests(unittest.TestCase):
    """解锁 / 显示器重新点亮 → 立刻恢复，不用等轮询。

    用户反馈「切回主屏幕时日程表弹出的速度还是太慢」：那条路原来是纯轮询，
    最快也要等一个周期（100 ms 实测平均 102 ms）。现在：
      * 轮询降到 40 ms（实测平均 40 ms）；
      * 另外在窗口上挂一个子类钩子，收到系统主动发来的 WM_POWERBROADCAST /
        WM_DISPLAYCHANGE 就**立刻**恢复，不再等轮询。
    """

    def _panel(self, tmp):
        from agenda.panel import AgendaPanel

        return AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                           window_mode="desktop", position=(120, 120))

    def test_poll_interval_is_snappy(self):
        from agenda.panel import FOREGROUND_POLL_MS

        self.assertLessEqual(FOREGROUND_POLL_MS, 50,
                             "轮询间隔是「回到屏幕」延迟的下界，必须够小")

    def test_wake_hook_is_installed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            try:
                panel.root.update()
                panel._init_desktop_layer()
                panel.root.update()
                hwnd = panel._hwnd
                self.assertTrue(hwnd, "拿不到窗口句柄")
                self.assertTrue(winlayer.wake_hooked(hwnd), "没挂上唤醒钩子")
            finally:
                panel.quit()

    def test_power_message_restores_the_panel_immediately(self):
        """往窗口发一条「显示器状态变了」，面板要马上回到屏幕上。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            try:
                panel.root.update()
                panel._init_desktop_layer()
                panel.root.update()
                hwnd = panel._hwnd

                calls: list[str] = []
                real_wake = panel._on_system_wake

                def spy(reason: str = "") -> None:
                    calls.append(reason)
                    real_wake(reason)

                panel._on_system_wake = spy
                winlayer.unhook_wake_events(hwnd)
                self.assertTrue(winlayer.hook_wake_events(hwnd, spy))

                # 模拟"全屏应用在前台"→ 面板被藏到屏幕外
                panel.hide_instant()
                panel._hidden_by_watcher = True
                panel.root.update()
                self.assertTrue(panel._hidden_offscreen)

                ctypes.windll.user32.SendMessageW(
                    wintypes.HWND(hwnd), winlayer.WM_POWERBROADCAST,
                    winlayer.PBT_POWERSETTINGCHANGE, 0)
                panel.root.update()
                self.assertEqual(calls, ["power"], "系统消息没有触发唤醒回调")
                self.assertFalse(panel._hidden_offscreen, "面板没有立刻回到屏幕上")
            finally:
                try:
                    winlayer.unhook_wake_events(panel._hwnd)
                except Exception:
                    pass
                panel.quit()

    def test_display_change_also_wakes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            try:
                panel.root.update()
                panel._init_desktop_layer()
                panel.root.update()
                hwnd = panel._hwnd
                calls: list[str] = []
                winlayer.unhook_wake_events(hwnd)
                winlayer.hook_wake_events(hwnd, lambda reason="": calls.append(reason))
                ctypes.windll.user32.SendMessageW(wintypes.HWND(hwnd),
                                                  winlayer.WM_DISPLAYCHANGE, 0, 0)
                self.assertEqual(calls, ["display"])
            finally:
                try:
                    winlayer.unhook_wake_events(panel._hwnd)
                except Exception:
                    pass
                panel.quit()


class PanelEscapeKeyTests(unittest.TestCase):
    """ESC 只能"收起"面板，不能把面板进程干掉。

    原来绑的是 quit()：面板一旦被聚焦（click_through 关着的时候就能点中它），
    随手一个 ESC 就把面板进程收拾了，而没有任何东西会把它拉回来 ——
    用户看到的就是"桌面日程自己没了"（真发生过）。
    """

    def test_escape_hides_instead_of_quitting(self):
        import tempfile

        from agenda.panel import AgendaPanel

        with tempfile.TemporaryDirectory() as tmp:
            panel = AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                                window_mode="desktop")
            panel.root.update()
            # 记下"收起来"这一步有没有被走到。不直接读 _hidden_offscreen：
            # 后台的显示桌面监控可能正好抢在前面把窗口放回屏幕，那样断言会随机飘。
            calls: list[str] = []
            real_hide = panel.hide_instant
            panel.hide_instant = lambda: (calls.append("hide"), real_hide())[1]
            # 键盘事件是发给"焦点窗口"的，不 focus 的话在整套测试里跑就收不到
            panel.root.focus_force()
            panel.root.update()
            panel.root.event_generate("<Escape>", when="now")
            panel.root.update()
            user_hidden = panel._user_hidden
            alive = bool(panel.root.winfo_exists())
            panel.hide_instant = real_hide
            panel.root.destroy()
        self.assertTrue(alive, "ESC 把面板窗口关掉了")
        self.assertEqual(calls, ["hide"], "ESC 应该收起面板")
        self.assertTrue(user_hidden, "收起要标记成「用户主动收起」，不然监控会立刻把它拉回来")


if __name__ == "__main__":
    unittest.main(verbosity=2)
