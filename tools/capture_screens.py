"""生成 README 头图（`screenshot-client.png` / `screenshot-panel.png`）。

图片里**必须只有虚构数据**：这两张图是仓库门面，以前是拿本人的真实课表截的
（课程、教师姓名、教室号、甚至状态栏里的本机路径都在图里）。
现在数据一律来自 `tools/demo_data.py`，工具本身不读 `data/`。

用法（需要 Pillow；用自带 runtime 之外的 Python 跑）：
    python tools/capture_screens.py
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import demo_data  # noqa: E402

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

PANEL_TITLE = "今日日程"
CONSOLE_TITLE = "桌面日程 · 控制台"


def find_window(title: str):
    from ctypes import wintypes

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    found: list[tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):  # type: ignore[no-untyped-def]
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


def grab(rect, target: Path, pad: int = 4) -> None:
    from PIL import ImageGrab

    x, y, width, height = rect
    ImageGrab.grab(bbox=(x - pad, y - pad, x + width + pad, y + height + pad),
                   all_screens=True).save(target)
    print(f"  → {target.name}（{width}x{height}）")


def clean_monday() -> object:
    """挑一个"没有节日气氛的周一"当面板头图的日期。

    为什么不用今天：演示课表只排工作日，撞上周末就是一张空面板；
    也不能挑过去的日期 —— "下一项"是按**真实此刻**算的，过去的日期只会显示"暂无后续安排"。
    为什么要跳过节日窗口：节日彩蛋按"节日当天 ±3 天"触发，9 月底随便挑一天都会顶着中秋横幅
    （节日效果另有 `docs/festival-shots/` 专门展示，头图要的是干净的主体）。
    """
    import datetime as dt

    from agenda.festival import hint_for
    from agenda.holidays import Calendar

    today = dt.date.today()
    day = today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)
    for _ in range(8):
        if hint_for(day, Calendar()) is None:
            break
        day += dt.timedelta(days=7)
    return day


def capture_panel(work: Path, day) -> None:
    """面板头图：用一个临时的、置顶的面板实例截图（不影响正在运行的那个）。"""
    from agenda.panel import AgendaPanel

    panel = AgendaPanel(work, pipeline_ms=0, autostart_pipeline=False,
                        window_mode="topmost", position=(120, 60))
    panel.root.attributes("-topmost", True)
    panel.today_override = day
    try:
        panel.refresh()
        for _ in range(10):
            panel.root.update()
            time.sleep(0.05)
        rect = find_window(PANEL_TITLE)
        if rect is None:
            print("  面板窗口没找到，跳过")
            return
        grab(rect, ROOT / "screenshot-panel.png")
    finally:
        panel._destroy_festival()
        panel.root.destroy()


def capture_console(work: Path) -> None:
    """控制台头图：通知页 + 今日与未来一周（演示数据）。

    这里用**真的** `AppController`（只是把数据目录指向演示目录）：
    自己搭 `SimpleNamespace` 的话，抬头那行"今日 N 条通知"会跟下面表格对不上。
    """
    from agenda.client_app import AppController
    from agenda.control_window import ControlWindow

    controller = AppController(work)
    controller.config.panel_visible = False
    window = ControlWindow(controller)
    try:
        window.root.geometry("1000x700+140+80")
        window.root.attributes("-topmost", True)
        window.refresh_all()
        window.root.lift()
        for _ in range(12):
            window.root.update()
            time.sleep(0.05)
        rect = find_window(CONSOLE_TITLE)
        if rect is None:
            print("  控制台窗口没找到，跳过")
            return
        grab(rect, ROOT / "screenshot-client.png", pad=0)
    finally:
        window.stop_tick()
        window.root.destroy()


def main() -> int:
    # 面板与控制台各用一份演示数据：面板被固定在"下一个干净的周一"，
    # 通知要按那一天往后排，否则头图里一条通知也没有。
    day = clean_monday()
    panel_work = demo_data.seed_dir(day, holidays=False)
    print(f"面板演示数据：{panel_work}（面板日期 {day}）")
    try:
        capture_panel(panel_work, day)
    finally:
        demo_data.cleanup(panel_work)

    console_work = demo_data.seed_dir(holidays=False)   # 控制台看的是真实今天
    print(f"控制台演示数据：{console_work}")
    try:
        capture_console(console_work)
    finally:
        demo_data.cleanup(console_work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
