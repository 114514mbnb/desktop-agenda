"""截图工具：按 Win32 物理坐标裁剪"今日日程"面板，供视觉自查。

用法：python tools/capture_panel.py [输出路径]
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


def find_panel(title: str = "今日日程") -> tuple[int, int, int, int] | None:
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


def main() -> int:
    from PIL import ImageGrab  # Windows 上由 Pillow 提供

    rect = find_panel()
    if rect is None:
        print("没找到正在运行的日程面板（先启动 python main.py）")
        return 1
    x, y, width, height = rect
    pad = 6
    image = ImageGrab.grab(
        bbox=(x - pad, y - pad, x + width + pad, y + height + pad), all_screens=True,
    )
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "screenshot-panel.png"
    image.save(target)
    print(f"面板物理矩形 {x},{y} {width}x{height} → 已保存 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
