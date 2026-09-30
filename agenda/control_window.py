"""客户端主控制台。

一块窗口管三件事：
  1. 面板开关、前台/托盘状态
  2. 通知：粘贴即自动识别并刷新
  3. 课表：从文件识别 / 粘贴识别 / 手动编辑，以及每节课上下课时间

课表导入只有"文件"和"粘贴"两条路（浏览器登录教务那条已整体删除，
见文件末尾的说明）：文件是静态的，可以反复解析，识别质量可控。
"""

from __future__ import annotations

import datetime as dt
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import theme
from .client_app import AppController
from .date_entry import DateEntry
from .eas import EasError, PlainTableAdapter
from .eas.importer import restore_backup
from .file_import import import_text
from .timeline import EMPTY_AGENDA_TEXT, build_timeline, day_heading, empty_hint, weeks_warning
from .timetable import DEFAULT_PERIODS, load_timetable

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

#: "外面有人在叫我们"（client.show / client.settings / client.quit）的轮询间隔。
#: 界面刷新是 5 秒一次，但请求必须更快：面板齿轮点一下要 5 秒才有反应，
#: 用户只会认为按钮坏了。200 ms 的 3 次 Path.exists() 开销可以忽略。
REQUEST_POLL_MS = 200

#: 识别走的是哪条路，用中文说清楚（用户据此判断要不要重点核对）
STRATEGY_LABELS = {
    "DOM": "页面单元格（最准确）",
    "HTML": "页面源码表格",
    "TABLE": "页面表格文本",
    "TEXT": "整页文本（兜底方案，建议逐条核对）",
    "NONE": "未读取到",
}


class ControlWindow:
    """主控制台窗口。"""

    def __init__(self, controller: AppController):
        self.controller = controller
        self.scale = theme.ensure_dpi_awareness()

        self.root = tk.Tk()
        self.root.title("桌面日程 · 控制台")
        self.root.configure(bg=theme.COLORS["bg"])
        self.root.geometry(self._initial_geometry())
        self.root.minsize(760, 560)
        theme.apply_windows_flourishes(self.root, rounded=False, dark_title=True)

        self.style = ttk.Style(self.root)
        self._setup_style()

        self._build_header()
        self._build_tabs()
        self._build_status()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._schedule_tick()
        self._schedule_request_poll()

    # ------------------------------------------------------------------
    def _initial_geometry(self) -> str:
        scale = self.scale or 1.0
        width = int(880 * scale)
        height = int(620 * scale)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = self.controller.config.console_x
        y = self.controller.config.console_y
        if x is None or y is None:
            x = max(0, int(screen_w / scale / 2 - width / scale / 2))
            y = max(0, int(screen_h / scale / 5))
        return f"{width}x{height}+{int(x)}+{int(y)}"

    def _setup_style(self) -> None:
        colors = theme.COLORS
        self.style.theme_use("clam")
        self.style.configure(
            "TNotebook", background=colors["bg"], borderwidth=0, tabmargins=(8, 6, 8, 0),
        )
        self.style.configure(
            "TNotebook.Tab", background=colors["bg_soft"], foreground=colors["text_dim"],
            padding=(18, 8), font=("Microsoft YaHei UI", 10),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", colors["card"])],
            foreground=[("selected", colors["text"])],
        )
        self.style.configure("TFrame", background=colors["bg"])
        self.style.configure(
            "Treeview", background=colors["card"], fieldbackground=colors["card"],
            foreground=colors["text"], rowheight=24, borderwidth=0,
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "Treeview.Heading", background=colors["bg_soft"], foreground=colors["text_dim"],
            font=("Microsoft YaHei UI", 9, "bold"), relief="flat",
        )
        self.style.map("Treeview", background=[("selected", colors["accent"])])
        # 日程表那一张要放得下两行文字（长标题自动折行），单独给个高一点的行高；
        # 课程表/假期表都是短文本，保持默认 24px 就行。
        self.style.configure(
            "Agenda.Treeview", background=colors["card"], fieldbackground=colors["card"],
            foreground=colors["text"], rowheight=24 * 2, borderwidth=0,
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "Agenda.Treeview.Heading", background=colors["bg_soft"], foreground=colors["text_dim"],
            font=("Microsoft YaHei UI", 9, "bold"), relief="flat",
        )
        self.style.map("Agenda.Treeview", background=[("selected", colors["accent"])])
        self.style.configure(
            "TCombobox", fieldbackground=colors["card"], background=colors["card"],
            foreground=colors["text"], arrowcolor=colors["text_dim"],
            bordercolor=colors["border"], lightcolor=colors["card"], darkcolor=colors["card"],
            selectbackground=colors["card"], selectforeground=colors["text"],
        )
        # 下拉列表（Listbox 那个弹层）不在 ttk 主题里，得单独上色，否则是刺眼的白底黑字
        self.root.option_add("*TCombobox*Listbox.background", colors["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", colors["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", colors["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.font", ("Microsoft YaHei UI", 9))
        # 只读状态下 ttk 会用 disabled 的颜色画字段，得把它也指回正常配色
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["card"]), ("disabled", colors["card_off"])],
            foreground=[("readonly", colors["text"]), ("disabled", colors["text_faint"])],
            background=[("readonly", colors["card"]), ("active", colors["card_now"])],
            arrowcolor=[("readonly", colors["text_dim"]), ("active", colors["text"])],
            selectbackground=[("readonly", colors["card"])],
            selectforeground=[("readonly", colors["text"])],
        )

    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        colors = theme.COLORS
        header = tk.Frame(self.root, bg=colors["bg_soft"])
        header.pack(fill="x")

        # 顶部两栏：左标题、右按钮；摘要单独一行，避免窗口偏窄时按钮被裁掉
        top = tk.Frame(header, bg=colors["bg_soft"])
        top.pack(fill="x")
        tk.Label(
            top, text="桌面日程", bg=colors["bg_soft"], fg=colors["text"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(side="left", padx=14, pady=(10, 0))

        right = tk.Frame(top, bg=colors["bg_soft"])
        right.pack(side="right", padx=14, pady=(10, 0))
        self.panel_button = self._button(right, "打开面板", self.toggle_panel, primary=True)
        self.panel_button.pack(side="left", padx=(0, 6))
        self._button(right, "刷新", self.refresh_all).pack(side="left", padx=(0, 6))
        # 教程入口放在**顶栏**：用户找不到教程（原话「你把新手教程放哪里了？」）——
        # 它原来只在设置页最底下，得先滚到底才看得见。
        self._button(right, "使用教程", self.open_tutorial).pack(side="left", padx=(0, 6))
        self._button(right, "最小化至托盘", self.minimize_to_tray).pack(side="left")

        self.summary_label = tk.Label(
            header, text="", bg=colors["bg_soft"], fg=colors["text_dim"],
            font=("Microsoft YaHei UI", 9), anchor="w",
        )
        self.summary_label.pack(fill="x", padx=14, pady=(0, 10))

    def _button(self, parent, text: str, command, *, primary: bool = False,
                width: int | None = None) -> tk.Button:
        colors = theme.COLORS
        return tk.Button(
            parent, text=text, command=command, relief="flat", bd=0,
            bg=colors["accent"] if primary else colors["card"],
            fg="#FFFFFF" if primary else colors["text"],
            activebackground=colors["accent"] if primary else colors["card_now"],
            activeforeground="#FFFFFF" if primary else colors["text"],
            padx=12, pady=6, cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold" if primary else "normal"),
            **({"width": width} if width else {}),
        )

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 0))
        self._build_notice_tab()
        self._build_timetable_tab()
        self._build_periods_tab()
        self._build_holiday_tab()
        self._build_settings_tab()

    # -- 通知 -----------------------------------------------------------
    def _build_notice_tab(self) -> None:
        colors = theme.COLORS
        frame = tk.Frame(self.notebook, bg=colors["bg"])
        self.notebook.add(frame, text="通知")

        # ① 录入卡：粘进来 → 看一眼 → 保存
        body = self._settings_card(frame, "粘贴群通知")
        box = tk.Frame(body, bg=colors["border"])
        box.pack(fill="both", expand=True, pady=(0, 8))
        self.notice_text = tk.Text(
            box, bg=colors["card"], fg=colors["text"], insertbackground=colors["text"],
            relief="flat", wrap="word", undo=True, font=("Microsoft YaHei UI", 10),
            padx=8, pady=6, height=9,
        )
        self.notice_text.pack(fill="both", expand=True, padx=1, pady=1)
        self.notice_text.bind("<<Modified>>", self._on_notice_modified)
        self.notice_text.bind("<Control-Return>", lambda _e: self.commit_notice())

        row = tk.Frame(body, bg=colors["bg_soft"])
        row.pack(fill="x")
        tk.Label(row, text="来源群名（可选）", bg=colors["bg_soft"], fg=colors["text_dim"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.notice_group = tk.Entry(
            row, bg=colors["card"], fg=colors["text"], insertbackground=colors["text"],
            relief="flat", width=18, font=("Microsoft YaHei UI", 9),
        )
        self.notice_group.pack(side="left", padx=(6, 12), ipady=3)
        self.notice_hint = tk.Label(row, text="", bg=colors["bg_soft"], fg=colors["text_faint"],
                                    font=("Microsoft YaHei UI", 9))
        self.notice_hint.pack(side="left")
        self._button(row, "保存并刷新", self.commit_notice, primary=True).pack(side="right")
        self._button(row, "撤销上一条", self.undo_last_notice).pack(side="right", padx=(0, 6))
        self._button(row, "清空通知", self.clear_notices).pack(side="right", padx=(0, 6))

        # ② 日程卡：已经录进来的东西
        body = self._settings_card(frame, "今日与未来一周")
        # 空状态/说明放这里：**能折行的独立标签**，不再塞进表格单元格
        # （塞进单元格会被列宽硬切，用户看到的就是半句话 —— 踩过）
        self.agenda_hint = tk.Label(
            body, text="", bg=colors["bg_soft"], fg=colors["text_faint"],
            font=("Microsoft YaHei UI", 9), justify="left", anchor="w", wraplength=820,
        )
        self.agenda_hint.pack(fill="x", pady=(0, 4))
        list_box = tk.Frame(body, bg=colors["border"])
        list_box.pack(fill="both", expand=True)
        columns = ("date", "time", "title", "place", "people", "group")
        self.agenda_tree = ttk.Treeview(list_box, columns=columns, show="headings", height=8,
                                        style="Agenda.Treeview")
        for key, title, width in (
            ("date", "日期", 110), ("time", "时段", 90), ("title", "事项", 260),
            ("place", "地点", 130), ("people", "人员", 110), ("group", "来源群", 140),
        ):
            self.agenda_tree.heading(key, text=title)
            self.agenda_tree.column(key, width=width, anchor="w", stretch=(key == "title"))
        # 先 pack 滚动条再 pack 表格：反过来的话表格的 expand 会把滚动条挤成 1px（面板上踩过）
        bar = tk.Scrollbar(list_box, command=self.agenda_tree.yview)
        bar.pack(side="right", fill="y")
        self.agenda_tree.pack(side="left", fill="both", expand=True)
        self.agenda_tree.configure(yscrollcommand=bar.set)
        # 右键某一行 = 这一条的菜单（跟面板上的一致：完成 / 改结束时间 / 删除）
        self.agenda_tree.bind("<Button-3>", self._agenda_menu)
        # 窗口大小一变就按新的列宽重排文字（自适应换行）
        self.agenda_tree.bind("<Configure>", lambda _e: self._schedule_agenda_rewrap())
        self.agenda_hint.bind(
            "<Configure>",
            lambda event: self.agenda_hint.configure(wraplength=max(240, event.width - 4)),
        )

    def _on_notice_modified(self, _event=None) -> None:
        if not self.notice_text.edit_modified():
            return
        self.notice_text.edit_modified(False)
        if self._notice_job is not None:
            self.root.after_cancel(self._notice_job)
        self._notice_job = self.root.after(900, self.preview_notice)

    _notice_job: str | None = None
    _request_job: str | None = None

    def preview_notice(self) -> None:
        self._notice_job = None
        text = self.notice_text.get("1.0", "end").strip()
        if not text:
            self.notice_hint.configure(text="")
            return
        try:
            report = self.controller.pipeline.ingest_text(
                text, group=self.notice_group.get().strip() or None,
                source_label="客户端预览", dry_run=True,
            )
        except Exception as error:  # 预览不该打断输入
            self.notice_hint.configure(text=f"解析异常：{error}", fg="#E0555B")
            return
        if report.candidates:
            titles = "；".join(event.title for event in report.events[:2])
            self.notice_hint.configure(
                text=f"识别到 {report.candidates} 条：{titles}", fg="#37C978",
            )
        else:
            self.notice_hint.configure(text="未识别出日程（请检查是否包含日期或时间）", fg="#E0555B")

    def commit_notice(self) -> None:
        text = self.notice_text.get("1.0", "end").strip()
        if not text:
            return
        try:
            report = self.controller.add_notice(
                text, group=self.notice_group.get().strip() or None,
            )
        except Exception as error:
            messagebox.showerror("保存失败", str(error), parent=self.root)
            return
        self.notice_text.delete("1.0", "end")
        self.notice_text.edit_modified(False)
        self.notice_hint.configure(text=f"已保存：{report.summary()}", fg="#37C978")
        self.controller.restart_panel() if self.controller.config.panel_visible else None
        self.refresh_agenda()

    def undo_last_notice(self) -> None:
        events = self.controller.refresh_events()
        if not events:
            self.notice_hint.configure(text="没有可撤销的记录", fg=theme.COLORS["text_faint"])
            return
        latest = max(events, key=lambda event: event.created_at or "")
        if not messagebox.askyesno("撤销", f"删除最近录入的「{latest.title}」？", parent=self.root):
            return
        store = self.controller.pipeline
        from .store import EventStore
        snapshot = EventStore.load(store.store_path)
        snapshot.events = [event for event in snapshot.events if event.id != latest.id]
        snapshot.save()
        self.controller.restart_panel() if self.controller.config.panel_visible else None
        self.refresh_agenda()
        self.notice_hint.configure(text=f"已删除「{latest.title}」", fg="#37D978")

    def clear_notices(self) -> None:
        """清空通知（课表不动）。清空前先存一份带时间戳的备份。"""
        events = self.controller.refresh_events()
        if not events:
            self.notice_hint.configure(text="没有通知可清空", fg=theme.COLORS["text_faint"])
            return
        if not messagebox.askyesno(
            "清空通知",
            f"确定删除全部 {len(events)} 条通知？（课程表不受影响）\n"
            "清空前将自动备份至 data\\events.cleared-<时间>.json。",
            parent=self.root,
        ):
            return
        from .store import EventStore
        store = EventStore.load(self.controller.pipeline.store_path)
        backup = self.controller.data_dir / f"events.cleared-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
        try:
            shutil.copyfile(self.controller.pipeline.store_path, backup)
        except OSError as error:
            messagebox.showerror("备份失败", f"备份通知至 {backup.name} 时出错：{error}", parent=self.root)
            return
        removed = store.clear()
        store.save()
        if self.controller.config.panel_visible:
            self.controller.restart_panel()
        self.refresh_agenda()
        self.refresh_header()
        self.notice_hint.configure(text=f"已清空 {removed} 条通知（备份：{backup.name}）", fg="#37D978")

    # -- 课程表 ---------------------------------------------------------
    def _build_timetable_tab(self) -> None:
        colors = theme.COLORS
        frame = tk.Frame(self.notebook, bg=colors["bg"])
        self.notebook.add(frame, text="课程表")

        # ① 导入卡
        body = self._settings_card(frame, "导入课表")
        toolbar = tk.Frame(body, bg=colors["bg_soft"])
        toolbar.pack(fill="x")
        self._button(toolbar, "从文件识别课表…", self.import_course_file, primary=True).pack(side="left", padx=(0, 6))
        self._button(toolbar, "粘贴课表…", self.paste_table_dialog).pack(side="left", padx=(0, 6))
        self._button(toolbar, "课表体检", self.check_timetable).pack(side="left", padx=(0, 6))
        self._button(toolbar, "导出 WakeUp CSV", self.export_wakeup_csv).pack(side="left", padx=(0, 6))
        self._button(toolbar, "撤销上次导入", self.undo_import).pack(side="left")

        # ② 课程卡
        body = self._settings_card(frame, "课程列表")
        self.course_hint = tk.Label(
            body, text="", bg=colors["bg_soft"], fg=colors["text_faint"],
            font=("Microsoft YaHei UI", 9), justify="left",
        )
        self.course_hint.pack(anchor="w", pady=(0, 4))

        table_box = tk.Frame(body, bg=colors["border"])
        table_box.pack(fill="both", expand=True, pady=(0, 8))
        columns = ("name", "weekday", "period", "location", "teacher", "weeks")
        self.course_tree = ttk.Treeview(table_box, columns=columns, show="headings", height=12)
        for key, title, width in (
            ("name", "课程", 190), ("weekday", "星期", 70), ("period", "节次", 80),
            ("location", "地点", 150), ("teacher", "老师", 110), ("weeks", "周数", 170),
        ):
            self.course_tree.heading(key, text=title)
            self.course_tree.column(key, width=width, anchor="w")
        bar = tk.Scrollbar(table_box, command=self.course_tree.yview)
        bar.pack(side="right", fill="y")
        self.course_tree.pack(side="left", fill="both", expand=True)
        self.course_tree.configure(yscrollcommand=bar.set)
        self.course_tree.bind("<Double-1>", lambda _e: self.edit_selected_course())
        self.course_tree.bind("<Delete>", lambda _e: self.delete_selected_course())

        edit_row = tk.Frame(body, bg=colors["bg_soft"])
        edit_row.pack(fill="x")
        for text, command in (
            ("新增课程", self.add_course_dialog),
            ("编辑所选", self.edit_selected_course),
            ("删除所选", self.delete_selected_course),
            ("清空课程表", self.clear_timetable),
        ):
            self._button(edit_row, text, command).pack(side="left", padx=(0, 6))

    def refresh_courses(self) -> None:
        for item in self.course_tree.get_children():
            self.course_tree.delete(item)
        table = load_timetable(self.controller.data_dir)
        if table is None or not table.courses:
            self.course_hint.configure(
                text=f"{EMPTY_AGENDA_TEXT}：请通过「从文件识别课表…」选择课表文件，或使用「粘贴课表…」，亦可「新增课程」手动录入"
            )
            self.course_tree.insert("", "end", values=(EMPTY_AGENDA_TEXT, "", "", "", "", ""))
            return
        self.course_hint.configure(
            text=f"数据源：{table.source} · 共 {len(table)} 门课"
                 + (f" · 第 1 周周一 = {table.term_start}" if table.term_start else "")
                 + weeks_warning(table)
        )
        # iid 用它在 timetable.json 里的**原始下标**，而不是显示序号。
        # 踩过的坑：显示是按"星期+节次"排过序的，但编辑/删除拿的是选中项的显示序号
        # 去 rows[] 里取——两者一错位，双击某门课弹出来的就是另一门课的数据，
        # 甚至取不到（文本框一片空白）。
        rows = self._course_rows()
        order = sorted(range(len(rows)),
                       key=lambda i: (_weekday_index(rows[i].get("weekday")),
                                      _period_start(rows[i].get("period")),
                                      str(rows[i].get("name") or "")))
        for position in order:
            course = rows[position]
            weeks = "每周" if course.get("weeks") is None else _weeks_text_any(course.get("weeks"))
            self.course_tree.insert("", "end", iid=str(position), values=(
                str(course.get("name") or ""),
                WEEKDAYS[_weekday_index(course.get("weekday"))],
                str(course.get("period") or ""),
                str(course.get("location") or "-"),
                str(course.get("teacher") or "-"),
                weeks,
            ))

    def _selected_course_index(self) -> int | None:
        selection = self.course_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _course_rows(self) -> list[dict]:
        payload = self.controller.load_timetable_payload()
        courses = payload.get("courses")
        return list(courses) if isinstance(courses, list) else []

    def _write_courses(self, rows: list[dict]) -> None:
        payload = self.controller.load_timetable_payload()
        payload.setdefault("termStart", dt.date.today().strftime("%Y-%m-%d"))
        payload.setdefault("periods", [[s, e] for s, e in DEFAULT_PERIODS])
        payload["courses"] = rows
        self.controller.save_timetable_payload(payload)
        self.refresh_courses()
        if self.controller.config.panel_visible:
            self.controller.restart_panel()

    def add_course_dialog(self, *, edit_index: int | None = None) -> None:
        rows = self._course_rows()
        initial = rows[edit_index] if edit_index is not None and 0 <= edit_index < len(rows) else {}

        dialog = tk.Toplevel(self.root)
        dialog.title("编辑课程" if edit_index is not None else "新增课程")
        dialog.configure(bg=theme.COLORS["bg"])
        dialog.transient(self.root)
        dialog.resizable(False, False)

        fields: dict[str, tk.Variable] = {}
        # 星期可能存成 "周三"，也可能存成 3 / "3"（导入的课表两种都有），
        # 所以要过 _weekday_index 归一，不能直接 int()。
        # 踩过的坑：这里原来写 int(initial["weekday"])，碰到 "周二" 直接抛
        # ValueError，而此时 grab_set() 已经生效——屏幕上就留下一个空白、
        # 还抢着焦点的窗口，主界面点不动，看起来就是"改不了/卡住了"。
        try:
            initial_weekday = _weekday_index(initial.get("weekday")) + 1
        except Exception:
            initial_weekday = 1
        labels = (
            ("name", "课程名称", str(initial.get("name") or "")),
            ("weekday", "星期（1-7）", str(initial_weekday)),
            ("period", "节次（如 1-2）", str(initial.get("period") or "1-2")),
            ("location", "地点", str(initial.get("location") or "")),
            ("teacher", "老师", str(initial.get("teacher") or "")),
            ("weeks", "周数（如 1-16 或 1-5、7-11单）", _weeks_text_any(initial.get("weeks"))),
        )
        body = tk.Frame(dialog, bg=theme.COLORS["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=12)
        for row_index, (key, label, value) in enumerate(labels):
            tk.Label(body, text=label, bg=theme.COLORS["bg"], fg=theme.COLORS["text_dim"],
                     font=("Microsoft YaHei UI", 9)).grid(row=row_index, column=0, sticky="w", pady=4)
            variable = tk.StringVar(value=value)
            fields[key] = variable
            tk.Entry(body, textvariable=variable, bg=theme.COLORS["card"], fg=theme.COLORS["text"],
                     insertbackground=theme.COLORS["text"], relief="flat", width=30,
                     font=("Microsoft YaHei UI", 10)).grid(row=row_index, column=1, sticky="ew", pady=4)
        body.columnconfigure(1, weight=1)

        def save() -> None:
            name = str(fields["name"].get()).strip()
            if not name:
                messagebox.showwarning("缺少课程名", "课程名称不能为空", parent=dialog)
                return
            try:
                weekday = int(str(fields["weekday"].get()).strip()) - 1
            except ValueError:
                messagebox.showwarning("星期格式有误", "星期请填写 1–7 之间的数字", parent=dialog)
                return
            if not 0 <= weekday <= 6:
                messagebox.showwarning("星期格式有误", "星期请填写 1–7 之间的数字", parent=dialog)
                return
            import re as _re
            numbers = [int(n) for n in _re.findall(r"\d+", str(fields["period"].get()))]
            if not numbers:
                messagebox.showwarning("节次格式有误", "节次请按 1-2 的格式填写", parent=dialog)
                return
            from .eas import parse_weeks
            row = {
                "name": name,
                # 统一写成 "周三" 这种形式：和导入的课表、timetable.parse_course 的
                # 解析口径一致（timetable.py 把 1-7 当"周几"、把 0-6 当下标，
                # 直接塞 0 基下标会把 "周二=1" 读成周一）。
                "weekday": WEEKDAYS[weekday],
                "period": f"{numbers[0]}-{numbers[1] if len(numbers) > 1 else numbers[0]}",
            }
            location = str(fields["location"].get()).strip()
            teacher = str(fields["teacher"].get()).strip()
            weeks_text = str(fields["weeks"].get()).strip()
            if location:
                row["location"] = location
            if teacher:
                row["teacher"] = teacher
            weeks = parse_weeks(weeks_text)
            if weeks:
                row["weeks"] = weeks_text
            current = self._course_rows()
            if edit_index is not None and 0 <= edit_index < len(current):
                current[edit_index] = row
            else:
                current.append(row)
            self._write_courses(current)
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=theme.COLORS["bg"])
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        self._button(buttons, "保存", save, primary=True).pack(side="right")
        self._button(buttons, "取消", dialog.destroy).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _e: save())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

        # grab_set 放在最后：窗口内容都建好、定位好了才抢焦点，
        # 万一上面任何一步出错，也不会留下一个抢着焦点却一片空白的窗口。
        _center_over(dialog, self.root)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        for child in body.winfo_children():
            if isinstance(child, tk.Entry):
                child.focus_set()
                break

    def edit_selected_course(self) -> None:
        index = self._selected_course_index()
        if index is None:
            return
        self.add_course_dialog(edit_index=index)

    def delete_selected_course(self) -> None:
        index = self._selected_course_index()
        if index is None:
            return
        rows = self._course_rows()
        if not 0 <= index < len(rows):
            return
        name = rows[index].get("name", "该课程")
        if not messagebox.askyesno("删除", f"删除「{name}」？", parent=self.root):
            return
        rows.pop(index)
        self._write_courses(rows)

    def clear_timetable(self) -> None:
        if not messagebox.askyesno("清空课程表", "确定清空全部课程？（可通过「撤销上次导入」恢复）", parent=self.root):
            return
        self._write_courses([])

    def undo_import(self) -> None:
        restored = restore_backup(self.controller.data_dir)
        if restored is None:
            messagebox.showinfo("没有备份", "尚未导入过课表，没有可恢复的备份。", parent=self.root)
            return
        self.refresh_courses()
        if self.controller.config.panel_visible:
            self.controller.restart_panel()
        messagebox.showinfo("已恢复", "已回到上次导入前的课表。", parent=self.root)

    def paste_table_dialog(self) -> None:
        self._text_import_dialog(
            title="粘贴课表内容",
            hint=("可粘贴自 Excel / WPS / 网页 / 教务系统复制的内容。"
                  "列顺序：课程 星期 开始节 结束节 老师 地点 周数（含表头亦可）；"
                  "整页课表网页的 HTML 同样支持。"),
            factory=None,
        )

    def _text_import_dialog(self, *, title: str, hint: str, factory=None) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=theme.COLORS["bg"])
        dialog.transient(self.root)
        dialog.geometry("680x480")
        tk.Label(dialog, text=hint, bg=theme.COLORS["bg"], fg=theme.COLORS["text_dim"],
                 font=("Microsoft YaHei UI", 9), wraplength=640, justify="left").pack(anchor="w", padx=14, pady=(12, 4))
        box = tk.Frame(dialog, bg=theme.COLORS["border"])
        box.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        text = tk.Text(box, bg=theme.COLORS["card"], fg=theme.COLORS["text"],
                       insertbackground=theme.COLORS["text"], relief="flat", wrap="none",
                       font=("Consolas", 10), undo=True)
        text.pack(fill="both", expand=True, padx=1, pady=1)
        text.focus_set()

        def apply() -> None:
            content = text.get("1.0", "end").strip()
            if not content:
                return
            try:
                if factory is not None:
                    result = factory(content)
                    dialog.destroy()
                    self._apply_import(result, source=title)
                    return
                # 没指定解析器：交给文件识别那条统一的链路（HTML / CSV / 表格文本都试）
                outcome = import_text(content, suffix=".html" if "<" in content[:2000] else ".txt")
            except EasError as error:
                messagebox.showerror("解析失败", str(error), parent=dialog)
                return
            dialog.destroy()
            if not outcome.courses:
                messagebox.showwarning(
                    "未能识别课表",
                    "\n".join(outcome.warnings) or "这段内容中没有课程结构。",
                    parent=self.root,
                )
                return
            self._apply_rows(outcome.courses, source=outcome.source or title,
                             term_start=outcome.term_start, warnings=list(outcome.warnings))

        buttons = tk.Frame(dialog, bg=theme.COLORS["bg"])
        buttons.pack(fill="x", padx=14, pady=(0, 12))
        self._button(buttons, "解析并导入", apply, primary=True).pack(side="right")
        self._button(buttons, "取消", dialog.destroy).pack(side="right", padx=(0, 8))

        # 同上：内容建完再 grab_set，避免异常时留下空白却抢焦点的窗口
        _center_over(dialog, self.root)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass

    def import_course_file(self) -> None:
        """从本地文件识别课表（.ics / .pdf / .html / .json / .csv / .txt）。"""
        from .file_import import FILE_TYPES, SUPPORTED_SUFFIXES, import_path

        paths = filedialog.askopenfilenames(
            title="选择课表文件（可多选）", filetypes=FILE_TYPES, parent=self.root,
        )
        if not paths:
            return
        rows: list[dict] = []
        warnings: list[str] = []
        sources: list[str] = []
        term_start = ""
        for raw in paths:
            path = Path(raw)
            outcome = import_path(path)
            rows.extend(outcome.courses)
            sources.append(f"{path.name}（{outcome.count} 条）")
            term_start = term_start or outcome.term_start
            warnings.extend(f"{path.name}：{line}" for line in outcome.warnings)
        if not rows:
            messagebox.showwarning(
                "未能识别课表",
                "这些文件中没有解析出课程：\n\n" + "\n".join(warnings[:8])
                + "\n\n支持的格式：" + "、".join(sorted(SUPPORTED_SUFFIXES))
                + "\n若确认是课表文件，可反馈给开发者以补充解析规则。",
                parent=self.root,
            )
            return
        self._apply_rows(rows, source="文件识别：" + "、".join(sources),
                         term_start=term_start, warnings=warnings)

    def export_wakeup_csv(self) -> None:
        csv_text = self.controller.export_wakeup_csv()
        path = filedialog.asksaveasfilename(
            title="导出为 WakeUp 模板 CSV", defaultextension=".csv",
            initialfile="课表模板.csv", filetypes=[("CSV 文件", "*.csv")], parent=self.root,
        )
        if not path:
            return
        Path(path).write_text(csv_text, encoding="utf-8-sig")
        messagebox.showinfo(
            "已导出",
            f"已保存至：\n{path}\n\n可发送至手机，使用 WakeUp 课程表的「excel导入」打开。",
            parent=self.root,
        )

    def _apply_import(self, result, *, source: str, force_review: bool | None = None,
                      on_retry=None, on_done=None):
        """把"适配器解析出来的课程对象"交给确认窗（内部转成行再走 _apply_rows）。"""
        rows = [
            {
                "name": course.name,
                "weekday": WEEKDAYS[course.weekday],
                "period": (f"{course.start_period}-{course.end_period}"
                           if course.end_period != course.start_period else str(course.start_period)),
                "weeks": _weeks_text(course.weeks) if course.weeks else "",
                "location": course.location or "",
                "teacher": course.teacher or "",
            }
            for course in result.courses
        ]
        return self._apply_rows(
            rows, source=source, force_review=force_review, on_retry=on_retry, on_done=on_done,
            warnings=list(getattr(result, "warnings", None) or []),
        )

    def _apply_rows(self, rows: list[dict], *, source: str, force_review: bool | None = None,
                    on_retry=None, on_done=None, term_start: str = "",
                    warnings: list[str] | None = None):
        """把"已经是行结构"的识别结果交给**确认窗**核对后再落盘。

        各条导入通道（文件/粘贴/CSV/浏览器）最后都汇到这里，落盘逻辑只有一份：
        课表 JSON、termStart、来源备注、刷新面板。

        on_retry：确认窗里点『重新识别』时调用的动作（浏览器导入用它再读一遍）。
        on_done：确认窗关闭后的收尾（浏览器导入用它关掉浏览器会话）。
        返回确认窗对象（关掉它之前可以拿它做断言/继续操作）。
        """
        warnings = list(warnings or [])
        log = getattr(self, "log", None)
        if warnings and callable(log):
            log("识别提示：" + "；".join(warnings[:5]))
        if not rows and callable(log):
            log("未识别到课程；确认窗口将打开，可手动添加")

        review = force_review if force_review is not None else self.controller.config.confirm_import
        if not review:
            if not rows:
                messagebox.showwarning("没有课程", "未解析出任何课程，请检查内容。", parent=self.root)
                return None
            messagebox.showinfo("导入完成", f"已导入 {len(rows)} 门课，面板已刷新。", parent=self.root)
            return self._write_rows(rows, source=source, term_start=term_start)

        from .course_review import CourseReviewDialog

        state = {"confirmed": False}

        def confirmed(edited: list[dict]) -> None:
            state["confirmed"] = True
            # 确认窗里的「第 1 教学周周一」优先于解析器猜的那个
            chosen_term = ""
            try:
                chosen_term = dialog.term_var.get().strip()
            except Exception:
                chosen_term = ""
            self._write_rows(edited, source=source, term_start=chosen_term or term_start)
            messagebox.showinfo(
                "导入完成",
                f"已导入 {len(edited)} 门课，面板已刷新。",
                parent=self.root,
            )

        def on_destroy(event) -> None:
            # <Destroy> 会为每个子控件冒泡上来，只认确认窗自己那一次
            if event.widget is not dialog:
                return
            if on_done is not None:
                on_done()
            if not state["confirmed"] and callable(log):
                log("已取消导入（课程表未改动）")

        dialog = CourseReviewDialog(
            self.root, rows, source=source, on_confirm=confirmed, on_retry=on_retry,
            term_start=term_start or self.term_start_entry.get().strip(),
        )
        dialog.bind("<Destroy>", on_destroy, add="+")
        return dialog

    def _write_rows(self, rows: list[dict], *, source: str, term_start: str = ""):
        """把行写进 timetable.json 并刷新界面（导入的最后一步，只此一份）。"""
        from .course_review import rows_to_courses

        courses = rows_to_courses(rows)
        payload = self.controller.load_timetable_payload()
        payload["courses"] = courses
        chosen = (term_start or self.term_start_entry.get().strip() or "").strip()
        if chosen:
            payload["termStart"] = chosen
            self.term_start_entry.set(chosen)
        else:
            payload.setdefault("termStart", "2026-09-07")
        if not payload.get("periods"):
            payload["periods"] = [[s, e] for s, e in DEFAULT_PERIODS]
        payload["note"] = f"来源：{source}（已人工确认）"
        self.controller.save_timetable_payload(payload)
        self.refresh_courses()
        if self.controller.config.panel_visible:
            self.controller.restart_panel()
        return courses

    # -- 上课时间 -------------------------------------------------------
    MIN_PERIODS = 1
    MAX_PERIODS = 24

    def _build_periods_tab(self) -> None:
        colors = theme.COLORS
        frame = tk.Frame(self.notebook, bg=colors["bg"])
        self.notebook.add(frame, text="上课时间")

        # ① 节数与学期卡
        body = self._settings_card(frame, "节数与学期")
        head = tk.Frame(body, bg=colors["bg_soft"])
        head.pack(fill="x")
        tk.Label(head, text="一共几节课", bg=colors["bg_soft"], fg=colors["text_dim"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.period_count_var = tk.StringVar(value=str(len(DEFAULT_PERIODS)))
        tk.Spinbox(
            head, from_=self.MIN_PERIODS, to=self.MAX_PERIODS, width=4, justify="center",
            textvariable=self.period_count_var, command=self.apply_period_count,
            bg=colors["card"], fg=colors["text"], insertbackground=colors["text"],
            relief="flat", buttonbackground=colors["card"], font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(6, 4), ipady=2)
        self._button(head, "套用节数", self.apply_period_count).pack(side="left", padx=(0, 18))

        tk.Label(head, text="学期开始（第 1 周周一）", bg=colors["bg_soft"], fg=colors["text_dim"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        # 日期控件：短横线是分隔标签，删不掉也敲不进非数字（用户要求）
        self.term_start_entry = DateEntry(head, colors=colors, font=("Microsoft YaHei UI", 9))
        self.term_start_entry.pack(side="left", padx=(6, 0))

        # ② 每节课时间卡
        body = self._settings_card(frame, "每节课时间")
        table_box = tk.Frame(body, bg=colors["border"])
        table_box.pack(fill="both", expand=True)
        self.period_canvas = tk.Canvas(table_box, bg=colors["bg"], highlightthickness=0)
        bar = tk.Scrollbar(table_box, command=self.period_canvas.yview)
        self.period_canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.period_canvas.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.period_grid = tk.Frame(self.period_canvas, bg=colors["bg"])
        self._period_window = self.period_canvas.create_window(
            (0, 0), window=self.period_grid, anchor="nw")
        self.period_grid.bind(
            "<Configure>",
            lambda _e: self.period_canvas.configure(scrollregion=self.period_canvas.bbox("all")))
        self.period_canvas.bind(
            "<Configure>",
            lambda event: self.period_canvas.itemconfigure(self._period_window, width=event.width))
        self.period_canvas.bind_all("<MouseWheel>", self._scroll_periods, add="+")

        self.period_rows: list[tuple[tk.StringVar, tk.StringVar, tk.StringVar, tk.StringVar]] = []
        self._build_period_rows(len(DEFAULT_PERIODS))

        actions = tk.Frame(frame, bg=colors["bg"])
        actions.pack(fill="x", padx=12, pady=(0, 12))
        self._button(actions, "保存课时", self.save_periods, primary=True).pack(side="left", padx=(0, 6))
        self._button(actions, "恢复默认时间", self.reset_periods).pack(side="left", padx=(0, 6))
        self._button(actions, "每节课时长相同（按第一节推算）", self.uniform_periods).pack(side="left")

    def _scroll_periods(self, event) -> None:
        try:
            self.period_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        except Exception:
            pass

    def _build_period_rows(self, count: int) -> None:
        """重建课时输入区。每节课两个时间，各由「时 + 分」两个下拉框组成。

        已有内容会**带过去**（先把当前值取出来再销毁旧控件）；
        新多出来的节若没有历史值，就用内置默认课时填。
        """
        colors = theme.COLORS
        for child in self.period_grid.winfo_children():
            child.destroy()
        # 取值而不是存变量：那些 StringVar 正跟着旧控件一起销毁
        previous = [[var.get() for var in row] for row in self.period_rows]
        if getattr(self, "_pending_periods", None):
            # 从 timetable.json 刚读出来的值：在它之后的部分才用默认值补
            pending = self._pending_periods
            for index, row in enumerate(pending):
                if index >= len(previous):
                    previous.append(list(row))
            self._pending_periods = None
        self.period_rows = []
        for index in range(count):
            column = index % 2
            row = index // 2
            cell = tk.Frame(self.period_grid, bg=colors["bg"])
            cell.grid(row=row, column=column, sticky="w", padx=(10, 26), pady=3)
            tk.Label(cell, text=f"第 {index + 1:>2} 节", bg=colors["bg"], fg=colors["text_dim"],
                     font=("Microsoft YaHei UI", 9), width=7, anchor="w").pack(side="left")
            values = list(previous[index]) if index < len(previous) else ["", "", "", ""]
            if not any(values) and index < len(DEFAULT_PERIODS):
                start, end = DEFAULT_PERIODS[index]
                values = self._split_time(start) + self._split_time(end)
            start_vars = self._time_cell(cell, values[0], values[1])
            tk.Label(cell, text="→", bg=colors["bg"], fg=colors["text_faint"],
                     font=("Microsoft YaHei UI", 9)).pack(side="left", padx=3)
            end_vars = self._time_cell(cell, values[2], values[3])
            self.period_rows.append((*start_vars, *end_vars))

    def _time_cell(self, parent, hour: str, minute: str) -> tuple[tk.StringVar, tk.StringVar]:
        """一个"时:分"输入：两个只读下拉框，中间那个冒号是标签（不用手敲）。"""
        colors = theme.COLORS
        hour_var = tk.StringVar(value=hour)
        minute_var = tk.StringVar(value=minute)
        hours = [f"{h:02d}" for h in range(24)]
        minutes = [f"{m:02d}" for m in range(0, 60, 5)]
        for variable, values, width in ((hour_var, hours, 3), (minute_var, minutes, 3)):
            ttk.Combobox(
                parent, textvariable=variable, values=values, width=width,
                state="readonly", font=("Microsoft YaHei UI", 9),
            ).pack(side="left", ipady=1)
            if variable is hour_var:
                tk.Label(parent, text=":", bg=colors["bg"], fg=colors["text"],
                         font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        return hour_var, minute_var

    @staticmethod
    def _split_time(text: str) -> list[str]:
        """"08:00" → ["08", "00"]；认不出来就给空（下面会按默认值补）。"""
        parts = str(text or "").replace("：", ":").split(":")
        if len(parts) != 2:
            return ["", ""]
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError:
            return ["", ""]
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return ["", ""]
        return [f"{hour:02d}", f"{minute:02d}"]

    def apply_period_count(self) -> None:
        try:
            count = int(str(self.period_count_var.get()).strip())
        except ValueError:
            messagebox.showwarning("节数不对", "节数请填写数字。", parent=self.root)
            return
        count = max(self.MIN_PERIODS, min(self.MAX_PERIODS, count))
        self.period_count_var.set(str(count))
        self._build_period_rows(count)

    def refresh_periods(self) -> None:
        payload = self.controller.load_timetable_payload()
        periods = payload.get("periods")
        table: list[tuple[str, str]] = []
        if isinstance(periods, list):
            for item in periods:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    table.append((str(item[0]), str(item[1])))
        if table:
            # 课表里存的节数才是事实：按它重排输入框。
            # 关键：**先把值交出去再重建**，否则重建时手里没有新值，
            # 会拿内置默认把用户刚填的时间冲掉（真踩过）。
            values = [self._split_time(start) + self._split_time(end) for start, end in table]
            count = max(self.MIN_PERIODS, min(self.MAX_PERIODS, len(table)))
            self.period_count_var.set(str(count))
            if len(self.period_rows) != count:
                self._pending_periods = values
                self._build_period_rows(count)
            for index, row in enumerate(values):
                if index >= len(self.period_rows):
                    break
                start_h, start_m, end_h, end_m = self.period_rows[index]
                start_h.set(row[0])
                start_m.set(row[1])
                end_h.set(row[2])
                end_m.set(row[3])
        elif not self.period_rows:
            self._build_period_rows(len(DEFAULT_PERIODS))
        term = payload.get("termStart")
        self.term_start_entry.set(term if term else dt.date.today())

    def _read_period_table(self) -> list[list[str]]:
        """读回课时表。

        末尾空行按"没配到那一节"处理（把节数调大但没填完是常见状态，不该拦着保存）；
        中间的缺口才算错——那说明用户填漏了。
        """
        filled: list[list[str]] = []
        gap_at: int | None = None
        for index, (start_h, start_m, end_h, end_m) in enumerate(self.period_rows, start=1):
            complete = bool(start_h.get() and start_m.get() and end_h.get() and end_m.get())
            if not complete:
                if gap_at is None:
                    gap_at = index
                continue
            if gap_at is not None:
                raise ValueError(f"第 {gap_at} 节为空、第 {index} 节却有内容——中间不可留空")
            start = f"{start_h.get()}:{start_m.get()}"
            end = f"{end_h.get()}:{end_m.get()}"
            if _minutes_of(end) <= _minutes_of(start):
                raise ValueError(f"第 {index} 节的下课时间不晚于上课时间（{start} → {end}）")
            filled.append([start, end])
        if not filled:
            raise ValueError("至少需要填写一节课的时间")
        return filled

    def save_periods(self) -> None:
        try:
            table = self._read_period_table()
        except ValueError as error:
            messagebox.showwarning("课时不对", str(error), parent=self.root)
            return
        term, term_error = self._checked_term_start()
        if term_error:
            messagebox.showwarning("学期开始不对", term_error, parent=self.root)
            return
        payload = self.controller.load_timetable_payload()
        payload["periods"] = table
        if term:
            payload["termStart"] = term
            self.term_start_entry.set(term)
        self.controller.save_timetable_payload(payload)
        # 节数记进客户端设置：面板与确认窗都按它排版
        self.period_count_var.set(str(len(table)))
        self.controller.config.period_count = len(table)
        try:
            self.controller.save()
        except Exception:
            pass
        if self.controller.config.panel_visible:
            self.controller.restart_panel()
        self.refresh_courses()
        messagebox.showinfo("已保存", f"共 {len(table)} 节课的时间已更新，面板已刷新。", parent=self.root)

    def _checked_term_start(self) -> tuple[str, str]:
        """校验"第 1 周周一"。

        必须是**周一**：周次是按它算的，填个周六会让整学期的"第几周"整体错位——
        实机踩过（填了 2026-09-05，一个周六）。
        """
        raw = self.term_start_entry.get().strip()
        if not raw:
            return "", "学期开始的年、月、日还没填完整。"
        day = None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                day = dt.datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if day is None:
            return "", f"「{raw}」不是有效日期，请检查年、月、日。"
        if day.weekday() != 0:
            monday = day - dt.timedelta(days=day.weekday())
            return monday.strftime("%Y-%m-%d"), (
                f"「{raw}」是{'一二三四五六日'[day.weekday()]}，并非周一。\n"
                f"第 1 教学周应从周一起算，已自动调整为 **{monday}**。"
            )
        return day.strftime("%Y-%m-%d"), ""

    def reset_periods(self) -> None:
        """恢复成默认课时（按当前节数；前 12 节是内置默认，多出来的自动往下顺延）。"""
        for index, (start_h, start_m, end_h, end_m) in enumerate(self.period_rows):
            if index < len(DEFAULT_PERIODS):
                start, end = DEFAULT_PERIODS[index]
            else:
                # 第 13 节往后：接着上一节往下推（每节 45 分钟 + 10 分钟课间）
                previous_end = self._row_minutes(index - 1, "end") or (12 * 60)
                start_mins = previous_end + 10
                end_mins = start_mins + 45
                start = f"{start_mins // 60 % 24:02d}:{start_mins % 60:02d}"
                end = f"{end_mins // 60 % 24:02d}:{end_mins % 60:02d}"
            start_h.set(start[:2])
            start_m.set(start[3:5])
            end_h.set(end[:2])
            end_m.set(end[3:5])

    def _row_minutes(self, index: int, which: str) -> int | None:
        if not 0 <= index < len(self.period_rows):
            return None
        row = self.period_rows[index]
        hour, minute = (row[0], row[1]) if which == "start" else (row[2], row[3])
        if not (hour.get() and minute.get()):
            return None
        return int(hour.get()) * 60 + int(minute.get())

    def uniform_periods(self) -> None:
        try:
            table = self._read_period_table()
        except ValueError as error:
            messagebox.showwarning("时间不对", str(error), parent=self.root)
            return
        first_start = _minutes_of(table[0][0])
        first_end = _minutes_of(table[0][1])
        duration = max(1, first_end - first_start)
        gap = 10
        current = first_start
        for index, (start_h, start_m, end_h, end_m) in enumerate(self.period_rows):
            if index >= len(table):
                break
            start_h.set(f"{current // 60 % 24:02d}")
            start_m.set(f"{current % 60:02d}")
            end = current + duration
            end_h.set(f"{end // 60 % 24:02d}")
            end_m.set(f"{end % 60:02d}")
            current = end + gap

    # -- 假期与调休 ------------------------------------------------------
    def _build_holiday_tab(self) -> None:
        """「假期」页：放假区间 + 调休补课。

        需求原话：「增加一个假期时间以及调休修改功能，我可以自定义时间假期时间，
        处于假期时间，日程表不显示课程表内容，但是显示群聊通知信息。调休功能可以将
        任意时间段的日程安排改为调休日的任务，在我给的日期旁边增加小字（调休X月X日日程）」
        """
        colors = theme.COLORS
        frame = tk.Frame(self.notebook, bg=colors["bg"])
        self.notebook.add(frame, text="假期")

        # ① 假期卡
        body = self._settings_card(frame, "假期")
        toolbar = tk.Frame(body, bg=colors["bg_soft"])
        toolbar.pack(fill="x", pady=(0, 6))
        self._button(toolbar, "新增假期", self.add_holiday_dialog, primary=True).pack(side="left", padx=(0, 6))
        self._button(toolbar, "按节日预填…", self.prefill_holidays).pack(side="left", padx=(0, 6))
        self._button(toolbar, "从放假通知识别…", self.paste_holiday_notice).pack(side="left", padx=(0, 6))
        self._button(toolbar, "清理已结束", self.prune_holidays).pack(side="left", padx=(0, 6))
        self._button(toolbar, "清空假期", self.clear_holidays).pack(side="left")

        self.holiday_hint = tk.Label(
            body, text="", bg=colors["bg_soft"], fg=colors["text_faint"],
            font=("Microsoft YaHei UI", 9), justify="left", anchor="w",
        )
        self.holiday_hint.pack(fill="x", pady=(0, 4))

        holiday_box = tk.Frame(body, bg=colors["border"])
        holiday_box.pack(fill="both", expand=True, pady=(0, 8))
        self.holiday_tree = ttk.Treeview(
            holiday_box, columns=("name", "start", "end", "days", "status", "note"),
            show="headings", height=6,
        )
        for key, title, width in (
            ("name", "名称", 140), ("start", "开始", 100), ("end", "结束", 100),
            ("days", "天数", 55), ("status", "状态", 70), ("note", "备注", 240),
        ):
            self.holiday_tree.heading(key, text=title)
            self.holiday_tree.column(key, width=width, anchor="w")
        holiday_bar = tk.Scrollbar(holiday_box, command=self.holiday_tree.yview)
        holiday_bar.pack(side="right", fill="y")
        self.holiday_tree.pack(side="left", fill="both", expand=True)
        self.holiday_tree.configure(yscrollcommand=holiday_bar.set)
        self.holiday_tree.bind("<Double-1>", lambda _e: self.edit_holiday())
        self.holiday_tree.bind("<Delete>", lambda _e: self.delete_holiday())

        holiday_actions = tk.Frame(body, bg=colors["bg_soft"])
        holiday_actions.pack(fill="x")
        for text, command in (("编辑所选", self.edit_holiday),
                              ("删除所选", self.delete_holiday)):
            self._button(holiday_actions, text, command).pack(side="left", padx=(0, 6))

        # ② 调休卡
        body = self._settings_card(frame, "调休（当日补哪一天的课程）")
        makeup_box = tk.Frame(body, bg=colors["border"])
        makeup_box.pack(fill="both", expand=True, pady=(0, 8))
        self.makeup_tree = ttk.Treeview(
            makeup_box, columns=("date", "weekday", "source", "source_weekday", "label"),
            show="headings", height=5,
        )
        for key, title, width in (
            ("date", "调休日（需上课）", 150), ("weekday", "星期", 70),
            ("source", "补课日期", 150), ("source_weekday", "星期", 70),
            ("label", "日期旁标注", 300),
        ):
            self.makeup_tree.heading(key, text=title)
            self.makeup_tree.column(key, width=width, anchor="w")
        makeup_bar = tk.Scrollbar(makeup_box, command=self.makeup_tree.yview)
        makeup_bar.pack(side="right", fill="y")
        self.makeup_tree.pack(side="left", fill="both", expand=True)
        self.makeup_tree.configure(yscrollcommand=makeup_bar.set)
        self.makeup_tree.bind("<Double-1>", lambda _e: self.edit_makeup())
        self.makeup_tree.bind("<Delete>", lambda _e: self.delete_makeup())

        makeup_actions = tk.Frame(body, bg=colors["bg_soft"])
        makeup_actions.pack(fill="x")
        self._button(makeup_actions, "新增调休", self.add_makeup_dialog).pack(side="left", padx=(0, 6))
        self._button(makeup_actions, "编辑所选", self.edit_makeup).pack(side="left", padx=(0, 6))
        self._button(makeup_actions, "删除所选", self.delete_makeup).pack(side="left")

    def _calendar(self):
        from .holidays import load_calendar
        return load_calendar(self.controller.data_dir)

    def _write_calendar(self, calendar) -> None:
        calendar.save(self.controller.data_dir)
        self.refresh_holidays()
        if self.controller.config.panel_visible:
            self.controller.restart_panel()

    def refresh_holidays(self) -> None:
        calendar = self._calendar()
        for tree, rows in ((self.holiday_tree, calendar.sorted_holidays()),
                           (self.makeup_tree, calendar.sorted_makeups())):
            for item in tree.get_children():
                tree.delete(item)
        today = dt.date.today()
        for index, holiday in enumerate(calendar.sorted_holidays()):
            status = calendar.holiday_status(holiday, today)
            self.holiday_tree.insert("", "end", iid=str(index), values=(
                holiday.name, str(holiday.start), str(holiday.end),
                f"{holiday.days} 天", status, holiday.note or "",
            ))
        for index, makeup in enumerate(calendar.sorted_makeups()):
            source = makeup.source
            self.makeup_tree.insert("", "end", iid=str(index), values=(
                str(makeup.date), WEEKDAYS[makeup.date.weekday()],
                str(source) if source else "（未填写）",
                WEEKDAYS[source.weekday()] if source else "-",
                makeup.text(),
            ))
        parts = [f"假期 {len(calendar.holidays)} 段 · 调休 {len(calendar.makeups)} 天"]
        if calendar.wrap_up(today) is not None:
            parts.append(f"今天在{calendar.wrap_up(today).holiday.name}的收尾期："
                         f"面板显示收心倒计时，还剩 {calendar.wrap_up(today).remaining} 天")
        finished = calendar.finished_holidays(today)
        if finished:
            parts.append(f"其中 {len(finished)} 段已结束（可点「清理已结束」）")
        elif not calendar.holidays:
            parts.append("假期内不排课，群通知照常显示；可点击「按节日预填…」生成草稿后修改")
        self.holiday_hint.configure(text="　·　".join(parts))

    def _selected_index(self, tree) -> int | None:
        selection = tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def add_holiday_dialog(self, *, edit_index: int | None = None) -> None:
        calendar = self._calendar()
        rows = calendar.sorted_holidays()
        initial = rows[edit_index] if edit_index is not None and 0 <= edit_index < len(rows) else None
        today = dt.date.today()
        fields = self._form_dialog(
            "编辑假期" if initial else "新增假期",
            (
                ("name", "假期名称", initial.name if initial else ""),
                ("start", "开始日期（年 - 月 - 日；短横线为分隔符，不可删除）",
                 initial.start if initial else today, "date"),
                ("end", "结束日期（含当日）", initial.end if initial else today, "date"),
                ("note", "备注（可留空）", initial.note if initial else ""),
            ),
        )
        if fields is None:
            return
        name = fields["name"].strip() or "假期"
        start = _parse_user_date(fields["start"], today.year)
        end = _parse_user_date(fields["end"], start.year if start else today.year)
        if start is None or end is None:
            messagebox.showwarning("日期不完整", "请把开始日期与结束日期的年、月、日都填写完整。",
                                   parent=self.root)
            return
        if end < start:
            start, end = end, start
        from .holidays import Holiday
        holiday = Holiday(name=name, start=start, end=end, note=fields["note"].strip())
        if initial is not None:
            calendar.remove_holiday(edit_index)
        calendar.add_holiday(holiday)
        self._write_calendar(calendar)

    def edit_holiday(self) -> None:
        index = self._selected_index(self.holiday_tree)
        if index is not None:
            self.add_holiday_dialog(edit_index=index)

    def delete_holiday(self) -> None:
        index = self._selected_index(self.holiday_tree)
        if index is None:
            return
        calendar = self._calendar()
        rows = calendar.sorted_holidays()
        if not 0 <= index < len(rows):
            return
        holiday = rows[index]
        if not messagebox.askyesno("删除假期", f"删除「{holiday.label()}」？", parent=self.root):
            return
        calendar.remove_holiday(index)
        self._write_calendar(calendar)

    def clear_holidays(self) -> None:
        if not messagebox.askyesno("清空假期", "确定清空已配置的假期与调休？", parent=self.root):
            return
        from .holidays import Calendar
        self._write_calendar(Calendar())

    def prune_holidays(self) -> None:
        """清理已经结束的假期（含收尾期）。

        不做成"到期自动删"：放假安排是用户自己录的事实，程序不该悄悄改数据。
        面板上的假期效果本来就是**到期自动失效**的（`holiday_on` 只在区间内命中），
        这里只是帮你把列表里翻不到的历史条目收掉。
        """
        calendar = self._calendar()
        today = dt.date.today()
        finished = calendar.finished_holidays(today)
        if not finished:
            messagebox.showinfo("没有可清理的假期",
                                "当前没有已结束的假期（收尾期内的假期会保留，方便看倒计时）。",
                                parent=self.root)
            return
        names = "、".join(item.name for item in finished[:6])
        if not messagebox.askyesno(
            "清理已结束的假期",
            f"将删除 {len(finished)} 段已结束的假期：{names}"
            + ("…" if len(finished) > 6 else "")
            + "\n\n调休记录不受影响。是否继续？",
            parent=self.root,
        ):
            return
        calendar.holidays = [item for item in calendar.holidays if item not in finished]
        self._write_calendar(calendar)

    def prefill_holidays(self) -> None:
        """按节日铺一份草稿（节日当天 + 常见连休长度），让用户改而不是从零填。"""
        from .holidays import suggested_holidays
        today = dt.date.today()
        calendar = self._calendar()
        added = 0
        for year in (today.year, today.year + 1):
            for holiday in suggested_holidays(year):
                if holiday.end < today:
                    continue
                calendar.add_holiday(holiday)
                added += 1
        if not added:
            messagebox.showinfo("没有可预填的节日", "该年份没有可用的节日数据。", parent=self.root)
            return
        if not messagebox.askyesno(
            "按节日预填",
            f"会按「节日当天 + 常见连休天数」写入 {added} 段假期草稿（覆盖今明两年剩余节日）。\n\n"
            "这些日期为**推算草稿**，请以学校或国务院通知为准。是否继续？",
            parent=self.root,
        ):
            return
        self._write_calendar(calendar)

    def add_makeup_dialog(self, *, edit_index: int | None = None) -> None:
        """新增/编辑调休：一行一组「调休日 → 补课日期」，日期都用带固定短横线的控件。

        原来是一个自由文本框里写「10月10日、10月11日」这种多值/区间写法；
        改成一行一组之后：日期只能按 年-月-日 填（短横线删不掉），
        要多天就点「添加一行」，比解析自由文本更不容易出错。
        """
        calendar = self._calendar()
        rows = calendar.sorted_makeups()
        initial = rows[edit_index] if edit_index is not None and 0 <= edit_index < len(rows) else None

        colors = theme.COLORS
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑调休" if initial else "新增调休")
        dialog.configure(bg=colors["bg"])
        dialog.transient(self.root)
        dialog.resizable(False, False)

        tk.Label(dialog, text="调休日（当日需上课）　→　补课日期（补哪一天）",
                 bg=colors["bg"], fg=colors["text_dim"],
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16, pady=(12, 2))

        rows_box = tk.Frame(dialog, bg=colors["bg"])
        rows_box.pack(fill="x", padx=16, pady=(8, 4))
        editors: list[dict] = []

        def add_row(day_value="", source_value="") -> None:
            row = tk.Frame(rows_box, bg=colors["bg"])
            row.pack(fill="x", pady=2)
            day_entry = DateEntry(row, value=day_value, colors=colors,
                                  font=("Microsoft YaHei UI", 10))
            day_entry.pack(side="left")
            tk.Label(row, text="→", bg=colors["bg"], fg=colors["text_faint"],
                     font=("Microsoft YaHei UI", 10)).pack(side="left", padx=6)
            source_entry = DateEntry(row, value=source_value, colors=colors,
                                     font=("Microsoft YaHei UI", 10))
            source_entry.pack(side="left")
            entry = {"day": day_entry, "source": source_entry, "frame": row}
            editors.append(entry)

            def remove() -> None:
                if len(editors) <= 1:
                    day_entry.clear()
                    source_entry.clear()
                    return
                editors.remove(entry)
                row.destroy()
                dialog.geometry("")           # 让窗口按内容收缩

            self._button(row, "删除本行", remove).pack(side="left", padx=(10, 0))

        if initial is not None:
            add_row(initial.date, initial.source or "")
        else:
            add_row(dt.date.today(), "")

        actions = tk.Frame(dialog, bg=colors["bg"])
        actions.pack(fill="x", padx=16, pady=(4, 0))
        self._button(actions, "添加一行", add_row).pack(side="left")

        label_row = tk.Frame(dialog, bg=colors["bg"])
        label_row.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(label_row, text="日期旁标注（留空则自动生成「调休X月X日日程」）",
                 bg=colors["bg"], fg=colors["text_dim"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        label_var = tk.StringVar(value=initial.label if initial else "")
        tk.Entry(label_row, textvariable=label_var, bg=colors["card"], fg=colors["text"],
                 insertbackground=colors["text"], relief="flat", width=24,
                 font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(8, 0), ipady=2)

        result: dict[str, object] = {"entries": None}

        def save() -> None:
            collected: list[tuple[dt.date, dt.date | None]] = []
            for entry in editors:
                day_text = entry["day"].get()
                if not day_text:
                    messagebox.showwarning("日期不完整", "每一行的调休日都要把年、月、日填完整。",
                                           parent=dialog)
                    return
                day = _parse_user_date(day_text, dt.date.today().year)
                if day is None:
                    messagebox.showwarning("日期无效", f"「{day_text}」不是有效日期。", parent=dialog)
                    return
                collected.append((day, _parse_user_date(entry["source"].get(),
                                                        day.year) if entry["source"].get() else None))
            result["entries"] = collected
            result["label"] = label_var.get().strip()
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=colors["bg"])
        buttons.pack(fill="x", padx=16, pady=(14, 14))
        self._button(buttons, "保存", save, primary=True).pack(side="right")
        self._button(buttons, "取消", dialog.destroy).pack(side="right", padx=(0, 8))
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        _center_over(dialog, self.root)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(dialog)

        entries = result.get("entries")
        if not entries:
            return
        from .holidays import Makeup
        if initial is not None:
            calendar.remove_makeup(edit_index)
        label = str(result.get("label") or "")
        multiple = len(entries) > 1
        for day, source in entries:                     # type: ignore[misc]
            calendar.add_makeup(Makeup(date=day, source=source,
                                       label="" if multiple else label))
        self._write_calendar(calendar)

    def edit_makeup(self) -> None:
        index = self._selected_index(self.makeup_tree)
        if index is not None:
            self.add_makeup_dialog(edit_index=index)

    def delete_makeup(self) -> None:
        index = self._selected_index(self.makeup_tree)
        if index is None:
            return
        calendar = self._calendar()
        calendar.remove_makeup(index)
        self._write_calendar(calendar)

    def paste_holiday_notice(self) -> None:
        """粘贴一段放假通知（学校发的原文），识别出假期区间和调休日。"""
        from .holiday_parse import parse_holiday_notice

        content = self._ask_text(
            "从放假通知识别假期",
            "请粘贴学校或国务院的放假通知原文（含「放假」「上班/补课」等表述即可）。\n"
            "识别结果将先列出供核对，确认后写入假期表。\n"
            "例：10月1日至10月8日放假调休，共8天。10月10日（星期六）上班，补10月7日（星期三）的课。",
        )
        if not content.strip():
            return
        result = parse_holiday_notice(content, default_year=dt.date.today().year)
        if not result.holidays and not result.makeups:
            messagebox.showwarning("未能识别放假安排", result.summary(), parent=self.root)
            return
        if not messagebox.askyesno(
            "识别结果（确认后写入）", result.summary() + "\n\n是否写入？", parent=self.root
        ):
            return
        calendar = self._calendar()
        from .holiday_parse import merge_into
        merge_into(calendar, result)
        self._write_calendar(calendar)

    def _ask_text(self, title: str, hint: str) -> str:
        """一个"给我一段文本"的小窗，返回内容（取消返回空串）。"""
        colors = theme.COLORS
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=colors["bg"])
        dialog.transient(self.root)
        dialog.geometry("680x460")
        tk.Label(dialog, text=hint, bg=colors["bg"], fg=colors["text_dim"],
                 font=("Microsoft YaHei UI", 9), wraplength=640, justify="left").pack(
            anchor="w", padx=14, pady=(12, 4))
        box = tk.Frame(dialog, bg=colors["border"])
        box.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        text = tk.Text(box, bg=colors["card"], fg=colors["text"],
                       insertbackground=colors["text"], relief="flat", wrap="word",
                       font=("Microsoft YaHei UI", 10), undo=True)
        text.pack(fill="both", expand=True, padx=1, pady=1)
        text.focus_set()
        result: dict[str, str] = {"value": ""}

        def apply() -> None:
            result["value"] = text.get("1.0", "end").strip()
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=colors["bg"])
        buttons.pack(fill="x", padx=14, pady=(0, 12))
        self._button(buttons, "识别", apply, primary=True).pack(side="right")
        self._button(buttons, "取消", dialog.destroy).pack(side="right", padx=(0, 8))
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        _center_over(dialog, self.root)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(dialog)
        return result["value"]

    def _form_dialog(self, title: str, fields) -> dict[str, str] | None:
        """一个通用的"几行输入框 + 保存/取消"小窗，返回 {key: 文本} 或 None。

        `fields` 里每一项是 `(key, 标签, 初值)`，也可以是
        `(key, 标签, 初值, "date")` —— 第四项写 `"date"` 时用带固定短横线的
        日期控件（`agenda/date_entry.py`），短横线删不掉，也敲不进非数字。

        和 add_course_dialog 一样：**内容建完再 grab_set**，
        免得中途异常留下一个抢着焦点的空白窗（那个坑踩过）。
        """
        colors = theme.COLORS
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=colors["bg"])
        dialog.transient(self.root)
        dialog.resizable(False, False)

        body = tk.Frame(dialog, bg=colors["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=12)
        widgets: dict[str, object] = {}
        first_field = None
        for offset, field in enumerate(fields):
            key, label, value = field[0], field[1], field[2]
            kind = field[3] if len(field) > 3 else "text"
            tk.Label(body, text=label, bg=colors["bg"], fg=colors["text_dim"],
                     font=("Microsoft YaHei UI", 9), anchor="w").grid(
                row=offset + 1, column=0, sticky="w", pady=4)
            if kind == "date":
                widget = DateEntry(body, value=value, colors=colors,
                                   font=("Microsoft YaHei UI", 10))
            else:
                widget = tk.Entry(body, textvariable=tk.StringVar(value=str(value)),
                                  bg=colors["card"], fg=colors["text"],
                                  insertbackground=colors["text"], relief="flat", width=34,
                                  font=("Microsoft YaHei UI", 10))
            widget.grid(row=offset + 1, column=1, sticky="ew", pady=4)
            widgets[key] = widget
            if first_field is None:
                first_field = widget
        body.columnconfigure(1, weight=1)

        def value_of(widget) -> str:
            if isinstance(widget, DateEntry):
                # 日期控件自己有补齐零的 get()；填不全时返回空串，由调用方判"没填"
                return widget.get() or ""
            return widget.get()

        result: dict[str, dict[str, str] | None] = {"value": None}

        def save() -> None:
            result["value"] = {key: value_of(widget) for key, widget in widgets.items()}
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=colors["bg"])
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        self._button(buttons, "保存", save, primary=True).pack(side="right")
        self._button(buttons, "取消", dialog.destroy).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _e: save())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        _center_over(dialog, self.root)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        if first_field is not None:
            first_field.focus_set()
        self.root.wait_window(dialog)
        return result["value"]

    # -- 设置 -----------------------------------------------------------
    def _settings_card(self, parent, title: str) -> tk.Frame:
        """设置页里的一块"卡片"：一行标题 + 内容区，四周一圈细边。

        为什么改成卡片：把解释性小字全删掉之后，一整页光秃秃的选项看着像半成品
        （用户反馈「设置以及教程的 UI 从一个使用者的角度来看不合适」）。
        层次改由**分组、留白和标题颜色**来表达，而不是靠小字说明。
        """
        colors = theme.COLORS
        outer = tk.Frame(parent, bg=colors["border"])           # 1px 细边
        outer.pack(fill="x", padx=14, pady=(0, 10))
        card = tk.Frame(outer, bg=colors["bg_soft"])
        card.pack(fill="x", padx=1, pady=1)
        tk.Label(card, text=title, bg=colors["bg_soft"], fg=colors["accent"],
                 font=("Microsoft YaHei UI", 10, "bold"), anchor="w",
                 ).pack(fill="x", padx=14, pady=(10, 6))
        body = tk.Frame(card, bg=colors["bg_soft"])
        body.pack(fill="x", padx=14, pady=(0, 12))
        return body

    def _build_settings_tab(self) -> None:
        colors = theme.COLORS
        frame = tk.Frame(self.notebook, bg=colors["bg"])
        self.notebook.add(frame, text="设置")

        self.panel_visible_var = tk.BooleanVar(value=self.controller.config.panel_visible)
        self.popup_var = tk.BooleanVar(value=self.controller.config.popup_reminders)
        self.desktop_only_var = tk.BooleanVar(value=self.controller.config.desktop_only)

        def check(parent, text: str, variable: tk.BooleanVar) -> None:
            tk.Checkbutton(
                parent, text=text, variable=variable, bg=colors["bg_soft"], fg=colors["text"],
                selectcolor=colors["card"], activebackground=colors["bg_soft"],
                activeforeground=colors["text"], font=("Microsoft YaHei UI", 9),
                highlightthickness=0, bd=0, anchor="w",
            ).pack(anchor="w", pady=3)

        # ① 启动
        body = self._settings_card(frame, "启动")
        check(body, "启动时显示桌面面板", self.panel_visible_var)
        check(body, "到点弹窗提醒", self.popup_var)
        self._build_autostart_settings(body)

        # ② 待机模式（二选一）
        body = self._settings_card(frame, "待机模式")
        for text, value in (("智能隐身", True), ("常驻待机", False)):
            tk.Radiobutton(
                body, text=text, variable=self.desktop_only_var, value=value,
                bg=colors["bg_soft"], fg=colors["text"], selectcolor=colors["card"],
                activebackground=colors["bg_soft"], activeforeground=colors["text"],
                font=("Microsoft YaHei UI", 9, "bold"), highlightthickness=0, bd=0,
                anchor="w", cursor="hand2",
            ).pack(anchor="w", pady=3)

        # ③④⑤ 各自成卡
        self._build_hotkey_settings(self._settings_card(frame, "剪贴板热键"))
        self._build_theme_settings(self._settings_card(frame, "面板主题"))

        # ⑥ 帮助：教程入口常驻在这儿，随时点得到
        body = self._settings_card(frame, "帮助")
        self._button(body, "查看使用教程", self.open_tutorial, primary=True).pack(side="left")
        self._button(body, "打开数据目录", self.open_data_dir).pack(side="left", padx=(6, 0))
        self._button(body, "定位教程文件", self.reveal_tutorial).pack(side="left", padx=(6, 0))

        # 底部：保存（整行，醒目）
        tk.Frame(frame, bg=colors["bg"]).pack(fill="x", pady=(2, 0))
        self._button(frame, "保存设置", self.save_settings, primary=True,
                     width=14).pack(anchor="w", padx=14, pady=(0, 12))

    # -- 课表体检 --------------------------------------------------------
    def check_timetable(self) -> None:
        """跑一遍课表体检，把结论显示在一个可复制的窗口里。

        为什么不直接弹 messagebox：报告可能有好几屏（冲突、缺字段、可疑课名…），
        用户需要**能选中、能复制**，出问题时才好发给人看。
        """
        from .timetable_check import check_timetable

        table = load_timetable(self.controller.data_dir)
        report = check_timetable(table)
        text = report.as_text(course_count=len(table.courses) if table else 0,
                              term_start=table.term_start if table else None)
        self._show_report_window(report.summary(), text)

    def _show_report_window(self, title: str, body: str) -> None:
        colors = theme.COLORS
        window = tk.Toplevel(self.root)
        window.title("课表体检")
        window.configure(bg=colors["bg"])
        window.geometry(f"{int(760 * (self.scale or 1.0))}x{int(560 * (self.scale or 1.0))}")
        tk.Label(window, text=title, bg=colors["bg"], fg=colors["text"],
                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w",
                 ).pack(fill="x", padx=14, pady=(12, 6))
        box = tk.Frame(window, bg=colors["border"])
        box.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        text = tk.Text(box, bg=colors["card"], fg=colors["text"], relief="flat",
                       wrap="word", font=("Microsoft YaHei UI", 9), padx=10, pady=8)
        scroll = tk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", body)
        text.configure(state="disabled")
        buttons = tk.Frame(window, bg=colors["bg"])
        buttons.pack(fill="x", padx=14, pady=(0, 12))

        def copy() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(body)
            messagebox.showinfo("已复制", "体检报告已复制到剪贴板。", parent=window)

        def save() -> None:
            path = filedialog.asksaveasfilename(
                title="保存体检报告", defaultextension=".txt",
                initialfile="课表体检报告.txt", filetypes=[("文本文件", "*.txt")],
                parent=window)
            if not path:
                return
            try:
                Path(path).write_text(body, encoding="utf-8")
            except OSError as error:
                messagebox.showerror("保存失败", str(error), parent=window)
                return
            messagebox.showinfo("已保存", f"已保存到：\n{path}", parent=window)

        self._button(buttons, "复制报告", copy, primary=True).pack(side="left")
        self._button(buttons, "保存为文件…", save).pack(side="left", padx=(6, 0))
        self._button(buttons, "关闭", window.destroy).pack(side="right")

    # -- 面板主题 --------------------------------------------------------
    def _build_theme_settings(self, frame) -> None:
        """主题三选一：经典深色 / 随时刻 / 我的照片。

        主题**不用点「保存设置」**：选完立刻落盘，日程表面板 1 秒内自己换过来
        （面板每 700 ms 巡检 client.json）。这样用户改主题时能马上看到效果。
        """
        colors = theme.COLORS
        from . import palettes

        config = self.controller.config
        mode = palettes.normalize_mode(getattr(config, "theme_mode", None))
        self.theme_mode_var = tk.StringVar(value=mode)
        card = colors["bg_soft"]              # 卡片底色

        for key in ("classic", "auto", "photo"):
            tk.Radiobutton(
                frame, text=palettes.MODES[key], variable=self.theme_mode_var, value=key,
                command=self.apply_theme_choice, bg=card, fg=colors["text"],
                selectcolor=colors["card"], activebackground=card,
                activeforeground=colors["text"], font=("Microsoft YaHei UI", 9, "bold"),
                highlightthickness=0, bd=0, anchor="w", cursor="hand2",
            ).pack(anchor="w", pady=3)

        photo_row = tk.Frame(frame, bg=card)
        photo_row.pack(anchor="w", pady=(8, 0))
        self._button(photo_row, "选择照片…", self.choose_theme_photo).pack(side="left")
        self._button(photo_row, "调整照片范围…", self.crop_theme_photo).pack(side="left", padx=(6, 0))
        self._button(photo_row, "清除照片", self.clear_theme_photo).pack(side="left", padx=(6, 0))

    def apply_theme_choice(self) -> None:
        """选完主题立刻生效（不用点保存设置）。"""
        from . import backdrop

        mode = self.theme_mode_var.get()
        config = self.controller.config
        if mode == "photo" and not backdrop.has_background(self.controller.data_dir):
            # 还没照片就先让他选，别写一个"照片模式但没照片"的状态
            self.choose_theme_photo()
            return
        config.theme_mode = mode
        self.controller.save()

    def choose_theme_photo(self) -> None:
        """选照片 → **框选范围** → 转存。

        中间这一步是用户点名要的：面板标题区又宽又扁，而照片多半是竖的，
        直接取最上面一条常常是一片天空/一面墙，看着像"照片没生效"。
        框选窗默认给一个"最大居中框"，比例按面板锁死，所见即所得。
        """
        from . import backdrop, photo_crop

        path = filedialog.askopenfilename(
            title="选择背景照片",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp *.gif *.webp"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        picture = backdrop.load_image(path)
        if picture is None:
            messagebox.showwarning("这张照片用不了",
                                   "这个图片格式读不出来（支持 JPG / PNG / BMP / GIF）",
                                   parent=self.root)
            return
        confirmed, box = photo_crop.ask_photo_crop(
            self.root, picture, aspect=self._photo_aspect(),
            colors=theme.COLORS, title="框选照片范围")
        if not confirmed:
            return                      # 取消：配置一个字都不动
        ok, message = backdrop.prepare_background(path, self.controller.data_dir, box=box)
        if not ok:
            messagebox.showwarning("这张照片用不了", message, parent=self.root)
            return
        config = self.controller.config
        config.theme_mode = "photo"
        config.theme_photo = backdrop.BACKGROUND_NAME
        self.controller.save()
        self.theme_mode_var.set("photo")

    def clear_theme_photo(self) -> None:
        from . import backdrop

        backdrop.clear_background(self.controller.data_dir)
        config = self.controller.config
        if getattr(config, "theme_mode", "") == "photo":
            config.theme_mode = "classic"
            self.theme_mode_var.set("classic")
        config.theme_photo = ""
        self.controller.save()

    def crop_theme_photo(self) -> None:
        """重新框选现有照片的范围（用的是转存时留下的那张未裁原图）。"""
        from . import backdrop, photo_crop

        # 用 source_file() 而不是 source_path()：老数据只存了成品、没有原图副本，
        # 自己拼路径会得到一个不存在的文件，框选完保存时直接报"找不到这个文件"。
        original = backdrop.source_file(self.controller.data_dir)
        source = backdrop.load_source(self.controller.data_dir)
        if source is None:
            messagebox.showinfo(
                "还没有照片",
                "请先点「选择照片…」挑一张；选过一次之后就能在这里调整范围了。",
                parent=self.root)
            return
        aspect = self._photo_aspect()
        confirmed, box = photo_crop.ask_photo_crop(self.root, source, aspect=aspect,
                                                  colors=theme.COLORS,
                                                  title="调整照片范围")
        if not confirmed:
            return
        ok, message = backdrop.prepare_background(original, self.controller.data_dir,
                                                 box=box)
        if not ok:
            messagebox.showwarning("这张照片用不了", message, parent=self.root)
            return
        config = self.controller.config
        config.theme_mode = "photo"
        config.theme_photo = backdrop.BACKGROUND_NAME
        self.controller.save()
        self.theme_mode_var.set("photo")

    def _photo_aspect(self) -> float:
        """照片成品的宽高比 = 面板宽度 : 标题区高度。

        框选按这个比例锁死，用户框到的就是最终看到的那一条（所见即所得）。
        面板宽度用户可调，所以现算；标题区高度的名义值在 panel.py 里。
        """
        from .panel import HEADER_STRIP_HEIGHT

        width = float(getattr(self.controller.config, "panel_width", None) or 360)
        return max(1.5, min(5.0, width / max(1, HEADER_STRIP_HEIGHT)))

    # -- 开机自启动 ------------------------------------------------------
    def _build_autostart_settings(self, frame) -> None:
        """开机自启动：只开面板（用户要求写死，界面不给改）。

        开关的真值在**文件系统**上（启动文件夹里有没有那个快捷方式），不在配置里：
        这样用户自己删掉快捷方式之后，界面上的勾也会跟着变，不会两边打架。
        """
        from . import autostart

        colors = theme.COLORS
        card = colors["bg_soft"]
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        tk.Checkbutton(
            frame, text="开机后自动打开日程表面板", variable=self.autostart_var,
            command=self.apply_autostart, bg=card, fg=colors["text"],
            selectcolor=colors["card"], activebackground=card,
            activeforeground=colors["text"], font=("Microsoft YaHei UI", 9, "bold"),
            highlightthickness=0, bd=0, anchor="w", cursor="hand2",
        ).pack(anchor="w", pady=3)      # 与"启动"卡里另外两个勾对齐（不留额外缩进）

    def apply_autostart(self) -> None:
        """勾 / 取消勾选：立刻在启动文件夹里建或删那个快捷方式。"""
        from . import autostart

        want = bool(self.autostart_var.get())
        root = getattr(self.controller, "project_root", None) or Path(__file__).resolve().parent.parent
        if want:
            ok, message = autostart.enable(root)
        else:
            ok, message = autostart.disable()
        # 以文件系统的**真实**状态回填复选框：失败时勾要弹回去，不能骗人
        self.autostart_var.set(autostart.is_enabled())
        # 成功不给回执（设置页只留功能名称）；失败必须说，否则用户只会看到"勾了没用"
        if not ok:
            messagebox.showwarning("开机自启动", message, parent=self.root)

    # -- 剪贴板热键 ------------------------------------------------------
    #: Tk 的 keysym → 我们能解析的键名（只列需要改名的，字母数字/F1-F24 直接透传）
    _KEYSYM_ALIASES = {
        "space": "Space", "Return": "Enter", "KP_Enter": "Enter", "Escape": "Esc",
        "BackSpace": "Backspace", "Insert": "Insert", "Delete": "Delete",
        "Home": "Home", "End": "End", "Prior": "PageUp", "Next": "PageDown",
        "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
        "Tab": "Tab", "ISO_Left_Tab": "Tab",
    }
    #: 单独按下这些键不算组合键（等用户按完整组合）
    _MODIFIER_KEYSYMS = {
        "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
        "Super_L", "Super_R", "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock",
        "Win_L", "Win_R", "ISO_Level3_Shift",
    }

    def _build_hotkey_settings(self, frame) -> None:
        """剪贴板全局热键的设置区。

        为什么要"检测"按钮：热键是**系统级独占**资源，被别的程序占着时登记会直接失败。
        与其让用户保存完发现按了没反应，不如当场告诉他"这个组合被占用了，换一个"。
        """
        colors = theme.COLORS
        config = self.controller.config
        card = colors["bg_soft"]              # 卡片底色（设置页用卡片分组）

        row = tk.Frame(frame, bg=card)
        row.pack(anchor="w", padx=16, pady=1)
        self.hotkey_enabled_var = tk.BooleanVar(value=bool(getattr(config, "hotkey_enabled", True)))
        tk.Checkbutton(
            row, text="启用", variable=self.hotkey_enabled_var, bg=card,
            fg=colors["text_dim"], selectcolor=colors["card"], activebackground=card,
            activeforeground=colors["text"], font=("Microsoft YaHei UI", 9),
            highlightthickness=0, bd=0,
        ).pack(side="left")

        self.hotkey_var = tk.StringVar(value=getattr(config, "hotkey", "") or "")
        self.hotkey_entry = tk.Entry(
            row, textvariable=self.hotkey_var, width=20, bg=colors["card"],
            fg=colors["text"], insertbackground=colors["text"], relief="flat",
            font=("Consolas", 10), justify="center",
        )
        self.hotkey_entry.pack(side="left", padx=(8, 6), ipady=3)
        # 直接按组合键就能填进去，不用手打（手打也支持）
        self.hotkey_entry.bind("<KeyPress>", self._capture_hotkey)
        self._button(row, "检测并保存", self.save_hotkey, primary=True).pack(side="left")

        # ⚠ 这一区**没有小字说明**（用户要求设置页只留功能名称）。
        # 热键的用法与限制写在 docs\教程.md；这里只在**出错**时借提示行说一句，
        # 因为"保存成功但按了没反应"比一句说明更难受。
        self.hotkey_hint = tk.Label(
            frame, text="", bg=card, fg=colors["text_faint"], anchor="w",
            justify="left", wraplength=int(700 * (self.scale or 1.0)),
            font=("Microsoft YaHei UI", 8),
        )
        self.hotkey_hint.pack(anchor="w", padx=16, pady=(2, 0))
        self.hotkey_hint.pack_forget()          # 没有话要说时就不占地方

        # 「选中文字就能识别」的开关：默认开。关掉它就是"只读剪贴板里已有的内容"，
        # 给不喜欢"程序替我按 Ctrl+C"的人留一条路（终端用户尤其需要）。
        select_row = tk.Frame(frame, bg=card)
        select_row.pack(anchor="w", pady=(6, 0))
        self.hotkey_selection_var = tk.BooleanVar(
            value=bool(getattr(config, "hotkey_selection", True)))
        tk.Checkbutton(
            select_row, text="按热键时优先识别「选中的文字」", variable=self.hotkey_selection_var,
            command=self.save_hotkey_selection, bg=card, fg=colors["text_dim"],
            selectcolor=colors["card"], activebackground=card,
            activeforeground=colors["text"], font=("Microsoft YaHei UI", 9),
            highlightthickness=0, bd=0, anchor="w",
        ).pack(side="left")

        # 打开设置页时就检查一遍**已保存**的组合：组合可能是在规则收紧之前设的
        # （例如只带 Shift 的 Shift+Z——它会在你打大写字母时被触发）。
        # 不在这里提示，用户只会看到"面板说临时改用了别的组合"，不知道去哪儿改。
        from . import hotkey as hotkey_mod

        saved = (getattr(config, "hotkey", "") or "").strip()
        if saved and bool(getattr(config, "hotkey_enabled", True)):
            try:
                hotkey_mod.parse(saved)
            except hotkey_mod.HotkeyError as error:
                self._set_hotkey_hint(f"当前保存的热键「{saved}」不可用：{error}", error=True)

    def save_hotkey_selection(self) -> None:
        """「优先识别选中的文字」开关：即时生效（面板 1 秒内自己读到新配置）。"""
        config = self.controller.config
        config.hotkey_selection = bool(self.hotkey_selection_var.get())
        self.controller.save()
        # 不给回执：设置页只留功能名称，开关本身就是状态
        self._set_hotkey_hint("")

    def _set_hotkey_hint(self, text: str, *, error: bool = False, faint: bool = False) -> None:
        """设置页唯一的提示行：**只在出错时露面**。

        用户要求设置页只留功能名称（「这些小字全部清除……没有任何用处，还显得冗余」），
        所以"已保存""已填入""已开启"这类回执一律不显示；空文本就把整行收起来，
        不留一行空白在那儿。
        但"这个组合被别的程序占用了"必须说 —— 否则用户看到的会是
        「保存成功但按下没反应」，那比一句说明难受得多。
        """
        if not text:
            try:
                self.hotkey_hint.pack_forget()
            except tk.TclError:
                pass
            return
        colors = theme.COLORS
        color = "#E0555B" if error else ("#37C978" if not faint else colors["text_faint"])
        try:
            self.hotkey_hint.configure(text=text, fg=color)
            if not self.hotkey_hint.winfo_manager():
                self.hotkey_hint.pack(anchor="w", padx=16, pady=(2, 0))
        except tk.TclError:
            pass

    def _capture_hotkey(self, event):
        """在输入框里按下组合键 → 自动填成规范写法。

        只认"修饰键 + 一个普通键"；单独按 Ctrl/Alt/Shift 时不动输入框（等组合完整）。
        """
        keysym = str(event.keysym)
        if keysym in self._MODIFIER_KEYSYMS:
            return "break"
        if keysym in self._KEYSYM_ALIASES:
            token = self._KEYSYM_ALIASES[keysym]
        elif len(keysym) == 1 and keysym.isalnum():
            token = keysym
        elif keysym.startswith("F") and keysym[1:].isdigit():
            token = keysym
        else:
            self._set_hotkey_hint(f"这个键暂不支持作为热键：{keysym}", error=True)
            return "break"

        parts: list[str] = []
        state = int(getattr(event, "state", 0))
        if state & 0x0004:
            parts.append("Ctrl")
        if state & (0x0008 | 0x20000):        # X11 的 Mod1 与 Windows 的 Alt
            parts.append("Alt")
        if state & 0x0001:
            parts.append("Shift")
        if not parts:
            self._set_hotkey_hint("至少要带一个修饰键（Ctrl / Alt / Shift）", error=True)
            return "break"
        text = "+".join(parts + [token])
        try:
            self.hotkey_var.set(text)
        except tk.TclError:
            pass
        return "break"                        # 别让字符落进输入框

    def save_hotkey(self) -> None:
        """检测冲突 → 保存 → 提示面板多久生效。"""
        from . import hotkey as hotkey_mod
        from .client_config import ClientConfig

        text = (self.hotkey_var.get() or "").strip()
        enabled = bool(self.hotkey_enabled_var.get())
        config = self.controller.config

        if not enabled:
            config.hotkey_enabled = False
            self.controller.save()
            self._set_hotkey_hint("")
            return

        # 保存的就是当前正用的组合时不算冲突：那是我们自己的
        current = (getattr(config, "hotkey", "") or "").strip()
        if hotkey_mod.describe(text) and hotkey_mod.describe(text) == hotkey_mod.describe(current) \
                and getattr(config, "hotkey_enabled", False):
            config.hotkey = hotkey_mod.describe(text)
            self.controller.save()
            self._set_hotkey_hint("")
            return

        ok, message = hotkey_mod.check(text)
        if not ok:
            self._set_hotkey_hint(message, error=True)
            return
        config.hotkey = hotkey_mod.describe(text)
        config.hotkey_enabled = True
        self.controller.save()
        self._set_hotkey_hint("")

    # -- 教程入口 -------------------------------------------------------
    def open_tutorial(self) -> None:
        """打开使用教程窗（docs/教程.md 渲染成可搜索的窗口）。"""
        from .tutorial import show_tutorial, tutorial_path

        if tutorial_path() is None:
            messagebox.showwarning(
                "找不到教程",
                "未找到 docs/教程.md。\n\n"
                "若已将程序复制到其他位置，请一并带上 docs 目录；"
                "也可以从项目目录重新启动。",
                parent=self.root,
            )
            return
        try:
            show_tutorial(self.root, data_dir=self.controller.data_dir)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("打不开教程", f"{type(error).__name__}: {error}", parent=self.root)

    def open_data_dir(self) -> None:
        self._open_in_explorer(self.controller.data_dir)

    def reveal_tutorial(self) -> None:
        from .tutorial import tutorial_path

        path = tutorial_path()
        if path is None:
            self.open_tutorial()
            return
        self._open_in_explorer(path)

    def _open_in_explorer(self, target) -> None:
        try:
            import os
            path = str(target)
            if os.name == "nt":
                os.startfile(path)          # noqa: S606  打开资源管理器
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("打不开", f"{type(error).__name__}: {error}", parent=self.root)

    def save_settings(self) -> None:
        """保存设置页上**还留在界面上**的那几项。

        已经拿掉的选项（inbox 间隔 / 面板图层 / 鼠标穿透 / 宠物模式）不再由界面写入：
        它们保持"默认值或配置文件里的原值"。想改的话直接编辑 `data/client.json`
        （字段名见 `agenda/client_config.py`），或者用命令行开关
        （`--window-mode topmost` / `--always-visible` / `--no-click-through`）。
        """
        config = self.controller.config
        config.panel_visible = bool(self.panel_visible_var.get())
        config.popup_reminders = bool(self.popup_var.get())
        # 关闭窗口的行为固定是"最小化至托盘"，不再从界面写 close_action
        config.close_action = "tray"
        config.desktop_only = bool(self.desktop_only_var.get())
        # 主题保持界面上选的那一项（这块是即时生效的，进这里只是防止被别处覆盖）
        if hasattr(self, "theme_mode_var"):
            config.theme_mode = self.theme_mode_var.get()
        self.controller.save()
        self.refresh_header()
        restart = messagebox.askyesno(
            "已保存", "设置已保存。待机模式需重启面板后生效，是否立即重启面板？",
            parent=self.root,
        )
        if restart and self.controller.panel_running():
            self.controller.restart_panel()
        elif restart and config.panel_visible:
            self.controller.start_panel()

    # -- 状态栏 ---------------------------------------------------------
    def _build_status(self) -> None:
        colors = theme.COLORS
        self.status = tk.Label(
            self.root, text="", bg=colors["bg_soft"], fg=colors["text_faint"],
            font=("Microsoft YaHei UI", 8), anchor="w", justify="left",
        )
        self.status.pack(fill="x", side="bottom", ipady=4, padx=0)

    # ------------------------------------------------------------------
    def refresh_header(self) -> None:
        running = self.controller.panel_running()
        self.panel_button.configure(text="关闭面板" if running else "打开面板")
        events = self.controller.refresh_events()
        today = dt.date.today()
        today_events = [e for e in events if e.date == today.strftime("%Y-%m-%d")]
        # 今天有节日 / 在放假 / 在调休，都在这一行说清楚
        from .festival import hint_for
        calendar = self._calendar()
        hint = hint_for(today, calendar)
        extras = []
        if hint is not None:
            extras.append(f"{hint.festival.name}")
        holiday = calendar.holiday_on(today)
        if holiday is not None:
            extras.append(f"{holiday.name}假期")
        makeup = calendar.makeup_on(today)
        if makeup is not None:
            extras.append(makeup.text())
        prefix = ("　·　".join(extras) + "　·　") if extras else ""
        self.summary_label.configure(
            text=f"{prefix}面板{'运行中' if running else '已关闭'} · 今日 {len(today_events)} 条通知 · 一周共 {len(events)} 项"
        )
        self.status.configure(
            text=f"数据目录：{self.controller.data_dir}   ·   "
                 f"面板进程：{self.controller.panel_pid() or '未运行'}   ·   "
                 f"退出方式：托盘右键 / 面板底部右键「退出应用」"
        )

    def refresh_agenda(self) -> None:
        for item in self.agenda_tree.get_children():
            self.agenda_tree.delete(item)
        events = self.controller.refresh_events()
        table = load_timetable(self.controller.data_dir)
        calendar = self._calendar()
        timeline = build_timeline(events, table, calendar=calendar)
        self._agenda_cards = {}
        self._agenda_rows = []
        if not timeline.total_cards:
            # 课表没导入 / 这几天没课 / 放假：说明文字放到**能折行的标签**里，
            # 表格里只留一句短标题（塞进单元格会被列宽硬切，踩过）
            self.agenda_hint.configure(text=f"{EMPTY_AGENDA_TEXT}　{empty_hint(table, calendar, timeline.today)}")
            self.agenda_tree.insert("", "end", values=(EMPTY_AGENDA_TEXT, "", "", "", "", ""))
            return
        self.agenda_hint.configure(text=f"共 {timeline.total_cards} 项（含课程）· 长文本会自动折行显示")
        for section in timeline.sections:
            for card in section.cards:
                heading = day_heading(section) if card is section.cards[0] else ""
                if heading and section.makeup_label:
                    heading = f"{heading}　{section.makeup_label}"
                self._agenda_rows.append((heading, card))
        # 立即填表；只有"窗口尺寸变化"那种连续事件才走去抖版本
        self._rewrap_agenda()

    def _agenda_cell_font(self):
        """日程表折行用的字体对象（一个窗口一个，别跨 Tk 解释器复用）。"""
        font = getattr(self, "_agenda_font", None)
        if font is None:
            from tkinter import font as tkfont
            font = tkfont.Font(root=self.root, family=AGENDA_FONT[0], size=AGENDA_FONT[1])
            self._agenda_font = font
        return font

    def _rewrap_agenda(self) -> None:
        """把表格里的文字按**当前列宽**重新折行。

        ttk.Treeview 不支持单元格自动换行，只能在文本里插 `\\n` + 把行高调成两行。
        所以这里按真实字体度量算：放不下就在合适的位置断开，最多两行，超出的用省略号。
        """
        rows = getattr(self, "_agenda_rows", None)
        if rows is None:
            return
        for item in self.agenda_tree.get_children():
            self.agenda_tree.delete(item)
        self._agenda_cards = {}
        font = self._agenda_cell_font()
        widths = {key: self.agenda_tree.column(key, "width") for key in
                  ("date", "time", "title", "place", "people", "group")}
        for heading, card in rows:
            # 所有列都允许折成两行：行高本来就是两行，折行不额外占地方，
            # 但能避免"10:10 · 第3-4节"这种被截成"10:10 · …"
            item = self.agenda_tree.insert("", "end", values=(
                _fit_cell(heading, widths["date"], font, max_lines=2),
                _fit_cell(card.time_label, widths["time"], font, max_lines=2),
                _fit_cell(card.title, widths["title"], font, max_lines=2),
                _fit_cell(card.location or "-", widths["place"], font, max_lines=2),
                _fit_cell(card.people or "-", widths["people"], font, max_lines=2),
                _fit_cell(card.badge or "-", widths["group"], font, max_lines=2),
            ))
            # 记住每一行对应哪张卡，右键才知道要操作谁
            self._agenda_cards[item] = card

    def _schedule_agenda_rewrap(self) -> None:
        """窗口/列宽变化时重排，但别每动一像素就重排一遍（去抖 120ms）。"""
        if not getattr(self, "_agenda_rows", None):
            return
        job = getattr(self, "_agenda_rewrap_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._agenda_rewrap_job = self.root.after(120, self._rewrap_agenda)

    # -- 今日与未来一周：右键某一行 --------------------------------------
    def _agenda_menu(self, event) -> None:
        item = self.agenda_tree.identify_row(event.y)
        if not item:
            return
        self.agenda_tree.selection_set(item)
        card = (getattr(self, "_agenda_cards", None) or {}).get(item)
        if card is None:
            return
        menu = tk.Menu(self.root, tearoff=0)
        head = card.title if len(card.title) <= 18 else card.title[:17] + "…"
        menu.add_command(label=f"—— {head} ——", state="disabled")
        menu.add_separator()
        menu.add_command(label="标记为已完成", command=lambda: self.agenda_done(card))
        menu.add_command(label="修改结束时间…", command=lambda: self.agenda_edit_end(card))
        menu.add_command(label="复制内容", command=lambda: self.agenda_copy(card))
        menu.add_separator()
        menu.add_command(label="删除此条…", command=lambda: self.agenda_delete(card))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def agenda_done(self, card) -> None:
        if card.kind == "course" or not card.event_id:
            messagebox.showinfo("该条目为课程", "课程请在「课程表」页删除或调整时间。", parent=self.root)
            return
        if self._remove_event(card.event_id):
            self.notice_hint.configure(text=f"已完成：{card.title}", fg="#37D978")
            self._after_event_change()

    def agenda_delete(self, card) -> None:
        if card.kind == "course" or not card.event_id:
            messagebox.showinfo("该条目为课程", "课程请在「课程表」页删除。", parent=self.root)
            return
        if not messagebox.askyesno("删除日程", f"确定删除「{card.title}」？", parent=self.root):
            return
        if self._remove_event(card.event_id):
            self.notice_hint.configure(text=f"已删除：{card.title}", fg="#37D978")
            self._after_event_change()

    def agenda_copy(self, card) -> None:
        parts = [card.title, card.time_label, card.date, card.location, card.people, card.notes]
        text = " | ".join(part for part in parts if part)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.notice_hint.configure(text="已复制到剪贴板", fg="#37D978")
        except tk.TclError:
            pass

    def agenda_edit_end(self, card) -> None:
        """改结束时间：通知直接改；课程写这条自己的 endTime 覆盖值。"""
        from .panel import ask_end_time  # 复用同一个输入方式

        current = card.end or (card.start or "18:00")
        new_end = ask_end_time(self.root, card.title, card.start, current)
        if not new_end:
            return
        if card.start and new_end <= card.start:
            messagebox.showwarning("时间不对", f"结束时间要晚于开始时间（{card.start}）", parent=self.root)
            return
        if card.kind == "course":
            if card.course_index is None:
                messagebox.showwarning("未找到对应课程", "课表已发生变化，请刷新后重试。", parent=self.root)
                return
            payload = self.controller.load_timetable_payload()
            courses = payload.get("courses") or []
            if not 0 <= card.course_index < len(courses):
                messagebox.showwarning("未找到对应课程", "课表已发生变化，请刷新后重试。", parent=self.root)
                return
            courses[card.course_index]["endTime"] = new_end
            self.controller.save_timetable_payload(payload)
        else:
            from .store import EventStore
            store_path = self.controller.pipeline.store_path
            store = EventStore.load(store_path) if store_path.exists() else EventStore(store_path)
            target = next((e for e in store.events if e.id == card.event_id), None)
            if target is None:
                messagebox.showwarning("未找到该条目", "该日程已不存在。", parent=self.root)
                return
            target.end = new_end
            store.save()
        self.notice_hint.configure(text=f"{card.title} 结束时间改为 {new_end}", fg="#37D978")
        self._after_event_change()

    def _remove_event(self, event_id: str) -> bool:
        from .store import EventStore

        store_path = self.controller.pipeline.store_path
        store = EventStore.load(store_path) if store_path.exists() else EventStore(store_path)
        if not store.remove(event_id):
            return False
        store.save()
        return True

    def _after_event_change(self) -> None:
        if self.controller.config.panel_visible:
            self.controller.restart_panel()
        self.refresh_agenda()
        self.refresh_header()

    def refresh_all(self) -> None:
        self.refresh_header()
        self.refresh_agenda()
        self.refresh_courses()
        self.refresh_periods()
        self.refresh_holidays()

    def _schedule_tick(self) -> None:
        """每 5 秒刷新一次控制台（面板可能被别处关掉、课表/通知可能被外部改过）。"""
        try:
            self.refresh_header()
            self.refresh_agenda()
        except Exception:
            pass
        try:
            self._tick_job = self.root.after(5000, self._schedule_tick)
        except tk.TclError:
            pass                    # 窗口已经销毁（退出应用的路上），不用再排队

    def _schedule_request_poll(self) -> None:
        """每 200 ms 看一次"外面有没有人在叫我们"，比界面刷新快 25 倍。

        这些请求（再次双击快捷方式 → `client.show`；面板右下角齿轮 → `client.settings`；
        托盘退出 → `client.quit`）原来是搭在 5 秒一次的 `_schedule_tick` 上的：
        点一下齿轮最长要等 5 秒窗口才有动静，用户会以为按钮失效了。
        单独拉一条轻量轮询，一次只做 3 次 `Path.exists()`（微秒级），
        界面刷新照旧 5 秒一次，互不影响。
        """
        try:
            self._check_show_request()
        except Exception:
            pass
        try:
            self._request_job = self.root.after(REQUEST_POLL_MS, self._schedule_request_poll)
        except tk.TclError:
            # `_check_show_request` 可能刚执行完「退出应用」把窗口销毁了，
            # 这时再排下一个 job 会抛 TclError、在控制台刷一屏 Tk 报错。
            pass

    def _select_tab(self, title: str) -> bool:
        """切到指定标题的页签（面板上的小齿轮用它直接打开「设置」）。"""
        try:
            for tab in self.notebook.tabs():
                if self.notebook.tab(tab, "text") == title:
                    self.notebook.select(tab)
                    return True
        except tk.TclError:
            pass
        return False

    def open_settings(self, tab: str = "设置") -> None:
        """把窗口叫出来并切到某一页（面板齿轮按钮的落地动作）。"""
        self.show()
        self._select_tab(tab)

    def _check_show_request(self) -> None:
        """别人（再次双击快捷方式 / 面板齿轮）请我们显示窗口时，把控制台叫回来。

        控制台"最小化至托盘"后窗口是隐藏的，用户再点一次图标理应把它叫出来——
        而不是像以前那样因为单实例保护**安静地什么都不做**（用户的原话："客户端怎么打不开了？"）。
        """
        from .client_app import QUIT_REQUEST, SETTINGS_REQUEST, SHOW_REQUEST

        data_dir = Path(self.controller.data_dir)
        settings_flag = data_dir / SETTINGS_REQUEST
        if settings_flag.exists():
            try:
                settings_flag.unlink()
            except OSError:
                pass
            self.open_settings()
            self._bring_to_front()
        show_flag = data_dir / SHOW_REQUEST
        if show_flag.exists():
            try:
                show_flag.unlink()
            except OSError:
                pass
            self.show()
            self._bring_to_front()
        quit_flag = data_dir / QUIT_REQUEST
        if quit_flag.exists():
            try:
                quit_flag.unlink()
            except OSError:
                pass
            # 这里**不能**调 on_close()：那只是"最小化至托盘"，托盘图标和进程都会留下，
            # 而用户在面板上点的是「退出应用（面板与托盘一并退出）」（真踩过）。
            self.quit_app()

    def _bring_to_front(self) -> None:
        """把控制台窗口放到最前，并让任务栏按钮闪一下。

        用户的原话：「点托盘图标/快捷方式时，把控制台窗口带到最前面，并在任务栏闪烁提醒」。
        只 `lift()` 是不够的：窗口在别的程序后面、或者被最小化了，用户还是看不到；
        任务栏闪一下是 Windows 上最标准的"看这里"。
        """
        try:
            self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            return
        hwnd = getattr(self, "_hwnd", 0)
        if not hwnd:
            # Tk 的 winfo_id() 给的是内部子窗口句柄，带标题栏的那层要往上找一层
            # （theme.py 里设置 DPI 时也是这么干的）
            try:
                import ctypes

                child = int(self.root.winfo_id())
                parent = int(ctypes.windll.user32.GetParent(child) or 0)
                hwnd = parent or child
                self._hwnd = hwnd
            except Exception:                                # noqa: BLE001
                return
        from . import winlayer

        winlayer.bring_to_front(hwnd, flash=True)

    def stop_tick(self) -> None:
        """取消所有定时任务（关窗时调用）。

        为什么要把**每一个** after 都收掉：Tk 的 after 回调挂在解释器上，
        窗口销毁后残留的回调会在解释器被拆掉时触发——
        实测的表现是进程直接崩（Windows 退出码 0xC000041D），
        而且只在"建过窗口 → 销毁 → 再建一个 Tk"的顺序下才复现，很难查。
        所以定时器只留这一个出口，不许别处再随手 after。
        """
        self._cancel_jobs()

    def _cancel_jobs(self) -> None:
        for name in ("_tick_job", "_notice_job", "_topmost_job", "_request_job"):
            job = getattr(self, name, None)
            if job is None:
                continue
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            setattr(self, name, None)

    # ------------------------------------------------------------------
    def toggle_panel(self) -> None:
        running = self.controller.toggle_panel()
        self.refresh_header()
        messagebox.showinfo("面板", "面板已打开" if running else "面板已关闭", parent=self.root)

    def minimize_to_tray(self) -> None:
        self.root.withdraw()

    def quit_app(self) -> None:
        """彻底退出：面板停掉、控制台窗口销毁、进程结束、托盘图标随进程一起消失。

        托盘右键的「退出应用」和面板上的「退出应用（面板与托盘一并退出）」走同一条路
        —— 后者是通过写 `client.quit` 请求文件让控制台执行的（`_check_show_request`）。

        顺序很重要：先停面板（它有自己的进程和 `panel.stop` 标记），再停定时器
        （残留的 `after` 回调会在解释器拆掉时把进程带崩，退出码 0xC000041D），
        最后才 destroy 主窗。
        """
        try:
            self.controller.stop_panel()
        except Exception:            # 面板本来就没在跑 / 权限问题，都不该挡住退出
            pass
        try:
            self.stop_tick()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def show(self) -> None:
        """把控制台窗口叫回来（可能被最小化至托盘、被最小化、或被挪出屏幕）。"""
        try:
            self.root.deiconify()
            self.root.state("normal")           # 从最小化恢复
            self.root.lift()
            self.root.attributes("-topmost", True)
            self._topmost_job = self.root.after(
                600, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def quick_entry(self) -> None:
        """把窗口叫出来 + 切到「通知」页 + 光标落在输入框（托盘菜单用）。

        托盘右键的「快速录入群消息…」要的是"立刻能粘"，不是"先看见窗口再自己找地方"。
        """
        self.show()
        try:
            self.notebook.select(0)              # 0 号页就是「通知」
            self.notice_text.focus_set()
        except (tk.TclError, AttributeError):
            pass

    def on_close(self) -> None:
        """点右上角 X：**只把控制台最小化至托盘，桌面面板继续显示**。

        用户原话：「我关闭客户端的时候默认自动关闭日程表，我要求客户端关闭进入状态栏中
        不会影响日程表的显示，只有在退出应用的时候才会关闭」。

        所以这里**不再提供"顺带退出面板"的选项**：退出应用只有两个入口 ——
        托盘图标右键「退出应用」、面板底部右键「退出应用」（都会写 client.quit）。
        """
        config = self.controller.config
        config.console_x = self.root.winfo_x()
        config.console_y = self.root.winfo_y()
        self.controller.save()

        first_time = not getattr(config, "tray_notice_shown", False)
        if first_time:
            # 第一次提示一次"去哪儿退出"，之后不再打扰
            config.tray_notice_shown = True
            self.controller.save()
            messagebox.showinfo(
                "已最小化至托盘",
                "控制台已最小化至托盘，桌面面板继续显示，通知与到点提醒照常工作。\n\n"
                "要完全退出：在托盘图标上右键 →「退出应用」，"
                "或在面板底部右键 →「退出应用」。",
                parent=self.root,
            )
        # tray：只关控制台窗口，面板继续跑。
        # 注意**不能**在这里 stop_tick：藏起来的控制台还要靠那个 tick 处理
        # "再次双击快捷方式 → 把窗口叫出来"的请求（client.show）。
        self.root.withdraw()


def _valid_time(text: str) -> bool:
    return _minutes_of(text) is not None


def _split_list(text: str) -> list[str]:
    """按逗号/顿号/分号/空格把用户一次填的多个日期拆开。"""
    import re as _re
    return [part.strip() for part in _re.split(r"[,，、;；\s]+", str(text or "")) if part.strip()]


def _parse_user_date(text: str, default_year: int):
    """用户在输入框里写的日期 → date（认不出来返回 None）。"""
    from .holidays import parse_date
    chunks = _split_list(text)
    return parse_date(chunks[0] if chunks else text, default_year=default_year)


def _parse_date_list(text: str, default_year: int):
    """一串日期（可含区间）→ date 列表。"""
    from .holidays import parse_date, parse_date_range
    found = []
    for chunk in _split_list(text):
        span = parse_date_range(chunk, default_year=default_year)
        if span is None:
            single = parse_date(chunk, default_year=default_year)
            if single is None:
                continue
            span = (single, single)
        import datetime as _dt
        found.extend(span[0] + _dt.timedelta(days=offset)
                     for offset in range((span[1] - span[0]).days + 1))
    return found


def _center_over(window: tk.Misc, parent: tk.Misc) -> None:
    """把 window 摆到 parent 正中；算不出来就交给窗口管理器。"""
    try:
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
        if width <= 1 or height <= 1:
            width = max(window.winfo_reqwidth(), 360)
            height = max(window.winfo_reqheight(), 200)
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
        if pw <= 1 or ph <= 1:
            x, y = (screen_w - width) // 2, (screen_h - height) // 3
        else:
            x, y = px + (pw - width) // 2, py + (ph - height) // 3
        x = max(0, min(x, screen_w - width))
        y = max(0, min(y, screen_h - height))
        window.geometry(f"+{x}+{y}")
    except tk.TclError:
        pass


def _weekday_index(value) -> int:
    """把 "周三" / "3" / 2 都归一成 0-6（0=周一）。认不出来当周一。"""
    if isinstance(value, int):
        return min(6, max(0, value - 1 if value >= 1 else value))
    text = str(value or "").strip()
    if text.isdigit():
        number = int(text)
        # 1-7 按"周几"理解；0-6 已经是下标
        return min(6, max(0, (number - 1) if 1 <= number <= 7 else number))
    if text in WEEKDAYS:
        return WEEKDAYS.index(text)
    import re as _re
    match = _re.search(r"[一二三四五六日天1-7]", text)
    if match:
        token = match.group(0)
        table = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        if token in table:
            return table[token]
        if token.isdigit():
            return min(6, max(0, int(token) - 1))
    return 0


def _period_start(value) -> int:
    """从 "3-4" / "第3节" 里取出起始节次，用于排序。认不出来算 1。"""
    import re as _re
    numbers = [int(n) for n in _re.findall(r"\d{1,2}", str(value or ""))]
    return numbers[0] if numbers else 1


#: 日程表单元格折行用的字体（跟 Agenda.Treeview 的字体保持一致）
AGENDA_FONT = ("Microsoft YaHei UI", 9)


def _measure_font(font):
    """允许传进"字体规格"或"已经建好的 Font"。

    不能把 Font 缓存在模块级：Font 属于某个 Tk 解释器，换个 root（测试里一个用例
    一个 root）之后拿旧对象 measure 会直接抛异常。所以缓存交给窗口自己持有。
    """
    if hasattr(font, "measure"):
        return font
    from tkinter import font as _tkfont
    family, size = font[0], font[1]
    return _tkfont.Font(family=family, size=size)


def _fit_cell(text: str, width_px: int, font_spec, *, max_lines: int = 1) -> str:
    """把文字按列宽折行（最多 max_lines 行），放不下就在行尾加省略号。

    ttk.Treeview 的单元格不会自动换行：文字超出列宽会被**硬切**（用户看到的
    就是半句话）。这里按真实字体度量手动插 `\\n`，配合加高的行高就成了"自适应换行"。
    """
    text = str(text or "")
    if not text:
        return ""
    font = _measure_font(font_spec)
    available = max(24, int(width_px) - 12)          # 留一点内边距
    if font.measure(text) <= available:
        return text
    lines: list[str] = []
    rest = text
    while rest and len(lines) < max_lines:
        if font.measure(rest) <= available:
            lines.append(rest)
            rest = ""
            break
        # 找这一行能放下的最长前缀
        cut = 1
        for index in range(1, len(rest) + 1):
            if font.measure(rest[:index]) > available:
                break
            cut = index
        # 尽量在标点/空格处断开，避免"半句话"式硬切
        for mark in ("，", "。", "、", "；", "：", "）", " ", "·", "-"):
            position = rest.rfind(mark, 0, cut)
            if position > cut // 2:
                cut = position + 1
                break
        lines.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        last = lines[-1]
        while last and font.measure(last + "…") > available:
            last = last[:-1]
        lines[-1] = last + "…"
    return "\n".join(lines)


def _minutes_of(text: str) -> int | None:
    """"08:00" → 480；认不出来返回 None。全角冒号也收。"""
    raw = str(text or "").strip().replace("：", ":")
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _weeks_text(weeks) -> str:
    from .eas import format_weeks
    try:
        return format_weeks(list(weeks))
    except Exception:
        return str(weeks)


def _weeks_text_any(value) -> str:
    if value is None:
        return "1-16"
    if isinstance(value, (list, tuple)):
        return _weeks_text(value)
    return str(value)


# 说明：原来这里有 EasDialog（内置浏览器登录教务 + 直连账号密码导入）和学校目录。
# 实测这条路识别质量太差（正方新版课表页字段混在一起、还要处理 iframe / WebVPN / CAS），
# 已按需求整体删除，改用 agenda/file_import.py 的「从文件识别课表」。
