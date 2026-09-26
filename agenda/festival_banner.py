"""节日横幅：面板顶部的彩蛋条（配色 + Canvas 装饰动画 + 点开有彩蛋）。

为什么装饰用 Canvas 画而不是贴 emoji：
实测（`tools/font_probe.py`）Tk 8.6 在这台机器上能把 emoji 画出来，但是**单色且很小**，
🏮 🧧 这种缩到 12px 基本糊成一团。Canvas 图元（圆、扇形、线）永远清晰、颜色可控、
还能动，所以"气氛"交给它，文字部分只留一个辨识度高的符号。
"""

from __future__ import annotations

import math
import tkinter as tk

from .festival import Festival, Particle

#: 动画帧间隔（ms）。60ms ≈ 16fps：够顺，又不会让 Tk 抢走面板刷新的时间
FRAME_MS = 60


def _tick_phase(seconds: float, particle: Particle) -> float:
    return particle.phase + seconds * particle.speed


def draw_particle(canvas: tk.Canvas, particle: Particle, width: int, height: int,
                  seconds: float, accent: str) -> None:
    """按类型把一个装饰图元画到画布上。坐标是 0~1 的相对值。"""
    x = particle.x * width
    y = particle.y * height
    size = particle.size * (height / 40.0) * 6.0
    phase = _tick_phase(seconds, particle)
    color = particle.color or accent

    if particle.kind == "moon":
        # 月亮：慢慢上下浮动 + 一圈光晕。
        # 光晕半径要克制（1.55×）：原来 1.9× 时圆比画布还高，被上下裁成"圆角方块"
        # （截图里一眼就看出来了）。
        y += math.sin(phase * math.pi) * height * 0.05
        canvas.create_oval(x - size * 1.55, y - size * 1.55, x + size * 1.55, y + size * 1.55,
                           fill=_mix(color, "#1A2136", 0.82), outline="")
        canvas.create_oval(x - size, y - size, x + size, y + size, fill=color, outline="")
        canvas.create_oval(x - size * 0.45, y - size * 0.55, x + size * 0.25, y - size * 0.05,
                           fill=_mix(color, "#FFFFFF", 0.35), outline="")
    elif particle.kind == "star":
        twinkle = 0.55 + 0.45 * abs(math.sin(phase * math.pi))
        radius = size * 0.5 * twinkle
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                           fill=_mix(color, "#FFFFFF", 0.25), outline="")
    elif particle.kind == "spark":
        radius = size * (0.25 + 0.2 * abs(math.sin(phase * math.pi)))
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline="")
    elif particle.kind == "firework":
        # 烟花：一圈射线，随相位一放一收
        burst = 0.35 + 0.65 * abs(math.sin(phase * math.pi))
        for index in range(8):
            angle = index * math.pi / 4 + phase
            length = size * 2.4 * burst
            canvas.create_line(x, y, x + math.cos(angle) * length, y + math.sin(angle) * length,
                               fill=color, width=1)
        canvas.create_oval(x - 1.6, y - 1.6, x + 1.6, y + 1.6, fill="#FFFFFF", outline="")
    elif particle.kind == "lantern":
        # 灯笼：椭圆灯身 + 上下横木 + 穗子，轻轻左右摆
        swing = math.sin(phase * math.pi * 2) * size * 0.5
        cx = x + swing
        canvas.create_line(x, 0, cx, y - size * 1.5, fill=_mix(color, "#000000", 0.25), width=1)
        canvas.create_rectangle(cx - size * 0.6, y - size * 1.5, cx + size * 0.6, y - size * 1.3,
                                fill=color, outline="")
        canvas.create_oval(cx - size, y - size * 1.3, cx + size, y + size * 0.9,
                           fill=color, outline=_mix(color, "#FFFFFF", 0.3))
        canvas.create_rectangle(cx - size * 0.6, y + size * 0.9, cx + size * 0.6, y + size * 1.1,
                                fill=color, outline="")
        canvas.create_line(cx, y + size * 1.1, cx, y + size * 1.9,
                           fill=_mix(color, "#FFD166", 0.4), width=1)
    elif particle.kind == "wave":
        # 云 / 水波：一段正弦细线
        points: list[float] = []
        for step in range(24):
            px = x - size * 2.2 + step * (size * 4.4 / 23)
            py = y + math.sin(step * 0.5 + phase * math.pi * 2) * size * 0.35
            points.extend((px, py))
        canvas.create_line(*points, fill=_mix(color, "#FFFFFF", 0.5), width=1, smooth=True)
    elif particle.kind == "boat":
        # 龙舟：船身 + 桅/桨
        bob = math.sin(phase * math.pi * 2) * size * 0.25
        canvas.create_arc(x - size * 1.6, y - size + bob, x + size * 1.6, y + size * 1.1 + bob,
                          start=180, extent=180, fill=color, outline="")
        canvas.create_line(x, y - size * 2.4 + bob, x, y + bob, fill=_mix(color, "#FFFFFF", 0.4), width=1)
        canvas.create_line(x, y - size * 1.9 + bob, x + size * 1.1, y - size * 0.9 + bob,
                           fill=_mix(color, "#FFFFFF", 0.4), width=1)
    elif particle.kind == "drop":
        # 雨丝：往下落的短线，落到底再从头来
        cycle = (phase * 0.35) % 1.0
        py = cycle * height
        canvas.create_line(x, py, x - size * 0.35, py + size * 0.9,
                           fill=_mix(color, "#FFFFFF", 0.45), width=1)
    elif particle.kind == "gear":
        # 齿轮：圆心 + 一圈齿，匀速转
        for index in range(8):
            angle = index * math.pi / 4 + phase * math.pi * 2
            canvas.create_line(x + math.cos(angle) * size * 0.7, y + math.sin(angle) * size * 0.7,
                               x + math.cos(angle) * size * 1.2, y + math.sin(angle) * size * 1.2,
                               fill=color, width=2)
        canvas.create_oval(x - size * 0.72, y - size * 0.72, x + size * 0.72, y + size * 0.72,
                           outline=color, width=1)
    elif particle.kind == "text" and particle.text:
        canvas.create_text(x, y, text=particle.text, fill=color,
                           font=("Microsoft YaHei UI", int(max(8, size))))


def _mix(color: str, other: str, ratio: float) -> str:
    """把两个 #RRGGBB 按比例混一混（ratio 是 other 的占比）。"""
    try:
        left = color.lstrip("#")
        right = other.lstrip("#")
        parts = []
        for index in range(3):
            a = int(left[index * 2:index * 2 + 2], 16)
            b = int(right[index * 2:index * 2 + 2], 16)
            parts.append(int(a + (b - a) * max(0.0, min(1.0, ratio))))
        return "#%02X%02X%02X" % tuple(parts)
    except (ValueError, IndexError):
        return color


#: 面板画"节日主题边框"时复用同一套混色（呼吸灯效果）
mix_color = _mix


class FestivalBanner(tk.Frame):
    """一条节日横幅：一行祝福语 + 一条会动的装饰带 +（点开后）彩蛋文字。"""

    def __init__(self, parent, hint, fonts, scale: float = 1.0, wrap_width: int = 320,
                 on_tick=None):
        self.hint = hint
        self.festival: Festival = hint.festival
        self.scale = scale
        self.fonts = fonts
        self.wrap_width = max(160, int(wrap_width))
        #: 每帧回调（面板用它让边框跟着呼吸）
        self.on_tick = on_tick
        self._seconds = 0.0
        self._job: str | None = None
        self.egg_visible = False
        super().__init__(parent, bg=self.festival.banner_bg)

        self.text_label = tk.Label(
            self, text=self._headline(), bg=self.festival.banner_bg, fg=self.festival.banner_fg,
            font=fonts.spec(fonts.meta, "bold"), anchor="w", cursor="hand2",
            justify="left", wraplength=self.wrap_width,
        )
        self.text_label.pack(fill="x", padx=int(12 * scale), pady=(int(5 * scale), 0))

        self.canvas = tk.Canvas(
            self, bg=self.festival.banner_bg, highlightthickness=0, bd=0,
            height=int(44 * scale),
        )
        self.canvas.pack(fill="x", padx=int(8 * scale), pady=(0, int(2 * scale)))

        self.egg_label = tk.Label(
            self, text=self.festival.egg, bg=self.festival.banner_bg, fg=self.festival.banner_fg,
            font=fonts.spec(fonts.badge), anchor="w", justify="left",
            wraplength=self.wrap_width,
        )
        self.tip_label = tk.Label(
            self, text="点击横幅查看节日寄语", bg=self.festival.banner_bg,
            fg=_mix(self.festival.banner_fg, self.festival.banner_bg, 0.35),
            font=fonts.spec(fonts.badge), anchor="w",
        )
        self.tip_label.pack(fill="x", padx=int(12 * scale), pady=(0, int(4 * scale)))

        for widget in (self, self.text_label, self.canvas, self.tip_label):
            widget.bind("<Button-1>", self.toggle_egg)
        self.canvas.bind("<Configure>", lambda _event: self._redraw())
        self._schedule()

    # -- 文案 ------------------------------------------------------------
    def _headline(self) -> str:
        """横幅主文案。

        不再往后面缀"（XX假期中）"：日期行已经写了「· 国庆节假期」，
        再缀一遍会把这一行顶出面板宽度（实测被截成"（国庆节"）。
        但节前/节后的"还有 N 天 / 已过 N 天"要带上 —— 这是"范围大一点"之后
        用户唯一能看出"为什么今天就有节日气氛"的线索。
        """
        text = f"{self.festival.emoji} {self.festival.name} · {self.festival.greeting}"
        timing = self.hint.timing_text()
        return f"{text}（{timing}）" if timing else text

    def update_hint(self, hint) -> None:
        """同一天之内文案会变（还有几天 → 已过几天），刷新一下就好，别重建控件。"""
        self.hint = hint
        try:
            self.text_label.configure(text=self._headline())
        except tk.TclError:
            pass

    def toggle_egg(self, _event=None) -> None:
        self.egg_visible = not self.egg_visible
        if self.egg_visible:
            self.egg_label.pack(fill="x", padx=int(12 * self.scale), pady=(0, int(4 * self.scale)),
                                before=self.tip_label)
            self.tip_label.configure(text="再次点击可收起")
        else:
            self.egg_label.pack_forget()
            self.tip_label.configure(text="点击横幅查看节日寄语")

    # -- 动画 ------------------------------------------------------------
    def _schedule(self) -> None:
        self._job = self.after(FRAME_MS, self._animate)

    def _animate(self) -> None:
        self._seconds += FRAME_MS / 1000.0
        self._redraw()
        if self.on_tick is not None:
            try:
                self.on_tick(self._seconds)
            except Exception:
                pass
        self._schedule()

    def stop(self) -> None:
        """停掉动画（面板退出时一定要调，否则 after 会在解释器析构时报错）。"""
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None

    def _redraw(self) -> None:
        try:
            self.canvas.delete("all")
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
            if width <= 1 or height <= 1:
                return
            for particle in self.festival.particles:
                draw_particle(self.canvas, particle, width, height, self._seconds,
                              self.festival.accent)
        except tk.TclError:
            pass
