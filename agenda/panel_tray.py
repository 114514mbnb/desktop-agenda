"""面板自己的托盘图标：只要程序在跑（哪怕只有面板），通知区里就有它。

用户的要求（原话）：
  * 「点托盘图标/快捷方式时，把控制台窗口带到最前面，并在任务栏闪烁提醒」
  * 「托盘图标要一直在：只要程序在跑（哪怕只有面板），任务栏右下角就有它的图标，
    点它能开控制台」

为什么由**面板**来管这个图标：面板才是常驻进程（开机自启拉的就是它），
控制台是"要用时才开"的。所以这里做一次**交接**：
  * 控制台没在跑 → 面板挂自己的图标；
  * 控制台起来了 → 面板撤掉图标（控制台自己会挂一个），始终**只有一个**，
    免得通知区里并排两个一模一样的图标。
这个交接每 2 秒对一次（只读一次 `client.lock` + 一次存活性检查，开销可以忽略）。
"""

from __future__ import annotations

from . import tray as tray_mod

#: 交接检查的间隔（毫秒）
TRAY_SYNC_MS = 2000


class PanelTray:
    """面板侧的托盘图标，含与控制台的交接。"""

    def __init__(self, *, title: str, icon_path, on_action, client_running,
                 on_balloon=None):
        self.title = title
        self.icon_path = icon_path
        self.on_action = on_action              # on_action(command_id)
        self.client_running = client_running    # () -> bool
        self.on_balloon = on_balloon
        self._icon = None

    # -- 交接 ------------------------------------------------------------
    def sync(self) -> str:
        """按"控制台在不在跑"挂上或撤掉自己的图标。返回做了什么（便于测试）。"""
        running = bool(self.client_running())
        if running and self._icon is not None:
            self.remove()
            return "removed"
        if not running and self._icon is None:
            return "added" if self.add() else "failed"
        return "kept"

    def add(self) -> bool:
        if self._icon is not None:
            return True
        try:
            icon = tray_mod.TrayIcon(self.title, self._dispatch,
                                     str(self.icon_path) if self.icon_path else None)
            if not icon.start():
                return False
        except Exception:                                # noqa: BLE001
            return False
        self._icon = icon
        return True

    def remove(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:                                # noqa: BLE001
            pass
        self._icon = None

    @property
    def active(self) -> bool:
        return self._icon is not None

    # -- 事件 ------------------------------------------------------------
    def _dispatch(self, command: int) -> None:
        """托盘线程里调过来的：**只转发**，真正的活由主线程干。

        为什么不能直接做事：Tk 只能从主线程碰。这里调用方（`panel.py`）会把
        动作排到 `root.after(0, ...)` 上，所以这里只回调一次就完事。
        """
        try:
            self.on_action(command)
        except Exception:                                # noqa: BLE001
            pass

    def balloon(self, title: str, message: str, *, warning: bool = False) -> bool:
        """气泡提示（"点了没反应"时给个可见回执）。"""
        if self._icon is None:
            return False
        try:
            return bool(self._icon.notify(title, message, warning=warning))
        except Exception:                                # noqa: BLE001
            return False
