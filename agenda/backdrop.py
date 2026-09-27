"""背景照片：解码、缩放、取色、编码成 Tk 能显示的图（**不依赖 Pillow**）。

为什么不用 Pillow：这个项目的硬约束是"零第三方运行时依赖"，而 Pillow 会给便携版
再添 4～5 MB（上一轮刚花了力气把包瘦到 15 MB），也与 README 里"零依赖"的说法冲突。

那怎么读 JPEG（Tk 自己只认 PNG/GIF）？用 Windows 自带的 **GDI+**（`gdiplus.dll`）：
  照片文件 → GdipCreateBitmapFromFile → GdipGetImageThumbnail（顺便缩放）
  → GdipBitmapLockBits 拿到 BGRA 像素
再给 Tk 就不难了：Tk 的 PhotoImage 能吃 base64 的 PNG，
而 PNG 用标准库 `zlib` + `binascii` 就能拼出来（见 `png_bytes`）。
实测 3840×2160 的 JPEG：解码 + 取色 39 ms，一次性开销可以忽略。

同一份 BGRA 像素还用来做两件事：
  * 按区域取平均色 → 让"照片模式"的文字自动变成深色或浅色（白底黑字、黑底白字）；
  * 生成整套界面配色（背景、卡片、分隔线、强调色都从照片里来）。
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"

#: 准备背景图时统一缩到这么宽（够 360～800 px 的面板用，又不会让 PNG 变得很大）
PREPARE_WIDTH = 1600


@dataclass(frozen=True)
class Picture:
    """一张已经解码好的位图（BGRA，和 Windows 的字节序一致）。"""

    width: int
    height: int
    pixels: bytes
    stride: int

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        offset = y * self.stride + x * 4
        b, g, r = self.pixels[offset], self.pixels[offset + 1], self.pixels[offset + 2]
        return r, g, b


# ---------------------------------------------------------------------------
# GDI+ 解码 / 缩放
# ---------------------------------------------------------------------------

class _GpRect(ctypes.Structure):
    _fields_ = [("X", ctypes.c_int), ("Y", ctypes.c_int),
                ("Width", ctypes.c_int), ("Height", ctypes.c_int)]


class _BitmapData(ctypes.Structure):
    _fields_ = [("Width", ctypes.c_uint), ("Height", ctypes.c_uint),
                ("Stride", ctypes.c_int), ("PixelFormat", ctypes.c_int),
                ("Scan0", ctypes.c_void_p), ("Reserved", ctypes.c_void_p)]


class _GdiPlus:
    """GDI+ 的启动/关闭。用完就关，别把 token 留着。"""

    def __init__(self) -> None:
        self.token = ctypes.c_void_p()
        self.ok = False
        if not _IS_WINDOWS:
            return
        try:
            class StartupInput(ctypes.Structure):
                _fields_ = [("GdiplusVersion", ctypes.c_uint32),
                            ("DebugEventCallback", ctypes.c_void_p),
                            ("SuppressBackgroundThread", ctypes.c_int),
                            ("SuppressExternalCodecs", ctypes.c_int)]

            self.gdiplus = ctypes.windll.gdiplus
            argument = StartupInput()
            argument.GdiplusVersion = 1
            self.ok = self.gdiplus.GdiplusStartup(
                ctypes.byref(self.token), ctypes.byref(argument), None) == 0
        except Exception:  # noqa: BLE001
            self.ok = False

    def __enter__(self) -> "_GdiPlus":
        return self

    def __exit__(self, *_exc) -> None:
        if self.ok:
            try:
                self.gdiplus.GdiplusShutdown(self.token)
            except Exception:  # noqa: BLE001
                pass


def load_image(path: Path | str, *, max_width: int = PREPARE_WIDTH) -> Picture | None:
    """解码任意常见图片格式（JPEG/PNG/BMP/GIF…），必要时等比缩到 `max_width`。"""
    if not _IS_WINDOWS:
        return None
    target = Path(path)
    if not target.is_file():
        return None
    with _GdiPlus() as gp:
        if not gp.ok:
            return None
        return _load_with(gp, target, max_width)


def _load_with(gp: _GdiPlus, target: Path, max_width: int) -> Picture | None:
    gdiplus = gp.gdiplus
    bitmap = ctypes.c_void_p()
    try:
        if gdiplus.GdipCreateBitmapFromFile(ctypes.c_wchar_p(str(target)),
                                            ctypes.byref(bitmap)) != 0:
            return None
        width, height = ctypes.c_int(), ctypes.c_int()
        gdiplus.GdipGetImageWidth(bitmap, ctypes.byref(width))
        gdiplus.GdipGetImageHeight(bitmap, ctypes.byref(height))
        if width.value <= 0 or height.value <= 0:
            return None

        source = bitmap
        thumbnail = ctypes.c_void_p()
        if max_width and width.value > max_width:
            ratio = max_width / width.value
            thumb_w = max(1, int(width.value * ratio))
            thumb_h = max(1, int(height.value * ratio))
            # GdipGetImageThumbnail 会保持长宽比，这里给的尺寸只是上限
            if gdiplus.GdipGetImageThumbnail(bitmap, thumb_w, thumb_h,
                                             ctypes.byref(thumbnail), None, None) == 0:
                source = thumbnail
                gdiplus.GdipGetImageWidth(source, ctypes.byref(width))
                gdiplus.GdipGetImageHeight(source, ctypes.byref(height))

        rect = _GpRect(0, 0, width.value, height.value)
        data = _BitmapData()
        # PixelFormat32bppARGB：拿到的是 BGRA，且每行有 stride 对齐
        if gdiplus.GdipBitmapLockBits(source, ctypes.byref(rect), 1, 0x0026200A,
                                      ctypes.byref(data)) != 0:
            return None
        try:
            if not data.Scan0:
                return None
            buffer = ctypes.string_at(data.Scan0, data.Stride * height.value)
            return Picture(width=width.value, height=height.value,
                           pixels=buffer, stride=data.Stride)
        finally:
            gdiplus.GdipBitmapUnlockBits(source, ctypes.byref(data))
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            if bitmap:
                gdiplus.GdipDisposeImage(bitmap)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# PNG 编码（标准库 zlib）+ 交给 Tk
# ---------------------------------------------------------------------------

def png_bytes(picture: Picture) -> bytes:
    """把 BGRA 像素拼成 PNG 字节流。用的是标准库，不引第三方。"""
    raw = bytearray()
    for y in range(picture.height):
        raw.append(0)                                  # 每行的 filter type：0 = 不滤波
        row = picture.pixels[y * picture.stride:y * picture.stride + picture.width * 4]
        rgba = bytearray()
        for x in range(0, len(row), 4):
            rgba += bytes((row[x + 2], row[x + 1], row[x], 255))   # BGRA → RGBA（照片不透明）
        raw.extend(rgba)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", binascii.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", picture.width, picture.height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def to_photoimage(root, picture: Picture):
    """转成 Tk 的 PhotoImage。调用方要自己留着引用，否则会被回收。"""
    import tkinter as tk

    encoded = base64.b64encode(png_bytes(picture)).decode("ascii")
    return tk.PhotoImage(master=root, data=encoded)


def write_png(picture: Picture, target: Path) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png_bytes(picture))
        return True
    except OSError:
        return False


def tint_picture(picture: Picture, color: str, ratio: float) -> Picture:
    """把整张图朝某个颜色混一档（ratio=0 原样，=1 全变成那个颜色）。

    用途：照片铺在标题区时先朝面板底色混一下，文字压在上面才读得清——
    Tk 的控件不支持透明度，只能在这一层把"半透明遮罩"预先算进像素里。
    """
    ratio = max(0.0, min(1.0, ratio))
    if ratio <= 0:
        return picture
    r, g, b = _parts(color)
    source = picture.pixels
    out = bytearray(len(source))
    for offset in range(0, len(source), 4):
        out[offset] = int(source[offset] * (1 - ratio) + b * ratio)
        out[offset + 1] = int(source[offset + 1] * (1 - ratio) + g * ratio)
        out[offset + 2] = int(source[offset + 2] * (1 - ratio) + r * ratio)
        out[offset + 3] = source[offset + 3]
    return Picture(width=picture.width, height=picture.height,
                   pixels=bytes(out), stride=picture.stride)


# ---------------------------------------------------------------------------
# 取色与对比度
# ---------------------------------------------------------------------------

def _parts(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(round(c)))) for c in rgb))


def blend(color_a: str, color_b: str, ratio: float) -> str:
    """按比例混合（0 = 全 a，1 = 全 b）。"""
    a, b = _parts(color_a), _parts(color_b)
    ratio = max(0.0, min(1.0, ratio))
    return hex_of(tuple(a[i] + (b[i] - a[i]) * ratio for i in range(3)))


def relative_luminance(color: str) -> float:
    """WCAG 相对亮度（0=黑，1=白）。文字该黑该白就看它。"""
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = _parts(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(color_a: str, color_b: str) -> float:
    """WCAG 对比度（1 = 完全一样，21 = 黑白）。正文建议 ≥4.5，次要文字 ≥3。"""
    light, dark = sorted((relative_luminance(color_a), relative_luminance(color_b)),
                         reverse=True)
    return (light + 0.05) / (dark + 0.05)


#: 深色字与浅色字的候选（不是纯黑纯白，纯色在照片上会显得生硬）
INK_DARK = "#1B2027"
INK_LIGHT = "#F4F7FB"


def readable_ink(background: str, *, dark: str = INK_DARK, light: str = INK_LIGHT) -> str:
    """背景亮就用深色字，背景暗就用浅色字——两边的对比度谁高用谁。"""
    return dark if contrast_ratio(background, dark) >= contrast_ratio(background, light) else light


def sample_region(picture: Picture, x: int, y: int, width: int, height: int,
                  *, step: int = 6) -> str:
    """取一块区域的平均色（隔 step 个像素采一次，够用且快）。"""
    if picture.width <= 0 or picture.height <= 0 or width <= 0 or height <= 0:
        return "#808080"
    left = max(0, min(picture.width - 1, x))
    top = max(0, min(picture.height - 1, y))
    right = max(left + 1, min(picture.width, x + width))
    bottom = max(top + 1, min(picture.height, y + height))
    step = max(1, step)

    total_r = total_g = total_b = 0
    count = 0
    for py in range(top, bottom, step):
        row_offset = py * picture.stride
        for px in range(left, right, step):
            offset = row_offset + px * 4
            total_b += picture.pixels[offset]
            total_g += picture.pixels[offset + 1]
            total_r += picture.pixels[offset + 2]
            count += 1
    if not count:
        return "#808080"
    return hex_of((total_r / count, total_g / count, total_b / count))


def average_color(picture: Picture, *, step: int = 12) -> str:
    return sample_region(picture, 0, 0, picture.width, picture.height, step=step)


def crop(picture: Picture, x: int, y: int, width: int, height: int) -> Picture:
    """裁一块出来（标题区只显示照片的上半部分时用）。"""
    left = max(0, min(picture.width - 1, x))
    top = max(0, min(picture.height - 1, y))
    right = max(left + 1, min(picture.width, x + width))
    bottom = max(top + 1, min(picture.height, y + height))
    rows = bytearray()
    for py in range(top, bottom):
        start = py * picture.stride + left * 4
        rows += picture.pixels[start:start + (right - left) * 4]
    return Picture(width=right - left, height=bottom - top, pixels=bytes(rows),
                   stride=(right - left) * 4)


def palette_from_image(picture: Picture, *, accent_hint: tuple[int, int, int] | None = None
                       ) -> dict[str, str]:
    """从照片里推导出一整套界面配色。

    做法（刻意保守，保证任何照片下文字都看得清）：
      1. 整体平均色 + 上三分之一（通常是人脸/天空，视觉重心）各取一次；
      2. 底色取它们的混合，再往"整体压暗/提亮"的方向走一档，保证和文字有对比；
      3. 卡片色比底色亮一点（暗底）或暗一点（亮底），形成层次；
      4. 文字色由**背景亮度**决定：亮背景用深墨，暗背景用浅墨；
      5. 强调色优先用照片里比较"跳"的颜色（饱和度最高的采样块），否则用中性蓝。
    """
    overall = average_color(picture)
    top = sample_region(picture, 0, 0, picture.width, max(1, picture.height // 3), step=10)
    base = blend(overall, top, 0.35)

    light_scene = relative_luminance(base) > 0.45
    if light_scene:
        # 亮照片：底色稍微压一点，否则白卡片和白底分不开
        bg = blend(base, "#FFFFFF", 0.55)
        bg_soft = blend(bg, "#000000", 0.06)
        card = blend(bg, "#FFFFFF", 0.55)
        card_now = blend(card, "#FFFFFF", 0.25)
        card_past = blend(bg, "#000000", 0.04)
        border = blend(bg, "#000000", 0.14)
        today_bg = blend(bg, bg_soft, 0.6)
        card_off = blend(bg, "#000000", 0.05)
    else:
        bg = blend(base, "#000000", 0.45)
        bg_soft = blend(bg, "#FFFFFF", 0.06)
        card = blend(bg, "#FFFFFF", 0.10)
        card_now = blend(card, "#FFFFFF", 0.10)
        card_past = blend(bg, "#000000", 0.12)
        border = blend(bg, "#FFFFFF", 0.16)
        today_bg = blend(bg, "#FFFFFF", 0.04)
        card_off = blend(bg, "#000000", 0.08)

    ink = readable_ink(card)
    ink_dim = blend(ink, card, 0.38)
    ink_faint = blend(ink, card, 0.58)
    accent = _accent_from(picture, light_scene=light_scene)
    return {
        "bg": bg,
        "bg_soft": bg_soft,
        "card": card,
        "card_now": card_now,
        "card_past": card_past,
        "line": blend(card, ink, 0.22),
        "text": ink,
        "text_dim": ink_dim,
        "text_faint": ink_faint,
        "accent": accent,
        "border": border,
        "today_bg": today_bg,
        "card_off": card_off,
    }


def _accent_from(picture: Picture, *, light_scene: bool) -> str:
    """从若干采样块里挑一个"最像强调色"的：饱和度高、又不能太亮/太暗。"""
    best: tuple[float, str] | None = None
    columns, rows = 3, 3
    for row in range(rows):
        for column in range(columns):
            block_w = max(1, picture.width // columns)
            block_h = max(1, picture.height // rows)
            color = sample_region(picture, column * block_w, row * block_h,
                                  block_w, block_h, step=12)
            r, g, b = _parts(color)
            high, low = max(r, g, b), min(r, g, b)
            saturation = (high - low) / high if high else 0.0
            luma = relative_luminance(color)
            # 太亮/太暗的块当强调色会看不清，直接排除
            if luma < 0.10 or luma > 0.85:
                continue
            score = saturation
            if best is None or score > best[0]:
                best = (score, color)
    if best is None or best[0] < 0.18:
        return "#2E6DD0" if light_scene else "#5B8DEF"
    color = best[1]
    if light_scene:
        return blend(color, "#000000", 0.25)       # 亮底上压深一点才够对比
    return blend(color, "#FFFFFF", 0.18)


# ---------------------------------------------------------------------------
# 用户选照片 → 准备好放进 data/theme/
# ---------------------------------------------------------------------------

THEME_DIR = "theme"
BACKGROUND_NAME = "background.png"


def background_path(data_dir: Path) -> Path:
    return Path(data_dir) / THEME_DIR / BACKGROUND_NAME


def prepare_background(source: Path | str, data_dir: Path, *,
                       max_width: int = PREPARE_WIDTH) -> tuple[bool, str]:
    """把用户挑的照片转成程序自己的背景图，返回 (成功?, 说明)。

    为什么要转存而不是直接用原图：
      * Tk 不认 JPEG，每次启动都解码原图（可能十几 MB）又慢又占内存；
      * 原图可能被移动/删除，转存之后行为稳定；
      * 顺手缩到 1600 px 宽，PNG 只有几百 KB。
    """
    source = Path(source)
    if not source.is_file():
        return False, "找不到这个文件"
    picture = load_image(source, max_width=max_width)
    if picture is None:
        return False, "这个图片格式读不出来（支持 JPG / PNG / BMP / GIF）"
    target = background_path(Path(data_dir))
    if not write_png(picture, target):
        return False, f"写入失败：{target}"
    size_kb = target.stat().st_size / 1024
    return True, f"已使用：{source.name}（{picture.width}×{picture.height}，{size_kb:.0f} KB）"


def load_background(data_dir: Path, *, max_width: int) -> Picture | None:
    """读程序自己的背景图并按面板宽度缩放。"""
    return load_image(background_path(Path(data_dir)), max_width=max_width)


def has_background(data_dir: Path) -> bool:
    return background_path(Path(data_dir)).is_file()


def clear_background(data_dir: Path) -> None:
    try:
        background_path(Path(data_dir)).unlink()
    except OSError:
        pass
