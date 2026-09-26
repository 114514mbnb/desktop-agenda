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
}

FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Noto Sans SC",
    "SimHei",
)


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
    """在深色底上叠一点强调色，得到"淡彩卡片"底色。dim<1 时整体压暗。"""
    value = mix(base, accent, ratio)
    if dim < 1.0:
        value = mix("#000000", value, dim)
    return value


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
