"""冒烟测试：所有模块都能编译、能导入。

为什么单独有这一项：GUI 模块（panel / control_window）在单元测试里不实例化，
一个语法或缩进错误可以躲过全部行为测试，直到用户双击启动才炸。
这里做两件事：
  1. compileall：全项目语法闸门
  2. importlib 导入每个模块：抓 ImportError / NameError 这类装载期错误
"""

from __future__ import annotations

import compileall
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODULES = (
    "agenda.models",
    "agenda.parsing",
    "agenda.extract",
    "agenda.timetable",
    "agenda.store",
    "agenda.aggregate",
    "agenda.timeline",
    "agenda.pipeline",
    "agenda.theme",
    "agenda.panel",
    "agenda.quick_entry",
    "agenda.client_config",
    "agenda.client_app",
    "agenda.control_window",
    "agenda.tray",
    "agenda.browser",
    "agenda.file_import",
    "agenda.eas",
    "agenda.eas.base",
    "agenda.eas.zfsoft",
    "agenda.eas.files",
    "agenda.eas.registry",
    "agenda.eas.importer",
    "main",
)


class SmokeTests(unittest.TestCase):
    def test_every_module_compiles(self):
        ok = compileall.compile_dir(str(ROOT / "agenda"), quiet=1, force=True)
        ok = compileall.compile_file(str(ROOT / "main.py"), quiet=1, force=True) and ok
        self.assertTrue(ok, "有 .py 文件语法/缩进错误，见 compileall 输出")

    def test_compiles_under_bundled_runtime(self):
        """用项目自带的 runtime 再编一遍。

        踩过的坑：外层 shell 的 compileall 可能用的是另一个解释器/缓存，
        结果是"编译通过"但双击启动仍报 SyntaxError。这里直接调用
        runtime\\python.exe 复核，跟用户实际运行的环境一致。
        """
        import subprocess

        runtime = ROOT / "runtime" / ("python.exe" if sys.platform == "win32" else "bin/python3")
        if not runtime.exists():
            self.skipTest("没有内置 runtime（用户改用系统 Python）")
        completed = subprocess.run(
            [str(runtime), "-B", "-m", "compileall", "-q", str(ROOT / "main.py"), str(ROOT / "agenda")],
            capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(
            completed.returncode, 0,
            f"内置 runtime 编译失败：\n{completed.stdout}\n{completed.stderr}",
        )

    def test_every_module_imports(self):
        failures: list[str] = []
        for name in MODULES:
            try:
                importlib.import_module(name)
            except Exception as error:  # noqa: BLE001
                failures.append(f"{name}: {type(error).__name__}: {error}")
        self.assertEqual(failures, [], "模块导入失败：\n" + "\n".join(failures))

    def test_gui_entrypoints_exist(self):
        """关键类/函数真的在，避免改名后调用方没跟上。"""
        import agenda.control_window as control_window
        import agenda.course_review as course_review
        import agenda.file_import as file_import
        import agenda.panel as panel
        import main as cli

        self.assertTrue(hasattr(panel, "AgendaPanel"))
        self.assertTrue(hasattr(control_window, "ControlWindow"))
        # CloseDialog 已删除：点 X 只最小化至托盘，不再让用户选"留托盘还是全退出"
        self.assertFalse(hasattr(control_window, "CloseDialog"),
                         "关闭行为已经固定成「最小化至托盘」，不该再有这个选择框")
        self.assertTrue(callable(control_window.ControlWindow.on_close))
        self.assertTrue(hasattr(course_review, "CourseReviewDialog"))
        self.assertTrue(callable(file_import.import_path))
        self.assertTrue(callable(file_import.import_text))
        self.assertTrue(callable(cli.main))
        parser = cli.build_parser()
        args = parser.parse_args(["--client", "--start-panel"])
        self.assertTrue(args.client and args.start_panel)
        args = parser.parse_args(["--width", "400", "--pipeline-minutes", "0.25"])
        self.assertEqual(args.width, 400)
        self.assertAlmostEqual(args.pipeline_minutes, 0.25)

    def test_browser_import_is_gone(self):
        """内置浏览器导入已按需求整体删除：类、按钮、模块都不该再出现。

        为什么钉住：它留下的可不止三个按钮——EasDialog 的 grab_set() 曾把整个客户端
        锁死（确认窗收不到点击），轮询还会叠出第二个确认窗。删掉就要删干净。
        """
        import agenda.control_window as control_window

        self.assertFalse(hasattr(control_window, "EasDialog"))
        self.assertFalse(hasattr(control_window.ControlWindow, "open_eas_dialog"))
        self.assertFalse(hasattr(control_window.ControlWindow, "reload_from_browser"))
        self.assertFalse(hasattr(control_window.ControlWindow, "import_wakeup_csv"))
        self.assertFalse((ROOT / "agenda" / "browser_import.py").exists())
        self.assertFalse((ROOT / "agenda" / "browser_import.pyc").exists())

    def test_timetable_tab_has_only_the_file_and_paste_channels(self):
        """课程表页的按钮就是这几颗：文件识别 / 粘贴 / 导出 / 撤销（+ 下面的增删改清空）。"""
        source = (ROOT / "agenda" / "control_window.py").read_text(encoding="utf-8")
        tab = source.split("def _build_timetable_tab")[1].split("def refresh_courses")[0]
        # 只看真正建按钮的那几行，注释里提到历史不算
        buttons = [
            line.split('"')[1]
            for line in tab.splitlines()
            if "self._button(" in line and '"' in line
        ]
        self.assertIn("从文件识别课表…", buttons)
        self.assertIn("粘贴课表…", buttons)
        self.assertNotIn("从教务系统导入…", buttons)
        self.assertNotIn("重新读取课表（浏览器）", buttons)
        self.assertNotIn("导入 WakeUp CSV…", buttons)

    def test_window_mode_flags(self):
        """面板图层是"打游戏不被挡"的开关，参数解析不能退化。"""
        import main as cli

        parser = cli.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.window_mode, "desktop")        # 默认就钉桌面
        self.assertFalse(args.no_click_through)
        self.assertFalse(args.always_visible)

        args = parser.parse_args(["--window-mode", "topmost"])
        self.assertEqual(args.window_mode, "topmost")
        args = parser.parse_args(["--no-click-through", "--always-visible"])
        self.assertTrue(args.no_click_through and args.always_visible)
        # 旧参数仍要能用
        args = parser.parse_args(["--no-topmost"])
        self.assertTrue(args.no_topmost)

    def test_client_config_exposes_layer_settings(self):
        from agenda.client_config import ClientConfig

        config = ClientConfig()
        self.assertEqual(config.window_mode, "desktop")
        self.assertTrue(config.desktop_only)
        # 默认**不**开鼠标穿透：开了面板就点不动，用户会以为"这不能编辑"
        self.assertFalse(config.click_through)

    def test_cli_and_config_agree_that_the_panel_is_clickable(self):
        """手工运行 `python main.py` 得到的面板必须**点得动**。

        真踩过：`cmd_run` 里写的是 `click_through=not args.no_click_through`，
        而 `--no-click-through` 默认 False，于是命令行启动的面板默认是穿透的
        —— 界面里默认明明是"不穿透"，教程第 14 节又恰好教用户手工跑这条命令，
        两边打架的结果就是用户又一次得出"这不能编辑"。
        现在两边都从 data/client.json 取值。
        """
        import main as cli
        from agenda.client_config import ClientConfig

        captured: list[dict] = []

        class FakePanel:
            def __init__(self, data_dir, **kwargs):
                captured.append(kwargs)

            def run(self):
                pass

        import agenda.panel as panel_mod

        real = panel_mod.AgendaPanel
        panel_mod.AgendaPanel = FakePanel
        try:
            with tempfile.TemporaryDirectory() as tmp:
                data = Path(tmp)
                args = cli.build_parser().parse_args(["--data-dir", str(data)])
                self.assertFalse(args.no_click_through)
                cli.cmd_run(args)
                self.assertFalse(captured[-1]["click_through"],
                                 "默认启动的面板不接受鼠标事件，用户会以为它坏了")

                # 配置文件里手改成穿透，命令行要照办
                config = ClientConfig.load(data)
                config.click_through = True
                config.save(data)
                cli.cmd_run(args)
                self.assertTrue(captured[-1]["click_through"],
                                "配置文件里的 click_through 没被采纳")

                # 显式开关仍然能强制关掉
                args = cli.build_parser().parse_args(
                    ["--data-dir", str(data), "--no-click-through"])
                cli.cmd_run(args)
                self.assertFalse(captured[-1]["click_through"])
        finally:
            panel_mod.AgendaPanel = real

    def test_school_course_url_is_registered(self):
        """青科大的课表页地址登记在目录里，导入对话框才能自动填。"""
        from agenda import eas

        school = eas.search_schools("青岛科技")[0]
        self.assertTrue(school.course_url.startswith("https://wvpn.qust.edu.cn/http/"))
        self.assertIn("xskbcx", school.course_url)
        self.assertEqual(school.base_url, "https://wvpn.qust.edu.cn")

    def test_tray_module_degrades_on_non_windows(self):
        """平台不支持时托盘应返回 False 而不是抛异常（服务端/CI 也能跑）。"""
        import agenda.tray as tray

        if sys.platform != "win32":
            icon = tray.TrayIcon("x", lambda _c: None)
            self.assertFalse(icon.start())


if __name__ == "__main__":
    unittest.main(verbosity=2)
