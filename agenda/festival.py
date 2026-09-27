"""节日彩蛋：节日日期 + 每个节日的配色、装饰、祝福语。

需求原话：「在中秋节，元旦，春节，国庆节等重要传统节日适当做些小彩蛋，
凸显一定的节日气氛和该节日特点，彩蛋的触发条件为节日当天以及相应法定假期内」。

关于日期怎么来的：
  * 元旦 / 劳动节 / 国庆节是公历固定日期；春节 / 元宵 / 端午 / 中秋是农历节日，
    清明是节气 —— 这些**全部交给 `agenda/lunar.py`（内部是随包附带的 borax
    `lunardate`，MIT）去算，覆盖农历 1900–2100 年**，不再逐年写日期表。
  * 超出这个范围就安静地不触发彩蛋，而不是猜一个错日期。

关于装饰怎么画：
  * **不用 emoji，用 Canvas 图元**。实测（`tools/font_probe.py`）Tk 8.6 在这台机器上
    能把 emoji 画出来，但小字号（9pt，横幅正文就是这个大小）下 🧧🏮 这类新 emoji
    直接变成豆腐块；圆/线/扇形则永远清晰、颜色可控、还能动。
    文字部分只留 ★ ✦ ☾ ❁ 这类各字号都稳的符号。
  * **触发条件**：节日当天；或落在 `data/calendar.json` 里同名的假期区间内
    （这样"国庆 10/1–10/8 放假"整段都有气氛）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date as Date
from datetime import timedelta

from . import lunar

#: 彩蛋支持的年份范围（来自 vendored 的农历库）
FIRST_YEAR = lunar.FIRST_LUNAR_YEAR
LAST_YEAR = lunar.LAST_LUNAR_YEAR

#: 节日当天前后各多少天也进入节日状态（用户要求"范围大一点"）。
#: 于是中秋 9/25 的话，9/22 起面板就换主题色、出横幅。
FESTIVAL_WINDOW_DAYS = 3


@dataclass(frozen=True)
class Particle:
    """彩蛋动画里的一个装饰图元。

    坐标是相对横幅画布的 0~1；`speed` 是每秒的相位推进量，
    `kind` 决定画成什么（见 festival_banner.py）。
    """

    kind: str          # moon | star | lantern | firework | boat | drop | gear | wave | text
    x: float
    y: float
    size: float = 1.0
    speed: float = 0.0
    phase: float = 0.0
    color: str = ""
    text: str = ""


@dataclass(frozen=True)
class Festival:
    key: str
    name: str
    emoji: str                                  # 横幅开头那个符号（只用"小字号也画得出来"的那些）
    greeting: str                               # 一句话祝福（横幅上）
    egg: str                                    # 点开横幅才看到的彩蛋文案
    accent: str                                 # 强调色（日期、符号）
    banner_bg: str                              # 横幅底色
    banner_fg: str                              # 横幅文字色
    particles: tuple[Particle, ...] = ()
    statutory: bool = True                      # 是不是法定假日（预填假期时只填这些）
    suggested_span: tuple[int, int] = (0, 0)    # 预填连休：节日前 N 天 / 后 M 天
    #: 这一年的具体日期（模板里是 None，festival_occurrences 会填上）
    day: Date | None = None

    def days_until(self, today: Date) -> int | None:
        if self.day is None:
            return None
        return (self.day - today).days


def _midautumn_particles(accent: str) -> tuple[Particle, ...]:
    """中秋：一轮满月 + 几点疏星 + 两朵薄云，月亮慢慢升。"""
    return (
        Particle("moon", 0.80, 0.50, size=2.0, speed=0.06, phase=0.0),
        Particle("star", 0.14, 0.28, size=1.0, speed=0.7, phase=0.0),
        Particle("star", 0.30, 0.66, size=0.8, speed=0.5, phase=1.3),
        Particle("star", 0.55, 0.20, size=0.9, speed=0.6, phase=2.1),
        Particle("wave", 0.22, 0.80, size=1.6, speed=0.25, phase=0.0),
        Particle("wave", 0.62, 0.88, size=1.3, speed=0.2, phase=1.7),
    )


def _spring_particles() -> tuple[Particle, ...]:
    """春节：两盏灯笼轻轻晃 + 雪花似的金屑 + 红包。"""
    return (
        Particle("lantern", 0.16, 0.30, size=1.0, speed=0.8, phase=0.0),
        Particle("lantern", 0.34, 0.22, size=0.8, speed=0.9, phase=1.1),
        Particle("lantern", 0.86, 0.30, size=1.0, speed=0.8, phase=2.4),
        Particle("spark", 0.55, 0.5, size=1.0, speed=0.5, phase=0.3),
        Particle("spark", 0.70, 0.5, size=1.0, speed=0.45, phase=1.9),
        Particle("spark", 0.45, 0.5, size=1.0, speed=0.55, phase=2.8),
    )


def _newyear_particles() -> tuple[Particle, ...]:
    """元旦：两簇烟花交替炸开。"""
    return (
        Particle("firework", 0.24, 0.52, size=2.2, speed=0.55, phase=0.0),
        Particle("firework", 0.62, 0.40, size=1.8, speed=0.45, phase=1.4),
        Particle("firework", 0.86, 0.60, size=1.4, speed=0.6, phase=2.6),
        Particle("star", 0.44, 0.20, size=0.9, speed=0.8, phase=0.6),
        Particle("star", 0.10, 0.72, size=0.8, speed=0.7, phase=2.2),
    )


def _national_particles() -> tuple[Particle, ...]:
    """国庆：五颗星 + 一支飘动的旗。"""
    return (
        Particle("star", 0.14, 0.46, size=2.0, speed=0.0, phase=0.0),
        Particle("star", 0.22, 0.24, size=1.0, speed=0.0, phase=0.0),
        Particle("star", 0.30, 0.24, size=1.0, speed=0.0, phase=0.0),
        Particle("star", 0.22, 0.70, size=1.0, speed=0.0, phase=0.0),
        Particle("star", 0.30, 0.70, size=1.0, speed=0.0, phase=0.0),
        Particle("wave", 0.60, 0.44, size=2.6, speed=0.35, phase=0.0),
        Particle("wave", 0.72, 0.44, size=2.6, speed=0.35, phase=1.6),
        Particle("spark", 0.88, 0.5, size=1.0, speed=0.5, phase=0.9),
    )


def _dragon_particles() -> tuple[Particle, ...]:
    """端午：一叶龙舟 + 水波。"""
    return (
        Particle("boat", 0.34, 0.60, size=1.8, speed=0.30, phase=0.0),
        Particle("wave", 0.20, 0.84, size=2.0, speed=0.35, phase=0.0),
        Particle("wave", 0.55, 0.90, size=1.6, speed=0.3, phase=1.5),
        Particle("wave", 0.85, 0.84, size=1.4, speed=0.4, phase=2.7),
        Particle("star", 0.80, 0.24, size=0.8, speed=0.6, phase=1.0),
    )


def _qingming_particles() -> tuple[Particle, ...]:
    """清明：细雨 + 抽芽的柳枝。"""
    return (
        Particle("drop", 0.18, 0.10, size=1.0, speed=1.0, phase=0.0),
        Particle("drop", 0.42, 0.10, size=1.0, speed=1.15, phase=1.3),
        Particle("drop", 0.68, 0.10, size=1.0, speed=1.05, phase=2.4),
        Particle("drop", 0.88, 0.10, size=1.0, speed=1.2, phase=0.7),
        Particle("wave", 0.30, 0.34, size=2.2, speed=0.18, phase=0.0),
        Particle("star", 0.55, 0.70, size=0.9, speed=0.5, phase=1.9),
    )


def _labor_particles() -> tuple[Particle, ...]:
    """劳动节：慢慢转的齿轮 + 星星点点。"""
    return (
        Particle("gear", 0.26, 0.50, size=2.0, speed=0.35, phase=0.0),
        Particle("gear", 0.44, 0.66, size=1.3, speed=-0.5, phase=0.0),
        Particle("spark", 0.66, 0.44, size=1.0, speed=0.6, phase=0.4),
        Particle("spark", 0.78, 0.62, size=1.0, speed=0.5, phase=1.8),
        Particle("star", 0.88, 0.28, size=0.9, speed=0.7, phase=2.6),
    )


def _lantern_particles() -> tuple[Particle, ...]:
    """元宵：灯笼 + 圆月。"""
    return (
        Particle("moon", 0.82, 0.34, size=2.0, speed=0.05, phase=0.0),
        Particle("lantern", 0.20, 0.34, size=1.0, speed=0.7, phase=0.0),
        Particle("lantern", 0.42, 0.44, size=0.9, speed=0.8, phase=1.2),
        Particle("lantern", 0.62, 0.30, size=1.0, speed=0.75, phase=2.3),
        Particle("star", 0.10, 0.66, size=0.8, speed=0.6, phase=1.0),
    )


#: 模板表（`day` 由 festival_occurrences 填）。
#: 文案里**不放 emoji**：实测（tools/font_probe.py）小字号下 🧧🏮 这类新 emoji
#: 会画成豆腐块，而横幅正文和彩蛋都是 9pt 上下。气氛交给上面的 Canvas 图元，
#: 文字部分只用 ★ ☆ ✦ ✧ ❋ ✺ ❁ ❀ ✿ ☾ ☽ 这些各字号都稳的符号。
TEMPLATES: tuple[Festival, ...] = (
    Festival(
        key="newyear", name="元旦", emoji="✦",
        greeting="元旦安康，新岁顺遂",
        egg="岁首之日，宜先览未来一周课程安排，"
            "再从去岁未竟之事中择其一续行。不必贪多，一件即可。",
        accent="#FFD166", banner_bg="#16233A", banner_fg="#FFF3D6",
        particles=_newyear_particles(),
        suggested_span=(0, 0),
    ),
    Festival(
        key="spring", name="春节", emoji="✺",
        greeting="新春吉祥，万事顺遂",
        egg="春节期间课程整体停排，群内通知照常汇总显示。"
            "假期可安心休息，不必担心遗漏通知。",
        accent="#FF6B6B", banner_bg="#2A1113", banner_fg="#FFE3C2",
        particles=_spring_particles(),
        suggested_span=(1, 6),
    ),
    Festival(
        key="lantern", name="元宵节", emoji="❁",
        greeting="元宵佳节，圆满如意",
        egg="正月十五，岁节之末。愿新岁圆满，诸事顺遂。",
        accent="#FFB454", banner_bg="#2A1A10", banner_fg="#FFE9C9",
        particles=_lantern_particles(),
        statutory=False, suggested_span=(0, 0),
    ),
    Festival(
        key="qingming", name="清明节", emoji="❋",
        greeting="清明时节，出行请留意天气",
        egg="清明前后多雨，祭扫踏青请备好雨具，注意出行安全。",
        accent="#7ED9A6", banner_bg="#13241B", banner_fg="#DCF6E6",
        particles=_qingming_particles(),
        suggested_span=(0, 0),
    ),
    Festival(
        key="labor", name="劳动节", emoji="✿",
        greeting="劳动节快乐，宜适时休息",
        egg="劳动节源于争取八小时工作制。假期期间课程停排，宜充分休息、调整状态。",
        accent="#F2994A", banner_bg="#2A1B10", banner_fg="#FFE6CC",
        particles=_labor_particles(),
        suggested_span=(0, 4),
    ),
    Festival(
        key="dragon", name="端午节", emoji="❀",
        greeting="端午安康，顺颂时祺",
        egg="端午安康。观龙舟、食角黍皆为节俗，假期期间课程停排。",
        accent="#4CD08A", banner_bg="#10251C", banner_fg="#D8F7E6",
        particles=_dragon_particles(),
        suggested_span=(0, 0),
    ),
    Festival(
        key="midautumn", name="中秋节", emoji="☾",
        greeting="中秋快乐，但愿人长久",
        egg="但愿人长久，千里共婵娟。"
            "今夜月明，不妨抬头一观。",
        accent="#FFD166", banner_bg="#1A2136", banner_fg="#FFF1CE",
        particles=_midautumn_particles("#FFD166"),
        suggested_span=(0, 0),
    ),
    Festival(
        key="national", name="国庆节", emoji="★",
        greeting="国庆节快乐，祝祖国繁荣昌盛",
        egg="祝祖国繁荣昌盛。假期出行人员密集，请注意安全；"
            "返校前请核对课表是否设有调休。",
        accent="#FF5A5F", banner_bg="#2A1013", banner_fg="#FFE0D6",
        particles=_national_particles(),
        suggested_span=(0, 6),
    ),
)


def festival_occurrences(year: int) -> list[Festival]:
    """某一年所有节日的具体日期。

    农历节日/节气交给 `agenda/lunar.py` 算（覆盖农历 1900–2100）。
    整年超出范围就返回空列表：**宁可这一年没有彩蛋，也不猜一个错日期**。
    （元旦/劳动节/国庆节是公历固定日期，其实算得出来，但只给它们开彩蛋
    会让"支持范围"这件事变得难以解释，所以一并不触发。）
    """
    if not FIRST_YEAR <= year <= LAST_YEAR:
        return []
    found: list[Festival] = []
    for template in TEMPLATES:
        day = lunar.festival_date(year, template.key)
        # 农历库的公历下界是 1900-01-31，所以 1900 年的元旦（1/1）落在范围外。
        # 这类边界日期一并不触发，保证"彩蛋日期一定在支持范围内"这条不变式。
        if day is None or not lunar.supported(day):
            continue
        found.append(replace(template, day=day))
    return sorted(found, key=lambda item: item.day or Date(year, 1, 1))


def supported_years() -> tuple[int, int]:
    """彩蛋能覆盖的年份区间（闭区间）。"""
    return FIRST_YEAR, LAST_YEAR


def occurrences_near(day: Date) -> list[Festival]:
    """`day` 前后那几年里的节日（跨年时要用，比如元旦假期从去年 12/30 开始）。"""
    found: list[Festival] = []
    for year in (day.year - 1, day.year, day.year + 1):
        found.extend(festival_occurrences(year))
    return found


def _own_span_contains(day: Date, festival: Festival) -> bool:
    """这一天是不是落在**这个节日自己的天数**里。

    `suggested_span` 本来是用来预填连休的（节日前 N 天 / 后 M 天），它同时也是
    "这个节日自然占几天"的准确描述：中秋 (0, 0) 就是一天，国庆 (0, 6) 是七天，
    春节 (1, 6)、劳动节 (0, 4) 同理。
    """
    if festival.day is None:
        return False
    before, after = festival.suggested_span
    return festival.day - timedelta(days=before) <= day <= festival.day + timedelta(days=after)


#: 假期区间和节日日期的**容差**：节日落在区间里就算数；偏离区间边界这么多天的也算
#: （法定假期的起止偶尔和节日当天差一天，比如节前一天开始连休）。
_RANGE_REACH_DAYS = 1
#: 只对得上一个节日的区间，长度不超过这么多天时才认为"整段都是它的假期"。
#: 再长的（比如一个月的寒假里只有春节）只覆盖节日自己的天数，不然整个寒假都挂着春节横幅。
_SINGLE_RANGE_MAX_DAYS = 10


def _matching_festivals(holiday) -> list[Festival]:
    """这个假期区间该放哪些节日的彩蛋。

    **只看日历里的日期，不看假期叫什么名字**（用户要求：「彩蛋的触发依据是你内置的
    农历以及日历」）。判定就是一句：这个节日的日期落在这个区间里（容差
    `_RANGE_REACH_DAYS` 天）。所以假期叫"中秋国庆连放"也好、改成"放假"也好，效果一样。

    注意不能用"区间前后 10 天内的节日都算"这种宽松判据：国庆 10/1–10/8 那个假期
    离中秋只差 6 天，那样会被当成"中秋+国庆"的合并区间，反而把 10/8 挤出彩蛋范围。
    """
    inside: list[Festival] = []
    for festival in occurrences_near(holiday.start):
        if festival.day is None:
            continue
        if (holiday.start - timedelta(days=_RANGE_REACH_DAYS)
                <= festival.day
                <= holiday.end + timedelta(days=_RANGE_REACH_DAYS)):
            inside.append(festival)
    inside.sort(key=lambda item: item.day or holiday.start)
    return inside


def festival_for(day: Date, calendar=None, *, window: int | None = None) -> Festival | None:
    """这一天该放哪个节日的彩蛋（没有就返回 None）。

    触发路径：
      1. **节日当天**；
      2. 落在 `calendar.json` 里**同名**假期区间内：
         - 区间只对得上**一个**节日（国庆 10/1–10/7、清明三天…）→ 整段都是它的气氛（原行为）；
         - 区间对得上**多个**节日（典型的是合并写法"中秋国庆连放"）→ **按各节日自己的天数
           分段**，谁的那几天归谁，不属于任何节日的日子**没有彩蛋**（恢复原样）。
      3. 节日前后各 `FESTIVAL_WINDOW_DAYS` 天（默认 3 天）也进入节日状态；
         假期刚结束的收尾期内同样保留（`calendar.wrap_up`）。

    第 2 条的"多节日分段"是用户反馈之后加的。原来是"名字对得上的第一个节日吃掉整段"，
    于是 `中秋国庆连放`（9/25–10/7）整段都算中秋：用户 9/28 看到"中秋 已过 3 天"，
    更离谱的是国庆假期里的 10/5 显示"中秋 已过 10 天"——**国庆自己的假期反而没有国庆**。
    """
    span = FESTIVAL_WINDOW_DAYS if window is None else max(0, int(window))
    holiday = calendar.holiday_on(day) if calendar is not None else None
    if holiday is not None:
        matched = _matching_festivals(holiday)
        if len(matched) > 1:
            # 合并区间（如"中秋国庆连放"）：按各节日自己的天数分段，谁的那几天归谁，
            # 中间不属于任何节日的日子就**没有彩蛋**（中秋过了就恢复原样）。
            for festival in matched:
                if _own_span_contains(day, festival):
                    return festival
            return None
        if len(matched) == 1:
            festival = matched[0]
            length = (holiday.end - holiday.start).days + 1
            if length <= _SINGLE_RANGE_MAX_DAYS:
                return festival             # 单一节日的假期：整段都有气氛
            if _own_span_contains(day, festival):
                return festival             # 区间太长（如整个寒假）：只覆盖节日自己的天数
            return None

    # 不在假期区间里（或这个区间对不上任何节日）：节日当天 / 前后 N 天
    if isinstance(day, Date):
        for festival in festival_occurrences(day.year):
            if festival.day == day:
                return festival
        if span:
            # 注意不能拿 `festival_occurrences` 返回的对象做 `is` 比较 ——
            # 每次调用都会重新构造对象，身份永远对不上（踩过）。
            nearby = _nearest(day, span)
            if nearby is not None:
                return nearby

    if calendar is None:
        return None
    # 假期结束后的收尾期也保持节日状态（和"收心倒计时"配套）。
    # 只看**日期**够不够近，不看假期叫什么（用户要求），而且要取离这个假期最近的
    # 那一年：occurrences_near 会翻前后三年，先撞上的是去年那个国庆（实测"已过 373 天"）。
    wrap_up = calendar.wrap_up(day)
    if wrap_up is None:
        return None
    nearby_best: tuple[int, Festival] | None = None
    for festival in occurrences_near(wrap_up.holiday.end):
        if festival.day is None:
            continue
        distance = abs((festival.day - wrap_up.holiday.end).days)
        if distance > 10:
            continue
        if nearby_best is None or distance < nearby_best[0]:
            nearby_best = (distance, festival)
    return nearby_best[1] if nearby_best is not None else None


def _nearest(day: Date, span: int) -> Festival | None:
    """前后 span 天里离 day 最近的那个节日（同距离取先到的）。"""
    best: tuple[int, Festival] | None = None
    for offset in range(-span, span + 1):
        current = day + timedelta(days=offset)
        for festival in festival_occurrences(current.year):
            if festival.day == current:
                distance = abs(offset)
                if best is None or distance < best[0]:
                    best = (distance, festival)
    return best[1] if best is not None else None


@dataclass
class FestivalHint:
    """给界面用的一小包信息（节日 + 是不是在假期里 + 离节日几天）。"""

    festival: Festival
    in_holiday: bool = False
    holiday_name: str = ""
    days_until: int | None = None
    #: 今天是节日前/后第几天：0 = 当天，负 = 节前，正 = 节后
    offset: int = 0
    #: 在"假期收尾期"里的话，剩下几天（否则 None）
    wrap_up_remaining: int | None = None

    @property
    def banner_text(self) -> str:
        return f"{self.festival.emoji} {self.festival.name} · {self.festival.greeting}"

    def timing_text(self) -> str:
        """横幅括号里那句：收心倒计时 / 还有几天 / 已过几天；当天返回空串。"""
        if self.wrap_up_remaining:
            return f"收心倒计时 {self.wrap_up_remaining} 天"
        if not self.offset:
            return ""
        if self.offset < 0:
            return f"还有 {-self.offset} 天"
        return f"已过 {self.offset} 天"


def hint_for(day: Date, calendar=None) -> FestivalHint | None:
    festival = festival_for(day, calendar)
    if festival is None:
        return None
    holiday = calendar.holiday_on(day) if calendar is not None else None
    wrap_up = calendar.wrap_up(day) if calendar is not None else None
    # 「在假期里」= 这个假期区间确实属于这个节日（按日期判定，不看名字）
    in_holiday = bool(
        holiday is not None
        and any(item.key == festival.key for item in _matching_festivals(holiday))
    )
    return FestivalHint(
        festival=festival,
        in_holiday=in_holiday,
        holiday_name=holiday.name if holiday is not None else "",
        days_until=festival.days_until(day),
        offset=(day - festival.day).days if festival.day is not None else 0,
        wrap_up_remaining=wrap_up.remaining if wrap_up is not None else None,
    )
