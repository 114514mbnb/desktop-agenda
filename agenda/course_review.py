"""识别结果确认窗：把从教务页/PDF/ICS 拿到的课程逐条列出来，**先让人改，再导入**。

为什么要这一步：课表识别的准确率再高也会有个别格子脏（教师名混进地点、周次写在别的列…）。
WakeUp 也是"识别完让你自己核对"。与其追求一次到位，不如给一个能改的表。

同时提供**逐格编辑**（双击单元格就地改），这样确认窗本身就是课表编辑器。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from . import theme

WEEKDAY_CHOICES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class CellEditor:
    """Treeview 的就地单元格编辑：双击某格 → 浮出一个输入框 → 回车/失焦写回。"""

    def __init__(self, tree: ttk.Treeview, columns: tuple[str, ...], on_commit):
        self.tree = tree
        self.columns = columns
        self.on_commit = on_commit
        self._widget: tk.Widget | None = None
        self._item: str | None = None
        self._column: str | None = None
        tree.bind("<Double-1>", self._begin)
        tree.bind("<Button-1>", self._maybe_commit, add="+")

    def _begin(self, event) -> None:
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or not column:
            return
        index = int(column.replace("#", "")) - 1
        if not 0 <= index < len(self.columns):
            return
        field = self.columns[index]
        bbox = self.tree.bbox(item, column)
        if not bbox:
            return
        self._commit()
        x, y, width, height = bbox
        current = self.tree.set(item, field)
        if field == "weekday":
            widget: tk.Widget = ttk.Combobox(self.tree, values=WEEKDAY_CHOICES, state="readonly")
            widget.set(current)
        else:
            widget = tk.Entry(self.tree, bg="#FFFFFF", relief="solid", bd=1,
                              font=("Microsoft YaHei UI", 9), justify="left")
            widget.insert(0, current)
            widget.select_range(0, "end")
        widget.place(x=x, y=y, width=width, height=height)
        widget.focus_set()
        widget.bind("<Return>", lambda _e: self._commit())
        widget.bind("<Escape>", lambda _e: self._cancel())
        widget.bind("<FocusOut>", lambda _e: self._commit())
        self._widget = widget
        self._item = item
        self._column = field

    def _maybe_commit(self, _event) -> None:
        # 点别的地方时先落盘
        if self._widget is not None:
            self._commit()

    def _value(self) -> str:
        if self._widget is None:
            return ""
        if isinstance(self._widget, ttk.Combobox):
            return self._widget.get().strip()
        return self._widget.get().strip()  # type: ignore[attr-defined]

    def _commit(self) -> None:
        if self._widget is None or self._item is None or self._column is None:
            return
        item, field, value = self._item, self._column, self._value()
        widget = self._widget
        self._widget = self._item = self._column = None
        try:
            widget.destroy()
        except tk.TclError:
            pass
        self.tree.set(item, field, value)
        self.on_commit(item, field, value)

    def _cancel(self) -> None:
        widget = self._widget
        self._widget = self._item = self._column = None
        if widget is not None:
            try:
                widget.destroy()
            except tk.TclError:
                pass


class CourseReviewDialog(tk.Toplevel):
    """识别结果确认/编辑窗。"""

    COLUMNS = ("name", "weekday", "period", "weeks", "location", "teacher")
    HEADINGS = {
        "name": ("课程", 150), "weekday": ("星期", 60), "period": ("节次", 64),
        "weeks": ("周次", 150), "location": ("地点", 150), "teacher": ("教师", 100),
    }

    def __init__(self, parent, rows: list[dict], *, source: str, on_confirm, on_retry=None,
                 term_start: str = "", title: str = "确认识别结果"):
        super().__init__(parent)
        self.rows = [dict(row) for row in rows]
        self.source = source
        self.on_confirm = on_confirm
        self.on_retry = on_retry
        self.term_start = term_start
        self.result: list[dict] | None = None

        self.title(title)
        self.configure(bg=theme.COLORS["bg"])
        self.transient(parent)
        self.geometry(self._centered_geometry())
        self.minsize(720, 420)

        colors = theme.COLORS
        header = tk.Frame(self, bg=colors["bg_soft"])
        header.pack(fill="x")
        tk.Label(
            header, text=f"识别到 {len(self.rows)} 条，请核对后再导入", bg=colors["bg_soft"],
            fg=colors["text"], font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left", padx=12, pady=8)
        tk.Label(
            header, text=source, bg=colors["bg_soft"], fg=colors["text_faint"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=12)

        tk.Label(
            self, text="双击任意单元格可直接修改；选中行后按 Delete 删除。确认无误后点击右下角「确认导入」。",
            bg=colors["bg"], fg=colors["text_dim"], font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=12, pady=(8, 4))

        term_row = tk.Frame(self, bg=colors["bg"])
        term_row.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(
            term_row, text="第 1 教学周周一", bg=colors["bg"], fg=colors["text_dim"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        self.term_var = tk.StringVar(value=self.term_start or "")
        tk.Entry(
            term_row, textvariable=self.term_var, bg=colors["card"], fg=colors["text"],
            insertbackground=colors["text"], relief="flat", width=13,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(6, 8), ipady=2)
        tk.Label(
            term_row,
            text="← 表格中的「周次」按此推算。与教务不一致时修改此处即可，无需逐条调整。",
            bg=colors["bg"], fg=colors["text_faint"], font=("Microsoft YaHei UI", 8),
        ).pack(side="left")

        table = tk.Frame(self, bg=colors["border"])
        table.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.tree = ttk.Treeview(table, columns=self.COLUMNS, show="headings", height=14)
        for key in self.COLUMNS:
            title_text, width = self.HEADINGS[key]
            self.tree.heading(key, text=title_text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        bar = tk.Scrollbar(table, command=self.tree.yview)
        bar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())

        self.editor = CellEditor(self.tree, self.COLUMNS, self._on_cell_commit)
        self._populate()

        actions = tk.Frame(self, bg=colors["bg"])
        actions.pack(fill="x", padx=12, pady=(0, 12))
        for text, command in (
            ("新增一行", self.add_row),
            ("删除所选", self.delete_selected),
            ("重新识别", self.retry),
        ):
            self._button(actions, text, command).pack(side="left", padx=(0, 6))
        self._button(actions, "确认导入", self.confirm, primary=True).pack(side="right")
        self._button(actions, "取消", self.cancel).pack(side="right", padx=(0, 8))

        self.status = tk.Label(
            self, text="", bg=colors["bg"], fg=colors["text_faint"],
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.status.pack(fill="x", padx=12, pady=(0, 8))
        self._update_status()

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())

    # -- 界面 ------------------------------------------------------------
    def _centered_geometry(self) -> str:
        """摆在屏幕正中偏上。

        为什么不交给窗口管理器：Tk 默认会把新窗口放在 (0,0)，
        实测出现过确认窗左上角被任务栏盖住、用户点不到也关不掉的情况。
        """
        width, height = 900, 560
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
        except Exception:
            return f"{width}x{height}"
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 40)
        return f"{width}x{height}+{x}+{y}"

    def _button(self, parent, text: str, command, *, primary: bool = False) -> tk.Button:
        colors = theme.COLORS
        return tk.Button(
            parent, text=text, command=command, relief="flat", bd=0,
            bg=colors["accent"] if primary else colors["card"],
            fg="#FFFFFF" if primary else colors["text"],
            activebackground=colors["accent"] if primary else colors["card_now"],
            activeforeground="#FFFFFF" if primary else colors["text"],
            padx=12, pady=6, cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold" if primary else "normal"),
        )

    def _populate(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(index), values=self._values(row))

    def _values(self, row: dict) -> tuple:
        return tuple(str(row.get(key, "") or "") for key in self.COLUMNS)

    def _row_of(self, item: str) -> dict | None:
        try:
            return self.rows[int(item)]
        except (ValueError, IndexError):
            return None

    # -- 编辑回调 --------------------------------------------------------
    def _on_cell_commit(self, item: str, field: str, value: str) -> None:
        row = self._row_of(item)
        if row is None:
            return
        if field == "weekday" and value not in WEEKDAY_CHOICES:
            value = row.get("weekday", "")
            self.tree.set(item, field, value)
        row[field] = value
        self._update_status()

    def add_row(self) -> None:
        self.rows.append({"name": "新课程", "weekday": "周一", "period": "1-2", "weeks": "1-16"})
        self._populate()
        self._update_status()

    def delete_selected(self) -> None:
        for item in sorted(self.tree.selection(), key=lambda value: int(value), reverse=True):
            index = int(item)
            if 0 <= index < len(self.rows):
                self.rows.pop(index)
        self._populate()
        self._update_status()

    def retry(self) -> None:
        """关掉确认窗、回到识别流程（由调用方重新跑一次识别）。"""
        self.result = None
        self.destroy()
        if self.on_retry is not None:
            self.on_retry()

    def _update_status(self) -> None:
        problems = validate_rows(self.rows)
        text = f"共 {len(self.rows)} 条"
        if problems:
            text += "　⚠ " + "；".join(problems[:3])
        self.status.configure(text=text, fg="#E0555B" if problems else theme.COLORS["text_faint"])

    def confirm(self) -> None:
        problems = validate_rows(self.rows)
        if problems:
            if not messagebox.askyesno("还有问题", "\n".join(problems) + "\n\n仍然导入吗？", parent=self):
                return
        if not self.rows:
            messagebox.showwarning("没有课程", "至少保留一行后再导入。", parent=self)
            return
        self.result = self.rows
        self.destroy()
        self.on_confirm(self.rows)

    def cancel(self) -> None:
        self.result = None
        self.destroy()


def validate_rows(rows: list[dict]) -> list[str]:
    """导入前的体检：把明显不对的行指出来（不阻止导入，只提示）。"""
    problems: list[str] = []
    for index, row in enumerate(rows, start=1):
        name = str(row.get("name") or "").strip()
        if not name:
            problems.append(f"第 {index} 行没有课程名")
        weekday = str(row.get("weekday") or "").strip()
        if weekday not in WEEKDAY_CHOICES:
            problems.append(f"第 {index} 行星期「{weekday}」不合法")
        period = str(row.get("period") or "").strip()
        numbers = [int(n) for n in __import__("re").findall(r"\d{1,2}", period)]
        if not numbers or not all(1 <= n <= 20 for n in numbers):
            problems.append(f"第 {index} 行节次「{period}」不合法")
        weeks = str(row.get("weeks") or "").strip()
        if weeks and not any(ch.isdigit() for ch in weeks):
            problems.append(f"第 {index} 行周次「{weeks}」无法识别")
    return problems


def rows_to_courses(rows: list[dict]) -> list[dict]:
    """确认窗的行 → timetable.json 的 course 结构。"""
    courses: list[dict] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        course: dict = {
            "name": name,
            "weekday": str(row.get("weekday") or "周一").strip(),
            "period": str(row.get("period") or "1-2").strip(),
        }
        for key in ("weeks", "location", "teacher"):
            value = str(row.get(key) or "").strip()
            if value:
                course[key] = value
        courses.append(course)
    return courses
