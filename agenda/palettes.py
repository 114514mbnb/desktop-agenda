"""面板主题：经典深色 / 随时刻 / 我的照片。

三种模式（用户点名的三个选项）：
  * **经典深色**（`classic`）—— 一直以来的那套深灰蓝配色，不变；
  * **随时刻**（`auto`）—— 按当前时间在**清晨 / 白天 / 黄昏 / 深夜**四套配色之间自动切换，
    参考《异环》登录界面那种"界面跟着天色走"的感觉；
  * **我的照片**（`photo`）—— 用户挑一张自己的照片，程序从照片里取色生成整套配色，
    并按背景亮度自动决定用深色字还是浅色字（白底黑字、黑底白字）。

为什么把调色板单独放一个模块：面板里 `COLORS["x"]` 的读取点有 89 处、控制台 215 处，
逐个改造调用点既危险又没必要——真正要做的是**保持同一套键**，运行时整体替换（见
`theme.apply_palette`）。所以这里的硬约束是：**每套配色必须有完全相同的键**，
少一个键就会在某个控件构建时 KeyError 崩掉；`tests/test_theme_modes.py` 钉住了这一点。
"""

from __future__ import annotations

import datetime as dt

#: 三种模式的机器名 → 界面上显示的名字
MODES: dict[str, str] = {
    "classic": "经典深色",
    "auto": "随时刻",
    "photo": "我的照片",
}
DEFAULT_MODE = "classic"

#: 四个时段
SLOT_LABELS: dict[str, str] = {
    "morning": "清晨",
    "day": "白天",
    "dusk": "黄昏",
    "night": "深夜",
}

#: 经典深色：和 `theme.COLORS` 的初始值一致（改这里等于改默认外观）
CLASSIC: dict[str, str] = {
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
    "card_off": "#161B24",
}

#: 清晨：暖白，像早读时的窗光
MORNING: dict[str, str] = {
    "bg": "#FBF7F1",
    "bg_soft": "#F3EBDF",
    "card": "#FFFFFF",
    "card_now": "#FFF4E3",
    "card_past": "#F6F2EB",
    "line": "#E2D7C7",
    "text": "#2C2721",
    "text_dim": "#5F5749",
    "text_faint": "#877F70",
    "accent": "#C2762A",
    "border": "#E6DCCC",
    "today_bg": "#F6EEE1",
    "card_off": "#EFE8DD",
}

#: 白天：冷白，长时间看着不累
DAY: dict[str, str] = {
    "bg": "#F3F6FA",
    "bg_soft": "#E8EEF7",
    "card": "#FFFFFF",
    "card_now": "#E9F1FF",
    "card_past": "#F1F4F8",
    "line": "#D6DEE9",
    "text": "#1A232F",
    "text_dim": "#515C6B",
    "text_faint": "#78838F",
    "accent": "#2E6DD0",
    "border": "#DBE3EE",
    "today_bg": "#EDF3FB",
    "card_off": "#ECF0F6",
}

#: 黄昏：暖暗，收尾时段
DUSK: dict[str, str] = {
    "bg": "#241A20",
    "bg_soft": "#2E2229",
    "card": "#3A2A33",
    "card_now": "#47333D",
    "card_past": "#2A1F26",
    "line": "#4B3843",
    "text": "#F6E8DF",
    "text_dim": "#CBB5AA",
    "text_faint": "#9E8C84",
    "accent": "#E58A4E",
    "border": "#3C2B32",
    "today_bg": "#301F27",
    "card_off": "#2A2027",
}

#: 深夜：深蓝黑，夜里不刺眼
NIGHT: dict[str, str] = {
    "bg": "#0C1119",
    "bg_soft": "#121926",
    "card": "#17202F",
    "card_now": "#1E2A3D",
    "card_past": "#111823",
    "line": "#22304A",
    "text": "#DCE6F5",
    "text_dim": "#9FB0C7",
    "text_faint": "#78899F",
    "accent": "#5B8DEF",
    "border": "#1B2536",
    "today_bg": "#141C29",
    "card_off": "#111722",
}

TIME_PALETTES: dict[str, dict[str, str]] = {
    "morning": MORNING,
    "day": DAY,
    "dusk": DUSK,
    "night": NIGHT,
}

#: 时段的起止（左闭右开，按小时）
_SLOT_HOURS: tuple[tuple[str, int, int], ...] = (
    ("morning", 5, 9),      # 05:00 – 08:59
    ("day", 9, 17),         # 09:00 – 16:59
    ("dusk", 17, 20),       # 17:00 – 19:59
    ("night", 20, 29),      # 20:00 – 次日 04:59（29 = 24+5，跨零点不写两段）
)


def slot_for(moment: dt.datetime | None = None) -> str:
    """当前属于哪个时段。凌晨 0-4 点算"深夜"，不是"清晨"——那时候开灯看屏幕才刺眼。"""
    now = moment or dt.datetime.now()
    hour = now.hour
    if hour < 5:
        hour += 24
    for name, start, end in _SLOT_HOURS:
        if start <= hour < end:
            return name
    return "night"


def normalize_mode(value: str | None) -> str:
    """配置里写的模式名；认不出来一律回退到经典深色（比崩掉好）。"""
    return value if value in MODES else DEFAULT_MODE


def palette_for(mode: str, *, now: dt.datetime | None = None,
                photo_palette: dict[str, str] | None = None) -> dict[str, str]:
    """取某个模式下此刻应该用的调色板。

    `photo_palette` 是照片模式从图片里算出来的那套；没给（照片丢了/还没选）
    就回退到经典深色，绝不让界面因为没有配色而崩。
    """
    mode = normalize_mode(mode)
    if mode == "auto":
        return dict(TIME_PALETTES[slot_for(now)])
    if mode == "photo":
        return dict(photo_palette or CLASSIC)
    return dict(CLASSIC)


def describe(mode: str, *, now: dt.datetime | None = None, photo_name: str = "") -> str:
    """给设置页用的一句话说明。"""
    mode = normalize_mode(mode)
    if mode == "auto":
        slot = slot_for(now)
        return f"随时刻：现在处于「{SLOT_LABELS[slot]}」，共 清晨 / 白天 / 黄昏 / 深夜 四套配色"
    if mode == "photo":
        return f"我的照片：{photo_name or '（还没选照片）'}"
    return "经典深色：固定配色，不随时间变化"
