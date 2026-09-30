"""开机自启动：在用户的「启动」文件夹里放一个快捷方式，只拉起**日程表面板**。

为什么只拉面板、而且**不给开关**（用户明确要求，这条写死）：
  他要的是"开机后桌面右侧那条日程表自己出现"。控制台是"要改设置时才打开"的东西，
  开机自动弹一个窗口既没必要也碍事。所以快捷方式指向 `tools\\launch-panel.vbs`，
  界面上只有"开 / 关"一个复选项，没有"要不要连控制台一起开"这种选择。

为什么用快捷方式而不是往启动文件夹里丢脚本：
  启动文件夹里的 .cmd/.bat 会闪一个黑框；.vbs 常被安全软件盯上。
  `.lnk` → wscript → `launch-panel.vbs` 是 Windows 上最不打扰人的做法，
  而且和 `tools\\install.cmd` 用的是同一套东西（两条路互认，不会各建各的）。

为什么建 .lnk 要绕一下 PowerShell：Python 标准库没有创建快捷方式的能力
（COM 的 IShellLink 要手搓虚表）。这里用 `-EncodedCommand`（base64 的 UTF-16LE）
把脚本整个传过去，彻底避开引号与中文编码的坑；`-Command` 也不受执行策略限制。
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

#: 快捷方式文件名（和 tools\install.ps1 用的一致，两条路互认）
SHORTCUT_NAME = "Desktop-Agenda.lnk"
#: 启动器：它只起面板，不起控制台
LAUNCHER_NAME = "launch-panel.vbs"
#: 快捷方式描述
DESCRIPTION = "桌面日程面板（只开面板，不开控制台）"


def startup_folder() -> Path:
    """当前用户的「启动」文件夹。"""
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path(folder: Path | None = None) -> Path:
    return (folder or startup_folder()) / SHORTCUT_NAME


def launcher_path(project_root: Path) -> Path:
    return Path(project_root) / "tools" / LAUNCHER_NAME


def is_enabled(folder: Path | None = None) -> bool:
    """开关的**真值**在文件系统上，不在配置里 —— 这样用户手删快捷方式也不会两边打架。"""
    try:
        return shortcut_path(folder).is_file()
    except OSError:
        return False


def shortcut_script(lnk: Path, vbs: Path, workdir: Path) -> str:
    """生成建快捷方式的 PowerShell 脚本（独立成函数，方便测试断言它只指向面板）。"""
    def quote(value: Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    wscript = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe"
    return (
        "$ErrorActionPreference='Stop';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + quote(lnk) + ");"
        "$s.TargetPath=" + quote(wscript) + ";"
        "$s.Arguments='\"' + " + quote(vbs) + " + '\"';"
        "$s.WorkingDirectory=" + quote(workdir) + ";"
        "$s.Description=" + quote(Path(DESCRIPTION)) + ";"
        "$s.Save()"
    )


def enable(project_root: Path, *, folder: Path | None = None) -> tuple[bool, str]:
    """开启开机自启动（只开面板）。返回 (成功?, 给用户看的话)。"""
    project_root = Path(project_root)
    if sys.platform != "win32":
        return False, "开机自启动只在 Windows 上可用"
    launcher = launcher_path(project_root)
    if not launcher.is_file():
        return False, f"缺少启动器：{launcher}"
    target_dir = folder or startup_folder()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return False, f"建不了启动文件夹：{error}"
    lnk = shortcut_path(target_dir)
    script = shortcut_script(lnk, launcher, project_root)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as error:  # noqa: BLE001
        return False, f"调用 PowerShell 失败：{error}"
    if not lnk.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()[:200]
        return False, f"快捷方式没建出来{f'：{detail}' if detail else ''}"
    return True, f"已开启：开机后会自动打开日程表面板（{lnk.name}）"


def disable(*, folder: Path | None = None) -> tuple[bool, str]:
    """关闭开机自启动（删掉那个快捷方式）。"""
    lnk = shortcut_path(folder)
    if not lnk.is_file():
        return True, "开机自启动本来就是关着的"
    try:
        lnk.unlink()
    except OSError as error:
        return False, f"删不掉快捷方式：{error}"
    return True, "已关闭：开机后不再自动打开面板"
