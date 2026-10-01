"""够用的 Markdown 解析：把 `docs/教程.md` 切成块。

为什么单独拿出来：这份教程要被**两个**渲染器读 ——
  * 应用里的教程窗（`agenda/tutorial.py`，Tk Text）
  * 浏览器版（`agenda/tutorial_html.py`，生成一个 HTML 给你收藏/放大图）
两套各写一遍解析，迟早会出现"窗口里和浏览器里显示的不一样"。
所以解析只写一份，两个渲染器都吃同一串块。

不追求符合 Markdown 规范，只求**读起来清楚**：标题、段落、列表、表格、代码块、
引用、分隔线、图片。行内语法压成纯文本（`**粗体**` 去星号、`[字](链接)` 只留字）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple

#: `![说明](路径)`。路径里可能有子目录（不收带空格的路径）。
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
#: 行内链接 `[文字](目标)`
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_LIST_PREFIXES = ("- ", "* ", "| ",
                  "1. ", "2. ", "3. ", "4. ", "5. ", "6. ", "7. ", "8. ", "9. ")


class Block(NamedTuple):
    """一块内容。`kind` 决定 `payload` 怎么用：

    | kind | payload |
    | --- | --- |
    | heading | (级别, 文字) |
    | paragraph | **原始文字**（含 `**粗体**`、`[字](链接)`，渲染器自己决定怎么处理） |
    | bullet | 原始文字 |
    | table_row | 原始文字（整行，由渲染器决定怎么切列） |
    | code | 一行代码 |
    | quote | 文字 |
    | image | (路径, 说明) |
    | rule | "" |
    """

    kind: str
    payload: object


def slug(title: str) -> str:
    """把章节标题变成锚点 id —— 和 `docs/教程.md` 目录里写的链接保持一致。

    规则（跟 GitHub 对中文标题的处理一致）：去掉标点、空格换连字符、小写。
    "1. 它是干什么的" → "1-它是干什么的"；"5. 把群通知加进来（五种方式）" →
    "5-把群通知加进来五种方式"。有偏差的话浏览器版的目录就点不动了。
    """
    cleaned = "".join(ch for ch in title.strip()
                      if ch.isalnum() or ch in " -_一-鿿")
    return cleaned.replace(" ", "-").lower()


def plain(text: str) -> str:
    """把行内 Markdown 语法压成纯文本。

    Text 控件没有富文本，`**加粗**` 会原样显示成星号——看着像坏了。
    所以这里：去强调符号、去行内代码反引号、`[文字](链接)` 只留文字。
    """
    value = _LINK_RE.sub(r"\1", text)
    value = value.replace("**", "").replace("`", "")
    return value


def table_cells(line: str) -> list[str]:
    """`| 甲 | 乙 |` → `["甲", "乙"]`。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(line: str) -> bool:
    """`| --- | --- |` 这种分隔行（没有内容，跳过）。"""
    stripped = line.strip()
    return stripped.startswith("|") and bool(stripped) and set(stripped) <= set("|-: ")


def iter_blocks(text: str) -> Iterator[Block]:
    """逐行读 Markdown，产出块。"""
    in_code = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            yield Block("code", line)
            continue
        if not line.strip():
            continue
        image = IMAGE_RE.match(line.strip())
        if image is not None:
            yield Block("image", (image.group(2), image.group(1)))
            continue
        if line.startswith("# "):
            yield Block("heading", (1, line[2:].strip()))
        elif line.startswith("## "):
            yield Block("heading", (2, line[3:].strip()))
        elif line.startswith("### "):
            yield Block("heading", (3, line[4:].strip()))
        elif line.strip() in {"---", "***"}:
            yield Block("rule", "")
        elif is_table_separator(line):
            continue
        elif line.lstrip().startswith("|"):
            yield Block("table_row", line.strip())
        elif line.lstrip().startswith(("- ", "* ")) or line.lstrip()[:2] in {
                f"{n}." for n in range(1, 10)}:
            yield Block("bullet", line.strip())
        elif line.lstrip().startswith(">"):
            yield Block("quote", line.lstrip(">").strip())
        else:
            yield Block("paragraph", line)


def iter_blocks_from_file(path) -> Iterator[Block]:
    return iter_blocks(path.read_text(encoding="utf-8"))
