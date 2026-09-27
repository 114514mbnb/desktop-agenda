"""快速录入窗口：把 QQ 群通知粘进来，立刻并入日程。

半自动管道的"人工"那一端——从 QQ 复制的通知文本直接 Ctrl+V 进来即可，
支持粘贴多条（用空行或 [群名] 分开），保存后实时解析并显示识别结果。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from . import theme


class QuickEntryDialog:
    def __init__(self, panel):
        self.panel = panel
        data_dir = panel.data_dir
        self.data_dir = data_dir

        root = tk.Toplevel(panel.root)
        self.root = root
        root.title("快速录入群通知")
        root.configure(bg=theme.COLORS["bg"])
        scale = panel.scale
        width = int(620 * scale)
        height = int(520 * scale)
        root.geometry(f"{width}x{height}+{panel.root.winfo_x() - width - int(16 * scale)}+{panel.root.winfo_y()}")
        root.attributes("-topmost", True)
        root.transient(panel.root)

        header = tk.Frame(root, bg=theme.COLORS["bg_soft"])
        header.pack(fill="x")
        tk.Label(
            header, text="把 QQ 群通知粘贴到这里", bg=theme.COLORS["bg_soft"], fg=theme.COLORS["text"],
            font=panel.fonts.spec(panel.fonts.heading, "bold"), anchor="w",
        ).pack(side="left", padx=int(14 * scale), pady=int(10 * scale))
        tk.Label(
            header, text="多条通知以空行或 [群名] 开头分隔；Ctrl+Enter 保存", bg=theme.COLORS["bg_soft"],
            fg=theme.COLORS["text_faint"], font=panel.fonts.spec(panel.fonts.badge),
        ).pack(side="right", padx=int(14 * scale))

        text_frame = tk.Frame(root, bg=theme.COLORS["border"])
        text_frame.pack(fill="both", expand=True, padx=int(12 * scale), pady=int(10 * scale))
        self.text = tk.Text(
            text_frame, bg=theme.COLORS["card"], fg=theme.COLORS["text"], insertbackground=theme.COLORS["text"],
            font=panel.fonts.spec(panel.fonts.body), relief="flat", wrap="word", undo=True,
            padx=int(10 * scale), pady=int(8 * scale),
        )
        self.text.pack(fill="both", expand=True, padx=1, pady=1)
        self.text.focus_set()

        options = tk.Frame(root, bg=theme.COLORS["bg"])
        options.pack(fill="x", padx=int(12 * scale))
        tk.Label(
            options, text="来源群名（可选）", bg=theme.COLORS["bg"], fg=theme.COLORS["text_dim"],
            font=panel.fonts.spec(panel.fonts.meta),
        ).pack(side="left")
        self.group_var = tk.StringVar()
        tk.Entry(
            options, textvariable=self.group_var, bg=theme.COLORS["card"], fg=theme.COLORS["text"],
            insertbackground=theme.COLORS["text"], relief="flat", font=panel.fonts.spec(panel.fonts.meta),
            width=22,
        ).pack(side="left", padx=(int(6 * scale), int(14 * scale)), ipady=int(3 * scale))
        self.preview_var = tk.StringVar(value="")
        tk.Label(
            options, textvariable=self.preview_var, bg=theme.COLORS["bg"], fg=theme.COLORS["text_faint"],
            font=panel.fonts.spec(panel.fonts.badge),
        ).pack(side="left")

        buttons = tk.Frame(root, bg=theme.COLORS["bg"])
        buttons.pack(fill="x", padx=int(12 * scale), pady=int(12 * scale))

        def button(text: str, command, primary: bool = False) -> tk.Button:
            return tk.Button(
                buttons, text=text, command=command, relief="flat", bd=0,
                bg=theme.COLORS["accent"] if primary else theme.COLORS["card"],
                fg="#FFFFFF" if primary else theme.COLORS["text"],
                activebackground=theme.COLORS["accent"] if primary else theme.COLORS["card_now"],
                activeforeground="#FFFFFF" if primary else theme.COLORS["text"],
                font=panel.fonts.spec(panel.fonts.meta, "bold" if primary else "normal"),
                padx=int(16 * scale), pady=int(6 * scale), cursor="hand2",
            )

        button("保存并解析", self.save, primary=True).pack(side="right")
        button("取消", self.close).pack(side="right", padx=(0, int(8 * scale)))
        button("预览识别结果", self.preview).pack(side="left")

        root.bind("<Control-Return>", lambda _e: self.save())
        root.bind("<Escape>", lambda _e: self.close())

    def preview(self) -> None:
        text = self.text.get("1.0", "end").strip()
        if not text:
            self.preview_var.set("尚无内容")
            return
        try:
            report = self.panel.pipeline.ingest_text(
                text, group=self.group_var.get().strip() or None,
                source_label="快速录入预览", dry_run=True,
            )
        except Exception as error:
            self.preview_var.set(f"解析失败：{error}")
            return
        titles = [card.title for card in report.events][:3]
        head = f"识别到 {report.candidates} 条"
        if titles:
            head += "：" + "；".join(titles)
        self.preview_var.set(head)

    def save(self) -> None:
        text = self.text.get("1.0", "end").strip()
        if not text:
            self.close()
            return
        try:
            report = self.panel.pipeline.ingest_text(
                text, group=self.group_var.get().strip() or None, source_label="快速录入",
            )
        except Exception as error:
            messagebox.showerror("保存失败", str(error), parent=self.root)
            return
        self.panel.refresh()
        if report.candidates == 0:
            messagebox.showinfo("未识别到日程", "这段文字中没有找到时间或活动信息，已忽略。", parent=self.root)
            return
        self.close()

    def close(self) -> None:
        self.root.destroy()
