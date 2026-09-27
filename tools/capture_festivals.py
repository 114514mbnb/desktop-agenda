"""节日彩蛋 / 假期 / 调休 的截图自查工具。

用法：python tools/capture_festivals.py [输出目录]

产物：
  * `festival-<key>-<日期>.png`      每个节日当天的面板（含彩蛋横幅）
  * `festival-<key>-<日期>-egg.png`  点开彩蛋之后
  * `holiday-<日期>.png`             假期当天：课表压掉、群通知还在
  * `makeup-<日期>.png`              调休当天：补课 + 日期旁的小字
  * `console-*.png`                  控制台「假期 / 通知 / 设置」三页

**数据全部来自 `tools/demo_data.py`（虚构课表 + 虚构通知）**，不再从 `data/` 复制：
原来这里是 `shutil.copy(data/timetable.json)`，于是每一张公开截图都带着本人的
真实课程、教师姓名和教室号。截图工具永远不该读用户的真实数据。
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from ctypes import wintypes
from datetime import date as Date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agenda.festival import festival_occurrences  # noqa: E402
from tools import demo_data  # noqa: E402

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

#: 假期里也要显示的那条群通知（放假期间"不排课但通知照旧"）。日期落在国庆假期内。
DEMO_EVENT = {
    "schemaVersion": 1,
    "events": [
        {
            "id": "demo-1",
            "title": "思想动态调研：请各班班长收齐后提交",
            "date": "2026-10-02",
            "start": "12:00",
            "end": "13:00",
            "people": ["班长"],
            "notes": "假期里发的通知，照样要显示",
            "group": "2026级本科生年级群",
            "tentative": False,
        }
    ],
}


def seed_dir(_base: Path | None = None) -> Path:
    """建一个只装演示数据的临时目录（课表 + 通知 + 假期日历都是编的）。

    参数留着只是为了不改调用点；**故意忽略它** —— 以前这里会把 `data/timetable.json`
    拷进来，结果所有公开截图都带着本人的真实课表。
    """
    work = demo_data.seed_dir()
    (work / "events.json").write_text(json.dumps(DEMO_EVENT, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    return work


def find_panel(title: str = "今日日程"):
    found: list[tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == title and user32.IsWindowVisible(hwnd):
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                found.append((rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
                return False
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def grab(panel, target: Path, *, reveal_egg: bool = False) -> bool:
    from PIL import ImageGrab

    panel.root.update()
    panel.root.update_idletasks()
    if reveal_egg and panel._festival_banner is not None:
        panel._festival_banner.toggle_egg()
        for _ in range(8):
            panel.root.update()
            time.sleep(0.05)
    panel.root.update_idletasks()
    rect = find_panel()
    if rect is None:
        return False
    x, y, width, height = rect
    pad = 4
    ImageGrab.grab(bbox=(x - pad, y - pad, x + width + pad, y + height + pad),
                   all_screens=True).save(target)
    return True


def set_today(panel, day: Date) -> None:
    panel.today_override = day
    panel.refresh()
    for _ in range(4):
        panel.root.update()
        time.sleep(0.05)


def capture_console(work: Path, out_dir: Path) -> bool:
    """控制台「假期」页的效果图（假期表 + 状态列 + 调休表）。"""
    from types import SimpleNamespace

    from PIL import ImageGrab

    from agenda.client_config import ClientConfig
    from agenda.control_window import ControlWindow

    config = ClientConfig.load(work)
    config.panel_visible = False
    controller = SimpleNamespace(
        data_dir=work, config=config,
        load_timetable_payload=lambda: json.loads(
            (work / "timetable.json").read_text(encoding="utf-8")),
        save_timetable_payload=lambda payload: None,
        restart_panel=lambda: None,
        refresh_events=lambda: [],
        panel_running=lambda: False, panel_pid=lambda: None, save=lambda: None,
    )
    window = ControlWindow(controller)
    try:
        window.root.geometry("1000x700+140+80")
        window.root.attributes("-topmost", True)
        window.refresh_all()
        index = [window.notebook.tab(tab, "text") for tab in window.notebook.tabs()].index("假期")
        window.notebook.select(index)
        window.root.lift()
        for _ in range(12):
            window.root.update()
            time.sleep(0.05)
        rect = find_panel("桌面日程 · 控制台")
        if rect is None:
            return False
        x, y, w, h = rect
        # GetWindowRect 拿到的**已经包含**标题栏和边框，所以不要再往上减 28px：
        # 那会连带把窗口背后的桌面/浏览器截进图里（以前的图左上角就有一条别家的界面）。
        bbox = (x, y, x + w, y + h)
        ImageGrab.grab(bbox=bbox, all_screens=True).save(out_dir / "console-holiday-tab.png")
        # 通知页：空状态长文案要折行显示（以前被单元格硬切）
        index = [window.notebook.tab(tab, "text") for tab in window.notebook.tabs()].index("通知")
        window.notebook.select(index)
        for _ in range(6):
            window.root.update()
            time.sleep(0.05)
        ImageGrab.grab(bbox=bbox, all_screens=True).save(out_dir / "console-notice-tab.png")
        # 设置页：界面精简后的样子（只剩启动显示 / 到点弹窗 / 待机模式二选一）
        index = [window.notebook.tab(tab, "text") for tab in window.notebook.tabs()].index("设置")
        window.notebook.select(index)
        for _ in range(6):
            window.root.update()
            time.sleep(0.05)
        ImageGrab.grab(bbox=bbox, all_screens=True).save(out_dir / "console-settings-tab.png")
        return True
    finally:
        window.stop_tick()
        window.root.destroy()


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "festival-shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = seed_dir()          # 只用虚构数据（见 tools/demo_data.py）

    from agenda.panel import AgendaPanel

    panel = AgendaPanel(work, pipeline_ms=0, autostart_pipeline=False,
                        window_mode="topmost", position=(120, 60))
    panel.root.attributes("-topmost", True)
    panel.root.update()
    saved = 0
    try:
        year = Date.today().year
        for festival in festival_occurrences(year):
            if festival.day is None:
                continue
            set_today(panel, festival.day)
            stamp = festival.day.strftime("%Y%m%d")
            if grab(panel, out_dir / f"festival-{festival.key}-{stamp}.png"):
                saved += 1
                print(f"saved festival-{festival.key}-{stamp}.png")
            if grab(panel, out_dir / f"festival-{festival.key}-{stamp}-egg.png", reveal_egg=True):
                print(f"saved festival-{festival.key}-{stamp}-egg.png")
            if panel._festival_banner is not None:
                panel._festival_banner.toggle_egg()
                panel.root.update()
        # 假期中：课表压掉、通知还在
        set_today(panel, Date(2026, 10, 2))
        if grab(panel, out_dir / "holiday-20261002.png"):
            saved += 1
            print("saved holiday-20261002.png")
        # 调休：补 10 月 7 日的课，日期旁边有小字
        set_today(panel, Date(2026, 10, 10))
        if grab(panel, out_dir / "makeup-20261010.png"):
            saved += 1
            print("saved makeup-20261010.png")
        # 节前窗口（范围扩大后的效果：还没到节日就有气氛，并写明还有几天）
        set_today(panel, Date(2026, 9, 23))
        if grab(panel, out_dir / "window-before-20260923.png"):
            saved += 1
            print("saved window-before-20260923.png")
        # 假期收尾期：按天数的收心倒计时
        set_today(panel, Date(2026, 10, 9))
        if grab(panel, out_dir / "wrapup-20261009.png"):
            saved += 1
            print("saved wrapup-20261009.png")
    finally:
        panel._destroy_festival()
        panel.root.destroy()

    try:
        if capture_console(work, out_dir):
            saved += 1
            print("saved console-holiday-tab.png")
    except Exception as error:  # noqa: BLE001
        print(f"控制台截图跳过：{error}")
    finally:
        demo_data.cleanup(work)
    print(f"共 {saved} 张（不含每个节日的 -egg 彩蛋截图）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
