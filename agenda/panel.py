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
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

from . import theme
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
    try:
        window.transient(parent)
    except tk.TclError:
        pass
    window.grab_set()
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
    _center_over(window, parent)
    parent.wait_window(window)
    return result["value"]


def _center_over(window: tk.Toplevel, parent) -> None:
    try:
        window.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - window.winfo_width()) // 2)
        y = parent.winfo_rooty() + 80
        window.geometry(f"+{max(0, x)}+{max(0, y)}")
    except tk.TclError:
        pass


class AgendaPanel:
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
        resizable: bool = True,
    ):
        self.data_dir = Path(data_dir)
        self.pipeline = Pipeline(self.data_dir)
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

        self._place_window()
        self._build_widgets()
        self.timeline: Timeline | None = None
        self._hidden_until: dt.datetime | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int, int] | None = None
        self._pipelines: list[str] = []

        theme.apply_windows_flourishes(self.root)
        self.refresh()
        try:
            self.root.after(300, self.start_drop_target)     # 拖放接收（拖文件进来就录入）
            self.root.after(320, self.start_foreground_watch)  # 100ms 快查：回屏幕立刻出现
        except Exception:
            pass
        if autostart_pipeline:
            self.root.after(1500, self.run_pipeline)
        self.root.after(self.refresh_ms, self._tick)
        if self.pipeline_ms:
            self.root.after(self.pipeline_ms, self._pipeline_tick)
        self.root.after(20_000, self._reminder_tick)
        self.root.after(700, self._watch_stop_flag)
        if self.window_mode != "topmost":
            self.root.after(400, self._init_desktop_layer)
            self.root.after(DESKTOP_POLL_MS, self._desktop_watch)
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
        header = tk.Frame(self.shell, bg=colors["bg_soft"], height=int(90 * self.scale))
        header.pack_propagate(False)

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

        for widget in (header, self.clock_label, self.date_label, self.next_label):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)
        for widget in (self.shell, self.canvas, self.inner, body,
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
            else:
                self._toast("客户端未能启动，请尝试桌面上的「桌面日程」快捷方式")
        except Exception as error:  # noqa: BLE001
            self._toast(f"无法唤起客户端：{error}")

    # -- 单条日程的右键操作 ----------------------------------------------
    def _card_menu(self, card: Card, event) -> None:
        """某一条日程的右键菜单：完成 / 改结束时间 / 删除 / 复制。"""
        menu = tk.Menu(self.root, tearoff=0)
        head = card.title if len(card.title) <= 18 else card.title[:17] + "…"
        menu.add_command(label=f"—— {head} ——", state="disabled")
        menu.add_separator()
        menu.add_command(label="标记为已完成", command=lambda: self.card_done(card))
        menu.add_command(label="修改结束时间…", command=lambda: self.card_edit_end(card))
        menu.add_command(label="复制内容", command=lambda: self.card_copy(card))
        menu.add_separator()
        menu.add_command(label="删除此条…", command=lambda: self.card_delete(card))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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

    def card_delete(self, card: Card) -> None:
        """彻底删除这一条（会问一次）。"""
        if card.kind == "course":
            self._toast("课程请在客户端「课程表」页删除")
            return
        if not card.event_id:
            return
        if not messagebox.askyesno("删除日程", f"确定删除「{card.title}」？", parent=self.root):
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

    def _do_drag(self, event) -> None:
        if self._drag_origin is None:
            return
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
        except tk.TclError:
            pass

    def _on_canvas_configure(self, event) -> None:
        try:
            self.canvas.itemconfigure(self._inner_id, width=event.width)
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
        # 宽度变了要重排卡片（换行位置跟着变）
        self.refresh()

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

    def _apply_foreground_change(self) -> None:
        """按当前前台状态调整面板（用户切回桌面时几乎无感地出现）。"""
        if getattr(self, "_closing", False):
            return
        try:
            from . import winlayer
            hidden = self._hidden_by_watcher
            # 「常驻待机」模式（desktop_only=False）：全屏应用在前台也不隐身，
            # 面板一直待在那儿。给游戏党之外的用户用（比如双屏、或者就想一直看见）。
            if self.desktop_only and winlayer.fullscreen_foreground():
                if not hidden:
                    # 挪到屏幕外而不是 withdraw：回来时才能"瞬间"（见 show_now）
                    self.hide_instant()
                    self._hidden_by_watcher = True
                return
            if hidden:
                self.show_now()
                self._hidden_by_watcher = False
            if not self._user_hidden and self._hwnd:
                # 被"显示桌面"最小化了 → 还原（无论前台是不是桌面）
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

    def _build_footer_menu(self) -> None:
        """底部状态栏的右键菜单。

        需求原话：「我要求右键状态栏标识可以退出应用，呼出客户端窗口，呼出快速录入群消息」。
        踩过的坑：底部那条（状态 + 操作提示）原来**没有绑任何菜单**——在那儿右键只有一片空白，
        既退不出去也找不到控制台。所以这里单独给它一套。
        """
        self.footer_menu = tk.Menu(self.root, tearoff=0)
        self.footer_menu.add_command(label="打开客户端窗口", command=self.open_console)
        self.footer_menu.add_command(label="快速录入群消息…", command=self.quick_entry)
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
        self._destroy_festival()
        self._cancel_jobs()
        self._save_geometry()
        self.root.destroy()

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
        if self._data_changed():
            self.refresh()
        self.root.after(700, self._watch_stop_flag)

    def _data_changed(self) -> bool:
        """events.json / timetable.json 有没有被别处改过（按 mtime 判断）。"""
        stamps: list[tuple[str, float]] = []
        # calendar.json 也要盯：用户在控制台改完假期/调休，面板 700ms 内就该变样
        for name in ("events.json", "timetable.json", "calendar.json"):
            try:
                stamps.append((name, (self.data_dir / name).stat().st_mtime))
            except OSError:
                stamps.append((name, 0.0))
        previous = getattr(self, "_data_stamps", None)
        self._data_stamps = stamps
        return previous is not None and previous != stamps

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
        """系统唤醒（解锁/亮屏/分辨率变化）时的即时恢复。"""
        if getattr(self, "_closing", False) or getattr(self, "_in_wake", False):
            return
        self._in_wake = True
        try:
            self._apply_foreground_change()
        finally:
            self._in_wake = False

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

    def show_now(self) -> None:
        """瞬间把面板放出来。

        为什么不用 `withdraw()` + `deiconify()`：那条路要等下一次 `_tick` 才把内容重画，
        实测恢复要 0.5 秒以上，用户的原话是"恢复显示的延迟太高了"。
        改成**只是把窗口挪回屏幕坐标**——窗口一直在（没 withdraw），
        所以挪回来就是一次 SetWindowPos，肉眼看不到延迟。
        """
        self._hidden_offscreen = False
        x, y = self._onscreen_position()
        self.root.geometry(f"+{x}+{y}")
        if self._hwnd:
            from . import winlayer
            winlayer.is_minimized(self._hwnd) and winlayer.restore_window(self._hwnd)
            if self.pet_mode:
                winlayer.raise_to_top_of_normal(self._hwnd)

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
        if self.timeline.festival is not None:
            return self.timeline.festival.accent
        if self.timeline.holiday is not None:
            return "#F2994A"
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
            self.next_label.configure(text="暂无后续安排", fg=theme.COLORS["text_faint"])
            return
        card = item.card
        head = "进行中 " if item.started else "下一项 "
        title = card.title if len(card.title) <= 10 else card.title[:9] + "…"
        text = f"{head}{item.text()} · {title}"
        if card.start:
            text += f"（{card.start}）"
        # 用真实字体度量截断：贴边面板没有省略号，超出可用宽度就会被切掉
        available = self.width - int(28 * self.scale)
        self.next_label.configure(
            text=self._fit_text(text, available),
            fg=theme.COLORS["accent"] if not item.started else "#37C978",
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
        if not self.timeline.total_cards:
            for child in self.inner.winfo_children():
                child.destroy()
            self._card_cache.clear()
            self._render_empty()
            self.canvas.yview_moveto(0.0)
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
        if not getattr(self, "_scroll_kept", False):
            self.canvas.yview_moveto(0.0)
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
            title_color = section.festival.accent
        tk.Label(
            head, text=day_heading(section), bg=head_bg, fg=title_color,
            font=self.fonts.spec(self.fonts.heading, "bold"), anchor="w",
        ).pack(side="left", padx=(int(14 * scale), 0), pady=int(4 * scale))

        # 调休小字：紧贴在日期右边，用强调色的小号字（用户点名要的
        # "在我给的日期旁边增加小字（调休X月X日日程）"）
        if section.makeup_label:
            tk.Label(
                head, text=section.makeup_label, bg=head_bg, fg="#F2994A",
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
                title_row, text="进行中", bg=bg, fg="#37C978",
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
                fg="#E0555B" if card.tentative else colors["text_faint"],
                font=self.fonts.spec(self.fonts.badge), anchor="w", justify="left", wraplength=wrap,
            ).pack(fill="x")

        bar = None
        if card.state == "now" and card.progress > 0:
            bar = tk.Canvas(content, height=int(3 * scale), bg=bg, highlightthickness=0, bd=0)
            bar.pack(fill="x", pady=(int(4 * scale), 0))

        # 卡片上的右键 = **这一条自己的菜单**（完成 / 改结束时间 / 删除 / 复制）；
        # 面板空白处的右键才是全局菜单。踩过的坑：卡片原来也绑全局菜单，
        # 于是"想关掉这一条"根本没入口。
        for widget in (card_frame, content, title_row, accent, title_label):
            widget.bind("<Button-3>", lambda event, item=card: self._card_menu(item, event))
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
        """双击卡片看全文（面板再宽也总有装不下的长文本）。"""
        colors = theme.COLORS
        window = tk.Toplevel(self.root)
        window.title("事项详情")
        window.configure(bg=colors["bg"])
        window.attributes("-topmost", True)
        text = tk.Text(
            window, bg=colors["card"], fg=colors["text"], relief="flat", wrap="word",
            font=self.fonts.spec(self.fonts.body), padx=14, pady=12, width=44, height=14,
        )
        text.pack(fill="both", expand=True, padx=2, pady=2)
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
        lines.extend(["", "（Esc 关闭，Ctrl+A 全选复制）"])
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        window.bind("<Escape>", lambda _e: window.destroy())
        window.update_idletasks()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        width = window.winfo_width()
        height = window.winfo_height()
        window.geometry(f"+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}")

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
