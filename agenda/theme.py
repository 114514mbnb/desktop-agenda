"""面板配色与 Windows 11 适配。

Win11 相关处理：
  * Per-Monitor V2 DPI 感知，避免 125%/150% 缩放下文字发虚
  * 无边框 + 置顶 + 工具窗口（不进 Alt+Tab）
  * 可选 DWM 圆角 / 深色标题栏；失败不影响功能
  * 字体优先 Segoe UI Variable（Win11 自带），中文回落 Microsoft YaHei UI
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 配色（深色半透明卡片风）
# ---------------------------------------------------------------------------

COLORS = {
    "bg": "#12161D",
    "bg_soft": "#171C25",
    "card": "#1D2430",
    "card_now": "#232C3B",
    "card_past": "#181D26",
    "line": "#2A3240",
    "text": "#E9EDF5",
    "text_dim": "#A7B0C0",
    "text_faint": "#6E7788",
    "accent": "#4C8DF6",
    "border": "#242C39",
    "today_bg": "#1A2130",
    #: 禁用/只读输入类控件的底色与文字（ttk 默认会给浅灰，在深色主题上很刺眼）
    "card_off": "#161B24",
    #: 「随时刻」主题里标题区那条竖向渐变的两个端点（上 → 下）。
    #: 经典模式这两个值和 bg_soft 一样（等于不画渐变，外观保持原样）。
    "head_top": "#171C25",
    "head_bottom": "#171C25",
}

FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Noto Sans SC",
    "SimHei",
)


def apply_palette(palette: dict) -> None:
    """**就地**替换配色（不重新绑定 COLORS 这个名字）。

    为什么是就地更新而不是 `COLORS = palette`：全项目有 300 多处
    `theme.COLORS["x"]` 的读取点，其中不少在函数默认参数或模块级常量里提前取过值；
    重新绑定会让"旧引用"和"新引用"指向两个字典，出现一半新一半旧的鬼界面。
    `dict.update` 保证所有引用看到的是同一份数据。

    传入的调色板必须带齐全部键——少一个键就会在某个控件构建时 KeyError
    （`tests/test_theme_modes.py` 钉住了这条）。
    """
    missing = set(COLORS) - set(palette)
    if missing:
        raise ValueError(f"调色板缺少这些键：{sorted(missing)}")
    COLORS.update(palette)


@dataclass(frozen=True)
class Fonts:
    family: str
    title: int = 15
    heading: int = 11
    time: int = 11
    body: int = 10
    meta: int = 9
    badge: int = 8

    def spec(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return (self.family, size, weight)


def ensure_dpi_awareness() -> float:
    """开启 Per-Monitor V2 DPI 感知并返回缩放系数（失败返回 1.0）。

    为什么必须开：在 125% 缩放的显示器上，DPI 不感知的进程会被合成器整体放大
    1.25 倍——右对齐的窗口于是被推出屏幕（实测几何 1146 却画在 1432）。
    开启后坐标 1:1，winfo_screenwidth 就是物理像素。
    """
    if sys.platform != "win32":
        return 1.0
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return 1.0
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


def mix(color_a: str, color_b: str, ratio: float) -> str:
    """把两个 #RRGGBB 按比例混合，ratio=0 取 a，=1 取 b。用于生成卡片底色。"""
    def parts(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    a_r, a_g, a_b = parts(color_a)
    b_r, b_g, b_b = parts(color_b)
    ratio = max(0.0, min(1.0, ratio))
    mixed = (
        round(a_r + (b_r - a_r) * ratio),
        round(a_g + (b_g - a_g) * ratio),
        round(a_b + (b_b - a_b) * ratio),
    )
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def tint(base: str, accent: str, ratio: float = 0.22, dim: float = 0.5) -> str:
    """在底色上叠一点强调色，得到"淡彩卡片"底色；`dim` 控制整体压暗/提亮的强度。

    ⚠ 方向必须跟着底色走：`dim<1` 时**深色底要压暗、浅色底要提亮**。
    原来这里写死了 `mix("#000000", value, dim)`（一律压暗），在浅色主题上直接把
    白卡片压成中灰，配上浅色主题的深色字就是"深底深字"——实拍截图里几乎看不清
    （`白天` 主题的卡片就是这个下场）。现在按底色亮度选锚点。
    """
    value = mix(base, accent, ratio)
    if dim < 1.0:
        anchor = "#000000" if _is_dark(base) else "#FFFFFF"
        value = mix(anchor, value, dim)
    return value


def _is_dark(color: str) -> bool:
    """这个颜色算深色还是浅色（用感知亮度，不用简单平均）。"""
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    raw = color.lstrip("#")
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return luminance < 0.45


def readable_accent(color: str, background: str, *, min_ratio: float = 3.0) -> str:
    """把一个"语义色"调整到当前底色上看得清（保持色相，只改明度）。

    为什么需要它：节日金 `#FFD166`、假期橙 `#F2994A`、进行中绿 `#37C978` 这些颜色
    都是按**深色底**挑的。放到浅色主题（清晨/白天）的底上，对比度只剩 1.2:1——
    实测截图里那行日期几乎看不见。与其给 8 个节日 × 4 套配色手工配 32 个颜色，
    不如统一按"混到对比度达标为止"来算，顺手也保护了以后新加的语义色。

    返回一个尽量贴近原色的颜色：先小步往黑/往白混，哪一步达标就返回。
    """
    if not (color.startswith("#") and background.startswith("#")):
        return color
    from . import backdrop       # 延迟导入：避免 theme ←→ backdrop 的循环依赖

    if backdrop.contrast_ratio(color, background) >= min_ratio:
        return color
    best, best_ratio = color, backdrop.contrast_ratio(color, background)
    for step in range(1, 10):
        for anchor in ("#000000", "#FFFFFF"):
            candidate = mix(color, anchor, step / 10)
            ratio = backdrop.contrast_ratio(candidate, background)
            if ratio > best_ratio:
                best, best_ratio = candidate, ratio
            if ratio >= min_ratio:
                return candidate
    return best


def pick_font_family(root) -> str:
    """挑一个系统里真实存在的中文字体。"""
    try:
        from tkinter import font as tkfont
        available = set(tkfont.families(root))
    except Exception:
        return FONT_CANDIDATES[0]
    for name in FONT_CANDIDATES:
        if name in available:
            return name
    return FONT_CANDIDATES[0]


def apply_windows_flourishes(window, *, rounded: bool = True, dark_title: bool = True, blur: bool = False) -> None:
    """Win11 圆角 / 深色标题栏 / 可选模糊；任何一步失败都静默跳过。

    注意：blur 默认关闭。DWM 的 ACCENT_ENABLE_BLURBEHIND 会让上面的 tk 控件
    也跟着变透明，实测会把深色面板冲淡到看不清字，所以默认只用纯色背景。
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()
    except Exception:
        return

    # 深色标题栏（Win10 1809+ / Win11）
    if dark_title:
        try:
            value = ctypes.c_int(1)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE 新/旧编号
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                )
        except Exception:
            pass

    # 圆角（Win11 22000+）
    if rounded:
        try:
            preference = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference)
            )
        except Exception:
            pass

    # 轻微背景模糊（Win11 22H2+）；会让 tk 控件也半透明，默认不开
    if not blur:
        return
    try:
        class AccentPolicy(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

        class WindowCompositionAttributeData(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                        ("SizeOfData", ctypes.c_size_t)]

        accent = AccentPolicy()
        accent.AccentState = 3  # ACCENT_ENABLE_BLURBEHIND
        accent.GradientColor = 0xCC12161D
        data = WindowCompositionAttributeData()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
    except Exception:
        pass
