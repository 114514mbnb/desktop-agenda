"""启动入口：开机自启、`--panel`、以及桌面快捷方式的"永远只出面板"。

用户把规则说死了（原话，两次）：

  「没有我的命令，不准你自行上传！……我双击快捷方式控制台一样会出现。
    控制台的呼出方式只能是 1. 打开日程表后再次点击桌面快捷方式
    2. 点击日程表右下角齿轮 3. 点击桌面右下角状态栏。
    也就是说，在第一层双击时能且只能呼出日程表！」

  「我退出重新打开，依旧是同时打开控制台以及日程版，我要求的是只打开日程表。
    你可以把控制台理解为日程表的下级管理，不是平级的。」

第二次把第一次的"再点一次快捷方式可以出控制台"也否掉了 —— 双击快捷方式
**永远只出日程表**。所以这里钉三件事：

1. 开机自启动**只拉面板**（快捷方式 → `tools\\launch-panel.vbs` → `main.py --panel`）；
2. `main.py --panel` 只拉面板，不开控制台、不进托盘；
3. 桌面快捷方式入口（`--shortcut`）**任何情况下都不许开控制台**：
   面板没在跑就拉面板，已在跑就只请它显示出来。
   控制台的入口只剩两个：面板右下角齿轮、任务栏托盘图标。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenda import autostart  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class AutostartTests(unittest.TestCase):
    """开机自启动：只开面板，且这条不给用户改。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "Startup"
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_by_default(self):
        self.assertFalse(autostart.is_enabled(self.folder))

    @unittest.skipUnless(sys.platform == "win32", "开机自启动是 Windows 的事")
    def test_enable_then_disable_round_trip(self):
        ok, message = autostart.enable(ROOT, folder=self.folder)
        self.assertTrue(ok, message)
        self.assertTrue(autostart.is_enabled(self.folder))
        self.assertTrue(autostart.shortcut_path(self.folder).is_file())

        ok, message = autostart.disable(folder=self.folder)
        self.assertTrue(ok, message)
        self.assertFalse(autostart.is_enabled(self.folder))

    def test_the_shortcut_only_starts_the_panel(self):
        """**用户把话说死了**：自启动仅打开日程表，不许带控制台。

        这里断言的是将要执行的 PowerShell 脚本：它必须指向 launch-panel.vbs，
        而且不能出现 --client / start-client 之类会把控制台一起拉起来的写法。
        """
        script = autostart.shortcut_script(Path(r"C:\X\Desktop-Agenda.lnk"),
                                           autostart.launcher_path(ROOT), ROOT)
        self.assertIn("launch-panel.vbs", script)
        self.assertIn("wscript.exe", script)
        for forbidden in ("--client", "start-client", "main.py"):
            self.assertNotIn(forbidden, script, f"自启动里混进了「{forbidden}」")

    def test_the_launcher_script_starts_the_panel_only(self):
        """启动器本体也得是"只开面板"：它以前跑的是 --client，会弹控制台。

        只看真正的命令行那一行 —— 注释里解释历史时会提到 `--client`，那不是命令。
        """
        body = (ROOT / "tools" / "launch-panel.vbs").read_text(encoding="utf-8")
        command = [line for line in body.splitlines()
                   if line.strip().startswith("cmd =") or line.strip().startswith("cmd=")]
        self.assertTrue(command, "launch-panel.vbs 里找不到命令行")
        line = " ".join(command)
        self.assertIn("--panel", line)
        self.assertNotIn("--client", line, f"启动器又去拉控制台了：{line}")


class ShortcutEntryTests(unittest.TestCase):
    """桌面快捷方式：第一层双击只出面板，第二层才叫控制台。"""

    def _patch(self, *, running: bool):
        """换掉 AppController 与 cmd_run / cmd_client，收集"到底干了什么"。

        面板改成了**在快捷方式自己的进程里**跑（`cmd_run`），不再 fork 一个 python
        —— 用户反馈「初始化开启太慢」，少一次解释器启动就少 200~400 ms。
        所以这里盯的是"有没有走面板这条路、有没有碰控制台"。
        """
        import main as cli

        import agenda.client_app as client_app

        runs: list[dict] = []
        consoles: list[dict] = []
        shows: list[str] = []

        class FakeController:
            def __init__(self, data_dir):
                self.data_dir = data_dir

            def panel_running(self):
                return running

            def start_panel(self, **_kwargs):
                runs.append({"via": "start_panel"})
                return True

            def request_show_panel(self):
                shows.append("show")
                return True

        real_controller = client_app.AppController
        real_run = cli.cmd_run
        real_client = cli.cmd_client
        client_app.AppController = FakeController

        def fake_run(args):
            runs.append({"via": "cmd_run", "width": args.width,
                         "window_mode": args.window_mode,
                         "front": getattr(args, "front", False)})
            return 0

        def fake_client(args):
            consoles.append({"client": getattr(args, "client", None),
                             "start_panel": getattr(args, "start_panel", None),
                             "hide_panel": getattr(args, "hide_panel", None)})
            return 0

        cli.cmd_run = fake_run
        cli.cmd_client = fake_client
        return cli, client_app, real_controller, real_run, real_client, runs, consoles, shows

    def _restore(self, cli, client_app, real_controller, real_run, real_client):
        client_app.AppController = real_controller
        cli.cmd_run = real_run
        cli.cmd_client = real_client

    def test_the_first_launch_opens_only_the_panel(self):
        """**第一层双击能且只能呼出日程表** —— 面板没在跑时一次都不许碰控制台。"""
        (cli, client_app, real_controller, real_run, real_client,
         runs, consoles, _shows) = self._patch(running=False)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = cli.build_parser().parse_args(["--shortcut", "--data-dir", tmp])
                code = cli.cmd_shortcut(args)
        finally:
            self._restore(cli, client_app, real_controller, real_run, real_client)
        self.assertEqual(code, 0)
        self.assertEqual([item["via"] for item in runs], ["cmd_run"],
                         "第一次双击应当在本进程里把面板跑起来")
        self.assertTrue(runs[0].get("front"),
                        "用户点是点出来了，但面板是桌面挂件、被浏览器盖住 = 看起来没反应")
        self.assertEqual(consoles, [], "第一次双击绝对不许开控制台")

    def test_the_shortcut_honours_the_saved_panel_settings(self):
        """进程内跑面板也不能忽略用户设置过的宽度/图层（原来那段拼命令行的活）。"""
        from agenda.client_config import ClientConfig

        (cli, client_app, real_controller, real_run, real_client,
         runs, _consoles, _shows) = self._patch(running=False)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config = ClientConfig.load(Path(tmp))
                config.panel_width = 411
                config.window_mode = "topmost"
                config.save(Path(tmp))
                args = cli.build_parser().parse_args(["--shortcut", "--data-dir", tmp])
                cli.cmd_shortcut(args)
        finally:
            self._restore(cli, client_app, real_controller, real_run, real_client)
        self.assertEqual(runs[0]["width"], 411, "面板宽度没照 client.json 走")
        self.assertEqual(runs[0]["window_mode"], "topmost", "面板图层没照 client.json 走")

    def test_a_second_launch_still_never_opens_the_console(self):
        """面板已经在跑时再双击：**只请面板显示出来**，仍然不许碰控制台。

        用户的原话（第二次纠正）：
          「我退出重新打开，依旧是同时打开控制台以及日程版，我要求的是只打开日程表。
            你可以把控制台理解为日程表的下级管理，不是平级的。」
        """
        (cli, client_app, real_controller, real_run, real_client,
         runs, consoles, shows) = self._patch(running=True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = cli.build_parser().parse_args(["--shortcut", "--data-dir", tmp])
                code = cli.cmd_shortcut(args)
        finally:
            self._restore(cli, client_app, real_controller, real_run, real_client)
        self.assertEqual(code, 0)
        self.assertEqual(runs, [], "面板已在跑时不该再拉一个")
        self.assertEqual(shows, ["show"], "应当请已经在跑的面板显示出来")
        self.assertEqual(consoles, [], "快捷方式永远不许开控制台")

    def test_cmd_panel_starts_the_panel_in_process(self):
        """`--panel`（开机自启动走这条）也只在本进程里跑面板。"""
        (cli, client_app, real_controller, real_run, real_client,
         runs, consoles, _shows) = self._patch(running=False)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = cli.build_parser().parse_args(["--panel", "--data-dir", tmp])
                code = cli.cmd_panel(args)
        finally:
            self._restore(cli, client_app, real_controller, real_run, real_client)
        self.assertEqual(code, 0)
        self.assertEqual([item["via"] for item in runs], ["cmd_run"])
        self.assertEqual(consoles, [], "开机自启动永远不许开控制台")

    def test_start_failing_to_run_is_reported(self):
        """`cmd_run` 起不来时不能假装成功。"""
        import main as cli

        import agenda.client_app as client_app

        class FakeController:
            def __init__(self, data_dir):
                self.data_dir = data_dir

            def panel_running(self):
                return False

        real_controller = client_app.AppController
        real_run = cli.cmd_run
        client_app.AppController = FakeController
        cli.cmd_run = lambda args: 1
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = cli.build_parser().parse_args(["--panel", "--data-dir", tmp])
                code = cli.cmd_panel(args)
        finally:
            client_app.AppController = real_controller
            cli.cmd_run = real_run
        self.assertEqual(code, 1, "起不来就该返回非零")

    def test_main_has_the_shortcut_entry(self):
        import main as cli

        args = cli.build_parser().parse_args(["--shortcut"])
        self.assertTrue(args.shortcut)
        self.assertFalse(args.client, "--shortcut 不该顺带把 --client 也点亮")

    def test_main_has_a_panel_only_entry(self):
        import main as cli

        args = cli.build_parser().parse_args(["--panel"])
        self.assertTrue(args.panel)
        self.assertFalse(args.client, "--panel 不该顺带把客户端也打开了")

    def test_cmd_panel_is_idempotent(self):
        """面板已经在跑时，`--panel` 要安静返回、不许再拉一个。"""
        import main as cli

        import agenda.client_app as client_app

        class FakeController:
            def __init__(self, data_dir):
                self.data_dir = data_dir

            def panel_running(self):
                return True

        real = client_app.AppController
        real_run = cli.cmd_run
        client_app.AppController = FakeController
        cli.cmd_run = lambda args: (_ for _ in ()).throw(
            AssertionError("面板已在跑时不该再跑一个"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = cli.build_parser().parse_args(["--panel", "--data-dir", tmp])
                code = cli.cmd_panel(args)
        finally:
            client_app.AppController = real
            cli.cmd_run = real_run
        self.assertEqual(code, 0, "面板已在跑时应当安静返回 0")

    def test_the_shortcut_scripts_point_at_the_right_entries(self):
        """两个 .cmd 入口：`start-client.cmd` 走 --shortcut，`start-panel.cmd` 走 --panel。"""
        client = (ROOT / "start-client.cmd").read_text(encoding="utf-8")
        command = " ".join(line for line in client.splitlines()
                           if line.strip().startswith("start "))
        self.assertIn("--shortcut", command)
        self.assertNotIn("--client", command,
                         f"start-client.cmd 又直接把控制台拉起来了：{command}")

        panel = (ROOT / "start-panel.cmd").read_text(encoding="utf-8")
        panel_command = " ".join(line for line in panel.splitlines()
                                 if line.strip().startswith("start "))
        self.assertIn("--panel", panel_command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
