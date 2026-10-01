"""面板主题：经典深色 / 随时刻 / 我的照片。

三种模式（用户点名的三个选项）：
  * **经典深色**（`classic`）—— 一直以来的那套深灰蓝配色，不变；
  * **随时刻**（`auto`）—— 按当前时间在**清晨 / 白天 / 黄昏 / 深夜**四套配色之间自动切换。
    每套都带一对 `head_top` / `head_bottom`：面板顶部标题区会画成**竖向渐变**，
    清晨是日出（天蓝 → 暖金）、黄昏是日落（紫 → 橙），中午和深夜各有一套低调的渐变；
  * **我的照片**（`photo`）—— 用户挑一张自己的照片，程序从照片里取色生成整套配色，
    并按背景亮度自动决定用深色字还是浅色字（白底黑字、黑底白字）。

为什么把调色板单独放一个模块：面板里 `COLORS["x"]` 的读取点有 89 处、控制台 215 处，
逐个改造调用点既危险又没必要——真正要做的是**保持同一套键**，运行时整体替换（见
`theme.apply_palette`）。所以这里的硬约束是：**每套配色必须有完全相同的键**，
少一个键就会在某个控件构建时 KeyError 崩掉；`tests/test_theme_modes.py` 钉住了这一点。

另一条硬约束：**正文颜色必须压在 `card` 上有足够对比度**（正文 ≥4.5、次要 ≥3，
WCAG AA）。渐变只画在标题区，正文永远是平的等价底色——所以"好看"不能拿可读性换。
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

#: 每个时段标题区渐变的一句话说明（设置页/日志用）
SLOT_SKY: dict[str, str] = {
    "morning": "日出：天蓝 → 暖金",
    "day": "正午：淡蓝 → 近白",
    "dusk": "日落：紫 → 橙",
    "night": "夜空：深蓝 → 近黑",
}

#: 经典深色：和 `theme.COLORS` 的初始值一致（改这里等于改默认外观）。
#: `head_top/head_bottom` 给的是 `bg_soft` —— 经典模式**不画渐变**（保持原样），
#: 这一对只是为了满足"每套配色键都一样"。
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
    "head_top": "#171C25",
    "head_bottom": "#171C25",
}

#: 清晨：日出。标题区天蓝→暖金，正文用暖白（和渐变下端接得上，不出现接缝）
MORNING: dict[str, str] = {
    "bg": "#F7F2EA",
    "bg_soft": "#F1E8DA",
    "card": "#FFFFFF",
    "card_now": "#FFF3E1",
    "card_past": "#F5F1EA",
    "line": "#E3D7C5",
    "text": "#2B2620",
    "text_dim": "#5B5244",
    "text_faint": "#867D6D",
    "accent": "#B96A22",
    "border": "#E7DCCA",
    "today_bg": "#F6EDE0",
    "card_off": "#EFE7DA",
    "head_top": "#93B7E9",
    "head_bottom": "#F8D2A2",
}

#: 白天：正午。冷白为主，标题区一条很淡的天蓝渐变（长时间盯着不累）
DAY: dict[str, str] = {
    "bg": "#F2F5F9",
    "bg_soft": "#E7EDF6",
    "card": "#FFFFFF",
    "card_now": "#E8F0FE",
    "card_past": "#EFF3F8",
    "line": "#D5DDE9",
    "text": "#1A232F",
    "text_dim": "#4E5967",
    "text_faint": "#76818E",
    "accent": "#2E6DD0",
    "border": "#DAE2ED",
    "today_bg": "#ECF2FA",
    "card_off": "#ECEFF5",
    "head_top": "#7FA9DC",
    "head_bottom": "#E6EEF9",
}

#: 黄昏：日落。标题区紫→橙，正文是暖紫棕的暗色（卡片提亮，字依然清楚）
DUSK: dict[str, str] = {
    "bg": "#241A22",
    "bg_soft": "#2F2229",
    "card": "#3B2A34",
    "card_now": "#4A3440",
    "card_past": "#2A1F26",
    "line": "#4E3A45",
    "text": "#F7EAE1",
    "text_dim": "#D2BCB1",
    "text_faint": "#A79089",
    # 强调色同时是**课徽章的底**（上面压白字）：要压到白字对比 ≥3 才看得清，
    # 所以没用更亮的橙（#F0A05F 只有 2.12）。#C97A3C 白字 3.32、当文字也够亮。
    "accent": "#C97A3C",
    "border": "#3E2C35",
    "today_bg": "#30212A",
    "card_off": "#2A2027",
    "head_top": "#6B4E7C",
    "head_bottom": "#F29C57",
}

#: 深夜：夜空。标题区深蓝→近黑，正文深蓝黑，夜里不刺眼
NIGHT: dict[str, str] = {
    "bg": "#0B1018",
    "bg_soft": "#121926",
    "card": "#182234",
    "card_now": "#1F2B40",
    "card_past": "#121A27",
    "line": "#23324C",
    "text": "#DFE8F7",
    "text_dim": "#A3B4CB",
    "text_faint": "#7A8CA3",
    # 同上：夜空蓝压深一档，白字对比从 2.88 提到 3.64
    "accent": "#5B84DB",
    "border": "#1C2638",
    "today_bg": "#141C2B",
    "card_off": "#111722",
    "head_top": "#1F2C49",
    "head_bottom": "#0A0E16",
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


def sky_gradient(mode: str, *, now: dt.datetime | None = None,
                 palette: dict[str, str] | None = None) -> tuple[str, str] | None:
    """取标题区渐变的两个端点；不需要渐变时返回 None。

    只有「随时刻」画渐变：经典模式的标题区一直是平的（用户没要求改它），
    照片模式铺的是用户自己的照片。
    """
    if normalize_mode(mode) != "auto":
        return None
    colors = palette or palette_for("auto", now=now)
    top, bottom = colors.get("head_top"), colors.get("head_bottom")
    if not top or not bottom or top.upper() == bottom.upper():
        return None
    return top, bottom


def describe(mode: str, *, now: dt.datetime | None = None, photo_name: str = "") -> str:
    """给设置页用的一句话说明。"""
    mode = normalize_mode(mode)
    if mode == "auto":
        slot = slot_for(now)
        return (f"随时刻：现在处于「{SLOT_LABELS[slot]}」——标题区是"
                f"{SLOT_SKY[slot]}的渐变，共 清晨 / 白天 / 黄昏 / 深夜 四套配色")
    if mode == "photo":
        return f"我的照片：{photo_name or '（还没选照片）'}"
    return "经典深色：固定配色，不随时间变化"
