"""生成 `docs/images/` 里的教程配图（**零第三方依赖**）。

为什么不复用 `tools/capture_screens.py`：那条路要 Pillow，而 Pillow 不在随程序发布的
运行时里 —— 教程配图要跟着仓库走，谁 clone 下来都应该能重拍。所以这里自己来：

  * 截图：`PrintWindow`（窗口自绘，被别的窗口遮住也拍得到）／`BitBlt`（菜单这种置顶小窗）
  * 标注：GDI 的画笔与字体，直接在内存位图上画框、箭头、编号气泡
  * 定位：标注框按**控件的真实坐标**算（`winfo_rootx/rooty` 减去窗口矩形），
    不用手写像素 —— 版面一改，手写坐标就全错位了
  * 留白：图右侧自动接一块深色底，说明文字放那儿，不遮住界面本身
  * 存盘：`agenda.backdrop` 的 PNG 编码（标准库 zlib）

图片内容一律来自 `tools/demo_data.py` 的**虚构数据**，不读 `data/`。

用法：
    runtime\\python.exe tools\\capture_tutorial.py
"""

from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agenda import backdrop  # noqa: E402
from agenda.backdrop import Picture  # noqa: E402
from tools import demo_data  # noqa: E402

OUT_DIR = ROOT / "docs" / "images"

#: 标注配色：红框箭头 + 白字深底，在深色面板和浅色控制台上都看得清
RED = "#FF4D4F"
BLUE = "#4C8DFF"
DARK = "#12171F"
WHITE = "#FFFFFF"

#: 右侧说明栏宽度
MARGIN = 320

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:                                       # noqa: BLE001
    user32.SetProcessDPIAware()

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wintypes.HWND
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_uint]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                   ctypes.c_uint]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
gdi32.CreatePen.restype = wintypes.HPEN
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
for _name in ("Rectangle", "Ellipse"):
    getattr(gdi32, _name).argtypes = [wintypes.HDC] + [ctypes.c_int] * 4
gdi32.RoundRect.argtypes = [wintypes.HDC] + [ctypes.c_int] * 6
gdi32.MoveToEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.LineTo.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.Polygon.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.c_int]
gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR,
                           ctypes.c_int]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetBkColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [wintypes.LPCWSTR]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                        ctypes.POINTER(wintypes.SIZE)]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]

BI_RGB, DIB_RGB_COLORS, TRANSPARENT, OPAQUE, SRCCOPY, PS_SOLID = 0, 0, 1, 2, 0x00CC0020, 0
NULL_BRUSH = 5


class _Header(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _Info(ctypes.Structure):
    _fields_ = [("bmiHeader", _Header), ("bmiColors", wintypes.DWORD * 3)]


def _ref(color: str) -> int:
    value = color.lstrip("#")
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return (blue << 16) | (green << 8) | red


class Shot:
    """一张位图 + 一支能往上画标注的 GDI 画笔。"""

    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self._dc = None
        self._bitmap = None
        self._bits = None
        self._objects: list[int] = []

    @classmethod
    def _blank(cls, width: int, height: int) -> "Shot":
        shot = cls()
        shot.width, shot.height = max(1, int(width)), max(1, int(height))
        info = _Info()
        info.bmiHeader.biSize = ctypes.sizeof(_Header)
        info.bmiHeader.biWidth = shot.width
        info.bmiHeader.biHeight = -shot.height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        shot._bitmap = gdi32.CreateDIBSection(None, ctypes.byref(info), DIB_RGB_COLORS,
                                              ctypes.byref(bits), None, 0)
        if not shot._bitmap:
            raise OSError("CreateDIBSection 失败")
        shot._bits = bits
        shot._dc = gdi32.CreateCompatibleDC(None)
        gdi32.SelectObject(shot._dc, shot._bitmap)
        return shot

    @classmethod
    def of_window(cls, hwnd: int) -> "Shot":
        rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        shot = cls._blank(rect.right - rect.left, rect.bottom - rect.top)
        user32.PrintWindow(wintypes.HWND(hwnd), shot._dc, 2)
        return shot

    @classmethod
    def of_screen(cls, x: int, y: int, width: int, height: int) -> "Shot":
        shot = cls._blank(width, height)
        screen = user32.GetDC(None)
        gdi32.BitBlt(shot._dc, 0, 0, width, height, screen, int(x), int(y), SRCCOPY)
        user32.ReleaseDC(None, screen)
        return shot

    @classmethod
    def from_pixels(cls, width: int, height: int, pixels: bytes) -> "Shot":
        shot = cls._blank(width, height)
        ctypes.memmove(shot._bits, pixels, len(pixels))
        return shot

    # -- 变换 ------------------------------------------------------------
    def pixels(self) -> bytes:
        return ctypes.string_at(self._bits, self.width * self.height * 4)

    def picture(self) -> Picture:
        return Picture(width=self.width, height=self.height, pixels=self.pixels(),
                       stride=self.width * 4)

    def with_margin(self, *, right: int = 0, bottom: int = 0, top: int = 0) -> "Shot":
        """右边／下面接一块深色底：说明文字放那儿，不遮住界面。"""
        out = Shot._blank(self.width + right, self.height + top + bottom)
        brush = out._brush(DARK)
        old = gdi32.SelectObject(out._dc, brush)
        gdi32.Rectangle(out._dc, 0, 0, out.width, out.height)
        gdi32.SelectObject(out._dc, old)
        gdi32.BitBlt(out._dc, 0, top, self.width, self.height, self._dc, 0, 0, SRCCOPY)
        return out

    def crop(self, x: int, y: int, width: int, height: int) -> "Shot":
        source = self.pixels()
        x, y = max(0, int(x)), max(0, int(y))
        width = max(1, min(int(width), self.width - x))
        height = max(1, min(int(height), self.height - y))
        out = bytearray(width * height * 4)
        for row in range(height):
            start = ((y + row) * self.width + x) * 4
            out[row * width * 4:(row + 1) * width * 4] = source[start:start + width * 4]
        return Shot.from_pixels(width, height, bytes(out))

    def zoom(self, factor: int) -> "Shot":
        """整倍数最近邻放大（局部细节看不清时用）。"""
        source = self.pixels()
        factor = max(1, int(factor))
        width, height = self.width * factor, self.height * factor
        out = bytearray(width * height * 4)
        for row in range(height):
            source_row = (row // factor) * self.width
            # ⚠ 必须 ×4：`out` 是**字节**数组，而 self.width 是像素数。
            # 第一版漏了 ×4，放大结果整片错位（齿轮特写拍出来是一片空白），
            # 而"只看第一行"的验证恰好发现不了 —— 第 0 行是对的，从第 1 行起才错。
            target_row = row * width * 4
            for column in range(width):
                offset = (source_row + column // factor) * 4
                target = target_row + column * 4
                out[target:target + 4] = source[offset:offset + 4]
        return Shot.from_pixels(width, height, bytes(out))

    # -- 画 --------------------------------------------------------------
    def _pen(self, color: str, width: int):
        pen = gdi32.CreatePen(PS_SOLID, int(width), _ref(color))
        self._objects.append(pen)
        return pen

    def _brush(self, color: str):
        brush = gdi32.CreateSolidBrush(_ref(color))
        self._objects.append(brush)
        return brush

    def box(self, x1, y1, x2, y2, *, color: str = RED, width: int = 3,
            radius: int = 6) -> "Shot":
        pen = self._pen(color, width)
        old_pen = gdi32.SelectObject(self._dc, pen)
        old_brush = gdi32.SelectObject(self._dc, gdi32.GetStockObject(NULL_BRUSH))
        if radius:
            gdi32.RoundRect(self._dc, int(x1), int(y1), int(x2), int(y2),
                            radius * 2, radius * 2)
        else:
            gdi32.Rectangle(self._dc, int(x1), int(y1), int(x2), int(y2))
        gdi32.SelectObject(self._dc, old_pen)
        gdi32.SelectObject(self._dc, old_brush)
        return self

    def circle(self, cx, cy, radius, *, color: str = RED, width: int = 4) -> "Shot":
        pen = self._pen(color, width)
        old_pen = gdi32.SelectObject(self._dc, pen)
        old_brush = gdi32.SelectObject(self._dc, gdi32.GetStockObject(NULL_BRUSH))
        gdi32.Ellipse(self._dc, int(cx - radius), int(cy - radius),
                      int(cx + radius), int(cy + radius))
        gdi32.SelectObject(self._dc, old_pen)
        gdi32.SelectObject(self._dc, old_brush)
        return self

    def arrow(self, x1, y1, x2, y2, *, color: str = RED, width: int = 4,
              head: int = 16) -> "Shot":
        pen, brush = self._pen(color, width), self._brush(color)
        old_pen = gdi32.SelectObject(self._dc, pen)
        old_brush = gdi32.SelectObject(self._dc, brush)
        gdi32.MoveToEx(self._dc, int(x1), int(y1), None)
        gdi32.LineTo(self._dc, int(x2), int(y2))
        dx, dy = int(x2) - int(x1), int(y2) - int(y1)
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        unit_x, unit_y = dx / length, dy / length
        perp_x, perp_y = -unit_y, unit_x
        base_x, base_y = int(x2 - unit_x * head), int(y2 - unit_y * head)
        half = head * 0.5
        points = (wintypes.POINT * 3)(
            wintypes.POINT(int(x2), int(y2)),
            wintypes.POINT(int(base_x + perp_x * half), int(base_y + perp_y * half)),
            wintypes.POINT(int(base_x - perp_x * half), int(base_y - perp_y * half)))
        gdi32.Polygon(self._dc, points, 3)
        gdi32.SelectObject(self._dc, old_pen)
        gdi32.SelectObject(self._dc, old_brush)
        return self

    def _font(self, size: int, *, bold: bool = True):
        font = gdi32.CreateFontW(-int(size), 0, 0, 0, 700 if bold else 400, 0, 0, 0,
                                 1, 0, 0, 5, 0, "Microsoft YaHei UI")
        self._objects.append(font)
        return font

    def label(self, x, y, text: str, *, color: str = WHITE, bg: str = DARK,
              size: int = 16) -> "Shot":
        font = self._font(size)
        old_font = gdi32.SelectObject(self._dc, font)
        gdi32.SetBkMode(self._dc, OPAQUE)
        gdi32.SetBkColor(self._dc, _ref(bg))
        gdi32.SetTextColor(self._dc, _ref(color))
        gdi32.TextOutW(self._dc, int(x), int(y), text, len(text))
        gdi32.SelectObject(self._dc, old_font)
        return self

    def text_width(self, text: str, *, size: int = 16) -> int:
        font = self._font(size)
        old_font = gdi32.SelectObject(self._dc, font)
        extent = wintypes.SIZE()
        gdi32.GetTextExtentPoint32W(self._dc, text, len(text), ctypes.byref(extent))
        gdi32.SelectObject(self._dc, old_font)
        return int(extent.cx)

    def badge(self, cx, cy, text: str, *, color: str = RED, radius: int = 16,
              size: int = 20) -> "Shot":
        brush, pen = self._brush(color), self._pen(color, 2)
        old_pen = gdi32.SelectObject(self._dc, pen)
        old_brush = gdi32.SelectObject(self._dc, brush)
        gdi32.Ellipse(self._dc, int(cx - radius), int(cy - radius),
                      int(cx + radius), int(cy + radius))
        gdi32.SelectObject(self._dc, old_pen)
        gdi32.SelectObject(self._dc, old_brush)
        font = self._font(size)
        old_font = gdi32.SelectObject(self._dc, font)
        gdi32.SetBkMode(self._dc, TRANSPARENT)
        gdi32.SetTextColor(self._dc, _ref(WHITE))
        extent = wintypes.SIZE()
        gdi32.GetTextExtentPoint32W(self._dc, text, len(text), ctypes.byref(extent))
        gdi32.TextOutW(self._dc, int(cx - extent.cx / 2), int(cy - extent.cy / 2),
                       text, len(text))
        gdi32.SelectObject(self._dc, old_font)
        return self

    def save(self, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        # 给整张图描一圈细边：教程窗里图是贴在深色正文上的，没有边看着会"飘"
        self.box(0, 0, self.width - 1, self.height - 1, color="#3A4557", width=1, radius=0)
        if not backdrop.write_png(self.picture(), target):
            raise OSError(f"写 PNG 失败：{target}")
        print(f"  → {target.relative_to(ROOT)}（{self.width}x{self.height}，"
              f"{target.stat().st_size / 1024:.0f} KB）")
        # 顺手写一份"显示尺寸副本"：教程窗让 Tk 直接读它，比自己解码再重编码快 5 倍
        # （13 张 570 ms → 106 ms）。教程窗那边会校验副本不比原图旧。
        display = target.parent / "display" / target.name
        display.parent.mkdir(parents=True, exist_ok=True)
        backdrop.write_png(backdrop.scale_to_width(self.picture(), 660), display)
        return target

    def release(self) -> None:
        for handle in self._objects:
            gdi32.DeleteObject(handle)
        self._objects.clear()
        if self._dc:
            gdi32.DeleteDC(self._dc)
            self._dc = None
        if self._bitmap:
            gdi32.DeleteObject(self._bitmap)
            self._bitmap = None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def find(root, *, text: str | None = None, kind=None):
    """按文字/类型找控件（标注点跟着真实控件走，版面改了也不会错位）。"""
    for widget in walk(root):
        if kind is not None and not isinstance(widget, kind):
            continue
        if text is not None:
            try:
                if not str(widget.cget("text")).startswith(text):
                    continue
            except Exception:                            # noqa: BLE001
                continue
        return widget
    return None


def hwnd_of(widget) -> int:
    return int(user32.GetAncestor(wintypes.HWND(widget.winfo_id()), 2))


def window_origin(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def pump(widget, times: int = 14) -> None:
    for _ in range(times):
        widget.update()
        time.sleep(0.04)


def clean_monday():
    """演示面板固定用"下一个没有节日气氛的周一"（理由同 capture_screens.py）。"""
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


def rect_of(widget, origin: tuple[int, int], *, pad: int = 5) -> tuple[int, int, int, int]:
    """控件在**窗口位图**里的矩形。

    必须在窗口还活着的时候算 —— 控件一旦 destroy，`winfo_*` 就会抛
    `TclError: application has been destroyed`（第一版是在标注阶段才去问控件，
    于是所有控制台图全挂在这儿）。
    """
    x1 = widget.winfo_rootx() - origin[0] - pad
    y1 = widget.winfo_rooty() - origin[1] - pad
    return (x1, y1, x1 + widget.winfo_width() + pad * 2, y1 + widget.winfo_height() + pad * 2)


class Annotator:
    """把"窗口里的一个矩形"翻译成"图里的框 + 右侧一条说明"。"""

    def __init__(self, shot: Shot, *, offset: tuple[int, int] = (0, 0)):
        self.shot = shot
        self.offset = offset
        self.row = 18

    def area(self, box: tuple[int, int, int, int], number: str, note: str, *,
             color: str = RED) -> None:
        x1, y1, x2, y2 = box
        x1 += self.offset[0]
        y1 += self.offset[1]
        x2 += self.offset[0]
        y2 += self.offset[1]
        self.shot.box(x1, y1, x2, y2, color=color, width=3)
        # 编号放在框的**右上角**：标题区那几行文字都是左对齐的，右上角基本是空的，
        # 放左上角会把"19:36"的第一个数字压掉。
        self.shot.badge(x2 - 18, y1 + 18, number, color=color)
        self.note(number, note)

    def note(self, number: str, note: str) -> None:
        """右侧说明栏里写一行（超宽就折一行，够用了）。"""
        left = self.shot.width - MARGIN + 16
        text = f"{number}  {note}"
        if self.shot.text_width(text) > MARGIN - 32:
            head, tail = text, ""
            while self.shot.text_width(head) > MARGIN - 32 and len(head) > 6:
                tail = head[-1] + tail
                head = head[:-1]
            self.shot.label(left, self.row, head)
            self.row += 30
            self.shot.label(left + 26, self.row, tail)
        else:
            self.shot.label(left, self.row, text)
        self.row += 36


# ---------------------------------------------------------------------------
# 各张图
# ---------------------------------------------------------------------------

def _panel(work, day, position=(150, 80)):
    from agenda import palettes, theme
    from agenda.panel import AgendaPanel

    # 同上：全局调色板先复位，免得上一张"我的照片"的取色串到这一张
    theme.apply_palette(palettes.palette_for("classic"))

    panel = AgendaPanel(work, pipeline_ms=0, autostart_pipeline=False,
                        window_mode="topmost", position=position)
    panel.root.attributes("-topmost", True)
    panel.today_override = day
    panel.refresh()
    pump(panel.root, 20)
    return panel


def shot_panel_overview() -> Path:
    """① 面板总览：这条时间线由哪几块组成。

    ⚠ 标注框一律按**控件的真实坐标**算，不许写死像素：
    头部高度是自适应的（`下一项` 长了会折行），字号还跟着 DPI 缩放走
    （这台机器 125%）。第一版就是写死的 y=4/48/78/112，实拍出来 ②③④ 全错位
    —— 用户拿截图来问"框选错位了"，说的就是这个。
    """
    day = clean_monday()
    work = demo_data.seed_dir(day, holidays=False)
    panel = _panel(work, day)
    try:
        hwnd = hwnd_of(panel.root)
        origin = window_origin(hwnd)[:2]
        base = Shot.of_window(hwnd)
        boxes = {
            "clock": rect_of(panel.clock_label, origin, pad=6),
            "date": rect_of(panel.date_label, origin, pad=5),
            "next": rect_of(panel.next_label, origin, pad=5),
            "gear": rect_of(panel.gear, origin, pad=7),
            "status": rect_of(panel.status_label, origin, pad=4),
            "hint": rect_of(panel.hint_label, origin, pad=4),
        }
        # "下面是按天分组的日程"：头部下沿到底部那一行之间
        header_bottom = panel.header.winfo_rooty() - origin[1] + panel.header.winfo_height()
        footer_top = panel.footer.winfo_rooty() - origin[1]
        boxes["days"] = (8, header_bottom + 4, base.width - 34, footer_top - 4)
    finally:
        panel._destroy_festival()
        panel.root.destroy()
        demo_data.cleanup(work)

    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    mark.area(boxes["clock"], "1", "现在是几点")
    mark.area(boxes["date"], "2", "今天几号、第几教学周")
    mark.area(boxes["next"], "3", "最近的一件事，还有多久")
    mark.area(boxes["days"], "4", "这里是按天分组的日程", color=BLUE)
    mark.area(boxes["gear"], "5", "右下角小齿轮 = 打开设置", color=BLUE)
    mark.area(boxes["status"], "6", "状态：今天几节课、几条通知")
    base.release()
    return shot.save(OUT_DIR / "01-面板总览.png")


def shot_panel_gear() -> Path:
    """② 齿轮特写：右下角放大 3 倍。"""
    day = clean_monday()
    work = demo_data.seed_dir(day, holidays=False)
    panel = _panel(work, day)
    try:
        hwnd = hwnd_of(panel.root)
        origin = window_origin(hwnd)[:2]
        base = Shot.of_window(hwnd)
        gear_x = panel.gear.winfo_rootx() - origin[0]
        gear_y = panel.gear.winfo_rooty() - origin[1]
        gear_w, gear_h = panel.gear.winfo_width(), panel.gear.winfo_height()
    finally:
        panel._destroy_festival()
        panel.root.destroy()
        demo_data.cleanup(work)

    # 以**齿轮为中心**取一块方图：直接取角落的话，圈会偏到一边去
    pad = 30
    side = gear_w + pad * 2
    left = min(max(0, gear_x + gear_w // 2 - side // 2), max(0, base.width - side))
    top = min(max(0, gear_y + gear_h // 2 - side // 2), max(0, base.height - side))
    corner = base.crop(left, top, side, side)
    zoomed = corner.zoom(4)
    zoomed.circle((gear_x + gear_w // 2 - left) * 4, (gear_y + gear_h // 2 - top) * 4,
                  side * 2 - 6, color=RED, width=6)
    shot = zoomed.with_margin(right=300)
    shot.label(zoomed.width + 16, 18, "点这个齿轮 = 打开设置", size=17)
    shot.label(zoomed.width + 16, 54, "（面板右下角，一直在这儿）", size=15)
    base.release()
    return shot.save(OUT_DIR / "02-齿轮.png")


def shot_panel_photo() -> Path:
    """③ 「我的照片」主题：照片铺在顶部，字压在照片上。"""
    day = clean_monday()
    work = demo_data.seed_dir(day, holidays=False)
    width, height = 900, 320
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            pixels[offset:offset + 4] = bytes((
                int(180 - 120 * x / width), int(60 + 120 * y / height),
                int(30 + 200 * x / width), 255))
    photo = work / "demo-photo.png"
    backdrop.write_png(Picture(width=width, height=height, pixels=bytes(pixels),
                               stride=width * 4), photo)
    backdrop.prepare_background(photo, work, box=(0.05, 0.1, 0.9, 0.42))
    (work / "client.json").write_text('{"theme_mode": "photo", "hotkey_enabled": false}',
                                      encoding="utf-8")
    panel = _panel(work, day)
    try:
        panel._apply_theme(force=True)
        panel.refresh()
        pump(panel.root, 16)
        hwnd = hwnd_of(panel.root)
        origin = window_origin(hwnd)[:2]
        base = Shot.of_window(hwnd)
        header_h = int(panel.header.winfo_height())
    finally:
        panel._destroy_festival()
        panel.root.destroy()
        demo_data.cleanup(work)

    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    mark.area((6, 6, base.width - 6, header_h), "1", "照片铺在这一条上，文字压在照片上面")
    mark.area((6, header_h + 6, base.width - 6, base.height - 6), "2",
              "下面这些颜色都是从照片里取出来的")
    base.release()
    return shot.save(OUT_DIR / "03-照片主题.png")


def _console(want_tab: str | None = None, *, holidays: bool = False):
    from agenda import palettes, theme
    from agenda.client_app import AppController
    from agenda.control_window import ControlWindow

    # 调色板是**进程级全局**的（`theme.COLORS`）：前面拍"我的照片"主题时把它改成了
    # 从照片取色的那套，后面所有窗口都会跟着串色（实测控制台整片变成照片的粉紫色）。
    # 每次开新窗口前先复位成经典深色。
    theme.apply_palette(palettes.palette_for("classic"))

    work = demo_data.seed_dir(holidays=holidays)
    controller = AppController(work)
    controller.config.panel_visible = False
    window = ControlWindow(controller)
    window.root.geometry("1080x780+120+60")
    window.root.attributes("-topmost", True)
    window.refresh_all()
    if want_tab:
        for tab in window.notebook.tabs():
            if window.notebook.tab(tab, "text") == want_tab:
                window.notebook.select(tab)
                break
    window.root.lift()
    pump(window.root, 18)
    return window, work


def _console_shot(want_tab: str, *, holidays: bool = False):
    """开控制台 → 切页 → 截图 → **在窗口还活着时**把要标注的矩形算好。"""
    window, work = _console(want_tab, holidays=holidays)
    try:
        hwnd = hwnd_of(window.root)
        origin = window_origin(hwnd)[:2]
        base = Shot.of_window(hwnd)
        boxes: dict[str, tuple[int, int, int, int]] = {}
        for key, name in (("text", "notice_text"), ("agenda", "agenda_tree"),
                          ("course", "course_tree"), ("holiday", "holiday_tree"),
                          ("makeup", "makeup_tree")):
            widget = getattr(window, name, None)
            if widget is not None and widget.winfo_ismapped():
                boxes[key] = rect_of(widget, origin)
        for label in ("保存并刷新", "从文件识别课表", "粘贴课表", "课表体检",
                      "按节日预填", "从放假通知识别", "清理已结束", "套用节数",
                      "新增课程", "导出 WakeUp CSV", "撤销上次导入", "新增假期",
                      "添加一行", "保存设置", "查看使用教程"):
            found = find(window.root, text=label, kind=(tk.Button,))
            if found is not None and found.winfo_ismapped():
                boxes[label] = rect_of(found, origin)
    finally:
        try:
            window.stop_tick()
        except Exception:                                 # noqa: BLE001
            pass
        window.root.destroy()
        demo_data.cleanup(work)
    return base, boxes


def shot_console_notice() -> Path:
    """④ 控制台-通知页：粘哪儿、点哪个按钮。"""
    base, boxes = _console_shot("通知")
    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    if "text" in boxes:
        mark.area(boxes["text"], "1", "群消息整段粘到这里")
    if "agenda" in boxes:
        mark.area(boxes["agenda"], "2", "已录入的日程，双击可改")
    if "保存并刷新" in boxes:
        mark.area(boxes["保存并刷新"], "3", "点它保存", color=BLUE)
    base.release()
    return shot.save(OUT_DIR / "04-控制台-通知.png")


def shot_console_timetable() -> Path:
    """⑤ 控制台-课程表页：导入课表的几条通道。"""
    base, boxes = _console_shot("课程表")
    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    if "从文件识别课表" in boxes:
        mark.area(boxes["从文件识别课表"], "1", "课表文件走这条（最准）")
    if "粘贴课表" in boxes:
        mark.area(boxes["粘贴课表"], "2", "复制来的表格走这条")
    if "课表体检" in boxes:
        mark.area(boxes["课表体检"], "3", "导入后跑一次，查可疑之处")
    if "course" in boxes:
        mark.area(boxes["course"], "4", "课程列表：双击改、Delete 删")
    base.release()
    return shot.save(OUT_DIR / "05-控制台-课程表.png")


def shot_console_periods() -> Path:
    """⑥ 控制台-上课时间页。"""
    base, boxes = _console_shot("上课时间")
    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    if "套用节数" in boxes:
        mark.area(boxes["套用节数"], "1", "改完节数点它重排")
    note = "2  每节课的时 / 分各一个下拉框，不用打冒号"
    shot.label(shot.width - MARGIN + 16, 18, note[:46])
    base.release()
    return shot.save(OUT_DIR / "06-控制台-上课时间.png")


def shot_console_holiday() -> Path:
    """⑦ 控制台-假期页（带演示假期表）。"""
    base, boxes = _console_shot("假期", holidays=True)
    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    if "holiday" in boxes:
        mark.area(boxes["holiday"], "1", "假期区间：这几天不排课")
    if "makeup" in boxes:
        mark.area(boxes["makeup"], "2", "调休：那天补哪一天的课", color=BLUE)
    if "从放假通知识别" in boxes:
        mark.area(boxes["从放假通知识别"], "3", "放假通知原文粘进去自动填")
    base.release()
    return shot.save(OUT_DIR / "07-控制台-假期.png")


def shot_console_settings() -> Path:
    """⑧ 控制台-设置页（清掉小字之后的样子）。"""
    base, _boxes = _console_shot("设置")
    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    mark.note("※", "这一页只有功能名称，说明都在这份教程里")
    mark.note("※", "改完点最下面的「保存设置」")
    base.release()
    return shot.save(OUT_DIR / "08-控制台-设置.png")


def shot_course_review() -> Path:
    """⑨ 确认识别结果窗：导入课表**必须**过这一关。"""
    from agenda.course_review import CourseReviewDialog

    rows = [dict(course) for course in demo_data.DEMO_TIMETABLE["courses"][:8]]
    root = tk.Tk()
    root.geometry("+5000+5000")
    dialog = CourseReviewDialog(root, rows, source="演示课表.ics",
                                on_confirm=lambda result: None,
                                term_start=demo_data.DEMO_TIMETABLE["termStart"])
    dialog.geometry("1020x580+160+90")
    dialog.attributes("-topmost", True)
    pump(dialog, 20)
    boxes = {}
    try:
        hwnd = hwnd_of(dialog)
        origin = window_origin(hwnd)[:2]
        base = Shot.of_window(hwnd)
        for label in ("确认导入", "重新识别", "取消"):
            found = find(dialog, text=label, kind=(tk.Button,))
            if found is not None:
                boxes[label] = rect_of(found, origin)
        # 术语输入区（"第 1 教学周周一"那一行）和课程表格：都按真实控件算，
        # 不写死坐标 —— 面板那几张就是写死坐标才错位的
        term_label = find(dialog, text="第 1 教学周周一")
        if term_label is not None:
            boxes["term"] = rect_of(term_label, origin, pad=6)
        if getattr(dialog, "tree", None) is not None:
            boxes["tree"] = rect_of(dialog.tree, origin, pad=4)
    finally:
        dialog.destroy()
        root.destroy()

    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    if "term" in boxes:
        mark.area(boxes["term"], "1", "第一教学周周一：周次按它算", color=BLUE)
    if "tree" in boxes:
        mark.area(boxes["tree"], "2", "双击任意一格就地改")
    if "确认导入" in boxes:
        mark.area(boxes["确认导入"], "3", "核对完点它导入", color=BLUE)
    if "重新识别" in boxes:
        mark.area(boxes["重新识别"], "4", "认错了就重新识别")
    base.release()
    return shot.save(OUT_DIR / "09-课表确认.png")


def shot_panel_menu() -> Path:
    """⑩ 选中一张卡片（补充备注那一步的前提）＋脚底那行提示。

    原本想拍右键菜单本体，但 Tk 的弹出菜单既不阻塞在可预期的地方、窗口矩形也量不到，
    合成事件下拍十次有九次是空的。与其放一张不可靠的图，不如把**这一步必须看懂的东西**
    拍清楚：点卡片＝选中（边框变蓝）、底部会写明选中了谁。
    """
    day = clean_monday()
    work = demo_data.seed_dir(day, holidays=False)
    panel = _panel(work, day)
    try:
        card = next(card for section in panel.timeline.sections for card in section.cards
                    if card.kind == "event")
        panel.select_card(card)
        pump(panel.root, 16)
        hwnd = hwnd_of(panel.root)
        base = Shot.of_window(hwnd)
        origin = window_origin(hwnd)[:2]
        frame = next((frame for frame, event_id in panel._card_frames
                      if event_id and event_id == card.event_id), None)
        card_box = rect_of(frame, origin) if frame is not None else None
        if card_box is None:                       # 退一步：用卡片在窗口里的位置估
            card_box = (8, 200, base.width - 20, 330)
        hint = panel.hint_label
        hint_box = rect_of(hint, origin, pad=4)
    finally:
        panel._destroy_festival()
        panel.root.destroy()
        demo_data.cleanup(work)

    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    mark.area(card_box, "1", "点一下卡片 = 选中（边框变蓝）")
    mark.area(hint_box, "2", "底部会写明选中了哪一条", color=BLUE)
    mark.note("※", "选中之后再按热键 = 把新消息补进它的备注")
    mark.note("※", "右键卡片还有：编辑 / 完成 / 改结束时间 / 复制 / 删除")
    base.release()
    return shot.save(OUT_DIR / "10-选中卡片.png")


def shot_crop_dialog() -> Path:
    """⑪ 框选照片范围：拖框 → 保存。"""
    from agenda import photo_crop, theme
    from agenda.panel import HEADER_STRIP_HEIGHT

    width, height = 900, 620
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            pixels[offset:offset + 4] = bytes((
                int(160 - 100 * x / width),
                int(80 + 100 * (1 - y / height)),
                int(40 + 180 * x / width), 255))
    picture = Picture(width=width, height=height, pixels=bytes(pixels), stride=width * 4)

    root = tk.Tk()
    root.geometry("+5200+5200")
    dialog = photo_crop.CropDialog(root, picture, aspect=360 / HEADER_STRIP_HEIGHT,
                                   colors=theme.COLORS, title="框选照片范围")
    dialog.window.attributes("-topmost", True)
    pump(dialog.window, 20)
    try:
        hwnd = hwnd_of(dialog.window)
        origin = window_origin(hwnd)[:2]
        base = Shot.of_window(hwnd)
        canvas = dialog.canvas
        canvas_x = canvas.winfo_rootx() - origin[0]
        canvas_y = canvas.winfo_rooty() - origin[1]
        left, top, box_w, box_h = dialog._rect()
    finally:
        dialog.window.destroy()
        root.destroy()

    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    mark.area((canvas_x + left, canvas_y + top, canvas_x + left + box_w,
               canvas_y + top + box_h), "1", "框住要显示的那块，比例已按面板锁好")
    mark.note("2", "在框外拖＝重画；在框里拖＝平移")
    mark.note("3", "点「保存」生效，点「用整张照片」不裁")
    base.release()
    return shot.save(OUT_DIR / "11-框选照片.png")


def shot_edit_dialog() -> Path | None:
    """⑫ 编辑单条通知窗。"""
    from agenda import theme
    from agenda.panel import ask_edit_event

    root = tk.Tk()
    root.geometry("+5300+5300")
    root.configure(bg=theme.COLORS["bg"])
    captured: dict = {}

    def grab_then_close() -> None:
        target = next((child for child in root.winfo_children()
                       if isinstance(child, tk.Toplevel) and child.winfo_exists()), None)
        if target is None:
            root.after(200, grab_then_close)
            return
        pump(target, 12)
        hwnd = hwnd_of(target)
        captured["origin"] = window_origin(hwnd)[:2]
        captured["shot"] = Shot.of_window(hwnd)
        target.destroy()

    root.after(900, grab_then_close)
    ask_edit_event(root, title="班会：本学期评奖评优材料提交", date="2026-10-12",
                   start="12:00", end="13:00", location="辅导员办公室",
                   people="各班班长", notes="电子版发群文件")
    root.destroy()
    shot_base = captured.get("shot")
    if shot_base is None:
        print("  编辑窗没抓到，跳过")
        return None

    shot = shot_base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    mark.note("※", "右键卡片 →「编辑此条通知…」")
    mark.note("※", "日期是三段输入框，短横线删不掉")
    mark.note("※", "改错了不关窗，下面用红字说明")
    shot_base.release()
    return shot.save(OUT_DIR / "12-编辑通知.png")


def shot_hotkey_feedback() -> Path:
    """⑬ 热键按下去之后，面板底部那行会说什么。"""
    day = clean_monday()
    work = demo_data.seed_dir(day, holidays=False)
    panel = _panel(work, day)
    try:
        panel._toast("已录入 2 条、更新 1 条通知（来自选中的文字）")
        pump(panel.root, 14)
        hwnd = hwnd_of(panel.root)
        origin = window_origin(hwnd)[:2]
        base = Shot.of_window(hwnd)
        hint = panel.hint_label
        hint_x = hint.winfo_rootx() - origin[0]
        hint_y = hint.winfo_rooty() - origin[1]
        hint_w = hint.winfo_width()
        hint_h = hint.winfo_height()
    finally:
        panel._destroy_festival()
        panel.root.destroy()
        demo_data.cleanup(work)

    shot = base.with_margin(right=MARGIN)
    mark = Annotator(shot)
    mark.area((hint_x - 6, hint_y - 6, hint_x + hint_w + 6, hint_y + hint_h + 6), "1",
              "底部这一行告诉你刚刚录了什么")
    base.release()
    return shot.save(OUT_DIR / "13-热键反馈.png")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"输出目录：{OUT_DIR}")
    tasks = [
        ("面板总览", shot_panel_overview),
        ("齿轮特写", shot_panel_gear),
        ("照片主题", shot_panel_photo),
        ("控制台-通知", shot_console_notice),
        ("控制台-课程表", shot_console_timetable),
        ("控制台-上课时间", shot_console_periods),
        ("控制台-假期", shot_console_holiday),
        ("控制台-设置", shot_console_settings),
        ("课表确认窗", shot_course_review),
        ("选中卡片", shot_panel_menu),
        ("框选照片", shot_crop_dialog),
        ("编辑通知窗", shot_edit_dialog),
        ("热键反馈", shot_hotkey_feedback),
    ]
    failed = []
    for name, func in tasks:
        print(f"[{name}]")
        try:
            func()
        except Exception as error:                        # noqa: BLE001
            failed.append((name, error))
            print(f"  失败：{type(error).__name__}: {error}")
    print()
    if failed:
        print(f"有 {len(failed)} 张没拍成：")
        for name, error in failed:
            print(f"  · {name}：{type(error).__name__}: {error}")
        return 1
    print(f"全部完成，共 {len(tasks)} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
