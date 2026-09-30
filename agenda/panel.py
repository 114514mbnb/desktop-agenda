"""桌面右侧常驻日程面板（Win11 / tkinter）。

行为：
  * 无边框、置顶、不进 Alt+Tab；可拖动标题区移动、拖右下角调整大小
  * 每 30 秒重算一次时间线（进行中/倒计时/进度会自己走）
  * 每 15 分钟自动跑一次整合（扫描 inbox 里的群通知）
  * 右键菜单：立即整合、快速录入、收起/展开、开机自启、退出
"""

from __future__ import annotations

import datetime as dt
import math
import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
#: `messagebox` / `ttk` 曾经漏了导入：`ask_end_time` 里用 `ttk.Combobox`、
#: `card_delete` 里用 `messagebox.askyesno`，两处都是**运行时才炸**的 NameError——
#: 菜单点下去什么都不发生（异常被 Tk 吞进 stderr，面板继续跑）。
#: 静态检查（用 AST 找"用了但没导入的名字"）现在会盯着这类问题。
from tkinter import messagebox, ttk

from . import theme
from .date_entry import DateEntry
from .festival import Festival, hint_for
from .festival_banner import FestivalBanner, mix_color as _mix_color
from .holidays import load_calendar
from .pipeline import Pipeline
from .timeline import (
    EMPTY_AGENDA_TEXT,
    Card,
    DaySection,
    Timeline,
    build_timeline,
    day_heading,
    empty_hint,
    footer_summary,
)
from .timetable import load_timetable

DESKTOP_POLL_MS = 5000        # 兜底巡检间隔（正常靠下面的快速轮询）
# 两个探测加起来 0.002 ms/次，40 ms 一次的开销可以忽略；40 ms 是为了让"切回主屏幕"
# 时面板回到屏幕上的延迟低到看不出来（100 ms 那版实测平均 102 ms，能感觉出来）。
FOREGROUND_POLL_MS = 40       # 前台状态快查间隔：决定"回到屏幕"的响应速度
#: 原生回调（窗口过程）攒下的待办，由这个间隔的 Tk 定时器统一执行。
#: 为什么必须绕这一道：见 `AgendaPanel._native_tick` 的注释——在窗口过程里直接调 Tk
#: 会让进程硬崩（实测：按一下剪贴板热键，面板进程当场消失，无异常、panel.log 为空）。
NATIVE_TICK_MS = 60
REFRESH_MS = 30_000           # 界面刷新（倒计时/进度）
PIPELINE_MS = 15 * 60_000     # 自动整合 inbox
#: 底部提示行平时是**空的**：用户要求把那串操作说明删掉，改成右下角一个小齿轮按钮
#: （`_build_gear`）。这一行只用来显示临时提示（拖入结果、toast）。
FOOTER_HINT = ""


def card_key(card: Card) -> tuple:
    """卡片的缓存键：**不含 progress/时间文本**这些每刷新都会变的东西。

    Card 是可变 dataclass（progress 每 30 秒都会动），直接拿它当字典键会
    `TypeError: unhashable type`，而且就算可哈希也永远命中不了缓存。
    只要这些字段一样，卡片控件就能整块复用，只重画进度条。

    必须带上 `event_id` / `course_index`：两条内容一模一样的日程（同标题同时段
    的两条通知、单双周两条同名课）是不同记录，右键"完成/删除"得打到各自那条上。
    """
    return (card.kind, card.title, card.start, card.end, card.date,
            card.location, card.people, card.notes, card.badge, card.color,
            card.tentative, card.state, card.countdown,
            card.event_id, card.course_index)


def ask_end_time(parent, title: str, start: str | None, current: str) -> str | None:
    """弹一个"选结束时间"的小窗，返回 "HH:MM" 或 None（取消）。

    时/分各一个下拉框、中间那个冒号是标签——用户不用敲冒号。
    放在模块级是因为面板和控制台都要用同一套，别各写一遍。
    """
    result: dict[str, str | None] = {"value": None}
    window = tk.Toplevel(parent)
    window.title("修改结束时间")
    window.configure(bg=theme.COLORS["bg"])
    # 先藏起来：布局尺寸和位置都算好了再显示，用户不会看到窗口先闪到左上角再跳过来。
    window.withdraw()
    try:
        window.transient(parent)
    except tk.TclError:
        pass
    window.resizable(False, False)

    tk.Label(
        window, text=title, bg=theme.COLORS["bg"], fg=theme.COLORS["text"],
        font=("Microsoft YaHei UI", 10, "bold"), anchor="w",
    ).pack(fill="x", padx=16, pady=(14, 2))
    tk.Label(
        window, text=f"开始 {start or '全天'}　｜　当前结束 {current or '未设置'}",
        bg=theme.COLORS["bg"], fg=theme.COLORS["text_faint"],
        font=("Microsoft YaHei UI", 8), anchor="w",
    ).pack(fill="x", padx=16)

    row = tk.Frame(window, bg=theme.COLORS["bg"])
    row.pack(fill="x", padx=16, pady=12)
    tk.Label(row, text="新的结束时间", bg=theme.COLORS["bg"], fg=theme.COLORS["text_dim"],
             font=("Microsoft YaHei UI", 9)).pack(side="left")
    base = current if len(current or "") == 5 else "18:00"
    hour_var = tk.StringVar(value=base[:2])
    minute_var = tk.StringVar(value=base[3:5])
    for variable, values in ((hour_var, [f"{h:02d}" for h in range(24)]),
                             (minute_var, [f"{m:02d}" for m in range(0, 60, 5)])):
        ttk.Combobox(row, textvariable=variable, values=values, width=3,
                     state="readonly", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(6, 2))
        if variable is hour_var:
            tk.Label(row, text=":", bg=theme.COLORS["bg"], fg=theme.COLORS["text"],
                     font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

    def confirm() -> None:
        result["value"] = f"{hour_var.get()}:{minute_var.get()}"
        window.destroy()

    buttons = tk.Frame(window, bg=theme.COLORS["bg"])
    buttons.pack(fill="x", padx=16, pady=(0, 14))
    tk.Button(buttons, text="保存", command=confirm, relief="flat", bd=0,
              bg=theme.COLORS["accent"], fg="#FFFFFF", padx=16, pady=6, cursor="hand2",
              font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")
    tk.Button(buttons, text="取消", command=window.destroy, relief="flat", bd=0,
              bg=theme.COLORS["card"], fg=theme.COLORS["text"], padx=16, pady=6, cursor="hand2",
              font=("Microsoft YaHei UI", 9)).pack(side="right", padx=(0, 8))
    window.bind("<Escape>", lambda _e: window.destroy())
    # 顺序很关键，三条都真踩过：
    #   1) 布局之前就 `focus_force()` → Tk 按默认尺寸把窗口映射出来，
    #      用户看到一个停在左上角、200x200 的空框；
    #   2) 只设位置不设尺寸 → 映射阶段 Tk 自己重排，对话框又掉回 +0+0；
    #   3) 面板平时沉在桌面层（不是置顶窗口）→ 它弹的对话框一起被别的窗口盖住，
    #      用户点了「修改结束时间…」只看到"什么都没发生"。
    # 所以：先让控件布局出尺寸 → 显式写全 WxH+X+Y → 再显示、置顶、抬到最前、抢焦点。
    _center_over(window, parent)
    try:
        window.attributes("-topmost", True)
        window.deiconify()
        window.lift()
        window.focus_force()
    except tk.TclError:
        try:
            window.deiconify()
        except tk.TclError:
            pass
    window.grab_set()
    parent.wait_window(window)
    return result["value"]


#: `HH:MM`（00:00–23:59）。编辑通知时人手输入，必须挡在前面。
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


#: 全屏判定的宽限期：前台窗口覆盖全屏后要**持续**这么久，才认定是"真·全屏应用"。
#: 为什么需要：截图工具（Win+Shift+S / QQ 截图 / Snipaste）的遮罩就是"覆盖全屏、
#: 位于 (0,0)"的窗口，一次误判就把面板挪到屏幕外 —— 用户正在看的东西（包括面板自己
#: 弹的窗）跟着从视野里消失。他截图给我看 bug 时正好踩到这一点。
FULLSCREEN_GRACE_MS = 1500

#: 标题区（照片铺的那一块）的名义高度，逻辑像素。
#: "框选照片范围"按 `面板宽度 : 这个高度` 锁定框的宽高比，让用户框到的就是最终看到的那一条。
#: 头部高度本身是自适应的（长了会折行），这里只是给框选用的名义值。
HEADER_STRIP_HEIGHT = 130

#: 双击判定窗口。卡片上单击=选中/取消选中、双击=看全文，而双击的**第二拍**
#: 也会被 Tk 当成一次单击发过来——不加保护的话，双击之后卡片反而变成"未选中"。
DOUBLE_CLICK_GUARD = 0.4

#: 拖动死区（像素）。面板的空白处也能拖，而按下时手会抖一两像素；
#: 没有这个门限的话，每点一次空白面板就悄悄挪一点。
DRAG_THRESHOLD = 4


def _descendants(widget) -> list:
    """`widget` 自己 + 它所有子孙控件（按创建顺序）。

    为什么需要：卡片的可点区域要覆盖**整张卡**。以前只绑了标题那一行和色条，
    用户点"备注那几行字"就没反应，他也确实抱怨了「取消选中的区域太小」。
    """
    found = [widget]
    for child in widget.winfo_children():
        found.extend(_descendants(child))
    return found


def merge_notes(existing: str | None, addition: str) -> str:
    """把一段补充内容并进原来的备注。

    短句用「；」并成一行（卡片上更像"备注"而不是一段散文）；
    多行或很长的那就以换行另起一段，免得挤成一坨看不清。原文一个字都不改。
    """
    addition = (addition or "").strip()
    existing = (existing or "").strip()
    if not existing:
        return addition
    if not addition:
        return existing
    if "\n" in addition or len(addition) > 60:
        return f"{existing}\n{addition}"
    return f"{existing}；{addition}"


def ask_edit_event(parent, *, title: str = "", date: str = "", start: str = "",
                   end: str = "", location: str = "", people: str = "",
                   notes: str = "") -> dict[str, str] | None:
    """《编辑此条通知》对话框：手动微调识别出来的内容。返回改好的字段，取消返回 None。

    为什么需要它：群通知的写法千奇百怪——呼语、周期词、区间、跟着追发的补充消息……
    解析器再全也有认不准的时候。以前用户只能"删掉重新录一遍"，现在直接改。

    字段：标题 / 日期 / 开始 / 结束 / 地点 / 人员 / 备注。
    日期用的是 `DateEntry`（月日各一个框、短横线是标签删不掉），
    和程序里其它改日期的地方保持一致——用户明确要求过"短横线不可删除"。
    校验失败**不关窗**，在下面用红字说明，用户改完再点保存。
    """
    result: dict[str, str] | None = None
    window = tk.Toplevel(parent)
    window.title("编辑此条通知")
    window.configure(bg=theme.COLORS["bg"])
    window.withdraw()                      # 布局摆好再显示，免得先闪到左上角
    try:
        window.transient(parent)
    except tk.TclError:
        pass
    window.resizable(False, False)

    colors = theme.COLORS
    body = tk.Frame(window, bg=colors["bg"])
    body.pack(fill="both", expand=True, padx=16, pady=(14, 6))
    body.columnconfigure(1, weight=1)
    row_index = 0

    def add_row(label: str):
        nonlocal row_index
        tk.Label(body, text=label, bg=colors["bg"], fg=colors["text_dim"],
                 font=("Microsoft YaHei UI", 9), anchor="w").grid(
            row=row_index, column=0, sticky="w", pady=(0, 6), padx=(0, 10))
        holder = tk.Frame(body, bg=colors["bg"])
        holder.grid(row=row_index, column=1, sticky="ew", pady=(0, 6))
        row_index += 1
        return holder

    def make_entry(holder, value: str, width: int):
        entry = tk.Entry(holder, bg=colors["card"], fg=colors["text"], relief="flat",
                         insertbackground=colors["text"], font=("Microsoft YaHei UI", 9),
                         width=width)
        entry.insert(0, value or "")
        entry.pack(side="left", ipady=3, ipadx=4, fill="x", expand=True)
        return entry

    def make_time_entry(holder, value: str):
        entry = tk.Entry(holder, bg=colors["card"], fg=colors["text"], relief="flat",
                         insertbackground=colors["text"], font=("Microsoft YaHei UI", 9),
                         width=5)
        entry.insert(0, value or "")
        entry.pack(side="left", ipady=3, ipadx=4)
        return entry

    title_entry = make_entry(add_row("标题"), title, 34)
    date_holder = add_row("日期")
    date_entry = DateEntry(date_holder, value=date, colors=colors,
                           font=("Microsoft YaHei UI", 9))
    date_entry.pack(side="left")

    time_holder = add_row("时间")
    start_entry = make_time_entry(time_holder, start)
    tk.Label(time_holder, text="起", bg=colors["bg"], fg=colors["text_faint"],
             font=("Microsoft YaHei UI", 8)).pack(side="left", padx=4)
    end_entry = make_time_entry(time_holder, end)
    tk.Label(time_holder, text="止", bg=colors["bg"],
             fg=colors["text_faint"], font=("Microsoft YaHei UI", 8)).pack(side="left", padx=4)

    location_entry = make_entry(add_row("地点"), location, 34)
    people_entry = make_entry(add_row("人员"), people, 34)

    notes_holder = add_row("备注")
    notes_text = tk.Text(notes_holder, bg=colors["card"], fg=colors["text"], relief="flat",
                         insertbackground=colors["text"], font=("Microsoft YaHei UI", 9),
                         width=34, height=4, wrap="word")
    notes_text.insert("1.0", notes or "")
    notes_text.pack(side="left", fill="both", expand=True)

    hint = tk.Label(window, text="", bg=colors["bg"], fg=theme.readable_accent("#E0555B", colors["bg"]),
                    font=("Microsoft YaHei UI", 8), anchor="w", justify="left", wraplength=320)
    hint.pack(fill="x", padx=16)

    def confirm() -> None:
        nonlocal result
        new_title = title_entry.get().strip()
        if not new_title:
            hint.configure(text="标题不能为空")
            return
        if not date_entry.is_complete():
            hint.configure(text="日期要填完整：年-月-日（例如 2026-09-28）")
            return
        new_start = start_entry.get().strip()
        new_end = end_entry.get().strip()
        for label, value in (("开始", new_start), ("结束", new_end)):
            if value and not _TIME_RE.match(value):
                hint.configure(text=f"{label}时间要写成 HH:MM（例如 09:30），现在是「{value}」")
                return
        if new_start and new_end and new_end <= new_start:
            hint.configure(text=f"结束时间要晚于开始时间（{new_start}）")
            return
        result = {
            "title": new_title,
            "date": date_entry.get(),
            "start": new_start,
            "end": new_end,
            "location": location_entry.get().strip(),
            "people": people_entry.get().strip(),
            "notes": notes_text.get("1.0", "end").strip(),
        }
        window.destroy()

    buttons = tk.Frame(window, bg=colors["bg"])
    buttons.pack(fill="x", padx=16, pady=(2, 14))
    tk.Button(buttons, text="保存", command=confirm, relief="flat", bd=0,
              bg=colors["accent"], fg="#FFFFFF", padx=16, pady=6, cursor="hand2",
              font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")
    tk.Button(buttons, text="取消", command=window.destroy, relief="flat", bd=0,
              bg=colors["card"], fg=colors["text"], padx=16, pady=6, cursor="hand2",
              font=("Microsoft YaHei UI", 9)).pack(side="right", padx=(0, 8))
    window.bind("<Escape>", lambda _e: window.destroy())
    window.bind("<Control-Return>", lambda _e: confirm())

    # 与 ask_end_time 同一套顺序：先布局 → 显式写全 WxH+X+Y → 再显示/置顶/抢焦点
    _center_over(window, parent)
    try:
        window.attributes("-topmost", True)
        window.deiconify()
        window.lift()
        window.focus_force()
    except tk.TclError:
        try:
            window.deiconify()
        except tk.TclError:
            pass
    window.grab_set()
    title_entry.focus_set()
    parent.wait_window(window)
    return result


def _center_over(window: tk.Toplevel, parent) -> None:
    """把对话框摆到父窗口附近；父窗口还没布局就退回屏幕中央。

    两条实测结论（都踩过）：
      * 只给 `+x+y` 不给尺寸时，Tk 在映射阶段会按自己的算法重排，对话框掉到屏幕左上角
        —— 实测 `263x180+0+0`，而这里算出来的是 `+138+140`。必须显式写全 `WxH+X+Y`。
      * 父窗口可能还没映射（`winfo_width()` 返回 1），这时 `(1-263)//2` 会被夹成 0，
        同样落到左上角；所以宽度不可信时改用屏幕居中。
    """
    try:
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
        if width <= 1 or height <= 1:                 # 还没映射：用请求尺寸
            width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        if pw <= 1 or ph <= 1:
            x, y = (screen_w - width) // 2, (screen_h - height) // 3
        else:
            x = parent.winfo_rootx() + max(0, (pw - width) // 2)
            y = parent.winfo_rooty() + max(0, min(80, (ph - height) // 2))
        x = max(0, min(x, max(0, screen_w - width)))
        y = max(0, min(y, max(0, screen_h - height)))
        window.geometry(f"{width}x{height}+{x}+{y}")
    except tk.TclError:
        pass


def _lower_widget(widget) -> None:
    """把控件压到同级窗口的最底层。

    为什么不能直接 `widget.lower()`：**Canvas 的 `lower` 方法被"画布条目"的命令占用了**
    （Tcl 里是 `canvas lower tagOrId ?belowThis?`），对画布调 `widget.lower()`
    会变成"把某个图元下移"，参数不对就直接抛
    `wrong # args: should be "... lower tagOrId ?belowThis?"`。
    绕到 Tcl 的窗口命令 `lower <窗口路径>` 才对。
    """
    try:
        widget.tk.call("lower", widget._w)
    except tk.TclError:
        pass


class AgendaPanel:
    #: 全局热键的登记编号（同一个进程里唯一即可）
    HOTKEY_ID = 0x4453_0001

    def __init__(
        self,
        data_dir: Path,
        *,
        width: int = 380,
        refresh_ms: int = REFRESH_MS,
        pipeline_ms: int = PIPELINE_MS,
        autostart_pipeline: bool = True,
        topmost: bool = True,
        position: tuple[int, int] | None = None,
        on_close_request=None,
        window_mode: str = "desktop",
        click_through: bool = False,
        desktop_only: bool = True,
        pet_mode: bool = True,
        front_on_start: bool = False,
        resizable: bool = True,
        hide_past: bool = True,
    ):
        self.data_dir = Path(data_dir)
        self.pipeline = Pipeline(self.data_dir)
        #: 面板是被用户**显式点起来**的（双击快捷方式 / 托盘菜单）吗？
        #: 是的话启动后临时置顶几秒 —— 桌面挂件平时在浏览器下面，
        #: 用户点完看不到它，只会得出"点了没反应"（原话）。
        self.front_on_start = bool(front_on_start)
        #: 过期的通知要不要占版面。面板默认隐藏（只回答"接下来要做什么"）；
        #: 留这个开关是为了测试能拿确定的时间看全部卡片 —— 否则用例的结果会
        #: 随"跑测试时是几点"变化（上午跑绿的、晚上跑红的）。
        self.hide_past = bool(hide_past)
        # 必须在建 Tk 之前开 DPI 感知：否则 125% 缩放下窗口会被合成器放大 1.25 倍，
        # 右对齐的面板会被推出屏幕。
        self.scale = theme.ensure_dpi_awareness()
        self.width = int(width)
        self.refresh_ms = refresh_ms
        self.pipeline_ms = pipeline_ms
        self.fixed_position = position
        self.on_close_request = on_close_request
        self.window_mode = "topmost" if topmost and window_mode == "topmost" else window_mode
        self.click_through = click_through
        self.desktop_only = desktop_only
        self.pet_mode = pet_mode
        self.resizable = resizable
        self._hwnd = 0
        self._hidden_by_watcher = False
        #: 剪贴板全局热键的登记状态（0 表示没登记；hwnd 记着登记在哪个窗口上，注销要用）
        self._hotkey_id = 0
        self._hotkey_hwnd = 0
        self._hotkey_text = ""
        #: 窗口过程里攒下的待办（热键按下了 / 系统唤醒了）——真正的处理在 _native_tick
        self._hotkey_pending = False
        self._wake_pending = False
        #: client.json 变过没有（变了就重挂热键、重套主题）
        self._config_dirty = False
        #: 用户主动收起（右键菜单"收起面板"）——此时监控不能把它拉回来
        self._user_hidden = False
        #: 是否被挪到屏幕外（"瞬间隐藏"用挪窗口实现，不是 withdraw）
        self._hidden_offscreen = False
        #: 卡片控件缓存：内容没变就复用，避免每 30 秒重建 190 多个控件
        self._card_cache: dict[tuple, tk.Frame] = {}
        #: 每张卡的时间轴/进度条画法（复用时只重画这两处）
        self._card_paint: dict[tuple, tuple] = {}
        #: 假期/调休数据（calendar.json），刷新时重读
        self._calendar_cache = None
        #: 节日彩蛋横幅（只在节日那几天存在）
        self._festival_banner: FestivalBanner | None = None
        self._festival_key: str | None = None
        #: 调试 / 截图用：把"今天"钉在某一天（None = 真的今天）。
        #: 节日彩蛋一年只出现几天，验收和测试得能把它调到台前来。
        self.today_override: dt.date | None = None

        self.root = tk.Tk()
        self.root.title("今日日程")
        self.root.configure(bg=theme.COLORS["bg"])
        self.root.overrideredirect(True)
        # 桌面模式：不做置顶窗口，像桌面小部件一样待在最底层
        self.root.attributes("-topmost", self.window_mode == "topmost")
        self.root.attributes("-toolwindow", True)

        self.font_family = theme.pick_font_family(self.root)
        self.fonts = theme.Fonts(family=self.font_family)

        # 主题必须在**建控件之前**定下来：`_build_widgets` 里所有 bg/fg 都是当场取色，
        # 先套好调色板，第一帧就是正确的颜色（不然启动瞬间会闪一下经典深色）。
        self._theme_picture = None
        self._header_photo = None
        self._theme_state = self._resolve_theme()
        self._theme_slot = ""
        self._apply_palette(self._theme_state)
        self._sync_slot()

        self._place_window()
        self._build_widgets()
        self.timeline: Timeline | None = None
        self._hidden_until: dt.datetime | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int, int] | None = None
        self._pipelines: list[str] = []
        #: 当前"选中"的通知（存 id 而不是 Card：刷新之后 Card 会换成新对象）。
        #: 选中状态下按热键 = 给这一条补充备注；没选中 = 新建通知。
        self._selected_event_id: str | None = None
        #: 上一次点选卡片的时刻（用来区分"再点一下取消"和"双击的第二拍"）
        self._last_select_at = 0.0
        #: 这次渲染出来的卡片控件，用来把选中态画上去。
        self._card_frames: list[tuple[tk.Frame, str | None]] = []
        #: 前台窗口第一次被看到"覆盖全屏"的时刻（配合 FULLSCREEN_GRACE_MS 判真伪）
        self._fullscreen_since: float | None = None

        theme.apply_windows_flourishes(self.root)
        self._show_header_photo()
        self.refresh()
        try:
            self.root.after(300, self.start_drop_target)     # 拖放接收（拖文件进来就录入）
            self.root.after(320, self.start_foreground_watch)  # 100ms 快查：回屏幕立刻出现
        except Exception:
            pass
        if autostart_pipeline:
            self.root.after(1500, self.run_pipeline)
        # 热键要等窗口真正建好（winfo_id 可用）再登记，所以排到事件循环里
        self.root.after(600, self._apply_hotkey)
        self.root.after(self.refresh_ms, self._tick)
        if self.pipeline_ms:
            self.root.after(self.pipeline_ms, self._pipeline_tick)
        self.root.after(20_000, self._reminder_tick)
        self.root.after(700, self._watch_stop_flag)
        # 面板自己的托盘图标：只要程序在跑，通知区里就该有它（用户点名要的）。
        # 排到事件循环里挂，不让它拖慢第一帧。
        self.root.after(300, self._init_tray)
        # 窗口过程里攒下的待办（热键/唤醒）在这里执行：**必须**是普通 Tk 回调上下文
        self.root.after(NATIVE_TICK_MS, self._native_tick)
        if self.window_mode != "topmost":
            self.root.after(400, self._init_desktop_layer)
            self.root.after(DESKTOP_POLL_MS, self._desktop_watch)
            if self.front_on_start:
                # 排在 `_init_desktop_layer`（400 ms）之后：等它把层压好，再提上来
                self.root.after(700, lambda: self._front_briefly())
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    # ------------------------------------------------------------------
    # 窗口与控件
    # ------------------------------------------------------------------
    def _place_window(self) -> None:
        dpi_scale = self.scale or 1.0
        # winfo_screen* 是物理像素，而 wm geometry 在 DPI 感知进程里按逻辑像素解释，
        # 两者差一个系统缩放（这台机器 1920/1536=1.25）。统一用逻辑像素布局。
        logical_w = self.root.winfo_screenwidth() / dpi_scale
        logical_h = self.root.winfo_screenheight() / dpi_scale
        max_width = max(240, logical_w - 32)
        self.width = int(min(self.width, max_width))
        height = int(logical_h * 0.72)
        if self.fixed_position is not None:
            x, y = self.fixed_position
            y = max(0, min(int(y), max(0, int(logical_h) - 120)))
            # x 也要夹：这个坐标可能是从 client.json 读回来的（上次拖到哪儿），
            # 换过显示器、拔掉副屏、改过缩放之后它可能已经在屏幕外 ——
            # 不夹的话面板会"启动就看不见"，用户只会得出"日程表自己没了"。
            # 至少留 80 px 露在屏幕内，够抓住拖回来。
            x = max(0, min(int(x), max(0, int(logical_w) - 80)))
        else:
            margin = 10
            x = max(0, int(logical_w - self.width - margin))
            y = max(0, int(logical_h * 0.10))
        self._target_x = x
        self._target_y = y
        self._target_height = height
        self.root.geometry(f"{self.width}x{height}+{x}+{y}")
        # overrideredirect 之后位置不一定立刻生效，映射完成再钉一次
        self.root.update_idletasks()
        self.root.after(60, self._pin_position)

    def _pin_position(self) -> None:
        try:
            self.root.geometry(f"{self.width}x{self._target_height}+{self._target_x}+{self._target_y}")
            self.root.update_idletasks()
            self.root.lift()
        except tk.TclError:
            pass

    def _build_widgets(self) -> None:
        colors = theme.COLORS
        # 边框：平时 1px 细边；节日期间会换成节日主色并加粗（见 _festival_frame_tick）
        outer = tk.Frame(self.root, bg=colors["border"])
        outer.pack(fill="both", expand=True)
        self.outer = outer
        self.shell = tk.Frame(outer, bg=colors["bg"])
        self.shell.pack(fill="both", expand=True, padx=self.FRAME_PAD, pady=self.FRAME_PAD)

        # 顶部：时间 + 下一项
        # 高度**不写死**：`下一项`那条遇到长标题会折行，写死高度就会把末行裁掉
        # （用户报「图中的下一项显示不全」，根子就在这儿）。交给 Tk 按内容撑开，
        # 单行时的高度和以前一样（≈112px），需要几行就长几行。
        header = tk.Frame(self.shell, bg=colors["bg_soft"])
        self.header = header
        # 照片模式的背景层：**先建**再建标签，后建的压在它上面（Tk 的叠放顺序就是创建顺序）。
        # 不用它的时候 place_forget 掉，对普通主题零影响。
        self.header_canvas = tk.Canvas(header, highlightthickness=0, bd=0, bg=colors["bg_soft"])
        self.header_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        _lower_widget(self.header_canvas)

        self.clock_label = tk.Label(
            header, text="--:--", bg=colors["bg_soft"], fg=colors["text"],
            font=self.fonts.spec(self.fonts.title + 6, "bold"), anchor="w",
        )
        self.clock_label.pack(fill="x", padx=int(14 * self.scale), pady=(int(10 * self.scale), 0))

        self.date_label = tk.Label(
            header, text="", bg=colors["bg_soft"], fg=colors["text_dim"],
            font=self.fonts.spec(self.fonts.meta), anchor="w",
        )
        self.date_label.pack(fill="x", padx=int(14 * self.scale))

        self.next_label = tk.Label(
            header, text="", bg=colors["bg_soft"], fg=colors["accent"],
            font=self.fonts.spec(self.fonts.heading, "bold"), anchor="w", justify="left",
        )
        self.next_label.pack(fill="x", padx=int(14 * self.scale), pady=(int(2 * self.scale), int(6 * self.scale)))
        # 记下这三个标签的 pack 参数：照片模式要把它们摘下来（别盖住照片），
        # 退出照片模式时再原样装回去。
        self._remember_header_pack()

        # 中部：可滚动时间线
        body = tk.Frame(self.shell, bg=colors["bg"])
        self.body = body
        self.canvas = tk.Canvas(body, bg=colors["bg"], highlightthickness=0, bd=0)
        # 滚轮条：细一点。滑块颜色要比面板底色亮一档，否则在深色面板上几乎看不见
        # （"线"色 #2A3240 跟卡片底 #1D2430 太接近，实测截图里就是一条若有若无的线）
        self.scrollbar = tk.Scrollbar(
            body, orient="vertical", command=self.canvas.yview,
            width=int(10 * self.scale), bd=0, highlightthickness=0,
            troughcolor=colors["bg_soft"], background=colors["text_faint"],
            activebackground=colors["accent"], relief="flat",
        )
        # 顺序很关键：**先 pack 滚动条**，再 pack 画布。
        # 反过来写的话 canvas 的 expand=True 会先把整行宽度吃掉，
        # 滚动条只剩 1px，肉眼看不到（实测 rect 里右边那条细线一直没出现）。
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.inner = tk.Frame(self.canvas, bg=colors["bg"])
        self._inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 底部：状态行 +（右下角）齿轮按钮
        # 高度**不能写死**：状态行是 1-3 行（数据源 / 课表 / 最近整合），
        # 写死 46px 的时候三行状态会把下面那行提示整个挤出去（踩过）。
        footer = tk.Frame(self.shell, bg=colors["bg_soft"])
        self.footer = footer
        # 状态行要**按面板宽度折行**：不设 wraplength 的话，长句子会被面板右边缘
        # 硬切（用户截图里"今日通知 0 条"只剩半截）。宽度变化时由 _apply_text_wrap 更新。
        self.status_label = tk.Label(
            footer, text="正在载入…", bg=colors["bg_soft"], fg=colors["text_faint"],
            font=self.fonts.spec(self.fonts.meta), anchor="w", justify="left",
            wraplength=self._wrap_width(),
        )
        self.status_label.pack(fill="x", padx=int(14 * self.scale), pady=(int(5 * self.scale), 0))
        # 底部这一行：左边是临时提示（拖入结果、toast），右边是齿轮按钮。
        # 平时提示是空的 —— 用户明确要求把原来那两行操作说明删掉，改成右下角一个齿轮。
        bottom = tk.Frame(footer, bg=colors["bg_soft"])
        bottom.pack(fill="x", padx=int(14 * self.scale), pady=(int(1 * self.scale), int(4 * self.scale)))
        self.hint_label = tk.Label(
            bottom, text="", bg=colors["bg_soft"], fg=colors["text_faint"],
            font=self.fonts.spec(self.fonts.badge), anchor="w", justify="left",
            wraplength=self._wrap_width() - int(26 * self.scale),
        )
        self.hint_label.pack(side="left", fill="x", expand=True)
        self._build_gear(bottom)

        # pack 的**先后顺序 = 抢空间的顺序**：Tk 的空间不够时，先 pack 的先拿到，
        # 后 pack 的被裁掉。footer 原来排在 body 后面，所以一出现节日横幅 + 收尾倒计时，
        # 底部那两行就被裁成半行（用户截图里"今日通知 0 条"只露出上半截）。
        # 现在按"固定高度优先"的顺序 pack：头部 → 底部 → 中间可伸缩区。
        header.pack(fill="x", side="top")
        footer.pack(fill="x", side="bottom")
        body.pack(fill="both", expand=True)

        # 可拖动区：标题区（时钟 / 日期 / 下一项）+ 照片模式盖在标题区上的那块画布
        # + 正文的空白处。
        #
        # 为什么必须带上 `header_canvas`：它在照片模式下铺满整块标题区，而且那时候三个
        # 标签都被摘掉了。Tk 的事件只发给指针底下的那个控件，而它的 bindtags 里**没有**
        # 父框架 —— 于是点标题区点到的是画布，绑在 header 上的拖动永远不触发，
        # 用户看到的就是"日程表拖不动了"。
        for widget in (header, self.header_canvas, self.clock_label,
                       self.date_label, self.next_label,
                       self.shell, self.body, self.canvas, self.inner):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)
        for widget in (self.shell, self.canvas, self.inner, body, self.header_canvas,
                       self.scrollbar, self.clock_label, self.date_label, self.next_label):
            widget.bind("<Button-3>", self._popup_menu)
            widget.bind("<MouseWheel>", self._on_wheel)
        # 底部状态栏单独一套菜单（打开客户端 / 快速录入 / 立即整合 / 收起 / 退出应用）。
        # 它不跟着上面的全局菜单走：这两块想干的事不一样。
        for widget in (footer, self.status_label, self.hint_label):
            widget.bind("<Button-3>", self._popup_footer_menu)
            widget.bind("<MouseWheel>", self._on_wheel)
        # 卡片是在 _render_card 里动态建的，滚轮事件统一在 root 上兜住，
        # 这样鼠标停在哪张卡上都能滚（原来每张卡各绑一次，滚动时回调过多，很卡）
        self.root.bind("<MouseWheel>", self._on_wheel)
        # ESC = 收起面板，**不是**退出进程。
        # 原来绑的是 quit()，面板一被聚焦（click_through 关着的时候就能点中它），
        # 随手一个 ESC 就把面板进程干掉了 —— 而没有任何东西会把它拉回来，
        # 用户看到的就是"桌面日程自己没了"。收起草稿是随时能叫回来的那种。
        self.root.bind("<Escape>", lambda _e: self.hide_now())
        if self.resizable:
            self._build_resize_grips()

        self._build_menu()
        self._build_footer_menu()

    # -- 右下角的小齿轮 --------------------------------------------------
    def _build_gear(self, parent) -> None:
        """日程表右下角那个小齿轮：点它就弹出日程表自己的设置/操作菜单。

        为什么画而不是用字体里的 ⚙：emoji/符号在不同字号下会变成豆腐块
        （见 `tools/font_probe.py`），Canvas 图元则永远清晰、颜色可控、还能做悬停高亮。
        """
        size = int(20 * self.scale)
        self.gear = tk.Canvas(parent, width=size, height=size, bg=theme.COLORS["bg_soft"],
                              highlightthickness=0, bd=0, cursor="hand2")
        self.gear.pack(side="right", padx=(int(6 * self.scale), 0))
        self.gear.bind("<Button-1>", self._open_gear_menu)
        self.gear.bind("<Button-3>", self._open_gear_menu)
        self.gear.bind("<Enter>", lambda _e: self._draw_gear(theme.COLORS["accent"]))
        self.gear.bind("<Leave>", lambda _e: self._draw_gear(theme.COLORS["text_dim"]))
        self._draw_gear(theme.COLORS["text_dim"])

    def _draw_gear(self, color: str) -> None:
        """在 20×20 的画布上画一个齿轮（8 个齿 + 一个圆孔）。"""
        try:
            canvas = self.gear
            canvas.delete("all")
            size = int(canvas.cget("width"))
            center = size / 2
            outer = size * 0.42
            inner = size * 0.17
            teeth = size * 0.22
            for index in range(8):
                angle = index * math.pi / 4
                canvas.create_line(
                    center + math.cos(angle) * outer * 0.72,
                    center + math.sin(angle) * outer * 0.72,
                    center + math.cos(angle) * (outer * 0.72 + teeth * 0.55),
                    center + math.sin(angle) * (outer * 0.72 + teeth * 0.55),
                    fill=color, width=max(2, int(2 * self.scale)),
                )
            canvas.create_oval(center - outer * 0.72, center - outer * 0.72,
                               center + outer * 0.72, center + outer * 0.72,
                               outline=color, width=max(2, int(2 * self.scale)))
            canvas.create_oval(center - inner, center - inner, center + inner, center + inner,
                               outline=color, width=max(1, int(1 * self.scale)))
        except tk.TclError:
            pass

    def _open_gear_menu(self, event=None) -> None:
        """齿轮菜单：叫出客户端设置 / 打开客户端 / 快速录入 / 立即整合 / 刷新 / 收起 / 退出。"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="日程表设置…", command=self.open_settings)
        menu.add_separator()
        menu.add_command(label="打开客户端窗口", command=self.open_console)
        menu.add_command(label="快速录入群消息…", command=self.quick_entry)
        menu.add_command(label="立即整合群通知", command=self.run_pipeline)
        menu.add_separator()
        menu.add_command(label="刷新界面", command=self.refresh)
        menu.add_command(label="收起面板", command=self.hide_now)
        menu.add_separator()
        menu.add_command(label="退出应用（面板与托盘一并退出）", command=self.quit_app)
        x = event.x_root if event is not None else self.gear.winfo_rootx()
        y = event.y_root if event is not None else self.gear.winfo_rooty()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def open_settings(self) -> None:
        """请客户端把窗口叫出来并切到「设置」页（齿轮里的第一项）。"""
        try:
            from .client_app import AppController
            controller = AppController(self.data_dir)
            if controller.request_settings():
                self._toast("已打开客户端设置")
            elif controller.start_console():
                # 客户端是**刚被拉起来**的：它启动后停在「通知」页，
                # 用户点的是「日程表设置…」，所以补一条请求让它切到「设置」页。
                # `start_console` 返回 True 时 client.lock 已经写好，这条请求必定有人接。
                controller.request_settings()
                self._toast("客户端已启动，正在打开设置")
            else:
                self._toast("客户端未能启动，请尝试桌面上的「桌面日程」快捷方式")
        except Exception as error:  # noqa: BLE001
            self._toast(f"无法唤起客户端：{error}")

    def _build_resize_grips(self) -> None:
        """在右边和下边加看不见的拖拽条，让面板能改大小（内容就不会被裁）。

        鼠标穿透开着时本来就点不到，所以这里只在非穿透场景有用；
        但尺寸持久化照常工作。
        """
        grip = int(6 * self.scale)
        edge = tk.Frame(self.root, bg=theme.COLORS["bg"], cursor="sb_h_double_arrow")
        edge.place(relx=1.0, rely=0.0, relheight=1.0, width=grip, anchor="ne")
        edge.bind("<Button-1>", self._start_resize)
        edge.bind("<B1-Motion>", self._do_resize)
        self._resize_edge = edge

        corner = tk.Frame(self.root, bg=theme.COLORS["bg"], cursor="size_nw_se")
        corner.place(relx=1.0, rely=1.0, width=grip * 2, height=grip * 2, anchor="se")
        corner.bind("<Button-1>", self._start_resize)
        corner.bind("<B1-Motion>", self._do_resize)
        self._resize_corner = corner

    def _build_menu(self) -> None:
        """右键菜单——**这是用户主动让面板消失的唯一途径**（客户端也可以）。

        面板不会因为"显示桌面"/Win+D 而消失；只有全屏应用会让它临时隐身。
        """
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="打开客户端窗口", command=self.open_console)
        self.menu.add_command(label="立即整合群通知", command=self.run_pipeline)
        self.menu.add_command(label="快速录入群消息…", command=self.quick_entry)
        # 热键的"可见入口"：菜单文案里带上当前组合，用户不用去设置里翻
        self.menu.add_command(label="从剪贴板录入通知", command=self.ingest_clipboard)
        self._clip_index = self.menu.index("end")
        self.menu.add_separator()
        self.menu.add_command(label="收起面板（可从客户端或托盘重新打开）", command=self.hide_now)
        self.menu.add_command(label="收起 1 小时", command=self.hide_for_hour)
        self.menu.add_command(label="刷新界面", command=self.refresh)
        self.menu.add_separator()
        self.menu.add_command(label="退出面板", command=self.quit)

    def open_console(self) -> None:
        """把客户端控制台叫出来（它可能被最小化到托盘了）。

        这是面板上的第二条"找回控制台"的路：万一托盘图标找不到、
        快捷方式也被单实例挡了，用户右键面板还能把控制台叫回来。
        客户端压根没在跑（面板是单独启动的）时，顺手把它拉起来。
        """
        try:
            from .client_app import AppController
            controller = AppController(self.data_dir)
            if controller.request_show():
                self._toast("已请求客户端显示窗口")
            elif controller.start_console():
                self._toast("客户端已启动")
                # 控制台一起来就会自己挂托盘图标，面板这个该撤了（见 panel_tray）
                self._sync_tray()
            else:
                self._toast("客户端未能启动，请尝试桌面上的「桌面日程」快捷方式")
        except Exception as error:  # noqa: BLE001
            self._toast(f"无法唤起客户端：{error}")

    # -- 面板自己的托盘图标 ----------------------------------------------
    def _init_tray(self) -> None:
        """挂上面板自己的托盘图标。

        用户要求：「托盘图标要一直在：只要程序在跑（哪怕只有面板），
        任务栏右下角就有它的图标，点它能开控制台」。面板才是常驻进程
        （开机自启拉的是它），所以图标由面板挂；控制台起来了就交接给它
        （细节和理由见 `agenda/panel_tray.py`）。
        """
        from .panel_tray import TRAY_SYNC_MS, PanelTray

        icon = self.data_dir / "agenda.ico"
        self._tray = PanelTray(
            title="桌面日程",
            icon_path=icon if icon.is_file() else None,
            on_action=self._tray_action,
            client_running=self._console_running,
        )
        self._sync_tray()
        try:
            self._tray_job = self.root.after(TRAY_SYNC_MS, self._tray_tick)
        except tk.TclError:
            self._tray_job = None

    def _sync_tray(self) -> None:
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.sync()

    def _tray_tick(self) -> None:
        from .panel_tray import TRAY_SYNC_MS

        if getattr(self, "_closing", False):
            return
        self._sync_tray()
        try:
            self._tray_job = self.root.after(TRAY_SYNC_MS, self._tray_tick)
        except tk.TclError:
            pass

    def _console_running(self) -> bool:
        """控制台在不在跑（它在跑就由它挂托盘图标，避免通知区里两个一样的图标）。"""
        try:
            from .client_app import AppController
            return AppController(self.data_dir).client_pid() is not None
        except Exception:                                    # noqa: BLE001
            return False

    def _tray_action(self, command: int) -> None:
        """托盘线程回调过来的菜单项：**排到主线程**再做（Tk 不能跨线程碰）。"""
        try:
            self.root.after(0, lambda: self._handle_tray(command))
        except tk.TclError:
            pass

    def _handle_tray(self, command: int) -> None:
        from . import tray as tray_mod

        ids = tray_mod.command_ids()
        if command == ids["open_console"]:
            self.open_console()
        elif command == ids["open_panel"]:
            self.show_now(front=True)
        elif command == ids["close_panel"]:
            self.hide_instant()
        elif command == ids["quick_entry"]:
            self.quick_entry()
        elif command == ids["merge"]:
            self.run_pipeline()
        elif command == ids["quit"]:
            self.quit_app()

    def tray_balloon(self, title: str, message: str, *, warning: bool = False) -> bool:
        """有托盘图标就弹一个气泡（用于"点了没反应"的回执）。"""
        tray = getattr(self, "_tray", None)
        if tray is None:
            return False
        return tray.balloon(title, message, warning=warning)

    # -- 单条日程的右键操作 ----------------------------------------------
    def _card_menu(self, card: Card, event) -> None:
        """某一条日程的右键菜单：编辑 / 补充 / 完成 / 改结束时间 / 复制 / 删除。"""
        menu = tk.Menu(self.root, tearoff=0)
        head = card.title if len(card.title) <= 18 else card.title[:17] + "…"
        menu.add_command(label=f"—— {head} ——", state="disabled")
        menu.add_separator()
        if card.kind == "event":
            menu.add_command(label="编辑此条通知…", command=lambda: self.card_edit(card))
            menu.add_command(label="选中此条（下次按热键 = 补充备注）",
                             command=lambda: self.select_card(card))
        else:
            menu.add_command(label="编辑此条通知…（课程请到客户端改）", state="disabled")
        menu.add_command(label="标记为已完成", command=lambda: self.card_done(card))
        menu.add_command(label="修改结束时间…", command=lambda: self.card_edit_end(card))
        menu.add_command(label="复制内容", command=lambda: self.card_copy(card))
        menu.add_separator()
        menu.add_command(label="删除此条…", command=lambda: self.card_delete(card))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _find_event(self, event_id: str | None):
        """按 id 取回事件（卡片是"快照"，直接改卡片不会落盘）。"""
        if not event_id:
            return None
        from .store import EventStore

        store_path = self.pipeline.store_path
        try:
            store = EventStore.load(store_path) if store_path.exists() else EventStore(store_path)
        except Exception:  # noqa: BLE001
            return None
        return next((event for event in store.events if event.id == event_id), None)

    def card_edit(self, card: Card) -> None:
        """《编辑此条通知》：手动微调解析出来的内容。

        为什么需要：群通知写法千奇百怪，解析器再全也有认不准的时候。
        以前只能"删掉重新录一遍"，现在可以直接改标题/日期/时间/地点/人员/备注。
        课程不走这里——课时表在客户端「课程表」页统一维护，改一条会影响所有周次，
        入口分开更不容易出错。
        """
        if card.kind == "course":
            self._toast("课程请到客户端「课程表」页修改")
            return
        target = self._find_event(card.event_id)
        if target is None:
            self._toast("这条日程已经不在数据里了，先刷新界面")
            return
        edited = ask_edit_event(
            self.root,
            title=target.title,
            date=target.date,
            start=target.start or "",
            end=target.end or "",
            location=target.location or "",
            people="、".join(target.people or ()),
            notes=target.notes or "",
        )
        if edited is None:
            return
        from .store import EventStore

        store_path = self.pipeline.store_path
        try:
            store = EventStore.load(store_path) if store_path.exists() else EventStore(store_path)
            fresh = next((event for event in store.events if event.id == target.id), None)
            if fresh is None:
                self._toast("这条日程已经不在数据里了")
                return
            fresh.title = edited["title"]
            fresh.date = edited["date"]
            fresh.start = edited["start"] or None
            fresh.end = edited["end"] or None
            fresh.location = edited["location"] or None
            fresh.people = tuple(
                part for part in re.split(r"[、,，;；\s]+", edited["people"]) if part
            )
            fresh.notes = edited["notes"] or None
            fresh.updated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 人手改过的内容不该被下一轮整合覆盖掉：留个记号，
            # 也让"日期待确认"这类自动标记随着人工确认一起消失。
            if fresh.date_source in ("notice-fallback", "relative"):
                fresh.date_source = "manual"
            store.save()
        except Exception as error:  # noqa: BLE001
            self._toast(f"保存失败：{error}")
            return
        self.refresh()
        self._toast(f"已保存：{edited['title']}")

    def select_card(self, card: Card) -> None:
        """把某条通知设为"当前选中"。

        选中之后按热键 = 给这一条**补充备注**；没有选中时按热键 = 新建一条通知。
        （用户要求：这条规则只对通知生效，对课表无效——课程卡片选不上。）
        """
        if card.kind != "event" or not card.event_id:
            # 课程点一下不该弹提示（用户只是想看看这张卡）：
            # 只有"本来选中着一条通知、现在点到课程上"才需要说明为什么选不中。
            had = self._selected_event_id is not None
            self._clear_selection()
            if had:
                self._toast("课程不能作为补充对象，已取消选中")
            return
        if self._selected_event_id == card.event_id:
            now = time.monotonic()
            if now - self._last_select_at < DOUBLE_CLICK_GUARD:
                return              # 这是双击的第二拍，保持选中，别来回跳
            self._clear_selection()
            self._toast("已取消选中")
            self._last_select_at = now
            return
        self._selected_event_id = card.event_id
        self._paint_selection()
        self._toast(f"已选中：{card.title}")
        self._last_select_at = time.monotonic()

    def _clear_selection(self) -> None:
        """取消选中，并把跟着它开的详情窗一起收掉。

        用户原话：「取消选中时事项详情的窗口不会自动关闭」——留着那个窗会让人以为
        还没取消选中（它和卡片是一对的）。
        """
        if self._selected_event_id is None:
            return
        self._selected_event_id = None
        self._paint_selection()
        self.close_card_detail()

    def _selected_event(self):
        """当前选中的通知（课程 / 没选中 / 已被删掉 → None）。"""
        return self._find_event(self._selected_event_id)

    def _paint_card_selection(self, frame, event_id: str | None) -> None:
        """给一张卡画选中态（选中 = 一圈强调色边框）。"""
        try:
            if event_id and event_id == self._selected_event_id:
                frame.configure(highlightbackground=theme.COLORS["accent"],
                                highlightcolor=theme.COLORS["accent"],
                                highlightthickness=2)
            else:
                frame.configure(highlightthickness=0)
        except tk.TclError:
            pass

    def _paint_selection(self) -> None:
        """重建卡片之后也要能还原选中态，所以按 id 判断、不认控件。"""
        for frame, event_id in getattr(self, "_card_frames", []):
            self._paint_card_selection(frame, event_id)

    def _append_note(self, event, addition: str) -> None:
        """把一段文字并进某条通知的备注里。"""
        from .store import EventStore

        store_path = self.pipeline.store_path
        store = EventStore.load(store_path) if store_path.exists() else EventStore(store_path)
        fresh = next((item for item in store.events if item.id == event.id), None)
        if fresh is None:
            self._toast("这条通知已经不在数据里了")
            return
        fresh.notes = merge_notes(fresh.notes, addition)
        fresh.updated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store.save()
        self.refresh()
        self._toast(f"已补充到「{fresh.title}」")

    def card_done(self, card: Card) -> None:
        """"这件事办完了" —— 从日程里去掉。

        和"删除"的区别只在说法上（一个像完成任务、一个像删数据），
        落到数据层都是把这条从 events.json 里移除；课程则不受影响。
        """
        if card.kind == "course":
            self._toast("课程不可标记完成；如需调整时间，请使用「修改结束时间」")
            return
        if not card.event_id:
            self._toast("该条目为课程，没有可完成的通知记录")
            return
        if self._remove_event(card.event_id):
            self._toast(f"已完成：{card.title}")
            self.refresh()

    def _raised(self):
        """临时把面板抬到最前，供 panel 自己弹的模态对话框使用（上下文管理器）。

        为什么需要：面板默认沉在桌面层（不是置顶窗口），它弹的 `messagebox` 会**连同
        面板一起**压在别的窗口下面——用户点了「删除此条…」既看不到确认框、也无法回答，
        表现就是"这个菜单坏了"（实测桌面模式下对话框 `-topmost` 为 0）。
        用 `with self._raised():` 包住对话框调用即可，退出时恢复原来的图层设置。
        """
        import contextlib

        @contextlib.contextmanager
        def _manager():
            try:
                was = bool(self.root.attributes("-topmost"))
            except tk.TclError:
                was = False
            try:
                self.root.attributes("-topmost", True)
                self.root.lift()
            except tk.TclError:
                pass
            try:
                yield
            finally:
                try:
                    self.root.attributes("-topmost", was or self.window_mode == "topmost")
                except tk.TclError:
                    pass
        return _manager()

    def card_delete(self, card: Card) -> None:
        """彻底删除这一条（会问一次）。"""
        if card.kind == "course":
            self._toast("课程请在客户端「课程表」页删除")
            return
        if not card.event_id:
            return
        with self._raised():        # 不抬起来的话确认框会被别的窗口盖住（见 _raised）
            confirmed = messagebox.askyesno("删除日程", f"确定删除「{card.title}」？",
                                            parent=self.root)
        if not confirmed:
            return
        if self._remove_event(card.event_id):
            self._toast(f"已删除：{card.title}")
            self.refresh()

    def _remove_event(self, event_id: str) -> bool:
        """从 events.json 里移掉一条。

        提醒名单（config.reminded）里的键是 `日期|时间|标题`，跟 event id 对不上，
        没法精确对应到这一条——所以这里只做长度修剪，不做语义删除
        （删错会让别的通知重复弹窗，比留个过期键更糟）。
        """
        from .store import EventStore

        path = self.pipeline.store_path
        try:
            store = EventStore.load(path) if path.exists() else EventStore(path)
        except Exception as error:  # noqa: BLE001
            self._toast(f"日程库读取失败：{error}")
            return False
        if not store.remove(event_id):
            return False
        store.save()
        config = self._load_client_config()
        if config is not None and len(config.reminded) > 80:
            config.reminded = config.reminded[-80:]
            self._set_client_config(config)
        return True

    def card_copy(self, card: Card) -> None:
        parts = [card.title, card.time_label, card.date]
        if card.location:
            parts.append(card.location)
        if card.people:
            parts.append(card.people)
        if card.notes:
            parts.append(card.notes)
        text = " | ".join(part for part in parts if part)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._toast("已复制到剪贴板")
        except tk.TclError:
            self._toast("复制失败")

    def card_edit_end(self, card: Card) -> None:
        """改这一条的结束时间。

        通知：直接改 events.json 里的 end。
        课程：只给这一条写 `endTime` 覆盖值（不动全校课时表——课时表是作息，
        改它会连带影响所有课）。
        """
        new_end = ask_end_time(self.root, card.title, card.start, card.end or "")
        if not new_end:
            return
        ok, message = self._apply_end_time(card, new_end)
        self._toast(message)
        if ok:
            self.refresh()

    def _apply_end_time(self, card: Card, new_end: str) -> tuple[bool, str]:
        """把新的结束时间写回对应的数据文件。返回 (是否成功, 给用户看的话)。"""
        import json
        import re

        if not re.fullmatch(r"\d{2}:\d{2}", new_end):
            return False, f"时间格式不对：{new_end}"
        if card.start and new_end <= card.start:
            return False, f"结束时间要晚于开始时间（{card.start}）"

        if card.kind == "course":
            if card.course_index is None:
                return False, "未能在课表中定位该课程"
            path = self.data_dir / "timetable.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                courses = payload.get("courses") or []
                if not 0 <= card.course_index < len(courses):
                    return False, "课表已发生变化，请刷新后重试"
                courses[card.course_index]["endTime"] = new_end
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as error:  # noqa: BLE001
                return False, f"写入课表失败：{error}"
            return True, f"{card.title} 结束时间改为 {new_end}"

        if not card.event_id:
            return False, "该条目没有可修改的记录"
        from .store import EventStore

        store_path = self.pipeline.store_path
        try:
            store = EventStore.load(store_path) if store_path.exists() else EventStore(store_path)
            target = next((event for event in store.events if event.id == card.event_id), None)
            if target is None:
                return False, "该日程已不存在"
            target.end = new_end
            store.save()
        except Exception as error:  # noqa: BLE001
            return False, f"写入日程失败：{error}"
        return True, f"{card.title} 结束时间改为 {new_end}"

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _start_drag(self, event) -> None:
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())
        # 记下按下点，配合 DRAG_THRESHOLD 判"这是点击还是拖动"
        self._drag_press = (event.x_root, event.y_root)
        self._drag_moved = False

    def _do_drag(self, event) -> None:
        if self._drag_origin is None:
            return
        if not self._drag_moved:
            press = getattr(self, "_drag_press", None)
            if press is not None:
                moved = max(abs(event.x_root - press[0]), abs(event.y_root - press[1]))
                if moved < DRAG_THRESHOLD:
                    return
                # 越过死区才真正开始拖：把原点重新锚在当前位置，
                # 否则面板会"跳"过那一小段（点击时的抖动也不该让它漂移）
                self._drag_origin = (event.x_root - self.root.winfo_x(),
                                     event.y_root - self.root.winfo_y())
            self._drag_moved = True
        x = event.x_root - self._drag_origin[0]
        y = event.y_root - self._drag_origin[1]
        self.root.geometry(f"+{x}+{y}")
        # 记住新位置：全屏隐身之后要用它把窗口挪回来（show_now）
        self._target_x, self._target_y = int(x), int(y)

    def _on_wheel(self, event) -> None:
        """滚轮翻页。

        性能关键：`yview_scroll(units)` 让 Canvas 内部一次搞定，
        不要循环调 `yview_moveto` 或逐卡重排——实测那样滚十几张卡就会明显卡顿。
        """
        try:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        except tk.TclError:
            pass
        return "break"          # 别再冒泡到别的控件，省一次派发

    def _on_inner_configure(self, _event=None) -> None:
        """内容尺寸变了才重算滚动区域。

        踩过的坑：每个卡片自己 bind 一次 <Configure>，滚动时几十个回调全被触发，
        就成了"滚轮很卡"。现在只在这里算一次。
        """
        try:
            region = self.canvas.bbox("all")
            if region != getattr(self, "_scroll_region", None):
                self._scroll_region = region
                self.canvas.configure(scrollregion=region)
            self._snap_to_top()
        except tk.TclError:
            pass

    def _on_canvas_configure(self, event) -> None:
        try:
            self.canvas.itemconfigure(self._inner_id, width=event.width)
            self._snap_to_top()
        except tk.TclError:
            pass

    def _snap_to_top(self) -> None:
        """把时间线钉在滚动区**最上方**。

        用户原话：「为什么我的日程安排不是处于滚轮条的最上方？不合逻辑。」
        他截图里内容跑到画布下半、上面空了一大块。Tk 在"内容比画布矮"时的对齐
        时机不好稳定复现（我试过新建、改尺寸、内容由多变少、真实事件循环四种序列，
        都是紧贴顶部），所以这里不猜：窗口项固定回 (0,0)、视口回到顶部，
        并且**延到 idle 再钉一次**——不管 Tk 中途把它摆到了哪儿，最后都会被掰回来。
        """
        try:
            self.canvas.coords(self._inner_id, 0, 0)
            self.canvas.yview_moveto(0.0)
        except tk.TclError:
            pass
        try:
            self.root.after_idle(self._snap_to_top_now)
        except tk.TclError:
            pass

    def _snap_to_top_now(self) -> None:
        try:
            self.canvas.coords(self._inner_id, 0, 0)
            self.canvas.yview_moveto(0.0)
        except tk.TclError:
            pass

    # -- 拖拽缩放 --------------------------------------------------------
    def _start_resize(self, event) -> None:
        self._resize_origin = (event.x_root, event.y_root, self.root.winfo_width())

    def _wrap_width(self) -> int:
        """底部文字能用的宽度（面板宽度 - 左右各 14px 内边距）。"""
        return max(160, int(self.width) - int(30 * self.scale))

    def _apply_text_wrap(self) -> None:
        """面板宽度变了：把底部两行的折行宽度同步过去。"""
        width = self._wrap_width()
        for label in (getattr(self, "status_label", None), getattr(self, "hint_label", None)):
            if label is None:
                continue
            try:
                label.configure(wraplength=width)
            except tk.TclError:
                pass

    def _do_resize(self, event) -> None:
        if self._resize_origin is None:
            return
        start_x, _, start_width = self._resize_origin
        new_width = max(int(260 * self.scale), start_width + (event.x_root - start_x))
        height = self.root.winfo_height()
        self.width = new_width
        self.root.geometry(f"{new_width}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self._apply_text_wrap()
        # 照片模式的背景要跟着新宽度重新缩放，否则会被拉伸或留白
        if self._theme_picture is not None:
            self._show_header_photo()
        # 宽度变了要重排卡片（换行位置跟着变）
        self.refresh()

    def _end_drag(self, _event=None) -> None:
        """松开鼠标：真的拖过才把新位置落盘。

        为什么在这里存而不是只在退出时存（`quit()` 里那次是原来的唯一一处）：
        面板被人强杀、机器直接关机时，那一次根本没机会跑 —— 用户拖到哪儿全白费，
        下次启动又回到默认位置。
        """
        if getattr(self, "_drag_moved", False):
            self._save_geometry()
        self._drag_moved = False
        self._drag_origin = None

    def _save_geometry(self) -> None:
        """记住面板大小与位置，下次启动沿用。"""
        config = self._load_client_config()
        if config is None:
            return
        try:
            config.panel_width = int(self.root.winfo_width())
            config.panel_x = int(self.root.winfo_x())
            config.panel_y = int(self.root.winfo_y())
            config.save(self.data_dir)
            # 这是**自己**写的配置，别让 700ms 的巡检把它当成"用户在控制台改了设置"：
            # 否则每拖一次面板就会重挂热键、重套主题（还会顺带刷一次界面）。
            # 置空之后下一次 `_data_changed()` 会返回"没变化"并重新取基线。
            self._data_stamps = None
        except Exception:
            pass

    def _popup_menu(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def hide_now(self) -> None:
        """用户主动收起面板（右键菜单）：**这是唯一让面板消失的用户操作**。

        标记 `_user_hidden`，这样"显示桌面"监控不会把它又拉回来。
        """
        self._user_hidden = True
        self._hidden_until = None
        self.hide_instant()

    def hide_for_hour(self) -> None:
        self._user_hidden = True
        self._hidden_until = dt.datetime.now() + dt.timedelta(hours=1)
        self.hide_instant()

    def quick_entry(self) -> None:
        from .quick_entry import QuickEntryDialog
        QuickEntryDialog(self)

    # -- 拖放接收 --------------------------------------------------------
    def start_drop_target(self) -> None:
        """开一个隐藏窗口接收"拖到面板上"的文件/文本。

        为什么另起窗口而不是在面板窗口上收：动 Tk 自己的窗口过程风险太大
        （见 dropzone 模块开头的说明）。这里只多一个 10x10 的隐藏窗口，代价可忽略。
        """
        if getattr(self, "_dropzone", None) is not None:
            return
        try:
            from .dropzone import DropTarget
        except Exception:
            return
        self._dropzone = DropTarget(self.on_dropped_files)
        if not self._dropzone.create():
            return
        self._pump_dropzone()

    def _pump_dropzone(self) -> None:
        """靠 Tk 的 after 把接收窗口的消息抽出来处理（同线程，回调里能安全动界面）。"""
        if getattr(self, "_closing", False):
            return
        zone = getattr(self, "_dropzone", None)
        if zone is not None:
            try:
                zone.pump()
            except Exception:
                pass
        self.root.after(120, self._pump_dropzone)

    def start_foreground_watch(self) -> None:
        """快速轮询前台状态：回到屏幕时尽量"立刻"把面板放出来。

        为什么是轮询而不是事件钩子：`SetWinEventHook` 的 ctypes 回调由系统线程调用，
        没有 GIL，碰 Python 对象会当场把进程搞崩（实测复现过，
        详见 `dropzone` 模块里的结论）。而两个探测加起来只要 0.01 ms，
        100 ms 轮询一次的开销可以忽略——用几乎为零的成本换来无风险的响应速度。
        """
        if getattr(self, "_fg_job", None) is not None:
            return
        self._fg_poll()

    def _fg_poll(self) -> None:
        if getattr(self, "_closing", False):
            return
        self._apply_foreground_change()
        self._fg_job = self.root.after(FOREGROUND_POLL_MS, self._fg_poll)

    def _has_open_dialog(self) -> bool:
        """面板自己弹的窗口（详情 / 编辑通知 / 修改结束时间 / 提醒）有没有开着的。

        开着就**绝不能**把面板挪走：用户正在那个窗里操作。
        实测：面板被挪到 -4000,-4000 时详情窗本身还在原处（不会跟着消失），
        但面板一走，用户就没法再跟它互动，看起来就是"弹窗没了"。
        """
        root = getattr(self, "root", None)
        if root is None:
            return False
        try:
            for child in root.winfo_children():
                if isinstance(child, tk.Toplevel) and child.winfo_ismapped():
                    return True
        except Exception:  # noqa: BLE001  纯探测，任何异常都当作"没有弹窗"
            return False
        return False

    def _is_really_fullscreen(self) -> bool:
        """判定"真·全屏应用在前台"。

        两条收紧（都为了不误伤）：
          * 有自己弹的窗开着 → 不算（见 `_has_open_dialog`）；
          * 覆盖全屏要**持续** `FULLSCREEN_GRACE_MS` 才算，一闪而过的遮罩不算。
        """
        if self._has_open_dialog():
            self._fullscreen_since = None
            return False
        try:
            from . import winlayer
            fullscreen = winlayer.fullscreen_foreground()
        except Exception:  # noqa: BLE001
            return False
        if not fullscreen:
            self._fullscreen_since = None
            return False
        now = time.monotonic()
        since = getattr(self, "_fullscreen_since", None)
        if since is None:
            self._fullscreen_since = now
            return False
        return (now - since) * 1000 >= FULLSCREEN_GRACE_MS

    def _apply_foreground_change(self) -> None:
        """按当前前台状态调整面板（用户切回桌面时几乎无感地出现）。"""
        if getattr(self, "_closing", False):
            return
        try:
            from . import winlayer
            hidden = self._hidden_by_watcher
            # 「常驻待机」模式（desktop_only=False）：全屏应用在前台也不隐身，
            # 面板一直待在那儿。给游戏党之外的用户用（比如双屏、或者就想一直看见）。
            if self.desktop_only and self._is_really_fullscreen():
                if not hidden:
                    # 挪到屏幕外而不是 withdraw：回来时才能"瞬间"（见 show_now）
                    self.hide_instant()
                    self._hidden_by_watcher = True
                return
            if hidden:
                self.show_now()
                self._hidden_by_watcher = False
            if not self._user_hidden and self._hwnd and not self._front_active():
                # 被"显示桌面"最小化了 → 还原（无论前台是不是桌面）
                # （`_front_active()`：用户显式要求显示时面板被临时置顶，
                #   这几秒内不许巡检把它压回底层，否则用户刚看到就没了）
                if winlayer.is_minimized(self._hwnd):
                    winlayer.restore_window(self._hwnd)
                    if self._hidden_offscreen:
                        self.show_now()
                if winlayer.desktop_is_foreground():
                    # 宠物模式：浮到普通窗口最上层；否则安静待在底层
                    if self.pet_mode:
                        winlayer.raise_to_top_of_normal(self._hwnd)
                    else:
                        winlayer.send_to_bottom(self._hwnd)
                        winlayer.set_topmost(self._hwnd, False)
        except Exception:
            pass

    def on_dropped_files(self, paths: list[str]) -> None:
        """有人往面板上丢了东西：文件按后缀分派，文件夹递归找文本。"""
        from .dropzone import TEXT_SUFFIXES, read_text_file

        files: list[str] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                files.extend(str(child) for child in sorted(path.rglob("*"))
                             if child.is_file() and child.suffix.lower() in TEXT_SUFFIXES)
            elif path.is_file():
                files.append(str(path))
        if not files:
            self._toast("无法识别拖入内容：请拖入 .txt / .html / .ics 文件，或直接拖入文件夹")
            return

        notices: list[Path] = []
        courses: list[Path] = []
        for name in files:
            suffix = Path(name).suffix.lower()
            if suffix == ".ics":
                courses.append(Path(name))
            elif suffix == ".pdf":
                courses.append(Path(name))
            elif suffix in TEXT_SUFFIXES:
                notices.append(Path(name))

        parts: list[str] = []
        if notices:
            text = "\n\n".join(read_text_file(path) for path in notices)
            try:
                report = self.pipeline.ingest_text(
                    text, group=None, source_label=f"拖入：{notices[0].name}",
                )
                parts.append(f"通知 {report.added} 条新增 / {report.updated} 条更新")
                self.refresh()
            except Exception as error:  # noqa: BLE001
                parts.append(f"通知解析失败：{error}")
        if courses:
            from .file_import import import_path
            outcome = import_path(courses[0])
            if outcome.count:
                self._review_dropped_courses(outcome, courses[0])
                parts.append(f"课表识别 {outcome.count} 条，待确认")
            else:
                parts.append("未能识别课表：" + ("；".join(outcome.warnings) or "格式不支持"))
        self._toast("　".join(parts) if parts else "没有可提取的内容")

    def _review_dropped_courses(self, outcome, path) -> None:
        """拖进来的课表也走可编辑确认窗，确认后才落盘。"""
        import json

        from .course_review import CourseReviewDialog, rows_to_courses
        from .eas.importer import save_timetable

        try:
            payload = json.loads((self.data_dir / "timetable.json").read_text(encoding="utf-8"))
        except Exception:
            payload = {}

        def confirmed(rows: list[dict]) -> None:
            payload["courses"] = rows_to_courses(rows)
            payload.setdefault("termStart", outcome.term_start or "2026-09-07")
            if outcome.periods and not payload.get("periods"):
                payload["periods"] = outcome.periods
            payload["note"] = f"来源：拖入 {path.name}（已人工确认）"
            save_timetable(self.data_dir, payload)
            self.refresh()
            self._toast(f"课表已导入 {len(rows)} 门")

        CourseReviewDialog(
            self.root, outcome.courses, source=f"拖入：{path.name}",
            on_confirm=confirmed, term_start=outcome.term_start,
        )

    def _toast(self, message: str, *, seconds: int = 6) -> None:
        """面板底部一行临时提示（拖放结果反馈用）。"""
        try:
            self.hint_label.configure(text=message)
            if getattr(self, "_toast_job", None) is not None:
                self.root.after_cancel(self._toast_job)
            self._toast_job = self.root.after(
                seconds * 1000,
                lambda: self.hint_label.configure(text=FOOTER_HINT))
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # 主题：经典深色 / 随时刻 / 我的照片
    # ------------------------------------------------------------------
    def _resolve_theme(self) -> dict:
        """按配置算出此刻该用的主题：模式、调色板、照片（照片模式才有）。"""
        from . import backdrop, palettes

        config = self._load_client_config()
        mode = palettes.normalize_mode(getattr(config, "theme_mode", None) if config else None)
        picture = None
        photo_palette = None
        note = ""
        if mode == "auto":
            note = palettes.SLOT_LABELS[palettes.slot_for()]
        elif mode == "photo":
            picture = backdrop.load_background(self.data_dir, max_width=max(360, self.width))
            if picture is None:
                # 照片被删了/还没选：退回经典深色，而不是给出一张没有配色的界面
                mode = "classic"
                note = "照片不可用，已临时使用经典深色"
            else:
                photo_palette = backdrop.palette_from_image(picture)
        palette = palettes.palette_for(mode, photo_palette=photo_palette)
        return {"mode": mode, "palette": palette, "picture": picture, "note": note}

    def _apply_palette(self, state: dict) -> None:
        theme.apply_palette(state["palette"])
        self._theme_picture = state.get("picture")

    def _apply_theme(self, *, force: bool = False) -> None:
        """重新解析主题并**当场**套用到已有控件上。

        什么时候调：
          * 用户在控制台改了主题（`client.json` 变动 → `_watch_stop_flag`）；
          * 「随时刻」模式下跨过时段边界（`_tick` 里检查）；
          * 面板启动时（在建控件之前，见 `__init__`）。
        """
        state = self._resolve_theme()
        changed = force or state["mode"] != self._theme_state.get("mode") \
            or state["palette"] != self._theme_state.get("palette")
        self._theme_state = state
        self._apply_palette(state)
        self._sync_slot()
        if changed:
            self._recolor_widgets()
            self._show_header_photo()
            self.refresh()

    def _sync_slot(self) -> None:
        """记下当前时段（只对「随时刻」有意义），跨时段时靠它比较出变化。"""
        from . import palettes

        self._theme_slot = palettes.slot_for() if self._theme_state.get("mode") == "auto" else ""

    def _slot_tick(self) -> None:
        """「随时刻」模式下，跨过时段边界就换配色（其余模式什么都不做）。"""
        try:
            from . import palettes

            if self._theme_state.get("mode") == "auto":
                current = palettes.slot_for()
                if current != self._theme_slot:
                    self._theme_slot = current
                    self._apply_theme()
        except Exception:  # noqa: BLE001
            pass

    def _theme_targets(self) -> list[tuple]:
        """需要跟着主题重新上色的"骨架"控件：(控件, 选项, 调色板键)。

        卡片和日期头不在这里——它们每次 `refresh()` 重建，建的时候自然取到新颜色。
        """
        colors = theme.COLORS
        targets: list[tuple] = [
            (self.outer, "bg", "border"),
            (self.shell, "bg", "bg"),
            (getattr(self, "header", None), "bg", "bg_soft"),
            (self.clock_label, "bg", "bg_soft"),
            (self.clock_label, "fg", "text"),
            (self.date_label, "bg", "bg_soft"),
            (self.date_label, "fg", "text_dim"),
            (self.next_label, "bg", "bg_soft"),
            (self.next_label, "fg", "accent"),
            (self.body, "bg", "bg"),
            (self.canvas, "bg", "bg"),
            (self.inner, "bg", "bg"),
            (self.footer, "bg", "bg_soft"),
            (self.status_label, "bg", "bg_soft"),
            (self.status_label, "fg", "text_faint"),
            (self.hint_label, "bg", "bg_soft"),
            (self.hint_label, "fg", "text_faint"),
            (self.gear, "bg", "bg_soft"),
        ]
        return [(widget, option, key) for widget, option, key in targets if widget is not None]

    def _recolor_widgets(self) -> None:
        colors = theme.COLORS
        for widget, option, key in self._theme_targets():
            try:
                widget.configure(**{option: colors[key]})
            except (tk.TclError, AttributeError):
                pass
        try:
            # 滚动条是 ttk 之外的原生控件，颜色得单独喂
            self.scrollbar.configure(troughcolor=colors["bg_soft"],
                                     background=colors["text_faint"],
                                     activebackground=colors["accent"])
        except tk.TclError:
            pass
        self._draw_gear(colors["text_dim"])

    def _remember_header_pack(self) -> None:
        """记下三个标签原本的 pack 参数 —— 照片模式把它们摘下来之后还能原样装回去。"""
        plan = []
        for widget in (self.clock_label, self.date_label, self.next_label):
            try:
                info = dict(widget.pack_info())
            except tk.TclError:
                continue
            info.pop("in", None)
            plan.append((widget, info))
        self._header_pack_plan = plan

    def _restore_header_labels(self) -> None:
        """把三个标签装回版面（退出照片模式时用）。"""
        for widget, info in getattr(self, "_header_pack_plan", []):
            if widget.winfo_manager() == "pack":
                continue
            try:
                widget.pack(**info)
            except tk.TclError:
                pass
        try:
            self.header.configure(height=0)      # 0 = 交回给 pack 自适应
        except tk.TclError:
            pass

    def _paint_header_text(self) -> None:
        """照片模式下，把时钟 / 日期 / 下一项画到标题区的 Canvas 上（文字**压在照片上**）。

        为什么必须换一种画法：Tk 的 Label 底色是**不透明**的，而这三个标签都是
        `fill="x"` —— 它们横向铺满整条标题区，把照片盖得只剩上下几像素的缝。
        用户看到的就是"选完照片只是颜色变了、照片没出现"（他报的"没有反应"）。
        Canvas 文本没有底色，画上去就是"照片 + 字"，这才是这块地方本来的设计意图。

        文字、字体、字色仍然以那三个 Label 为准（它们是状态与度量的事实来源），
        Label 只是从版面上摘下来，对象保留。
        """
        canvas = getattr(self, "header_canvas", None)
        if canvas is None or self._header_photo is None:
            return
        canvas.delete("text")
        scale = self.scale
        available = max(80, self.width - int(28 * scale))
        y = int(10 * scale)
        for label, wrap in ((self.clock_label, 0), (self.date_label, 0),
                            (self.next_label, available)):
            text = label.cget("text")
            if not text:
                continue
            try:
                item = canvas.create_text(
                    int(14 * scale), y, text=text, anchor="nw", justify="left",
                    fill=label.cget("fg"), font=label.cget("font"),
                    width=wrap or 0, tags="text")
                box = canvas.bbox(item)
            except tk.TclError:
                continue
            y = (box[3] if box else y) + int(1 * scale)
        try:
            self.header.configure(height=y + int(6 * scale))
        except tk.TclError:
            pass

    def _show_header_photo(self) -> None:
        """照片模式：把照片铺在顶部标题区，时钟/日期/下一项 画在照片上。

        为什么文字要换成 Canvas 文本：Tk 的控件**不透明**，Label 的底色会把照片
        整条盖住（详见 `_paint_header_text`）。整屏照片背景得把面板主体改成 Canvas
        自绘才行（那是另一档工作量）；顶部这一条既能真正看到照片，又不影响正文可读性，
        窗口的其余部分用从照片里算出来的配色，整体是一套的。
        """
        header = getattr(self, "header", None)
        canvas = getattr(self, "header_canvas", None)
        if header is None or canvas is None:
            return
        picture = self._theme_picture
        try:
            if picture is None:
                canvas.delete("all")
                canvas.place_forget()
                self._header_photo = None
                self._header_backdrop_color = None      # 非照片模式别沿用上一张照片的底色
                self._restore_header_labels()
                return
            from . import backdrop

            # 文字改由 Canvas 画：先把三个 Label 从版面上摘下来，别让它们的底色盖住照片
            for label in (self.clock_label, self.date_label, self.next_label):
                label.pack_forget()

            width = max(1, header.winfo_width() or self.width)
            height = max(1, header.winfo_height() or int(90 * self.scale))
            # 按标题区尺寸重新缩放：照片实际显示多大就解多大，不做无谓的放大
            scaled = backdrop.load_background(self.data_dir, max_width=width)
            if scaled is None:
                return
            scrim = theme.COLORS["bg_soft"]
            # 高度裁到标题区那么大——按宽度缩放后高度不一定刚好。
            # 从**偏上**的位置取（1/3 处）：照片的视觉重心通常在那儿，
            # 直接取最上面一条常常是一片天空/一面墙，看着还像"照片没生效"。
            if scaled.height > height:
                top = max(0, (scaled.height - height) // 3)
                scaled = backdrop.crop(scaled, 0, top, scaled.width, height)
            # 渐变**必须算进像素**：Tk 的 Canvas 矩形是不透明的，叠几条上去等于把照片
            # 盖成一堆纯色（原来就是这么写的，用户看到"选了照片没反应"）。
            # 上淡下浓：上面尽量看得见照片，往下渐渐化进面板底色，压在上面的字也就不糊。
            # 强度别调太重：第一版给到 0.34/0.80，实拍出来照片像隔了一层毛玻璃
            # （用户的原话是"没有反应"，太淡等于白做）。文字的可读性由 readable_ink 负责，
            # 不靠压低照片来换。
            fade = backdrop.vertical_fade(scaled, scrim, top=0.16, bottom=0.68)
            self._header_photo = backdrop.to_photoimage(self.root, fade)
            canvas.delete("all")
            canvas.configure(width=width, height=fade.height, bg=scrim)
            canvas.create_image(0, 0, image=self._header_photo, anchor="nw")
            # 记下标题区的**实际**底色（渐变之后），供"下一项"那行挑字色用
            self._header_backdrop_color = backdrop.sample_region(
                fade, 0, 0, fade.width, max(1, fade.height // 3), step=8)
            # 头部文字按照片的**真实**底色挑深/浅，不然浅色照片上白字会看不清
            ink = backdrop.readable_ink(self._header_backdrop_color)
            dim = backdrop.blend(ink, self._header_backdrop_color, 0.25)
            try:
                self.clock_label.configure(fg=ink)
                self.date_label.configure(fg=dim)
            except tk.TclError:
                pass
            canvas.place(x=0, y=0, relwidth=1, relheight=1)
            _lower_widget(canvas)
            # 让"下一项"按新底色重挑一次字色，再把三行字画到照片上。
            # 只在时间线已经建好时做：构造期 `_show_header_photo()` 早于第一次
            # `refresh()`，那时 `_render_next()` 的断言会炸（被下面的兜底 except 吞掉，
            # 表现是照片死活出不来 —— 实测踩过）。
            if self.timeline is not None:
                self._render_next()
            self._paint_header_text()
        except Exception:  # noqa: BLE001
            self._header_photo = None

    # ------------------------------------------------------------------
    # 剪贴板全局热键：按一下就把剪贴板里的群通知解析入库
    # ------------------------------------------------------------------
    def _apply_hotkey(self) -> None:
        """按配置登记（或注销）全局热键；配置改了、面板启动时都会调它。

        为什么登记在**面板**上：面板是常驻进程，而且它本来就负责 inbox 自动整合
        （`_pipeline_tick`），录入这件事归它管最省事。面板没开时热键不可用——
        这一条在控制台设置页里写明了，不藏着。
        """
        try:
            from . import hotkey as hotkey_mod
        except Exception:  # noqa: BLE001
            return
        config = self._load_client_config()
        if config is None:
            return
        hwnd = self._hwnd or self.root.winfo_id()
        enabled = bool(getattr(config, "hotkey_enabled", False))
        spec = getattr(config, "hotkey", "") or ""

        # 先撤销旧的：用户可能改了组合，也可能直接关掉了开关
        if self._hotkey_id and self._hotkey_hwnd:
            hotkey_mod.unregister(self._hotkey_hwnd, self._hotkey_id)
        self._hotkey_id = 0
        self._hotkey_hwnd = 0

        if not enabled:
            self._hotkey_text = ""
            self._refresh_clipboard_menu_label()
            return
        fallback_note = ""
        try:
            parsed = hotkey_mod.parse(spec)
        except hotkey_mod.HotkeyError as error:
            # 配置里的组合不合法（或这次改成不安全了，例如只带 Shift 的 Shift+Z）：
            # 退回默认组合并提示，而不是让用户面对"按了没反应"。
            try:
                parsed = hotkey_mod.parse(hotkey_mod.DEFAULT_HOTKEY)
                fallback_note = (f"热键 {spec!r} 不可用（{error}），"
                                 f"已临时改用 {parsed.text}，请到「设置」里改一个")
            except hotkey_mod.HotkeyError:
                self._hotkey_text = ""
                self._refresh_clipboard_menu_label()
                return

        # 钩子只要挂一次；重复挂会返回 False，不影响下面登记
        hotkey_mod.hook_hotkey(hwnd, self._on_hotkey)
        ok, message = hotkey_mod.register(hwnd, parsed, self.HOTKEY_ID)
        if ok:
            self._hotkey_id = self.HOTKEY_ID
            self._hotkey_hwnd = hwnd
            self._hotkey_text = parsed.text
            if fallback_note:
                self._toast(fallback_note, seconds=10)
        else:
            self._hotkey_text = ""
            self._toast(f"热键 {parsed.text} 没能启用：{message}")
        self._refresh_clipboard_menu_label()

    def _on_hotkey(self, hotkey_id: int) -> None:
        """窗口过程回调：**只置一个标记，绝不做别的事**。

        这不是保守，是踩出来的：`WM_HOTKEY` 是在 Tk 派发窗口消息的过程中送进子类过程的，
        在那里调用 Tk 控件代码（哪怕只是 `refresh()` 重建卡片）会重入 Tcl 的事件处理，
        后果是**面板进程当场消失**——没有 Python 异常、`panel.log` 也是空的，
        用户看到的就是"按一下热键，日程表没了"。真正的录入交给 `_native_tick`。
        """
        if hotkey_id == self.HOTKEY_ID:
            self._hotkey_pending = True

    def _native_tick(self) -> None:
        """在**普通 Tk 回调上下文**里执行窗口过程攒下的待办。

        三条原生回调都必须走这条路（窗口过程里只许置标记）：
          * `WM_HOTKEY`（剪贴板录入）——原来直接调 `ingest_clipboard()`，实测把进程打死；
          * 电源/显示变化消息（`_on_system_wake`）——原来直接调 `_apply_foreground_change()`，
            它内部会 `geometry()` / `SetWindowPos`，属同一类隐患；
          * 拖放（`dropzone`）本来就是这么做的：窗口过程只入队，`_pump_dropzone` 再抽出来。
        延迟上限就是 `NATIVE_TICK_MS`（60 ms），肉眼看不出。
        """
        if getattr(self, "_closing", False):
            return
        if self._wake_pending:
            self._wake_pending = False
            if not getattr(self, "_in_wake", False):
                self._in_wake = True
                try:
                    self._apply_foreground_change()
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    self._in_wake = False
        if self._hotkey_pending:
            self._hotkey_pending = False
            try:
                self.ingest_clipboard()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.root.after(NATIVE_TICK_MS, self._native_tick)
        except tk.TclError:
            pass

    def ingest_clipboard(self) -> None:
        """热键/菜单入口：**优先抓"选中的文字"**，没有选区时退回读剪贴板。

        为什么要抓选区：用户的心智是"我在 QQ 里选中这条通知，按一下热键就该录进去"，
        而不是"先按 Ctrl+C 再按热键"（用户原话：「我要求不需要录入剪贴板，
        直接选中后使用热键就能识别」）。

        抓到文字之后走哪条路由**面板上有没有选中一条通知**决定（用户要求）：
          * 选中了某条通知 → 这段文字作为它的**备注补充**并进去；
          * 没选中 → 照旧当成一条新通知来解析入库。
        这条规则只对通知生效：课程卡片选不上，选中它等于没选中（会明确提示）。

        抓选区的做法是**借用一下剪贴板**：记住原内容 → 合成 Ctrl+C → 读出来 → 复原。
        终端类窗口会跳过（那里 Ctrl+C 是中断信号，不能乱发）；
        设置里可以整体关掉这个行为（`hotkey_selection`）。
        """
        try:
            from . import hotkey as hotkey_mod
        except Exception:  # noqa: BLE001
            return

        config = self._load_client_config()
        use_selection = bool(getattr(config, "hotkey_selection", True))
        target = self._selected_event()
        text = ""
        source = ""
        note = ""
        fell_back = False
        if use_selection:
            text, note = hotkey_mod.capture_selection()
            source = "选中的文字"
        if not text.strip():
            try:
                fallback = hotkey_mod.read_clipboard_text()
            except Exception:  # noqa: BLE001
                fallback = ""
            if fallback.strip():
                text, source = fallback, "剪贴板"
                # 选区没抓到、退而用剪贴板时要**说出来**：不说的话用户看到的热键结果
                # 其实是他上一次复制的内容，只会觉得"识别错了"或"没反应"（实测踩过）。
                # 补充备注时这句话没意义——那时候"选中"指的是面板上选中的卡片。
                fell_back = bool(note) and target is None
                note = ""
            elif note:
                self._toast(note, seconds=8)
                return
        if not text.strip():
            self._toast("剪贴板里没有文本，也没有检测到选中的文字")
            return
        # 整页网页/长文截断：录入只关心通知，超长文本会让解析变慢、界面发顿
        if len(text) > 20_000:
            text = text[:20_000]

        if target is not None:
            # 选中了某条通知：这段文字是它的补充内容，不解析、不新建
            try:
                self._append_note(target, text)
            except Exception as error:  # noqa: BLE001
                self._toast(f"补充失败：{error}")
            return

        try:
            report = self.pipeline.ingest_text(text, source_label=f"热键录入（{source}）")
        except Exception as error:  # noqa: BLE001
            self._toast(f"录入失败：{error}")
            return
        if report.added or report.updated:
            self._pipelines.append(f"热键录入 {report.added} 条")
            self._pipelines = self._pipelines[-3:]
            self.refresh()
            origin = "剪贴板（没抓到选中的文字）" if fell_back else source
            self._toast(f"已录入 {report.added} 条、更新 {report.updated} 条通知（来自{origin}）")
        elif report.candidates:
            self._toast("这几条通知之前已经录过了")
        else:
            origin = "剪贴板（没抓到选中的文字）" if fell_back else source
            self._toast(f"{origin}里没有识别到通知（需要带时间和事项的群消息）")

    def _refresh_clipboard_menu_label(self) -> None:
        """把当前热键写进右键菜单，让用户随时看得见它是什么。"""
        text = getattr(self, "_hotkey_text", "")
        label = f"从剪贴板录入通知（{text}）" if text else "从剪贴板录入通知"
        for menu, index in ((getattr(self, "menu", None), getattr(self, "_clip_index", None)),
                            (getattr(self, "footer_menu", None), getattr(self, "_clip_index_footer", None))):
            if menu is None or index is None:
                continue
            try:
                menu.entryconfigure(index, label=label)
            except tk.TclError:
                pass

    def _build_footer_menu(self) -> None:
        """底部状态栏的右键菜单。

        需求原话：「我要求右键状态栏标识可以退出应用，呼出客户端窗口，呼出快速录入群消息」。
        踩过的坑：底部那条（状态 + 操作提示）原来**没有绑任何菜单**——在那儿右键只有一片空白，
        既退不出去也找不到控制台。所以这里单独给它一套。
        """
        self.footer_menu = tk.Menu(self.root, tearoff=0)
        self.footer_menu.add_command(label="打开客户端窗口", command=self.open_console)
        self.footer_menu.add_command(label="快速录入群消息…", command=self.quick_entry)
        self.footer_menu.add_command(label="从剪贴板录入通知", command=self.ingest_clipboard)
        self._clip_index_footer = self.footer_menu.index("end")
        self.footer_menu.add_command(label="立即整合群通知", command=self.run_pipeline)
        self.footer_menu.add_separator()
        self.footer_menu.add_command(label="刷新界面", command=self.refresh)
        self.footer_menu.add_command(label="收起面板", command=self.hide_now)
        self.footer_menu.add_separator()
        self.footer_menu.add_command(label="退出应用（面板与托盘一并退出）", command=self.quit_app)

    def _popup_footer_menu(self, event) -> None:
        try:
            self.footer_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.footer_menu.grab_release()

    def quit_app(self) -> None:
        """彻底退出：面板自己退，同时请客户端也退。

        面板和客户端是两个进程；只退面板的话托盘还留着，
        而用户点「退出应用」想的是一起走 —— 所以顺手给客户端写一条退出请求。
        """
        try:
            from .client_app import AppController, QUIT_REQUEST
            controller = AppController(self.data_dir)
            if controller.client_pid() is not None:
                (self.data_dir / QUIT_REQUEST).write_text("panel", encoding="utf-8")
        except Exception:
            pass
        self.quit()

    def quit(self) -> None:
        self._closing = True
        # 先把托盘图标摘掉再退：进程被系统结束的话图标会**留在通知区**，
        # 点它永远没反应（用户报过"点了那个图标好长时间都没有响应"，就是这么来的）。
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.remove()
        self._release_hotkey()
        self._destroy_festival()
        self._cancel_jobs()
        self._save_geometry()
        self.root.destroy()

    def _release_hotkey(self) -> None:
        """退出前把热键还回去。

        不还的话，进程结束后 Windows 也会回收登记，但**下一个进程启动时**可能撞上
        还没清理干净的那一下（实测表现为"刚重启完热键没反应，过一会儿又好了"）。
        主动注销是零成本的，顺手做掉。
        """
        if not self._hotkey_id or not self._hotkey_hwnd:
            return
        try:
            from . import hotkey as hotkey_mod
            hotkey_mod.unregister(self._hotkey_hwnd, self._hotkey_id)
            hotkey_mod.unhook_hotkey(self._hotkey_hwnd)
        except Exception:  # noqa: BLE001
            pass
        self._hotkey_id = 0
        self._hotkey_hwnd = 0

    def _cancel_jobs(self) -> None:
        """把所有还排着的 after 取消掉再销毁窗口。

        面板的定时器是**自我续期**的（`_tick` 里再排一次 `_tick`），一个个记 job id
        太容易漏；`after info` 能一次性列出这个解释器里所有待执行的 job。
        不取消的话，销毁窗口后 Tk 会刷一屏
        `invalid command name "…_watch_stop_flag"`（实测）。
        """
        try:
            jobs = self.root.tk.call("after", "info")
        except tk.TclError:
            return
        for job in jobs or ():
            try:
                self.root.after_cancel(job)
            except (tk.TclError, ValueError):
                pass

    # ------------------------------------------------------------------
    # 客户端联动：停止标记、到点提醒、位置持久化
    # ------------------------------------------------------------------
    def _watch_stop_flag(self) -> None:
        """每 700 ms 干两件事：看客户端的停止标记，看数据文件有没有被改。

        为什么盯着文件 mtime：用户在面板上右键改了结束时间、或在客户端里补了一条通知，
        面板原来要等下一次 30 秒刷新才看见——"改完跟没改一样"。
        现在 700 ms 内就重画，代价只是两次 stat()。
        """
        if getattr(self, "_closing", False):
            return
        flag = self.data_dir / "panel.stop"
        if flag.exists():
            try:
                flag.unlink()
            except OSError:
                pass
            if self.on_close_request is not None:
                self.on_close_request()
            else:
                self.quit()
            return
        # 「请显示出来」的请求（桌面快捷方式用它）：收起过就展开，没收起就什么也不做。
        # 快捷方式**只**能走这条 —— 它永远不许开控制台（用户把这条定死了）。
        show_flag = self.data_dir / "panel.show"
        if show_flag.exists():
            try:
                show_flag.unlink()
            except OSError:
                pass
            if self.root.state() != "withdrawn":
                was_hidden = getattr(self, "_hidden_offscreen", False)
                # front=True：桌面挂件平时在浏览器下面，不提上来就等于"没反应"
                self.show_now(front=True)
                if not was_hidden:
                    # 面板本来就在屏幕上：双击快捷方式**看起来什么都没发生**，
                    # 用户会判断成"点了没反应"（原话）。给两个可见回执。
                    self._toast("日程表已经在运行")
                    self.tray_balloon("桌面日程", "日程表已经在运行，就在屏幕上。")
        if self._data_changed():
            if self._config_dirty:
                # 用户在控制台改了设置（热键、主题…）：面板自己重挂/重套，不用重启
                self._apply_hotkey()
                self._apply_theme()
            self.refresh()
        self._slot_tick()
        self.root.after(700, self._watch_stop_flag)

    def _data_changed(self) -> bool:
        """数据文件或设置文件有没有被别处改过（按 mtime 判断）。

        client.json 也在盯着：用户在控制台改完热键/主题，面板 700 ms 内就该跟着变，
        而不是要用户自己去重启面板（"改完跟没改一样"是同一个坑）。
        """
        stamps: list[tuple[str, float]] = []
        for name in ("events.json", "timetable.json", "calendar.json", "client.json"):
            try:
                stamps.append((name, (self.data_dir / name).stat().st_mtime))
            except OSError:
                stamps.append((name, 0.0))
        previous = getattr(self, "_data_stamps", None)
        self._data_stamps = stamps
        if previous is None or previous == stamps:
            return False
        self._config_dirty = previous[-1][1] != stamps[-1][1]
        return True

    def _load_client_config(self):
        try:
            from .client_config import ClientConfig
            return ClientConfig.load(self.data_dir)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 桌面图层：像桌面小部件一样待在底层，游戏/全屏时自动隐身
    # ------------------------------------------------------------------
    def _init_desktop_layer(self) -> None:
        """把面板压到非置顶窗口最底层，并按需开启鼠标穿透。"""
        try:
            from . import winlayer
        except Exception:
            return
        try:
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            parent = winlayer._user32().GetParent(hwnd) if winlayer._IS_WINDOWS else 0
            self._hwnd = int(parent or hwnd)
        except Exception:
            return
        winlayer.set_topmost(self._hwnd, False)
        winlayer.send_to_bottom(self._hwnd)
        if self.click_through:
            winlayer.apply_click_through(self._hwnd)
        # 从消息层堵死最小化：任务栏"显示桌面"最小化不了它，面板也就不会"消失"
        winlayer.block_minimize(self._hwnd)
        # 解锁 / 显示器重新点亮 / 分辨率变化：系统会主动发消息，立刻恢复，
        # 不用等下一次轮询（用户反馈"切回主屏幕时太慢"就是这个延迟）
        winlayer.hook_wake_events(self._hwnd, self._on_system_wake)

    def _on_system_wake(self, reason: str = "") -> None:
        """窗口过程回调：**只置标记**（同 `_on_hotkey`，这里同样不能碰 Tk）。

        原来这里直接调 `_apply_foreground_change()`，而那里面会 `geometry()` /
        `SetWindowPos`——和热键那次崩的是同一类：在窗口过程里重入 Tk。
        现在的恢复延迟上限是 `NATIVE_TICK_MS`（60 ms），与原来的 40 ms 前台快查同一量级。
        """
        if not getattr(self, "_closing", False):
            self._wake_pending = True

    def _desktop_watch(self) -> None:
        """兜底巡检：正常靠 `_fg_poll`（100 ms 一次），这个慢轮询只防漏。

        三种情况都要处理（都是实机踩出来的）：
          1. **最小化了要还原**：任务栏右下角的"显示桌面"按钮会把所有窗口最小化，
             面板也在内。光靠 SetWindowPos 救不回来，必须 ShowWindow(SW_RESTORE)。
             用户的原话是"我按时间旁边这个按钮，日程表还会消失"。
          2. **宠物模式**：桌面在前台时把面板浮到普通窗口最上层。
          3. **全屏应用隐身**：游戏/视频时藏起来，不挡画面也不挡操作。

        注意：面板**只会因为全屏应用而隐藏**，或者由客户端/右键菜单关闭。
        """
        if getattr(self, "_closing", False):
            return
        self._apply_foreground_change()
        self.root.after(DESKTOP_POLL_MS, self._desktop_watch)

    def _set_client_config(self, config) -> None:
        try:
            config.save(self.data_dir)
        except OSError:
            pass

    def _reminder_tick(self) -> None:
        """到点提醒：距离开始还有 N 分钟时弹一次窗，每个事项只弹一次。"""
        config = self._load_client_config()
        if config is not None and self.timeline is not None and config.popup_reminders:
            item = self.timeline.next_item
            if item is not None and not item.started:
                card = item.card
                key = f"{card.date}|{card.start}|{card.title}"
                if (item.minutes_until <= config.remind_before_minutes
                        and key not in config.reminded):
                    config.reminded.append(key)
                    config.reminded = config.reminded[-80:]
                    self._set_client_config(config)
                    self._popup_reminder(card, item.minutes_until)
        self.root.after(20_000, self._reminder_tick)

    def show_now(self, *, front: bool = False) -> None:
        """瞬间把面板放出来。

        为什么不用 `withdraw()` + `deiconify()`：那条路要等下一次 `_tick` 才把内容重画，
        实测恢复要 0.5 秒以上，用户的原话是"恢复显示的延迟太高了"。
        改成**只是把窗口挪回屏幕坐标**——窗口一直在（没 withdraw），
        所以挪回来就是一次 SetWindowPos，肉眼看不到延迟。

        `front=True`（用户**显式**要求显示：双击快捷方式 / 托盘菜单 / 齿轮）时，
        还会把面板临时提到**所有窗口最上面**几秒 —— 见 `_front_briefly()`。
        """
        self._hidden_offscreen = False
        x, y = self._onscreen_position()
        self.root.geometry(f"+{x}+{y}")
        if self._hwnd:
            from . import winlayer
            winlayer.is_minimized(self._hwnd) and winlayer.restore_window(self._hwnd)
            if self.pet_mode:
                winlayer.raise_to_top_of_normal(self._hwnd)
        if front:
            self._front_briefly()

    def _front_briefly(self, *, seconds: float = 3.0) -> None:
        """临时把面板压在所有窗口最上面，几秒后放回它自己的层。

        为什么需要：面板是**桌面挂件**（`window_mode="desktop"`），平时被
        `send_to_bottom` 压在普通窗口之下。用户双击桌面快捷方式时它确实"显示了"，
        但只要浏览器/编辑器是最大化的，它就**被完全盖住** —— 用户看到的是
        "点了半天没反应"（原话）。所以显式要求显示时，先让他**看得见**。
        """
        if not self._hwnd:
            return
        from . import winlayer
        self._front_until = time.monotonic() + max(0.5, seconds)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        winlayer.set_topmost(self._hwnd, True)
        winlayer.flash_window(self._hwnd)          # 有任务栏按钮时闪一下（没有也不报错）
        try:
            self.root.lift()
            self.root.after(int(max(0.5, seconds) * 1000), self._end_front)
        except tk.TclError:
            pass

    def _end_front(self) -> None:
        """临时置顶结束：回到面板本来该待的层。"""
        self._front_until = 0.0
        if getattr(self, "_closing", False) or not self._hwnd:
            return
        from . import winlayer
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass
        winlayer.set_topmost(self._hwnd, False)
        if self.pet_mode and winlayer.desktop_is_foreground():
            winlayer.raise_to_top_of_normal(self._hwnd)
        else:
            winlayer.send_to_bottom(self._hwnd)

    def _front_active(self) -> bool:
        return time.monotonic() < getattr(self, "_front_until", 0.0)

    def hide_instant(self) -> None:
        """瞬间收起来：把窗口挪到屏幕外，而不是 withdraw。

        withdraw 会让 Tk 拆掉映射，回来时重新映射 + 重画 → 有延迟；
        挪到屏幕外则窗口一直映射着，回来只是改坐标。
        """
        self._hidden_offscreen = True
        self.root.geometry("+-4000+-4000")

    def _onscreen_position(self) -> tuple[int, int]:
        """当前应该在屏幕上的坐标（拖动后会更新 _target_x/_target_y）。"""
        if self.fixed_position is not None:
            return int(self.fixed_position[0]), int(self.fixed_position[1])
        return int(getattr(self, "_target_x", 0)), int(getattr(self, "_target_y", 0))

    def _popup_reminder(self, card: Card, minutes: int) -> None:
        colors = theme.COLORS
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=card.color)
        shell = tk.Frame(toast, bg=colors["card"])
        shell.pack(padx=2, pady=2)
        tk.Label(
            shell, text=("马上开始" if minutes <= 1 else f"{minutes} 分钟后") + f" · {card.title}",
            bg=colors["card"], fg=colors["text"], font=self.fonts.spec(self.fonts.body, "bold"),
        ).pack(anchor="w", padx=14, pady=(10, 2))
        detail = " · ".join(part for part in (card.time_label, card.location, card.meta_line) if part)
        tk.Label(
            shell, text=detail, bg=colors["card"], fg=colors["text_dim"],
            font=self.fonts.spec(self.fonts.meta), justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 10))
        toast.update_idletasks()
        screen_w = toast.winfo_screenwidth()
        width = toast.winfo_reqwidth()
        toast.geometry(f"+{max(0, screen_w - width - 24)}+60")
        toast.after(12_000, toast.destroy)

    # ------------------------------------------------------------------
    # 周期性任务
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if self._hidden_until is not None and dt.datetime.now() >= self._hidden_until:
            self._hidden_until = None
            self._user_hidden = False
            self.show_now()
        self.refresh()
        self.root.after(self.refresh_ms, self._tick)

    def _pipeline_tick(self) -> None:
        self.run_pipeline()
        self.root.after(self.pipeline_ms, self._pipeline_tick)

    def run_pipeline(self) -> None:
        try:
            report = self.pipeline.run()
            self._pipelines.append(report.summary())
        except Exception as error:  # 面板不能因为一次整合失败就退出
            self._pipelines.append(f"整合失败：{error}")
        self.refresh()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        now = dt.datetime.now()
        self.clock_label.configure(text=now.strftime("%H:%M"))
        table = None
        try:
            table = load_timetable(self.data_dir)
        except Exception as error:
            self._pipelines.append(f"课表读取失败：{error}")
        self._table = table          # 空状态提示要用它区分"没导入"和"这几天没课"
        self._calendar_cache = self._load_calendar()
        events = self.pipeline.load_events()
        self.timeline = build_timeline(
            events,
            table,
            today=self.today_override,
            now=now,
            last_run_text=self.pipeline.last_run_text(),
            calendar=self._calendar_cache,
            # 面板是"接下来要干什么"的看板：已经结束的通知自动让位
            hide_past_events=self.hide_past,
        )
        self.date_label.configure(
            text=self._fit_text(self._date_text(), self.width - int(28 * self.scale),
                                self.date_label),
            fg=self._date_color(),
        )
        self._render_festival()
        self._render_countdown()
        self._render_next()
        self._render_sections()
        self._render_status()
        # 照片模式下三行字是画在 Canvas 上的（时钟/日期/下一项 刚刚才更新），
        # 这里跟着重画一遍，否则时间走了字还停在旧值。
        if self._header_photo is not None:
            self._paint_header_text()

    def _load_calendar(self):
        try:
            return load_calendar(self.data_dir)
        except Exception:
            from .holidays import Calendar
            return Calendar()

    def _date_text(self) -> str:
        """日期行：**带年份**（用户要求）+ 教学周 + 节日/假期标记。"""
        assert self.timeline is not None
        today = self.timeline.today
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]
        text = f"{today.year}年{today.month}月{today.day}日 {weekday}"
        if self.timeline.week_label:
            text += f" · {self.timeline.week_label}"
        if self.timeline.holiday is not None:
            left = self.timeline.holiday_left
            text += f" · {self.timeline.holiday.name}假期"
            if left:
                text += f"（还剩 {left} 天）"
        elif self.timeline.makeup is not None:
            text += f" · {self.timeline.makeup.text()}"
        return text

    def _date_color(self) -> str:
        assert self.timeline is not None
        background = theme.COLORS["bg_soft"]
        if self.timeline.festival is not None:
            return theme.readable_accent(self.timeline.festival.accent, background)
        if self.timeline.holiday is not None:
            return theme.readable_accent("#F2994A", background)
        return theme.COLORS["text_dim"]

    # -- 节日彩蛋横幅 ----------------------------------------------------
    def _render_festival(self) -> None:
        """有节日就插一条横幅（在日期行下面、时间线上面），没有就拆掉。"""
        assert self.timeline is not None
        hint = hint_for(self.timeline.today, self._calendar_cache)
        key = hint.festival.key if hint is not None else None
        # 比的是"现在这条横幅到底是哪个节日"，不是"上一次算出来的 key"：
        # 只比 key 的话，"横幅还在、但今天已经没有节日了"（key=None）会被
        # 当成"没变化"，国庆的横幅就会一直挂到调休那天（实测踩过）。
        showing = self._festival_key if self._festival_banner is not None else None
        if key == showing:
            # 节日没变：只刷新"还有几天/已过几天"这类会变的文案
            if self._festival_banner is not None and hint is not None:
                self._festival_banner.update_hint(hint)
            return
        self._destroy_festival()
        self._festival_key = key
        if hint is None:
            self._apply_festival_frame(None)
            return
        self._festival_banner = FestivalBanner(
            self.shell, hint, self.fonts, scale=self.scale,
            wrap_width=self.width - int(34 * self.scale),
            on_tick=self._festival_frame_tick,
        )
        self._festival_banner.pack(fill="x", before=self.body)
        self._festival_banner.pack_propagate(True)
        self._apply_festival_frame(hint.festival)

    def _destroy_festival(self) -> None:
        if self._festival_banner is not None:
            try:
                self._festival_banner.stop()
                self._festival_banner.destroy()
            except tk.TclError:
                pass
            self._festival_banner = None
        self._apply_festival_frame(None)

    # -- 节日主题边框 ----------------------------------------------------
    #: 平时边框粗细（像素，逻辑像素）
    FRAME_PAD = 1
    #: 节日期间边框粗细
    FESTIVAL_FRAME_PAD = 3

    def _apply_festival_frame(self, festival: Festival | None) -> None:
        """把窗口边框换成节日主色（没有节日时恢复成普通的细边框）。"""
        try:
            if festival is None:
                self.outer.configure(bg=theme.COLORS["border"])
                self.shell.pack_configure(padx=self.FRAME_PAD, pady=self.FRAME_PAD)
                self._festival_frame_color = None
            else:
                self.outer.configure(bg=festival.accent)
                self.shell.pack_configure(padx=self.FESTIVAL_FRAME_PAD,
                                          pady=self.FESTIVAL_FRAME_PAD)
                self._festival_frame_color = festival.accent
        except tk.TclError:
            pass

    def _festival_frame_tick(self, seconds: float) -> None:
        """跟着横幅的动画一起让边框"呼吸"。

        颜色在「节日主色」和「面板底色」之间来回混，看起来像呼吸灯；
        每帧只是一次 configure，开销可以忽略。
        """
        accent = getattr(self, "_festival_frame_color", None)
        if not accent:
            return
        ratio = 0.18 + 0.22 * (1 + math.sin(seconds * 1.7)) / 2
        try:
            self.outer.configure(bg=_mix_color(accent, theme.COLORS["bg"], ratio))
        except tk.TclError:
            pass

    # -- 假期收尾倒计时 --------------------------------------------------
    def _render_countdown(self) -> None:
        """假期结束后那几天：日期下方一条小倒计时（按天数）。"""
        assert self.timeline is not None
        wrap_up = self.timeline.wrap_up
        if wrap_up is None:
            self._destroy_countdown()
            return
        accent = self.timeline.festival.accent if self.timeline.festival is not None else "#F2994A"
        # 别用 ⏳ 这类符号：实测在这个字体下会画成豆腐块（有测试盯着）
        text = f"✦ {wrap_up.short()}"
        if getattr(self, "_countdown_strip", None) is None:
            colors = theme.COLORS
            strip = tk.Frame(self.shell, bg=colors["bg_soft"])
            label = tk.Label(strip, text=text, bg=colors["bg_soft"], fg=accent,
                             font=self.fonts.spec(self.fonts.badge, "bold"), anchor="w")
            label.pack(fill="x", padx=int(14 * self.scale), pady=int(3 * self.scale))
            strip.pack(fill="x", before=self.body)
            self._countdown_strip = strip
            self._countdown_label = label
        else:
            self._countdown_label.configure(text=text, fg=accent)

    def _destroy_countdown(self) -> None:
        strip = getattr(self, "_countdown_strip", None)
        if strip is not None:
            try:
                strip.destroy()
            except tk.TclError:
                pass
            self._countdown_strip = None

    def _render_next(self) -> None:
        assert self.timeline is not None
        item = self.timeline.next_item
        if item is None:
            self.next_label.configure(text="暂无后续安排", fg=theme.COLORS["text_faint"],
                                      wraplength=self.width - int(28 * self.scale))
            return
        card = item.card
        head = "进行中 " if item.started else "下一项 "
        # **不再截断标题**：用户原话「图中的下一项显示不全」。
        # 放不下就折行；头部没写死高度，会跟着让高，末行不会被裁。
        text = f"{head}{item.text()} · {card.title}"
        if card.start:
            text += f"（{card.start}）"
        # 标题区可能压着照片：强调色要按**真实底色**调过才看得清
        banner = getattr(self, "_header_backdrop_color", None) or theme.COLORS["bg_soft"]
        color = theme.readable_accent(theme.COLORS["accent"], banner)
        if item.started:
            color = theme.readable_accent("#37C978", banner)
        self.next_label.configure(
            text=text, fg=color, justify="left",
            wraplength=self.width - int(28 * self.scale),
        )

    def _next_font(self) -> tkfont.Font:
        if getattr(self, "_next_font_cache", None) is None:
            self._next_font_cache = tkfont.Font(font=self.next_label.cget("font"))
        return self._next_font_cache

    def _fit_text(self, text: str, available: int, widget: tk.Widget | None = None) -> str:
        """按真实字体度量截断到可用宽度，末尾加省略号。

        贴边面板没有省略号，超出可用宽度会被硬切掉半个字（日期行带上假期名，
        例如"中秋国庆连放"，就会超宽）。所以统一过这里处理。
        """
        target = widget if widget is not None else self.next_label
        cache = getattr(self, "_fit_font_cache", None)
        if cache is None or cache[0] is not target:
            cache = (target, tkfont.Font(font=target.cget("font")))
            self._fit_font_cache = cache
        font = cache[1]
        if font.measure(text) <= available:
            return text
        for length in range(len(text) - 1, 0, -1):
            candidate = text[:length] + "…"
            if font.measure(candidate) <= available:
                return candidate
        return "…"

    def _render_sections(self) -> None:
        """重排时间线。

        性能关键：**内容没变就复用旧控件**，不要再建一遍。
        实测 18 门课 / 192 个控件时，全量重建一次要 273 ms——每 30 秒定时刷新
        撞上滚轮就会明显卡顿（用户原话"用鼠标滚轮检视日程表太卡了"）。
        复用之后只剩状态色和进度条的更新，量级降到几毫秒。
        """
        assert self.timeline is not None
        # 卡片控件每轮重排都会重新登记（复用旧控件的那条路也会重新登记），
        # 不清空的话这个列表会一直涨，选中态也会画到已经被销毁的控件上。
        self._card_frames = []
        if not self.timeline.total_cards:
            for child in self.inner.winfo_children():
                child.destroy()
            self._card_cache.clear()
            # 这一轮一个日期头都没建，旧的那串引用要清掉：留着的话下一轮会对着
            # 已经销毁的控件调 destroy()（虽然被 try 吞了，但那是悬空引用）。
            self._day_heads = []
            self._render_empty()
            self._on_inner_configure()
            self._snap_to_top()
            return

        cache = self._card_cache
        reused: dict[tuple, tk.Frame] = {}
        for child in self.inner.winfo_children():
            child.pack_forget()
        # 日期头是每次重排都新建的（卡片才走缓存），所以上一轮的那些得**销毁**。
        # 只 pack_forget 的话它们会一直挂在 inner 下面：每 30 秒一次刷新、
        # 一次 7 个日期头，挂一天就是上万个 Tk 控件（实测就是这么攒出来的）。
        for head in getattr(self, "_day_heads", []):
            try:
                head.destroy()
            except tk.TclError:
                pass
        self._day_heads = []
        for section in self.timeline.sections:
            # 没有安排的日子**不占版面**：用户原话「我要求的是只显示有事件任务的那天，
            # 像 9月29 和 9月30 日，明明没有安排为什么要显示呢？况且此时还是假期期间！」
            # 今天例外——面板是"今天怎么样"的看板，今天空着本身也是信息，
            # 而且它是整个列表的锚点（没有它用户会以为面板坏了）。
            if not section.cards and not section.is_today:
                continue
            self._render_day(section, reused, cache)
        # 用不上的旧卡片直接销毁（课程/通知被删掉的情况）
        for key, widget in list(cache.items()):
            if key not in reused:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
                self._card_paint.pop(key, None)
        self._card_cache = dict(reused)
        self._on_inner_configure()
        self._scroll_region = None
        if not getattr(self, "_scroll_kept", False):
            self._snap_to_top()
        self._scroll_kept = False

    def _render_empty(self) -> None:
        """一周都没有课时，别留一整列空白日期头，直接给一句明确的话。"""
        colors = theme.COLORS
        scale = self.scale
        wrap_width = max(120, self.width - int(56 * scale))
        box = tk.Frame(self.inner, bg=colors["bg"])
        box.pack(fill="x", pady=(int(28 * scale), 0))
        tk.Label(
            box, text=EMPTY_AGENDA_TEXT, bg=colors["bg"], fg=colors["text"],
            font=self.fonts.spec(self.fonts.title, "bold"), anchor="center",
        ).pack(fill="x")
        tk.Label(
            box, text=empty_hint(getattr(self, "_table", None), self._calendar_cache,
                                 self.timeline.today if self.timeline else None),
            bg=colors["bg"], fg=colors["text_faint"],
            font=self.fonts.spec(self.fonts.meta), anchor="center", justify="center",
            wraplength=wrap_width,
        ).pack(fill="x", pady=(int(8 * scale), 0))

    def _render_day(self, section: DaySection, reused: dict, cache: dict) -> None:
        colors = theme.COLORS
        scale = self.scale
        head_bg = colors["today_bg"] if section.is_today else colors["bg"]
        head = tk.Frame(self.inner, bg=head_bg)
        head.pack(fill="x", pady=(int(6 * scale), 0))
        if not hasattr(self, "_day_heads"):
            self._day_heads = []
        self._day_heads.append(head)

        title_color = colors["accent"] if section.is_today else colors["text"]
        if section.is_weekend and not section.is_today:
            title_color = colors["text_dim"]
        if section.festival is not None:
            # 节日的强调色是按深色底挑的，浅色主题下要就地调暗才看得见
            title_color = theme.readable_accent(section.festival.accent, head_bg)
        tk.Label(
            head, text=day_heading(section), bg=head_bg, fg=title_color,
            font=self.fonts.spec(self.fonts.heading, "bold"), anchor="w",
        ).pack(side="left", padx=(int(14 * scale), 0), pady=int(4 * scale))

        # 调休小字：紧贴在日期右边，用强调色的小号字（用户点名要的
        # "在我给的日期旁边增加小字（调休X月X日日程）"）
        if section.makeup_label:
            tk.Label(
                head, text=section.makeup_label, bg=head_bg,
                fg=theme.readable_accent("#F2994A", head_bg),
                font=self.fonts.spec(self.fonts.badge, "bold"), anchor="w",
            ).pack(side="left", padx=(int(6 * scale), 0), pady=int(4 * scale))

        counts = []
        if section.course_count:
            counts.append(f"{section.course_count} 节课")
        if section.event_count:
            counts.append(f"{section.event_count} 条通知")
        if section.holiday is not None and not counts:
            counts.append("假期")
        tk.Label(
            head, text=" · ".join(counts) or "无安排", bg=head_bg, fg=colors["text_faint"],
            font=self.fonts.spec(self.fonts.badge), anchor="e",
        ).pack(side="right", padx=int(14 * scale))

        if not section.cards:
            tk.Label(
                self.inner, text=f"　{EMPTY_AGENDA_TEXT}", bg=colors["bg"], fg=colors["text_faint"],
                font=self.fonts.spec(self.fonts.meta), anchor="w",
            ).pack(fill="x", padx=int(14 * scale))
            return

        for index, card in enumerate(section.cards):
            key = card_key(card)
            widget = cache.get(key)
            if widget is not None and widget.winfo_exists():
                # 内容没变：直接摆回去，只刷新进度/状态相关的画法
                widget.pack(fill="x")
                self._refresh_card_dynamics(key)
            else:
                widget = self._render_card(card, last=index == len(section.cards) - 1)
            reused[key] = widget
            # 复用旧控件时不会走 _render_card，所以选中态要在这里**两条路都补**一次
            frame = getattr(widget, "card_frame", None)
            if frame is not None:
                self._card_frames.append((frame, card.event_id))
                self._paint_card_selection(frame, card.event_id)

    def _render_card(self, card: Card, *, last: bool) -> tk.Frame:
        colors = theme.COLORS
        scale = self.scale
        row = tk.Frame(self.inner, bg=colors["bg"])
        row.pack(fill="x")

        # 左：时间竖列
        gutter = tk.Frame(row, bg=colors["bg"], width=int(52 * scale))
        gutter.pack(side="left", fill="y")
        gutter.pack_propagate(False)
        start_color = colors["text_faint"] if card.state == "past" else colors["text"]
        tk.Label(
            gutter, text=card.start or "全天", bg=colors["bg"], fg=start_color,
            font=self.fonts.spec(self.fonts.time, "bold"), anchor="e",
        ).pack(fill="x", padx=(0, int(4 * scale)), pady=(int(4 * scale), 0))
        if card.end:
            tk.Label(
                gutter, text=card.end, bg=colors["bg"], fg=colors["text_faint"],
                font=self.fonts.spec(self.fonts.badge), anchor="e",
            ).pack(fill="x", padx=(0, int(4 * scale)))

        # 中：时间轴（圆点 + 连接线），高度在卡片渲染完后按实际高度对齐
        axis = tk.Canvas(row, width=int(20 * scale), height=1, bg=colors["bg"], highlightthickness=0, bd=0)
        axis.pack(side="left", fill="y")

        # 右：卡片（WakeUp 风格：淡彩底 + 左侧色条 + 标题/地点/人员/备注）
        if card.state == "now":
            bg = theme.mix(theme.COLORS["card_now"], card.color, 0.30)
        elif card.state == "past":
            bg = theme.tint(theme.COLORS["card_past"], card.color, 0.12)
        else:
            bg = theme.tint(theme.COLORS["card"], card.color, 0.20)
        card_frame = tk.Frame(row, bg=bg)
        card_frame.pack(side="left", fill="x", expand=True, padx=(0, int(12 * scale)), pady=(int(2 * scale), int(2 * scale)))
        # 挂在 row 上：`_render_day` 复用旧卡片时拿不到 card_frame，只能从这里取
        row.card_frame = card_frame

        accent = tk.Frame(card_frame, bg=card.color, width=int(3 * scale))
        accent.pack(side="left", fill="y")
        content = tk.Frame(card_frame, bg=bg)
        content.pack(side="left", fill="both", expand=True, padx=int(8 * scale), pady=int(5 * scale))

        # 可用宽度按"当前窗口宽度"算，窗口拉宽后内容跟着重排（不再硬截断）
        try:
            current_width = max(self.root.winfo_width(), self.width)
        except Exception:
            current_width = self.width
        wrap = max(140, current_width - int(104 * scale))
        title_row = tk.Frame(content, bg=bg)
        title_row.pack(fill="x")
        title_color = colors["text_dim"] if card.state == "past" else colors["text"]
        title_label = tk.Label(
            title_row, text=card.title, bg=bg, fg=title_color,
            font=self.fonts.spec(self.fonts.body, "bold"), anchor="w", justify="left", wraplength=wrap,
        )
        title_label.pack(side="left", fill="x", expand=True)
        if card.kind == "course":
            tk.Label(
                title_row, text="课", bg=card.color, fg="#FFFFFF",
                font=self.fonts.spec(self.fonts.badge, "bold"), padx=int(4 * scale),
            ).pack(side="left", padx=(int(6 * scale), 0))
        if card.state == "now":
            tk.Label(
                title_row, text="进行中", bg=bg, fg=theme.readable_accent("#37C978", bg),
                font=self.fonts.spec(self.fonts.badge, "bold"),
            ).pack(side="right")

        meta = card.meta_line
        if meta:
            tk.Label(
                content, text=meta, bg=bg, fg=colors["text_dim"],
                font=self.fonts.spec(self.fonts.meta), anchor="w", justify="left", wraplength=wrap,
            ).pack(fill="x", pady=(int(1 * scale), 0))

        # 备注完整显示（多行自动换行），不再截成一行 45 字
        if card.notes:
            tk.Label(
                content, text=card.notes, bg=bg, fg=colors["text_faint"],
                font=self.fonts.spec(self.fonts.badge), anchor="w", justify="left", wraplength=wrap,
            ).pack(fill="x")

        if card.badge:
            tk.Label(
                content, text=card.badge, bg=bg,
                fg=theme.readable_accent("#E0555B", bg) if card.tentative else colors["text_faint"],
                font=self.fonts.spec(self.fonts.badge), anchor="w", justify="left", wraplength=wrap,
            ).pack(fill="x")

        bar = None
        if card.state == "now" and card.progress > 0:
            bar = tk.Canvas(content, height=int(3 * scale), bg=bg, highlightthickness=0, bd=0)
            bar.pack(fill="x", pady=(int(4 * scale), 0))

        # 卡片上的右键 = **这一条自己的菜单**（编辑 / 选中 / 完成 / 改结束时间 / 删除 / 复制）；
        # 面板空白处的右键才是全局菜单。踩过的坑：卡片原来也绑全局菜单，
        # 于是"想关掉这一条"根本没入口。
        # 左键点**整张卡的任何地方**（含时间列、时间轴、备注文字）= 选中/取消选中；
        # 选中之后按热键就是给这一条补充备注。
        for widget in _descendants(row):
            widget.bind("<Button-3>", lambda event, item=card: self._card_menu(item, event))
            widget.bind("<Button-1>", lambda event, item=card: self.select_card(item))
            widget.bind("<MouseWheel>", self._on_wheel)
            widget.bind("<Double-Button-1>", lambda _e, item=card: self.show_card_detail(item))

        # 时间轴与进度条的画法存起来：内容复用时也靠它"只重画这一点"，不重建控件。
        # 注意这里**不再**每张卡都 update_idletasks()——192 个控件时那一次同步刷新
        # 就是卡顿的主要来源。高度交给 <Configure>（渲染完成后 Tk 自然会发）。
        def draw_axis(_event=None, canvas=axis, item=card, is_last=last) -> None:
            canvas.delete("all")
            height = max(canvas.winfo_height(), canvas.winfo_reqheight(), int(30 * scale))
            center = height // 2
            if not is_last:
                canvas.create_line(center + int(10 * scale) // 2, center,
                                   center + int(10 * scale) // 2, height,
                                   fill=colors["line"], width=2)
            radius = int(5 * scale) if item.state == "now" else int(4 * scale)
            canvas.create_oval(
                center - radius, center - radius, center + radius, center + radius,
                fill=item.color, outline=colors["bg"], width=2,
            )
            if item.state == "now":
                canvas.create_oval(
                    center - radius - 3, center - radius - 3, center + radius + 3, center + radius + 3,
                    outline=item.color, width=1,
                )

        axis.bind("<Configure>", draw_axis)
        draw_axis()

        def draw_bar(_event=None, canvas=bar, progress=card.progress, color=card.color) -> None:
            if canvas is None:
                return
            canvas.delete("all")
            width = max(canvas.winfo_width(), canvas.winfo_reqwidth())
            canvas.create_rectangle(0, 0, width, int(3 * scale), fill=colors["line"], outline="")
            canvas.create_rectangle(0, 0, int(width * progress), int(3 * scale), fill=color, outline="")

        if bar is not None:
            bar.bind("<Configure>", draw_bar)
            draw_bar()

        self._card_paint[card_key(card)] = (draw_axis, draw_bar)
        return row

    def _refresh_card_dynamics(self, key: tuple) -> None:
        """卡片内容没变时，只重画时间轴圆点和进度条（几毫秒，不重建控件）。"""
        painter = self._card_paint.get(key)
        if painter is None:
            return
        draw_axis, draw_bar = painter
        try:
            draw_axis()
            draw_bar()
        except tk.TclError:
            pass

    def show_card_detail(self, card: Card) -> None:
        """双击卡片看全文（面板再宽也总有装不下的长文本）。

        全局**只保留一个**详情窗：再双击别的卡片就换内容。以前每双击一次新开一个，
        用户连着看三条就叠了三层窗（他自己截的图里正好是三个）。
        """
        colors = theme.COLORS
        if getattr(self, "_detail_window", None) is not None:
            try:
                if self._detail_window.winfo_exists():
                    text = self._detail_text
                    text.configure(state="normal")
                    text.delete("1.0", "end")
                    text.insert("1.0", "\n".join(self._detail_lines(card)))
                    text.configure(state="disabled")
                    self._detail_window.title("事项详情")
                    self._detail_window.lift()
                    self._detail_window.focus_force()
                    return
            except tk.TclError:
                pass
            self._detail_window = None

        window = tk.Toplevel(self.root)
        window.title("事项详情")
        window.configure(bg=colors["bg"])
        text = tk.Text(
            window, bg=colors["card"], fg=colors["text"], relief="flat", wrap="word",
            font=self.fonts.spec(self.fonts.body), padx=14, pady=12, width=44, height=14,
        )
        text.pack(fill="both", expand=True, padx=2, pady=2)
        text.insert("1.0", "\n".join(self._detail_lines(card)))
        text.configure(state="disabled")
        window.bind("<Escape>", lambda _e: self.close_card_detail())
        self._detail_window = window
        self._detail_text = text
        # 和「修改结束时间」同样的两个坑：布局前就设 `-topmost`/抢焦点会让 Tk 按默认尺寸
        # 把窗口映射到左上角；只给 `+x+y` 又会被映射阶段的重排抹掉。
        # 所以先让控件布局出尺寸，再显式写全 WxH+X+Y，最后才置顶 + 抢焦点。
        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        window.geometry(f"{width}x{height}"
                        f"+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}")
        try:
            window.attributes("-topmost", True)
            window.lift()
            window.focus_force()
        except tk.TclError:
            pass

    @staticmethod
    def _detail_lines(card: Card) -> list[str]:
        """详情窗里的正文。

        这里**不写操作说明**（原来是「（Esc 关闭，Ctrl+A 全选复制）」这种）。
        用户原话：「把图二的那行字删掉，同时类似的解释文字都删掉，太掉价了」——
        一个只会看内容的窗口不需要教人怎么关它。
        """
        lines = [
            card.title,
            "",
            f"日期：{card.date}",
            f"时段：{card.time_label}",
        ]
        if card.location:
            lines.append(f"地点：{card.location}")
        if card.people:
            lines.append(f"人员：{card.people}")
        if card.badge:
            lines.append(f"标签：{card.badge}")
        if card.notes:
            lines.extend(["", "备注：", card.notes])
        return lines

    def close_card_detail(self) -> None:
        """关掉详情窗（取消选中时也调它）。"""
        window = getattr(self, "_detail_window", None)
        self._detail_window = None
        self._detail_text = None
        if window is None:
            return
        try:
            window.destroy()
        except tk.TclError:
            pass

    def _render_status(self) -> None:
        assert self.timeline is not None
        parts = [footer_summary(self.timeline)]
        # 课表来源（"课表：timetable.json"）按用户要求不再显示：日程表本身已经
        # 说明一切，技术性路径属于"内部细节"，不该占面板底部的版面。
        if self._pipelines:
            parts.append(f"最近整合：{self._pipelines[-1]}")
        elif self.timeline.last_run_text:
            parts.append(self.timeline.last_run_text)
        self.status_label.configure(text="\n".join(parts))

    # ------------------------------------------------------------------
    def run(self) -> None:
        self.root.mainloop()
