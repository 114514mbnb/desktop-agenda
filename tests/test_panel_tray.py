"""面板自己的托盘图标 + "点了没反应"的可见回执。

用户提的三个问题，这里各钉一条：
  1. 「点托盘图标/快捷方式时，把控制台窗口带到最前面，并在任务栏闪烁提醒」
  2. 「托盘图标要一直在：只要程序在跑（哪怕只有面板），任务栏右下角就有它的图标，
      点它能开控制台」
  3. 双击快捷方式时面板本来就在屏幕上 → 必须给个可见回执，否则用户以为"点了没反应"
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agenda import panel_tray, tray
from agenda.panel_tray import PanelTray


class FakeIcon:
    """替身托盘图标：记录 start/stop/notify，不碰真实通知区。"""

    def __init__(self, title, on_action, icon_path=None):
        self.title = title
        self.on_action = on_action
        self.icon_path = icon_path
        self.started = False
        self.stopped = False
        self.notes: list[tuple[str, str]] = []

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.stopped = True

    def notify(self, title: str, message: str, *, warning: bool = False) -> bool:
        self.notes.append((title, message))
        return True


class PanelTrayHandoffTests(unittest.TestCase):
    """面板与控制台的托盘交接：通知区里**始终只有一个**图标。"""

    def setUp(self) -> None:
        self.made: list[FakeIcon] = []
        self.real = tray.TrayIcon
        made = self.made

        class Factory(FakeIcon):
            def __init__(self, title, on_action, icon_path=None):
                super().__init__(title, on_action, icon_path)
                made.append(self)

        tray.TrayIcon = Factory
        self.console_running = False
        self.tray = PanelTray(
            title="桌面日程", icon_path=Path("x.ico"),
            on_action=lambda _c: None,
            client_running=lambda: self.console_running,
        )
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        tray.TrayIcon = self.real
        self.tray.remove()

    def test_the_icon_is_added_when_only_the_panel_runs(self):
        """只有面板在跑时，通知区里要有图标（用户点名要的）。"""
        self.assertEqual(self.tray.sync(), "added")
        self.assertTrue(self.tray.active)
        self.assertTrue(self.made[0].started)

    def test_it_hands_over_to_the_console(self):
        """控制台起来了就把面板的图标撤掉 —— 不然通知区里并排两个一样的图标。"""
        self.tray.sync()
        self.console_running = True
        self.assertEqual(self.tray.sync(), "removed")
        self.assertFalse(self.tray.active)
        self.assertTrue(self.made[0].stopped)

    def test_it_takes_the_icon_back_when_the_console_exits(self):
        """控制台退了，图标要回到面板手里（否则用户又找不到它了）。"""
        self.tray.sync()
        self.console_running = True
        self.tray.sync()
        self.console_running = False
        self.assertEqual(self.tray.sync(), "added")
        self.assertTrue(self.tray.active)
        self.assertEqual(len(self.made), 2, "应当重新挂一个，而不是复用已经停掉的")

    def test_syncing_repeatedly_does_not_pile_up_icons(self):
        for _ in range(5):
            self.tray.sync()
        self.assertEqual(len(self.made), 1)

    def test_a_failing_start_is_reported_instead_of_claimed(self):
        class Broken(FakeIcon):
            def start(self) -> bool:
                return False

        tray.TrayIcon = Broken
        self.assertEqual(self.tray.sync(), "failed")
        self.assertFalse(self.tray.active)

    def test_the_action_callback_is_forwarded(self):
        seen: list[int] = []
        forward = PanelTray(title="x", icon_path=None,
                            on_action=seen.append,
                            client_running=lambda: False)
        forward.sync()
        self.made[-1].on_action(tray.ID_OPEN_CONSOLE)
        self.assertEqual(seen, [tray.ID_OPEN_CONSOLE])

    def test_a_crashing_action_callback_does_not_kill_the_tray_thread(self):
        def boom(_command):
            raise RuntimeError("boom")

        forward = PanelTray(title="x", icon_path=None, on_action=boom,
                            client_running=lambda: False)
        forward.sync()
        self.made[-1].on_action(tray.ID_QUIT)      # 不许抛出去

    def test_balloon_works_only_with_a_live_icon(self):
        self.assertFalse(self.tray.balloon("桌面日程", "还没挂图标"))
        self.tray.sync()
        self.assertTrue(self.tray.balloon("桌面日程", "已经有了"))
        self.assertEqual(self.made[0].notes, [("桌面日程", "已经有了")])


class TrayBalloonTests(unittest.TestCase):
    """`tray.balloon()` 以前是个空壳：`return True` 但什么都不做。"""

    def test_the_module_level_helper_is_not_a_stub(self):
        source = Path(tray.__file__).read_text(encoding="utf-8")
        self.assertIn("def balloon", source)
        self.assertIn("_LAST_ICON", source, "模块级 balloon 没有转发给任何图标")

    def test_it_says_false_when_there_is_no_icon(self):
        saved = tray._LAST_ICON
        tray._LAST_ICON = None
        try:
            self.assertFalse(tray.balloon("标题", "内容"),
                             "没有图标却报成功 —— 调用方会以为用户看到了提示")
        finally:
            tray._LAST_ICON = saved

    def test_the_icon_reports_a_balloon(self):
        class FakeIcon:
            def __init__(self):
                self.notes = []

            def notify(self, title, message, *, warning=False):
                self.notes.append((title, message, warning))
                return True

        fake = FakeIcon()
        tray._LAST_ICON = fake
        try:
            self.assertTrue(tray.balloon("桌面日程", "日程表已经在运行", warning=True))
        finally:
            tray._LAST_ICON = None
        self.assertEqual(fake.notes, [("桌面日程", "日程表已经在运行", True)])

    def test_explorer_restart_is_handled(self):
        """Explorer 重启后通知区的图标会消失，得重新挂上。"""
        source = Path(tray.__file__).read_text(encoding="utf-8")
        self.assertIn("WM_TASKBARCREATED", source)
        self.assertIn("_readd_icon", source)
        self.assertIn("NIF_INFO", source, "没有 NIF_INFO 就弹不出气泡")


class PanelFeedbackTests(unittest.TestCase):
    """托盘菜单的命令必须落到**主线程**上做（Tk 不能跨线程碰）。"""

    def test_tray_commands_map_to_panel_actions(self):
        source = Path(panel_tray.__file__).parent.joinpath("panel.py").read_text(
            encoding="utf-8")
        for name in ("_init_tray", "_tray_tick", "_handle_tray", "_console_running"):
            self.assertIn(f"def {name}", source, f"panel.py 里没有 {name}")
        for item in ("open_console", "open_panel", "close_panel",
                     "quick_entry", "merge", "quit"):
            self.assertIn(f'ids["{item}"]', source, f"托盘菜单项 {item} 没有落点")

    def test_the_tray_action_is_scheduled_on_the_main_thread(self):
        source = Path(panel_tray.__file__).parent.joinpath("panel.py").read_text(
            encoding="utf-8")
        self.assertIn('self.root.after(0, lambda: self._handle_tray(command))', source,
                      "托盘回调没有排到主线程 —— Tk 不允许跨线程操作")

    def test_the_icon_is_released_before_the_process_goes_away(self):
        """退出前必须摘掉图标：进程被结束的话图标会**留在通知区**，
        点它永远没反应（用户就是这么报的："点了那个图标好长时间都没有响应"）。"""
        source = Path(panel_tray.__file__).parent.joinpath("panel.py").read_text(
            encoding="utf-8")
        quit_body = source.split("    def quit(self)", 1)[1].split("def ", 1)[0]
        self.assertIn("tray.remove()", quit_body, "退出路径没有摘掉托盘图标")

    def test_the_shortcut_gives_visible_feedback_when_already_visible(self):
        """双击快捷方式时面板本来就在屏幕上 → 要给可见回执，不能"看起来没反应"。"""
        source = Path(panel_tray.__file__).parent.joinpath("panel.py").read_text(
            encoding="utf-8")
        self.assertIn("日程表已经在运行", source)
        self.assertIn("tray_balloon(", source, "没有气泡回执")

    def test_the_console_is_brought_to_the_front_with_a_flash(self):
        from agenda import control_window, winlayer

        source = Path(control_window.__file__).read_text(encoding="utf-8")
        self.assertIn("def _bring_to_front", source)
        self.assertIn("winlayer.bring_to_front(hwnd, flash=True)", source)
        winlayer_source = Path(winlayer.__file__).read_text(encoding="utf-8")
        self.assertIn("FlashWindowEx", winlayer_source, "没有真的调用闪烁 API")
        self.assertIn("def bring_to_front", winlayer_source)


if __name__ == "__main__":
    unittest.main()
