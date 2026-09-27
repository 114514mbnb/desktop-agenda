"""全局热键 + 剪贴板录入的测试。

这个功能有两层，分开测：
  * **纯文本层**（解析 / 规范化 / 拒绝非法写法）—— 任何平台都能跑；
  * **Win32 层**（冲突检测、真正登记、WM_HOTKEY 送达、剪贴板读写）—— 只在 Windows 上跑。

热键这种东西最容易"看起来能用其实没生效"（登记失败被静默吞掉、消息没进我们的窗口过程），
所以下面这些用例都盯着**可观察的结果**：冲突要能报出来、按下要真的回调到、
剪贴板写进去要能读回来。
"""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import hotkey  # noqa: E402
from tests import ClipboardSafeTestCase  # noqa: E402

IS_WINDOWS = sys.platform == "win32"


class HotkeyParsingTests(unittest.TestCase):
    """写法解析：用户输入千奇百怪，规范化之后必须只有一种形态。"""

    def test_basic_combo(self):
        parsed = hotkey.parse("Ctrl+Alt+Q")
        self.assertEqual(parsed.mods, hotkey.MOD_CONTROL | hotkey.MOD_ALT)
        self.assertEqual(parsed.vk, ord("Q"))
        self.assertEqual(parsed.text, "Ctrl+Alt+Q")

    def test_case_and_spacing_are_forgiving(self):
        for text in ("ctrl+alt+q", "CTRL + ALT + Q", "  Ctrl+Alt+q  ", "ctl+alt+q"):
            with self.subTest(text=text):
                self.assertEqual(hotkey.parse(text).text, "Ctrl+Alt+Q")

    def test_canonical_modifier_order(self):
        """不管用户怎么写，存进配置的形态固定为 Ctrl+Alt+Shift+Win+键。"""
        self.assertEqual(hotkey.parse("shift+win+ctrl+k").text, "Ctrl+Shift+Win+K")
        self.assertEqual(hotkey.parse("win+shift+alt+ctrl+k").text, "Ctrl+Alt+Shift+Win+K")

    def test_win_and_chinese_synonyms(self):
        self.assertEqual(hotkey.parse("super+q").mods, hotkey.MOD_WIN)
        self.assertEqual(hotkey.parse("Ctrl+空格").vk, 0x20)
        self.assertEqual(hotkey.parse("Ctrl+回车").vk, 0x0D)
        self.assertEqual(hotkey.parse("Ctrl+上").vk, 0x26)

    def test_function_keys_and_symbols(self):
        self.assertEqual(hotkey.parse("Ctrl+F9").vk, 0x78)
        self.assertEqual(hotkey.parse("Ctrl+F24").text, "Ctrl+F24")
        self.assertEqual(hotkey.parse("Alt+1").vk, 0x31)
        self.assertEqual(hotkey.parse("Ctrl+/").text, "Ctrl+/")

    def test_rejects_a_bare_key(self):
        """不带修饰键会吃掉全系统的一个按键——必须拒绝。"""
        with self.assertRaises(hotkey.HotkeyError) as caught:
            hotkey.parse("Q")
        self.assertIn("修饰键", str(caught.exception))

    def test_rejects_modifier_only(self):
        with self.assertRaises(hotkey.HotkeyError) as caught:
            hotkey.parse("Ctrl+Alt")
        self.assertIn("再加一个键", str(caught.exception))

    def test_rejects_two_main_keys(self):
        with self.assertRaises(hotkey.HotkeyError):
            hotkey.parse("Ctrl+Q+W")

    def test_rejects_unknown_key(self):
        with self.assertRaises(hotkey.HotkeyError) as caught:
            hotkey.parse("Ctrl+不知道这是什么键")
        self.assertIn("认不出", str(caught.exception))

    def test_rejects_empty(self):
        for text in ("", "   ", "+", "++"):
            with self.subTest(text=text):
                with self.assertRaises(hotkey.HotkeyError):
                    hotkey.parse(text)

    def test_rejects_shift_only_combos(self):
        """只带 Shift 的组合会在**正常打字**时被触发——用户就是这么中招的。

        实测：把热键设成 `Shift+Z` 后，每打一个大写 Z 都会命中一次全局热键；
        当时每次触发都会让面板进程崩掉，用户看到的现象是"日程表用着用着就没了"。
        """
        for text in ("Shift+Z", "Shift+A", "shift+1"):
            with self.subTest(text=text):
                with self.assertRaises(hotkey.HotkeyError) as caught:
                    hotkey.parse(text)
                self.assertIn("Ctrl / Alt / Win", str(caught.exception))

    def test_rejects_common_editing_shortcuts(self):
        """Ctrl+C/V/X/Z… 是全宇宙通用的编辑快捷键，注册成全局热键只会天天打架。"""
        for text, name in (("Ctrl+Z", "撤销"), ("Ctrl+C", "复制"), ("Ctrl+V", "粘贴"),
                           ("Ctrl+A", "全选"), ("Ctrl+S", "保存")):
            with self.subTest(text=text):
                with self.assertRaises(hotkey.HotkeyError) as caught:
                    hotkey.parse(text)
                self.assertIn(name, str(caught.exception))

    def test_accepts_safe_combos_with_the_same_keys(self):
        """挡住危险组合，但别把孩子跟洗澡水一起倒掉。"""
        for text in ("Ctrl+Alt+Q", "Ctrl+Shift+Z", "Ctrl+Alt+Z", "Alt+Q", "Win+Q",
                     "Ctrl+F9", "Alt+Shift+Z", "Ctrl+Alt+Shift+F9"):
            with self.subTest(text=text):
                self.assertTrue(hotkey.parse(text).text)

    def test_warns_about_system_reserved_combos(self):
        """Alt+Tab / Win+L 这类是系统自己的，登记上去只会让人以为程序坏了。"""
        for text in ("Alt+Tab", "Win+L", "Alt+F4", "Ctrl+Esc"):
            with self.subTest(text=text):
                with self.assertRaises(hotkey.HotkeyError) as caught:
                    hotkey.parse(text)
                self.assertIn("不建议", str(caught.exception))

    def test_describe_is_lenient(self):
        self.assertEqual(hotkey.describe("ctrl+alt+q"), "Ctrl+Alt+Q")
        self.assertEqual(hotkey.describe("胡说八道"), "")
        self.assertEqual(hotkey.describe(""), "")


@unittest.skipUnless(IS_WINDOWS, "全局热键是 Windows 专有功能")
class HotkeyWin32Tests(unittest.TestCase):
    """真刀真枪：登记、冲突、消息送达。"""

    FREE_SPEC = "Ctrl+Win+Alt+F9"        # 极不可能被别的程序占用
    ID = 0x4A31

    def test_probe_says_free_for_an_unused_combo(self):
        ok, message = hotkey.probe(hotkey.parse(self.FREE_SPEC))
        self.assertTrue(ok, f"空闲组合被误判成冲突：{message}")

    def test_probe_detects_a_real_conflict(self):
        """先占住一个组合，再探测同一个组合——必须报"已被占用"，而不是默默可用。"""
        parsed = hotkey.parse(self.FREE_SPEC)
        started, _ = hotkey.register(0, parsed, self.ID)
        self.assertTrue(started, "测试自身没能占住这个组合")
        try:
            ok, message = hotkey.probe(parsed)
            self.assertFalse(ok, "冲突没有被检测出来")
            self.assertIn("占用", message)
            verdict, why = hotkey.check(self.FREE_SPEC)
            self.assertFalse(verdict)
            self.assertIn("占用", why)
        finally:
            hotkey.unregister(0, self.ID)
        # 注销之后应该又能用了（证明上面失败的登记被清理干净）
        self.assertTrue(hotkey.probe(parsed)[0])

    def test_check_reports_parse_errors_as_text(self):
        ok, message = hotkey.check("Q")
        self.assertFalse(ok)
        self.assertIn("修饰键", message)

    def test_check_accepts_our_own_registration(self):
        """重复保存同一个组合是常见操作，不能报成"被占用"。"""
        parsed = hotkey.parse(self.FREE_SPEC)
        self.assertTrue(hotkey.register(0, parsed, self.ID)[0])
        try:
            ok, message = hotkey.check(self.FREE_SPEC, exclude_own=0)
            self.assertTrue(ok, message)
            self.assertIn("正在使用", message)
        finally:
            hotkey.unregister(0, self.ID)


@unittest.skipUnless(IS_WINDOWS, "全局热键是 Windows 专有功能")
class HotkeyHookTests(unittest.TestCase):
    """WM_HOTKEY 必须真的进到我们的窗口过程并回调出来。

    用 `SendMessageW(hwnd, WM_HOTKEY, id, 0)` 模拟"用户按下了热键"，
    这样不用真的去按键，也不依赖谁有焦点。
    """

    ID = 0x4A32

    def _panel(self, tmp):
        from agenda.panel import AgendaPanel

        return AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                           window_mode="desktop")

    def test_hotkey_message_reaches_the_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            try:
                panel.root.update()
                hwnd = panel._hwnd or panel.root.winfo_id()
                calls: list[int] = []
                hotkey.unhook_hotkey(hwnd)
                self.assertTrue(hotkey.hook_hotkey(hwnd, calls.append), "钩子没挂上")
                ctypes.windll.user32.SendMessageW(
                    wintypes.HWND(hwnd), hotkey.WM_HOTKEY, self.ID, 0)
                panel.root.update()
                self.assertEqual(calls, [self.ID], "WM_HOTKEY 没有回调出来")
            finally:
                try:
                    hotkey.unhook_hotkey(panel._hwnd or panel.root.winfo_id())
                except Exception:
                    pass
                panel.quit()

    def test_hook_is_idempotent_and_removable(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(tmp)
            try:
                panel.root.update()
                hwnd = panel._hwnd or panel.root.winfo_id()
                hotkey.unhook_hotkey(hwnd)
                self.assertTrue(hotkey.hook_hotkey(hwnd, lambda _i: None))
                self.assertTrue(hotkey.hotkey_hooked(hwnd))
                self.assertFalse(hotkey.hook_hotkey(hwnd, lambda _i: None), "重复挂载应返回 False")
                hotkey.unhook_hotkey(hwnd)
                self.assertFalse(hotkey.hotkey_hooked(hwnd))
            finally:
                panel.quit()


@unittest.skipUnless(IS_WINDOWS, "剪贴板读取走的是 Win32 API")
class ClipboardTests(ClipboardSafeTestCase):
    def test_round_trip(self):
        sample = "【提醒】\n1.中秋国庆假期备案：今天中午12:00前完成。"
        self.assertTrue(hotkey.write_clipboard_text(sample), "写剪贴板失败")
        self.assertEqual(hotkey.read_clipboard_text(), sample)

    def test_read_never_raises(self):
        """剪贴板被别的程序占着、或者里面不是文本时，只该返回空串，不该抛异常。"""
        self.assertIsInstance(hotkey.read_clipboard_text(attempts=1), str)


class PanelHotkeyTests(ClipboardSafeTestCase):
    """面板侧的接线：配置读得到、开关起作用、录入动作有反馈。"""

    @staticmethod
    def _no_selection(data: Path) -> None:
        """关掉「抓选区」。默认开启时，测试进程会真的向前台窗口发一次 Ctrl+C，
        那不可控（可能把别处的文字抓进来）。抓选区本身由 `SelectionCaptureTests`
        用假系统交互做确定性验证。"""
        from agenda.client_config import ClientConfig

        config = ClientConfig.load(data)
        config.hotkey_selection = False
        config.save(data)

    def _panel(self, tmp):
        from agenda.panel import AgendaPanel

        return AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                           window_mode="desktop")

    def test_panel_registers_the_configured_hotkey(self):
        if not IS_WINDOWS:
            self.skipTest("只有 Windows 支持")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            from agenda.client_config import ClientConfig

            config = ClientConfig.load(data)
            config.hotkey_enabled = True
            config.hotkey = "Ctrl+Win+Alt+F10"
            config.save(data)

            panel = self._panel(data)
            try:
                panel.root.update()
                panel._apply_hotkey()
                panel.root.update()
                self.assertTrue(panel._hotkey_id, "面板没有登记热键")
                current = hotkey.registered(panel._hotkey_hwnd)
                self.assertIsNotNone(current, "面板没有登记配置里的热键")
                # 写法会被规范化成固定顺序（Ctrl+Alt+Shift+Win+键）
                self.assertEqual(current.text, hotkey.parse("Ctrl+Win+Alt+F10").text)
                self.assertEqual(current.vk, 0x79)          # F10
            finally:
                panel.quit()

    def test_disabled_hotkey_is_not_registered(self):
        if not IS_WINDOWS:
            self.skipTest("只有 Windows 支持")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            from agenda.client_config import ClientConfig

            config = ClientConfig.load(data)
            config.hotkey_enabled = False
            config.save(data)

            panel = self._panel(data)
            try:
                panel.root.update()
                panel._apply_hotkey()
                panel.root.update()
                self.assertIsNone(hotkey.registered(panel._hwnd), "关掉了却还占着热键")
            finally:
                panel.quit()

    def test_clipboard_action_ingests_a_notice(self):
        """按热键的完整闭环：剪贴板里的群通知 → 真的进了 events.json。"""
        if not IS_WINDOWS:
            self.skipTest("只有 Windows 支持")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            panel = self._panel(data)
            try:
                panel.root.update()
                hotkey.write_clipboard_text(
                    "【提醒】\n1.中秋国庆假期备案：今天中午12:00前完成去向备案。\n"
                    "  地点：明德楼 302   人员：各班班长\n")
                panel.ingest_clipboard()
                panel.root.update()
                from agenda.pipeline import Pipeline

                events = Pipeline(data).load_events()
                titles = [event.title for event in events]
                self.assertTrue(any("假期备案" in title for title in titles),
                                f"剪贴板内容没有入库，当前事件：{titles}")
            finally:
                panel.quit()

    def test_empty_clipboard_is_harmless(self):
        if not IS_WINDOWS:
            self.skipTest("只有 Windows 支持")
        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(Path(tmp))
            try:
                panel.root.update()
                hotkey.write_clipboard_text("   ")
                panel.ingest_clipboard()          # 不许抛异常
                panel.root.update()
            finally:
                panel.quit()

    def test_hotkey_callback_only_sets_a_flag(self):
        """**窗口过程里不许调 Tk**——按一下热键把面板进程打死过，这条必须钉住。

        实测症状：按下热键后通知确实入库了，但面板进程当场消失（无异常、panel.log 为空）。
        根因是在子类过程里调 `ingest_clipboard()`（它要重建控件、重画画布），
        重入 Tcl 的事件处理导致硬崩。现在回调只置标记，`_native_tick` 再干活。
        """
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            panel = self._panel(data)
            try:
                panel.root.update()
                panel._hotkey_pending = False
                panel._on_hotkey(panel.HOTKEY_ID)
                self.assertTrue(panel._hotkey_pending, "没有留下待办标记")
                self.assertFalse(panel._hotkey_pending is False)
                # 别人的热键 id 不该触发
                panel._hotkey_pending = False
                panel._on_hotkey(panel.HOTKEY_ID + 1)
                self.assertFalse(panel._hotkey_pending)
            finally:
                panel.quit()

    def test_native_tick_does_the_ingest(self):
        """待办要被 `_native_tick` 取走并真的入库。"""
        if not IS_WINDOWS:
            self.skipTest("只有 Windows 支持")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self._no_selection(data)
            panel = self._panel(data)
            try:
                panel.root.update()
                hotkey.write_clipboard_text(
                    "【提醒】\n1.测试：明天之内提交材料，地点 明德楼 302。\n")
                panel._on_hotkey(panel.HOTKEY_ID)          # 窗口过程只置标记
                panel._native_tick()                       # 普通 Tk 上下文里干活
                panel.root.update()
                from agenda.pipeline import Pipeline

                titles = [event.title for event in Pipeline(data).load_events()]
                self.assertTrue(any("测试" in title for title in titles),
                                f"待办没有被执行，当前事件：{titles}")
                self.assertFalse(panel._hotkey_pending, "待办没有被清掉")
            finally:
                panel.quit()

    def test_native_tick_is_reentrant_safe_after_quit(self):
        """面板已经在关闭流程里时，tick 不该再排下一次（否则 Tk 会报 invalid command）。"""
        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel(Path(tmp))
            try:
                panel.root.update()
                panel._closing = True
                panel._native_tick()                       # 不该抛异常
            finally:
                panel.quit()

    def test_invalid_saved_hotkey_falls_back_to_the_default(self):
        """配置里存着不安全/不合法的组合时，面板要退回默认组合并提示，
        而不是让用户面对"按了没反应"。"""
        if not IS_WINDOWS:
            self.skipTest("只有 Windows 支持")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            from agenda.client_config import ClientConfig

            config = ClientConfig.load(data)
            config.hotkey = "Shift+Z"          # 规则收紧之前设的，现在不合法
            config.hotkey_enabled = True
            config.save(data)

            panel = self._panel(data)
            toasts: list[str] = []
            panel._toast = lambda message, **kw: toasts.append(message)
            try:
                panel.root.update()
                panel._apply_hotkey()
                panel.root.update()
                self.assertEqual(panel._hotkey_text, hotkey.DEFAULT_HOTKEY,
                                 "没有退回默认组合")
                self.assertIsNotNone(hotkey.registered(panel._hotkey_hwnd))
                self.assertTrue(any("临时改用" in message for message in toasts),
                                f"没有提示用户去改设置：{toasts}")
            finally:
                panel.quit()

    def test_config_change_is_picked_up(self):
        """用户改完热键，面板要在下一次数据巡检时自己重新登记，不用重启。"""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            panel = self._panel(data)
            try:
                panel.root.update()
                before = panel._data_changed()   # 第一次只记录基线
                self.assertFalse(before)
                from agenda.client_config import ClientConfig

                config = ClientConfig.load(data)
                config.hotkey = "Ctrl+Win+Alt+F11"
                config.save(data)
                time.sleep(0.02)
                self.assertTrue(panel._data_changed(), "client.json 改动没有被察觉")
            finally:
                panel.quit()


@unittest.skipUnless(IS_WINDOWS, "全局热键与真实按键只在 Windows 上可测")
class HotkeyLivePanelTests(ClipboardSafeTestCase):
    """真起一个面板进程、真按热键，验证**进程还活着**且通知入库。

    为什么要这么重的一层测试：这个 bug（在窗口过程里调 Tk → 进程硬崩）用同进程的
    单元测试根本抓不到——测试进程自己就是面板，崩了整套测试直接消失；
    而且当时的用例只断言了「回调被调用了」，崩溃发生在回调返回之后。
    只有另起进程 + 检查它还活着，才能把这条链路钉住。
    """

    #: 冷门组合，避免和别的程序或用户自己的实例撞车
    SPEC = "Ctrl+Alt+Shift+F9"
    NOTICE = "【提醒】\n1.集成测试：明天之内提交材料，地点 明德楼 302。\n"

    @staticmethod
    def _press() -> None:
        """合成一次 Ctrl+Alt+Shift+F9（RegisterHotKey 认真实输入）。"""
        vks = [0x11, 0x12, 0x10, 0x78]        # Ctrl / Alt / Shift / F9
        user32 = ctypes.windll.user32
        for vk in vks:
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
        for vk in reversed(vks):
            user32.keybd_event(vk, 0, 0x0002, 0)
            time.sleep(0.02)

    def test_real_keypress_ingests_and_keeps_the_panel_alive(self):
        import json
        import subprocess

        root = Path(__file__).resolve().parent.parent
        pythonw = root / "runtime" / "pythonw.exe"
        if not pythonw.exists():
            self.skipTest("没有自带运行时 pythonw.exe")

        parsed = hotkey.parse(self.SPEC)
        free, why = hotkey.probe(parsed)
        if not free:
            self.skipTest(f"{self.SPEC} 已被占用，跳过（{why}）")

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            from agenda.client_config import ClientConfig

            config = ClientConfig.load(data)
            config.hotkey = self.SPEC
            config.hotkey_enabled = True
            # 集成测试走"纯剪贴板"路径：抓选区会真发 Ctrl+C 到当时的前台窗口，
            # 那是不可控的；抓选区本身由 SelectionCaptureTests 用假系统交互确定性验证。
            config.hotkey_selection = False
            config.save(data)
            (data / "events.json").write_text(
                json.dumps({"schemaVersion": 1, "events": []}), encoding="utf-8")

            process = subprocess.Popen(
                [str(pythonw), "-B", str(root / "main.py"),
                 "--data-dir", str(data), "--width", "320"],
                cwd=str(root))
            try:
                registered = False
                for _ in range(40):                    # 最多等 10 秒
                    time.sleep(0.25)
                    if process.poll() is not None:
                        break
                    if not hotkey.probe(parsed)[0]:
                        registered = True
                        break
                if process.poll() is not None:
                    self.fail("面板进程在启动阶段就退出了")
                if not registered:
                    self.skipTest("面板没有登记热键（可能被别的程序占用）")

                hotkey.write_clipboard_text(self.NOTICE)
                self._press()
                time.sleep(2.0)

                self.assertIsNone(
                    process.poll(),
                    "按下热键后面板进程退出了——这正是那个硬崩 bug 的症状")
                payload = json.loads((data / "events.json").read_text(encoding="utf-8"))
                titles = [str(item.get("title", "")) for item in payload.get("events", [])]
                self.assertTrue(any("集成测试" in title for title in titles),
                                f"热键没能把剪贴板内容入库：{titles}")

                # 再来一次：连续使用必须稳定（第一次崩溃过之后再也用不了）
                hotkey.write_clipboard_text(
                    "【提醒】\n1.集成测试乙：后天之内提交，地点 明德楼 303。\n")
                self._press()
                time.sleep(2.0)
                self.assertIsNone(process.poll(), "第二次按热键把面板按死了")
                payload = json.loads((data / "events.json").read_text(encoding="utf-8"))
                titles = [str(item.get("title", "")) for item in payload.get("events", [])]
                self.assertTrue(any("集成测试乙" in title for title in titles),
                                f"第二次热键没有入库：{titles}")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    process.kill()


@unittest.skipUnless(IS_WINDOWS, "抓选区依赖 Win32 的按键与剪贴板")
class SelectionCaptureTests(unittest.TestCase):
    """「选中文字 → 按热键 → 直接识别」，不用先手动复制。

    用户原话：「我要求不需要录入剪贴板，直接选中后使用热键就能识别」。
    实现上是**借用剪贴板**：记住原内容 → 合成一次 Ctrl+C → 读出来 → 复原。
    这几个用例把 press/read/write/sleep 全换成假的，只验证顺序与分支，
    不发真按键、不碰真剪贴板（也就不会污染跑测试的人自己的剪贴板）。
    """

    def _harness(self, clipboard: list[str], class_name: str = "Notepad",
                 press_result: str | None = None):
        """造一套假的"系统交互"，返回 (捕获函数参数, 记录本)。"""
        log: list[str] = []

        def read() -> str:
            return clipboard[0] if clipboard else ""

        def write(text: str) -> bool:
            log.append(f"write:{text[:12]}")
            if clipboard:
                clipboard[0] = text
            else:
                clipboard.append(text)
            return True

        def press() -> None:
            log.append("press")
            if press_result is not None:
                clipboard[0] = press_result     # 模拟"前台程序把选区放进剪贴板"

        return {"press": press, "read": read, "write": write,
                "sleep": lambda _s: None, "foreground_class": lambda: class_name,
                "attempts": 3, "interval": 0.0}, log

    def test_captures_and_restores_the_old_clipboard(self):
        clipboard = ["我原来的剪贴板内容"]
        kwargs, log = self._harness(clipboard, press_result="选中的那条群通知")
        text, note = hotkey.capture_selection(**kwargs)
        self.assertEqual(text, "选中的那条群通知")
        self.assertEqual(note, "")
        self.assertEqual(log, ["press", "write:我原来的剪贴板内容"],
                         "没有「先发键、再复原」这个顺序")
        self.assertEqual(clipboard[0], "我原来的剪贴板内容", "原来的剪贴板没被放回去")

    def test_empty_clipboard_needs_no_restore(self):
        clipboard: list[str] = [""]
        kwargs, log = self._harness(clipboard, press_result="选中的文字")
        text, _note = hotkey.capture_selection(**kwargs)
        self.assertEqual(text, "选中的文字")
        self.assertEqual(log, ["press"], "本来就没有内容，不该多写一次剪贴板")

    def test_no_selection_reports_clearly(self):
        """按了 Ctrl+C 但剪贴板没变 = 用户没选中任何东西。"""
        clipboard = ["原内容"]
        kwargs, _log = self._harness(clipboard, press_result=None)   # 不改变剪贴板
        text, note = hotkey.capture_selection(**kwargs)
        self.assertEqual(text, "")
        self.assertIn("没有检测到选中的文字", note)

    def test_console_windows_are_skipped(self):
        """终端里 Ctrl+C 是中断信号——**绝不能**往里发（可能打断用户正在跑的命令）。"""
        for class_name in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty"):
            with self.subTest(class_name=class_name):
                clipboard = ["原内容"]
                kwargs, log = self._harness(clipboard, class_name=class_name,
                                            press_result="不该被抓到")
                text, note = hotkey.capture_selection(**kwargs)
                self.assertEqual(text, "")
                self.assertNotIn("press", log, "往终端发了 Ctrl+C")
                self.assertIn("终端", note)

    def test_our_own_windows_are_skipped(self):
        """前台是本程序自己的窗口时（例如刚点过面板），没必要也没法抓选区。"""
        kwargs, log = self._harness(["原内容"], class_name="TkTopLevel")
        text, note = hotkey.capture_selection(**kwargs)
        self.assertEqual(text, "")
        self.assertNotIn("press", log)
        self.assertIn("本程序窗口", note)

    def test_press_failure_is_reported(self):
        log: list[str] = []

        def broken_press() -> None:
            raise OSError("模拟按键失败")

        text, note = hotkey.capture_selection(
            press=broken_press, read=lambda: "", write=lambda _t: True,
            sleep=lambda _s: None, foreground_class=lambda: "Notepad", attempts=1)
        self.assertEqual(text, "")
        self.assertIn("复制指令", note)
        self.assertEqual(log, [])

    def test_is_console_window_matching(self):
        self.assertTrue(hotkey.is_console_window("ConsoleWindowClass"))
        self.assertTrue(hotkey.is_console_window("  cascadia_hosting_window_class "))
        self.assertFalse(hotkey.is_console_window("Notepad"))
        self.assertFalse(hotkey.is_console_window(""))

    # -- 修饰键污染：用户"选中了按热键却录不进去"的真正原因 --------------
    def _modifier_recorder(self, held: set[int], release_after: float | None = None):
        """造一套假的键盘状态与发送记录。

        `held`：一开始按着的键。
        `release_after`：模拟用户过了这么多秒才松手；None 表示**一直按着**（等不到松手）。
        """
        state = {"held": set(held), "elapsed": 0.0}
        log: list[tuple[int, bool]] = []

        def key_state(vk: int) -> bool:
            return vk in state["held"]

        def sleep(seconds: float) -> None:
            state["elapsed"] += seconds
            if release_after is not None and state["elapsed"] >= release_after:
                state["held"] = set()

        def send(vk: int, up: bool) -> None:
            log.append((vk, up))

        return {"key_state": key_state, "sleep": sleep, "send": send}, log

    def test_held_modifiers_are_released_before_the_copy(self):
        """按着 Alt+Shift 时，必须先补 KEYUP，合成的 Ctrl+C 才不会被污染。

        实测（Chromium，和 QQ NT 同引擎）：
          不按修饰键          5/6 成功
          按住 Alt+Shift      0/6 成功   ← 用户按 Alt+Shift+S 热键时的真实状态
          先补 KEYUP 再发     6/6 成功
        系统在**按下 S 的瞬间**就发 WM_HOTKEY，而面板隔几十毫秒才抓选区，
        那会儿用户通常还没松手——所以这一步不是可选的。
        """
        kwargs, log = self._modifier_recorder({hotkey.VK_MENU, hotkey.VK_SHIFT})
        hotkey._press_ctrl_c(**kwargs)

        released = [vk for vk, up in log if up and vk in (hotkey.VK_MENU, hotkey.VK_SHIFT)]
        self.assertIn(hotkey.VK_MENU, released, "Alt 没有先松开")
        self.assertIn(hotkey.VK_SHIFT, released, "Shift 没有先松开")

        ctrl_down = log.index((hotkey.VK_CONTROL, False))
        for vk in (hotkey.VK_MENU, hotkey.VK_SHIFT):
            self.assertLess(log.index((vk, True)), ctrl_down,
                            "松修饰键必须发生在按 Ctrl 之前，否则还是被污染")

    def test_user_who_lets_go_is_not_fought_with(self):
        """用户自己松手了就别插手：不该发任何多余的 KEYUP。"""
        kwargs, log = self._modifier_recorder({hotkey.VK_MENU, hotkey.VK_SHIFT},
                                              release_after=0.1)
        hotkey._press_ctrl_c(**kwargs)
        self.assertEqual(log, [(hotkey.VK_CONTROL, False), (hotkey.VK_C, False),
                               (hotkey.VK_C, True), (hotkey.VK_CONTROL, True)],
                         "用户已经松手，不该再补 KEYUP")

    def test_plain_ctrl_c_when_nothing_is_held(self):
        kwargs, log = self._modifier_recorder(set())
        hotkey._press_ctrl_c(**kwargs)
        self.assertEqual(log, [(hotkey.VK_CONTROL, False), (hotkey.VK_C, False),
                               (hotkey.VK_C, True), (hotkey.VK_CONTROL, True)])

    def test_ctrl_is_not_treated_as_pollution(self):
        """Ctrl 按着不算污染——Ctrl+C 本来就带 Ctrl，松掉反而打断用户。"""
        kwargs, log = self._modifier_recorder({hotkey.VK_CONTROL})
        hotkey._press_ctrl_c(**kwargs)
        self.assertNotIn((hotkey.VK_CONTROL, True), log[:1],
                         "不该先把用户按着的 Ctrl 松开")
        self.assertTrue(any(vk == hotkey.VK_C and not up for vk, up in log))

    def test_release_modifiers_reports_what_it_forced(self):
        kwargs, log = self._modifier_recorder({hotkey.VK_LWIN})
        forced = hotkey._release_modifiers(**kwargs)
        self.assertEqual(forced, [hotkey.VK_LWIN])
        self.assertEqual(log, [(hotkey.VK_LWIN, True)])

    # -- 重试：前台程序偶发漏掉第一次合成按键 ----------------------------
    def test_retries_when_the_first_copy_is_ignored(self):
        """第一次合成按键被前台漏掉时要**再补一次**，而不是直接放弃。

        实测 Chromium 单发成功率约 5/6；对用户来说就是"有时好用有时不好用"。
        """
        clipboard = ["原内容"]
        presses = {"count": 0}

        def press() -> None:
            presses["count"] += 1
            if presses["count"] >= 2:              # 第二次才生效
                clipboard[0] = "第二条通知的内容"

        text, note = hotkey.capture_selection(
            press=press, read=lambda: clipboard[0], write=lambda _t: True,
            sleep=lambda _s: None, foreground_class=lambda: "Chrome_WidgetWin_1",
            attempts=2, interval=0.0)
        self.assertEqual(text, "第二条通知的内容")
        self.assertEqual(note, "")
        self.assertEqual(presses["count"], 2, "没有补发第二次")

    def test_retries_are_bounded(self):
        """前台就是不认账时不能无限按：按满 retries 次就收手。"""
        presses = {"count": 0}

        def press() -> None:
            presses["count"] += 1

        text, note = hotkey.capture_selection(
            press=press, read=lambda: "原内容", write=lambda _t: True,
            sleep=lambda _s: None, foreground_class=lambda: "Notepad",
            attempts=1, interval=0.0, retries=3)
        self.assertEqual(text, "")
        self.assertIn("没有检测到选中的文字", note)
        self.assertEqual(presses["count"], 3)

    def test_success_on_the_first_try_does_not_retry(self):
        clipboard = ["原内容"]
        presses = {"count": 0}

        def press() -> None:
            presses["count"] += 1
            clipboard[0] = "一次就成"

        text, _note = hotkey.capture_selection(
            press=press, read=lambda: clipboard[0], write=lambda _t: True,
            sleep=lambda _s: None, foreground_class=lambda: "Notepad",
            attempts=2, interval=0.0)
        self.assertEqual(text, "一次就成")
        self.assertEqual(presses["count"], 1)


class PanelSelectionModeTests(ClipboardSafeTestCase):
    """面板侧的两种模式：优先抓选区 / 只用剪贴板。

    这个类**必须**继承 `ClipboardSafeTestCase`：下面
    `test_clipboard_only_mode_does_not_press_keys` 要往系统剪贴板里写一条样例通知，
    忘了还原的话，用户按一下热键就会把这句测试文本录进他的真实日程
    （实测发生过——用户截图里那条莫名其妙的日程就是这么来的）。
    """

    def _panel(self, tmp):
        from agenda.panel import AgendaPanel

        return AgendaPanel(Path(tmp), pipeline_ms=0, autostart_pipeline=False,
                           window_mode="desktop")

    @staticmethod
    def _no_selection(data: Path) -> None:
        """关掉"抓选区"（默认开）：测试进程里真发 Ctrl+C 会打到当时的前台窗口，不可控。"""
        from agenda.client_config import ClientConfig

        config = ClientConfig.load(data)
        config.hotkey_selection = False
        config.save(data)

    def _save(self, data: Path, **fields):
        from agenda.client_config import ClientConfig

        config = ClientConfig.load(data)
        for key, value in fields.items():
            setattr(config, key, value)
        config.save(data)

    def test_selection_mode_is_tried_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self._save(data, hotkey_selection=True)
            panel = self._panel(data)
            calls: list[str] = []
            try:
                panel.root.update()
                import agenda.hotkey as hotkey_mod

                real = hotkey_mod.capture_selection
                hotkey_mod.capture_selection = lambda: (calls.append("selection") or
                                                        ("选中的通知：明天9点开会", ""))
                panel.pipeline.ingest_text = lambda text, **kw: calls.append(f"ingest:{text[:8]}") or \
                    type("R", (), {"added": 1, "updated": 0, "candidates": 1})()
                try:
                    panel.ingest_clipboard()
                    panel.root.update()
                finally:
                    hotkey_mod.capture_selection = real
                self.assertEqual(calls[0], "selection", "没有先尝试抓选区")
                self.assertTrue(any(call.startswith("ingest:") for call in calls),
                                f"抓到的选区没有入库：{calls}")
            finally:
                panel.quit()

    def test_clipboard_only_mode_does_not_press_keys(self):
        """关掉开关后**绝不能**模拟按键，只能读剪贴板里已有的内容。"""
        if not IS_WINDOWS:
            self.skipTest("剪贴板读取是 Windows 专有")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self._save(data, hotkey_selection=False)
            panel = self._panel(data)
            try:
                panel.root.update()
                import agenda.hotkey as hotkey_mod

                real = hotkey_mod.capture_selection
                hotkey_mod.capture_selection = lambda: (_ for _ in ()).throw(
                    AssertionError("关掉开关后仍然尝试抓选区"))
                hotkey.write_clipboard_text("【提醒】\n1.只用剪贴板：明天之内提交材料。\n")
                try:
                    panel.ingest_clipboard()
                    panel.root.update()
                finally:
                    hotkey_mod.capture_selection = real
                from agenda.pipeline import Pipeline

                titles = [event.title for event in Pipeline(data).load_events()]
                self.assertTrue(any("只用剪贴板" in title for title in titles),
                                f"剪贴板模式没能录入：{titles}")
            finally:
                panel.quit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
