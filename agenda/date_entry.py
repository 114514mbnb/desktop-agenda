"""日期输入控件：**中间的短横线删不掉**。

需求原话：「我要求你把所有能够修改日期的地方中 - 改为不可删除」。

做法不是"在文本框里拦截按键"，而是**根本不给短横线成为字符的机会**：
年、月、日三个小输入框，中间那两个 `-` 是 `tk.Label`。于是：
  * 退格/删除/全选删除都碰不到分隔符（它就不是文本）；
  * 只能敲数字，敲不进字母、空格、冒号；
  * 年填满 4 位自动跳到月，月填满 2 位自动跳到日；在空的框里按退格退回上一格；
  * 直接粘贴 `2026-10-01` / `2026/10/1` / `20261001` 到任意一格，会拆开填好；
  * `get()` 永远返回补齐零的 `YYYY-MM-DD`，`is_complete()` 判断是否三项都填了。
对外接口跟 `tk.StringVar` 的用法接近（`get()` / `set()`），方便替换原来的 Entry。
"""

from __future__ import annotations

import re
import tkinter as tk

#: 年 / 月 / 日各自的宽度（字符）
YEAR_LEN, MONTH_LEN, DAY_LEN = 4, 2, 2

_SEPARATED_RE = re.compile(r"\s*(\d{2,4})\s*\D{1,2}\s*(\d{1,2})\s*\D{1,2}\s*(\d{1,2})\s*")
_SEPARATOR_RE = re.compile(r"[^\d]")


def _digits(text: str, limit: int) -> str:
    return "".join(ch for ch in str(text or "") if ch.isdigit())[:limit]


def _split(text: str) -> tuple[str, str, str]:
    """把各种写法拆成（年, 月, 日）。

    注意**不能**用"能匹配上就取"的正则：`2026-10` 这种"只填了一半"的输入
    在宽松正则下会被拆成 202 / 6 / 10，凭空造出一个日期来。所以：
      * 带分隔符的，必须**整串**匹配上三段才算数；
      * 否则退回"只取数字、按 4/2/2 切"，填一半就只有一半。
    """
    raw = str(text or "").strip()
    if not raw:
        return "", "", ""
    if _SEPARATOR_RE.search(raw):
        match = _SEPARATED_RE.fullmatch(raw)
        if match:
            return match.group(1), match.group(2), match.group(3)
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[:4], digits[4:6], digits[6:8]


class DateEntry(tk.Frame):
    """三个输入框拼出的日期输入：`2026` `-` `10` `-` `01`。"""

    def __init__(self, parent, *, value="", colors=None, font=None, on_change=None,
                 width_scale: float = 1.0):
        colors = colors or {}
        bg = colors.get("bg", "#101722")
        fg = colors.get("text", "#E8EDF7")
        field_bg = colors.get("card", "#1D2430")
        dim = colors.get("text_faint", "#8A93A6")
        super().__init__(parent, bg=bg)
        self.on_change = on_change
        entry_kwargs = {
            "bg": field_bg, "fg": fg, "insertbackground": fg, "relief": "flat",
            "justify": "center", "font": font,
        }

        self.year_var = tk.StringVar()
        self.month_var = tk.StringVar()
        self.day_var = tk.StringVar()
        self.year = tk.Entry(self, textvariable=self.year_var, width=int(5 * width_scale),
                             **entry_kwargs)
        self.month = tk.Entry(self, textvariable=self.month_var, width=int(3 * width_scale),
                              **entry_kwargs)
        self.day = tk.Entry(self, textvariable=self.day_var, width=int(3 * width_scale),
                            **entry_kwargs)
        # 两个短横线是 Label：它们不是文本，所以**删不掉**
        self._dash1 = tk.Label(self, text="-", bg=bg, fg=dim, font=font)
        self._dash2 = tk.Label(self, text="-", bg=bg, fg=dim, font=font)
        self.year.pack(side="left", ipady=2)
        self._dash1.pack(side="left", padx=int(2 * width_scale))
        self.month.pack(side="left", ipady=2)
        self._dash2.pack(side="left", padx=int(2 * width_scale))
        self.day.pack(side="left", ipady=2)

        for widget, limit, var in ((self.year, YEAR_LEN, self.year_var),
                                   (self.month, MONTH_LEN, self.month_var),
                                   (self.day, DAY_LEN, self.day_var)):
            widget.configure(validate="key",
                             validatecommand=(self.register(self._validate), "%P", limit, "%W"))
            var.trace_add("write", self._make_tracer(limit))
            widget.bind("<FocusIn>", lambda _e, w=widget: w.selection_range(0, "end"))
            widget.bind("<KeyRelease-BackSpace>", self._on_backspace)
            widget.bind("<<Paste>>", self._on_paste)
            widget.bind("<Control-v>", self._on_paste)

        self.set(value)

    # -- 校验与联动 ------------------------------------------------------
    def _validate(self, proposed: str, limit: str, widget_name: str) -> bool:
        """只放数字进来，且不超过各自位数（其它一律拒绝，所以敲不进 '-'）。"""
        if proposed == "":
            return True
        if not proposed.isdigit():
            return False
        if len(proposed) > int(limit):
            # 已经填满还继续敲数字：把多出来的字符丢掉，而不是整体拒绝
            target = self._var_of(widget_name)
            if target is not None:
                keep = (target.get() + proposed)[: int(limit)]
                target.set(keep)
            return False
        return True

    def _var_of(self, widget_name: str) -> tk.StringVar | None:
        if widget_name == str(self.year):
            return self.year_var
        if widget_name == str(self.month):
            return self.month_var
        if widget_name == str(self.day):
            return self.day_var
        return None

    def _make_tracer(self, limit: int):
        def tracer(*_args) -> None:
            self._normalize_and_advance(limit)
        return tracer

    def _normalize_and_advance(self, limit: int) -> None:
        widget = self.year if limit == YEAR_LEN else (self.month if limit == MONTH_LEN else self.day)
        var = {YEAR_LEN: self.year_var, MONTH_LEN: self.month_var, DAY_LEN: self.day_var}[limit]
        text = var.get()
        if len(text) == limit:
            # 月份 0/1 开头的不急着补零（还要继续敲），但超过 1 的（如 "9"）可以补
            if limit != MONTH_LEN or text[0] in "01":
                self._focus_next(widget)
        if self.on_change is not None:
            self.on_change()

    def _focus_next(self, widget: tk.Entry) -> None:
        order = (self.year, self.month, self.day)
        try:
            index = order.index(widget)
        except ValueError:
            return
        if index + 1 < len(order):
            order[index + 1].focus_set()

    def _on_backspace(self, event) -> None:
        widget = event.widget
        if widget.get():
            return
        order = (self.year, self.month, self.day)
        try:
            index = order.index(widget)
        except ValueError:
            return
        if index > 0:
            previous = order[index - 1]
            previous.focus_set()
            previous.icursor("end")
        return None

    def _on_paste(self, event) -> str | None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return None
        self.set(text)
        return "break"

    # -- 对外接口 --------------------------------------------------------
    def set(self, value) -> None:
        """接受 date / "2026-10-01" / "2026/10/1" / "20261001"；填一半也可以。"""
        if hasattr(value, "year") and hasattr(value, "month"):
            year, month, day = f"{value.year:04d}", f"{value.month:02d}", f"{value.day:02d}"
        else:
            year, month, day = _split(value)
        self.year_var.set(_digits(year, YEAR_LEN))
        self.month_var.set(_digits(month, MONTH_LEN))
        self.day_var.set(_digits(day, DAY_LEN))

    def clear(self) -> None:
        self.set("")

    def is_complete(self) -> bool:
        return (len(self.year_var.get()) == YEAR_LEN
                and len(self.month_var.get()) in (1, 2)
                and len(self.day_var.get()) in (1, 2))

    def get(self) -> str:
        """补齐零的 `YYYY-MM-DD`（填不全时返回空串，让调用方走"没填"的分支）。"""
        if not self.is_complete():
            return ""
        year, month, day = self.year_var.get(), self.month_var.get(), self.day_var.get()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    def focus_set(self) -> None:  # noqa: N802 (跟 tk 的命名保持一致)
        self.year.focus_set()

    def set_state(self, state: str) -> None:
        for widget in (self.year, self.month, self.day):
            widget.configure(state=state)
