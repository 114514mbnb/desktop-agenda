"""客户端控制器：把面板、设置、导入、托盘串起来。

线程模型（刻意简单）：
  * 面板、控制台各自跑在**自己的进程**里（panel.py 已支持独立启动）
    → 关掉控制台不会带走面板，关掉面板也不会带走控制台
  * 控制器只负责"起进程 / 停进程 / 读设置 / 落盘"
这个设计的代价是面板状态不走内存共享，全靠 data 目录里的文件；
好处是任何一个窗口崩了都不会连带另一个，也不会出现 tkinter 跨线程的坑。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .client_config import ClientConfig
from .pipeline import Pipeline

LOCK_NAME = "panel.lock"
#: 想让**已经在跑的**客户端把控制台窗口显示出来时，往这个文件写一下即可。
#: 为什么需要：控制台可以"最小化到托盘"（窗口被隐藏但进程还在），
#: 这时再去双击桌面快捷方式，单实例保护只会安静退出——用户看到的就是
#: "客户端怎么打不开了？"（真踩过）。有了这个请求文件，重新启动就等于"把窗口叫回来"。
SHOW_REQUEST = "client.show"
#: 请已经在跑的客户端整体退出（不用去托盘里翻菜单）
QUIT_REQUEST = "client.quit"
#: 请已经在跑的客户端把窗口显示出来**并切到设置页**（面板右下角那个小齿轮用它）
SETTINGS_REQUEST = "client.settings"
#: 请已经在跑的**面板**把自己显示出来（收起过、或被全屏应用挡住时用）。
#: 桌面快捷方式走这条：双击快捷方式只负责"把日程表叫到眼前"，绝不叫控制台。
SHOW_PANEL_REQUEST = "panel.show"


class AppController:
    """面板进程的启停 + 通知录入 + 课表落盘。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.config = ClientConfig.load(self.data_dir)
        self.pipeline = Pipeline(self.data_dir)
        self.python = sys.executable
        self.project_root = Path(__file__).resolve().parent.parent

    # -- 面板进程 --------------------------------------------------------
    def request_show(self) -> bool:
        """请已经在跑的客户端显示控制台窗口。返回 False 表示没人接收（可以自己启动）。"""
        return self._write_request(SHOW_REQUEST)

    def request_settings(self) -> bool:
        """请已经在跑的客户端显示窗口并切到「设置」页（面板右下角齿轮用）。"""
        return self._write_request(SETTINGS_REQUEST)

    def request_show_panel(self) -> bool:
        """请已经在跑的**面板**把自己显示出来（它每 700ms 巡检 `panel.show`）。

        为什么写文件而不是给面板发消息：面板是独立进程，图省事又可靠的办法就是
        留一个请求文件（和 `panel.stop` 同一套机制，那条已经跑得很稳）。
        """
        path = self.data_dir / SHOW_PANEL_REQUEST
        try:
            path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            return False
        return True

    def _write_request(self, name: str) -> bool:
        pid = self.client_pid()
        if pid is None:
            return False
        try:
            (self.data_dir / name).write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            return False
        return True

    def client_pid(self) -> int | None:
        """客户端进程 pid（client.lock 里那个）。"""
        lock = self.data_dir / "client.lock"
        if not lock.exists():
            return None
        try:
            pid = int(lock.read_text(encoding="utf-8-sig").strip().strip("\ufeff") or "0")
        except (ValueError, OSError):
            return None
        if pid <= 0:
            return None
        return pid if _alive(pid) else None

    def panel_pid(self) -> int | None:
        lock = self.data_dir / LOCK_NAME
        if not lock.exists():
            return None
        try:
            pid = int(lock.read_text(encoding="utf-8-sig").strip().strip("\ufeff") or "0")
        except (ValueError, OSError):
            return None
        if pid <= 0:
            return None
        return pid if _alive(pid) else None

    def panel_running(self) -> bool:
        return self.panel_pid() is not None

    def start_panel(self, *, autostart_pipeline: bool = True) -> bool:
        """启动面板进程；已在跑则返回 False。"""
        if self.panel_running():
            return False
        try:
            lock = self.data_dir / LOCK_NAME
            if lock.exists():
                lock.unlink()
        except OSError:
            pass
        command = [
            self.python, "-B", str(self.project_root / "main.py"),
            "--data-dir", str(self.data_dir),
            "--width", str(self.config.panel_width),
            "--pipeline-minutes", str(self.config.pipeline_minutes),
            "--window-mode", str(self.config.window_mode),
        ]
        if self.config.window_mode == "desktop" and not self.config.click_through:
            command.append("--no-click-through")
        if not self.config.desktop_only:
            command.append("--always-visible")
        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(command, cwd=str(self.project_root), creationflags=creation)
        for _ in range(40):
            time.sleep(0.15)
            if self.panel_running():
                return True
        return False

    def start_console(self) -> bool:
        """把客户端（托盘 + 控制台）拉起来；已经在跑就只请它显示窗口。

        什么时候会走到这里：面板是单独启动的（`python main.py` 只开面板），
        这时没有客户端进程，`request_show()` 没人接——但用户点的就是
        「打开客户端窗口」，所以直接把客户端起起来，而不是回一句"叫不出来"。
        `--hide-panel` 是必须的：面板已经在跑了，再让客户端拉一个就重复了。
        """
        if self.request_show():
            return True
        command = [
            self.python, "-B", str(self.project_root / "main.py"),
            "--data-dir", str(self.data_dir),
            "--client", "--hide-panel",
        ]
        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(command, cwd=str(self.project_root), creationflags=creation)
        for _ in range(30):
            time.sleep(0.15)
            if self.client_pid() is not None:
                return True
        return False

    def stop_panel(self) -> bool:
        """请面板自己退出（写停止标记），退不掉再强杀。"""
        pid = self.panel_pid()
        if pid is None:
            return False
        flag = self.data_dir / "panel.stop"
        try:
            flag.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
        for _ in range(20):
            time.sleep(0.1)
            if not _alive(pid):
                return True
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               capture_output=True)
            else:
                os.kill(pid, 15)
        except Exception:
            return False
        for _ in range(20):
            time.sleep(0.1)
            if not _alive(pid):
                return True
        return False

    def toggle_panel(self) -> bool:
        """开→关，关→开；返回切换后是否在运行。"""
        if self.panel_running():
            self.stop_panel()
            self.config.panel_visible = False
        else:
            self.config.panel_visible = True
            self.start_panel()
        self.save()
        return self.panel_running()

    def restart_panel(self) -> None:
        if self.panel_running():
            self.stop_panel()
        self.start_panel()

    # -- 设置 ------------------------------------------------------------
    def save(self) -> None:
        self.config.save(self.data_dir)

    # -- 通知录入 --------------------------------------------------------
    def add_notice(self, text: str, *, group: str | None = None, source: str = "客户端录入"):
        """把粘贴的通知并入日程；返回本次跑批报告。"""
        return self.pipeline.ingest_text(text, group=group, source_label=source)

    def refresh_events(self) -> list:
        return self.pipeline.load_events()

    def run_inbox(self):
        """把 data/inbox 里的文件也整合一遍。"""
        return self.pipeline.run()

    # -- 课表 ------------------------------------------------------------
    def timetable_path(self) -> Path:
        return self.data_dir / "timetable.json"

    def load_timetable_payload(self) -> dict:
        path = self.timetable_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_timetable_payload(self, payload: dict) -> Path:
        from .eas.importer import save_timetable
        return save_timetable(self.data_dir, payload)

    def export_wakeup_csv(self) -> str:
        """把当前课表导成 WakeUp 官方模板 CSV，方便在手机上导同一份课表。"""
        from .eas import format_weeks
        payload = self.load_timetable_payload()
        courses = payload.get("courses") or []
        lines = ["课程名称,星期,开始节数,结束节数,老师,地点,周数"]
        for course in courses:
            if not isinstance(course, dict):
                continue
            name = str(course.get("name") or "").strip()
            if not name:
                continue
            weekday = course.get("weekday", "")
            if isinstance(weekday, int):
                weekday = weekday + 1
            period = str(course.get("period") or "1")
            numbers = [n for n in __import__("re").findall(r"\d+", period)]
            start = numbers[0] if numbers else "1"
            end = numbers[1] if len(numbers) > 1 else start
            weeks_raw = course.get("weeks")
            weeks = "1-16"
            if isinstance(weeks_raw, (list, tuple)) and weeks_raw:
                weeks = format_weeks([int(w) for w in weeks_raw])
            elif isinstance(weeks_raw, str) and weeks_raw.strip():
                weeks = weeks_raw.strip()
            teacher = str(course.get("teacher") or "无") or "无"
            location = str(course.get("location") or "无") or "无"
            lines.append(f"{name},{weekday},{start},{end},{teacher},{location},{weeks}")
        return "\n".join(lines) + "\n"


def _alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)
