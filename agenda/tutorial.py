"""使用教程窗：把 docs/教程.md 渲染成一个能搜索、能跳章节的窗口。

为什么要做成窗口而不是让人去翻文件：教程只有"当场能打开"才会被看。
所以设置页里常驻一个入口，另外 `main.py --tutorial` 也能直接打开。

版面：**左边窄目录（分类）+ 右边宽正文**。每个章节的标题做成一条真卡片标题栏
（`Text.window_create` 嵌一个带边框的 Frame）—— Tk 的标签背景只铺在文字底下，
做不出块状感；嵌 Frame 才有"卡片"，而正文留在 Text 里，选中和搜索都还在。

图片：`![说明](images/xx.png)` 会真的贴进来，**双击用系统看图工具打开原图**；
想看更大更清楚的图（或想收藏），点右上角「在浏览器打开」——
会生成 `docs/教程.html` 并用系统浏览器打开（那版是完整的卡片式排版，图能点击放大）。
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import backdrop, markdown_blocks, theme

TUTORIAL_FILENAME = "教程.md"
#: 图片在教程窗里的最大显示宽度（逻辑像素）。再宽就得横向滚动，反而更难读。
IMAGE_MAX_WIDTH = 660
#: 拍图工具顺手生成的"显示尺寸副本"放在这个子目录里。
#: 为什么要有它：自己解码 → 缩放 → 再编成 PNG 交给 Tk，13 张要 570 ms；
#: 让 Tk 直接读文件只要 106 ms。窗口打开快慢主要就卡在这儿。
DISPLAY_DIR_NAME = "display"

#: `![说明](路径)`（真正的解析在 `markdown_blocks`，这里留个名字给老测试用）
_IMAGE_RE = markdown_blocks.IMAGE_RE


def _display_copy(original: Path) -> Path | None:
    """取"显示尺寸副本"的路径；没有、或比原图旧，就返回 None。

    副本比原图旧说明原图被改过（可能是手工换的图），这时必须走解码那条路，
    否则窗口里显示的还是旧内容 —— 这种"图换了但界面没变"最难查。
    """
    candidate = original.parent / DISPLAY_DIR_NAME / original.name
    try:
        if candidate.is_file() and candidate.stat().st_mtime >= original.stat().st_mtime - 1:
            return candidate
    except OSError:
        pass
    return None


def _open_path(path: Path) -> None:
    """用系统默认程序打开一个文件（教程里双击配图用）。"""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))                      # noqa: S606
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(path)])
    except Exception:                                    # noqa: BLE001
        pass


def plain_markdown(text: str) -> str:
    """把行内 Markdown 语法压成纯文本。

    实现在 `markdown_blocks.plain`——教程窗和浏览器版共用同一份，
    免得两边显示得不一样。这里留个同名函数，是因为别处（含测试）都在用它。
    """
    return markdown_blocks.plain(text)


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
        tk.Button(
            header, text="在浏览器打开", command=self.open_in_browser, relief="flat", bd=0,
            bg=colors["accent"], fg="#FFFFFF", padx=12, pady=4, cursor="hand2",
            font=(family, 9, "bold"),
        ).pack(side="right", padx=(0, 10), pady=8)

        body = tk.Frame(self.root, bg=colors["bg"])
        body.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        # 左：**窄**目录。只列章节，宽度够一行短标题就行（长标题自己会被 Listbox 截断，
        # 鼠标悬停有提示；正文那一侧才是主体）。
        left = tk.Frame(body, bg=colors["border"], width=178)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        tk.Label(left, text="章节", bg=colors["bg_soft"], fg=colors["text_dim"],
                 font=(family, 9, "bold"), anchor="w").pack(fill="x", padx=10, pady=(8, 4))
        self.toc = tk.Listbox(
            left, bg=colors["bg"], fg=colors["text_dim"], selectbackground=colors["accent"],
            selectforeground="#FFFFFF", relief="flat", highlightthickness=0, bd=0,
            font=(family, 9), activestyle="none", selectmode="browse",
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
        tk.Label(search_row, text="双击配图可用系统看图工具打开原图", bg=colors["bg"],
                 fg=colors["text_faint"], font=(family, 8)).pack(side="left", padx=(10, 0))

        # 正文：Text + 滚动条（先 pack 滚动条，否则会被 Text 的 expand 挤成 1px）
        text_box = tk.Frame(right, bg=colors["border"])
        text_box.pack(fill="both", expand=True, pady=(0, 8))
        self.scrollbar = tk.Scrollbar(text_box, width=10, bd=0, highlightthickness=0,
                                      troughcolor=colors["bg_soft"], background=colors["text_faint"],
                                      activebackground=colors["accent"], relief="flat")
        self.scrollbar.pack(side="right", fill="y")
        self.text = tk.Text(
            text_box, bg=colors["bg"], fg=colors["text"], relief="flat", wrap="word",
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
        self.text.tag_configure("caption", font=(family, 9), foreground=colors["text_faint"],
                                justify="center", spacing3=10)

    # -- 渲染 ------------------------------------------------------------
    def _render(self) -> None:
        if self.path is None:
            self.text.insert("end", f"没找到 {TUTORIAL_FILENAME}。\n\n"
                                    "该文件应与 README.md 位于同一 docs 目录。\n", "body")
            return
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as error:
            self.text.insert("end", f"读取教程失败：{error}\n", "body")
            return

        from . import markdown_blocks

        self._headings: list[tuple[str, str]] = []      # (显示文字, 锚点)
        self._anchors: dict[str, str] = {}
        self._images: list = []                          # 防 PhotoImage 被 GC
        self._chapter_marks: list[tuple[str, str]] = []  # (锚点名, 章节标题)
        self._missing_images = 0
        in_table = False
        for block in markdown_blocks.iter_blocks(text):
            kind, payload = block.kind, block.payload
            if kind == "heading":
                level, title = payload
                title = str(title)
                if level == 1:
                    self._anchor("h1", title, chapter=False)
                elif level == 2:
                    if title == "目录":
                        self._anchor("h2", title, chapter=False)
                    else:
                        # 章节标题做成一条**真卡片**的标题栏（嵌进正文的 Frame），
                        # 正文仍在 Text 里 —— 这样既能卡片式分块，又保住了选中与搜索。
                        anchor = self._chapter_header(title)
                        self._headings.append((title, anchor))
                        self._chapter_marks.append((anchor, title))
                else:
                    # 三级标题不进目录：目录只列章节，否则一屏全是子条目反而找不到主线
                    self._anchor("h3", title, chapter=False)
            elif kind == "image":
                source, caption = payload
                self._insert_image(str(source), str(caption))
            elif kind == "code":
                self.text.insert("end", str(payload) + "\n", "code")
            elif kind == "rule":
                if in_table:
                    in_table = False
                self.text.insert("end", "\n", "rule")
            elif kind == "table_row":
                in_table = True
                self.text.insert("end", "  " + markdown_blocks.plain(str(payload)) + "\n",
                                 "bullet")
            elif kind == "bullet":
                in_table = False
                self.text.insert("end", "  " + str(payload) + "\n", "bullet")
            elif kind == "quote":
                in_table = False
                self.text.insert("end", str(payload) + "\n", "quote")
            else:
                in_table = False
                self.text.insert("end", str(payload) + "\n", "body")
        del in_table
        for display, _anchor in self._headings:
            self.toc.insert("end", display)
        self._toc_current = -1

    def _chapter_header(self, title: str) -> str:
        """插一条章节标题栏（真 Frame，不是文字上色），返回锚点名。

        为什么用嵌入式 Frame：Tk 的 Text 标签背景只铺在**文字底下**，做不出"卡片"；
        嵌一个带边框的 Frame 进去才有块状感，同时正文还留在 Text 里（能选中、能搜索）。
        """
        colors = theme.COLORS
        family = self.fonts.family
        # ⚠ 位置必须在**插入之前**记下来，并且把 gravity 锁成 left：
        # 否则后续 `insert("end", ...)` 会把这个 mark 一路顶到文末 ——
        # 目录点一下就直接跳到文档结尾（实测所有章节的锚点全变成 431.0）。
        index = self.text.index("end-1c")
        band = tk.Frame(self.text, bg=colors["border"])
        card = tk.Frame(band, bg=colors["bg_soft"])
        card.pack(fill="x", padx=1, pady=1)
        tk.Label(card, text=title, bg=colors["bg_soft"], fg=colors["accent"],
                 font=(family, 12, "bold"), anchor="w",
                 ).pack(fill="x", padx=14, pady=(9, 9))
        self.text.insert("end", "\n")
        self.text.window_create("end", window=band, padx=0, pady=6)
        self.text.insert("end", "\n")
        name = f"anchor_{len(self._anchors)}"
        self.text.mark_set(name, index)
        self.text.mark_gravity(name, "left")
        self._anchors[title] = name
        return name

    def _anchor(self, tag: str, title: str, *, chapter: bool) -> None:
        index = self.text.index("end-1c")
        clean = plain_markdown(title).strip()
        self.text.insert("end", clean + "\n", tag)
        name = f"anchor_{len(self._anchors)}"
        self.text.mark_set(name, index)
        self.text.mark_gravity(name, "left")     # 同上：别让后续插入把锚点顶走
        self._anchors[clean] = name
        if chapter:
            self._headings.append((clean, name))

    def _insert_image(self, relative: str, caption: str) -> None:
        """把一张配图贴进正文。

        图片路径相对**教程文件**所在目录（也就是 `docs/`）。找不到就写一行提示，
        而不是安静地什么都不显示 —— 那样用户只会以为"教程里本来就没图"。

        快慢的关键：**优先让 Tk 直接读"显示尺寸副本"**（拍图工具生成的），
        比我们自己解码+缩放+重编码快 5 倍（106 ms vs 570 ms，13 张）。
        """
        base = self.path.parent if self.path is not None else Path.cwd()
        target = (base / relative).resolve()
        photo = None
        if target.is_file():
            copy = _display_copy(target)
            if copy is not None:
                try:
                    photo = tk.PhotoImage(master=self.text, file=str(copy))
                except tk.TclError:
                    photo = None
            if photo is None:
                try:
                    picture = backdrop.load_image(target, max_width=IMAGE_MAX_WIDTH)
                    if picture is not None:
                        photo = backdrop.to_photoimage(self.text, picture)
                except Exception:                        # noqa: BLE001
                    photo = None
        if photo is None:
            self._missing_images += 1
            self.text.insert("end", f"〔配图缺失或打不开：{relative}〕\n", "quote")
            return
        self._images.append(photo)
        tag = f"tutorial_image_{len(self._images)}"
        self.text.insert("end", "\n")
        before = self.text.index("end-1c")
        # ⚠ `image_create` 不认 `tags=` 这个关键字（Tk 里 tag 是**位置参数**，
        # tkinter 的包装只转成 -option value，于是会报 unknown option "-tags"）。
        # 所以先建图，再用 tag_add 把这一格圈起来。
        self.text.image_create("end", image=photo, align="center", padx=6, pady=6)
        after = self.text.index("end-1c")
        self.text.tag_add(tag, before, after)
        # 双击 → 用系统看图工具打开原图（教程窗里缩放过的图看不清细节）
        self.text.tag_bind(tag, "<Double-Button-1>",
                           lambda _event, path=target: _open_path(path))
        self.text.insert("end", "\n")
        if caption:
            self.text.insert("end", caption + "\n", "caption")

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
        index = selection[0]
        title = self.toc.get(index)
        anchor = self._anchors.get(title)
        if anchor:
            self.text.see(anchor)
            self.text.mark_set("insert", anchor)
            self._toc_current = index          # 别让滚动同步器立刻把它改回去

    def _sync_toc(self) -> None:
        """滚动时高亮"当前读到哪一章"。

        长文档里没有这个会很晕：目录停在第一章，正文已经翻到第八节了。
        """
        marks = getattr(self, "_chapter_marks", None)
        if not marks:
            return
        try:
            top = self.text.index("@0,0")
        except tk.TclError:
            return
        current = -1
        for index, (anchor, _title) in enumerate(marks):
            try:
                if self.text.compare(anchor, "<=", top):
                    current = index
            except tk.TclError:
                continue
        if current < 0 or current == getattr(self, "_toc_current", None):
            return
        self._toc_current = current
        try:
            self.toc.selection_clear(0, "end")
            self.toc.selection_set(current)
            self.toc.see(current)
        except tk.TclError:
            pass

    def open_in_browser(self) -> None:
        """生成一份卡片式 HTML 并用系统浏览器打开（图能点击放大、能收藏）。"""
        from . import tutorial_html

        if self.path is None:
            return
        target = tutorial_html.open_in_browser(self.path)
        if target is None:
            messagebox.showwarning("打不开浏览器", "生成教程网页失败，请检查 docs 目录是否可写。",
                                   parent=self.root)
            return
        self._browser_target = target

    def _on_wheel(self, event) -> None:
        try:
            self.text.yview_scroll(-1 if event.delta > 0 else 1, "units")
        except tk.TclError:
            pass
        self._sync_toc()
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
