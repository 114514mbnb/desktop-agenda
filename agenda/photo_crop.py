"""框选照片范围：让用户自己挑照片的哪一块铺到面板标题区。

为什么需要它：程序只把照片铺在面板顶部那一条（时钟/日期/下一项 压在照片上）。
这条的宽高比是"又宽又扁"的，而用户给的照片多半是竖的或者接近方形——
直接取最上面一条，很容易取到一片天空/一面墙，看着就像"照片没生效"。
所以让用户自己框：框到哪儿，铺出来的就是哪儿。

框是**按比例**（0~1 的 x/y/w/h）返回的：对话框里显示的是缩略图，
像素坐标和原图对不上，比例则与缩放无关。

交互（和常见的截图/裁剪工具一致）：
  * 在选区**外面**按下拖动 → 重新画一个框（宽高比锁死，不会拉成别的形状）
  * 在选区**里面**按下拖动 → 整体平移
  * 「居中」按钮 → 回到最大的居中框；「用整张照片」→ 不裁，铺的时候自适应
"""

from __future__ import annotations

import tkinter as tk

#: 对话框里照片最大显示多大（逻辑像素）。面板本身才 360 宽，够看了。
MAX_VIEW_WIDTH = 720
MAX_VIEW_HEIGHT = 430
#: 选区边框
OUTLINE_WIDTH = 2


def default_box(aspect: float, width: int, height: int) -> tuple[float, float, float, float]:
    """在 `width × height` 的画面里取一个宽高比 = `aspect` 的**最大居中**框（比例值）。

    画面比目标比例更宽 → 框占满高度、左右居中；否则占满宽度、上下居中。
    """
    aspect = max(0.05, float(aspect))
    if width <= 0 or height <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    if width / height >= aspect:                    # 画面更宽 → 以高度为准
        box_w = height * aspect
        box_h = float(height)
    else:                                           # 面画更高 → 以宽度为准
        box_w = float(width)
        box_h = width / aspect
    box_w = min(box_w, float(width))
    box_h = min(box_h, float(height))
    return ((width - box_w) / 2 / width, (height - box_h) / 2 / height,
            box_w / width, box_h / height)


def clamp_box(box, *, aspect: float | None = None, size=None):
    """把框夹回画面内；给了 `aspect` + `size`（画面像素尺寸）就顺手把比例校正回来。

    **比例必须换算到像素再比**：归一化的 w/h 只有在正方形画面里才等于像素比例。
    第一版就是直接在归一化坐标里比的，一张 1146×779 的照片算出来是 4.07 而不是 2.77。
    """
    x, y, w, h = (float(value) for value in box)
    if aspect and size:
        width, height = float(size[0]), float(size[1])
        if width <= 0 or height <= 0:
            raise ValueError("size 必须是正数")
        aspect = max(0.05, float(aspect))
        left, top = x * width, y * height
        box_w = max(1.0, w * width)
        box_h = max(1.0, h * height)
        if box_w / box_h > aspect:
            box_w = box_h * aspect
        else:
            box_h = box_w / aspect
        # 装不下就整体缩到装得下 —— 缩的是两个方向，比例不变
        shrink = min(1.0, width / box_w, height / box_h)
        box_w *= shrink
        box_h *= shrink
        left = max(0.0, min(width - box_w, left))
        top = max(0.0, min(height - box_h, top))
        return (left / width, top / height, box_w / width, box_h / height)
    w = max(1e-4, min(1.0, w))
    h = max(1e-4, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return (x, y, w, h)


class CropDialog:
    """框选窗口。

    `show()` 返回 `(用户是否确认, 归一化框或 None)`。
    **两个返回值是必须的**：`None` 表示"用整张照片"，而"取消"是另一回事——
    取消不该动配置。用一个 None 表示两种意思迟早写错（第一版就是这么写的）。
    """

    def __init__(self, parent, picture, *, aspect: float, colors: dict,
                 title: str = "框选照片范围", font_family: str = "Microsoft YaHei UI"):
        self.picture = picture
        self.aspect = max(0.05, float(aspect))
        self.colors = colors
        self.font_family = font_family
        self.result = None
        self.confirmed = False

        # 照片按比例缩到显示区里
        scale = min(MAX_VIEW_WIDTH / max(1, picture.width),
                    MAX_VIEW_HEIGHT / max(1, picture.height), 1.0)
        self.view_w = max(2, int(picture.width * scale))
        self.view_h = max(2, int(picture.height * scale))
        self.box = default_box(self.aspect, self.view_w, self.view_h)
        self._drag = None

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.configure(bg=colors["bg"])
        self.window.transient(parent)
        self.window.resizable(False, False)

        tk.Label(self.window, text="拖动框选要显示在面板顶部的那一块（比例已按面板锁定）",
                 bg=colors["bg"], fg=colors["text_dim"],
                 font=(font_family, 9)).pack(anchor="w", padx=14, pady=(12, 6))

        self.canvas = tk.Canvas(self.window, width=self.view_w, height=self.view_h,
                                bg=colors["card"], highlightthickness=1,
                                highlightbackground=colors["border"], bd=0)
        self.canvas.pack(padx=14)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        buttons = tk.Frame(self.window, bg=colors["bg"])
        buttons.pack(fill="x", padx=14, pady=(10, 14))

        def button(text, command, primary=False):
            widget = tk.Button(
                buttons, text=text, command=command, relief="flat", bd=0,
                bg=colors["accent"] if primary else colors["card"],
                fg="#FFFFFF" if primary else colors["text"],
                padx=14, pady=6, cursor="hand2",
                font=(font_family, 9, "bold" if primary else "normal"))
            widget.pack(side="left", padx=(0, 6))
            return widget

        button("保存", self._confirm, primary=True)
        button("居中", lambda: self._set_box(default_box(self.aspect, self.view_w, self.view_h)))
        button("用整张照片", self._use_whole)
        tk.Button(buttons, text="取消", command=self.window.destroy, relief="flat", bd=0,
                  bg=colors["bg"], fg=colors["text_dim"], padx=14, pady=6, cursor="hand2",
                  font=(font_family, 9)).pack(side="right")

        self._photo = None
        self._paint_photo()
        self._redraw()
        self._center(parent)

    # -- 画 --------------------------------------------------------------
    def _paint_photo(self) -> None:
        from . import backdrop

        # 先按宽度缩，再按需要补到高度：两个方向各来一次，取整误差不会累积
        scaled = backdrop.scale_to_width(self.picture, self.view_w)
        if scaled.height != self.view_h:
            scaled = backdrop.scale_to_height(scaled, self.view_h)
        self._photo = backdrop.to_photoimage(self.window, scaled)

    def _rect(self) -> tuple[float, float, float, float]:
        x, y, w, h = self.box
        return x * self.view_w, y * self.view_h, w * self.view_w, h * self.view_h

    def _redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        canvas.create_image(0, 0, image=self._photo, anchor="nw")
        left, top, width, height = self._rect()
        right, bottom = left + width, top + height
        # 选区外面压暗：Tk 没有透明度，用点阵 stipple 冒充半透明
        dim = self._dim_color()
        for x1, y1, x2, y2 in ((0, 0, self.view_w, top),
                               (0, bottom, self.view_w, self.view_h),
                               (0, top, left, bottom),
                               (right, top, self.view_w, bottom)):
            if x2 - x1 > 0 and y2 - y1 > 0:
                canvas.create_rectangle(x1, y1, x2, y2, fill=dim, outline="",
                                        stipple="gray50")
        canvas.create_rectangle(left, top, right, bottom,
                                outline=self.colors["accent"], width=OUTLINE_WIDTH)
        # 三分线，方便对齐
        for step in (1, 2):
            canvas.create_line(left + width * step / 3, top,
                               left + width * step / 3, bottom,
                               fill=self.colors["accent"], dash=(3, 4))
            canvas.create_line(left, top + height * step / 3,
                               right, top + height * step / 3,
                               fill=self.colors["accent"], dash=(3, 4))

    def _dim_color(self) -> str:
        """选区外面压暗用的颜色（配 stipple 点阵冒充半透明）。"""
        return "#000000"

    # -- 交互 ------------------------------------------------------------
    def _on_press(self, event) -> None:
        left, top, width, height = self._rect()
        inside = (left <= event.x <= left + width) and (top <= event.y <= top + height)
        if inside:
            self._drag = ("move", event.x, event.y, self.box)
        else:
            self._drag = ("draw", event.x, event.y, None)
            self._draw_to(event.x, event.y)

    def _on_drag(self, event) -> None:
        if self._drag is None:
            return
        mode, start_x, start_y, origin = self._drag
        if mode == "draw":
            self._draw_to(event.x, event.y)
            return
        origin_x, origin_y, width, height = origin
        moved_x = origin_x + (event.x - start_x) / self.view_w
        moved_y = origin_y + (event.y - start_y) / self.view_h
        self._set_box((moved_x, moved_y, width, height))

    def _on_release(self, _event) -> None:
        self._drag = None

    def _draw_to(self, x: int, y: int) -> None:
        """从按下点拖出的框：宽高比锁死，并且始终留在画面里。

        全程用**像素**算（view 是等比缩过的，像素比例才等于成品比例）。
        """
        start_x, start_y = self._drag[1], self._drag[2]
        width_px = abs(x - start_x)
        height_px = abs(y - start_y)
        if width_px <= 0 or height_px <= 0:
            return
        if width_px / height_px > self.aspect:
            width_px = height_px * self.aspect
        else:
            height_px = width_px / self.aspect
        left_px = start_x - width_px if x < start_x else min(start_x, x)
        top_px = start_y - height_px if y < start_y else min(start_y, y)
        self._set_box((left_px / self.view_w, top_px / self.view_h,
                       width_px / self.view_w, height_px / self.view_h))

    def _set_box(self, box) -> None:
        self.box = clamp_box(box, aspect=self.aspect, size=(self.view_w, self.view_h))
        self._redraw()

    # -- 收尾 ------------------------------------------------------------
    def _use_whole(self) -> None:
        self.result = None
        self.confirmed = True
        self.window.destroy()

    def _confirm(self) -> None:
        self.result = self.box
        self.confirmed = True
        self.window.destroy()

    def _center(self, parent) -> None:
        try:
            self.window.update_idletasks()
            width = self.window.winfo_reqwidth()
            height = self.window.winfo_reqheight()
            screen_w = self.window.winfo_screenwidth()
            screen_h = self.window.winfo_screenheight()
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 3)
            self.window.geometry(f"{width}x{height}+{x}+{y}")
            self.window.attributes("-topmost", True)
            self.window.lift()
            self.window.focus_force()
        except tk.TclError:
            pass

    def show(self):
        self.window.grab_set()
        self.window.wait_window()
        return self.confirmed, self.result


def ask_photo_crop(parent, picture, *, aspect: float, colors: dict,
                   title: str = "框选照片范围"):
    """弹框选窗；返回 `(是否确认, 归一化 (x, y, w, h) 或 None)`。

    `(False, _)` = 取消（别动配置）；`(True, None)` = 用整张照片。
    """
    return CropDialog(parent, picture, aspect=aspect, colors=colors, title=title).show()
