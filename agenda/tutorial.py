"""使用教程窗：把 docs/教程.md 渲染成一个能搜索、能跳章节的窗口。

为什么要做成窗口而不是让人去翻文件：教程只有"当场能打开"才会被看。
所以设置页里常驻一个入口，另外 `main.py --tutorial` 也能直接打开。

渲染方式说明：这是个零第三方依赖的项目，没有 markdown 库，所以这里只做
"够用的排版"——识别标题/列表/表格/代码块/引用，用 tkinter 的 Text 标签来上色。
不追求完全符合 Markdown 规范，追求**读起来清楚**。
"""

from __future__ import annotations

import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import theme

TUTORIAL_FILENAME = "教程.md"


def plain_markdown(text: str) -> str:
    """把行内 Markdown 语法压成纯文本。

    Text 控件没有富文本，`**加粗**` 会原样显示成星号——看着像坏了。
    所以这里：去强调符号、去行内代码反引号、`[文字](链接)` 只留文字。
    """
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    value = value.replace("**", "").replace("`", "")
    return value


def tutorial_path() -> Path | None:
    """找到教程文件。打包/搬运后位置可能变，所以按几个候选路径找。"""
    here = Path(__file__).resolve().parent          # agenda/
    candidates = [
        here.parent / "docs" / TUTORIAL_FILENAME,   # <项目>/docs/教程.md
        here.parent / TUTORIAL_FILENAME,
        Path.cwd() / "docs" / TUTORIAL_FILENAME,
        Path.cwd() / TUTORIAL_FILENAME,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


class TutorialWindow:
    """教程窗：左侧目录 + 右侧正文，支持搜索。"""

    def __init__(self, parent=None, *, data_dir: Path | None = None):
        self.path = tutorial_path()
        self.root = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.root.title("桌面日程 · 使用教程")
        self.root.configure(bg=theme.COLORS["bg"])
        self.root.geometry("980x720")
        self.root.minsize(720, 480)
        theme.apply_windows_flourishes(self.root, rounded=False, dark_title=True)

        colors = theme.COLORS
        family = theme.pick_font_family(self.root)
        self.fonts = theme.Fonts(family=family)

        header = tk.Frame(self.root, bg=colors["bg_soft"])
        header.pack(fill="x")
        tk.Label(
            header, text="使用教程", bg=colors["bg_soft"], fg=colors["text"],
            font=(family, 13, "bold"),
        ).pack(side="left", padx=14, pady=10)
        tk.Label(
            header,
            text="" if self.path else "未找到 docs/教程.md —— 请从项目目录重新启动",
            bg=colors["bg_soft"], fg=colors["text_faint"], font=(family, 8),
        ).pack(side="right", padx=14)

        body = tk.Frame(self.root, bg=colors["bg"])
        body.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        # 左：章节目录
        left = tk.Frame(body, bg=colors["border"], width=210)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)
        tk.Label(left, text="章节", bg=colors["bg_soft"], fg=colors["text_dim"],
                 font=(family, 9, "bold"), anchor="w").pack(fill="x", padx=10, pady=(8, 4))
        self.toc = tk.Listbox(
            left, bg=colors["bg"], fg=colors["text_dim"], selectbackground=colors["accent"],
            selectforeground="#FFFFFF", relief="flat", highlightthickness=0, bd=0,
            font=(family, 9), activestyle="none",
        )
        self.toc.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        self.toc.bind("<<ListboxSelect>>", self._jump)

        # 右上：搜索
        right = tk.Frame(body, bg=colors["bg"])
        right.pack(side="left", fill="both", expand=True)
        search_row = tk.Frame(right, bg=colors["bg"])
        search_row.pack(fill="x", pady=(0, 6))
        tk.Label(search_row, text="搜索", bg=colors["bg"], fg=colors["text_dim"],
                 font=(family, 9)).pack(side="left")
        self.query = tk.StringVar()
        entry = tk.Entry(search_row, textvariable=self.query, bg=colors["card"], fg=colors["text"],
                         insertbackground=colors["text"], relief="flat", font=(family, 9))
        entry.pack(side="left", fill="x", expand=True, padx=(6, 6), ipady=3)
        entry.bind("<Return>", lambda _e: self.search())
        tk.Button(search_row, text="查找", command=self.search, relief="flat", bd=0,
                  bg=colors["accent"], fg="#FFFFFF", padx=12, pady=4, cursor="hand2",
                  font=(family, 9, "bold")).pack(side="left")
        tk.Button(search_row, text="下一个", command=self.search_next, relief="flat", bd=0,
                  bg=colors["card"], fg=colors["text"], padx=12, pady=4, cursor="hand2",
                  font=(family, 9)).pack(side="left", padx=(6, 0))

        # 正文：Text + 滚动条（先 pack 滚动条，否则会被 Text 的 expand 挤成 1px）
        text_box = tk.Frame(right, bg=colors["border"])
        text_box.pack(fill="both", expand=True, pady=(0, 8))
        self.scrollbar = tk.Scrollbar(text_box, width=10, bd=0, highlightthickness=0,
                                      troughcolor=colors["bg_soft"], background=colors["text_faint"],
                                      activebackground=colors["accent"], relief="flat")
        self.scrollbar.pack(side="right", fill="y")
        self.text = tk.Text(
            text_box, bg=colors["card"], fg=colors["text"], relief="flat", wrap="word",
            padx=18, pady=14, spacing1=2, spacing3=3, highlightthickness=0, bd=0,
            font=(family, 10), yscrollcommand=self.scrollbar.set,
        )
        self.text.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.scrollbar.configure(command=self.text.yview)
        self.text.bind("<MouseWheel>", self._on_wheel)

        self._configure_tags()
        self._render()
        self.root.bind("<Escape>", lambda _e: self.close())
        self.root.bind("<Control-f>", lambda _e: entry.focus_set())

    # -- 样式 ------------------------------------------------------------
    def _configure_tags(self) -> None:
        colors = theme.COLORS
        family = self.fonts.family
        self.text.tag_configure("h1", font=(family, 17, "bold"), foreground=colors["text"],
                                spacing1=14, spacing3=8)
        self.text.tag_configure("h2", font=(family, 13, "bold"), foreground=colors["accent"],
                                spacing1=16, spacing3=6)
        self.text.tag_configure("h3", font=(family, 11, "bold"), foreground=colors["text"],
                                spacing1=10, spacing3=4)
        self.text.tag_configure("body", font=(family, 10), foreground=colors["text_dim"],
                                lmargin1=2, lmargin2=2, spacing3=2)
        self.text.tag_configure("bullet", font=(family, 10), foreground=colors["text_dim"],
                                lmargin1=16, lmargin2=30)
        self.text.tag_configure("code", font=("Consolas", 9), foreground="#8BD5A0",
                                background=theme.COLORS["bg"], lmargin1=14, lmargin2=14)
        self.text.tag_configure("table", font=(family, 9), foreground=colors["text_dim"],
                                lmargin1=14, lmargin2=14)
        self.text.tag_configure("quote", font=(family, 9), foreground=colors["text_faint"],
                                lmargin1=16, lmargin2=16)
        self.text.tag_configure("link", foreground=colors["accent"], underline=True)
        self.text.tag_configure("find", background="#B58900", foreground="#1A1A1A")
        self.text.tag_configure("rule", foreground=colors["line"])

    # -- 渲染 ------------------------------------------------------------
    def _render(self) -> None:
        if self.path is None:
            self.text.insert("end", f"没找到 {TUTORIAL_FILENAME}。\n\n"
                                    "该文件应与 README.md 位于同一 docs 目录。\n", "body")
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            self.text.insert("end", f"读取教程失败：{error}\n", "body")
            return

        self._headings: list[tuple[str, str]] = []      # (显示文字, 锚点)
        self._anchors: dict[str, str] = {}
        in_code = False
        for raw in lines:
            line = raw.rstrip()
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                self.text.insert("end", line + "\n", "code")
                continue
            if not line.strip():
                self.text.insert("end", "\n")
                continue
            if line.startswith("# "):
                self._anchor("h1", line[2:].strip(), chapter=True)
            elif line.startswith("## "):
                self._anchor("h2", line[3:].strip(), chapter=True)
            elif line.startswith("### "):
                # 三级标题不进目录：目录只列章节，否则一屏全是子条目反而找不到主线
                self._anchor("h3", line[4:].strip(), chapter=False)
            elif line.strip() in {"---", "***"}:
                self.text.insert("end", "─" * 60 + "\n", "rule")
            elif line.lstrip().startswith(("|", "- ", "* ", "1.", "2.", "3.", "4.", "5.", "6.")):
                if line.lstrip().startswith("|") and set(line.strip()) <= set("|-: "):
                    continue        # 表格分隔行，跳过
                self.text.insert("end", "  " + plain_markdown(line.strip()) + "\n", "bullet")
            elif line.lstrip().startswith(">"):
                self.text.insert("end", plain_markdown(line.lstrip(">").strip()) + "\n", "quote")
            else:
                self.text.insert("end", plain_markdown(line) + "\n", "body")
        # 目录文字 = 去掉 Markdown 语法后的样子
        for display, anchor in self._headings:
            self.toc.insert("end", display)

    def _anchor(self, tag: str, title: str, *, chapter: bool) -> None:
        index = self.text.index("end-1c")
        clean = plain_markdown(title).strip()
        self.text.insert("end", clean + "\n", tag)
        name = f"anchor_{len(self._anchors)}"
        self.text.mark_set(name, index)
        self._anchors[clean] = name
        if chapter:
            self._headings.append((clean, name))

    def _jump(self, _event=None) -> None:
        selection = self.toc.curselection()
        if not selection:
            return
        title = self.toc.get(selection[0])
        anchor = self._anchors.get(title)
        if anchor:
            self.text.see(anchor)
            self.text.mark_set("insert", anchor)

    def _on_wheel(self, event) -> None:
        try:
            self.text.yview_scroll(-1 if event.delta > 0 else 1, "units")
        except tk.TclError:
            pass
        return "break"

    # -- 搜索 ------------------------------------------------------------
    def search(self) -> None:
        needle = self.query.get().strip()
        self.text.tag_remove("find", "1.0", "end")
        if not needle:
            return
        self._hits: list[str] = []
        start = "1.0"
        while True:
            found = self.text.search(needle, start, stopindex="end", nocase=True)
            if not found:
                break
            end = f"{found}+{len(needle)}c"
            self.text.tag_add("find", found, end)
            self._hits.append(found)
            start = end
        self._hit_index = -1
        if not self._hits:
            self.root.title("桌面日程 · 使用教程（没找到「" + needle + "」）")
            return
        self.root.title(f"桌面日程 · 使用教程（{len(self._hits)} 处匹配）")
        self.search_next()

    def search_next(self) -> None:
        hits = getattr(self, "_hits", None)
        if not hits:
            return
        self._hit_index = (self._hit_index + 1) % len(hits)
        position = hits[self._hit_index]
        self.text.see(position)
        self.text.mark_set("insert", position)

    def close(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def show_tutorial(parent=None, *, data_dir: Path | None = None) -> TutorialWindow:
    """打开教程窗（唯一入口，设置页和命令行都走这里）。"""
    window = TutorialWindow(parent, data_dir=data_dir)
    if parent is None:
        window.root.mainloop()
    return window


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    show_tutorial()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
